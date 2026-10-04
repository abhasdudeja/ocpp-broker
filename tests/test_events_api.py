"""
GET /api/events against a real server: what a console tab sees as chargers connect, report, lose
backends, fail over and are sent commands, and how it catches up after a dropped connection.
"""

import asyncio
import json

import httpx
import pytest

from ocpp_broker import backend_manager, server
from ocpp_broker.events import EVENT_TYPES, EventBus

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


def broker_org(name="Home"):
    return {"name": name, "connect_to_backend": False}


def relay_org(leader, follower, name="Fleet", **extra):
    return {
        "name": name,
        "connect_to_backend": True,
        "backends": [{"id": "lead", "url": leader.url, "leader": True}, {"id": "follow", "url": follower.url}],
        **extra,
    }


class Stream:
    """An open /api/events stream read message by message."""

    def __init__(self, client, path="/api/events", headers=None):
        self._manager = client.stream("GET", path, headers=headers)
        self.response = None
        self._lines = None

    async def __aenter__(self):
        self.response = await self._manager.__aenter__()
        self._lines = self.response.aiter_lines()
        return self

    async def __aexit__(self, *exc):
        await self._manager.__aexit__(*exc)

    async def message(self, timeout=5.0):
        """The next SSE message as {"id", "event", "data" (decoded JSON), "comment"}."""
        fields = {}
        async def read():
            async for line in self._lines:
                if line == "":
                    if fields:
                        return fields
                    continue
                if line.startswith(":"):
                    fields["comment"] = line[1:].strip()
                    continue
                name, _, value = line.partition(": ")
                fields[name] = json.loads(value) if name == "data" else value
            raise AssertionError("the stream ended")
        return await asyncio.wait_for(read(), timeout)

    async def event(self, type, where=lambda e: True, timeout=5.0):
        """The next event of this type (anything else is passed over)."""
        while True:
            message = await self.message(timeout)
            if message.get("event") == type and where(message["data"]):
                return message["data"]

    async def events_until(self, type, where=lambda e: True, timeout=5.0):
        """Every event up to and including the first of this type."""
        seen = []
        while True:
            message = await self.message(timeout)
            if message.get("event") in EVENT_TYPES:
                seen.append(message["data"])
                if message["event"] == type and where(message["data"]):
                    return seen


async def boot(charger):
    await charger.call("BootNotification", BOOT_PAYLOAD)
    return await charger.recv()


async def ask(charger, action, payload):
    await charger.call(action, payload)
    return await charger.recv()


# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_stream_needs_the_api_key(run_server):
    port = await run_server({"organizations": [broker_org()]})
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        assert (await client.get("/api/events")).status_code == 401
        assert (await client.get("/api/events", headers={"X-API-Key": "wrong"})).status_code == 401


@pytest.mark.asyncio
async def test_the_stream_is_unbuffered_event_stream_that_opens_with_the_run_id(run_server):
    port = await run_server({"organizations": [broker_org()]})
    async with rest(port) as client:
        run_id = (await client.get("/api/system/info")).json()["instance_id"]
        async with Stream(client) as stream:
            assert stream.response.status_code == 200
            assert stream.response.headers["content-type"].startswith("text/event-stream")
            assert "no-cache" in stream.response.headers["cache-control"]
            assert stream.response.headers["x-accel-buffering"] == "no", "tells nginx not to hold the stream back"
            opening = await stream.message()
            assert opening["event"] == "stream.open"
            assert opening["data"] == {"instance_id": run_id, "last_id": 0, "missed": False}
            assert "id" not in opening, "the opening message is not a numbered event, so it never becomes Last-Event-ID"


@pytest.mark.asyncio
async def test_a_charger_visits_and_every_step_is_an_event(run_server):
    port = await run_server({"organizations": [broker_org()]})
    async with rest(port) as client, Stream(client) as stream:
        await stream.message()
        charger = await ScriptedCharger.connect(port, "Home", "CP-1")
        connected = await stream.event("charger.connected")
        assert (connected["org"], connected["charger_id"]) == ("Home", "CP-1")
        assert connected["data"]["mode"] == "broker" and connected["data"]["ocpp_version"] == "1.6"
        assert connected["data"]["remote_address"].startswith("127.0.0.1")

        await boot(charger)
        booted = await stream.event("charger.boot")
        assert booted["data"] == {"vendor": "TestVendor", "model": "TestModel", "firmware_version": None}

        await ask(charger, "StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"})
        await ask(charger, "StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"})  # no change
        await ask(charger, "StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
        first = await stream.event("charger.status")
        second = await stream.event("charger.status")
        assert first["data"] == {"connector_id": 1, "status": "Available", "previous": None, "error_code": "NoError"}
        assert second["data"]["status"] == "Charging" and second["data"]["previous"] == "Available", "the repeated status said nothing new"

        await ask(charger, "StartTransaction", {"connectorId": 1, "idTag": "SECRET-TAG", "meterStart": 100, "timestamp": "2026-10-04T10:00:00Z"})
        started = await stream.event("transaction.started")
        assert started["data"] == {"connector_id": 1, "meter_start": 100}
        await ask(charger, "StopTransaction", {"transactionId": 7, "meterStop": 4100, "timestamp": "2026-10-04T11:00:00Z", "reason": "Local"})
        stopped = await stream.event("transaction.stopped")
        assert stopped["data"] == {"transaction_id": 7, "meter_stop": 4100, "reason": "Local"}

        await charger.close()
        gone = await stream.event("charger.disconnected")
        assert (gone["org"], gone["charger_id"]) == ("Home", "CP-1") and gone["data"]["connected_for_seconds"] >= 0

    ids = [e.id for e in server.broker.events.replay(0).events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    everything = json.dumps([e.data for e in server.broker.events.replay(0).events])
    assert "SECRET-TAG" not in everything, "id tags are never put on the stream"


@pytest.mark.asyncio
async def test_a_stream_can_be_limited_to_one_organization_or_charger(run_server):
    port = await run_server({"organizations": [broker_org("Home"), broker_org("Depot")]})
    async with rest(port) as client:
        async with Stream(client, "/api/events?org=Depot") as depot, Stream(client, "/api/events?org=Home&charger_id=B") as only_b:
            await depot.message()
            await only_b.message()
            a = await ScriptedCharger.connect(port, "Home", "A")
            b = await ScriptedCharger.connect(port, "Home", "B")
            d = await ScriptedCharger.connect(port, "Depot", "B")
            try:
                seen_by_depot = await depot.events_until("charger.connected")
                assert [(e["org"], e["charger_id"]) for e in seen_by_depot] == [("Depot", "B")]
                seen_by_b = await only_b.events_until("charger.connected")
                assert [(e["org"], e["charger_id"]) for e in seen_by_b] == [("Home", "B")], "not Home/A, and not Depot/B"
            finally:
                for charger in (a, b, d):
                    await charger.close()


@pytest.mark.asyncio
async def test_reconnecting_with_the_last_id_catches_up_on_exactly_what_was_missed(run_server):
    port = await run_server({"organizations": [broker_org()]})
    async with rest(port) as client:
        async with Stream(client) as stream:
            await stream.message()
            one = await ScriptedCharger.connect(port, "Home", "one")
            seen = await stream.event("charger.connected")
        # the connection is gone; things happen meanwhile
        two = await ScriptedCharger.connect(port, "Home", "two")
        three = await ScriptedCharger.connect(port, "Home", "three")
        try:
            while server.broker.events.listener_count:  # the first stream has been torn down
                await asyncio.sleep(0.01)
            async with Stream(client, headers={"Last-Event-ID": str(seen["id"])}) as again:
                opening = await again.message()
                assert opening["data"]["missed"] is False
                caught_up = [(await again.message())["data"]["charger_id"] for _ in range(2)]
                assert caught_up == ["two", "three"], "what happened while it was away, in order, and not what it already had"
        finally:
            for charger in (one, two, three):
                await charger.close()


@pytest.mark.asyncio
async def test_a_client_that_missed_more_than_is_remembered_is_told_to_reload(run_server):
    port = await run_server({"organizations": [broker_org()]})
    server.broker.events = EventBus(history=2)
    async with rest(port) as client:
        for n in range(5):
            server.broker.events.publish("charger.status", "Home", "CP", n=n)
        async with Stream(client, headers={"Last-Event-ID": "1"}) as stream:
            assert (await stream.message())["data"]["missed"] is True
        async with Stream(client, headers={"Last-Event-ID": "999"}) as stream:
            assert (await stream.message())["data"]["missed"] is True, "an id from another run of the broker"
        async with Stream(client, headers={"Last-Event-ID": "not a number"}) as stream:
            assert (await stream.message())["data"]["missed"] is False, "an unreadable id is treated as a first connection"


@pytest.mark.asyncio
async def test_a_first_connection_can_start_with_recent_events(run_server):
    port = await run_server({"organizations": [broker_org()]})
    async with rest(port) as client:
        for n in range(5):
            server.broker.events.publish("charger.status", "Home", "CP", n=n)
        async with Stream(client, "/api/events?replay=2") as stream:
            await stream.message()
            assert [(await stream.message())["data"]["data"]["n"] for _ in range(2)] == [3, 4]
        assert (await client.get("/api/events?replay=-1")).status_code == 422
        assert (await client.get("/api/events?replay=100000")).status_code == 422


@pytest.mark.asyncio
async def test_when_all_streams_are_taken_the_next_gets_a_clear_503_and_frees_up_later(run_server):
    port = await run_server({"organizations": [broker_org()]})
    server.broker.events = EventBus(max_listeners=1)
    async with rest(port) as client:
        async with Stream(client) as first:
            await first.message()
            refused = await client.get("/api/events")
            assert refused.status_code == 503 and "At most 1" in refused.json()["detail"]
        while server.broker.events.listener_count:
            await asyncio.sleep(0.01)
        async with Stream(client) as again:
            assert (await again.message())["event"] == "stream.open"


@pytest.mark.asyncio
async def test_an_open_console_stream_does_not_keep_the_broker_from_stopping(monkeypatch):
    """uvicorn waits for open responses on shutdown; without BrokerServer one open tab would hang it forever."""
    import socket
    import time

    monkeypatch.setattr(server.broker, "config_data", {"organizations": []})
    monkeypatch.setattr(server.broker, "events", EventBus())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    uv = server.BrokerServer(server.build_uvicorn_config(server.app, {"broker": {"host": "127.0.0.1", "port": port}}))
    uv.config.log_level = "warning"
    task = asyncio.create_task(uv.serve(sockets=[sock]))
    while not uv.started:
        await asyncio.sleep(0.01)
    try:
        async with rest(port) as client, Stream(client) as stream:
            assert (await stream.message())["event"] == "stream.open"
            started = time.monotonic()
            uv.should_exit = True
            await asyncio.wait_for(task, timeout=10)
            assert time.monotonic() - started < 5, "the stream was ended, not waited out"
            with pytest.raises(AssertionError, match="the stream ended"):
                await stream.message(timeout=2)
    finally:
        uv.should_exit = True
        if not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_a_stream_asked_for_during_shutdown_is_refused_cleanly(run_server):
    port = await run_server({"organizations": [broker_org()]})
    server.broker.events.close()
    async with rest(port) as client:
        refused = await client.get("/api/events")
        assert refused.status_code == 503 and "shutting down" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_backend_links_and_a_failover_are_events(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower, leader_failover_timeout=0.3)]})
    async with rest(port) as client, Stream(client) as stream:
        await stream.message()
        charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
        try:
            await boot(charger)
            up = {}
            while len(up) < 2:
                link = await stream.event("backend.link", lambda e: e["data"]["connected"])
                up[link["data"]["backend"]] = link["data"]["role"]
            assert up == {"lead": "leader", "follow": "follower"}

            await leader.stop()
            down = await stream.event("backend.link", lambda e: not e["data"]["connected"])
            assert down["data"] == {"backend": "lead", "role": "leader", "connected": False}
            assert (down["org"], down["charger_id"]) == ("Fleet", "CP1")
            failover = await stream.event("backend.failover")
            assert failover["data"] == {"old_leader": "lead", "new_leader": "follow", "reason": "failover"}
        finally:
            await charger.close()
            await leader.stop()
            await follower.stop()


@pytest.mark.asyncio
async def test_shutting_down_a_session_is_not_announced_as_a_backend_outage(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [relay_org(leader, follower)]})
    async with rest(port) as client, Stream(client) as stream:
        await stream.message()
        charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
        try:
            await boot(charger)
            await stream.event("charger.boot")
            await charger.close()
            seen = await stream.events_until("charger.disconnected")
            assert not [e for e in seen if e["type"] == "backend.link" and not e["data"]["connected"]], seen
        finally:
            await leader.stop()
            await follower.stop()


@pytest.mark.asyncio
async def test_a_replaced_charger_is_announced_without_a_disconnect(run_server):
    port = await run_server({"organizations": [broker_org()]})
    async with rest(port) as client, Stream(client) as stream:
        await stream.message()
        first = await ScriptedCharger.connect(port, "Home", "CP1")
        await stream.event("charger.connected")
        second = await ScriptedCharger.connect(port, "Home", "CP1")
        try:
            seen = await stream.events_until("charger.replaced")
            assert [e["type"] for e in seen] == ["charger.connected", "charger.replaced"]
            await first.ws.wait_closed()
            await asyncio.sleep(0.2)
            assert "charger.disconnected" not in [e.type for e in server.broker.events.replay(0).events], "the charger is still there, on its new connection"
        finally:
            await second.close()
