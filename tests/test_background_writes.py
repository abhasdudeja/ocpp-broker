"""
A charger is answered before what it said is written to MongoDB, so a slow or unreachable database cannot
slow the replies, and what it said is still stored, in order, in the background.
"""

import asyncio
import time

import httpx
import pytest

from ocpp_broker import broker as broker_module
from ocpp_broker import server
from ocpp_broker.history import HistoryConfig
from ocpp_broker.write_behind import WriteBehind

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger

START = {"connectorId": 1, "idTag": "TAG1", "meterStart": 100, "timestamp": "2026-10-04T10:00:00Z"}


class SlowMongo:
    """A MongoDB service whose every call takes ``delay`` seconds (``None``: never answers) and is recorded."""

    database_name = "slow"

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []  # (method, kwargs) in the order they finished
        self.started = []
        self.sequence = 0

    def is_connected(self):
        return True

    async def ping(self):
        return True

    async def next_sequence(self, name):
        await self._wait()
        self.sequence += 1
        return self.sequence

    async def _wait(self):
        if self.delay is None:
            await asyncio.sleep(3600)
        elif self.delay:
            await asyncio.sleep(self.delay)

    def __getattr__(self, method):
        if method.startswith("_"):
            raise AttributeError(method)

        async def call(*args, **kwargs):
            self.started.append(method)
            await self._wait()
            self.calls.append((method, kwargs))

        return call

    def methods(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def tags():
    server.broker.tag_manager = None
    yield
    server.broker.tag_manager = None


def org():
    return {"name": "Home", "connect_to_backend": False, "tags": [{"id_tag": "TAG1", "status": "Accepted"}]}


async def timed(charger, action, payload):
    started = time.monotonic()
    await charger.call(action, payload)
    reply = await charger.recv()
    return reply, time.monotonic() - started


async def stored(timeout=5):
    assert await server.broker.writes.flush(timeout), "the writes did not finish"


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


@pytest.mark.asyncio
async def test_every_reply_is_prompt_however_slow_the_database_is_and_everything_is_stored_afterwards(run_server, tags, monkeypatch):
    mongo = SlowMongo(delay=0.6)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        requests = [
            ("BootNotification", BOOT_PAYLOAD),
            ("Heartbeat", {}),
            ("StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"}),
            ("Authorize", {"idTag": "TAG1"}),
            ("StartTransaction", START),
            ("MeterValues", {"connectorId": 1, "meterValue": [{"timestamp": "2026-10-04T10:05:00Z", "sampledValue": [{"value": "5"}]}]}),
            ("DataTransfer", {"vendorId": "Acme"}),
            ("DiagnosticsStatusNotification", {"status": "Idle"}),
            ("FirmwareStatusNotification", {"status": "Idle"}),
        ]
        slowest = 0.0
        for action, payload in requests:
            reply, seconds = await timed(charger, action, payload)
            assert reply[0] == 3, (action, reply)
            slowest = max(slowest, seconds)
            if action == "StartTransaction":
                tx = reply[2]["transactionId"]
        # the transaction counter is the one thing a reply needs from the database, so StartTransaction waits for it
        assert slowest < 1.0, f"a reply took {slowest:.2f} s with a database that takes 0.6 s per call"
        await timed(charger, "StopTransaction", {"transactionId": tx, "idTag": "TAG1", "meterStop": 9, "timestamp": "2026-10-04T11:00:00Z"})

        assert len(mongo.calls) < 6, "most of it is still waiting: the replies did not wait for it"
        await stored(30)
        methods = mongo.methods()
        for expected in ("save_boot_notification", "update_heartbeat_timestamp", "save_status_notification", "save_authorization",
                         "save_meter_values", "save_data_transfer", "save_ocpp_message"):
            assert expected in methods, (expected, methods)
        assert methods.count("save_transaction") == 2, "the start and the stop"
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_database_that_never_answers_does_not_slow_replies_and_is_reported(run_server, tags, monkeypatch):
    mongo = SlowMongo(delay=None)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    monkeypatch.setattr(broker_module, "COUNTER_TIMEOUT", 0.3)
    port = await run_server({"organizations": [org()]})
    monkeypatch.setattr(server.broker, "writes", WriteBehind(timeout=0.1, breaker=2, cooldown=0.1))
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        for action, payload in [("BootNotification", BOOT_PAYLOAD), ("Heartbeat", {}), ("Authorize", {"idTag": "TAG1"})]:
            reply, seconds = await timed(charger, action, payload)
            assert reply[0] == 3 and seconds < 0.3, (action, seconds)
        reply, seconds = await timed(charger, "StartTransaction", START)
        assert reply[0] == 3 and reply[2]["transactionId"] and seconds < 1.5, "the counter gave up after its time limit and the in-memory fallback answered"

        await asyncio.sleep(0.6)
        async with rest(port) as client:
            info = (await client.get("/api/system/info")).json()["mongodb"]
        assert info["pending_writes"] > 0 and info["failed_writes"] > 0 and info["writes_degraded"] is True
        assert info["dropped_writes"] == 0

        for _ in range(5):
            reply, seconds = await timed(charger, "Heartbeat", {})
            assert seconds < 0.3, "still prompt, minutes into the outage"
    finally:
        await charger.close()
        for lane in server.broker.writes._lanes:
            lane._closed = True  # nothing will ever be written; do not wait for it at shutdown


@pytest.mark.asyncio
async def test_many_chargers_are_stored_side_by_side_over_a_slow_link(run_server, tags, monkeypatch):
    mongo = SlowMongo(delay=0.25)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    chargers = [await ScriptedCharger.connect(port, "Home", f"CP{n}") for n in range(8)]
    try:
        started = time.monotonic()
        for charger in chargers:
            await charger.call("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
        for charger in chargers:
            assert (await charger.recv())[0] == 3
        await stored(10)
        took = time.monotonic() - started
        statuses = [kw["charger_id"] for m, kw in mongo.calls if m == "save_status_notification"]
        assert sorted(statuses) == [f"CP{n}" for n in range(8)]
        # 16 writes (status and call result per charger) of 0.25 s would take 4 s one at a time; chargers share lanes by chance
        assert took < 2.8, f"{took:.2f} s: the writes overlapped across chargers"
    finally:
        for charger in chargers:
            await charger.close()


@pytest.mark.asyncio
async def test_a_transaction_is_stored_before_it_is_stopped_even_when_the_database_is_slow(run_server, tags, monkeypatch):
    mongo = SlowMongo(delay=0.05)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        reply, _ = await timed(charger, "StartTransaction", START)
        await timed(charger, "StopTransaction", {"transactionId": reply[2]["transactionId"], "idTag": "TAG1", "meterStop": 9, "timestamp": "2026-10-04T11:00:00Z"})
        await stored()
        kinds = [kw["transaction_type"] for m, kw in mongo.calls if m == "save_transaction"]
        assert kinds == ["start", "stop"]
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_burst_of_heartbeats_is_stored_once_per_charger_not_once_each(run_server, tags, monkeypatch):
    mongo = SlowMongo(delay=0.2)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        for _ in range(25):
            await charger.call("Heartbeat", {})
            await charger.recv()
        await stored(10)
        beats = [kw for m, kw in mongo.calls if m == "update_heartbeat_timestamp"]
        assert 1 <= len(beats) <= 4, f"{len(beats)} stored for 25 heartbeats"
        assert server.broker.writes.coalesced >= 15
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_slow_database_does_not_hold_up_what_a_relay_backend_sends_to_the_charger(run_server, monkeypatch):
    """With the message log on, every frame is a write of its own; none of them is waited for."""
    mongo = SlowMongo(delay=1.0)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    backend = await ScriptedBackend().start()
    port = await run_server({"organizations": [{"name": "Fleet", "connect_to_backend": True, "backends": [{"id": "lead", "url": backend.url, "leader": True}]}]})
    monkeypatch.setattr(server.broker, "history", HistoryConfig(messages=True))
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        for _ in range(100):
            if backend.clients:
                break
            await asyncio.sleep(0.05)
        await backend.send([2, "from-backend", "Reset", {"type": "Soft"}])
        started = time.monotonic()
        frame = await charger.recv(timeout=3)
        assert frame[2] == "Reset" and time.monotonic() - started < 0.5, "forwarded at once, saved afterwards"
        await stored(10)
        assert "save_message" in mongo.methods()
    finally:
        await charger.close()
        await backend.stop()


@pytest.mark.asyncio
async def test_a_rest_command_is_not_delayed_by_a_slow_database(run_server, tags, monkeypatch):
    mongo = SlowMongo(delay=1.0)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client:
            started = time.monotonic()
            request = asyncio.ensure_future(client.post("/api/ocpp/organizations/Home/chargers/CP1/commands", json={"action": "ClearCache", "payload": {}, "timeout": 5}))
            frame = await charger.recv(timeout=3)
            assert time.monotonic() - started < 0.5, "the command went out before its record was written"
            await charger.send([3, frame[1], {"status": "Accepted"}])
            assert (await request).json()["status"] == "success"
            assert time.monotonic() - started < 1.0
        await stored(10)
        assert mongo.methods().count("save_command") == 1, "the command is recorded once, with its outcome"
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_transaction_counter_that_does_not_answer_falls_back_after_its_time_limit(run_server, tags, monkeypatch, caplog):
    mongo = SlowMongo(delay=None)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    monkeypatch.setattr(broker_module, "COUNTER_TIMEOUT", 0.2)
    port = await run_server({"organizations": [org()]})
    monkeypatch.setattr(server.broker, "writes", WriteBehind(timeout=0.05, breaker=1, cooldown=0.05))
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        started = time.monotonic()
        reply, _ = await timed(charger, "StartTransaction", START)
        assert time.monotonic() - started < 1.0
        assert reply[2]["transactionId"] > 1_000_000_000, "an id from the in-memory counter"
        assert "Could not allocate transaction id" in caplog.text and "TimeoutError" in caplog.text
    finally:
        await charger.close()
        for lane in server.broker.writes._lanes:
            lane._closed = True


@pytest.mark.asyncio
async def test_what_is_waiting_is_written_before_the_broker_stops(monkeypatch):
    import socket

    mongo = SlowMongo(delay=0.3)
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    monkeypatch.setattr(server.broker, "config_data", {"organizations": [org()]})
    monkeypatch.setattr(server.broker, "writes", WriteBehind())
    server.broker.tag_manager = None
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    uv = server.BrokerServer(server.build_uvicorn_config(server.app, {"broker": {"host": "127.0.0.1", "port": port}}))
    uv.config.log_level = "warning"
    task = asyncio.create_task(uv.serve(sockets=[sock]))
    while not uv.started:
        await asyncio.sleep(0.01)
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        await timed(charger, "StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"})
        await timed(charger, "Heartbeat", {})
        assert mongo.methods().count("save_status_notification") == 0, "still waiting"
    finally:
        uv.should_exit = True
        await asyncio.wait_for(task, timeout=15)
        server.broker.tag_manager = None
    assert "save_status_notification" in mongo.methods() and "update_heartbeat_timestamp" in mongo.methods(), "written during shutdown"


@pytest.mark.asyncio
async def test_without_a_writer_the_handlers_still_write_in_line():
    """A stand-in broker without ``writes`` (as some tests and embedders use) keeps the old behaviour."""
    from types import SimpleNamespace

    from ocpp_broker.write_behind import background

    mongo = SlowMongo()
    inline = background(SimpleNamespace(mongodb_service=mongo))
    assert inline is mongo
    assert background(SimpleNamespace(mongodb_service=None)) is None
    assert background(SimpleNamespace()) is None
