# Changelog

All notable changes. The version number is in `pyproject.toml` (the only place); see [docs/releasing.md](docs/releasing.md) for how a release is made.

## Unreleased (since 0.5)

### Added

- **Web console** at `/ui` (React, served by the broker, no separate server): overview, chargers and a charger page with the connections diagram, live events (Server-Sent Events), a command panel generated from the OCPP schemas, backends, offline chargers, tags (add, edit, bulk, import, export, sync), history and admin pages. Sign in with the API key.
- **Live event stream** `GET /api/events` and **command catalog** `GET /api/ocpp/commands/catalog`; per-charger state model (`/api/orgs`, `/api/chargers`, `/api/chargers/{org}/{id}`, `/api/backends`, `/api/system/info`).
- **The broker as a backend inside a relay** (`local: true` backend): as the leader, with the other backends receiving copies; or as a silent standby that takes over when the external leader fails ([docs](docs/leader-follower.md)).
- **Transaction id mapping** between a charger and several backends, persisted in MongoDB, including reservation and charging-profile ids.
- **Fail-back** (`leader_failback`, `leader_failback_delay`) and a way to **change the leader by hand** (`POST /api/chargers/{org}/{id}/leader`, and a button in the console).
- **History**: `commands`, an opt-in `ocpp_messages` log, indexes and retention in MongoDB, `GET /api/history/...` and the History pages. Records are written by the broker only.
- **Admin API** (`/api/admin`, off unless `admin.enabled`) and Admin pages: add, change and remove organizations, backends and charger credentials while the broker runs, with a check before applying, a kept copy of the file, an audit log, and write-only hashed passwords ([docs](docs/admin.md)).
- **API keys with labels** (`security.api_keys`) and **throttling of wrong keys** (`security.api_key_throttle`).
- **Charger authentication** (OCPP security profile 1, HTTP Basic) per organization, hashed passwords (`ocpp-broker-hash-password`).
- Followers and **leader failover**; buffering of charger frames during a backend outage; ping/pong watchdog on charger sockets.
- Writes to MongoDB happen **after** the charger has been answered (bounded, ordered, outage-tolerant queue); MongoDB is retried if it was down at startup.

### Changed

- The `POST /api/mongodb/...` routes that wrote records are gone, so history cannot be forged over REST. Only `GET /api/mongodb/health` remains.
- `call_results`, `call_errors` and the per-action command collections are no longer written; see the `commands` and `ocpp_messages` collections.
- Transactions and statuses are stored with the charger's own time (`received_at` holds when the broker stored them).
- `OCPP_BROKER_CONFIG` is honoured; a configuration file named with `-c` that does not exist is an error (exit status 2) instead of starting with no organizations.
- `logging.level` and `LOG_LEVEL` are applied.
- Settings nothing reads are no longer filled in by the loader (a file that still has them loads as before).

### Removed

- `ChargerRegistry`, and the `API_HOST` / `API_PORT` overrides.
- The custom message validator and its `validate_messages_*` settings (the `ocpp` library validates when the broker is the backend).

### Known limits

- Relay mode needs the charger and its backends to speak the same OCPP version; only OCPP 1.6 is supported (see [plans/roadmap.md](plans/roadmap.md)).
- Each broker instance has its own sessions and its own configuration file.
- Saving a change in the admin API rewrites `config.yaml` without its comments (a copy is kept).
