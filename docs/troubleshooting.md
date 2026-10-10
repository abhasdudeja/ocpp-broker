# Troubleshooting Guide

Problems that can actually occur with the OCPP broker, with causes and fixes taken from how the code behaves. Everything runs on one port (default 8765). REST examples need the API key; set it first:

```bash
export OCPP_BROKER_API_KEY="your-key"   # the value the broker runs with
```

## First checks

```bash
# Is it up? (no auth needed)
curl -fsS http://localhost:8765/health            # {"status":"ok"}

# Is something listening on the port?
ss -ltnp | grep 8765

# What did the broker log? (under systemd, see deployment.md)
sudo journalctl -u ocpp-broker --since "1 hour ago"

# Which version is installed?
pip show ocpp-broker
```

The log level is `INFO`; set `logging.level: DEBUG` (or the `LOG_LEVEL` environment variable) for more. See [Monitoring & Logging](monitoring.md) for the log messages worth searching for.

## Startup problems

### Address already in use

uvicorn cannot bind `broker.host:broker.port`. Another process owns the port (find it with `ss -ltnp | grep 8765`), or a second broker is already running. Change `broker.port` in the config, or set `BROKER_PORT`. Environment variables and a `.env` file in the working directory override the YAML, so check them if the port you configured is not the one in use.

### Every charger is rejected / "Configuration file not found"

If `-c` is missing or wrong, and there is no `./config.yaml`, the server logs `Configuration file not found at ..., using unified defaults.` and starts with no organizations. Every charger then gets close code 4002. Pass the path explicitly:

```bash
ocpp-broker-server -c /opt/ocpp-broker/config/config.yaml
```

The `OCPP_BROKER_CONFIG` environment variable names the file when `-c` is not given. A named file that does not exist stops the server with `Configuration file not found` (exit status 2).

### Config errors at startup

The process exits with a traceback ending in one of these messages:

| Message | Fix |
|---|---|
| `Failed to parse YAML config: ...` | Invalid YAML. Check with `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`. |
| `Each organization must have a name.` | Every entry under `organizations:` needs `name`. |
| `Organization names must be unique` | Rename the duplicate. |
| `Broker port must be a positive integer` | Fix `broker.port`. |
| `Organization X: credentials for charger Y need password_hash or password` | A `charger_auth.credentials` entry is empty. |

Keys the code does not read are silently ignored (for example `api.port`, `broker.timeout`, `worker_processes`). If a setting seems to have no effect, check the [Configuration Guide](configuration.md).

## A charger cannot connect

The URL is `ws://HOST:8765/{org_name}/{charger_id}`: exactly two path segments, and the org name must match a configured `name` exactly (case-sensitive). What the charger sees tells you the cause:

| What the charger sees | Cause | Fix |
|---|---|---|
| HTTP 401, `WWW-Authenticate: Basic` | The org enforces `charger_auth` (it lists `credentials`, or has `required: true`) and the Basic credentials are missing or wrong. The username must be the charger id, the charger id must be listed under `credentials`, and the password must match its `password`/`password_hash`. | Configure the charger's authorization key, or fix the entry. Log: `Rejected charger X from org 'Y': authentication failed`. |
| HTTP 403 on the handshake | The `Sec-WebSocket-Protocol` header is missing, or does not include the org's `ocpp_subprotocol` (default `ocpp1.6`). The broker refuses the upgrade before accepting it, so every client sees HTTP 403 (the code path calls `close(1002)`, but a close before accept is only delivered as the 403). | Make the charger offer `ocpp1.6` (or change the org's `ocpp_subprotocol`). Log: `subprotocol mismatch - expected ..., got ...` or `missing required sec-websocket-protocol header`. |
| Connects, then closed with code 4002 "Unknown organization" | The org in the URL is not in the config. | Fix the URL or add the organization. Log: `unknown organization 'Y'`. |
| Closed with code 4003 "Replaced by a newer connection" | The same org and charger id connected again; the new connection wins. Two physical chargers sharing an id will keep evicting each other. | Give each charger a unique id. Log: `reconnected while a session was still open; replacing it`. |
| Connection refused / timeout | Broker not running, listening on another host/port (`broker.host: 127.0.0.1` accepts only local connections), or a firewall or proxy in between. | `ss -ltnp \| grep 8765`, then check the firewall and proxy. |
| Dropped after about 40 s | The charger does not answer WebSocket pings. The broker pings every `security.websocket.ping_interval` (20 s) and drops a socket that does not answer within `ping_timeout` (20 s). Look for `Cleaned up charger X session.` in the log. | Make sure the charger (and any proxy) answers WebSocket-level pings, or raise the two `security.websocket` values. |

If a simulated charger on the same computer connects but the real one does not, check that `broker.host` is `0.0.0.0` (not `127.0.0.1`), that the firewall allows the port, and the charger's URL, id, password and protocol; the steps are in [Running it on your own computer](deployment.md#4-connect-a-charger).

You can test the handshake without a charger (with Basic credentials only if the org enforces them):

```bash
curl -i --max-time 3 -N -u CP001:secret \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Protocol: ocpp1.6" \
  http://localhost:8765/MyOrg/CP001
```

`HTTP/1.1 101 Switching Protocols` means it was accepted. (After a 401, uvicorn also logs `ASGI callable returned without completing handshake.`; it accompanies the rejection.)

There is also a `/ocpp-check` WebSocket endpoint that accepts any `ocpp1.6`, `ocpp2.0` or `ocpp2.0.1` subprotocol and closes; it is only a handshake test.

## Broker mode (`connect_to_backend: false`)

- **The charger gets a `NotImplemented` CallError.** In broker mode the broker answers only BootNotification, Authorize, Heartbeat, StatusNotification, MeterValues, StartTransaction, StopTransaction, DataTransfer, DiagnosticsStatusNotification and FirmwareStatusNotification. Any other action from the charger is rejected. The `ocpp` library also validates every message against the OCPP 1.6 schema; invalid ones get a CallError.
- **StartTransaction or Authorize returns `Invalid`.** The tag is not known to the broker (or is expired). Tags come from `organizations[].tags` in the config and from the API. Add one:

  ```bash
  curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
    -d '{"id_tag": "TAG1", "status": "Accepted"}' \
    http://localhost:8765/api/tags/organizations/MyOrg/tags
  ```

- **Tags missing after a restart.** Without MongoDB, tags live in memory: only the config tags survive a restart. See the MongoDB section below.
- **`Tag management disabled - no organizations with tag management enabled` in the log.** This INFO line is misleading: the tag API and tag authorization are always active.

## Relay mode (`connect_to_backend: true`)

An organization without a `connect_to_backend` key defaults to **true** (relay). If you expected the broker to act as the central system, set `connect_to_backend: false`.

### Charger connects, then is closed straight away

The log shows `Error in message handling for X: Organization Y has no backend definition.` A relay org needs at least one entry in `backends` with a `url`. Add one, or switch the org to broker mode.

### Charger receives `InternalError` / "Backend unavailable, please retry"

The leader backend could not take the charger's CALL. The broker queues charger frames for an unreachable leader (up to `backend_buffer_size`, default 200) and gives up on a frame after `backend_outage_timeout` (default 30 s), or immediately if the queue is full. Then it answers the charger with `[4, id, "InternalError", "Backend unavailable, please retry", {}]`. Diagnose:

```bash
curl -fsS -H "X-API-Key: $OCPP_BROKER_API_KEY" http://localhost:8765/orgs/MyOrg/backends
```

`"connected": false` for the leader means the broker cannot reach it. Look for `Backend connection error for X (Y): ...` in the log, which gives the reason. Check that:

- the broker connects to `<backend url>/<charger_id>`, so `url` is a base URL without the charger id and the backend must accept that path;
- the backend accepts the subprotocol (`ocpp_subprotocol` per backend or per org). A mismatch is logged as `OCPP subprotocol not negotiated for X!` but the connection continues;
- the backend is reachable from the broker host (DNS, firewall, `wss://` certificate).

The broker retries with a 1 s delay that doubles to 30 s. Queued frames are delivered in order when the backend returns (`Delivered N buffered frame(s)`).

### Follower backends do not control the charger

By design. Followers receive a copy of each charger-initiated CALL, but their replies are discarded and anything they send to the charger is ignored. Only the leader (`leader: true`, or the first backend if none or several are marked) talks to the charger. See [Leader-Follower](leader-follower.md).

### Failover did not happen

Failover needs a connected follower, and the leader must stay unreachable for `leader_failover_timeout` (default 15 s; `0` disables it). Log: `FAILOVER: leader ... unreachable, promoting follower ...`. If no follower is healthy you get `leader backend ... is down and no follower is healthy; still waiting`. A recovered old leader comes back as a follower; it gets the charger back only with `leader_failback: true`, after `leader_failback_delay` seconds of an unbroken connection, and not when an operator chose the current leader.

### Messages are not validated

In relay mode frames are forwarded in both directions without validation (only the transaction ids in six message types are translated when there are several backends), so malformed messages are the backend's problem. Validation by the broker only exists in broker mode.

## REST API

All routes except `/health`, `/docs`, `/redoc` and `/openapi.json` need the API key. Error bodies are FastAPI's default `{"detail": ...}`.

| Status | Cause | Fix |
|---|---|---|
| 503 `REST API disabled: set security.api_key or OCPP_BROKER_API_KEY` | No API key configured (the startup log says `REST API is DISABLED`). | Set `OCPP_BROKER_API_KEY` (or `security.api_key`) and restart. |
| 401 `Missing or invalid API key` | Wrong or missing key. | Send `X-API-Key: <key>` or `Authorization: Bearer <key>`. |
| 404 `Charger Y/X not connected` | The charger is not connected to this broker process, or the org/id is misspelled (case-sensitive). | List connected chargers: `GET /api/ocpp/organizations/Y/chargers`. |
| 504 | The charger did not reply within `timeout` (default 30 s, range 1-300). The body has `"status": "timeout"`. | Raise `timeout` in the generic command route, or check the charger. |
| 422 | Broker mode only: the action or payload is not valid OCPP 1.6, and nothing was sent. (In relay mode the frame is sent exactly as given, with no validation.) | Fix the payload (camelCase on the generic route). |
| 503 (command route) | The charger disconnected while the command was in flight. | Retry once it reconnects. |
| Browser blocked by CORS | No CORS headers are sent unless `security.cors.allow_origins` lists the origin. | Add the origin. `"*"` works but never together with `allow_credentials: true`. |

A command returns HTTP 200 even if the charger rejected it; check `status` (`success` or `error`) and `response`/`error` in the body:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "Soft"}' \
  http://localhost:8765/api/ocpp/organizations/MyOrg/chargers/CP001/commands/Reset
```

## MongoDB and persistence

- **Startup warning `MongoDB not configured or disabled`, and `TRANSACTION IDS FOR ORG ... ARE NOT DURABLE`.** MongoDB is off. Transaction ids come from an in-memory counter seeded from the clock, so they can collide with earlier ids after a restart, and nothing is persisted. Tags added through the API are lost on restart. Enable it with `mongodb.enabled: true` or `MONGODB_ENABLED=true`.
- **`Failed to connect to MongoDB` / `Failed to initialize MongoDB service` at startup.** MongoDB was enabled but could not be reached (5 s timeout). The broker keeps running without it, and the effects above apply. Check `MONGODB_CONNECTION_STRING` (it overrides `mongodb.connection_string`), then confirm with:

  ```bash
  curl -fsS -H "X-API-Key: $OCPP_BROKER_API_KEY" http://localhost:8765/api/mongodb/health
  ```

  `"status":"not_configured"` means the broker is not using MongoDB. The broker tries again every few seconds and logs the failure each time, so a MongoDB that is switched on by mistake (a `.env` file, or `MONGODB_ENABLED=true` left in the environment) fills the log: set `MONGODB_ENABLED=false` and restart. A `.env` is loaded from the working folder and from the repository, and variables set in the terminal win over it; see [Running it on your own computer](deployment.md#running-it-on-your-own-computer).
- **Tags edited directly in MongoDB are not seen.** The broker reads MongoDB only when its cache has no tags for the org. Reload:

  ```bash
  curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" "http://localhost:8765/api/tags/sync?org_name=MyOrg"
  ```

  For an org that has tags stored in MongoDB, MongoDB wins and replaces the cache; for an org with none, the config/in-memory tags are pushed up. Without `org_name` all orgs are synced. This returns 503 `MongoDB is not connected` when MongoDB is not in use.
- **Relay-mode transactions and meter values are not in MongoDB.** The backend answers the charger, so it stores them; the broker stores them only in broker mode. In relay mode it records status changes, commands and, if switched on, the message log. See [Monitoring & Logging](monitoring.md).
- **`Error saving ...` in the log.** A write to MongoDB failed; the OCPP message itself was still processed.

## Restarting and recovery

```bash
sudo systemctl restart ocpp-broker
sudo systemctl status ocpp-broker
```

A restart drops all charger sessions (chargers must reconnect) and the in-memory command history (`GET /api/ocpp/commands/{message_id}/response` keeps only the last 1000 commands, in memory). Back up `config.yaml` and, if used, the MongoDB database; there is no other state.

## Reporting issues

Include the broker version (`pip show ocpp-broker`), Python version, the relevant part of `config.yaml` (remove `api_key`, passwords and hashes), the log lines around the problem, and whether the org is in broker or relay mode.

## Related Documentation

- [Installation Guide](installation.md)
- [Configuration Guide](configuration.md)
- [Quick Start Guide](quick-start.md)
- [Production Deployment](deployment.md)
- [Monitoring & Logging](monitoring.md)
- [API Reference](api-reference.md)
