# Quick Start Guide

Get a broker running, connect a simulated charger, and send it a command. About five minutes.

## 1. Install

```bash
pip install ocpp-broker
```

or, from a checkout of the repository:

```bash
pip install -e .
```

This provides the `ocpp-broker-server` command. Python 3.10 or newer is required.

## 2. Create a configuration

Save as `config.yaml` in the directory you will start the broker from:

```yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "MyOrg"
    connect_to_backend: false      # the broker itself acts as the central system
    tags:
      - id_tag: "ADMIN001"
        status: "Accepted"
        description: "Administrator card"
```

`connect_to_backend: false` is what makes the broker answer chargers itself. Leave it out and the organization becomes a relay organization (the default is `true`) that needs `backends`.

## 3. Start the broker

The REST API needs an API key. Set one before starting, otherwise every `/api` request returns `503`:

```bash
export OCPP_BROKER_API_KEY=change-me
ocpp-broker-server -c config.yaml
```

On Windows PowerShell use `$env:OCPP_BROKER_API_KEY = "change-me"`.

The broker prints a few startup warnings. For this config you will see that `MyOrg` accepts unauthenticated chargers and that MongoDB is not configured (nothing is persisted, transaction ids are not durable). Both are fine for a first try; see [Configuration](configuration.md) to change them.

Everything is served on port 8765: the charger WebSocket, the REST API, `/health` and the Swagger UI at <http://localhost:8765/docs>.

```bash
curl http://localhost:8765/health
```

```json
{"status":"ok"}
```

## 4. Connect a simulated charger

A charger connects to `ws://HOST:8765/{organization}/{charger id}` and must request the `ocpp1.6` subprotocol. Install the client library and save this as `charger.py`:

```bash
pip install websockets
```

```python
import asyncio
import json
import uuid

import websockets


async def main():
    uri = "ws://localhost:8765/MyOrg/CP001"
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:

        async def call(action, payload):
            await ws.send(json.dumps([2, str(uuid.uuid4()), action, payload]))
            return json.loads(await ws.recv())

        print(await call("BootNotification",
                         {"chargePointVendor": "TestVendor", "chargePointModel": "TestModel"}))
        print(await call("Authorize", {"idTag": "ADMIN001"}))
        print(await call("Authorize", {"idTag": "UNKNOWN"}))

        # Stay connected and answer commands sent through the REST API.
        async for raw in ws:
            frame = json.loads(raw)
            if frame[0] == 2:
                print("received command:", frame[2], frame[3])
                await ws.send(json.dumps([3, frame[1], {"status": "Accepted"}]))


asyncio.run(main())
```

```bash
python charger.py
```

Expected output (timestamps and ids will differ):

```
[3, '…', {'currentTime': '2026-10-03T06:22:22.088867+00:00', 'interval': 300, 'status': 'Accepted'}]
[3, '…', {'idTagInfo': {'status': 'Accepted'}}]
[3, '…', {'idTagInfo': {'status': 'Invalid'}}]
```

The broker accepted the boot, authorized `ADMIN001` from the configured tag list and rejected the tag it does not know.

## 5. Send the charger a command

With `charger.py` still running, in another terminal:

```bash
curl -X POST http://localhost:8765/api/ocpp/organizations/MyOrg/chargers/CP001/commands/Reset \
  -H "X-API-Key: $OCPP_BROKER_API_KEY" -H "Content-Type: application/json" \
  -d '{"type": "Soft"}'
```

```json
{
  "message_id": "fbaecb17-5a3e-440e-b218-e824793edf0f",
  "organization": "MyOrg",
  "charger_id": "CP001",
  "action": "Reset",
  "status": "success",
  "response": {"status": "Accepted"},
  "error": null,
  "timestamp": "2026-10-03T06:22:22.142596+00:00"
}
```

The call waits for the charger and returns its real answer in `response`. `charger.py` prints `received command: Reset {'type': 'Soft'}`. To list connected chargers:

```bash
curl http://localhost:8765/api/ocpp/organizations/MyOrg/chargers -H "X-API-Key: $OCPP_BROKER_API_KEY"
```

## 6. Next: relay to your own backend

To forward a charger's traffic to an existing central system instead, add a relay organization:

```yaml
organizations:
  - name: "Fleet"
    connect_to_backend: true
    backends:
      - id: main
        url: ws://central.example.com/ocpp   # the broker connects to {url}/{charger id}
        leader: true
```

Chargers then connect to `ws://HOST:8765/Fleet/{charger id}`. If the backend is unreachable the broker buffers the charger's messages and delivers them when it returns. Add more backends to get observers and automatic failover: see [Leader/Follower](leader-follower.md).

## Common problems

| Symptom | Cause and fix |
|---------|---------------|
| WebSocket handshake fails with HTTP `403` | The client did not request `ocpp1.6`. Pass the subprotocol (`wscat -c URL -s ocpp1.6`). |
| Closed with code `4002` | The first URL segment is not an organization name in the config. |
| REST calls return `401` | Wrong or missing `X-API-Key`. |
| REST calls return `503` | The broker was started without `OCPP_BROKER_API_KEY`. |
| `404 Charger ... not connected` | The charger id or organization in the URL does not match a live connection. |
| Config is ignored, every charger gets `4002` | The file was not found (use `-c`); the broker started with no organizations. |

More in [Troubleshooting](troubleshooting.md).

## Where to go next

- [Configuration Guide](configuration.md): every setting
- [Broker-as-Backend Mode](broker_as_backend.md): what the broker handles itself
- [API Reference](api-reference.md): REST and WebSocket details
- [Tag Management](tag-management.md): authorization tags
- [Installation Guide](installation.md)
