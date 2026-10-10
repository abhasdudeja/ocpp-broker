import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone

import websockets


def now():
    return datetime.now(timezone.utc).isoformat()


async def main(uri):
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:

        async def call(action, payload):
            await ws.send(json.dumps([2, str(uuid.uuid4()), action, payload]))
            while True:
                frame = json.loads(await ws.recv())
                if frame[0] == 3:
                    print(f"{action:<20} -> {frame[2]}", flush=True)
                    return frame[2]
                if frame[0] == 2:  # a command arrived meanwhile
                    await answer(frame)

        async def answer(frame):
            print(f"COMMAND received: {frame[2]} {frame[3]}", flush=True)
            await ws.send(json.dumps([3, frame[1], {"status": "Accepted"}]))

        await call("BootNotification", {"chargePointVendor": "TestVendor", "chargePointModel": "TestModel"})
        await call("StatusNotification", {"connectorId": 1, "errorCode": "NoError", "status": "Available", "timestamp": now()})
        await call("Authorize", {"idTag": "ADMIN001"})
        started = await call("StartTransaction", {"connectorId": 1, "idTag": "ADMIN001", "meterStart": 0, "timestamp": now()})
        tx = started["transactionId"]
        await call("StatusNotification", {"connectorId": 1, "errorCode": "NoError", "status": "Charging", "timestamp": now()})
        for wh in (1500, 3200, 5000):
            await asyncio.sleep(1)
            await call("MeterValues", {"connectorId": 1, "transactionId": tx, "meterValue": [
                {"timestamp": now(), "sampledValue": [{"value": str(wh), "measurand": "Energy.Active.Import.Register", "unit": "Wh"}]}]})
        await call("StopTransaction", {"transactionId": tx, "idTag": "ADMIN001", "meterStop": 5000, "timestamp": now(), "reason": "Local"})
        await call("StatusNotification", {"connectorId": 1, "errorCode": "NoError", "status": "Available", "timestamp": now()})
        print("session finished; staying connected for commands (Ctrl+C to quit)", flush=True)
        async for raw in ws:
            frame = json.loads(raw)
            if frame[0] == 2:
                await answer(frame)


asyncio.run(main(sys.argv[1]))