"""
Giving the charger back to the configured leader (leader_failback), and changing the leader by hand.
"""

import asyncio

import httpx
import pytest

from ocpp_broker import backend_manager, server
from ocpp_broker.config import load_config
from ocpp_broker.events import EventBus

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger, wait_for

START = {"connectorId": 1, "idTag": "TAG1", "meterStart": 100, "timestamp": "2026-10-04T10:00:00Z"}


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


@pytest.fixture(autouse=True)
def fresh_tags():
    server.broker.tag_manager = None
    yield
    server.broker.tag_manager = None


def org(*backends, **extra):
    return {
        "name": "Org",
        "connect_to_backend": True,
        "leader_failover_timeout": 0.3,
        "tags": [{"id_tag": "TAG1", "status": "Accepted"}],
        "backends": list(backends),
        **extra,
    }


def external(backend, key, leader=False):
    return {"id": key, "url": backend.url, **({"leader": True} if leader else {})}


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


async def detail(port):
    async with rest(port) as client:
        return (await client.get("/api/chargers/Org/CP1")).json()


async def connect(port):
    charger = await ScriptedCharger.connect(port, "Org", "CP1")
    for _ in range(100):
        info = await detail(port)
        if info.get("leader", {}).get("connected") and info.get("followers_connected") == info.get("followers_total"):
            return charger
        await asyncio.sleep(0.05)
    raise AssertionError("the backends never connected")


async def leader_is(port, key, timeout=8.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    info = {}
    while loop.time() < deadline:
        info = await detail(port)
        if info["leader"]["key"] == key:
            return info
        await asyncio.sleep(0.05)
    raise AssertionError(f"the leader never became {key}: {info['leader']}")


async def stays_leader(port, key, seconds):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        assert (await detail(port))["leader"]["key"] == key
        await asyncio.sleep(0.05)


async def exchange(charger, action, payload):
    await charger.call(action, payload)
    return await charger.recv()


@pytest.mark.asyncio
async def test_the_waiting_for_the_configured_leader_ends_with_the_session(run_server):
    primary, backup = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [org(external(primary, "primary", True), external(backup, "backup"), leader_failback=True, leader_failback_delay=30)]})
    charger = await connect(port)
    try:
        await primary.stop()
        await leader_is(port, "backup")
        session = server.broker.sessions[("Org", "CP1")]
        watcher = session._failback_task
        assert watcher is not None and not watcher.done()
        await charger.close()
        await wait_for(lambda: watcher.done(), timeout=5)
    finally:
        await primary.stop()
        await backup.stop()


def events_of(kind="backend.failover"):
    return [e for e in server.broker.events.replay(0).events if e.type == kind]


# --------------------------------------------------------------------------- automatic
@pytest.mark.asyncio
async def test_the_charger_goes_back_to_the_configured_leader_once_it_has_stayed_up(run_server):
    primary, backup = await ScriptedBackend(first_id=100).start(), await ScriptedBackend(first_id=500).start()
    port = await run_server({"organizations": [org(external(primary, "primary", True), external(backup, "backup"), leader_failback=True, leader_failback_delay=1.0)]})
    primary_port = primary.port
    charger = await connect(port)
    try:
        assert (await exchange(charger, "StartTransaction", START))[2]["transactionId"] == 100
        await primary.stop()
        info = await leader_is(port, "backup")
        assert [b["configured_leader"] for b in info["backends"]] == [False, True], "the configuration still says primary"

        await primary.start(primary_port)
        session = server.broker.sessions[("Org", "CP1")]
        await wait_for(lambda: session.follower_conns[0].is_ready(), timeout=8)
        await asyncio.sleep(0.5)
        assert (await detail(port))["leader"]["key"] == "backup", "not at once: it has to stay up for the whole delay first"
        await leader_is(port, "primary")
        assert [(e.data["new_leader"], e.data["reason"]) for e in events_of()] == [("backup", "failover"), ("primary", "failback")]

        before = len(primary.calls)
        reply = await exchange(charger, "Authorize", {"idTag": "TAG1"})
        assert reply[0] == 3 and len(primary.calls) > before, "the original leader answers again"
    finally:
        await charger.close()
        await primary.stop()
        await backup.stop()


@pytest.mark.asyncio
async def test_a_leader_that_keeps_dropping_is_not_given_the_charger_back(run_server):
    primary, backup = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [org(external(primary, "primary", True), external(backup, "backup"), leader_failback=True, leader_failback_delay=1.0)]})
    primary_port = primary.port
    charger = await connect(port)
    try:
        await primary.stop()
        await leader_is(port, "backup")
        for _ in range(3):  # up for half the delay, then gone again
            await primary.start(primary_port)
            await asyncio.sleep(0.5)
            await primary.stop()
            await asyncio.sleep(0.2)
        assert (await detail(port))["leader"]["key"] == "backup"
        assert [e for e in events_of() if e.data["reason"] == "failback"] == [], "it was never up for the whole delay"
        await primary.start(primary_port)
        await leader_is(port, "primary")
    finally:
        await charger.close()
        await primary.stop()
        await backup.stop()


@pytest.mark.asyncio
async def test_without_leader_failback_the_charger_stays_where_it_is(run_server):
    primary, backup = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [org(external(primary, "primary", True), external(backup, "backup"), leader_failback=False, leader_failback_delay=0.3)]})
    primary_port = primary.port
    charger = await connect(port)
    try:
        await primary.stop()
        await leader_is(port, "backup")
        await primary.start(primary_port)
        await stays_leader(port, "backup", 1.5)
    finally:
        await charger.close()
        await primary.stop()
        await backup.stop()


@pytest.mark.asyncio
async def test_a_leader_chosen_by_hand_is_not_taken_away_again(run_server):
    primary, backup = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [org(external(primary, "primary", True), external(backup, "backup"), leader_failback=True, leader_failback_delay=0.3)]})
    charger = await connect(port)
    try:
        async with rest(port) as client:
            assert (await client.post("/api/chargers/Org/CP1/leader", json={"backend": "backup"})).status_code == 200
        await leader_is(port, "backup")
        await stays_leader(port, "backup", 1.5)
        assert [e.data["reason"] for e in events_of()] == ["manual"]
    finally:
        await charger.close()
        await primary.stop()
        await backup.stop()


@pytest.mark.asyncio
async def test_the_original_leader_is_not_waited_for_when_the_current_leader_has_failed_too(run_server):
    a, b, c = await ScriptedBackend().start(), await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [org(external(a, "a", True), external(b, "b"), external(c, "c"), leader_failback=True, leader_failback_delay=0.4)]})
    a_port, b_port = a.port, b.port
    charger = await connect(port)
    try:
        await a.stop()
        await leader_is(port, "b")
        await b.stop()
        await leader_is(port, "c")
        await a.start(a_port)
        await leader_is(port, "a")
        await b.start(b_port)
        await stays_leader(port, "a", 0.8)
    finally:
        await charger.close()
        for backend in (a, b, c):
            await backend.stop()


@pytest.mark.asyncio
async def test_when_the_broker_took_over_as_standby_it_hands_the_charger_back(run_server):
    primary = await ScriptedBackend(first_id=100).start()
    primary_port = primary.port
    port = await run_server({"organizations": [org(external(primary, "primary", True), {"id": "broker", "local": True}, leader_failback=True, leader_failback_delay=0.5)]})
    charger = await connect(port)
    try:
        await primary.stop()
        await leader_is(port, "broker")
        session = server.broker.sessions[("Org", "CP1")]
        assert session.mode.value == "broker" and session.charge_point is not None
        own = await exchange(charger, "StartTransaction", START)
        assert own[2]["transactionId"] != 100, "the broker numbers it itself, not the external leader"

        await primary.start(primary_port)
        await leader_is(port, "primary")
        assert session.mode.value == "relay" and session.charge_point is None
        before = len(primary.calls)
        reply = await exchange(charger, "Authorize", {"idTag": "TAG1"})
        assert reply[0] == 3 and len(primary.calls) > before
        info = await detail(port)
        assert info["mode"] == "relay" and [b["key"] for b in info["backends"]] == ["primary", "broker"]
    finally:
        await charger.close()
        await primary.stop()


# --------------------------------------------------------------------------- by hand
@pytest.mark.asyncio
async def test_an_operator_can_make_a_connected_follower_the_leader_and_back(run_server):
    a, b = await ScriptedBackend(first_id=100).start(), await ScriptedBackend(first_id=500).start()
    port = await run_server({"organizations": [org(external(a, "a", True), external(b, "b"))]})
    charger = await connect(port)
    try:
        async with rest(port) as client:
            first = await client.post("/api/chargers/Org/CP1/leader", json={"backend": "b"})
            assert first.status_code == 200 and first.json() == {"old_leader": "a", "new_leader": "b"}
            await leader_is(port, "b")
            before = len(b.calls)
            assert (await exchange(charger, "Authorize", {"idTag": "TAG1"}))[0] == 3 and len(b.calls) > before
            back = await client.post("/api/chargers/Org/CP1/leader", json={"backend": "a"})
            assert back.json() == {"old_leader": "b", "new_leader": "a"}
            await leader_is(port, "a")
    finally:
        await charger.close()
        await a.stop()
        await b.stop()


@pytest.mark.asyncio
async def test_a_leader_change_the_operator_cannot_make_is_refused_with_the_reason(run_server):
    a, b = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [org(external(a, "a", True), external(b, "b"), leader_failover_timeout=0)]})
    charger = await connect(port)
    try:
        async with rest(port) as client:
            assert (await client.post("/api/chargers/Org/CP1/leader", json={"backend": "a"})).json()["detail"] == "a already leads"
            assert "not a backend of this charger" in (await client.post("/api/chargers/Org/CP1/leader", json={"backend": "zzz"})).json()["detail"]
            await b.stop()
            await wait_for(lambda: not server.broker.sessions[("Org", "CP1")].follower_conns[0].is_ready())
            refused = await client.post("/api/chargers/Org/CP1/leader", json={"backend": "b"})
            assert refused.status_code == 409 and "not connected" in refused.json()["detail"]
            assert (await client.post("/api/chargers/Org/Nobody/leader", json={"backend": "b"})).status_code == 404
            assert (await client.post("/api/chargers/Org/CP1/leader", json={})).status_code == 422
        assert (await detail(port))["leader"]["key"] == "a"
    finally:
        await charger.close()
        await a.stop()
        await b.stop()


@pytest.mark.asyncio
async def test_changing_the_leader_needs_the_api_key(run_server):
    a, b = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [org(external(a, "a", True), external(b, "b"))]})
    charger = await connect(port)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as anonymous:
            assert (await anonymous.post("/api/chargers/Org/CP1/leader", json={"backend": "b"})).status_code == 401
        assert (await detail(port))["leader"]["key"] == "a"
    finally:
        await charger.close()
        await a.stop()
        await b.stop()


@pytest.mark.asyncio
async def test_a_charger_in_broker_mode_has_no_leader_to_change(run_server):
    port = await run_server({"organizations": [{"name": "Org", "connect_to_backend": False}]})
    charger = await ScriptedCharger.connect(port, "Org", "CP1")
    try:
        await exchange(charger, "BootNotification", BOOT_PAYLOAD)
        async with rest(port) as client:
            assert (await client.post("/api/chargers/Org/CP1/leader", json={"backend": "x"})).status_code == 404
    finally:
        await charger.close()


def test_the_event_bus_keeps_the_reason():
    bus = EventBus()
    event = bus.publish("backend.failover", "Org", "CP1", old_leader="a", new_leader="b", reason="manual")
    assert event.data["reason"] == "manual"


# --------------------------------------------------------------------------- configuration
@pytest.fixture
def write(tmp_path):
    def make(extra):
        path = tmp_path / "config.yaml"
        path.write_text(f"organizations:\n  - name: Fleet\n    backends:\n      - {{url: ws://x/o}}\n{extra}", encoding="utf-8")
        return str(path)

    return make


def test_leader_failback_is_off_and_waits_a_minute_by_default(write):
    [fleet] = load_config(write(""))["organizations"]
    assert fleet["leader_failback"] is False and fleet["leader_failback_delay"] == 60


def test_leader_failback_can_be_set(write):
    [fleet] = load_config(write("    leader_failback: true\n    leader_failback_delay: 5.5\n"))["organizations"]
    assert fleet["leader_failback"] is True and fleet["leader_failback_delay"] == 5.5


@pytest.mark.parametrize(
    "extra, message",
    [
        ("    leader_failback: yes please\n", "leader_failback must be true or false"),
        ("    leader_failback: 1\n", "leader_failback must be true or false"),
        ("    leader_failback_delay: 0\n", "above 0"),
        ("    leader_failback_delay: -3\n", "above 0"),
        ("    leader_failback_delay: soon\n", "above 0"),
        ("    leader_failback_delay: true\n", "above 0"),
    ],
)
def test_leader_failback_settings_that_cannot_be_used_stop_the_broker(write, extra, message):
    with pytest.raises(ValueError, match=message):
        load_config(write(extra))
