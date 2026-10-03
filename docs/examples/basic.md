# Basic Examples

Small, runnable examples. Everything is served on one port (8765 by default): the charger WebSocket, the REST API, `/health` and the Swagger UI at `/docs`.

Prerequisites: `pip install -e .` from the repository root (this also installs `websockets`, which the charger scripts use; version 14 or newer).

## 1. Start the broker

```yaml
# config.yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: demo
    connect_to_backend: false   # the broker is the central system (default is true: relay mode)
    tags:
      - id_tag: TAG001
        status: Accepted
        description: Test RFID card
      - id_tag: STOLEN01
        status: Blocked
```

```bash
export OCPP_BROKER_API_KEY=change-me      # the REST API refuses all requests without a key
python -m ocpp_broker.server -c config.yaml     # same as: ocpp-broker-server -c config.yaml
```

On Windows PowerShell use `$env:OCPP_BROKER_API_KEY = "change-me"`.

Expect two warnings at startup: MongoDB is not configured (nothing is persisted) and the `demo` org accepts unauthenticated chargers (see [charger authentication](advanced.md#3-charger-authentication)).

```bash
curl http://localhost:8765/health
# {"status":"ok"}
```

A charger connects to `ws://HOST:8765/{org_name}/{charger_id}` and must offer the `ocpp1.6` WebSocket subprotocol (the org's `ocpp_subprotocol`). A missing or different subprotocol is refused during the handshake (the client sees HTTP 403); an unknown organization is accepted and then closed with code 4002.

## 2. Simulate a charger

A charger that sends the usual message flow to the broker:

```python
# simple_charger.py
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone

import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8765/demo/CP001"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def call(ws, action, payload):
    """Send one CALL, wait for the reply frame and return its payload."""
    await ws.send(json.dumps([2, str(uuid.uuid4()), action, payload]))
    frame = json.loads(await ws.recv())
    print(f"{action:<20} -> {frame}")
    return frame[2] if frame[0] == 3 else None  # CALLRESULT payload (None on CALLERROR)


async def main():
    async with websockets.connect(URL, subprotocols=["ocpp1.6"]) as ws:
        print("connected, subprotocol:", ws.subprotocol)

        await call(ws, "BootNotification", {"chargePointVendor": "Demo", "chargePointModel": "Sim-1"})
        await call(ws, "Heartbeat", {})
        await call(ws, "StatusNotification",
                   {"connectorId": 1, "errorCode": "NoError", "status": "Available", "timestamp": now()})

        await call(ws, "Authorize", {"idTag": "TAG001"})    # known, Accepted
        await call(ws, "Authorize", {"idTag": "STOLEN01"})  # known, Blocked
        await call(ws, "Authorize", {"idTag": "NOBODY"})    # not in the tag list -> Invalid

        started = await call(ws, "StartTransaction",
                             {"connectorId": 1, "idTag": "TAG001", "meterStart": 0, "timestamp": now()})
        tx_id = started["transactionId"]

        await call(ws, "MeterValues", {
            "connectorId": 1,
            "transactionId": tx_id,
            "meterValue": [{
                "timestamp": now(),
                "sampledValue": [{"value": "1500", "measurand": "Energy.Active.Import.Register", "unit": "Wh"}],
            }],
        })

        await call(ws, "StopTransaction", {
            "transactionId": tx_id, "meterStop": 4200, "timestamp": now(), "idTag": "TAG001", "reason": "Local",
        })

        # Actions the broker does not implement as a central system get a CALLERROR.
        await call(ws, "Reset", {"type": "Soft"})


asyncio.run(main())
```

Output (message ids, timestamps and the transaction id differ on every run):

```text
connected, subprotocol: ocpp1.6
BootNotification     -> [3, '<id>', {'currentTime': '2026-10-03T06:22:39.826538+00:00', 'interval': 300, 'status': 'Accepted'}]
Heartbeat            -> [3, '<id>', {'currentTime': '2026-10-03T06:22:39.830897+00:00'}]
StatusNotification   -> [3, '<id>', {}]
Authorize            -> [3, '<id>', {'idTagInfo': {'status': 'Accepted'}}]
Authorize            -> [3, '<id>', {'idTagInfo': {'status': 'Blocked'}}]
Authorize            -> [3, '<id>', {'idTagInfo': {'status': 'Invalid'}}]
StartTransaction     -> [3, '<id>', {'transactionId': 1791008560, 'idTagInfo': {'status': 'Accepted'}}]
MeterValues          -> [3, '<id>', {}]
StopTransaction      -> [3, '<id>', {'idTagInfo': {'status': 'Accepted'}}]
Reset                -> [4, '<id>', 'NotImplemented', 'Request Action is recognized but not supported by the receiver', {'cause': 'No handler for Reset registered.'}]
```

What to notice:

- In broker mode the central system handles only `BootNotification`, `Authorize`, `Heartbeat`, `StatusNotification`, `MeterValues`, `StartTransaction`, `StopTransaction`, `DataTransfer`, `DiagnosticsStatusNotification` and `FirmwareStatusNotification`. Anything else gets `NotImplemented`.
- The `interval` in the BootNotification reply is `ocpp.commands.core.heartbeat_interval` (default 300).
- Tags decide the `idTagInfo.status`. An unknown tag is `Invalid`, but the broker still allocates a `transactionId` for a StartTransaction; the charger is expected to honour the status.
- Without MongoDB, transaction ids come from an in-memory counter seeded from the clock; they are not durable across restarts. Enable MongoDB (see [advanced](advanced.md#7-mongodb)) for a durable per-organization counter.
- Payloads are validated by the `ocpp` library: a malformed CALL is answered with a CALLERROR.

## 3. A charger that stays connected and answers commands

To try the REST command API a charger must be connected and must reply to what the broker sends. This script boots, then answers commands until you stop it. It takes an optional password for [charger authentication](advanced.md#3-charger-authentication).

```python
# charger_responder.py
import asyncio
import base64
import json
import sys
import uuid

import websockets

# usage: python charger_responder.py ws://localhost:8765/demo/CP001 [password]
URL = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8765/demo/CP001"
PASSWORD = sys.argv[2] if len(sys.argv) > 2 else None
CHARGER_ID = URL.rstrip("/").rsplit("/", 1)[-1]

# CALLRESULT payloads for the commands the broker's REST API can send.
REPLIES = {
    "Reset": {"status": "Accepted"},
    "ChangeAvailability": {"status": "Accepted"},
    "ChangeConfiguration": {"status": "Accepted"},
    "ClearCache": {"status": "Accepted"},
    "GetConfiguration": {
        "configurationKey": [{"key": "HeartbeatInterval", "readonly": False, "value": "300"}],
        "unknownKey": [],
    },
    "RemoteStartTransaction": {"status": "Accepted"},
    "RemoteStopTransaction": {"status": "Accepted"},
    "UnlockConnector": {"status": "Unlocked"},
    "TriggerMessage": {"status": "Accepted"},
    "SetChargingProfile": {"status": "Accepted"},
    "ReserveNow": {"status": "Accepted"},
    "DataTransfer": {"status": "Accepted", "data": "pong"},
}


async def main():
    headers = {}
    if PASSWORD:  # OCPP security profile 1: HTTP Basic, username = charger id
        token = base64.b64encode(f"{CHARGER_ID}:{PASSWORD}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    async with websockets.connect(URL, subprotocols=["ocpp1.6"], additional_headers=headers) as ws:
        print("connected as", CHARGER_ID)
        boot = [2, str(uuid.uuid4()), "BootNotification",
                {"chargePointVendor": "Demo", "chargePointModel": "Sim-1"}]
        await ws.send(json.dumps(boot))

        async for raw in ws:  # stay connected and answer whatever the broker sends
            frame = json.loads(raw)
            if frame[0] == 3:  # CALLRESULT to one of our own calls
                print("reply to our call:", frame[2])
                continue
            _, msg_id, action, payload = frame
            print("command from broker:", action, payload)
            if action in REPLIES:
                await ws.send(json.dumps([3, msg_id, REPLIES[action]]))
            else:
                await ws.send(json.dumps([4, msg_id, "NotSupported", f"{action} not supported", {}]))

            if action == "TriggerMessage" and payload.get("requestedMessage") == "Heartbeat":
                # TriggerMessage obliges the charger to send the message it was asked for
                await ws.send(json.dumps([2, str(uuid.uuid4()), "Heartbeat", {}]))


asyncio.run(main())
```

Run it in a second terminal: `python charger_responder.py`. It prints each command it receives (`python -u` if output is buffered).

## 4. REST API

Every `/api/...` and `/orgs/...` call needs the API key, as `X-API-Key: <key>` or `Authorization: Bearer <key>`. `/health`, `/docs`, `/redoc` and `/openapi.json` do not. Without any key configured the API answers 503; a wrong or missing key gives 401.

```bash
export OCPP_BROKER_API_KEY=change-me
BASE=http://localhost:8765
```

### Connected chargers

```bash
curl -H "X-API-Key: $OCPP_BROKER_API_KEY" $BASE/api/ocpp/organizations/demo/chargers
# {"organization":"demo","chargers":[{"charger_id":"CP001","organization":"demo","mode":"broker","connected":true}]}

curl -H "X-API-Key: $OCPP_BROKER_API_KEY" $BASE/api/ocpp/organizations/demo/chargers/CP001/status
# {"charger_id":"CP001","organization":"demo","mode":"broker","connected":true,"has_backend":false}
```

An org that does not exist, or has nothing connected, returns an empty `chargers` list; a charger that is not connected returns 404 `{"detail":"Charger demo/NOPE not connected"}`.

### Send a command to a charger

The call waits (default 30 s) for the charger's reply. The generic route takes the OCPP action and a camelCase OCPP payload:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"action":"GetConfiguration","payload":{"key":["HeartbeatInterval"]},"timeout":10}' \
  $BASE/api/ocpp/organizations/demo/chargers/CP001/commands
```

```json
{
  "message_id": "49282a31-54e6-492d-a415-94616d333b5a",
  "organization": "demo",
  "charger_id": "CP001",
  "action": "GetConfiguration",
  "status": "success",
  "response": {"configurationKey": [{"key": "HeartbeatInterval", "readonly": false, "value": "300"}], "unknownKey": []},
  "error": null,
  "timestamp": "2026-10-03T06:23:53.618691+00:00"
}
```

`timeout` is 1 to 300 seconds. Typed routes (`.../commands/{Action}`) take snake_case bodies and always use the 30 s default:

```bash
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"id_tag":"TAG001","connector_id":1}' \
  $BASE/api/ocpp/organizations/demo/chargers/CP001/commands/RemoteStartTransaction
# {"message_id":"...","organization":"demo","charger_id":"CP001","action":"RemoteStartTransaction","status":"success","response":{"status":"Accepted"},"error":null,"timestamp":"..."}

curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"requested_message":"Heartbeat"}' \
  $BASE/api/ocpp/organizations/demo/chargers/CP001/commands/TriggerMessage

curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"type":"Inoperative","connector_id":1}' \
  $BASE/api/ocpp/organizations/demo/chargers/CP001/commands/ChangeAvailability
```

Typed routes exist for CancelReservation, ChangeAvailability, ChangeConfiguration, ClearCache, ClearChargingProfile, DataTransfer, GetCompositeSchedule, GetConfiguration, GetDiagnostics, GetLocalListVersion, RemoteStartTransaction, RemoteStopTransaction, ReserveNow, Reset, SendLocalList, SetChargingProfile, TriggerMessage, UnlockConnector and UpdateFirmware. See `/docs` for each body.

The outcome is in `status`:

| `status` | HTTP | Meaning |
| --- | --- | --- |
| `success` | 200 | the charger replied; its CallResult payload is in `response` |
| `error` | 200 | the charger answered with a CALLERROR; `error` is `"Code: description"` |
| `timeout` | 504 | no reply within `timeout`; same body shape, `error` is `"No response within 3s"` |

Other failures use FastAPI's default `{"detail": ...}` body: 404 charger not connected, 422 the action or payload is not a valid OCPP 1.6 CALL (nothing is sent), 503 the charger disconnected mid-call. The last 1000 command outcomes are kept in memory:

```bash
curl -H "X-API-Key: $OCPP_BROKER_API_KEY" $BASE/api/ocpp/commands/<message_id>/response
```

Example of an error reply, when the charger does not support `GetLocalListVersion`:

```json
{"message_id": "6c9d6d79-e59c-4ac1-96f2-bda5a4af6849", "organization": "demo", "charger_id": "CP001",
 "action": "GetLocalListVersion", "status": "error", "response": null,
 "error": "NotSupported: GetLocalListVersion not supported", "timestamp": "2026-10-03T06:24:07.233808+00:00"}
```

### Tags

Tags are held in memory (seeded from `organizations[].tags`); without MongoDB they are lost on restart.

```bash
# list / filter (query: id_tag, status, tag_type, parent_id_tag, limit, offset)
curl -H "X-API-Key: $OCPP_BROKER_API_KEY" "$BASE/api/tags/organizations/demo/tags?status=Blocked"
# {"tags":[{"id_tag":"STOLEN01","status":"Blocked","tag_type":"RFID", ...}],"total":1,"limit":100,"offset":0}

# add (id_tag is 1-20 printable ASCII characters)
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"id_tag":"CARD42","status":"Accepted","expiry_date":"2030-01-01T00:00:00Z","description":"Visitor card"}' \
  $BASE/api/tags/organizations/demo/tags
# {"success":true,"message":"Tag CARD42 added successfully"}

# what an OCPP Authorize would answer (the body is a bare JSON string)
curl -X POST -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '"CARD42"' $BASE/api/tags/organizations/demo/tags/authorize
# {"idTag":"CARD42","idTagInfo":{"status":"Accepted","expiryDate":"2030-01-01T00:00:00Z","parentIdTag":null}}

curl -H "X-API-Key: $OCPP_BROKER_API_KEY" $BASE/api/tags/organizations/demo/statistics
# {"total_tags":3,"active_tags":2,"expired_tags":0,"blocked_tags":1,"tags_by_type":{"RFID":3},"tags_by_status":{"Accepted":2,"Blocked":1}}

curl -X DELETE -H "X-API-Key: $OCPP_BROKER_API_KEY" $BASE/api/tags/organizations/demo/tags/CARD42
# {"success":true,"message":"Tag CARD42 deleted successfully"}
```

Adding an existing tag returns 400 `{"detail":"Failed to add tag"}`; use `PUT .../tags/{id_tag}` to change one. Bulk, import and export are in [advanced](advanced.md#4-tags-import-export-bulk-and-mongodb-sync).

## 5. Configuration variants

Relay mode forwards every charger frame to your own central system. The `url` is a base: the broker connects to `{url}/{charger_id}`, and a relay org needs at least one backend (give it an `id` and mark it `leader: true`).

```yaml
organizations:
  - name: acme
    connect_to_backend: true            # relay mode; this is the default
    backends:
      - id: central
        url: ws://central.example.com/ocpp   # charger CP100 connects to ws://central.example.com/ocpp/CP100
        leader: true
```

Relay and broker-mode organizations can share one broker. The charger picks its organization through the URL path (`ws://host:8765/acme/CP100`, `ws://host:8765/demo/CP001`):

```yaml
broker:
  port: 8765

organizations:
  # Relay: forwards every frame to your own central system
  - name: acme
    backends:
      - id: central
        url: ws://central.example.com/ocpp
        leader: true

  # Broker mode: this broker is the central system
  - name: demo
    connect_to_backend: false
    tags:
      - id_tag: TAG001
        status: Accepted
```

Failover, store-and-forward and followers are covered in [advanced](advanced.md#1-relay-to-a-backend-with-a-follower).

## 6. Load test

Twenty concurrent chargers in the `demo` org, each booting and sending five heartbeats:

```python
# many_chargers.py
import asyncio
import json
import time
import uuid

import websockets

BASE = "ws://localhost:8765/demo"
COUNT = 20


async def charger(n: int) -> int:
    """Boot, then send 5 heartbeats; returns the number of heartbeats answered."""
    answered = 0
    async with websockets.connect(f"{BASE}/LOAD{n:03d}", subprotocols=["ocpp1.6"]) as ws:
        async def call(action, payload):
            await ws.send(json.dumps([2, str(uuid.uuid4()), action, payload]))
            return json.loads(await ws.recv())

        await call("BootNotification", {"chargePointVendor": "Demo", "chargePointModel": "Load"})
        for _ in range(5):
            frame = await call("Heartbeat", {})
            answered += frame[0] == 3
            await asyncio.sleep(1)
    return answered


async def main():
    started = time.perf_counter()
    results = await asyncio.gather(*(charger(n) for n in range(COUNT)), return_exceptions=True)
    ok = [r for r in results if isinstance(r, int)]
    print(f"{len(ok)}/{COUNT} chargers finished, {sum(ok)} heartbeats answered "
          f"in {time.perf_counter() - started:.1f}s")
    for r in results:
        if isinstance(r, Exception):
            print("failed:", repr(r))


asyncio.run(main())
```

Output on a local run: `20/20 chargers finished, 100 heartbeats answered in 7.3s`.

## Related documentation

- [Advanced examples](advanced.md)
- [Quick start](../quick-start.md)
- [Configuration](../configuration.md)
- [Broker-as-backend mode](../broker_as_backend.md)
- [Leader/follower relay](../leader-follower.md)
- [API reference](../api-reference.md)
