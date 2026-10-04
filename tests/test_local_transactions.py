"""
What the broker remembers of the transactions it numbered itself (broker mode and the local leader):
a retried StartTransaction gets the same transaction, a repeated StopTransaction is recognised, and the
console can list what is running.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ocpp_broker import server
from ocpp_broker.local_transactions import MAX_CLOSED, MAX_OPEN, RETAIN_CLOSED, LocalTransactions, start_key

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedCharger

START = {"connectorId": 1, "idTag": "TAG1", "meterStart": 100, "timestamp": "2026-10-04T10:00:00Z"}
KEY = start_key(1, "TAG1", 100, "2026-10-04T10:00:00Z")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------
def test_a_start_with_the_same_four_facts_is_found_again_and_a_different_one_is_not():
    registry = LocalTransactions()
    assert registry.find_start(KEY) is None
    registry.started(KEY, 7, 1, "TAG1", 100)
    assert registry.find_start(KEY).tx_id == 7
    for other in (start_key(2, "TAG1", 100, "2026-10-04T10:00:00Z"), start_key(1, "TAG2", 100, "2026-10-04T10:00:00Z"),
                  start_key(1, "TAG1", 101, "2026-10-04T10:00:00Z"), start_key(1, "TAG1", 100, "2026-10-04T10:00:01Z")):
        assert registry.find_start(other) is None, other


def test_a_stop_closes_the_transaction_once():
    registry = LocalTransactions()
    registry.started(KEY, 7, 1, "TAG1", 100)
    tx, repeated = registry.stopped(7, 900, "Local")
    assert (tx.state, tx.meter_stop, tx.reason, repeated) == ("closed", 900, "Local", False)
    tx, repeated = registry.stopped(7, 905, "EVDisconnected")
    assert repeated is True and tx.meter_stop == 900, "the first stop stands"
    assert registry.stats["stopped"] == 1 and registry.stats["repeated_stops"] == 1


def test_a_stop_for_a_transaction_it_never_started_is_reported_as_unknown():
    registry = LocalTransactions()
    assert registry.stopped(99, 1, None) == (None, False)
    assert registry.stats["unknown_stops"] == 1


def test_a_finished_transaction_still_answers_a_retried_start_until_it_is_forgotten():
    clock = Clock()
    registry = LocalTransactions(clock)
    registry.started(KEY, 7, 1, "TAG1", 100)
    registry.stopped(7, 900, None)
    assert registry.find_start(KEY).tx_id == 7, "a start retried after the charge already ended still gets its id"
    clock.now = RETAIN_CLOSED + 1
    assert registry.find_start(KEY) is None
    assert registry.stopped(7, 1, None) == (None, False)


def test_open_transactions_are_never_forgotten_by_age():
    clock = Clock()
    registry = LocalTransactions(clock)
    registry.started(KEY, 7, 1, "TAG1", 100)
    clock.now = RETAIN_CLOSED * 30
    assert registry.find_start(KEY).tx_id == 7 and registry.open_count() == 1


def test_the_registry_is_bounded():
    registry = LocalTransactions()
    for n in range(MAX_OPEN + 10):
        registry.started(start_key(1, "T", n, "t"), n, 1, "T", n)
    assert registry.open_count() == MAX_OPEN
    assert registry.find_start(start_key(1, "T", 0, "t")) is None, "the oldest open one was forgotten"
    assert registry.find_start(start_key(1, "T", MAX_OPEN + 9, "t")) is not None

    closed = LocalTransactions()
    for n in range(MAX_CLOSED + 10):
        closed.started(start_key(1, "T", n, "t"), n, 1, "T", n)
        closed.stopped(n, n, None)
    assert len(closed.snapshot()) == MAX_CLOSED
    assert closed.snapshot()[0]["transaction_id"] == 10, "the oldest finished ones went first"


def test_it_is_idle_only_when_nothing_is_running():
    registry = LocalTransactions()
    assert registry.is_idle()
    registry.started(KEY, 7, 1, "TAG1", 100)
    assert not registry.is_idle()
    registry.stopped(7, 1, None)
    assert registry.is_idle()


def test_the_snapshot_lists_transactions_oldest_first():
    registry = LocalTransactions()
    registry.started(start_key(1, "A", 0, "t1"), 5, 1, "A", 0)
    registry.started(start_key(2, "B", 0, "t2"), 6, 2, "B", 0)
    registry.stopped(5, 9, None)
    assert registry.snapshot() == [
        {"transaction_id": 5, "state": "closed", "connector_id": 1},
        {"transaction_id": 6, "state": "open", "connector_id": 2},
    ]


# ---------------------------------------------------------------------------
# In the handlers
# ---------------------------------------------------------------------------
@pytest.fixture
def tags():
    server.broker.tag_manager = None
    yield
    server.broker.tag_manager = None


def org(**extra):
    return {"name": "Home", "connect_to_backend": False, "tags": [{"id_tag": "TAG1", "status": "Accepted"}], **extra}


@pytest.fixture
def numbers(monkeypatch):
    """Counts the transaction ids the broker hands out."""
    issued = []
    real = server.broker.next_transaction_id

    async def counted(org_name):
        value = await real(org_name)
        issued.append(value)
        return value

    monkeypatch.setattr(server.broker, "next_transaction_id", counted)
    return issued


@pytest.fixture
def mongo(monkeypatch):
    service = MagicMock()
    service.is_connected.return_value = True
    for name in ("save_transaction", "save_boot_notification", "save_authorization", "save_status_notification", "update_heartbeat_timestamp", "save_meter_values", "save_ocpp_message"):
        setattr(service, name, AsyncMock())
    monkeypatch.setattr(server.broker, "mongodb_service", service, raising=False)
    return service


async def exchange(charger, action, payload):
    await charger.call(action, payload)
    return await charger.recv()


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


@pytest.mark.asyncio
async def test_a_retried_start_gets_the_same_transaction_and_is_stored_once(run_server, tags, numbers, mongo):
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        first = await exchange(charger, "StartTransaction", START)
        again = await exchange(charger, "StartTransaction", START)
        assert again[2]["transactionId"] == first[2]["transactionId"]
        assert again[2]["idTagInfo"]["status"] == "Accepted"
        assert len(numbers) == 1, "only one id was taken from the counter"
        starts = [c for c in mongo.save_transaction.await_args_list if c.kwargs["transaction_type"] == "start"]
        assert len(starts) == 1, "one transaction stored, not two"

        other = await exchange(charger, "StartTransaction", {**START, "meterStart": 5000, "timestamp": "2026-10-04T12:00:00Z"})
        assert other[2]["transactionId"] != first[2]["transactionId"], "a genuinely new start is a new transaction"
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_retried_start_with_a_tag_that_is_refused_gets_the_same_id_and_the_refusal(run_server, tags, numbers):
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        bad = {**START, "idTag": "NOBODY"}
        first, again = await exchange(charger, "StartTransaction", bad), await exchange(charger, "StartTransaction", bad)
        assert first[2]["idTagInfo"]["status"] == again[2]["idTagInfo"]["status"] == "Invalid"
        assert first[2]["transactionId"] == again[2]["transactionId"] and len(numbers) == 1
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_repeated_stop_is_answered_and_stored_once(run_server, tags, mongo):
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        tx = (await exchange(charger, "StartTransaction", START))[2]["transactionId"]
        stop = {"transactionId": tx, "idTag": "TAG1", "meterStop": 900, "timestamp": "2026-10-04T11:00:00Z", "reason": "Local"}
        first, again = await exchange(charger, "StopTransaction", stop), await exchange(charger, "StopTransaction", stop)
        assert first[0] == again[0] == 3 and first[2] == again[2] == {"idTagInfo": {"status": "Accepted"}}
        stops = [c for c in mongo.save_transaction.await_args_list if c.kwargs["transaction_type"] == "stop"]
        assert len(stops) == 1
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_stop_for_a_transaction_the_broker_did_not_start_is_answered_and_logged(run_server, tags, mongo, caplog):
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        with caplog.at_level(logging.WARNING, logger="ocpp_broker.charge_point.CP1"):
            reply = await exchange(charger, "StopTransaction", {"transactionId": 4242, "idTag": "TAG1", "meterStop": 1, "timestamp": "2026-10-04T11:00:00Z"})
        assert reply[0] == 3, "a central system cannot refuse a stop"
        assert "4242" in caplog.text and "did not start" in caplog.text
        assert any(c.kwargs["transaction_id"] == 4242 for c in mongo.save_transaction.await_args_list), "it is still recorded"
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_the_meter_readings_sent_with_a_stop_are_stored(run_server, tags, mongo):
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        tx = (await exchange(charger, "StartTransaction", START))[2]["transactionId"]
        data = [{"timestamp": "2026-10-04T10:30:00Z", "sampledValue": [{"value": "55", "unit": "Wh"}]}]
        await exchange(charger, "StopTransaction", {"transactionId": tx, "meterStop": 900, "timestamp": "2026-10-04T11:00:00Z", "transactionData": data})
        [stop] = [c for c in mongo.save_transaction.await_args_list if c.kwargs["transaction_type"] == "stop"]
        # plain dicts, in the library's snake_case like the other stored readings (MeterValues)
        assert stop.kwargs["transaction_data"] == [{"timestamp": "2026-10-04T10:30:00Z", "sampled_value": [{"value": "55", "unit": "Wh"}]}]
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_the_console_lists_what_the_broker_started_and_how_many_are_running(run_server, tags):
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        tx1 = (await exchange(charger, "StartTransaction", START))[2]["transactionId"]
        tx2 = (await exchange(charger, "StartTransaction", {**START, "connectorId": 2, "timestamp": "2026-10-04T10:01:00Z"}))[2]["transactionId"]
        await exchange(charger, "StopTransaction", {"transactionId": tx1, "idTag": "TAG1", "meterStop": 9, "timestamp": "2026-10-04T11:00:00Z"})
        async with rest(port) as client:
            detail = (await client.get("/api/chargers/Home/CP1")).json()
            rows = {r["transaction_id"]: r for r in detail["transactions"]}
            assert rows[tx1]["state"] == "closed" and rows[tx2]["state"] == "open"
            assert rows[tx2]["backend_ids"] == {"broker": tx2}
            assert detail["open_transactions"] == 1
            assert (await client.get("/api/chargers")).json()["chargers"][0]["open_transactions"] == 1
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_running_transaction_survives_the_chargers_reconnecting_and_an_idle_registry_does_not(run_server, tags, numbers):
    port = await run_server({"organizations": [org()]})
    first = await ScriptedCharger.connect(port, "Home", "CP1")
    tx = (await exchange(first, "StartTransaction", START))[2]["transactionId"]
    await first.close()
    await _gone(("Home", "CP1"))
    assert ("Home", "CP1") in server.broker.local_transactions, "a transaction is still running, so it is remembered"

    second = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        again = await exchange(second, "StartTransaction", START)
        assert again[2]["transactionId"] == tx, "the charger asked again after reconnecting"
        await exchange(second, "StopTransaction", {"transactionId": tx, "idTag": "TAG1", "meterStop": 9, "timestamp": "2026-10-04T11:00:00Z"})
    finally:
        await second.close()
    await _gone(("Home", "CP1"))
    assert ("Home", "CP1") not in server.broker.local_transactions, "nothing is running: nothing is kept for a charger that left"


async def _gone(key):
    import asyncio

    for _ in range(100):
        if key not in server.broker.sessions:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the session did not end")


@pytest.mark.asyncio
async def test_a_charger_boots_and_everything_still_works_without_a_registry(run_server, tags):
    """Brokers built by tests or by hand without ``local_transactions_for`` keep the old behaviour."""
    port = await run_server({"organizations": [org()]})
    real = server.broker.local_transactions_for
    server.broker.local_transactions_for = None  # type: ignore[assignment]
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        assert (await exchange(charger, "BootNotification", BOOT_PAYLOAD))[2]["status"] == "Accepted"
        first, again = await exchange(charger, "StartTransaction", START), await exchange(charger, "StartTransaction", START)
        assert first[2]["transactionId"] != again[2]["transactionId"], "no memory, no de-duplication"
    finally:
        server.broker.local_transactions_for = real  # type: ignore[method-assign]
        await charger.close()
