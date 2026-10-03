"""
Transaction ids come from a per-organization MongoDB counter; the in-memory
fallback must be loud about its weaker guarantees.
"""

import asyncio
import logging

import pytest

from ocpp_broker.broker import OcppBroker
from ocpp_broker.mongodb_service import MongoDBService


class CounterCollection:
    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def find_one_and_update(self, flt, update, upsert=False, return_document=None):
        await asyncio.sleep(0)  # yield, as a real round trip would
        doc = self.docs.setdefault(flt["_id"], {"_id": flt["_id"], "seq": 0})
        doc["seq"] += update["$inc"]["seq"]
        return dict(doc)


class FakeDB(dict):
    def __missing__(self, name):
        self[name] = CounterCollection()
        return self[name]


def _mongo(connected=True):
    service = MongoDBService("mongodb://unused")
    service._connected = connected
    service.db = FakeDB()
    return service


@pytest.fixture
def broker():
    b = OcppBroker()
    b.mongodb_service = _mongo()
    return b


@pytest.mark.asyncio
async def test_ids_are_sequential_per_organization(broker):
    assert [await broker.next_transaction_id("A") for _ in range(3)] == [1, 2, 3]
    assert await broker.next_transaction_id("B") == 1
    assert await broker.next_transaction_id("A") == 4


@pytest.mark.asyncio
async def test_concurrent_allocations_are_unique(broker):
    ids = await asyncio.gather(*(broker.next_transaction_id("A") for _ in range(50)))
    assert sorted(ids) == list(range(1, 51))


@pytest.mark.asyncio
async def test_counter_survives_a_broker_restart(broker):
    await broker.next_transaction_id("A")
    await broker.next_transaction_id("A")

    restarted = OcppBroker()
    restarted.mongodb_service = broker.mongodb_service  # same database

    assert await restarted.next_transaction_id("A") == 3


@pytest.mark.asyncio
async def test_fallback_without_mongodb_warns_loudly_once_per_org(caplog):
    broker = OcppBroker()  # no MongoDB configured
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.broker"):
        first = await broker.next_transaction_id("A")
        second = await broker.next_transaction_id("A")
        await broker.next_transaction_id("B")

    assert second == first + 1
    warnings = [r for r in caplog.records if "NOT DURABLE" in r.getMessage()]
    assert len(warnings) == 2, "one banner per organization, not one per transaction"
    assert all(r.levelno == logging.WARNING for r in warnings)
    assert "ORG 'A'" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_fallback_ids_are_not_the_old_tiny_random_seed():
    broker = OcppBroker()
    # Seeded from the clock: a restarted broker must not start over near 1.
    assert await broker.next_transaction_id("A") > 1_000_000_000


@pytest.mark.asyncio
async def test_falls_back_when_mongodb_errors_then_recovers(caplog):
    broker = OcppBroker()
    broker.mongodb_service = _mongo()

    async def boom(*args, **kwargs):
        raise RuntimeError("connection reset")

    good = broker.mongodb_service.next_sequence
    broker.mongodb_service.next_sequence = boom

    with caplog.at_level(logging.WARNING, logger="ocpp_broker.broker"):
        fallback_id = await broker.next_transaction_id("A")

    assert fallback_id > 1_000_000_000
    assert any("connection reset" in r.getMessage() for r in caplog.records)
    assert any("NOT DURABLE" in r.getMessage() for r in caplog.records)

    broker.mongodb_service.next_sequence = good
    assert await broker.next_transaction_id("A") == 1
    assert "A" not in broker._fallback_warned, "warning re-arms for the next outage"


@pytest.mark.asyncio
async def test_disconnected_mongodb_uses_fallback():
    broker = OcppBroker()
    broker.mongodb_service = _mongo(connected=False)
    assert await broker.next_transaction_id("A") > 1_000_000_000


@pytest.mark.asyncio
async def test_next_sequence_refuses_when_disconnected():
    with pytest.raises(RuntimeError):
        await _mongo(connected=False).next_sequence("x")
