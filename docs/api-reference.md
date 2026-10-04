# API Reference

The broker exposes a charger WebSocket endpoint and a REST API on **one port** (`broker.port`, default `8765`). Interactive documentation generated from the code is always available at `/docs` (Swagger UI), `/redoc` and `/openapi.json`; this page describes behaviour the generated docs cannot.

All examples assume:

```bash
export BROKER=http://localhost:8765
export OCPP_BROKER_API_KEY=change-me     # the key the broker was started with
```

## Authentication

Every REST route under `/api` and `/orgs` requires the API key, sent as either header:

```
X-API-Key: <key>
Authorization: Bearer <key>
```

The broker reads the key from the `OCPP_BROKER_API_KEY` environment variable or `security.api_key` in `config.yaml` (the variable wins).

| Situation | Response |
|-----------|----------|
| Missing or wrong key | `401` `{"detail": "Missing or invalid API key"}` with `WWW-Authenticate: Bearer` |
| No key configured | `503` `{"detail": "REST API disabled: set security.api_key or OCPP_BROKER_API_KEY"}` |
| No key configured and `security.allow_unauthenticated_api: true` | open access (development only) |

`/health`, `/docs`, `/redoc` and `/openapi.json` need no key.

## Errors

There is no custom error envelope. Errors use FastAPI's default body:

```json
{"detail": "Charger orgA/NOPE not connected"}
```

Request bodies that fail validation return `422` with a list:

```json
{"detail": [{"type": "missing", "loc": ["body", "type"], "msg": "Field required", "input": {}}]}
```

| Status | Meaning |
|--------|---------|
| `400` | The operation was refused (for example adding a tag that already exists) or import text could not be parsed |
| `401` | Missing or wrong API key |
| `404` | Charger not connected, tag not found, or organization unknown |
| `422` | Invalid request body, or (broker mode) an OCPP command that fails the OCPP 1.6 schema |
| `503` | No API key configured, MongoDB not available, tag management unavailable, or the charger is unreachable |
| `504` | The charger did not answer a command in time |

There is no rate limiting.

## Health

```http
GET /health
HEAD /health
```

```json
{"status": "ok"}
```

Always `200` while the process is serving. It does not check MongoDB or backends.

```http
GET /api/mongodb/health
```

```json
{"status": "not_configured", "connected": false}
```

When MongoDB is configured: `{"status": "connected" | "disconnected", "connected": true | false, "database": "ocpp_broker"}`. `connected` only reflects whether the connection at startup succeeded; it is never re-checked.

## System information

```http
GET /api/system/info
```

```json
{
  "version": "0.5",
  "instance_id": "3f9c1a7e",
  "started_at": "2026-10-04T08:12:31.402117Z",
  "uptime_seconds": 5231.4,
  "api_auth": "api_key",
  "ui_enabled": true,
  "ui_built": true,
  "organizations": 2,
  "connected_chargers": 14,
  "mongodb": {"configured": true, "connected": true, "reachable": true, "database": "ocpp_broker"}
}
```

Needs the API key, so it is also the cheapest call to check a key with (`401` wrong or missing key, `503` no key configured). `instance_id` is random per process. `connected_chargers` counts this process only, because sessions are not shared between instances. Unlike the other MongoDB health route, `mongodb.reachable` pings the server on every call (2 second limit), while `connected` is the startup result.

## Console endpoints (organizations and chargers)

Read-only views of **this process**, used by the [web console](web-console.md). Sessions are per process, so with several broker instances each one answers for its own chargers only. All need the API key.

```http
GET /api/orgs
```

One entry per configured organization:

```json
[
  {
    "name": "Fleet", "mode": "relay", "ocpp_version": "1.6",
    "charger_auth_required": true, "connected_chargers": 14,
    "backends": [
      {"key": "lead", "url": "ws://primary.example.com/ocpp", "local": false, "leader": true, "ocpp_subprotocol": "ocpp1.6"},
      {"key": "follow", "url": "ws://standby.example.com/ocpp", "local": false, "leader": false, "ocpp_subprotocol": "ocpp1.6"}
    ],
    "transaction_id_mapping": true
  }
]
```

`mode` is `broker` (the broker answers the chargers) or `relay` (a backend does). `backends` is empty in plain broker mode; with a [local leader](leader-follower.md#the-broker-as-the-leader-local-backend) the organization is `broker` mode and lists `{"key": "broker", "url": null, "local": true, "leader": true, ...}` followed by its followers. `leader` is the configured leader (a failover changes who leads a charger, see below). `key` is the backend's `id`, or its URL if it has none (`broker` for a local backend without an `id`).

```http
GET /api/chargers?org=Fleet&q=cp
```

The chargers connected to this instance, ordered by organization and id. `org` limits to one organization; `q` keeps ids containing the text (case-insensitive). Only connected chargers exist here: a charger that is not connected is simply absent.

```json
{
  "total": 1,
  "chargers": [
    {
      "org": "Fleet", "charger_id": "CP001", "online": true, "mode": "relay", "ocpp_version": "1.6",
      "connected_at": "2026-10-04T08:12:31Z", "last_seen": "2026-10-04T09:01:07Z", "remote_address": "10.0.0.5:51234",
      "vendor": "Acme", "model": "Wallbox 7", "firmware_version": "2.1.0",
      "connector_statuses": {"0": "Available", "1": "Charging"},
      "leader": {"key": "lead", "url": "ws://primary.example.com/ocpp", "role": "leader", "local": false,
                 "connected": true, "buffered_frames": 0, "down_for_seconds": null},
      "followers_total": 1, "followers_connected": 1, "buffered_frames": 0,
      "open_transactions": 1, "degraded_transactions": 0
    }
  ]
}
```

- `vendor`, `model`, `firmware_version` and `connector_statuses` come from the charger's own `BootNotification` and `StatusNotification` messages, which the broker watches in both modes. They are empty until the charger has sent them. `connector_statuses` key `0` is the charger as a whole.
- `remote_address` is the peer address the broker sees: behind a reverse proxy that is the proxy's address.
- `leader` is the charger's current leader. In broker mode it is the broker itself (`"local": true`, `"url": null`). `buffered_frames` is how many charger frames are waiting for an unreachable leader ([store-and-forward](leader-follower.md#leader-outage-store-and-forward)); `down_for_seconds` is how long a link has been down.
- `degraded_transactions` counts running transactions in which some backend is being skipped because the broker never learned its transaction id ([details](leader-follower.md#transaction-ids)).

```http
GET /api/chargers/{org}/{charger_id}
```

The same fields plus the full picture of one charger: `boot` (vendor, model, serial number, firmware, ICCID, IMSI, meter details, when it arrived), `last_heartbeat_at`, `connectors` (status, error code, info, time), `backends` (the leader first, then the followers, each with `key`, `url`, `role`, `local`, `connected`, `buffered_frames`, `down_for_seconds`), `transaction_id_mapping` (whether ids are translated for this charger), `transactions` (one row per transaction: the id the charger holds, its state `pending`/`open`/`closed`, each backend's own id by key, which backends are skipped, which have not yet answered), `reservations` and `charging_profiles` (the ids the charger holds with each backend's own id), `frames_in` / `frames_out` (messages received from and sent to the charger on this connection) and `id_table_stats` (what the id table has done: `rewritten`, `remapped`, `skipped`, ...).

`404` if the charger is not connected to this instance.

```http
GET /api/chargers/{org}/{charger_id}/commands
```

The commands sent to this charger through the API or the web console while it stayed connected, newest first, at most the last 50:

```json
{
  "commands": [
    {
      "message_id": "6f1c0c7e-...", "action": "ChangeAvailability", "status": "success",
      "payload": {"connectorId": 1, "type": "Inoperative"}, "response": {"status": "Accepted"}, "error": null,
      "sent_at": "2026-10-04T09:01:07Z", "finished_at": "2026-10-04T09:01:07Z", "duration_ms": 120
    }
  ]
}
```

`status` is `pending`, `success`, `error` (the charger refused it, or it could not be sent), `timeout` or `cancelled` (the HTTP caller went away). A command rejected as invalid (`422`) was never sent and is not listed. The history belongs to the connection: it is empty again after the charger reconnects, and it is kept in memory only. The value of a `ChangeConfiguration` for the `AuthorizationKey` key, and the same key in a `GetConfiguration` answer, is replaced by `***`. `404` if the charger is not connected to this instance.

### Live events

```http
GET /api/events?org=Fleet&charger_id=CP001&replay=50
```

A [Server-Sent Events](https://html.spec.whatwg.org/multipage/server-sent-events.html) stream (`Content-Type: text/event-stream`) of what happens on **this instance**, used by the [web console](web-console.md#live-updates). It needs the API key like every other route, sent as the `X-API-Key` header. The browser's `EventSource` cannot send headers, so read it with `fetch` or any HTTP client that can:

```bash
curl -N -H "X-API-Key: $KEY" "http://localhost:8765/api/events?replay=5"
```

```text
event: stream.open
data: {"instance_id": "a1b2c3d4", "last_id": 41, "missed": false}

id: 38
event: charger.status
data: {"id": 38, "type": "charger.status", "time": "2026-10-04T09:01:07+00:00", "org": "Fleet", "charger_id": "CP001", "data": {"connector_id": 1, "status": "Charging", "previous": "Preparing", "error_code": "NoError"}}

: keepalive
```

- `org` and `charger_id` limit the stream to one organization or charger. `replay` (0 to 200, default 0) starts a first connection with that many of the latest matching events.
- The first message is always `stream.open`. It is not a numbered event. It carries this run's `instance_id` and `missed`, which is true when the client asked to resume with `Last-Event-ID` but some of what it missed is no longer remembered, or the id comes from another run of the broker. A client that sees `missed` should reload whatever it shows.
- Every other message is an event with an increasing `id` (per run of the broker), an `event:` name equal to its `type`, and one JSON `data:` line with `id`, `type`, `time`, `org`, `charger_id` and `data`.
- To resume after a dropped connection send the last `id` seen as the `Last-Event-ID` header. The broker remembers the last 1000 events.
- A comment line `: keepalive` is sent every 15 seconds when nothing else is, so proxies do not close an idle stream. A client that falls 256 events behind is disconnected (reconnect with `Last-Event-ID` to catch up), and at most 100 streams are served at once (`503` beyond that).
- When the broker stops it ends open streams first (the server would otherwise wait for them forever), so a client sees its stream close and reconnects.
- Events say *what happened*, never what was said: no OCPP payloads, no id tags and no command payloads.

| `type` | `data` |
|--------|--------|
| `charger.connected` | `mode`, `ocpp_version`, `remote_address` |
| `charger.disconnected` | `connected_for_seconds`. Not published when a newer connection had already replaced this one |
| `charger.replaced` | none: the charger connected again while an old connection was still open, which was closed |
| `charger.boot` | `vendor`, `model`, `firmware_version` from its `BootNotification` |
| `charger.status` | `connector_id` (0 is the whole charger), `status`, `previous` (null the first time), `error_code`. Only sent when the status or error code changed |
| `backend.link` | `backend` (its key), `role` (`leader` or `follower`), `connected`. Sent when a link comes up and once when it goes down, not on every failed retry |
| `backend.failover` | `old_leader`, `new_leader` (backend keys) |
| `transaction.started` | `connector_id`, `meter_start`: the charger asked to start one (ids are not yet known at this point) |
| `transaction.stopped` | `transaction_id` (the id the charger holds), `meter_stop`, `reason` |
| `command.result` | `message_id`, `action`, `status` (`success`, `error`, `timeout`, `cancelled`), `error` |

Events are not stored: after a restart the numbering starts again, and a console opened later sees only what it asks `replay` for.

## Charger WebSocket

```
ws://HOST:8765/{org_name}/{charger_id}
```

| Requirement | Detail |
|-------------|--------|
| Subprotocol | The client must send `Sec-WebSocket-Protocol` including the organization's `ocpp_subprotocol` (default `ocpp1.6`). Otherwise the upgrade is refused with HTTP `403` (nothing is accepted, so the client sees a failed handshake, not a close code). |
| Authentication | If the organization has `charger_auth.credentials`, send `Authorization: Basic base64(charger_id:password)`. Failure is HTTP `401` with `WWW-Authenticate: Basic` on the upgrade. |
| Organization | Must exist in the configuration. Otherwise the socket is accepted and closed with code `4002` (`Unknown organization`). |
| Liveness | The server pings every `security.websocket.ping_interval` seconds and closes a socket that does not answer within `ping_timeout`. |
| Reconnects | A second connection with the same organization and charger id closes the first one with code `4003` and replaces it. |

```python
import asyncio, base64, json, websockets

async def main():
    token = base64.b64encode(b"CP001:my-key").decode()
    async with websockets.connect(
        "ws://localhost:8765/orgA/CP001",
        subprotocols=["ocpp1.6"],
        additional_headers={"Authorization": f"Basic {token}"},   # only if the org uses charger_auth
    ) as ws:
        await ws.send(json.dumps([2, "1", "BootNotification",
                                  {"chargePointVendor": "Acme", "chargePointModel": "X1"}]))
        print(await ws.recv())

asyncio.run(main())
```

```
[3,"1",{"currentTime":"2026-10-03T06:22:22.088867+00:00","interval":300,"status":"Accepted"}]
```

`GET /ocpp-check` is a WebSocket test endpoint that accepts an `ocpp1.6`, `ocpp2.0` or `ocpp2.0.1` subprotocol and closes immediately. There is no WebSocket endpoint for backends: the broker connects out to them.

OCPP-J frames: CALL `[2, id, "Action", {payload}]`, CALLRESULT `[3, id, {payload}]`, CALLERROR `[4, id, "Code", "Description", {details}]`.

In broker mode the broker answers `BootNotification`, `Authorize`, `Heartbeat`, `StatusNotification`, `MeterValues`, `StartTransaction`, `StopTransaction`, `DataTransfer`, `DiagnosticsStatusNotification` and `FirmwareStatusNotification`. Any other action gets a `NotImplemented` CALLERROR. See [Broker-as-Backend](broker_as_backend.md).

## OCPP commands (`/api/ocpp`)

Send an OCPP request to a connected charger and get its reply. The call **waits** for the charger (up to the timeout).

### Connected chargers

```http
GET /api/ocpp/organizations/{org_name}/chargers
GET /api/ocpp/organizations/{org_name}/chargers/{charger_id}/status
```

```json
{"organization": "orgA", "chargers": [{"charger_id": "CP001", "organization": "orgA", "mode": "broker", "connected": true}]}
```

```json
{"charger_id": "CP001", "organization": "orgA", "mode": "broker", "connected": true, "has_backend": false}
```

`mode` is `broker` or `relay`. `has_backend` is true when the session holds a backend link (relay mode). Only chargers connected right now are listed; an unknown or disconnected charger is `404`.

### Generic command

```http
POST /api/ocpp/organizations/{org_name}/chargers/{charger_id}/commands
```

```bash
curl -X POST "$BROKER/api/ocpp/organizations/orgA/chargers/CP001/commands" \
  -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"action": "Reset", "payload": {"type": "Soft"}, "timeout": 10}'
```

| Field | Description |
|-------|-------------|
| `action` | Any OCPP 1.6 central-system-to-charger action |
| `payload` | The OCPP payload with **camelCase** keys (`connectorId`, `idTag`) |
| `timeout` | Seconds to wait for the charger, 1-300, default 30 |

### Typed commands

`POST .../chargers/{charger_id}/commands/{Action}` builds the OCPP payload from a **snake_case** body. These routes always use the default 30 second timeout; use the generic route to choose another.

| Route | Body fields |
|-------|-------------|
| `ChangeAvailability` | `connector_id` (0 = whole charger), `type` (`Inoperative` or `Operative`) |
| `ChangeConfiguration` | `key`, `value` |
| `ClearCache` | none |
| `DataTransfer` | `vendor_id`, optional `message_id`, `data` |
| `GetConfiguration` | optional `key` (list); send `{}` for all keys |
| `RemoteStartTransaction` | `id_tag`, optional `connector_id`, `charging_profile` (object) |
| `RemoteStopTransaction` | `transaction_id` |
| `Reset` | `type` (`Hard` or `Soft`) |
| `SendLocalList` | `list_version`, `update_type` (`Full` or `Differential`), optional `local_authorization_list` |
| `SetChargingProfile` | `connector_id`, `cs_charging_profiles` (object, OCPP camelCase keys inside) |
| `UnlockConnector` | `connector_id` |
| `UpdateFirmware` | `location`, `retrieve_date` (ISO 8601), optional `retry_interval` |
| `ClearChargingProfile` | optional `id`, `connector_id`, `charging_profile_purpose`, `stack_level` |
| `GetCompositeSchedule` | `connector_id`, `duration`, optional `charging_rate_unit` (`W` or `A`) |
| `TriggerMessage` | `requested_message`, optional `connector_id` |
| `GetDiagnostics` | `location`, optional `start_time`, `stop_time`, `retry_interval`, `retries` |
| `GetLocalListVersion` | none |
| `CancelReservation` | `reservation_id` |
| `ReserveNow` | `connector_id`, `expiry_date`, `id_tag`, `reservation_id`, optional `parent_id_tag` |

```bash
curl -X POST "$BROKER/api/ocpp/organizations/orgA/chargers/CP001/commands/ChangeAvailability" \
  -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"connector_id": 1, "type": "Inoperative"}'
```

### Command catalog

```http
GET /api/ocpp/commands/catalog
```

The 19 commands a central system can send under OCPP 1.6, each with the JSON Schema (draft 4) of its payload as the `ocpp` library validates it, a one-line `summary`, a `risk` and the typed `route`. The [web console](web-console.md#sending-a-command) builds its command forms from this.

```json
{
  "ocpp_version": "1.6",
  "commands": [
    {"action": "Reset", "summary": "Restart the charger", "risk": "disruptive",
      "json_schema": {"type": "object", "properties": {"type": {"type": "string", "enum": ["Hard", "Soft"]}}, "required": ["type"]},
      "route": "/api/ocpp/organizations/{org_name}/chargers/{charger_id}/commands/Reset"}
  ]
}
```

`risk` is `read` (only asks), `change` (alters settings or data on the charger) or `disruptive` (can interrupt a charging session, take a connector out of service or restart the charger). The generic route above accepts every command in the catalog.

### Command response

```json
{
  "message_id": "fbaecb17-5a3e-440e-b218-e824793edf0f",
  "organization": "orgA",
  "charger_id": "CP001",
  "action": "Reset",
  "status": "success",
  "response": {"status": "Accepted"},
  "error": null,
  "timestamp": "2026-10-03T06:22:22.142596+00:00"
}
```

| `status` | HTTP | Meaning |
|----------|------|---------|
| `success` | `200` | The charger answered with a CALLRESULT; its payload (camelCase) is in `response` |
| `error` | `200` | The charger answered with a CALLERROR; `error` is `"Code: description"`, for example `"NotSupported: Hard reset not supported"` |
| `timeout` | `504` | No answer in time (same body, `error` is `"No response within 1s"`) |

Other outcomes: `404` charger not connected, `503` the charger disconnected or could not be written to, `422` invalid command.

- **Broker mode:** the request is validated against the OCPP 1.6 schema before anything is sent (`422` for an unknown action, a missing or unknown field, or a bad value). The call goes through the `ocpp` library, so the reply is validated too.
- **Relay mode:** the frame is sent exactly as given, with no validation, and the charger's reply is intercepted by message id; it is **not** forwarded to the backend. Commands issued by the backend itself still reach the charger.
- A `success` or `error` result is also saved to MongoDB (as `call_result` / `call_error`) when MongoDB is enabled.

### Looking up an earlier command

```http
GET /api/ocpp/commands/{message_id}/response
```

Returns the same body as the original call (`status` is `pending` while it waits). The broker keeps the last 1000 outcomes in memory, so an older or unknown id is `404`.

## Tags (`/api/tags`)

Tags authorize `Authorize` and `StartTransaction` in broker mode. See [Tag Management](tag-management.md) for the model and workflows.

| Method and route | Purpose |
|------------------|---------|
| `GET /api/tags/status` | Whether tag management is active |
| `GET /api/tags/organizations` | Organizations that have a tag list |
| `GET /api/tags/organizations/{org}/list` | The organization's full tag list |
| `GET /api/tags/organizations/{org}/tags` | Search (query: `id_tag`, `status`, `tag_type`, `parent_id_tag`, `limit` 1-1000 default 100, `offset`) |
| `POST /api/tags/organizations/{org}/tags` | Add a tag |
| `GET`, `PUT`, `DELETE /api/tags/organizations/{org}/tags/{id_tag}` | Read, replace, delete |
| `POST /api/tags/organizations/{org}/tags/authorize` | Authorize a tag; the body is a bare JSON string |
| `GET /api/tags/organizations/{org}/statistics` | Counts by status and type |
| `POST /api/tags/organizations/{org}/tags/validate` | Check a tag without storing it (query `for_update=true` skips the duplicate check) |
| `POST /api/tags/organizations/{org}/tags/bulk` | `{"operation": "add" \| "update" \| "delete", "tags": [...]}` |
| `POST /api/tags/organizations/{org}/tags/import` | `{"source": "json" \| "csv", "data": "...", "overwrite_existing": false, "validate_only": false}` |
| `POST /api/tags/organizations/{org}/tags/export` | `{"format": "json" \| "csv", "include_metadata": true}`; CSV is returned as a download |
| `POST /api/tags/sync` | Reload from MongoDB (query `org_name` optional); `503` without MongoDB |

Tag fields:

| Field | Notes |
|-------|-------|
| `id_tag` | 1-20 printable ASCII characters (required) |
| `status` | `Accepted`, `Blocked`, `Expired`, `Invalid` or `ConcurrentTx` (required) |
| `tag_type` | `RFID` (default), `NFC`, `QRCode`, `MobileApp`, `UserId` |
| `expiry_date` | ISO 8601 string |
| `parent_id_tag`, `description`, `metadata` | optional |
| `created_at`, `updated_at` | set by the broker |

```bash
curl -X POST "$BROKER/api/tags/organizations/orgA/tags" \
  -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"id_tag": "USER1", "status": "Accepted", "tag_type": "RFID", "metadata": {"dept": "IT"}}'
```

```json
{"success": true, "message": "Tag USER1 added successfully"}
```

Adding an existing tag is `400` `{"detail": "Failed to add tag"}`; reading a missing one is `404` `{"detail": "Tag not found"}`.

**Status**

```json
{"enabled": true, "message": "Tag management is active", "mongodb_persistence": false, "organizations": ["orgA"]}
```

**Full list.** The organization's tag list as stored: `{"list_version": 1, "tags": [...], "created_at": ..., "updated_at": ...}`. For an organization with no list the answer is `{"listVersion": 0, "tags": []}` (note the different key spelling).

**Authorize**

```bash
curl -X POST "$BROKER/api/tags/organizations/orgA/tags/authorize" \
  -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" -d '"ADMIN001"'
```

```json
{"idTag": "ADMIN001", "idTagInfo": {"status": "Accepted", "expiryDate": null, "parentIdTag": null}}
```

An unknown tag answers `Invalid`; a tag past its expiry (or with an unreadable `expiry_date`) answers `Expired`.

**Statistics**

```json
{"total_tags": 2, "active_tags": 1, "expired_tags": 1, "blocked_tags": 0,
 "tags_by_type": {"RFID": 2}, "tags_by_status": {"Accepted": 2}}
```

`active_tags` counts `Accepted` tags that have not passed their expiry; `expired_tags` counts tags with status `Expired` or a past expiry.

**Validate**

```json
{"is_valid": false,
 "errors": ["Tag ADMIN001 already exists in organization orgA",
            "parent_id_tag 'GHOST' does not exist in organization orgA"],
 "warnings": []}
```

Errors: non-printable id, duplicate, unreadable `expiry_date`, missing or self-referencing parent. A past `expiry_date` is only a warning.

**Bulk.** Items succeed or fail independently:

```json
{"operation": "add", "total": 2, "succeeded": 1, "failed": 1,
 "results": [{"id_tag": "B1", "success": true, "error": null},
             {"id_tag": "ADMIN001", "success": false, "error": "already exists"}]}
```

**Import.** JSON may be `{"tags": [...]}` or a bare list; CSV needs the header `id_tag,status,tag_type,expiry_date,parent_id_tag,description` (a `metadata` column holds JSON). Bad records are reported and the rest are imported; existing tags are skipped unless `overwrite_existing` is true; `validate_only` changes nothing. Text that cannot be parsed at all is `400`.

```json
{"source": "json", "validate_only": false, "total": 2, "imported": 1, "updated": 0, "skipped": 0,
 "errors": [{"record": 2, "id_tag": "I2", "error": "status: Input should be 'Accepted', 'Blocked', 'Expired', 'Invalid' or 'ConcurrentTx'"}]}
```

**Export.** CSV (`text/csv`, `Content-Disposition: attachment; filename="orgA-tags.csv"`) or JSON `{"organization", "exported_at", "count", "tags": [...]}`. `include_metadata: false` omits `created_at`, `updated_at` and `metadata`. Both formats can be imported again.

**Sync.** With MongoDB enabled, `POST /api/tags/sync` reloads tags and answers `{"success": true, "message": "...", "organizations": {"orgA": {"loaded": 3, "seeded": 0, "dropped": 0}}}`. See [Tag Management](tag-management.md#syncing-with-mongodb). Without MongoDB: `503` `{"detail": "MongoDB is not connected"}`.

## MongoDB data API (`/api/mongodb`)

These routes let an external system write OCPP records into the broker's MongoDB database. Every route returns `503` `{"detail": "MongoDB service not available"}` when MongoDB is not connected, and `{"status": "success", "message": "..."}` otherwise. See [MongoDB Integration](mongodb-integration.md).

| Route | Required fields (all take `org_name`, `charger_id`, optional `timestamp`) |
|-------|------------------------------------|
| `POST /status-notification` | `connector_id`, `status`; optional `error_code`, `info`, `vendor_id`, `vendor_error_code` |
| `POST /meter-values` | `connector_id`, `meter_value` (list of objects); optional `transaction_id` |
| `POST /boot-notification` | `charge_point_model`, `charge_point_vendor`; optional `firmware_version`, `iccid`, `imsi`, `meter_type`, `meter_serial_number` |
| `POST /transaction` | `transaction_id`, `connector_id`, `id_tag`, `transaction_type` (`start` or `stop`, default `start`); optional `meter_start`, `meter_stop`, `stop_reason`, `reservation_id` |
| `POST /authorization` | `id_tag`, `status`; optional `expiry_date`, `parent_id_tag` |
| `POST /data-transfer` | `vendor_id`; optional `message_id`, `data`, `status` |
| `POST /ocpp-message` | `message_type` (`call`, `call_result`, `call_error`), `action`, `payload`; optional `direction`, `message_id` |
| `GET /health` | see [Health](#health) |

## Backends (`/orgs`)

```http
GET /orgs/{org}/backends
```

```json
[{"charger_id": "CP001", "url": "ws://backend.example.com/ocpp", "leader": true, "connected": true}]
```

One entry per backend link of every charger currently connected in relay mode (the leader and each follower). `url` is the configured base URL; `connected` says whether that link is up right now. An organization that has had no relay-mode charger since the broker started is `404` `{"detail": "Organization not found"}`; once its chargers have all left the list is empty.

There are no endpoints to list organizations, add or remove backends, promote a leader or reload the configuration; change `config.yaml` and restart the broker.

## Related documentation

- [Quick Start](quick-start.md)
- [Configuration Guide](configuration.md)
- [Broker-as-Backend Mode](broker_as_backend.md)
- [Leader/Follower](leader-follower.md)
- [Tag Management](tag-management.md)
- [MongoDB Integration](mongodb-integration.md)
- [Troubleshooting](troubleshooting.md)
