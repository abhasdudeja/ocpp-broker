# OCPP Broker (Multi-Organization)

This repository contains a prototype OCPP middleware broker that supports multiple organizations,
per-organization leader/follower backends, and a REST API to manage backends dynamically.

- WebSocket broker: `ws://<host>:<port>/<org>/<charger_id>`
- REST API (FastAPI): `http://<host>:8080/docs`
- Config: `config.yaml` (defines orgs and backends)

See `docs/` for full documentation.
