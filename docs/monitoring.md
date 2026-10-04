# Monitoring & Logging

What the broker gives you for observing a running instance: a health endpoint, a few read-only REST routes, plain log output, and (optionally) data written to MongoDB.

**There is no metrics system.** The broker has no `/metrics` endpoint, no Prometheus or StatsD export, no message counters, and no built-in alerting. If you need numbers or alerts, build them from the pieces below (health checks, REST polling, log matching, MongoDB queries).

All examples use the default port 8765. REST routes need the API key (`X-API-Key` header, or `Authorization: Bearer`); set it in the shell first:

```bash
export OCPP_BROKER_API_KEY="your-key"   # the same value the broker runs with
```

## Health and status endpoints

| Endpoint | Auth | Tells you |
|---|---|---|
| `GET /health` (also `HEAD`) | none | The process is up and serving HTTP. Always `{"status":"ok"}`. |
| `GET /api/mongodb/health` | API key | Whether MongoDB was configured and connected. |
| `GET /api/tags/status` | API key | Whether MongoDB persistence is active for tags, and which orgs have tag lists. |
| `GET /api/ocpp/organizations/{org}/chargers` | API key | Chargers currently connected for an org. |
| `GET /api/ocpp/organizations/{org}/chargers/{id}/status` | API key | One charger: mode and whether it has a backend link. |
| `GET /orgs/{org}/backends` | API key | Relay mode: each backend link, which is leader, which are connected. |

`/health` is a liveness check only. It does not look at MongoDB, backends, or chargers.

### Liveness

```bash
curl -fsS http://localhost:8765/health
```

### MongoDB

```bash
curl -fsS -H "X-API-Key: $OCPP_BROKER_API_KEY" http://localhost:8765/api/mongodb/health
```

Responses:

- `{"status":"not_configured","connected":false}`: MongoDB is disabled, or it was enabled but the initial connection failed (the broker then continues without it).
- `{"status":"connected","connected":true,"database":"ocpp_broker"}`: connected at startup.

The flag is set once at startup. The route does not ping MongoDB, so it will not notice a database that goes away later. Watch the log for `Error saving ...` lines instead.

### Connected chargers

```bash
curl -fsS -H "X-API-Key: $OCPP_BROKER_API_KEY" \
  http://localhost:8765/api/ocpp/organizations/orgA/chargers
```

```json
{"organization": "orgA",
 "chargers": [{"charger_id": "CP001", "organization": "orgA", "mode": "broker", "connected": true}]}
```

`mode` is `broker` (the broker is the central system) or `relay` (frames are forwarded to backends). This is a live view of this process only: a charger that is not connected is simply absent, and there is no last-seen time here. Single charger:

```bash
curl -fsS -H "X-API-Key: $OCPP_BROKER_API_KEY" \
  http://localhost:8765/api/ocpp/organizations/orgA/chargers/CP001/status
```

This returns `{"charger_id", "organization", "mode", "connected", "has_backend"}`, or HTTP 404 `{"detail":"Charger orgA/CP001 not connected"}`.

### Backend links (relay mode)

```bash
curl -fsS -H "X-API-Key: $OCPP_BROKER_API_KEY" http://localhost:8765/orgs/orgA/backends
```

```json
[{"charger_id": "CP001", "url": "ws://backend1.example.com/ocpp", "leader": true,  "connected": true},
 {"charger_id": "CP001", "url": "ws://backend2.example.com/ocpp", "leader": false, "connected": false}]
```

One entry per charger per backend. `connected: false` means that link is currently down and the broker is retrying. After a failover the `leader` flags move. HTTP 404 `{"detail":"Organization not found"}` is returned for an org that has had no relay-mode charger connect since startup (this includes broker-mode orgs). Entries disappear when the charger disconnects.

## Logging

- Logging is standard-library `logging` to stderr at level **INFO**. The level is hard-coded in `server.py`: the `logging.level` config key, the `LOG_LEVEL` environment variable and any `--debug` flag have no effect, and DEBUG messages cannot be enabled without editing the code.
- Format: `2026-10-03 11:54:35 [WARNING] ocpp_broker.broker: message`. Logger names all start with `ocpp_broker.`.
- uvicorn writes its own lines (HTTP requests, WebSocket accept/reject, `connection open`) in its own format alongside the broker's.
- There is no log file option, rotation, or JSON output. Send stderr wherever you want it: journald under systemd (see [deployment](deployment.md)), or a redirect.
- Many broker log lines start with an emoji marker; match on the text, not the marker.
- Config warnings are printed twice at startup, because the configuration is loaded twice. This is harmless.

### Messages worth watching

Texts are quoted from the source; `X` is a charger id and `Y` an organization.

| Level | Message (excerpt) | Meaning |
|---|---|---|
| WARNING | `REST API is DISABLED: every /api request returns 503 until security.api_key (or OCPP_BROKER_API_KEY) is set` | No API key configured; all REST calls fail with 503. |
| WARNING | `REST API is UNAUTHENTICATED (security.allow_unauthenticated_api is true)` | REST API is open to anyone who can reach the port. |
| WARNING | `Organization Y accepts UNAUTHENTICATED chargers` | No `charger_auth` for that org; any client can connect as any charger. |
| WARNING | `Organization Y requires charger authentication but lists no credentials: every charger will be rejected.` | `charger_auth.required: true` with no credentials. |
| WARNING | `... charger credential(s) are stored as plaintext 'password'` | Use `password_hash` (`ocpp-broker-hash-password`). |
| WARNING | `MongoDB not configured or disabled: transaction ids will come from a non-durable in-memory counter and nothing will be persisted.` | No MongoDB. |
| ERROR | `Failed to initialize MongoDB service: ...` (usually preceded by `Failed to connect to MongoDB: ...`) | MongoDB was enabled but unreachable at startup (5 s timeout). The broker keeps running without it. |
| WARNING | `!!! TRANSACTION IDS FOR ORG 'Y' ARE NOT DURABLE !!!` | Logged once per org when the first transaction id is allocated without MongoDB. Ids restart with the broker. |
| ERROR | `Could not allocate transaction id from MongoDB for org 'Y': ...` | MongoDB was connected but the counter failed; the fallback counter is used. |
| WARNING | `Transaction id mapping is memory-only: without MongoDB it is lost when the broker restarts, and followers lose track of transactions already running.` | Relay organization with several backends and no MongoDB. Logged once per process. |
| INFO | `Restored N transaction id record(s) for Y/X` | A charger's stored transaction id table was loaded after a restart. |
| WARNING | `Stored transaction N names backend(s) no longer configured (...); their ids are dropped` | A backend was renamed or removed since the record was stored. |
| WARNING | `Organization Y: backend(s) without an id (...) are identified by their URL in the transaction id table; ...` | Give each backend an `id` so a URL change does not orphan stored transactions. |
| WARNING | `Organization Y: backend id(s) ... are used more than once; ...` | Two backends share an `id`. |
| WARNING | `Leader X never issued an id for transaction N (it did not see the start); passed through unchanged` | The leader has no id for a running transaction; the charger's own id is sent, which may land on another transaction there. |
| WARNING | `Leader issued transaction id N, which the charger already holds; the charger will see M instead` | An id collision after a failover was remapped. |
| WARNING | `Follower X never gave an id for a start; giving up on its copies` | A follower did not answer its copy of a start within `transaction_ids.follower_wait`; it is skipped for that transaction. |
| WARNING | `Leader answered a repeated StartTransaction with a second transaction of its own; the charger keeps id N` | A retried start (after the 60 s stale limit) was started twice on that backend; the charger keeps the first id. |
| WARNING | `Transaction N was never stopped and has been idle for D days; forgetting it` | No `StopTransaction` arrived within `transaction_ids.retain_open`. |
| WARNING | `Transaction id store is waiting for MongoDB (retry in Ns): ...` | MongoDB is unreachable; stored changes wait and are retried. |
| WARNING | `Could not store a transaction id record (retry in Ns): ...` | A write failed; it is retried. |
| ERROR | `Giving up on a transaction id record MongoDB keeps rejecting: ...` | One record was refused 5 times and dropped. |
| WARNING | `Transaction id store is backed up (N changes waiting); dropped the oldest` | MongoDB has been unreachable long enough to fill the queue. |
| WARNING | `Could not load stored transaction ids for Y/X: ...` | The charger starts with an empty table. |
| WARNING | `Configuration file not found at ..., using unified defaults.` | Wrong `-c` path or no `config.yaml`. The default config has no organizations, so every charger is rejected. |
| WARNING | `Rejected charger X from org 'Y': authentication failed` | Bad or missing Basic credentials; the charger got HTTP 401. |
| ERROR | `Rejected charger X from org 'Y': subprotocol mismatch - expected ..., got ...` | Charger did not offer the org's `ocpp_subprotocol`. |
| ERROR | `Rejected charger X from org 'Y': missing required sec-websocket-protocol header` | Charger sent no subprotocol. |
| WARNING | `Rejected charger X: unknown organization 'Y'.` | URL org name is not in the config (close code 4002). |
| WARNING | `Charger Y/X reconnected while a session was still open; replacing it` | A second connection for the same org and charger id evicted the first (close code 4003). Frequent occurrences mean a flapping charger or a duplicated charger id. |
| INFO | `Accepted charger X for org 'Y' (backend connection: enabled\|disabled)` | Connection accepted. `enabled` = relay mode. |
| INFO | `Cleaned up charger X session.` | The charger is gone (clean close, network drop, or ping timeout). |
| ERROR | `Charger X did not accept a frame in time; dropping the connection` | A write to the charger blocked for over 10 s. |
| ERROR | `Error in message handling for X: Organization Y has no backend definition.` | Relay-mode org with an empty `backends` list. |
| WARNING | `Backend connection error for X (Y): ...` | The backend connection failed; the broker retries (1 s backoff, doubling up to 30 s). |
| INFO | `Backend unavailable for X; buffered frame (N waiting)` | Charger frames are queued for the leader. |
| WARNING | `Backend unavailable for X and outbox full (n/m); refusing frame` | `backend_buffer_size` reached. |
| WARNING | `Backend still unavailable for X after Ns; giving up on frame` | `backend_outage_timeout` expired for a queued frame. |
| WARNING | `[X] answering <Action> with CallError: backend unavailable` | The charger was sent `InternalError` / `Backend unavailable, please retry`. |
| INFO | `Delivered N buffered frame(s) to backend for X` | Backend came back; queue flushed in order. |
| WARNING | `FAILOVER: leader <url> unreachable, promoting follower <url>` | A follower became leader. |
| WARNING | `[X] leader backend <url> is down and no follower is healthy; still waiting` | Leader down with no follower to promote. |
| ERROR | `OCPP subprotocol not negotiated for X! Expected ...` | The backend did not agree on the subprotocol. The connection continues. |
| INFO | `OCPP command <Action> to Y/X finished: <status> (message_id: ...)` | Result of a REST-issued command. |

A simple filter for the important ones:

```bash
journalctl -u ocpp-broker | grep -E "FAILOVER|NOT DURABLE|Backend unavailable|Rejected charger|Failed to (connect|initialize) .*MongoDB|reconnected while"
```

(`ocpp-broker` is the unit name from the example in [deployment](deployment.md).)

## MongoDB data

If MongoDB is enabled (`mongodb.enabled: true` or `MONGODB_ENABLED=true`) the broker writes OCPP data to these collections of the configured database. Which collections fill depends on the mode:

- **Broker mode** (`connect_to_backend: false`): the broker handles the charger's messages and stores them. `charger_statuses` (every StatusNotification), `charger_statuses_latest` (one document per org/charger/connector), `meter_values`, `charger_configurations` (per charger, updated on BootNotification, with `last_boot_time`), `transactions` (start inserts; stop updates the same document), `authorizations`, `data_transfers`, and `charger_heartbeats_latest` (one document per charger with `last_heartbeat`; heartbeats are not stored individually).
- **Relay mode**: frames are forwarded without being stored. Only commands are recorded: calls from the leader backend to the charger, and commands/results issued through the REST API, in per-action collections.
- Both modes: `tags`, `tag_list_versions`, and `counters` (transaction id sequences, `_id` = `transaction_id:<org>`).

Useful for monitoring, for example when was a charger last heard from:

```bash
mongosh ocpp_broker --eval 'db.charger_heartbeats_latest.find({org_name: "orgA"}, {_id: 0, charger_id: 1, last_heartbeat: 1})'
```

The broker creates no indexes and no retention policy; collections grow until you prune them.

`GET /api/tags/organizations/{org}/statistics` returns counts of an organization's tags by status and type. It is tag inventory, not traffic statistics.

## A minimal external check

Nothing is shipped for this; the script below is only an example of combining the real endpoints. It exits non-zero if the broker is down or no chargers are connected for an org.

```bash
#!/bin/sh
# check-ocpp-broker.sh ORG   (needs curl and python3; OCPP_BROKER_API_KEY in the environment)
ORG="$1"
curl -fsS http://localhost:8765/health >/dev/null || { echo "broker not healthy"; exit 1; }
curl -fsS -H "X-API-Key: $OCPP_BROKER_API_KEY" \
  "http://localhost:8765/api/ocpp/organizations/$ORG/chargers" |
python3 -c 'import json,sys; n=len(json.load(sys.stdin)["chargers"]); print(n, "charger(s) connected"); sys.exit(0 if n else 1)'
```

Run it from cron or your existing monitoring agent.

## Related Documentation

- [Production Deployment](deployment.md)
- [Troubleshooting](troubleshooting.md)
- [API Reference](api-reference.md)
- [Configuration Guide](configuration.md)
