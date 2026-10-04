# Configuration Guide

The broker is configured with one YAML file. This page lists every setting the code reads, what it does and its default. Settings that are accepted but have no effect are listed at the end so you do not rely on them.

## Where the configuration comes from

`ocpp-broker-server` looks for the file in this order:

1. `-c /path/to/config.yaml` (`--config`)
2. the `OCPP_BROKER_CONFIG` environment variable
3. `config.yaml` in the current working directory
4. `config.yaml` at the root of the source tree (only meaningful for a checkout, not an installed package)

A file named by `-c` or `OCPP_BROKER_CONFIG` that does not exist is an **error**: the server logs `Configuration file not found: ...` and exits with status 2, rather than starting with nothing. If no file is named and none is found at 3 or 4 the broker starts with built-in defaults **and no organizations**, so every charger is refused (`Unknown organization`, close code 4002); a warning is logged. The file is read once, and the environment overrides below apply in that case too.

Environment variables override a few values ([see below](#environment-variables)). A `.env` file in the working directory is loaded first, so it can supply them.

The whole broker (charger WebSocket, REST API, Swagger UI at `/docs`, `/health`) listens on **one** port: `broker.port`.

## Annotated reference

```yaml
broker:
  host: 0.0.0.0              # bind address (default 0.0.0.0)
  port: 8765                 # must be a positive integer (default 8765)

mongodb:                     # optional; see mongodb-integration.md
  enabled: false             # default false
  connection_string: "mongodb://localhost:27017"
  database_name: "ocpp_broker"

ocpp:
  commands:
    core:
      heartbeat_interval: 300   # seconds; sent as `interval` in the BootNotification reply (broker mode)

security:
  api_key: "..."                    # REST API key; prefer the OCPP_BROKER_API_KEY environment variable
  allow_unauthenticated_api: false  # true = REST API needs no key (development only)
  cors:
    allow_origins: []               # explicit origins; empty = no CORS headers
    allow_credentials: false        # never combined with "*"
  websocket:
    ping_interval: 20               # seconds between pings to chargers
    ping_timeout: 20                # a charger that does not answer a ping in time is dropped

ui:
  enabled: true                     # serve the web console at /ui (default true)

data_transfer:                      # broker mode only; see "DataTransfer" below
  enabled: true
  known_vendors: []
  known_message_ids: []

organizations:
  - name: "orgA"                    # required, unique; first path segment of the charger URL
    connect_to_backend: false       # false = broker mode, true = relay mode (DEFAULT IS TRUE)
    ocpp_subprotocol: "ocpp1.6"     # WebSocket subprotocol the charger must request
    tags: []                        # initial authorization tags (see tag-management.md)
    charger_auth: {}                # HTTP Basic credentials for chargers
    backends: []                    # relay mode only
    backend_buffer_size: 200
    backend_outage_timeout: 30
    leader_failover_timeout: 15
```

## Organizations

An organization is a namespace for chargers. A charger connects to `ws://HOST:PORT/{org name}/{charger id}`; the same charger id may exist in several organizations and they stay independent. Organization names must be unique and every organization needs a `name`, otherwise the file is rejected at startup.

### Mode: `connect_to_backend`

| Value | Mode | What happens |
|-------|------|--------------|
| `false` | **Broker mode** | The broker is the central system. It answers the charger itself ([Broker-as-Backend](broker_as_backend.md)). |
| `true` | **Relay mode** | The broker opens a WebSocket per charger to each backend and forwards frames both ways ([Leader/Follower](leader-follower.md)). |

**The default is `true`**: an organization without `connect_to_backend` is a relay organization and must list `backends`, or its chargers cannot be served. Always set the key explicitly.

### Relay mode: `backends`

```yaml
organizations:
  - name: "orgB"
    connect_to_backend: true
    ocpp_subprotocol: "ocpp1.6"
    backends:
      - id: primary                       # names the backend in the transaction id table and logs; keep it stable
        url: ws://primary.example.com/ocpp
        leader: true
      - id: observer
        url: ws://secondary.example.com/ocpp
        ocpp_subprotocol: "ocpp1.6"       # optional; defaults to the organization's
```

- `url` is a base URL. For a charger `CP001` the broker connects to `{url}/CP001` (a trailing `/` on `url` is removed).
- `leader: true` marks the backend that talks to the charger. If none is marked the first is used; if several are marked only the first counts (a warning is logged).
- Every other backend is a **follower**: it receives a copy of the charger's requests and its replies are discarded.
- `local: true` (instead of a `url`) makes one backend **this broker itself**, as the leader: the broker answers the charger like broker mode does and the other backends are followers. `id` is optional (`broker` by default) and the entry cannot have a `url`. It leads unless another backend is marked `leader: true`, in which case it is a silent standby that takes over if that leader fails. See [The broker as the leader](leader-follower.md#the-broker-as-the-leader-local-backend) and [as a standby](leader-follower.md#the-broker-as-a-standby-local-follower).
- Every backend needs a `url` unless it is `local`; the file is refused otherwise.
- Backends cannot be given credentials: the broker does not send an `Authorization` header to them.
- A relay organization with an empty `backends` list fails each charger session with an error.

| Relay tuning | Default | Meaning |
|--------------|---------|---------|
| `backend_buffer_size` | `200` | Frames held per charger while the leader is unreachable. A full buffer refuses new frames. |
| `backend_outage_timeout` | `30` | Seconds a held request may wait. Then the charger gets a `CALLERROR` (`InternalError`). |
| `leader_failover_timeout` | `15` | Seconds the leader may be unreachable before the first healthy follower is promoted. `0` disables failover. |

#### Transaction ids: `transaction_ids`

With more than one backend the broker translates transaction ids so each backend is spoken to in its own ids ([why and how](leader-follower.md#transaction-ids)). Optional; the defaults below apply when the block is absent.

```yaml
organizations:
  - name: "orgB"
    transaction_ids:
      mapping: true          # default: on when the organization has more than one backend
      follower_wait: 5       # seconds to hold copies for a follower that has not yet given its id
      dedupe_start: true     # answer a retried StartTransaction from the stored result
      retain_closed: 86400   # seconds a finished transaction stays in the table
      retain_open: 2592000   # seconds an unfinished transaction is kept without any activity (30 days)
```

| Setting | Default | Meaning |
|---------|---------|---------|
| `mapping` | on with more than one backend, otherwise off | `true` or `false` forces it for this organization. |
| `follower_wait` | `5` | Positive number of seconds. |
| `dedupe_start` | `true` | `false` makes a retried start a second transaction, as without the table. |
| `retain_closed` | `86400` | Positive number of seconds. |
| `retain_open` | `2592000` | Positive number of seconds. A transaction that was never stopped is forgotten after this long without any frame for it. |

A wrong type or a value that is not positive is rejected when the file is loaded. With MongoDB enabled the table is stored there and these two retention values also set when MongoDB removes the records ([details](mongodb-integration.md#the-transaction-id-table-transaction_id_map)); `backends[].id` should then be set, because stored records are matched to backends by it.

### Charger authentication: `charger_auth`

HTTP Basic authentication on the WebSocket upgrade (OCPP 1.6 security profile 1). The username must be the charger id from the URL.

```yaml
organizations:
  - name: "orgA"
    connect_to_backend: false
    charger_auth:
      credentials:
        CP001:
          password_hash: "pbkdf2_sha256$200000$<salt>$<hash>"   # from: python -m ocpp_broker.auth
        CP002: "a-plain-text-key"                                # shorthand for {password: ...}; discouraged
```

- Authentication is enforced for an organization as soon as it lists `credentials` (or sets `required: true`). A charger that is not listed, or sends no or wrong credentials, is refused with HTTP `401` and a `WWW-Authenticate: Basic` header.
- An organization with no `credentials` accepts any charger. A warning is logged at startup.
- `required: true` with no credentials rejects every charger (a warning is logged).
- Generate a hash with `ocpp-broker-hash-password` (or `python -m ocpp_broker.auth`).
- This does not encrypt anything. Put the broker behind a TLS-terminating reverse proxy if the network is not trusted; the broker itself has no TLS settings.

### Initial tags: `tags`

```yaml
    tags:
      - id_tag: "ADMIN001"
        status: "Accepted"          # Accepted | Blocked | Expired | Invalid | ConcurrentTx
        tag_type: "RFID"            # RFID | NFC | QRCode | MobileApp | UserId (default RFID)
        expiry_date: "2030-12-31T23:59:59Z"
        parent_id_tag: null
        description: "Administrator card"
        metadata: {department: "IT"}
```

Tags are loaded into memory at startup and are used to answer `Authorize` and `StartTransaction` in broker mode. See [Tag Management](tag-management.md).

## DataTransfer

Applies to broker mode (relay mode forwards `DataTransfer` untouched). The section is system-wide, not per organization.

```yaml
data_transfer:
  enabled: true                  # false: every DataTransfer is answered NotImplemented
  validate_vendors: true         # default true
  validate_message_ids: true     # default true
  known_vendors: ["ABB", "Siemens"]
  known_message_ids: ["MSG001"]
  vendors:
    ABB:
      allowed_message_ids: ["MSG001"]
      auto_accept: true          # default true; false answers Rejected
      require_message_id: false  # default false; true answers UnknownMessageId when absent
  vendor_messages:
    "ABB:MSG001":
      auto_accept: true
```

Behaviour:

- A vendor not in `known_vendors` or `vendors` is answered `UnknownVendorId`, but only when `validate_vendors` is true and at least one vendor is known. The same rule applies to message ids (`UnknownMessageId`).
- The known message ids are the union of `known_message_ids`, the ids in `vendor_messages` keys and every vendor's `allowed_message_ids`.
- A vendor listed under `vendors` is handled by its settings; otherwise the request is accepted and `data` is echoed back (JSON is re-serialised, anything else returned as is).
- With no `data_transfer` section at all, every DataTransfer is accepted.

## MongoDB

```yaml
mongodb:
  enabled: true
  connection_string: "mongodb://localhost:27017"
  database_name: "ocpp_broker"
```

MongoDB is optional. Without it nothing is persisted, tags live only in memory and transaction ids come from a non-durable counter (a warning is logged). See [MongoDB Integration](mongodb-integration.md).

## Security section

| Setting | Default | Meaning |
|---------|---------|---------|
| `security.api_key` | none | REST API key. The `OCPP_BROKER_API_KEY` environment variable wins over it. |
| `security.allow_unauthenticated_api` | `false` | With no key configured the REST API answers `503` unless this is `true`. |
| `security.cors.allow_origins` | `[]` | Origins allowed to call the API from a browser. Empty means no CORS headers. |
| `security.cors.allow_credentials` | `false` | Ignored (and an error is logged) if `allow_origins` contains `"*"`. |
| `security.websocket.ping_interval` | `20` | Seconds between server pings to each charger. |
| `security.websocket.ping_timeout` | `20` | Seconds to wait for the pong before the connection is closed. |

Requests need `X-API-Key: <key>` or `Authorization: Bearer <key>`. `/health`, `/docs`, `/redoc` and `/openapi.json` do not need the key.

## Web console

| Setting | Default | Meaning |
|---------|---------|---------|
| `ui.enabled` | `true` | Serve the bundled web console at `/ui` on the broker's port. With `false`, every `/ui` path answers `404`. |

The console files are static and need no key to download; the console itself asks for the API key and sends it with every API call. They are on the same port as the chargers, so keep `/ui` off the public internet in the same way as `/api` (see [Deployment](deployment.md)). A release package includes the console; a source checkout answers `503` at `/ui/` until it has been built (`npm ci && npm run build` in `ui/`).

Changing the setting needs a restart, like every other setting; there is no live reload.

## Environment variables

| Variable | Effect |
|----------|--------|
| `OCPP_BROKER_API_KEY` | REST API key (overrides `security.api_key`) |
| `BROKER_HOST`, `BROKER_PORT` | Override `broker.host` / `broker.port` |
| `MONGODB_ENABLED` | `true`, `1`, `yes` or `on` enables MongoDB |
| `MONGODB_CONNECTION_STRING` | Overrides `mongodb.connection_string` (default `mongodb://localhost:27017`) |
| `MONGODB_DATABASE_NAME` | Overrides `mongodb.database_name` (default `ocpp_broker`) |
| `OCPP_BROKER_CONFIG` | The configuration file, when `-c` is not given. |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` (overrides `logging.level`; an unknown value is ignored) |

`env.example` in the repository lists these.

## Accepted but ignored

The loader fills in defaults for the settings below, but nothing in the broker reads them. Changing them has no effect:

- `broker.ocpp_version`, `broker.enable_validation`, `broker.enable_smart_charging`, `broker.enable_firmware_management`, `broker.enable_local_auth`, `broker.enable_reservations`, `broker.enable_tag_management`
- `api.*` and the `API_HOST` / `API_PORT` variables (the REST API shares the broker port)
- `ocpp.validation`, `ocpp.commands.smart_charging|firmware|local_auth|reservations`
- `logging.ocpp_commands`, `logging.tag_management` (only `logging.level` is read)
- `security.ocpp.*`, `security.tags.*`
- top-level `tag_management.*` and `organizations[].tag_management`
- `organizations[].chargers`, `organizations[].backends[].chargers`, `organizations[].ocpp_features`

Also not supported: TLS settings, connection limits, metrics, proxy settings and backend credentials. These keys are not rejected, they are simply never read. Three per-organization keys that used to exist (`validate_messages_when_backend_leader`, `validate_messages_when_broker_backend`, `validation`) now log a warning if present.

## Examples

### Broker mode with authentication

```yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "depot"
    connect_to_backend: false
    charger_auth:
      credentials:
        CP001: {password_hash: "pbkdf2_sha256$200000$<salt>$<hash>"}
    tags:
      - {id_tag: "ADMIN001", status: "Accepted"}
```

### Relay to a leader with an observer

```yaml
organizations:
  - name: "fleet"
    connect_to_backend: true
    backends:
      - {id: main, url: "ws://main.example.com/ocpp", leader: true}
      - {id: audit, url: "ws://audit.example.com/ocpp"}
    leader_failover_timeout: 15
```

### Both modes side by side

```yaml
organizations:
  - name: "external"
    connect_to_backend: true
    backends:
      - {id: vendor, url: "ws://central.example.com/ocpp", leader: true}
  - name: "local"
    connect_to_backend: false
```

## Checking a configuration

```bash
python -c "from ocpp_broker.config import load_config; load_config('config.yaml')"
```

This raises on a YAML syntax error, a non-positive port, a duplicate or missing organization name, or `charger_auth` credentials without a password, and logs the warnings described above. It does not contact any backend or MongoDB. There is no `--dry-run` option.

## Common mistakes

- **Relay organization without `backends`** (often caused by leaving out `connect_to_backend`, which defaults to `true`).
- **Backend `url` that already ends in the charger id.** The broker appends `/{charger_id}` itself.
- **A wrong `logging.level`.** Only `DEBUG`, `INFO`, `WARNING`, `ERROR` and `CRITICAL` are accepted; anything else stops the broker at startup. `DEBUG` also turns on the WebSocket libraries' own debug output, which is very verbose.
- **REST calls returning `503`.** No API key is configured; set `OCPP_BROKER_API_KEY`.

## Related documentation

- [Quick Start](quick-start.md)
- [Broker-as-Backend Mode](broker_as_backend.md)
- [Leader/Follower](leader-follower.md)
- [Tag Management](tag-management.md)
- [MongoDB Integration](mongodb-integration.md)
- [Troubleshooting](troubleshooting.md)
