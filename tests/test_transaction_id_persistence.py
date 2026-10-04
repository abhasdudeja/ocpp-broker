"""
Keeping the transaction id table across restarts: export/restore in the table,
the background store, and a "restarted" broker over real sockets.
"""

import asyncio
import json
import logging

import pytest
from pymongo.errors import ConnectionFailure

from ocpp_broker.broker import OcppBroker
from ocpp_broker.config import _apply_defaults
from ocpp_broker.transaction_ids import TransactionIdTable
from ocpp_broker.transaction_store import TransactionStore

from .fakes import FakeCharger, ScriptedBackend, fake_mongo_service, wait_for
from .test_transaction_id_relay import begin, finish, start_relay, stop_frame

LEADER, FOLLOW = "P", "S"


class Clocks:
    """Independent monotonic and wall clocks, like the real ones."""

    def __init__(self, mono=1000.0, wall=1_800_000_000.0):
        self.mono, self.wall = mono, wall

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds


def make_table(clocks, **kwargs):
    kwargs.setdefault("retain_closed", 100.0)
    kwargs.setdefault("retain_open", 1000.0)
    return TransactionIdTable(clock=lambda: clocks.mono, wall_clock=lambda: clocks.wall, **kwargs)


class Journal:
    """Collects what the table asks to store, keyed like the real store."""

    def __init__(self, table):
        self.docs, self.expires, self.calls = {}, {}, []
        table.on_change = self.on_change

    def on_change(self, uid, doc, expires):
        self.calls.append((uid, doc, expires))
        if doc is None:
            self.docs.pop(uid, None)
            self.expires.pop(uid, None)
        else:
            self.docs[uid], self.expires[uid] = doc, expires


def call(table, parsed, leader=LEADER, followers=None):
    return table.from_charger(parsed, json.dumps(parsed), leader, {FOLLOW: True} if followers is None else followers)


def start_call(mid, tag="A", meter=0):
    return [2, mid, "StartTransaction", {"connectorId": 1, "idTag": tag, "meterStart": meter, "timestamp": "t0"}]


def begin_table(table, mid, leader_id, follower_id, tag="A", meter=0):
    call(table, start_call(mid, tag, meter))
    answer = table.from_leader(LEADER, [3, mid, {"transactionId": leader_id, "idTagInfo": {"status": "Accepted"}}], json.dumps([3, mid, {"transactionId": leader_id, "idTagInfo": {"status": "Accepted"}}]))
    table.from_follower(FOLLOW, [3, mid, {"transactionId": follower_id, "idTagInfo": {"status": "Accepted"}}])
    return json.loads(answer.frame)[2]["transactionId"]


def stop_call(tx, mid="stop"):
    return [2, mid, "StopTransaction", {"transactionId": tx, "meterStop": 1, "timestamp": "t"}]


def ids_to_followers(plan):
    return [json.loads(f)[3]["transactionId"] for _, f in plan.to_followers]


def dotted_keys(node, path=""):
    """Every dict key containing a dot or starting with $ (MongoDB treats those specially)."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if "." in str(key) or str(key).startswith("$"):
                found.append(f"{path}/{key}")
            found += dotted_keys(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += dotted_keys(value, f"{path}[{i}]")
    return found


# --------------------------------------------------------------------------
# The table: what gets stored, and what comes back
# --------------------------------------------------------------------------
def test_a_record_is_stored_once_it_is_worth_keeping_and_again_when_it_changes():
    clocks = Clocks()
    table = make_table(clocks)
    journal = Journal(table)

    call(table, start_call("s1"))
    assert journal.calls == [], "a start nobody has answered yet is not worth storing"

    table.from_leader(LEADER, [3, "s1", {"transactionId": 7}], json.dumps([3, "s1", {"transactionId": 7}]))
    assert [c[1]["state"] for c in journal.calls] == ["open"]
    table.from_follower(FOLLOW, [3, "s1", {"transactionId": 3, "idTagInfo": {"status": "Accepted"}}])
    assert journal.calls[-1][1]["backend_ids"] == [[LEADER, 7], [FOLLOW, 3]]
    call(table, stop_call(7))
    last = journal.calls[-1][1]
    assert last["state"] == "closed" and last["closed_at"] is not None
    assert len({uid for uid, _, _ in journal.calls}) == 1, "one record throughout"


def test_a_pending_start_that_a_follower_already_numbered_is_stored():
    clocks = Clocks()
    table = make_table(clocks)
    journal = Journal(table)
    call(table, start_call("s1"))
    table.from_follower(FOLLOW, [3, "s1", {"transactionId": 3, "idTagInfo": {"status": "Accepted"}}])
    assert [c[1]["state"] for c in journal.calls] == ["pending"]
    assert journal.calls[0][1]["tx_id"] is None


def test_expiry_follows_the_state_of_the_transaction():
    clocks = Clocks()
    table = make_table(clocks)
    journal = Journal(table)
    begin_table(table, "s1", 7, 3)
    uid = next(iter(journal.docs))
    assert journal.expires[uid] == pytest.approx(clocks.wall + 1000.0), "open: retain_open from now"
    clocks.advance(50)
    call(table, stop_call(7))
    assert journal.expires[uid] == pytest.approx(clocks.wall + 100.0), "closed: retain_closed from the stop"


def test_a_closed_record_keeps_its_original_expiry_and_times_when_it_is_touched_again():
    clocks = Clocks()
    table = make_table(clocks, retain_closed=100_000.0)
    journal = Journal(table)
    started = clocks.wall
    tx = begin_table(table, "s1", 7, 3)
    clocks.advance(40)
    call(table, stop_call(tx))
    closed_at = clocks.wall

    clocks.advance(3700)  # long enough that a retried stop refreshes the stored copy
    call(table, stop_call(tx, "stop-retry"))
    (uid,) = journal.docs
    assert journal.expires[uid] == pytest.approx(closed_at + 100_000.0), "retention counts from the stop, not from the retry"
    doc = journal.docs[uid]
    assert doc["closed_at"] == pytest.approx(closed_at)
    assert doc["created_at"] == pytest.approx(started)


def test_documents_have_no_dotted_keys_even_when_backends_are_named_by_url():
    clocks = Clocks()
    table = make_table(clocks)
    journal = Journal(table)
    url_leader, url_follower = "ws://lead.example.com/ocpp", "ws://follow.example.com/ocpp"
    call(table, start_call("s1"), leader=url_leader, followers={url_follower: True})
    table.from_leader(url_leader, [3, "s1", {"transactionId": 7, "idTagInfo": {"status": "Accepted"}}], json.dumps([3, "s1", {"transactionId": 7}]))
    table.from_follower(url_follower, [3, "s1", {"transactionId": 3, "idTagInfo": {"status": "Accepted"}}])
    (doc,) = journal.docs.values()
    assert dotted_keys(doc) == []
    json.dumps(doc)  # plain data only

    restored = make_table(Clocks())
    assert restored.restore([doc], {url_leader, url_follower}) == 1
    assert restored.snapshot()[0]["backend_ids"] == {url_leader: 7, url_follower: 3}


def test_a_restored_table_behaves_like_the_one_that_was_stored():
    old = Clocks(mono=1000.0)
    table = make_table(old)
    journal = Journal(table)
    open_tx = begin_table(table, "s1", 7, 3, tag="A", meter=1)
    closed_tx = begin_table(table, "s2", 8, 4, tag="B", meter=2)
    call(table, stop_call(closed_tx, "st-B"))

    new = Clocks(mono=5.0, wall=old.wall + 20)  # a new process: its monotonic clock starts elsewhere
    fresh = make_table(new)
    assert fresh.restore(list(journal.docs.values()), {LEADER, FOLLOW}) == 2

    retry = call(fresh, start_call("s1-retry", tag="A", meter=1))  # the open transaction's start, sent again
    assert retry.to_leader is None and retry.to_followers == []
    assert json.loads(retry.reply)[2]["transactionId"] == open_tx
    assert ids_to_followers(call(fresh, stop_call(open_tx, "st-A"))) == [3], "the follower is spoken to in its own id"
    assert ids_to_followers(call(fresh, stop_call(closed_tx, "st-B-retry"))) == [4], "a retried stop still maps"
    assert fresh.stats["unknown_backend"] == 0


def test_a_retried_start_after_a_restart_is_answered_from_the_stored_result():
    old = Clocks()
    table = make_table(old)
    journal = Journal(table)
    tx = begin_table(table, "s1", 7, 3)
    fresh = make_table(Clocks(mono=1.0, wall=old.wall + 5))
    fresh.restore(list(journal.docs.values()), {LEADER, FOLLOW})
    retry = call(fresh, start_call("s1-retry"))
    assert retry.to_leader is None and retry.to_followers == []
    assert json.loads(retry.reply) == [3, "s1-retry", {"transactionId": tx, "idTagInfo": {"status": "Accepted"}}]


def test_a_restored_table_remaps_a_collision_with_a_stored_id():
    old = Clocks()
    table = make_table(old)
    journal = Journal(table)
    begin_table(table, "s1", 7, 3)
    fresh = make_table(Clocks(mono=1.0, wall=old.wall + 5))
    fresh.restore(list(journal.docs.values()), {LEADER, FOLLOW})
    call(fresh, start_call("s2", tag="B", meter=9))
    answer = fresh.from_leader(LEADER, [3, "s2", {"transactionId": 7}], json.dumps([3, "s2", {"transactionId": 7}]))
    assert json.loads(answer.frame)[2]["transactionId"] != 7, "7 is still held by the stored transaction"


def test_expired_records_are_not_restored():
    old = Clocks()
    table = make_table(old)
    journal = Journal(table)
    begin_table(table, "s1", 7, 3, tag="OLD-OPEN")
    closed = begin_table(table, "s2", 8, 4, tag="OLD-CLOSED", meter=1)
    call(table, stop_call(closed))
    docs = list(journal.docs.values())

    later = make_table(Clocks(mono=1.0, wall=old.wall + 500))  # past retain_closed (100), inside retain_open (1000)
    assert later.restore(docs, {LEADER, FOLLOW}) == 1
    assert [r["state"] for r in later.snapshot()] == ["open"]

    much_later = make_table(Clocks(mono=1.0, wall=old.wall + 5000))
    assert much_later.restore(docs, {LEADER, FOLLOW}) == 0


def test_stored_ids_of_backends_that_are_no_longer_configured_are_dropped(caplog):
    old = Clocks()
    table = make_table(old)
    journal = Journal(table)
    begin_table(table, "s1", 7, 3)
    fresh = make_table(Clocks(mono=1.0, wall=old.wall + 1))
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.transaction_ids"):
        fresh.restore(list(journal.docs.values()), {LEADER})  # S was removed from the configuration
    assert fresh.snapshot()[0]["backend_ids"] == {LEADER: 7}
    assert fresh.stats["unknown_backend"] == 1
    assert "no longer configured" in caplog.text


def test_a_live_record_wins_over_a_stored_one_and_junk_is_skipped(caplog):
    old = Clocks()
    table = make_table(old)
    journal = Journal(table)
    begin_table(table, "s1", 7, 3)
    docs = list(journal.docs.values())

    live = make_table(Clocks(mono=1.0, wall=old.wall + 1))
    begin_table(live, "other", 7, 9, tag="LIVE", meter=5)  # the same id 7, a different transaction
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.transaction_ids"):
        restored = live.restore([*docs, {"state": "open"}], {LEADER, FOLLOW})
    assert restored == 0
    assert "unreadable" in caplog.text
    assert live.snapshot()[0]["backend_ids"] == {LEADER: 7, FOLLOW: 9}


def test_an_open_transaction_nobody_stopped_is_eventually_forgotten_and_removed_from_storage():
    clocks = Clocks()
    table = make_table(clocks)
    journal = Journal(table)
    begin_table(table, "s1", 7, 3)
    assert journal.docs
    clocks.advance(1001)
    assert table.is_idle()
    assert journal.docs == {}, "the stored copy is deleted with the in-memory one"
    assert journal.calls[-1][1] is None


def test_the_stored_expiry_is_refreshed_by_activity_but_not_on_every_frame():
    clocks = Clocks()
    table = make_table(clocks, retain_open=100_000.0)
    journal = Journal(table)
    tx = begin_table(table, "s1", 7, 3)
    saved = len(journal.calls)
    for _ in range(20):
        clocks.advance(10)
        call(table, [2, "mv", "MeterValues", {"transactionId": tx, "meterValue": []}])
    assert len(journal.calls) == saved, "meter values within the hour cause no writes"
    clocks.advance(3700)
    call(table, [2, "mv", "MeterValues", {"transactionId": tx, "meterValue": []}])
    assert len(journal.calls) == saved + 1, "an hour on, the expiry is pushed out"


def test_forgetting_a_record_that_was_never_stored_stores_nothing():
    clocks = Clocks()
    table = make_table(clocks)
    journal = Journal(table)
    call(table, start_call("s1"))
    clocks.advance(400)  # stale pending record
    assert table.is_idle()
    assert journal.calls == []


# --------------------------------------------------------------------------
# The store: a background writer that never blocks and never loses a reachable write
# --------------------------------------------------------------------------
class FlakyMongo:
    """Stands in for MongoDBService, recording calls and failing on demand."""

    def __init__(self):
        self.connected = True
        self.saved, self.deleted, self.index_calls, self.attempts = {}, [], 0, 0
        self.fail_with = None  # exception raised by the next writes
        self.reject_uids = set()
        self.index_failures = 0

    def is_connected(self):
        return self.connected

    async def ensure_transaction_map_indexes(self):
        self.index_calls += 1
        if self.index_failures:
            self.index_failures -= 1
            raise RuntimeError("index build refused")

    async def save_transaction_map_doc(self, org, charger, document, expires_at):
        self.attempts += 1
        if self.fail_with is not None:
            raise self.fail_with
        if document["uid"] in self.reject_uids:
            raise ValueError("document rejected")
        self.saved[document["uid"]] = (org, charger, document, expires_at)

    async def delete_transaction_map_doc(self, uid):
        self.deleted.append(uid)
        self.saved.pop(uid, None)

    async def load_transaction_map_docs(self, org, charger):
        if self.fail_with is not None:
            raise self.fail_with
        return [d for o, c, d, _ in self.saved.values() if (o, c) == (org, charger)]


def doc(uid, n=0):
    return {"uid": uid, "state": "open", "n": n}


@pytest.mark.asyncio
async def test_the_store_writes_in_the_background_and_flush_waits_for_it():
    mongo = FlakyMongo()
    store = TransactionStore(mongo)
    store.save("OrgA", "CP1", "u1", doc("u1"), 2_000_000_000.0)
    assert mongo.saved == {}, "save() only notes the change"
    assert await store.flush()
    org, charger, document, expires = mongo.saved["u1"]
    assert (org, charger, document) == ("OrgA", "CP1", doc("u1"))
    assert expires.tzinfo is not None and expires.year == 2033
    await store.close()


@pytest.mark.asyncio
async def test_repeated_changes_to_one_record_are_coalesced_and_the_last_wins():
    mongo = FlakyMongo()
    store = TransactionStore(mongo)
    for n in range(100):
        store.save("OrgA", "CP1", "u1", doc("u1", n), 2_000_000_000.0)
    await store.flush()
    assert mongo.saved["u1"][2]["n"] == 99
    assert mongo.attempts <= 2, "not one write per change"
    await store.close()


@pytest.mark.asyncio
async def test_a_delete_after_a_save_deletes():
    mongo = FlakyMongo()
    store = TransactionStore(mongo)
    store.save("OrgA", "CP1", "u1", doc("u1"), 2_000_000_000.0)
    store.delete("u1")
    await store.flush()
    assert "u1" not in mongo.saved and mongo.deleted == ["u1"]
    await store.close()


@pytest.mark.asyncio
async def test_writes_wait_out_an_outage_and_go_through_afterwards():
    mongo = FlakyMongo()
    mongo.connected = False
    store = TransactionStore(mongo, retry_delay=0.02, max_retry_delay=0.05, max_attempts=2)
    for i in range(5):
        store.save("OrgA", "CP1", f"u{i}", doc(f"u{i}"), 2_000_000_000.0)
    await asyncio.sleep(0.3)
    assert mongo.saved == {} and store.stats["dropped"] == 0, "an outage is never counted against the records"
    mongo.connected = True
    assert await store.flush(timeout=2)
    assert sorted(mongo.saved) == [f"u{i}" for i in range(5)]
    await store.close()


@pytest.mark.asyncio
async def test_a_driver_connection_error_is_an_outage_not_a_rejection():
    mongo = FlakyMongo()
    mongo.fail_with = ConnectionFailure("server selection timed out")
    store = TransactionStore(mongo, retry_delay=0.02, max_retry_delay=0.05, max_attempts=2)
    store.save("OrgA", "CP1", "u1", doc("u1"), 2_000_000_000.0)
    await asyncio.sleep(0.3)
    assert store.stats["dropped"] == 0 and mongo.attempts >= 3
    mongo.fail_with = None
    assert await store.flush(timeout=2)
    assert "u1" in mongo.saved
    await store.close()


@pytest.mark.asyncio
async def test_a_record_mongodb_keeps_rejecting_is_given_up_on_without_blocking_the_rest(caplog):
    mongo = FlakyMongo()
    mongo.reject_uids = {"bad"}
    store = TransactionStore(mongo, retry_delay=0.01, max_retry_delay=0.02, max_attempts=3)
    store.save("OrgA", "CP1", "bad", doc("bad"), 2_000_000_000.0)
    store.save("OrgA", "CP1", "good", doc("good"), 2_000_000_000.0)
    with caplog.at_level(logging.ERROR, logger="ocpp_broker.transaction_store"):
        assert await store.flush(timeout=2)
    assert "good" in mongo.saved and "bad" not in mongo.saved
    assert store.stats["dropped"] == 1
    assert "keeps rejecting" in caplog.text
    await store.close()


@pytest.mark.asyncio
async def test_the_backlog_is_bounded_and_the_oldest_change_is_dropped():
    mongo = FlakyMongo()
    mongo.connected = False
    store = TransactionStore(mongo, max_pending=3, retry_delay=5)
    for i in range(5):
        store.save("OrgA", "CP1", f"u{i}", doc(f"u{i}"), 2_000_000_000.0)
    assert list(store._pending) == ["u2", "u3", "u4"]
    assert store.stats["dropped"] == 2
    await store.close(timeout=0.01)


@pytest.mark.asyncio
async def test_loading_gives_what_was_stored_for_that_charger_only():
    mongo = FlakyMongo()
    store = TransactionStore(mongo)
    store.save("OrgA", "CP1", "u1", doc("u1"), 2_000_000_000.0)
    store.save("OrgA", "CP2", "u2", doc("u2"), 2_000_000_000.0)
    await store.flush()
    assert await store.load("OrgA", "CP1") == [doc("u1")]
    assert await store.load("OrgA", "CP9") == []
    await store.close()


@pytest.mark.asyncio
async def test_loading_never_fails_a_session(caplog):
    mongo = FlakyMongo()
    store = TransactionStore(mongo)
    mongo.connected = False
    assert await store.load("OrgA", "CP1") == []
    mongo.connected = True
    mongo.fail_with = RuntimeError("boom")
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.transaction_store"):
        assert await store.load("OrgA", "CP1") == []
    assert "Could not load" in caplog.text


@pytest.mark.asyncio
async def test_indexes_are_built_once_and_retried_if_the_first_attempt_fails():
    mongo = FlakyMongo()
    mongo.index_failures = 1
    store = TransactionStore(mongo, retry_delay=0.01, max_retry_delay=0.02)
    store.save("OrgA", "CP1", "u1", doc("u1"), 2_000_000_000.0)
    assert await store.flush(timeout=2)
    store.save("OrgA", "CP1", "u2", doc("u2"), 2_000_000_000.0)
    await store.flush()
    assert mongo.index_calls == 2, "one failure, then one success, then no more"
    await store.close()


# --------------------------------------------------------------------------
# The real MongoDBService methods over the in-memory database
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_service_stores_loads_and_deletes_records_and_creates_its_indexes():
    service = fake_mongo_service()
    store = TransactionStore(service)
    store.save("OrgA", "CP1", "u1", {"uid": "u1", "state": "open"}, 2_000_000_000.0)
    store.save("OrgA", "CP2", "u2", {"uid": "u2", "state": "open"}, 2_000_000_000.0)
    await store.flush()
    collection = service.db["transaction_id_map"]
    assert {d["_id"] for d in collection.docs} == {"u1", "u2"}
    assert collection.docs[0]["org_name"] == "OrgA" and collection.docs[0]["expires_at"].tzinfo is not None
    keys = [(k, o) for k, o in collection.indexes]
    assert ("expires_at", {"expireAfterSeconds": 0}) in keys, "records expire on their own"
    assert ([("org_name", 1), ("charger_id", 1)], {}) in keys

    assert await store.load("OrgA", "CP1") == [{"uid": "u1", "state": "open"}]
    store.delete("u1")
    await store.flush()
    assert {d["_id"] for d in collection.docs} == {"u2"}
    await store.close()


# --------------------------------------------------------------------------
# A "restarted" broker over real sockets
# --------------------------------------------------------------------------
async def reconnect(broker, leader, follower, **org):
    """A fresh broker object (a restart) serving the same charger, with the same backends."""
    broker.config_data = {
        "organizations": [
            {
                "name": "OrgA",
                "connect_to_backend": True,
                "backends": [
                    {"id": "lead", "url": leader.url, "leader": True},
                    {"id": "follow", "url": follower.url},
                ],
                **org,
            }
        ]
    }
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    await wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
    session = broker.sessions[("OrgA", "CP1")]
    await wait_for(
        lambda: session.backend_conn
        and session.follower_conns
        and all(c.is_ready() for c in [session.backend_conn, *session.follower_conns])
    )
    return session, charger, task


@pytest.mark.asyncio
async def test_after_a_broker_restart_the_stored_mapping_is_back():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    service = fake_mongo_service()
    charger = None
    try:
        # first run
        first = OcppBroker()
        first.mongodb_service = service
        first_session, charger1, task1 = await reconnect(first, leader, follower)
        tx = await begin(charger1, "s1")
        await wait_for(lambda: first_session._ids.snapshot()[0]["backend_ids"].get("follow") == 1)
        assert await first._tx_store.flush()
        stored = service.db["transaction_id_map"].docs
        assert len(stored) == 1 and stored[0]["data"]["tx_id"] == tx == 10
        await charger1.close()
        await asyncio.wait_for(task1, timeout=5)

        # the broker restarts: a new object, nothing in memory, the same database and backends
        second = OcppBroker()
        second.mongodb_service = service
        session, charger, task = await reconnect(second, leader, follower)
        assert second.transaction_tables[("OrgA", "CP1")].snapshot()[0]["backend_ids"] == {"lead": 10, "follow": 1}

        charger.deliver(stop_frame(tx, "st1", meter_stop=55))
        await wait_for(lambda: follower.transactions[1]["stop"])
        assert follower.transactions[1]["stop"]["meterStop"] == 55, "the follower is still spoken to in its own id"
        assert leader.transactions[10]["stop"]["meterStop"] == 55
        assert follower.unknown == [] and leader.unknown == []
        assert await second._tx_store.flush()
        assert service.db["transaction_id_map"].docs[0]["data"]["state"] == "closed"
    finally:
        if charger is not None:
            await finish(charger, task, leader, follower)
        else:
            await leader.stop()
            await follower.stop()


@pytest.mark.asyncio
async def test_after_a_restart_without_mongodb_the_leader_works_and_the_follower_is_skipped(caplog):
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower, transaction_ids={"follower_wait": 0.2})
    charger2 = task2 = None
    try:
        tx = await begin(charger, "s1")
        await wait_for(lambda: session._ids.snapshot()[0]["backend_ids"].get("follow") == 1)
        await charger.close()
        await asyncio.wait_for(task, timeout=5)

        second = OcppBroker()  # no MongoDB: the table starts empty
        with caplog.at_level(logging.WARNING, logger="ocpp_broker.broker"):
            session2, charger2, task2 = await reconnect(second, leader, follower, transaction_ids={"follower_wait": 0.2})
        assert "memory-only" in caplog.text
        assert second.transaction_tables[("OrgA", "CP1")].snapshot() == []

        charger2.deliver(stop_frame(tx, "st1", meter_stop=66))
        await wait_for(lambda: leader.transactions[10]["stop"])
        await asyncio.sleep(0.4)
        assert leader.transactions[10]["stop"]["meterStop"] == 66, "the leader's id is the charger's id, so it still works"
        assert follower.transactions[1]["stop"] is None, "the follower lost track and is skipped, not sent the wrong id"
        assert follower.unknown == []
    finally:
        if charger2 is not None:
            await finish(charger2, task2, leader, follower)
        else:
            await leader.stop()
            await follower.stop()


@pytest.mark.asyncio
async def test_the_memory_only_warning_is_given_once_per_process(caplog):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    broker = OcppBroker()
    broker.config_data = {
        "organizations": [
            {
                "name": "OrgA",
                "connect_to_backend": True,
                "backends": [{"id": "lead", "url": leader.url, "leader": True}, {"id": "follow", "url": follower.url}],
            }
        ]
    }
    entry = broker.config_data["organizations"][0]
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.broker"):
        await broker.transaction_table("OrgA", "CP1", entry)
        await broker.transaction_table("OrgA", "CP2", entry)
    assert caplog.text.count("memory-only") == 1
    await leader.stop()
    await follower.stop()


@pytest.mark.asyncio
async def test_a_session_starts_even_if_mongodb_cannot_be_read():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    service = fake_mongo_service()
    service._connected = False  # unreachable at the moment the session starts
    broker = OcppBroker()
    broker.mongodb_service = service
    session, charger, task = await reconnect(broker, leader, follower)
    try:
        assert await begin(charger, "s1") == 10
    finally:
        await finish(charger, task, leader, follower)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", [0, -1, "week", True])
def test_retain_open_must_be_a_positive_number(value):
    with pytest.raises(ValueError, match="retain_open"):
        _apply_defaults({"organizations": [{"name": "A", "transaction_ids": {"retain_open": value}}]})


def test_backends_without_ids_or_with_repeated_ids_are_warned_about(caplog):
    cfg = {
        "organizations": [
            {"name": "Anon", "backends": [{"url": "ws://a"}, {"url": "ws://b"}]},
            {"name": "Twice", "backends": [{"id": "x", "url": "ws://a"}, {"id": "x", "url": "ws://b"}]},
            {"name": "Fine", "backends": [{"id": "a", "url": "ws://a"}, {"id": "b", "url": "ws://b"}]},
            {"name": "Single", "backends": [{"url": "ws://a"}]},
            {"name": "Off", "transaction_ids": {"mapping": False}, "backends": [{"url": "ws://a"}, {"url": "ws://b"}]},
        ]
    }
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.config"):
        _apply_defaults(cfg)
    text = caplog.text
    assert "Organization Anon: backend(s) without an id" in text
    assert "Organization Twice: backend id(s) x are used more than once" in text
    for quiet in ("Fine", "Single", "Off"):
        assert f"Organization {quiet}: backend" not in text
