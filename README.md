# OCPP Broker

A comprehensive OCPP 1.6 broker built on the upstream [`ocpp`](https://github.com/mobilityhouse/ocpp) Python library, providing bi-directional message routing, tag management, and multi-backend support.

## Features

- ✅ **OCPP 1.6 Compliant** - Powered by the upstream `ocpp` library for spec-compliant parsing and validation
- ✅ **Multi-Backend Support** - Relay messages to upstream backends with leader/follower logic
- ✅ **Broker-as-Backend Mode** - Handle OCPP commands locally without external backends
- ✅ **Tag Management** - Comprehensive tag authorization with REST API
- ✅ **Multi-Organization** - Support multiple organizations with isolated configurations
- ✅ **Session Management** - Clean session lifecycle with automatic cleanup

## Quick Start

### Installation

```bash
pip install ocpp-broker
```

### Basic Usage

```bash
# Start the broker server
ocpp-broker-server

# Or with custom config
ocpp-broker-server -c /path/to/config.yaml
```

### Configuration

Create a `config.yaml` file:

```yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "MyOrg"
    connect_to_backend: false  # Broker acts as backend (the default is true: relay mode)
    tags:
      - id_tag: "USER001"
        status: "Accepted"
        tag_type: "RFID"
```

## Security

**REST API.** The REST API (`/api/...`, `/orgs/...`) is served on the same port as the
charger WebSocket and requires an API key. Set `OCPP_BROKER_API_KEY` (or `security.api_key`)
and send it as `X-API-Key: <key>` or `Authorization: Bearer <key>`. With no key configured
every REST request returns `503`; `security.allow_unauthenticated_api: true` opens it up for
local development only. `/health` is always open.

**Chargers.** Add `charger_auth.credentials` to an organization to enforce HTTP Basic auth on
the WebSocket upgrade (OCPP 1.6 security profile 1). The username is the charger id in the URL
and the password its authorization key; rejected upgrades get HTTP `401`. Store keys as hashes:

```bash
python -m ocpp_broker.auth          # prompts for the key and prints a password_hash
```

```yaml
organizations:
  - name: "MyOrg"
    charger_auth:
      credentials:
        CP001:
          password_hash: "pbkdf2_sha256$200000$..."
```

An organization with no credentials accepts any charger (and logs a warning at startup).
Basic auth does not encrypt anything: terminate TLS in front of the broker (profile 2) if the
network is not trusted. Browsers can only call the REST API from origins listed in
`security.cors.allow_origins`.

## Relay mode behaviour

When an organization sets `connect_to_backend: true` the broker relays frames to its backends:

- Frames for an unreachable backend are buffered (`backend_buffer_size`, default 200) and
  delivered in order on reconnect. A CALL that waits longer than `backend_outage_timeout`
  (default 30 s) is answered with a `CALLERROR`.
- Follower backends receive a copy of every charger-initiated CALL (observe-only; their replies
  are discarded). If the leader is unreachable for `leader_failover_timeout` (default 15 s,
  `0` disables) the first healthy follower becomes leader. With `leader_failback: true` the
  charger goes back to the configured leader once it has been healthy for `leader_failback_delay`.
- The broker does not validate relayed frames; the backend does. In broker mode the `ocpp`
  library validates every message.
- In relay mode the backend assigns transaction ids. In broker mode they come from a
  per-organization MongoDB counter; without MongoDB the broker falls back to an in-memory
  counter and logs a loud warning, because those ids are not durable.

## Deploying on a VPS (Contabo)

These steps put the broker on a fresh Ubuntu 22.04 or 24.04 VPS (Contabo, or any provider with a plain Linux server) as a service, behind nginx with HTTPS, so chargers connect with `wss://`. They are an example, not something the project tests. [docs/deployment.md](docs/deployment.md) has the reasoning, upgrading, backups and what to check when something does not work.

**You need:** the VPS and its IP address; a domain name with an `A` record (for example `ocpp.example.com`) pointing at that IP, because HTTPS certificates need a name; a MongoDB connection string (a free MongoDB Atlas cluster is enough); and, on your own computer, Git, Python 3.10+ and Node 20.19+.

Replace `YOUR_VPS_IP`, `ocpp.example.com` and `YOUR_HOME_IP` (the public address you will open the console from) throughout.

**1. Build the package on your computer.** The release on PyPI (0.4) is older than this code and has no web console, so install from the repository:

```bash
git clone https://github.com/abhasdudeja/ocpp-broker.git
cd ocpp-broker
(cd ui && npm ci && npm run build)       # builds the console into the package
python -m pip install build
python -m build --wheel                   # writes dist/ocpp_broker-<version>-py3-none-any.whl
scp dist/ocpp_broker-*.whl root@YOUR_VPS_IP:/tmp/
```

Once a newer release is on PyPI, `pip install ocpp-broker` in step 3 replaces all of this.

**2. Prepare the server.** Log in with the root password or SSH key from Contabo's welcome email, update, install what is needed and close every port except SSH, HTTP and HTTPS:

```bash
ssh root@YOUR_VPS_IP
apt update && apt upgrade -y
apt install -y python3 python3-venv nginx certbot python3-certbot-nginx ufw
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

The broker's own port, 8765, stays closed on purpose; only nginx talks to it. If you also turned on the firewall in Contabo's customer panel, open the same three ports there.

**3. Install the broker** in its own virtual environment, run by a user that cannot log in:

```bash
useradd --system --home /opt/ocpp-broker --create-home --shell /usr/sbin/nologin ocpp-broker
sudo -u ocpp-broker python3 -m venv /opt/ocpp-broker/venv
sudo -u ocpp-broker /opt/ocpp-broker/venv/bin/pip install /tmp/ocpp_broker-*.whl
sudo -u ocpp-broker mkdir -p /opt/ocpp-broker/config
```

**4. Configure it.** Make a hash for each charger's password (it asks for the password and prints `pbkdf2_sha256$...`):

```bash
/opt/ocpp-broker/venv/bin/ocpp-broker-hash-password
```

Create `/opt/ocpp-broker/config/config.yaml` (`nano` is installed). This one answers chargers itself; for relay mode set `connect_to_backend: true` and add `backends` (see [Configuration](docs/configuration.md)):

```yaml
broker:
  host: 127.0.0.1          # only nginx on this machine may reach the plain-text port
  port: 8765

mongodb:
  enabled: true
  database_name: "ocpp_production"

organizations:
  - name: "MyOrg"
    connect_to_backend: false
    charger_auth:
      credentials:
        CP001:
          password_hash: "pbkdf2_sha256$200000$..."   # the hash from above
```

The secrets go in a file only root can read, not in the YAML. Generate the API key, then add your MongoDB connection string (in Atlas, add the VPS IP under *Network Access* first):

```bash
install -m 600 /dev/null /etc/ocpp-broker.env
echo "OCPP_BROKER_API_KEY=$(openssl rand -hex 32)" > /etc/ocpp-broker.env
nano /etc/ocpp-broker.env     # add a line: MONGODB_CONNECTION_STRING=mongodb+srv://USER:PASSWORD@CLUSTER.mongodb.net
```

Keep a copy of the API key (it is the line in that file); the console asks for it at sign-in.

**5. Run it as a service** that starts at boot and restarts if it stops:

```bash
cat > /etc/systemd/system/ocpp-broker.service <<'EOF'
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
EOF
systemctl daemon-reload
systemctl enable --now ocpp-broker
systemctl status ocpp-broker --no-pager
curl http://127.0.0.1:8765/health          # {"status":"ok"}
```

If it is not running, `journalctl -u ocpp-broker -n 50 --no-pager` shows why (a wrong connection string and a missing config file are the usual causes).

**6. Put nginx and HTTPS in front.** The charger WebSocket and the REST API and console share one port, so nginx is where the API and console are kept for you alone:

```bash
cat > /etc/nginx/conf.d/websocket-upgrade.conf <<'EOF'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
EOF

cat > /etc/nginx/sites-available/ocpp <<'EOF'
server {
    listen 80;
    server_name ocpp.example.com;

    # REST API, web console and docs: only from your own address
    location ~ ^/(api|ui|orgs|docs|redoc|openapi\.json)(/|$) {
        allow YOUR_HOME_IP;
        deny all;
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
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
EOF
ln -s /etc/nginx/sites-available/ocpp /etc/nginx/sites-enabled/ocpp
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d ocpp.example.com        # asks for an email, then adds HTTPS and renewal
```

Do not name an organization `api`, `ui`, `orgs`, `docs` or `redoc`: nginx would treat its charger URLs as API paths. If your home address changes, change `allow` and reload nginx, or reach the console through an SSH tunnel instead (`ssh -L 8765:127.0.0.1:8765 root@YOUR_VPS_IP`, then open `http://localhost:8765/ui`).

**7. Check it from your computer, then connect a charger:**

```bash
curl https://ocpp.example.com/health        # {"status":"ok"}
```

Open `https://ocpp.example.com/ui` from `YOUR_HOME_IP` and sign in with the API key. Point the charger at `wss://ocpp.example.com/MyOrg/CP001` with the user name `CP001` and its password (HTTP Basic, OCPP security profile 1). A charger that cannot do TLS cannot be made to connect securely by this setup: ask its vendor for `wss://` support rather than opening plain `ws://` to the internet.

**Upgrading later:** build a new wheel as in step 1, copy it over, then

```bash
sudo -u ocpp-broker /opt/ocpp-broker/venv/bin/pip install --upgrade --force-reinstall --no-deps /tmp/ocpp_broker-*.whl
systemctl restart ocpp-broker
```

A restart disconnects the chargers for a few seconds; they reconnect on their own.

## Documentation

For complete documentation, see the [docs/](docs/README.md) directory:

- [Installation Guide](docs/installation.md)
- [Quick Start](docs/quick-start.md)
- [Configuration Guide](docs/configuration.md)
- [Broker-as-Backend Mode](docs/broker_as_backend.md)
- [Leader/Follower](docs/leader-follower.md)
- [OCPP 1.6 Support](docs/ocpp16_features.md)
- [Tag Management](docs/tag-management.md)
- [MongoDB Integration](docs/mongodb-integration.md)
- [API Reference](docs/api-reference.md)
- [Architecture Overview](docs/architecture.md)
- [Web Console](docs/web-console.md) (the browser UI at `/ui`), [Changing organizations at runtime](docs/admin.md)
- [Deployment](docs/deployment.md), [Monitoring](docs/monitoring.md), [Troubleshooting](docs/troubleshooting.md)

## Development

```bash
# Install with test and lint dependencies
pip install -e ".[tests,lint]"

# Run tests
pytest

# Lint and type-check (what CI runs)
ruff check src tests
mypy

# Build package
python -m build
```

## License

MIT License - see LICENSE file for details.

## Links

- [OCPP 1.6 Specification](https://www.openchargealliance.org/protocols/ocpp-16/)
- [Upstream ocpp Library](https://github.com/mobilityhouse/ocpp)

