# System Architecture

How the OCPP broker is put together: the modules in `src/ocpp_broker/`, how a charger connection flows through them, and what is (and is not) concurrent.

## Overview

The broker is a **single Python process** running one asyncio event loop and one uvicorn server on one port (default `8765`). That one port serves the charger WebSocket endpoint, the REST API, `/health` and the Swagger docs. Everything is in memory: a charger's session lives in the process it connected to.

Each organization runs in one of two modes, chosen by `connect_to_backend` (default `true`):

```
BROKER mode (connect_to_backend: false)        RELAY mode (connect_to_backend: true)

  charger <--ws--> ChargerSession                charger <--ws--> ChargerSession
                      |                                              |
              BrokerChargePoint                          +-----------+-----------+
          (ocpp library, v1.6 handlers,                  |                       |
           schema validation)                     leader BackendConnection   follower BackendConnection(s)
                      |                           (all frames, store-and-      (copy of charger CALLs,
        TagManager / MongoDB /                     forward outbox)              observe-only)
        DataTransferHandler                              |                       |
                                                   leader backend          follower backend(s)
```

- **Broker mode**: the broker is the central system. It answers the ten charger-initiated actions it implements (BootNotification, Authorize, Heartbeat, DiagnosticsStatusNotification, FirmwareStatusNotification, StatusNotification, MeterValues, StartTransaction, StopTransaction, DataTransfer). Any other action gets a `NotImplemented` CallError. The `ocpp` library validates every CALL and CALLRESULT against the OCPP 1.6 JSON schemas.
- **Relay mode**: the broker is a transparent proxy to one or more backend central systems, with one set of backend sockets per charger. Frames are forwarded unchanged and are not validated, with one exception: when an organization has more than one backend, the transaction ids in six message types are translated so each backend is spoken to in its own ids (see [Transaction ids](leader-follower.md#transaction-ids)). See [Leader-Follower](leader-follower.md) for multi-backend behaviour.

## Modules

| Module | Role |
|---|---|
| `server.py` | FastAPI app and uvicorn setup (entry point `ocpp-broker-server`). Defines the charger WebSocket endpoint, `/ocpp-check`, `/health`, CORS, and the uvicorn WebSocket ping settings. |
| `api_server.py` | The REST routers (tags, OCPP commands, MongoDB data, backend listing), all mounted on the same app behind the API key. |
| `broker.py` | `OcppBroker`: the global orchestrator. Owns the sessions, configuration, MongoDB service, tag manager and transaction-id allocation. |
| `session.py` | `ChargerSession`: one per connected charger. Runs broker or relay mode, sends remote commands, fans out to followers, handles failover. |
| `backend_manager.py` | `BackendConnection`: one WebSocket from the broker to one backend for one charger, with reconnect and the store-and-forward outbox. |
| `charge_point.py` | `BrokerChargePoint` (subclass of the `ocpp` library's v1.6 `ChargePoint`) and `StarletteWebSocketAdapter`. Broker-mode message handlers. |
| `tag_manager.py` | `TagManager`: id-tag storage, search, import/export, and the authorization decision. |
| `data_transfer_handler.py` | `DataTransferHandler`: decides the reply to a charger's DataTransfer. |
| `mongodb_service.py` | `MongoDBService`: optional persistence through `motor`. |
| `auth.py` | Charger HTTP Basic auth, API-key dependency, password hashing (`ocpp-broker-hash-password`). |
| `config.py` | Loads `config.yaml`, applies defaults and environment overrides, validates. |
| `sockets.py` | `locked_send`: serialised, time-bounded socket writes. |
| `middleware.py` | `process_charger_to_backend`: parses a relay frame as JSON for logging/routing and returns it unchanged. A pass-through, not a pipeline. |
| `transaction_ids.py` | `TransactionIdTable`: relay mode with followers. One record per transaction, mapping the id the charger holds to each backend's own id, plus the per-follower copy queue. Pure logic with no I/O; `session.py` feeds it frames. The broker keeps one table per charger so it outlives a socket. |
| `registry.py` | `ChargerRegistry`: a per-organization set of charger ids, written on BootNotification in broker mode. Nothing reads it. |

### server.py: the entry point

`server.py` creates the FastAPI `app`, the global `OcppBroker`, and mounts the REST routers. At startup (`main_async`) it loads the configuration, initialises MongoDB if enabled, creates the tag manager, applies CORS, then runs uvicorn on `broker.host:broker.port`.

Charger endpoint `ws://HOST:8765/{org_name}/{charger_id}`, in order:

1. If the organization requires charger authentication, check the HTTP Basic `Authorization` header (username must equal the charger id). On failure the upgrade is refused with HTTP 401.
2. Require a `Sec-WebSocket-Protocol` header that includes the organization's `ocpp_subprotocol` (default `ocpp1.6`); otherwise the upgrade is refused (the client sees HTTP 403).
3. Accept the socket and call `OcppBroker.handle_charger`.

`handle_charger` closes with 4002 if the organization is unknown. The broker has no TLS; terminate TLS in a reverse proxy.

REST routes sit behind an API key (`X-API-Key` header or `Authorization: Bearer`, from `OCPP_BROKER_API_KEY` or `security.api_key`). With no key configured they return 503 unless `security.allow_unauthenticated_api` is true. `/health`, `/docs`, `/redoc` and `/openapi.json` need no key. CORS is off unless `security.cors.allow_origins` lists origins. There is no separate API port.

### broker.py: OcppBroker

- `sessions` is a dict keyed by `(org_name, charger_id)`, so the same charger id in two organizations is two independent sessions.
- **Duplicate connect**: when a charger connects again while its session is still open, the new session replaces the old one in the dict and the old socket is closed with code 4003 (it gets a 5 second grace period to unwind, then its task is cancelled).
- `org_backends` records, for relay-mode chargers, the leader and follower `BackendConnection` objects. `GET /orgs/{org}/backends` reads it.
- `next_transaction_id`: a per-organization atomic counter in MongoDB (first id 1). Without MongoDB it falls back to an in-memory counter seeded from the clock, with a loud warning (ids are not durable).
- `forward_backend_message` is the callback for messages arriving from a backend: followers' messages are ignored, the leader's are written to the charger.
- Creates the `TagManager` at startup, so the REST tag API works before any charger connects.

### session.py: ChargerSession

Created per connection. `start()` picks the mode and runs it until the charger disconnects; `close()` cancels background tasks, closes all backend connections and fails any pending REST command.

- **Broker mode**: wraps the Starlette WebSocket in `StarletteWebSocketAdapter`, creates a `BrokerChargePoint` and runs its `start()` loop (the library's receive/route loop).
- **Relay mode**: creates the leader `BackendConnection` and one `BackendConnection` per follower (without waiting for them to connect), then loops: receive a charger frame, hand it to a waiting REST command if it is that command's reply, otherwise send to the leader and fan a copy of CALLs out to followers.
- **Send lock**: one `asyncio.Lock` per charger socket, shared with the adapter, so frames written by different tasks never interleave.
- **`send_command`**: the entry point for REST remote commands (below).
- **Failover**: promotes a follower when the leader stays down; see [Leader-Follower](leader-follower.md).

### backend_manager.py: BackendConnection

Owns one outbound WebSocket (`{backend.url}/{charger_id}`, configured subprotocol, ping every 20s, 1 MiB max message). It reconnects with backoff (1s, doubling to 30s), keeps a bounded outbox while the backend is down, flushes it in order on reconnect, and reports undeliverable frames back to the session. It has its own per-socket send lock.

### charge_point.py: BrokerChargePoint

Handlers are declared with the library's `@on("Action")` decorator and return `call_result` objects. They call the tag manager for authorization, `broker.next_transaction_id` for StartTransaction, `DataTransferHandler` for DataTransfer, and save to MongoDB when it is connected. BootNotification replies `Accepted` with `interval` taken from `ocpp.commands.core.heartbeat_interval` (default 300). `StarletteWebSocketAdapter` exposes the `recv`/`send`/`close` interface the library expects on top of the Starlette socket, and writes through the shared send lock.

### tag_manager.py, data_transfer_handler.py, mongodb_service.py

- **TagManager** keeps tags in memory per organization (loaded from `organizations[].tags` in the config, plus anything added through the REST API) and uses MongoDB when connected. `authorize_tag` returns `Invalid` for an unknown tag, `Expired` for a past or unreadable expiry date, otherwise the tag's stored status. See [Tag Management](tag-management.md).
- **DataTransferHandler** is created on the first DataTransfer. It uses the system-wide `data_transfer.*` config. See [OCPP 1.6 Features](ocpp16_features.md).
- **MongoDBService** is optional. Nothing is persisted without it. See [MongoDB Integration](mongodb-integration.md).

## Charger lifecycle

1. `server.py` authenticates the upgrade, checks the subprotocol and accepts the socket.
2. `OcppBroker.handle_charger` looks up the organization, builds a `ChargerSession`, registers it in `sessions` (evicting any previous session for the same org and charger id), and calls `session.start()`.
3. The session runs in broker or relay mode until the socket closes or an error occurs.
4. In a `finally` block the session is closed and removed from `sessions` (only if it is still the registered one).

## Remote command round trip

A REST call to `POST /api/ocpp/organizations/{org}/chargers/{id}/commands[/{Action}]` looks up `sessions[(org, id)]` (404 if absent), then `ChargerSession.send_command` waits for the charger's actual reply.

**Broker mode** (through `BrokerChargePoint.call`):

```
REST client
   |  POST .../commands
   v
api_server.send_ocpp_command -- session = sessions[(org, id)]
   v
ChargerSession.send_command
   v
_command_via_charge_point
   |  check the action is an ocpp.v16 CALL and the payload fits its JSON schema
   |  (invalid -> 422, nothing sent)
   v
BrokerChargePoint.call(request, unique_id=message_id)   <- library call lock: one CALL
   |                                                        in flight per charger
   v
StarletteWebSocketAdapter.send --> charger
                                      |
BrokerChargePoint.start() receive loop <-- CALLRESULT / CALLERROR
   |  library matches the reply by message id
   v
CommandResult (success | error | timeout) --> HTTP response
```

**Relay mode** (the reply is intercepted by message id):

```
REST client
   |  POST .../commands
   v
ChargerSession.send_command
   v
_command_via_relay
   |  register a future in _pending_calls[message_id]
   |  send [2, message_id, action, payload] to the charger exactly as given
   |  (no payload validation; any action name is accepted)
   v
charger --> CALLRESULT / CALLERROR
   |
_relay_loop receives the frame
   |  _resolve_pending_call: id matches a pending REST command?
   |     yes -> resolve the future; the frame is NOT forwarded to any backend
   |     no  -> forward to leader (and CALL copies to followers) as usual
   v
CommandResult --> HTTP response
```

In both modes `timeout` (default 30s, 1 to 300) bounds the wait. Outcomes: `success`/`error` return HTTP 200, `timeout` returns 504 with the same body, a charger that is or becomes disconnected returns 503. The last 1000 outcomes are kept in memory for `GET /api/ocpp/commands/{message_id}/response`. In relay mode, several commands to the same charger may be outstanding at once; in broker mode they queue behind each other because of the library's call lock.

## Concurrency and liveness

- **One process, one event loop.** There is no worker pool and no inter-process state. Run one broker per set of chargers; sessions and the tag cache are per process (use `POST /api/tags/sync` to reload tags from MongoDB). Transaction ids stay unique across processes only when MongoDB supplies them.
- **Per-socket write locks.** Every write to a charger socket goes through that session's lock, and every write to a backend socket through that connection's lock (`sockets.locked_send`). A single write that takes longer than 10s raises a timeout; for the session's own sends the charger socket is then closed with code 1011.
- **Ping/pong watchdog.** uvicorn pings charger sockets every `security.websocket.ping_interval` (default 20s) and drops a socket that does not answer within `ping_timeout` (default 20s). Backend sockets ping every 20s with a 20s timeout.
- **Auth off the event loop.** Charger password hashing (PBKDF2) runs in a worker thread.
- **Background tasks.** Follower copies and the failover watcher run as asyncio tasks owned by the session and are cancelled when it closes.
- **Logging.** Standard library logging to stderr at a fixed INFO level.

## Related Documentation

- [Installation Guide](installation.md)
- [Configuration Guide](configuration.md)
- [Leader-Follower](leader-follower.md)
- [OCPP 1.6 Features](ocpp16_features.md)
- [Broker-as-Backend Mode](broker_as_backend.md)
- [API Reference](api-reference.md)
- [Production Deployment](deployment.md)
