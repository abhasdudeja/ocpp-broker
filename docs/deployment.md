# Production Deployment

How to run the OCPP broker as a long-lived service.

## What the project ships

The project is a Python package (`pip install ocpp-broker`, Python 3.10 or newer) with two console commands:

- `ocpp-broker-server [-c CONFIG]` (same as `python -m ocpp_broker.server`) starts the broker.
- `ocpp-broker-hash-password [PASSWORD]` prints a `pbkdf2_sha256` hash for `charger_auth` credentials.

It does **not** ship a Dockerfile, Compose file, systemd unit, Kubernetes manifests, or reverse-proxy configuration. The snippets below are examples written for this guide. They use only real commands, flags and settings, but they are not part of the project and are not tested by it.

How the broker runs:

- One process, one port (default `0.0.0.0:8765`). The charger WebSocket (`ws://HOST:8765/{org}/{charger_id}`), the REST API, the web console (`/ui`), `/health` and the Swagger UI (`/docs`) all share it. There is no separate API port.
- The broker has no TLS support. Chargers connect with plain `ws://` unless you put a TLS-terminating reverse proxy in front.
- Charger sessions and the tag cache live in process memory. MongoDB is optional but strongly recommended (see below).
- The configuration file is read once at startup; restart the process to apply a change to it. The exception is the organizations (chargers, backends, credentials): with `admin.enabled` they can be changed while the broker runs ([Admin](admin.md)). A restart disconnects every charger, which then has to reconnect.

## Installation

The release on PyPI may be older than the repository. Check `pip index versions ocpp-broker` against `version` in `pyproject.toml` and `CHANGELOG.md`: anything that the changelog lists under *Unreleased* (web console, history, admin, fail-back, OCPP 2.x relay) is only in a build made from the repository.

From PyPI:

```bash
python3 -m venv /opt/ocpp-broker/venv
/opt/ocpp-broker/venv/bin/pip install ocpp-broker
mkdir -p /opt/ocpp-broker/config
```

From the repository, without installing Node or Git on the server: build the wheel on your own computer (the console is built first so that it is inside the wheel) and copy it over. The commands are in step 1 of [Deploying on a VPS](#deploying-on-a-vps-contabo) below; install the copied file with `pip install /tmp/ocpp_broker-*.whl` in place of `pip install ocpp-broker`.

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

- Pass the config path explicitly, with `-c` or the `OCPP_BROKER_CONFIG` environment variable. A path given either way that does not exist stops the server with `Configuration file not found` (exit status 2). Without either the server looks for `./config.yaml`, then for a `config.yaml` in the repository root (only meaningful for a source checkout); if neither exists it logs a warning and starts with **no organizations**, so every charger is rejected.
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

    # REST API, web console, docs and management routes: restrict to your own network
    location ~ ^/(api|ui|orgs|docs|redoc|openapi\.json)(/|$) {
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
- The REST API and the charger WebSocket are on the same port, so the proxy is the place to keep the API off the public internet. The regex above would also catch a charger URL such as `/api/CP1`, so do not name an organization `api`, `ui`, `orgs`, `docs` or `redoc`. (The broker itself has no such clash: the web console at `/ui` is plain HTTP and the charger endpoint only accepts WebSocket upgrades.)
- The broker pings chargers every 20 s (`security.websocket.ping_interval`), so a proxy `proxy_read_timeout` comfortably above that will not cut idle chargers.
- When the proxy runs on the same host, set `broker.host: 127.0.0.1` so the plain-text port is not reachable from outside.
- The web console reads `GET /api/events`, a stream that stays open for as long as the page does. The proxy must not buffer it or time it out: the broker sends `X-Accel-Buffering: no` (which nginx honours) and a keepalive comment every 15 s, so nginx's default `proxy_read_timeout` of 60 s is already enough and the example above needs no change. With another proxy, turn response buffering off for `/api/events` and keep its read timeout above 15 s. If the stream cannot be kept open the console still works: it says "Reconnecting…" and refreshes its pages every few seconds.
- Point load-balancer or uptime checks at `GET /health`.

## Running it on your own computer

For trying the broker out, developing against it, or testing a real charger on your network before a server exists. Written for Windows PowerShell; on Linux or macOS the same steps work with `export VAR=value` and `source venv/bin/activate`.

### 1. Install into a virtual environment

From a copy of the repository (the release on PyPI may be older, see [Installation](#installation)):

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -e .
```

Use `.\venv\Scripts\python.exe` for every command below instead of activating the environment; it avoids PowerShell's script-policy errors and the Microsoft Store Python's habit of keeping console commands off the `PATH`. `python -m ocpp_broker.server` does what `ocpp-broker-server` does.

`No module named 'ocpp_broker'` means the command ran with a Python that the install did not go into. The web console at `/ui` needs `src\ocpp_broker\ui_dist\index.html`; it is not in git, so build it once (`cd ui; npm ci; npm run build`, Node 20.19 or newer) or take a copy that has it.

### 2. Write a configuration

Make a working folder and keep the configuration there, not in the repository. Make a hash for the charger's password (it asks for the password and prints `pbkdf2_sha256$...`):

```powershell
.\venv\Scripts\python.exe -m ocpp_broker.auth
```

Save as `config.yaml`:

```yaml
broker:
  host: 127.0.0.1          # this computer only; 0.0.0.0 to accept chargers from the network
  port: 8765

mongodb:
  enabled: false

organizations:
  - name: "MyOrg"
    connect_to_backend: false      # the broker answers the charger itself
    tags:
      - id_tag: "ADMIN001"
        status: "Accepted"
    charger_auth:
      credentials:
        CP001:
          password_hash: "pbkdf2_sha256$200000$..."
```

Check the file really is called `config.yaml` (Notepad adds `.txt`; `dir` shows the true name) and pass its full or relative path with `-c`. A path that does not exist stops the server with `Configuration file not found`.

### 3. Pin the settings, then start

**Do this in every new terminal.** A `.env` file is loaded automatically, and environment variables beat the YAML. If a `.env` is in the working folder (or in the repository, which is found even when you start from elsewhere), it decides whether MongoDB is used and which database, whatever your test configuration says. A test run can therefore write into a real database. Variables set in the terminal win over `.env`, so set them first:

```powershell
$env:MONGODB_ENABLED = "false"; $env:OCPP_BROKER_API_KEY = "local-test-key"
```

They last only for that terminal window; a new window starts without them (the symptoms are `MongoDB ... Failed to connect` lines every few seconds, and `REST API is DISABLED`). To use a MongoDB instead, set `MONGODB_ENABLED`, `MONGODB_CONNECTION_STRING` and `MONGODB_DATABASE_NAME` (a throw-away name such as `ocpp_local`) the same way. Start:

```powershell
.\venv\Scripts\python.exe -m ocpp_broker.server -c .\config.yaml
```

A healthy start ends with `OCPP Broker ready` and `Uvicorn running on http://127.0.0.1:8765`. Then:

```powershell
curl.exe http://127.0.0.1:8765/health
```

Open `http://127.0.0.1:8765/ui` and sign in with the API key.

### 4. Connect a charger

**A simulated one**, from a second terminal: the script in the [Quick Start](quick-start.md#4-connect-a-simulated-charger), with the URL `ws://CP001:YOUR_PASSWORD@127.0.0.1:8765/MyOrg/CP001`. Characters such as `@` or `:` in the password must be URL-encoded in that address.

**A real one.** Set these in the charger:

| Setting | Value |
|---------|-------|
| Server URL | `ws://BROKER_IP:8765/MyOrg`, plus the charger id if the charger does not add it itself: `ws://BROKER_IP:8765/MyOrg/CP001` |
| Charger id | exactly the id in `charger_auth.credentials` (`CP001`); it is also the user name |
| Password (authorization key) | the one you hashed |
| Protocol | OCPP 1.6 JSON (`ocpp1.6`), not SOAP |
| Security | HTTP Basic (security profile 1) over plain `ws://`; the broker has no TLS |

and make the broker reachable:

1. Change `broker.host` to `0.0.0.0` and restart. With `127.0.0.1` only this computer can connect, and `/health` answering "ok" on this computer proves nothing about other devices.
2. Find the computer's address (`ipconfig`, the IPv4 line, for example `192.168.1.25`); that is `BROKER_IP` for a charger on the same network. Give the computer a fixed address in the router so it does not change.
3. Allow the port in Windows Firewall (administrator PowerShell):

   ```powershell
   New-NetFirewallRule -DisplayName "OCPP broker test" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow
   ```

4. From another device on the network (a phone's browser will do) open `http://192.168.1.25:8765/health`. If that fails the firewall or the network is in the way (guest and public Wi-Fi often block traffic between devices); fix that before looking at the charger.

### 5. Reaching it from the internet

To let a charger outside your network connect, forward TCP 8765 on the router to the computer's fixed address, and give the charger your public address (`curl.exe ifconfig.me`; a free dynamic DNS name keeps it stable when your provider changes it). It does not work when the address on the router's status page differs from the public one: the provider uses carrier-grade NAT, and only a tunnel or a server ([VPS](#deploying-on-a-vps-contabo)) will do.

What you are exposing: the port is plain `ws://`, so the charger password and the API key cross the internet in clear text; and the REST API, the console and `/docs` share the port with the chargers, so anyone who finds it can try them. Use long random passwords and API key, keep it for a short test, and undo it afterwards: remove the router forward and

```powershell
Remove-NetFirewallRule -DisplayName "OCPP broker test"
```

A tunnel (`cloudflared tunnel --url http://localhost:8765`, ngrok) needs no router change and gives `wss://`; the quick-tunnel address changes each run, and not every charger supports `wss://`.

### When it does not work

| Symptom | Cause and fix |
|---------|---------------|
| `No module named 'ocpp_broker'` | The install went into another Python. Run `.\venv\Scripts\python.exe -m pip install -e .` and use that interpreter. |
| `Configuration file not found: ...` | The `-c` path does not exist (often the example path from a guide, or `config.yaml.txt`). |
| `Failed to connect to MongoDB: localhost:27017 ... refused`, repeating | MongoDB is switched on (by a `.env` or the YAML) and none runs. Set `MONGODB_ENABLED=false` in this terminal, or start a MongoDB. The broker works meanwhile. |
| `REST API is DISABLED` | `OCPP_BROKER_API_KEY` is not set in this terminal. |
| `/ui` is not found | The console is not built, see step 1. |
| Simulator works, real charger does not | The broker is on `127.0.0.1`, the firewall blocks the port, or the charger's URL, id, password or protocol is wrong (table above). |
| HTTP 401 / 403 / close 4002 | See [A charger cannot connect](troubleshooting.md#a-charger-cannot-connect). |

## Deploying on a VPS (Contabo)

The numbered, copy-and-paste steps are in the [README](../README.md#deploying-on-a-vps-contabo): build the wheel, prepare an Ubuntu server, install, configure, run under systemd, put nginx and HTTPS in front, check. This section is what the steps do not say.

**Why it is laid out that way**

- **Closed ports.** Only 22, 80 and 443 are open. The broker listens on `127.0.0.1:8765` (`broker.host`), so the plain-text port cannot be reached from outside even if the firewall is changed by mistake. Contabo's customer panel has an optional firewall, but do not rely on it having been set up: the `ufw` rules on the server are the ones that count.
- **A domain name.** Let's Encrypt certificates are issued for names, not IP addresses. Without a name you can still run the broker, but only as plain `ws://`, which sends charger passwords and the API key in clear text; do not do that over the internet.
- **The API and console are not public.** The `allow YOUR_HOME_IP; deny all;` block is what keeps `/api`, `/ui` and `/docs` to your own address; the API key is a second lock, not the only one. Without a fixed address use the SSH tunnel in the README.
- **MongoDB.** Atlas (free tier) needs no setup on the server, but the VPS address must be in its *Network Access* list, and the connection string belongs in `/etc/ocpp-broker.env`, not the YAML. A MongoDB on the same VPS also works (`mongodb://127.0.0.1:27017` with authentication switched on, and never a public port 27017); then use `mongodump` for [backups](#backup).
- **A separate database name** (`ocpp_production` in the example) keeps experiments from sharing records with the real thing.

**Behind nginx, what changes**

- The broker sees every connection as coming from `127.0.0.1`. The *Address* the console shows for a charger is therefore the proxy, not the charger, and the wrong-key throttle (`security.api_key_throttle`) counts all API users together. With the `allow` list in the example that is just you, so a mistyped key ten times locks you out for a minute, nobody else.
- The console's live stream (`/api/events`) works through the example without extra settings (see [TLS and reverse proxy](#tls-and-reverse-proxy)).
<!-- docs-test: skip -->
- Certificates renew themselves: `certbot` installs a systemd timer. `certbot renew --dry-run` checks it.

**Looking after it**

```bash
systemctl status ocpp-broker --no-pager
journalctl -u ocpp-broker -f                # live log
journalctl -u ocpp-broker --since "1 hour ago" --no-pager
systemctl restart ocpp-broker               # after editing config.yaml or the env file
```

Upgrading is a new wheel and a restart (the README has the commands). To go back, install the previous wheel the same way. Take a [backup](#backup) before upgrading across versions and read the `CHANGELOG.md` entry.

Back up before anything risky, and on a schedule. A minimal nightly job for the configuration (the admin API also keeps its own `config.yaml.bak-<time>` copies beside the file, up to `admin.keep_backups`):

```bash
cat > /etc/cron.d/ocpp-broker-backup <<'EOF'
15 3 * * * root cp /opt/ocpp-broker/config/config.yaml /var/backups/ocpp-broker-config-$(date +\%Y\%m\%d).yaml
EOF
```

Atlas keeps its own backups on paid tiers only; on the free tier use `mongodump` from a computer you trust.

**When it does not work**

| Symptom | Check |
|---------|-------|
| `systemctl status` says `failed` | `journalctl -u ocpp-broker -n 50 --no-pager`. A wrong or unreachable connection string, a missing or misspelt config path, or a YAML error (the message names the setting). |
| `curl http://127.0.0.1:8765/health` works but `https://...` does not | DNS not yet pointing at the VPS (`dig +short ocpp.example.com`), port 80 or 443 closed (`ufw status`, the Contabo panel firewall), or `nginx -t` failing. |
| `certbot` fails | The name must already resolve to this server, and port 80 must be reachable from outside. |
| Console says `403` or does not load | Your address is not the one in `allow`. `curl ifconfig.me` from your computer shows what it is. Use the SSH tunnel meanwhile. |
| Console loads but sign-in says the key is wrong | The key is the value of `OCPP_BROKER_API_KEY` in `/etc/ocpp-broker.env`; after changing the file run `systemctl restart ocpp-broker`. Ten wrong keys in a minute lock the address out for a minute. |
| Charger gets `401` | The user name must be the charger id from the URL and the password the one that was hashed. |
| Charger gets `403` on connect | It did not offer the subprotocol the organization expects (`ocpp1.6` by default), or the URL is not `/<organization>/<charger id>`. |
| Charger connects, then drops about every minute | A proxy timeout. The example uses `proxy_read_timeout 3600s`; a second proxy or load balancer in front of nginx needs the same. |
| No history, tags or counters appear | `GET /api/mongodb/health` (with the API key) shows whether MongoDB is connected. The Atlas network list is the usual cause. |

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
- With `admin.store: mongodb` the instances share the organizations and the admin audit log ([Admin](admin.md#keeping-the-organizations-in-mongodb)); with the default file store each has its own configuration file.
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
