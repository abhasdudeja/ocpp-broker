# Production Deployment

How to run the OCPP broker as a long-lived service.

## What the project ships

The project is a Python package (`pip install ocpp-broker`, Python 3.10 or newer) with two console commands:

- `ocpp-broker-server [-c CONFIG]` (same as `python -m ocpp_broker.server`) starts the broker.
- `ocpp-broker-hash-password [PASSWORD]` prints a `pbkdf2_sha256` hash for `charger_auth` credentials.

It does **not** ship a Dockerfile, Compose file, systemd unit, Kubernetes manifests, or reverse-proxy configuration. The snippets below are examples written for this guide. They use only real commands, flags and settings, but they are not part of the project and are not tested by it.

How the broker runs:

- One process, one port (default `0.0.0.0:8765`). The charger WebSocket (`ws://HOST:8765/{org}/{charger_id}`), the REST API, `/health` and the Swagger UI (`/docs`) all share it. There is no separate API port.
- The broker has no TLS support. Chargers connect with plain `ws://` unless you put a TLS-terminating reverse proxy in front.
- Charger sessions and the tag cache live in process memory. MongoDB is optional but strongly recommended (see below).
- Configuration is read once at startup. There is no reload; restart the process to apply changes. A restart disconnects every charger, which then has to reconnect.

## Installation

```bash
python3 -m venv /opt/ocpp-broker/venv
/opt/ocpp-broker/venv/bin/pip install ocpp-broker
mkdir -p /opt/ocpp-broker/config
```

See the [Installation Guide](installation.md) for other install options.

## Configuration

Example `/opt/ocpp-broker/config/config.yaml` for a relay organization with authenticated chargers and MongoDB:

```yaml
broker:
  host: 0.0.0.0
  port: 8765

mongodb:
  enabled: true
  connection_string: "mongodb://localhost:27017"
  database_name: "ocpp_broker"

organizations:
  - name: "ProductionCharging"
    connect_to_backend: true          # relay mode: forward to the backends below
    ocpp_subprotocol: "ocpp1.6"
    charger_auth:                      # HTTP Basic on the WebSocket upgrade
      credentials:
        PROD_001:
          password_hash: "pbkdf2_sha256$200000$<salt>$<hash>"   # from ocpp-broker-hash-password
    backends:
      - id: "production_backend"
        url: "wss://your-backend.example.com/ocpp"   # broker connects to <url>/<charger_id>
        leader: true
```

Notes:

- Always pass the config path explicitly with `-c`. Without `-c` the server looks for `./config.yaml`, then for a `config.yaml` in the repository root (only meaningful for a source checkout); if neither exists it logs `Configuration file not found at ..., using unified defaults.` and starts with **no organizations**, so every charger is rejected. The `OCPP_BROKER_CONFIG` environment variable is not used by `ocpp-broker-server`.
- Set the REST API key in the environment, not in the file: `OCPP_BROKER_API_KEY` (it overrides `security.api_key`). With no key, every REST request returns 503 unless `security.allow_unauthenticated_api: true` is set, which you should not do in production.
- Organizations without `charger_auth.credentials` accept any client as any charger (the broker logs a warning at startup).
- Environment overrides that are read: `BROKER_HOST`, `BROKER_PORT`, `MONGODB_ENABLED`, `MONGODB_CONNECTION_STRING`, `MONGODB_DATABASE_NAME`, `OCPP_BROKER_API_KEY`. A `.env` file in the working directory is also loaded. Environment values win over the YAML.
- Without MongoDB, transaction ids come from a non-durable in-memory counter (the broker logs `TRANSACTION IDS ... ARE NOT DURABLE`), and tags added through the API are lost on restart. Enable MongoDB before production use.

See the [Configuration Guide](configuration.md) for every option.

## systemd example

```ini
# /etc/systemd/system/ocpp-broker.service  (example, not shipped)
[Unit]
Description=OCPP Broker
After=network.target

[Service]
User=ocpp-broker
WorkingDirectory=/opt/ocpp-broker
EnvironmentFile=/etc/ocpp-broker.env
ExecStart=/opt/ocpp-broker/venv/bin/ocpp-broker-server -c /opt/ocpp-broker/config/config.yaml
Restart=always
RestartSec=10
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

`/etc/ocpp-broker.env` holds the secrets, readable only by root:

```bash
OCPP_BROKER_API_KEY=a-long-random-string
# MONGODB_CONNECTION_STRING=mongodb://user:password@localhost:27017
```

```bash
sudo useradd --system --home /opt/ocpp-broker ocpp-broker
sudo chmod 600 /etc/ocpp-broker.env
sudo systemctl daemon-reload
sudo systemctl enable --now ocpp-broker
sudo systemctl status ocpp-broker
sudo journalctl -u ocpp-broker -f          # logs go to stderr, so journald captures them
curl http://localhost:8765/health          # {"status":"ok"}
```

There is no `ExecReload`: the broker does not handle SIGHUP. Use `systemctl restart`.

## TLS and reverse proxy

Terminate TLS in a reverse proxy so chargers can use `wss://` and the API key and Basic credentials are not sent in clear text. The proxy must forward WebSocket upgrades and the `Authorization` header (nginx forwards it by default). Example nginx server block (the `map` goes in the `http` context):

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name ocpp.example.com;
    ssl_certificate     /etc/ssl/ocpp/fullchain.pem;
    ssl_certificate_key /etc/ssl/ocpp/privkey.pem;

    # REST API, docs and management routes: restrict to your own network
    location ~ ^/(api|orgs|docs|redoc|openapi\.json)(/|$) {
        allow 10.0.0.0/8;
        deny all;
        proxy_pass http://127.0.0.1:8765;
    }

    # Charger WebSockets and /health
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
}
```

<!-- docs-test: skip -->
- The REST API and the charger WebSocket are on the same port, so the proxy is the place to keep the API off the public internet. The regex above would also catch a charger URL such as `/api/CP1`, so do not name an organization `api`, `orgs`, `docs` or `redoc`.
- The broker pings chargers every 20 s (`security.websocket.ping_interval`), so a proxy `proxy_read_timeout` comfortably above that will not cut idle chargers.
- When the proxy runs on the same host, set `broker.host: 127.0.0.1` so the plain-text port is not reachable from outside.
- Point load-balancer or uptime checks at `GET /health`.

## Docker example

No Dockerfile exists in the repository. This one is an untested example that installs the published package:

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir ocpp-broker
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')"
CMD ["ocpp-broker-server", "-c", "/config/config.yaml"]
```

```bash
docker build -t ocpp-broker .
docker run -d --name ocpp-broker -p 8765:8765 \
  -v "$(pwd)/config.yaml:/config/config.yaml:ro" \
  -e OCPP_BROKER_API_KEY=a-long-random-string \
  ocpp-broker
docker logs -f ocpp-broker
```

Inside a container keep `broker.host: 0.0.0.0` and the same port as `EXPOSE`. `localhost` in `mongodb.connection_string` refers to the container itself, so point it at a reachable MongoDB host or set `MONGODB_CONNECTION_STRING`.

## Running more than one instance

The broker is not clustered. If you run several instances behind a load balancer, be aware that:

- A charger's session exists only in the instance it connected to. REST commands (`/api/ocpp/.../commands`) and the charger listing must be sent to that instance; others answer 404 `Charger ... not connected`.
- The "same charger reconnects, old socket closed with 4003" logic is per instance. If a charger lands on a different instance after a reconnect, the old session is not evicted by it; it ends when its own connection drops.
- Each instance has its own in-memory tag cache. After changing tags in MongoDB, call `POST /api/tags/sync` on every instance.
- Transaction ids come from an atomic per-organization counter in MongoDB, so instances sharing one MongoDB do not hand out the same id. Without MongoDB each instance has its own counter.

Unless you need this, a single instance with `Restart=always` is the simpler setup.

## Backup

What to back up is the config file and, if enabled, the MongoDB database:

```bash
cp /opt/ocpp-broker/config/config.yaml /backups/config-$(date +%Y%m%d).yaml
mongodump --db ocpp_broker --out /backups/mongo-$(date +%Y%m%d)
```

## Related Documentation

- [Installation Guide](installation.md)
- [Configuration Guide](configuration.md)
- [Monitoring & Logging](monitoring.md)
- [Troubleshooting](troubleshooting.md)
- [API Reference](api-reference.md)
