"""
Chargers that are not connected now (remembered in MongoDB) and the per-backend summary,
`GET /api/chargers/offline` and `GET /api/backends`.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from ocpp_broker import backend_manager, server
from ocpp_broker.api_server import create_api
from ocpp_broker.broker import OcppBroker
from ocpp_broker.session import ChargerSession

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger, fake_mongo_service, wait_for


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


@pytest.fixture
def mongo(monkeypatch):
    service = fake_mongo_service()
    monkeypatch.setattr(server.broker, "mongodb_service", service, raising=False)
    monkeypatch.setattr(server.broker, "_presence_indexed", False)
    return service


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


def broker_org(name="Home"):
    return {"name": name, "connect_to_backend": False}


async def stored(mongo, org="Home", charger_id="CP1", until=lambda d: True):
    """The presence document, once the background write has happened."""
    for _ in range(100):
        docs = [d for d in mongo.db["charger_presence"].docs if d["org_name"] == org and d["charger_id"] == charger_id]
        if docs and until(docs[0]):
            return docs[0]
        await asyncio.sleep(0.02)
    raise AssertionError(f"nothing stored for {org}/{charger_id}: {mongo.db['charger_presence'].docs}")


async def left(key):
    for _ in range(100):
        if key not in server.broker.sessions:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("still connected")


# ---------------------------------------------------------------------------
# Offline chargers
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_without_mongodb_the_answer_says_why_nothing_is_remembered(run_server):
    port = await run_server({"organizations": [broker_org()]})
    server.broker.mongodb_service = None
    async with rest(port) as client:
        body = (await client.get("/api/chargers/offline")).json()
    assert body == {"available": False, "reason": "MongoDB is not configured, so chargers that are not connected are not remembered", "chargers": [], "total": 0}


@pytest.mark.asyncio
async def test_a_mongodb_that_is_not_connected_or_not_answering_is_reported(run_server, mongo):
    port = await run_server({"organizations": [broker_org()]})
    async with rest(port) as client:
        mongo._connected = False
        assert (await client.get("/api/chargers/offline")).json()["reason"] == "MongoDB is not connected"
        mongo._connected = True
        mongo.list_presence = AsyncMock(side_effect=RuntimeError("timed out"))  # type: ignore[method-assign]
        body = (await client.get("/api/chargers/offline")).json()
        assert body["available"] is False and "timed out" in body["reason"]


@pytest.mark.asyncio
async def test_a_charger_is_remembered_when_it_connects_boots_and_leaves(run_server, mongo):
    port = await run_server({"organizations": [broker_org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    doc = await stored(mongo, until=lambda d: "last_connected_at" in d)
    assert doc["mode"] == "broker" and doc["remote_address"].startswith("127.0.0.1")
    await charger.call("BootNotification", {**BOOT_PAYLOAD, "firmwareVersion": "1.2.3", "chargePointSerialNumber": "SN-1"})
    await charger.recv()
    doc = await stored(mongo, until=lambda d: "vendor" in d)
    assert (doc["vendor"], doc["model"], doc["firmware_version"], doc["serial_number"]) == ("TestVendor", "TestModel", "1.2.3", "SN-1")

    async with rest(port) as client:
        assert (await client.get("/api/chargers/offline")).json()["chargers"] == [], "it is connected now"
        await charger.close()
        await left(("Home", "CP1"))
        await stored(mongo, until=lambda d: "last_disconnected_at" in d)
        body = (await client.get("/api/chargers/offline")).json()
    assert body["available"] is True and body["total"] == 1
    [gone] = body["chargers"]
    assert (gone["org"], gone["charger_id"], gone["vendor"], gone["model"], gone["firmware_version"]) == ("Home", "CP1", "TestVendor", "TestModel", "1.2.3")
    assert gone["last_seen_at"] and gone["last_disconnected_at"] and gone["last_connected_at"] and gone["last_boot_at"]
    assert gone["last_seen_at"] >= gone["last_connected_at"]


@pytest.mark.asyncio
async def test_a_charger_that_comes_back_is_no_longer_listed_as_offline(run_server, mongo):
    port = await run_server({"organizations": [broker_org()]})
    first = await ScriptedCharger.connect(port, "Home", "CP1")
    await first.close()
    await left(("Home", "CP1"))
    await stored(mongo, until=lambda d: "last_disconnected_at" in d)
    async with rest(port) as client:
        assert [c["charger_id"] for c in (await client.get("/api/chargers/offline")).json()["chargers"]] == ["CP1"]
        second = await ScriptedCharger.connect(port, "Home", "CP1")
        try:
            await wait_for(lambda: ("Home", "CP1") in server.broker.sessions)
            assert (await client.get("/api/chargers/offline")).json()["chargers"] == []
        finally:
            await second.close()


@pytest.mark.asyncio
async def test_a_replaced_connection_is_not_recorded_as_a_disconnect(run_server, mongo):
    port = await run_server({"organizations": [broker_org()]})
    first = await ScriptedCharger.connect(port, "Home", "CP1")
    await stored(mongo, until=lambda d: "last_connected_at" in d)
    second = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        await first.ws.wait_closed()
        await asyncio.sleep(0.3)
        assert "last_disconnected_at" not in mongo.db["charger_presence"].docs[0], "the charger never left: it reconnected"
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_the_list_can_be_filtered_and_is_ordered_by_when_each_was_last_seen(run_server, mongo):
    port = await run_server({"organizations": [broker_org("Home"), broker_org("Depot")]})
    now = datetime.now(timezone.utc)
    for org, cid, age in (("Home", "North-1", 50), ("Home", "south-2", 10), ("Depot", "North-3", 30)):
        await mongo.record_presence(org, cid, last_seen_at=now - timedelta(minutes=age))
    async with rest(port) as client:
        everything = (await client.get("/api/chargers/offline")).json()
        assert [c["charger_id"] for c in everything["chargers"]] == ["south-2", "North-3", "North-1"]
        assert [c["charger_id"] for c in (await client.get("/api/chargers/offline", params={"org": "Home"})).json()["chargers"]] == ["south-2", "North-1"]
        assert [c["charger_id"] for c in (await client.get("/api/chargers/offline", params={"q": "NORTH"})).json()["chargers"]] == ["North-3", "North-1"]
        assert (await client.get("/api/chargers/offline", params={"q": "zzz"})).json()["total"] == 0


@pytest.mark.asyncio
async def test_times_read_back_from_mongodb_are_marked_as_utc(run_server, mongo):
    port = await run_server({"organizations": [broker_org()]})
    await mongo.record_presence("Home", "CP9", last_seen_at=datetime(2026, 10, 4, 8, 0, 0))  # naive, as MongoDB returns them
    async with rest(port) as client:
        [gone] = (await client.get("/api/chargers/offline")).json()["chargers"]
    assert gone["last_seen_at"].endswith(("Z", "+00:00")), gone["last_seen_at"]


@pytest.mark.asyncio
async def test_a_charger_served_by_a_relay_is_remembered_too(run_server, mongo):
    backend = await ScriptedBackend().start()
    port = await run_server({"organizations": [{"name": "Fleet", "connect_to_backend": True, "backends": [{"id": "lead", "url": backend.url}]}]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await charger.call("BootNotification", BOOT_PAYLOAD)
        await charger.recv()
        doc = await stored(mongo, "Fleet", until=lambda d: "vendor" in d)
        assert doc["mode"] == "relay" and doc["model"] == "TestModel"
    finally:
        await charger.close()
        await backend.stop()


@pytest.mark.asyncio
async def test_a_mongodb_that_fails_does_not_touch_the_charger(run_server, mongo, caplog):
    port = await run_server({"organizations": [broker_org()]})
    mongo.record_presence = AsyncMock(side_effect=RuntimeError("disk full"))  # type: ignore[method-assign]
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        await charger.call("Heartbeat", {})
        assert (await charger.recv())[0] == 3
        await wait_for(lambda: "Could not record Home/CP1" in caplog.text)
        assert "disk full" in caplog.text
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_the_index_is_made_once_and_is_unique(run_server, mongo):
    port = await run_server({"organizations": [broker_org()]})
    for _ in range(2):
        charger = await ScriptedCharger.connect(port, "Home", "CP1")
        await stored(mongo, until=lambda d: "last_connected_at" in d)
        await charger.close()
        await left(("Home", "CP1"))
        await asyncio.sleep(0.1)
    assert mongo.db["charger_presence"].indexes == [([("org_name", 1), ("charger_id", 1)], {"unique": True})]
    assert len(mongo.db["charger_presence"].docs) == 1, "one document per charger, however often it connects"


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
def relay_org(leader, follower, **extra):
    return {
        "name": "Fleet",
        "connect_to_backend": True,
        "backends": [{"id": "lead", "url": leader.url, "leader": True}, {"id": "follow", "url": follower.url}],
        **extra,
    }


async def up(port, org, charger_ids, followers=1):
    async with rest(port) as client:
        for cid in charger_ids:
            for _ in range(100):
                detail = (await client.get(f"/api/chargers/{org}/{cid}")).json()
                if detail.get("followers_connected", 0) >= followers and detail["leader"]["connected"]:
                    break
                await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_each_backend_is_summed_over_the_connected_chargers(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower), broker_org("Home")]})
    chargers = [await ScriptedCharger.connect(port, "Fleet", cid) for cid in ("CP1", "CP2")]
    try:
        await up(port, "Fleet", ["CP1", "CP2"])
        async with rest(port) as client:
            [fleet] = (await client.get("/api/backends")).json()
        assert (fleet["org"], fleet["mode"], fleet["chargers"]) == ("Fleet", "relay", 2), "plain broker organizations have no backends and are not listed"
        lead, follow = fleet["backends"]
        assert (lead["key"], lead["url"], lead["local"], lead["configured_leader"]) == ("lead", leader.url, False, True)
        assert (lead["leading"], lead["following"], lead["links_up"], lead["links_down"]) == (2, 0, 2, 0)
        assert (follow["leading"], follow["following"], follow["links_up"], follow["links_down"]) == (0, 2, 2, 0)
        assert lead["down_chargers"] == [] and lead["buffered_frames"] == 0
    finally:
        for c in chargers:
            await c.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_a_backend_that_is_down_shows_which_chargers_lost_it_and_what_waits_for_it(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower, leader_failover_timeout=0, backend_outage_timeout=30)]})
    one, two = await ScriptedCharger.connect(port, "Fleet", "CP1"), await ScriptedCharger.connect(port, "Fleet", "CP2")
    try:
        await up(port, "Fleet", ["CP1", "CP2"])
        await leader.stop()
        await one.call("Heartbeat", {})
        await one.call("Heartbeat", {})
        async with rest(port) as client:
            for _ in range(100):
                [fleet] = (await client.get("/api/backends")).json()
                lead = fleet["backends"][0]
                if lead["links_down"] == 2 and lead["buffered_frames"] == 2:
                    break
                await asyncio.sleep(0.05)
        assert (lead["links_up"], lead["links_down"], lead["buffered_frames"]) == (0, 2, 2)
        assert lead["down_chargers"] == ["CP1", "CP2"]
        assert fleet["backends"][1]["links_down"] == 0
    finally:
        await one.close()
        await two.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_after_a_failover_the_counts_follow_who_leads_now(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower, leader_failover_timeout=0.3)]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await up(port, "Fleet", ["CP1"])
        await leader.stop()
        async with rest(port) as client:
            for _ in range(100):
                [fleet] = (await client.get("/api/backends")).json()
                if fleet["backends"][1]["leading"] == 1:
                    break
                await asyncio.sleep(0.05)
        lead, follow = fleet["backends"]
        assert (follow["leading"], follow["following"], lead["leading"], lead["following"]) == (1, 0, 0, 1)
        assert lead["configured_leader"] is True and follow["configured_leader"] is False, "the configuration is unchanged by a failover"
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_the_local_backend_is_listed_among_the_backends_of_its_organization(run_server):
    mirror = await ScriptedBackend().start()
    server.broker.tag_manager = None
    port = await run_server({"organizations": [{"name": "Hybrid", "connect_to_backend": True, "backends": [{"id": "broker", "local": True}, {"id": "mirror", "url": mirror.url}]}]})
    charger = await ScriptedCharger.connect(port, "Hybrid", "CP1")
    try:
        await up(port, "Hybrid", ["CP1"])
        async with rest(port) as client:
            [hybrid] = (await client.get("/api/backends")).json()
        assert hybrid["mode"] == "broker"
        local, other = hybrid["backends"]
        assert (local["key"], local["url"], local["local"], local["leading"], local["links_up"]) == ("broker", None, True, 1, 1)
        assert (other["following"], other["links_up"]) == (1, 1)
    finally:
        await charger.close()
        await mirror.stop()
        server.broker.tag_manager = None


@pytest.mark.asyncio
async def test_backends_can_be_limited_to_one_organization_and_need_the_key(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower), {**relay_org(leader, follower), "name": "Other"}]})
    try:
        async with rest(port) as client:
            assert [o["org"] for o in (await client.get("/api/backends")).json()] == ["Fleet", "Other"]
            assert [o["org"] for o in (await client.get("/api/backends", params={"org": "Other"})).json()] == ["Other"]
            assert (await client.get("/api/backends", params={"org": "Nope"})).json() == []
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as anonymous:
            assert (await anonymous.get("/api/backends")).status_code == 401
            assert (await anonymous.get("/api/chargers/offline")).status_code == 401
    finally:
        await leader.stop()
        await follower.stop()


def test_at_most_twenty_chargers_are_named_for_a_backend_that_is_down():
    broker = OcppBroker()
    broker.config_data = {"organizations": [{"name": "Fleet", "connect_to_backend": True, "backends": [{"id": "lead", "url": "ws://x/ocpp"}]}]}
    entry = broker.config_data["organizations"][0]
    down = SimpleNamespace(key="lead", url="ws://x/ocpp", is_ready=lambda: False, disconnected_since=None, buffered_count=1)
    for n in range(25):
        session = ChargerSession(broker, f"CP{n:02}", "Fleet", entry, SimpleNamespace(client=None, headers={}))
        session.backend_conn, session.follower_conns = down, []  # type: ignore[assignment]
        broker.sessions[("Fleet", f"CP{n:02}")] = session
    body = TestClient(create_api(broker)).get("/api/backends", headers=AUTH_HEADERS).json()
    [lead] = body[0]["backends"]
    assert lead["links_down"] == 25 and lead["buffered_frames"] == 25
    assert lead["down_chargers"] == [f"CP{n:02}" for n in range(20)]
