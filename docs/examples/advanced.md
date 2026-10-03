# Advanced Examples

Relay with followers and failover, charger authentication, tag import/export, DataTransfer policy, remote commands and MongoDB. These build on [basic.md](basic.md): the scripts `simple_charger.py` and `charger_responder.py` come from there, and all REST calls need the API key:

```bash
export OCPP_BROKER_API_KEY=change-me
BASE=http://localhost:8765
```

## 1. Relay to a backend with a follower

In relay mode (`connect_to_backend: true`, the default) the broker opens one WebSocket per charger to each configured backend at `{url}/{charger_id}` and forwards frames untouched, with no validation.

- The **leader** (`leader: true`) gets every frame from the charger, and only its frames reach the charger.
- **Followers** get a copy of every CALL the charger sends. Their replies are discarded and nothing is buffered for them.

```yaml
# relay.yaml
broker:
  port: 8765

organizations:
  - name: acme
    connect_to_backend: true          # relay mode (this is also the default)
    ocpp_subprotocol: ocpp1.6
    backend_buffer_size: 200          # frames held while the leader is unreachable
    backend_outage_timeout: 30        # seconds before a held CALL is answered with a CallError
    leader_failover_timeout: 15       # seconds the leader may be down before a follower takes over (0 = never)
    backends:
      - id: primary
        url: ws://localhost:9001/ocpp   # the charger connects to ws://localhost:9001/ocpp/<charger_id>
        leader: true
      - id: shadow
        url: ws://localhost:9002/ocpp
```

To try it locally, run two stand-in backends. This one prints every frame and answers CALLs with minimal valid results:

```python
# mock_backend.py
import asyncio
import json
import sys
from datetime import datetime, timezone

import websockets

# usage: python mock_backend.py PORT [NAME]
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9001
NAME = sys.argv[2] if len(sys.argv) > 2 else f"backend:{PORT}"

RESULTS = {
    "BootNotification": lambda: {"status": "Accepted", "interval": 60,
                                 "currentTime": datetime.now(timezone.utc).isoformat()},
    "Heartbeat": lambda: {"currentTime": datetime.now(timezone.utc).isoformat()},
    "Authorize": lambda: {"idTagInfo": {"status": "Accepted"}},
    "StartTransaction": lambda: {"transactionId": 1, "idTagInfo": {"status": "Accepted"}},
}


async def handler(ws):
    print(f"[{NAME}] charger connected on {ws.request.path}", flush=True)
    try:
        async for raw in ws:
            frame = json.loads(raw)
            print(f"[{NAME}] received {frame}", flush=True)
            if frame[0] == 2:  # CALL: answer it
                _, msg_id, action, _payload = frame
                result = RESULTS.get(action, lambda: {})()
                await ws.send(json.dumps([3, msg_id, result]))
    except websockets.ConnectionClosed:
        pass
    print(f"[{NAME}] charger disconnected", flush=True)


async def main():
    async with websockets.serve(handler, "localhost", PORT, subprotocols=["ocpp1.6"]):
        print(f"[{NAME}] listening on ws://localhost:{PORT}", flush=True)
        await asyncio.Future()


asyncio.run(main())
```

```bash
python mock_backend.py 9001 primary &
python mock_backend.py 9002 shadow &
python -m ocpp_broker.server -c relay.yaml &
python simple_charger.py ws://localhost:8765/acme/CP100
```

The charger gets its replies from `primary`. In the output `primary` sees every frame (`charger connected on /ocpp/CP100`, then BootNotification, Heartbeat, ...), while `shadow` sees the same CALLs but never gets a reply back to the charger. Frames sent before a follower's link is up are not replayed to it.

With the responder from basic.md connected (`python charger_responder.py ws://localhost:8765/acme/CP100`), inspect the links:

```bash
curl -H "X-API-Key: $OCPP_BROKER_API_KEY" $BASE/orgs/acme/backends
```

```json
[{"charger_id":"CP100","url":"ws://localhost:9001/ocpp","leader":true,"connected":true},
 {"charger_id":"CP100","url":"ws://localhost:9002/ocpp","leader":false,"connected":true}]
```

The list only covers chargers currently connected (404 for an unknown org). REST commands work in relay mode too. The broker sends the CALL exactly as given and intercepts the charger's reply, so the backend never sees it. There is no payload validation in relay mode, so any action name is passed on:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"requested_message":"Heartbeat"}' \
  $BASE/api/ocpp/organizations/acme/chargers/CP100/commands/TriggerMessage
# {"message_id":"...","organization":"acme","charger_id":"CP100","action":"TriggerMessage","status":"success","response":{"status":"Accepted"},"error":null,"timestamp":"..."}
```

The Heartbeat the charger then sends goes to `primary` (and a copy to `shadow`) like any other CALL.

## 2. Failover and store-and-forward

While the leader is unreachable the broker keeps the charger connected:

- Frames from the charger wait in an outbox of up to `backend_buffer_size` (default 200) and are flushed in order when the leader reconnects. The broker retries with a delay that starts at 1 s and doubles up to 30 s.
- A CALL that waits longer than `backend_outage_timeout` (default 30 s), or does not fit in a full outbox, is answered to the charger with `[4, "<id>", "InternalError", "Backend unavailable, please retry", {}]`. An undeliverable CALLRESULT is dropped with a warning.
- If the leader stays down for `leader_failover_timeout` (default 15 s, `0` disables) and a follower is connected, the first healthy follower in config order becomes the leader. The old leader becomes a follower when it returns; there is no automatic fail-back. Frames held for the old leader are answered with a CALLERROR, not replayed.

With the setup from section 1, stop the `primary` mock and watch the links:

```bash
curl -H "X-API-Key: $OCPP_BROKER_API_KEY" $BASE/orgs/acme/backends
# right after the outage: primary is still leader, "connected": false
# [{"charger_id":"CP100","url":"ws://localhost:9001/ocpp","leader":true,"connected":false},
#  {"charger_id":"CP100","url":"ws://localhost:9002/ocpp","leader":false,"connected":true}]

# after leader_failover_timeout (15 s): shadow is the leader
# [{"charger_id":"CP100","url":"ws://localhost:9002/ocpp","leader":true,"connected":true},
#  {"charger_id":"CP100","url":"ws://localhost:9001/ocpp","leader":false,"connected":false}]
```

The broker logs `FAILOVER: leader ws://localhost:9001/ocpp unreachable, promoting follower ws://localhost:9002/ocpp`. CALLs that were waiting for the old leader are answered with a CALLERROR at that moment (`Backend unavailable, please retry`); the charger's retries, and everything it sends afterwards, go to the new leader.

## 3. Charger authentication

Chargers can authenticate with HTTP Basic on the WebSocket upgrade (OCPP security profile 1). The username must equal the charger id. Authentication is enforced for an org as soon as `charger_auth.credentials` is non-empty (or `charger_auth.required: true`); then only the listed chargers can connect. The broker has no TLS of its own, so terminate TLS in a reverse proxy.

Generate a hash (omit the argument to be prompted instead of leaving the password in your shell history):

```bash
python -m ocpp_broker.auth s3cret      # also installed as: ocpp-broker-hash-password s3cret
# pbkdf2_sha256$200000$<salt>$<hash>
```

```yaml
# secure.yaml
broker:
  port: 8765

organizations:
  - name: secure
    connect_to_backend: false
    charger_auth:
      credentials:
        CP001:
          # hash of "s3cret", generated with: python -m ocpp_broker.auth s3cret
          password_hash: "pbkdf2_sha256$200000$M8NACXBqeBOvT0sKN/pjVQ==$5pZVMVcafrMH6cW8BF10UjTm6Om3mv9IkwpIJV4dwhU="
        CP002: "dev-only-plaintext"      # a bare string is a plaintext password (logs a warning)
    tags:
      - id_tag: TAG001
        status: Accepted

ocpp:
  commands:
    core:
      heartbeat_interval: 60             # interval returned in BootNotification replies

data_transfer:
  known_vendors: [VendorA, VendorB]
  known_message_ids: [GetPrice, Ping, Report]
  vendors:
    VendorA:
      allowed_message_ids: [GetPrice, Ping]
    VendorB:
      require_message_id: true
      allowed_message_ids: [Report]
      auto_accept: false                 # recognised, but answered with Rejected
```

(The `data_transfer` block is used in section 5.) Connect with the responder from basic.md, which takes the password as its second argument:

```bash
python charger_responder.py ws://localhost:8765/secure/CP001 s3cret
```

A client that fails authentication is refused before the WebSocket opens:

```python
# auth_check.py
import asyncio
import base64

import websockets


def basic(user_and_password: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(user_and_password.encode()).decode()}


async def try_connect(label, url, headers):
    try:
        async with websockets.connect(url, subprotocols=["ocpp1.6"], additional_headers=headers):
            print(label, "-> connected")
    except websockets.InvalidStatus as exc:
        print(label, "-> HTTP", exc.response.status_code)


async def main():
    url = "ws://localhost:8765/secure/CP001"
    await try_connect("no credentials", url, {})
    await try_connect("wrong password", url, basic("CP001:nope"))
    await try_connect("wrong username", url, basic("CP999:s3cret"))
    await try_connect("correct", url, basic("CP001:s3cret"))
    await try_connect("charger not listed", "ws://localhost:8765/secure/CP003", basic("CP003:x"))


asyncio.run(main())
```

Expected: `HTTP 401` for the first, second, third and last attempts (with a `WWW-Authenticate: Basic` header), `connected` for the correct one.

## 4. Tags: import, export, bulk and MongoDB sync

These calls use the `demo` org from basic.md. Every operation reports per-record results, so one bad record never aborts the rest.

Import CSV (columns `id_tag,status,tag_type,expiry_date,parent_id_tag,description`, plus `created_at,updated_at,metadata` where `metadata` is a JSON string). The third record is too long, so it is reported and skipped:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"source":"csv","data":"id_tag,status,tag_type,description\nFLEET001,Accepted,RFID,Van 1\nFLEET002,Accepted,RFID,Van 2\nTOOLONG_TAG_ID_OVER_20_CHARS,Accepted,RFID,bad\n"}' \
  $BASE/api/tags/organizations/demo/tags/import
```

```json
{"source":"csv","validate_only":false,"total":3,"imported":2,"updated":0,"skipped":0,
 "errors":[{"record":3,"id_tag":"TOOLONG_TAG_ID_OVER_20_CHARS","error":"id_tag: String should have at most 20 characters"}]}
```

Existing tags are skipped unless `overwrite_existing` is true; `validate_only` reports what would happen without changing anything (JSON source shown here, `data` is a string containing JSON):

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"source":"json","data":"[{\"id_tag\":\"FLEET001\",\"status\":\"Blocked\"}]","overwrite_existing":true,"validate_only":true}' \
  $BASE/api/tags/organizations/demo/tags/import
# {"source":"json","validate_only":true,"total":1,"imported":0,"updated":1,"skipped":0,"errors":[]}
```

Bulk add, update or delete (`operation` is `add`, `update` or `delete`):

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"operation":"delete","tags":[{"id_tag":"FLEET002","status":"Accepted"},{"id_tag":"GHOST","status":"Accepted"}]}' \
  $BASE/api/tags/organizations/demo/tags/bulk
```

```json
{"operation":"delete","total":2,"succeeded":1,"failed":1,
 "results":[{"id_tag":"FLEET002","success":true,"error":null},{"id_tag":"GHOST","success":false,"error":"not found"}]}
```

Check a tag against the org's rules without storing it:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"id_tag":"X1","status":"Accepted","parent_id_tag":"MISSING"}' \
  $BASE/api/tags/organizations/demo/tags/validate
# {"is_valid":false,"errors":["parent_id_tag 'MISSING' does not exist in organization demo"],"warnings":[]}
```

Export as JSON (`{"organization", "exported_at", "count", "tags": [...]}`) or as a CSV download that can be imported again:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"format":"csv","include_metadata":false}' \
  -OJ $BASE/api/tags/organizations/demo/tags/export     # saves demo-tags.csv
```

```text
id_tag,status,tag_type,expiry_date,parent_id_tag,description
TAG001,Accepted,RFID,,,Test RFID card
STOLEN01,Blocked,RFID,,,
FLEET001,Accepted,RFID,,,Van 1
```

With MongoDB enabled (section 7) tags are persisted. After editing tags directly in the database, make the broker reload them:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" "$BASE/api/tags/sync?org_name=demo"
```

MongoDB is authoritative for an org that has stored tags (the in-memory set is replaced); an org with none stored has its in-memory tags (for example from `config.yaml`) pushed to MongoDB. The reply has the form `{"success": true, "message": "...", "organizations": {"demo": {"loaded": 0, "seeded": 3, "dropped": 0}}}`. Without MongoDB the endpoint returns 503 `{"detail":"MongoDB is not connected"}`.

## 5. DataTransfer policy

Incoming `DataTransfer` CALLs from chargers are checked against the `data_transfer` block of `secure.yaml` above (it is system-wide, not per organization):

- `known_vendors` / `known_message_ids` with `validate_vendors` / `validate_message_ids` (both default true) answer `UnknownVendorId` / `UnknownMessageId`.
- `vendors.<id>.allowed_message_ids`, `require_message_id` and `auto_accept` refine a vendor. `auto_accept: false` answers `Rejected`.
- `vendor_messages."<vendor>:<message>".auto_accept` does the same per vendor and message.
- `data_transfer.enabled: false` answers `NotImplemented` to everything.

```python
# data_transfer_demo.py
import asyncio
import base64
import json
import uuid

import websockets

URL = "ws://localhost:8765/secure/CP001"
AUTH = "Basic " + base64.b64encode(b"CP001:s3cret").decode()  # username = charger id


async def main():
    async with websockets.connect(
        URL, subprotocols=["ocpp1.6"], additional_headers={"Authorization": AUTH}
    ) as ws:
        for payload in [
            {"vendorId": "VendorA", "messageId": "GetPrice", "data": '{"connector": 1}'},
            {"vendorId": "VendorA", "messageId": "Reboot"},    # not in known_message_ids
            {"vendorId": "VendorB", "messageId": "Report"},    # vendor has auto_accept: false
            {"vendorId": "VendorB"},                           # require_message_id: true
            {"vendorId": "Mystery", "messageId": "Ping"},      # not in known_vendors
        ]:
            await ws.send(json.dumps([2, str(uuid.uuid4()), "DataTransfer", payload]))
            _, _, result = json.loads(await ws.recv())
            print(f"{payload['vendorId']}/{payload.get('messageId')}: {result}")


asyncio.run(main())
```

```text
VendorA/GetPrice: {'status': 'Accepted', 'data': '{"connector": 1}'}
VendorA/Reboot: {'status': 'UnknownMessageId'}
VendorB/Report: {'status': 'Rejected'}
VendorB/None: {'status': 'UnknownMessageId'}
Mystery/Ping: {'status': 'UnknownVendorId'}
```

An accepted message echoes `data` back (JSON is re-serialised). The other direction, a DataTransfer from the broker to a charger, is a REST command:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"vendor_id":"VendorX","message_id":"Ping","data":"ping"}' \
  $BASE/api/ocpp/organizations/secure/chargers/CP001/commands/DataTransfer
# ... "status":"success","response":{"status":"Accepted","data":"pong"} ...
```

## 6. Remote commands with nested payloads

Typed routes take snake_case bodies; nested objects are passed to the charger as given, so write those in OCPP's camelCase. In broker mode the CALL is validated against the OCPP 1.6 schema before it is sent (422 if invalid, nothing is sent); in relay mode it is forwarded as is. With `charger_responder.py` connected to `demo/CP001`:

```bash
# SetChargingProfile: limit connector 1 to 16 A by default
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"connector_id":1,"cs_charging_profiles":{"chargingProfileId":1,"stackLevel":0,"chargingProfilePurpose":"TxDefaultProfile","chargingProfileKind":"Absolute","chargingSchedule":{"chargingRateUnit":"A","chargingSchedulePeriod":[{"startPeriod":0,"limit":16}]}}}' \
  $BASE/api/ocpp/organizations/demo/chargers/CP001/commands/SetChargingProfile

# ReserveNow
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"connector_id":1,"expiry_date":"2030-01-01T12:00:00Z","id_tag":"TAG001","reservation_id":7}' \
  $BASE/api/ocpp/organizations/demo/chargers/CP001/commands/ReserveNow

# RemoteStopTransaction
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"transaction_id":42}' \
  $BASE/api/ocpp/organizations/demo/chargers/CP001/commands/RemoteStopTransaction
# each: {"message_id":"...","status":"success","response":{"status":"Accepted"},"error":null, ...}

# Invalid payload: rejected without contacting the charger
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"type":"Bogus"}' -w "\n%{http_code}\n" \
  $BASE/api/ocpp/organizations/demo/chargers/CP001/commands/Reset
# {"detail":"Invalid payload for Reset: FormatViolationError: ... 'Bogus' is not one of ['Hard', 'Soft'] ..."}
# 422
```

Charger replies are not interpreted: a charger that answers `{"status":"Rejected"}` still gives `"status":"success"` at the REST level, because the command was delivered and answered. Check `response` for the OCPP-level result.

## 7. MongoDB

MongoDB is optional. When connected, the broker persists the traffic it handles as the central system, makes transaction ids durable and stores tags. Settings can also come from the environment, which wins over the file: `MONGODB_ENABLED`, `MONGODB_CONNECTION_STRING`, `MONGODB_DATABASE_NAME` (a `.env` file is loaded too).

```yaml
mongodb:
  enabled: true
  connection_string: "mongodb://localhost:27017"
  database_name: ocpp_broker

organizations:
  - name: demo
    connect_to_backend: false
```

```bash
curl -H "X-API-Key: $OCPP_BROKER_API_KEY" $BASE/api/mongodb/health
# {"status":"connected","connected":true,"database":"ocpp_broker"}
# without MongoDB: {"status":"not_configured","connected":false}
```

Collections written: `charger_statuses` and `charger_statuses_latest` (StatusNotification), `meter_values`, `charger_configurations` (BootNotification), `charger_heartbeats_latest` (latest heartbeat only), `transactions` (start inserts, stop updates the same document), `authorizations`, `data_transfers`, `tags`, `tag_list_versions`, `counters` (one transaction id counter per org) and a raw-message collection per action for REST commands and some charger messages. Transaction ids start at 1 per organization.

The `/api/mongodb/*` routes (`status-notification`, `meter-values`, `boot-notification`, `transaction`, `authorization`, `data-transfer`, `ocpp-message`) let an external system write the same records. They return 503 when MongoDB is not connected.

## 8. REST API settings and several organizations

The API key comes from `OCPP_BROKER_API_KEY` or `security.api_key` (the environment wins). `security.allow_unauthenticated_api: true` opens the API for development. Browser dashboards need their origin listed for CORS (`"*"` works only without credentials). The charger socket keepalive is also configured here.

```yaml
security:
  # api_key: "..."                    # or set OCPP_BROKER_API_KEY (the environment wins)
  cors:
    allow_origins: ["https://dashboard.example.com"]
    allow_credentials: false
  websocket:
    ping_interval: 20                 # server pings every charger this often (seconds)
    ping_timeout: 20                  # ...and drops one that does not answer within this
organizations:
  - name: demo
    connect_to_backend: false
```

A relay org can have per-backend subprotocols, and relay and broker orgs live side by side. The same charger id may connect to two orgs; the sessions are independent. A charger that reconnects while its old session is still open replaces it (the old socket is closed with code 4003).

```yaml
organizations:
  - name: acme
    ocpp_subprotocol: ocpp1.6
    backends:
      - id: primary
        url: ws://central-a.example.com/ocpp
        leader: true
        ocpp_subprotocol: ocpp1.6       # per-backend override of the org setting
      - id: audit
        url: ws://central-b.example.com/ocpp
  - name: demo
    connect_to_backend: false
```

## Related documentation

- [Basic examples](basic.md)
- [Configuration](../configuration.md)
- [Leader/follower relay](../leader-follower.md)
- [Broker-as-backend mode](../broker_as_backend.md)
- [API reference](../api-reference.md)
- [Monitoring](../monitoring.md)
