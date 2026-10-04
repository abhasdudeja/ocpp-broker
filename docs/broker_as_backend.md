# Broker-as-Backend Mode

In broker mode the broker is the central system: chargers connect to it and it answers them itself, with no external backend. An organization is in broker mode when `connect_to_backend: false`.

```
Charger <--- WebSocket ---> Broker (central system)
```

Compare relay mode (`connect_to_backend: true`), where the broker forwards frames to your own backends; see [Leader/Follower](leader-follower.md). To answer the charger itself **and** copy everything it says to external backends, mark one backend `local: true` ([the broker as the leader](leader-follower.md#the-broker-as-the-leader-local-backend)); everything below applies to that case too.

## Configuration

```yaml
organizations:
  - name: "LocalCharging"
    connect_to_backend: false      # required: the default is true (relay mode)
    ocpp_subprotocol: "ocpp1.6"    # optional, this is the default
    tags:                          # who may charge
      - id_tag: "ADMIN001"
        status: "Accepted"
    charger_auth:                  # optional HTTP Basic credentials per charger
      credentials:
        CP001: {password_hash: "pbkdf2_sha256$200000$<salt>$<hash>"}

ocpp:
  commands:
    core:
      heartbeat_interval: 300      # seconds, sent in the BootNotification reply
```

Chargers connect to `ws://HOST:8765/LocalCharging/{charger id}`. Details of every setting are in the [Configuration Guide](configuration.md).

## What the broker handles

The broker implements every message an OCPP 1.6 charger can initiate. Each request is validated against the OCPP 1.6 schema by the `ocpp` library before the broker looks at it; a request that does not fit the schema is answered with a CALLERROR.

| Charger sends | Broker does | Reply |
|---------------|-------------|-------|
| `BootNotification` | Records the charger in MongoDB (`charger_configurations`) when enabled | Always `Accepted`, with `currentTime` and `interval` = `ocpp.commands.core.heartbeat_interval` (default 300) |
| `Heartbeat` | Stores the latest heartbeat time in MongoDB (heartbeats are not kept individually) | `currentTime` |
| `StatusNotification` | Stores it in MongoDB (`charger_statuses`, and the latest per connector in `charger_statuses_latest`) | empty |
| `Authorize` | Looks the tag up in the organization's tags (see below) | `idTagInfo` |
| `StartTransaction` | Authorizes the tag, allocates a transaction id, stores the transaction | `transactionId` and `idTagInfo` |
| `StopTransaction` | Authorizes the tag if one is given, updates the stored transaction | `idTagInfo` |
| `MeterValues` | Stores the readings in MongoDB (`meter_values`) | empty |
| `DataTransfer` | Applies the `data_transfer` rules ([Configuration](configuration.md#datatransfer)) | `status` and optional `data` |
| `DiagnosticsStatusNotification` | Logs it and stores the raw message | empty |
| `FirmwareStatusNotification` | Logs it and stores the raw message | empty |

MongoDB writes happen only when MongoDB is enabled. Without it the broker still answers every message, but nothing is persisted.

Anything else a charger sends is answered with a CALLERROR: `NotImplemented` for a known OCPP 1.6 action (for example `ReserveNow`, which only the central system may send) and `NotSupported` for an unknown action. A payload that violates the schema gets a CALLERROR (for example `ProtocolError` for a missing required field) whose `cause` says why.

### Authorization

`Authorize`, `StartTransaction` and `StopTransaction` use the organization's tags ([Tag Management](tag-management.md)):

| Tag | `idTagInfo.status` |
|-----|--------------------|
| not in the tag list | `Invalid` |
| listed, `expiry_date` in the past (or not a readable date) | `Expired` |
| listed | the tag's own status (`Accepted`, `Blocked`, `Expired`, `Invalid`, `ConcurrentTx`) |

`parentIdTag` and `expiryDate` are returned when the tag has them.

### Transactions

- `StartTransaction` **always** gets a `transactionId`, even when the tag is not accepted. `idTagInfo.status` tells the charger whether to continue; per OCPP a charger must stop the transaction if it is not `Accepted`.
- Transaction ids come from a per-organization counter in MongoDB (first id is 1, atomic, survives restarts). Without MongoDB the broker falls back to an in-memory counter seeded from the clock and logs a `TRANSACTION IDS ... ARE NOT DURABLE` warning once per organization. Those ids are only unique within the process and may collide with ids from before a restart.
- A charger that did not get the answer to a `StartTransaction` sends it again. The broker recognises the retry by its four fixed facts (connector, tag, meter reading and timestamp) and answers with the **same transaction id**, with the tag judged again, and stores the transaction once. A different start is a new transaction.
- The broker remembers, per charger, which transactions it numbered and which are open (up to 100 open and 200 finished ones, finished ones for 24 hours). A `StopTransaction` is **always answered**, since a central system cannot refuse one, but a repeated stop is recognised (answered again, stored once) and a stop for a transaction the broker did not start, or no longer remembers, is logged as a warning (and still stored). The meter readings sent with a stop (`transactionData`) are stored with the transaction (MongoDB field `transaction_data`, in the library's snake_case like other stored readings). The web console lists these transactions.
- This memory is in the broker's process only: it survives a charger reconnecting but not a broker restart, after which a retried start gets a new id. A `StopTransaction` without an `idTag` is still answered `idTagInfo: Invalid`.
- Reservations, smart-charging profiles and local-list contents are not tracked by the broker. They are sent to chargers on request (below).

## Commands from the broker to chargers

The central system initiates these through the [REST API](api-reference.md#ocpp-commands-apiocpp). The broker sends the request through the `ocpp` library, validates it against the OCPP 1.6 schema (`422` if invalid), waits for the charger and returns the charger's reply:

```bash
curl -X POST http://localhost:8765/api/ocpp/organizations/LocalCharging/chargers/CP001/commands/ChangeAvailability \
  -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"connector_id": 1, "type": "Inoperative"}'
```

Typed routes exist for `CancelReservation`, `ChangeAvailability`, `ChangeConfiguration`, `ClearCache`, `ClearChargingProfile`, `DataTransfer`, `GetCompositeSchedule`, `GetConfiguration`, `GetDiagnostics`, `GetLocalListVersion`, `RemoteStartTransaction`, `RemoteStopTransaction`, `ReserveNow`, `Reset`, `SendLocalList`, `SetChargingProfile`, `TriggerMessage`, `UnlockConnector` and `UpdateFirmware`. The generic `/commands` route accepts any OCPP 1.6 central-system action.

## Example exchange

```
> [2,"1","BootNotification",{"chargePointVendor":"Acme","chargePointModel":"X1"}]
< [3,"1",{"currentTime":"2026-10-03T06:26:03.605560+00:00","interval":300,"status":"Accepted"}]

> [2,"4","Authorize",{"idTag":"ADMIN001"}]
< [3,"4",{"idTagInfo":{"status":"Accepted","parentIdTag":"ROOT"}}]

> [2,"6","Authorize",{"idTag":"NOBODY"}]
< [3,"6",{"idTagInfo":{"status":"Invalid"}}]

> [2,"7","StartTransaction",{"connectorId":1,"idTag":"NOBODY","meterStart":0,"timestamp":"2026-01-01T12:00:00Z"}]
< [3,"7",{"transactionId":1791008765,"idTagInfo":{"status":"Invalid"}}]

> [2,"15","DataTransfer",{"vendorId":"Other"}]
< [3,"15",{"status":"UnknownVendorId"}]

> [2,"18","ReserveNow",{"connectorId":1}]
< [4,"18","NotImplemented","Request Action is recognized but not supported by the receiver",{"cause":"No handler for ReserveNow registered."}]
```

(The large `transactionId` is the clock-seeded fallback used when MongoDB is off.) `DataTransfer` with an unknown vendor was answered by a config with `data_transfer.known_vendors: ["ABB"]`.

## Behaviour to know about

- **Session lifecycle:** one session per `(organization, charger id)`. If the same charger connects again the old connection is closed with code `4003` and replaced. The broker pings chargers every 20 s and drops a connection that does not answer within another 20 s.
- **Persistence and restarts:** without MongoDB, tags added through the API and everything else in memory are lost on restart; only the tags in `config.yaml` come back. With MongoDB see [MongoDB Integration](mongodb-integration.md).
- **Scale:** one process, with sessions held in memory. Running several broker instances needs a load balancer that keeps a charger on one instance, and tag changes must be re-synced (`POST /api/tags/sync`).
- **Logging:** each handled request is logged at INFO (`BootNotification received (Acme / X1) ...`, `Authorize for ADMIN001 → Accepted`, ...). The log level is fixed at INFO.

## Moving an organization to or from broker mode

Switching is a configuration change and a restart:

1. Set `connect_to_backend` to `false` (or `true` and add `backends`).
2. Restart `ocpp-broker-server`; chargers reconnect on their own retry schedule.

Moving to broker mode means the broker now owns authorization and transaction ids. Existing transactions that the old backend started are not known to the broker; ids restart from the MongoDB counter, so make sure they do not overlap with ids the chargers still hold.

## Related documentation

- [Configuration Guide](configuration.md)
- [API Reference](api-reference.md)
- [Tag Management](tag-management.md)
- [MongoDB Integration](mongodb-integration.md)
- [Architecture](architecture.md)
