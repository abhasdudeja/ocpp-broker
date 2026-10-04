"""
The transaction id store against a real MongoDB: what the in-memory fake cannot show
(index definitions, BSON round trips through the real driver, the TTL index).

Skipped unless OCPP_TEST_MONGO_URI points at a MongoDB, e.g.

    docker run -d -p 27018:27017 mongo:7 --setParameter ttlMonitorSleepSecs=1
    OCPP_TEST_MONGO_URI=mongodb://localhost:27018 pytest tests/test_transaction_store_mongo.py

Each test uses its own throw-away database. The TTL test waits for MongoDB's TTL
monitor (a minute by default, a second with ttlMonitorSleepSecs=1).
"""

import asyncio
import os
import time
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from ocpp_broker.mongodb_service import MongoDBService
from ocpp_broker.transaction_store import TransactionStore

from .test_transaction_id_persistence import FOLLOW, LEADER, Clocks, Journal, begin_table, call, ids_to_followers, make_table, stop_call

URI = os.environ.get("OCPP_TEST_MONGO_URI")
pytestmark = pytest.mark.skipif(not URI, reason="set OCPP_TEST_MONGO_URI to run against a real MongoDB")

FAR_FUTURE = 4_000_000_000.0


@pytest_asyncio.fixture
async def service():
    svc = MongoDBService(URI, f"ocpp_test_{uuid.uuid4().hex[:8]}")
    await svc.connect()
    yield svc
    await svc.client.drop_database(svc.database_name)
    await svc.disconnect()


@pytest.mark.asyncio
async def test_the_indexes_are_created_idempotently_with_a_ttl_on_expires_at(service):
    await service.ensure_transaction_map_indexes()
    await service.ensure_transaction_map_indexes()
    info = await service.db[service.TRANSACTION_MAP].index_information()
    by_key = {tuple(map(tuple, spec["key"])): spec for spec in info.values()}
    assert by_key[(("expires_at", 1),)]["expireAfterSeconds"] == 0
    assert (("org_name", 1), ("charger_id", 1)) in by_key
    assert len(info) == 3, "_id plus our two, no duplicates after a second call"


@pytest.mark.asyncio
async def test_a_real_table_survives_a_round_trip_through_mongodb(service):
    clocks = Clocks()
    table = make_table(clocks)
    journal = Journal(table)
    open_tx = begin_table(table, "s1", 7, 3, tag="A", meter=1)
    closed_tx = begin_table(table, "s2", 8, 4, tag="B", meter=2)
    call(table, stop_call(closed_tx, "st-B"))

    store = TransactionStore(service)
    for uid, doc in journal.docs.items():
        store.save("OrgA", "CP1", uid, doc, FAR_FUTURE)
    store.save("OrgA", "CP2", "elsewhere", {"uid": "elsewhere", "state": "open"}, FAR_FUTURE)
    assert await store.flush(timeout=10)

    loaded = await TransactionStore(service).load("OrgA", "CP1")
    assert sorted(d["uid"] for d in loaded) == sorted(journal.docs), "only this charger's records come back"

    fresh = make_table(Clocks(mono=2.0, wall=clocks.wall + 30))
    assert fresh.restore(loaded, {LEADER, FOLLOW}) == 2
    assert ids_to_followers(call(fresh, stop_call(open_tx, "st-A"))) == [3]
    assert ids_to_followers(call(fresh, stop_call(closed_tx, "st-B-again"))) == [4]
    await store.close()


@pytest.mark.asyncio
async def test_saving_twice_updates_one_document_and_a_delete_removes_it(service):
    when = datetime.fromtimestamp(FAR_FUTURE, tz=timezone.utc)
    await service.save_transaction_map_doc("OrgA", "CP1", {"uid": "u1", "state": "open"}, when)
    await service.save_transaction_map_doc("OrgA", "CP1", {"uid": "u1", "state": "closed"}, when)
    assert await service.load_transaction_map_docs("OrgA", "CP1") == [{"uid": "u1", "state": "closed"}]
    await service.delete_transaction_map_doc("u1")
    await service.delete_transaction_map_doc("u1")  # deleting twice is fine
    assert await service.load_transaction_map_docs("OrgA", "CP1") == []


@pytest.mark.asyncio
async def test_expired_records_are_removed_by_mongodb_itself(service):
    store = TransactionStore(service)
    store.save("OrgA", "CP1", "old", {"uid": "old", "state": "closed"}, time.time() - 60)
    store.save("OrgA", "CP1", "new", {"uid": "new", "state": "open"}, FAR_FUTURE)
    assert await store.flush(timeout=10)

    deadline = time.monotonic() + float(os.environ.get("OCPP_TEST_MONGO_TTL_WAIT", "150"))
    while time.monotonic() < deadline:
        left = {d["uid"] for d in await service.load_transaction_map_docs("OrgA", "CP1")}
        if left == {"new"}:
            break
        await asyncio.sleep(1)
    assert {d["uid"] for d in await service.load_transaction_map_docs("OrgA", "CP1")} == {"new"}
    await store.close()
