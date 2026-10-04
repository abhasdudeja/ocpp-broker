"""
The console's read-only endpoints (/api/orgs, /api/chargers, /api/chargers/{org}/{id}),
against a real server with scripted chargers and backends.
"""

import asyncio

import httpx
import pytest

from ocpp_broker import backend_manager

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger

BOOT = {**BOOT_PAYLOAD, "chargePointSerialNumber": "SN-1", "firmwareVersion": "1.2.3", "iccid": "8931", "meterType": "M1"}


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


async def eventually(client, path, predicate, timeout=5.0):
    """Poll a GET until the JSON satisfies ``predicate``; returns it (or fails with the last body)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    body = None
    while loop.time() < deadline:
        response = await client.get(path)
        body = response.json() if response.status_code == 200 else None
        if body is not None and predicate(body):
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"{path} never matched; last body: {body}")


def broker_org(name="Home"):
    return {"name": name, "connect_to_backend": False}


def relay_org(leader, follower, name="Fleet", **extra):
    return {
        "name": name,
        "connect_to_backend": True,
        "backends": [
            {"id": "lead", "url": leader.url, "leader": True},
            {"id": "follow", "url": follower.url},
        ],
        **extra,
    }


async def boot(charger, payload=BOOT):
    await charger.call("BootNotification", payload)
    return await charger.recv()


# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_console_endpoints_need_the_api_key(run_server):
    port = await run_server({"organizations": [broker_org()]})
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        for path in ("/api/orgs", "/api/chargers", "/api/chargers/Home/CP1"):
            assert (await client.get(path)).status_code == 401, path
            assert (await client.get(path, headers={"X-API-Key": "wrong"})).status_code == 401, path


@pytest.mark.asyncio
async def test_organizations_are_described_with_their_backends_and_connected_chargers(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server(
        {
            "organizations": [
                broker_org(),
                relay_org(leader, follower, charger_auth={"required": True, "credentials": {}}),
                {"name": "Solo", "connect_to_backend": True, "backends": [{"url": leader.url}]},
            ]
        }
    )
    try:
        async with rest(port) as client:
            orgs = {o["name"]: o for o in (await client.get("/api/orgs")).json()}
            assert orgs["Home"] == {
                "name": "Home", "mode": "broker", "ocpp_version": "1.6", "charger_auth_required": False,
                "connected_chargers": 0, "backends": [], "transaction_id_mapping": False,
            }
            fleet = orgs["Fleet"]
            assert fleet["mode"] == "relay" and fleet["charger_auth_required"] is True
            assert fleet["transaction_id_mapping"] is True, "two backends: ids are translated"
            assert [(b["key"], b["leader"], b["url"]) for b in fleet["backends"]] == [
                ("lead", True, leader.url), ("follow", False, follower.url)
            ]
            assert orgs["Solo"]["transaction_id_mapping"] is False and orgs["Solo"]["backends"][0]["key"] == leader.url

            charger = await ScriptedCharger.connect(port, "Home", "CP1")
            try:
                await boot(charger)
                counted = await eventually(client, "/api/orgs", lambda o: any(x["connected_chargers"] for x in o))
                assert {o["name"]: o["connected_chargers"] for o in counted}["Home"] == 1
            finally:
                await charger.close()
    finally:
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_a_broker_mode_charger_shows_what_it_reported(run_server):
    port = await run_server({"organizations": [broker_org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP-Alpha")
    try:
        await boot(charger)
        await charger.call("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
        await charger.recv()
        await charger.call("StatusNotification", {"connectorId": 2, "status": "Faulted", "errorCode": "GroundFailure", "info": "RCD"})
        await charger.recv()
        await charger.call("Heartbeat", {})
        await charger.recv()

        async with rest(port) as client:
            listing = await eventually(client, "/api/chargers", lambda b: b["total"] == 1 and b["chargers"][0]["connector_statuses"].get("2"))
            row = listing["chargers"][0]
            assert (row["org"], row["charger_id"], row["online"], row["mode"], row["ocpp_version"]) == ("Home", "CP-Alpha", True, "broker", "1.6")
            assert (row["vendor"], row["model"], row["firmware_version"]) == ("TestVendor", "TestModel", "1.2.3")
            assert row["connector_statuses"] == {"1": "Charging", "2": "Faulted"}
            assert row["remote_address"].startswith("127.0.0.1")
            assert row["leader"] == {
                "key": "broker", "url": None, "role": "leader", "local": True, "connected": True, "buffered_frames": 0, "down_for_seconds": None
            }
            assert (row["followers_total"], row["followers_connected"], row["open_transactions"]) == (0, 0, 0)

            detail = (await client.get("/api/chargers/Home/CP-Alpha")).json()
            assert detail["boot"]["serial_number"] == "SN-1" and detail["boot"]["iccid"] == "8931" and detail["boot"]["meter_type"] == "M1"
            faulted = next(c for c in detail["connectors"] if c["connector_id"] == 2)
            assert (faulted["status"], faulted["error_code"], faulted["info"]) == ("Faulted", "GroundFailure", "RCD")
            assert detail["last_heartbeat_at"] is not None and detail["last_seen"] is not None
            assert detail["frames_in"] >= 4 and detail["frames_out"] >= 4, "the broker answered each message"
            assert detail["backends"][0]["local"] is True
            assert detail["transactions"] == [] and detail["transaction_id_mapping"] is False
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_filters_and_the_unknown_charger(run_server):
    port = await run_server({"organizations": [broker_org("Home"), broker_org("Depot")]})
    one = await ScriptedCharger.connect(port, "Home", "North-1")
    two = await ScriptedCharger.connect(port, "Depot", "south-2")
    try:
        async with rest(port) as client:
            await eventually(client, "/api/chargers", lambda b: b["total"] == 2)
            assert [c["charger_id"] for c in (await client.get("/api/chargers")).json()["chargers"]] == ["south-2", "North-1"], "ordered by organization, then id"
            assert (await client.get("/api/chargers", params={"org": "Home"})).json()["total"] == 1
            assert (await client.get("/api/chargers", params={"q": "SOUTH"})).json()["chargers"][0]["charger_id"] == "south-2"
            assert (await client.get("/api/chargers", params={"q": "north"})).json()["chargers"][0]["charger_id"] == "North-1"
            assert (await client.get("/api/chargers", params={"q": "zzz"})).json() == {"chargers": [], "total": 0}
            missing = await client.get("/api/chargers/Home/nope")
            assert missing.status_code == 404 and "not connected" in missing.json()["detail"]
    finally:
        await one.close()
        await two.close()


@pytest.mark.asyncio
async def test_a_charger_disappears_when_it_disconnects(run_server):
    port = await run_server({"organizations": [broker_org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    async with rest(port) as client:
        await eventually(client, "/api/chargers", lambda b: b["total"] == 1)
        await charger.close()
        await eventually(client, "/api/chargers", lambda b: b["total"] == 0)


@pytest.mark.asyncio
async def test_a_relay_charger_shows_its_leader_followers_and_the_transaction_table(run_server):
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    port = await run_server({"organizations": [relay_org(leader, follower)]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await boot(charger)
        await charger.call("StartTransaction", {"connectorId": 1, "idTag": "A", "meterStart": 5, "timestamp": "2026-10-04T10:00:00Z"})
        assert (await charger.recv())[2]["transactionId"] == 10

        async with rest(port) as client:
            detail = await eventually(
                client, "/api/chargers/Fleet/CP1",
                lambda d: d["transactions"] and d["transactions"][0]["backend_ids"] == {"lead": 10, "follow": 1},
            )
            assert detail["mode"] == "relay" and detail["transaction_id_mapping"] is True
            assert [(b["key"], b["role"], b["connected"], b["local"]) for b in detail["backends"]] == [
                ("lead", "leader", True, False), ("follow", "follower", True, False)
            ]
            assert detail["backends"][0]["url"] == leader.url and detail["backends"][1]["url"] == follower.url
            assert detail["transactions"] == [
                {"transaction_id": 10, "state": "open", "backend_ids": {"lead": 10, "follow": 1}, "degraded": [], "awaiting": []}
            ]
            assert (detail["followers_total"], detail["followers_connected"], detail["open_transactions"], detail["degraded_transactions"]) == (1, 1, 1, 0)
            assert detail["vendor"] == "TestVendor", "relay mode learns it by watching the boot message"
            assert detail["frames_in"] >= 2 and detail["frames_out"] >= 2, "traffic is counted in relay mode too"

            await follower.stop()
            await eventually(client, "/api/chargers/Fleet/CP1", lambda d: d["followers_connected"] == 0)
            down = (await client.get("/api/chargers/Fleet/CP1")).json()["backends"][1]
            assert down["connected"] is False and down["down_for_seconds"] is not None
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_a_transaction_a_backend_never_learned_is_counted_as_degraded(run_server):
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1, mute=True).start()
    port = await run_server({"organizations": [relay_org(leader, follower, transaction_ids={"follower_wait": 0.2})]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await charger.call("StartTransaction", {"connectorId": 1, "idTag": "A", "meterStart": 5, "timestamp": "2026-10-04T10:00:00Z"})
        await charger.recv()
        await charger.call("MeterValues", {"connectorId": 1, "transactionId": 10, "meterValue": []})
        await charger.recv()
        async with rest(port) as client:
            detail = await eventually(client, "/api/chargers/Fleet/CP1", lambda d: d["degraded_transactions"] == 1)
            assert detail["transactions"][0]["degraded"] == ["follow"]
            assert detail["id_table_stats"]["skipped"] >= 1
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_buffered_frames_and_a_failover_are_visible(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower, leader_failover_timeout=0, backend_outage_timeout=30)]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await boot(charger)
        async with rest(port) as client:
            await leader.stop()
            await eventually(client, "/api/chargers/Fleet/CP1", lambda d: d["leader"]["connected"] is False)
            await charger.call("Heartbeat", {})
            await charger.call("Heartbeat", {})
            detail = await eventually(client, "/api/chargers/Fleet/CP1", lambda d: d["buffered_frames"] == 2)
            assert detail["leader"]["buffered_frames"] == 2 and detail["leader"]["down_for_seconds"] is not None
            assert (await client.get("/api/chargers")).json()["chargers"][0]["buffered_frames"] == 2
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_after_a_failover_the_new_leader_comes_first(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower, leader_failover_timeout=0.3)]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await boot(charger)
        async with rest(port) as client:
            await eventually(client, "/api/chargers/Fleet/CP1", lambda d: d["followers_connected"] == 1)
            await leader.stop()
            detail = await eventually(client, "/api/chargers/Fleet/CP1", lambda d: d["leader"]["key"] == "follow", timeout=5)
            assert [(b["key"], b["role"]) for b in detail["backends"]] == [("follow", "leader"), ("lead", "follower")]
            assert detail["backends"][1]["connected"] is False
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_reservations_and_profiles_the_charger_holds_are_listed(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower)]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await boot(charger)
        await leader.command("ReserveNow", {"connectorId": 1, "expiryDate": "2099-01-01T00:00:00Z", "idTag": "T", "reservationId": 5}, "r1")
        assert (await charger.recv())[3]["reservationId"] == 5
        await charger.send([3, "r1", {"status": "Accepted"}])
        await leader.command("SetChargingProfile", {"connectorId": 1, "csChargingProfiles": {"chargingProfileId": 3, "stackLevel": 0}}, "p1")
        await charger.recv()
        await charger.send([3, "p1", {"status": "Accepted"}])
        async with rest(port) as client:
            detail = await eventually(client, "/api/chargers/Fleet/CP1", lambda d: d["reservations"] and d["charging_profiles"])
            assert detail["reservations"][0]["id"] == 5 and detail["reservations"][0]["backend_ids"] == {"lead": 5}
            assert detail["charging_profiles"][0]["id"] == 3
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_a_single_backend_organization_has_no_transaction_table(run_server):
    only = await ScriptedBackend().start()
    port = await run_server({"organizations": [{"name": "Solo", "connect_to_backend": True, "backends": [{"id": "only", "url": only.url, "leader": True}]}]})
    charger = await ScriptedCharger.connect(port, "Solo", "CP1")
    try:
        await boot(charger)
        async with rest(port) as client:
            detail = await eventually(client, "/api/chargers/Solo/CP1", lambda d: d["leader"]["connected"])
            assert detail["transaction_id_mapping"] is False and detail["transactions"] == []
            assert (detail["followers_total"], detail["id_table_stats"]) == (0, {})
    finally:
        await charger.close()
        await only.stop()


def test_the_console_routes_are_in_the_openapi_schema_with_typed_responses():
    from ocpp_broker import server

    paths = server.app.openapi()["paths"]
    for path, ref in (
        ("/api/orgs", None),
        ("/api/chargers", "ChargerList"),
        ("/api/chargers/{org}/{charger_id}", "ChargerDetail"),
    ):
        schema = paths[path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema.get("$ref", "").endswith(ref) if ref else schema["items"]["$ref"].endswith("OrgSummary"), path
