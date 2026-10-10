"""
A demo broker with scripted chargers and backends, to look at the web console without any hardware.

    python scripts/demo.py            # then open http://127.0.0.1:8765/ui/  (API key: demo-key)

What it starts, all in this one process:

* the broker on port 8765 with five organizations, one for each way a charger can be served;
* two scripted backends (ports 9101 and 9102) that answer like central systems and number transactions themselves;
* a handful of scripted chargers that boot, report connector statuses, charge, answer the commands you send
  from the console, and keep sending heartbeats.

Things to try: send a command from a charger's page; watch the live indicator and the event feed; stop the
primary backend by creating the file ``demo-stop-primary`` in the current folder (the Standby organization's broker
takes over, the Fleet organization's follower takes over) and bring it back by creating ``demo-start-primary``;
disconnect a charger to see it listed under "Not connected now" (``demo-drop-CP-TEMP`` drops it).

This is a throwaway tool, not part of the package. With MongoDB reachable (default mongodb://127.0.0.1:27018, for
example ``docker run -d -p 27018:27017 mongo:7``; set DEMO_MONGO to use another) it uses a database called
ocpp_demo, which it empties at start. DEMO_USE_ENV=1 uses the MONGODB_CONNECTION_STRING of the environment or .env
instead, still with the database ocpp_demo (set DEMO_MONGO_DB to change it), and leaves it alone unless DEMO_RESET=1. Without MongoDB it uses an in-memory stand-in, so "Not connected now"
still works but nothing is stored anywhere and tag sync reports MongoDB as not connected.
"""

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
os.environ.setdefault("OCPP_BROKER_API_KEY", "demo-key")

from ocpp_broker import server  # noqa: E402
from tests.fakes import ScriptedBackend, ScriptedCharger, fake_mongo_service  # noqa: E402

PORT = int(os.environ.get("DEMO_PORT", "8765"))
PRIMARY_PORT, SECOND_PORT = 9101, 9102
BOOT = {"chargePointVendor": "Acme", "chargePointModel": "Wallbox 7", "firmwareVersion": "2.1.0", "iccid": "8931088", "meterType": "EM-3"}


async def answer_commands(charger):
    """Read what the broker sends and answer commands like a cooperative charger."""
    try:
        while True:
            frame = await charger.recv(timeout=3600)
            if not (isinstance(frame, list) and frame and frame[0] == 2):
                continue
            action = frame[2]
            if action == "GetConfiguration":
                body = {"configurationKey": [{"key": "HeartbeatInterval", "readonly": False, "value": "60"}, {"key": "AuthorizationKey", "readonly": False, "value": "s3cret"}], "unknownKey": []}
            elif action == "Reset" and frame[3].get("type") == "Hard":
                await charger.send([4, frame[1], "NotSupported", "Hard reset is not supported on this model", {}])
                continue
            elif action == "ClearCache":
                continue  # never answers: the console shows a timeout
            elif action == "GetLocalListVersion":
                body = {"listVersion": 3}
            elif action == "GetCompositeSchedule":
                body = {"status": "Accepted"}
            else:
                body = {"status": "Accepted"}
            await charger.send([3, frame[1], body])
    except Exception:
        pass


async def run_charger(org, cid, script, flip=False, lifetime=None):
    charger = await ScriptedCharger.connect(PORT, org, cid)
    asyncio.create_task(answer_commands(charger))
    await charger.call("BootNotification", {**BOOT, "chargePointSerialNumber": f"SN-{cid}"})
    await asyncio.sleep(0.4)
    await script(charger)
    step = 0
    waited = 0
    while lifetime is None or waited < lifetime:
        await asyncio.sleep(6)
        waited += 6
        if pathlib.Path(f"demo-drop-{cid}").exists():
            break
        try:
            await charger.call("Heartbeat", {})
            if flip:
                step += 1
                await charger.call("StatusNotification", {"connectorId": 1, "status": ["Charging", "SuspendedEV", "Charging", "Finishing"][step % 4], "errorCode": "NoError"})
        except Exception:
            return
    await charger.close()


async def watch_primary(primary):
    """The files ``demo-stop-primary`` and ``demo-start-primary`` take the primary backend down and up."""
    while True:
        await asyncio.sleep(1)
        stop, start = pathlib.Path("demo-stop-primary"), pathlib.Path("demo-start-primary")
        if stop.exists():
            stop.unlink()
            print("primary backend stopped", flush=True)
            await primary.stop()
        if start.exists():
            start.unlink()
            print("primary backend started", flush=True)
            await primary.start(PRIMARY_PORT)


async def main():
    primary = await ScriptedBackend(first_id=100).start(PRIMARY_PORT)
    second = await ScriptedBackend(first_id=1).start(SECOND_PORT)
    tags = [
        {"id_tag": "ADMIN001", "status": "Accepted", "description": "Admin card"},
        {"id_tag": "GUEST-7", "status": "Blocked", "tag_type": "NFC", "description": "Lost card"},
        {"id_tag": "TEMP-1", "status": "Accepted", "expiry_date": "2020-01-01T00:00:00Z"},
    ]
    server.broker.config_data = {
        "organizations": [
            {"name": "Home", "connect_to_backend": False, "tags": tags},
            {
                "name": "Standby", "connect_to_backend": True, "leader_failover_timeout": 8, "tags": tags,
                "backends": [{"id": "primary", "url": primary.url, "leader": True}, {"id": "broker", "local": True}, {"id": "analytics", "url": second.url}],
            },
            {
                "name": "Hybrid", "connect_to_backend": True, "tags": tags,
                "backends": [{"id": "broker", "local": True}, {"id": "analytics", "url": second.url}],
            },
            {
                "name": "Fleet", "connect_to_backend": True, "leader_failover_timeout": 8,
                "backends": [{"id": "primary", "url": primary.url, "leader": True}, {"id": "standby", "url": second.url}],
            },
            {"name": "Depot", "connect_to_backend": True, "backends": [{"id": "only", "url": second.url, "leader": True}]},
        ]
    }
    # Which MongoDB: the local one by default. A .env file in the project (the broker reads it) must not redirect the
    # demo to a real database by accident, so the broker's own MONGODB_* variables are set here explicitly.
    # DEMO_USE_ENV=1 uses the connection string from the environment/.env, always with the database DEMO_MONGO_DB
    # (default ocpp_demo, never the one named in .env), and does not empty it unless DEMO_RESET=1.
    use_env = os.environ.get("DEMO_USE_ENV") == "1"
    if use_env:
        from dotenv import dotenv_values

        mongo_uri = os.environ.get("MONGODB_CONNECTION_STRING") or dotenv_values(pathlib.Path(__file__).resolve().parents[1] / ".env").get("MONGODB_CONNECTION_STRING") or ""
    else:
        mongo_uri = os.environ.get("DEMO_MONGO", "mongodb://127.0.0.1:27018")
    demo_db = os.environ.get("DEMO_MONGO_DB", "ocpp_demo")
    os.environ["MONGODB_CONNECTION_STRING"], os.environ["MONGODB_DATABASE_NAME"], os.environ["MONGODB_ENABLED"] = mongo_uri, demo_db, "true"
    server.broker.config_data["mongodb"] = {"enabled": True, "connection_string": mongo_uri, "database_name": demo_db}
    real_mongo = False
    try:
        from motor.motor_asyncio import AsyncIOMotorClient

        probe = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=2000)
        await probe.admin.command("ping")
        if not use_env or os.environ.get("DEMO_RESET") == "1":
            await probe.drop_database(demo_db)
        probe.close()
        await server.broker._initialize_mongodb()
        real_mongo = server.broker.mongodb_service is not None
    except Exception as exc:
        print(f"No usable MongoDB ({exc.__class__.__name__}); using an in-memory stand-in", flush=True)
        server.broker.config_data["mongodb"] = {"enabled": False}
    if not real_mongo:
        server.broker.mongodb_service = fake_mongo_service()
    server.broker.tag_manager = None  # rebuilt with (or without) MongoDB behind it
    server.broker._ensure_tag_manager()
    cfg = {"broker": {"host": "127.0.0.1", "port": PORT}, "security": {"websocket": {"ping_interval": 20, "ping_timeout": 20}}}
    uv = server.BrokerServer(server.build_uvicorn_config(server.app, cfg))
    uv.config.log_level = "warning"
    serving = asyncio.create_task(uv.serve())
    while not uv.started:
        await asyncio.sleep(0.1)
    host = mongo_uri.split("@")[-1].split("/")[0].split("?")[0]  # never print the credentials
    where = f"MongoDB at {host}, database {demo_db}" if real_mongo else "in-memory stand-in for MongoDB"
    print(f"\nBroker up ({where}). Console: http://127.0.0.1:{PORT}/ui/   API key: {os.environ['OCPP_BROKER_API_KEY']}\n", flush=True)

    async def wallbox(c):
        await c.call("StatusNotification", {"connectorId": 0, "status": "Available", "errorCode": "NoError"})
        await c.call("StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"})
        await c.call("StatusNotification", {"connectorId": 2, "status": "Available", "errorCode": "NoError"})

    def charging(card):
        async def script(c):
            await c.call("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
            await c.call("StartTransaction", {"connectorId": 1, "idTag": card, "meterStart": 100, "timestamp": "2026-10-04T08:00:00Z"})
            await asyncio.sleep(0.5)
            await c.call("MeterValues", {"connectorId": 1, "meterValue": [{"timestamp": "2026-10-04T08:05:00Z", "sampledValue": [{"value": "12"}]}]})
        return script

    async def faulted(c):
        await c.call("StatusNotification", {"connectorId": 1, "status": "Faulted", "errorCode": "GroundFailure", "info": "RCD tripped"})

    await asyncio.sleep(0.5)
    await asyncio.gather(
        watch_primary(primary),
        run_charger("Home", "WB-01", wallbox),
        run_charger("Standby", "SB-01", charging("ADMIN001"), flip=True),
        run_charger("Hybrid", "HY-01", charging("ADMIN001")),
        run_charger("Fleet", "CP-001", charging("ADMIN001")),
        run_charger("Fleet", "CP-002", faulted),
        run_charger("Home", "CP-TEMP", wallbox, lifetime=24),  # leaves after a while, so it shows up under "Not connected now"
        serving,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
