"""
Tag persistence round-trips through MongoDB, and POST /api/tags/sync.

Uses the real MongoDBService over an in-memory fake database, so tags are stored
the way production stores them (including datetime timestamps).
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from ocpp_broker.api_server import create_api
from ocpp_broker.broker import OcppBroker
from ocpp_broker.mongodb_service import MongoDBService
from ocpp_broker.schemas.tags import OCPPTag
from ocpp_broker.tag_manager import TagManager, TagSyncUnavailable

from .fakes import AUTH_HEADERS


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield dict(doc)

        return gen()


class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []

    @staticmethod
    def _match(doc, query):
        return all(doc.get(k) == v for k, v in query.items())

    def find(self, query):
        return _Cursor([d for d in self.docs if self._match(d, query)])

    async def find_one(self, query):
        return next((dict(d) for d in self.docs if self._match(d, query)), None)

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, query):
                doc.update(update["$set"])
                return
        if upsert:
            self.docs.append({**query, **update["$set"]})

    async def delete_one(self, query):
        for doc in self.docs:
            if self._match(doc, query):
                self.docs.remove(doc)
                return Mock(deleted_count=1)
        return Mock(deleted_count=0)


class FakeDB(dict):
    def __missing__(self, name):
        self[name] = FakeCollection()
        return self[name]


@pytest.fixture
def db():
    return FakeDB()


def make_service(db, connected=True):
    service = MongoDBService("mongodb://unused")
    service._connected = connected
    service.db = db
    return service


def make_manager(db, config=None):
    return TagManager(config or {}, mongodb_service=make_service(db))


def stored(db, org="Org1"):
    return {d["id_tag"]: d for d in db["tags"].docs if d["org_name"] == org}


def external_insert(db, org, id_tag, status="Accepted", **extra):
    """A tag written straight into MongoDB by something other than this broker."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # motor returns naive UTC
    db["tags"].docs.append(
        {"org_name": org, "id_tag": id_tag, "status": status, "tag_type": "RFID",
         "created_at": now, "updated_at": now, **extra}
    )


# ---------------------------------------------------------------------------
# The read path (was broken: stored datetimes did not fit the str timestamp fields)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tags_saved_to_mongodb_can_be_read_back_by_a_fresh_manager(db):
    first = make_manager(db)
    await first.add_tag("Org1", OCPPTag(id_tag="TAG1", status="Accepted", metadata={"k": 1}))
    await first.add_tag("Org1", OCPPTag(id_tag="TAG2", status="Blocked"))

    restarted = make_manager(db)  # empty cache, same database

    tag = await restarted.get_tag("Org1", "TAG1")
    assert tag is not None and tag.metadata == {"k": 1}
    assert isinstance(tag.created_at, str) and isinstance(tag.updated_at, str)
    assert (await restarted.authorize_tag("Org1", "TAG1"))["status"] == "Accepted"
    assert (await restarted.authorize_tag("Org1", "TAG2"))["status"] == "Blocked"
    assert (await restarted.search_tags("Org1", _search())).total == 2


@pytest.mark.asyncio
async def test_tag_list_loads_from_mongodb(db):
    await make_manager(db).add_tag("Org1", OCPPTag(id_tag="TAG1", status="Accepted"))

    tag_list = await make_manager(db).get_tag_list("Org1")

    assert tag_list is not None and [t.id_tag for t in tag_list.tags] == ["TAG1"]


def _search():
    from ocpp_broker.schemas.tags import TagSearchRequest

    return TagSearchRequest()


# ---------------------------------------------------------------------------
# sync_from_mongodb
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_replaces_the_cache_with_what_mongodb_holds(db):
    manager = make_manager(db)
    await manager.add_tag("Org1", OCPPTag(id_tag="KEEP", status="Accepted"))
    await manager.add_tag("Org1", OCPPTag(id_tag="REVOKED", status="Accepted"))
    await manager.add_tag("Org1", OCPPTag(id_tag="CHANGED", status="Accepted"))

    # Someone edits MongoDB directly: revoke one tag, block another, add a new one.
    db["tags"].docs[:] = [d for d in db["tags"].docs if d["id_tag"] != "REVOKED"]
    next(d for d in db["tags"].docs if d["id_tag"] == "CHANGED")["status"] = "Blocked"
    external_insert(db, "Org1", "BRANDNEW")
    assert (await manager.authorize_tag("Org1", "REVOKED"))["status"] == "Accepted", "stale until synced"

    summary = await manager.sync_from_mongodb("Org1")

    assert summary == {"Org1": {"loaded": 3, "seeded": 0, "dropped": 1}}
    assert (await manager.authorize_tag("Org1", "REVOKED"))["status"] == "Invalid"
    assert (await manager.authorize_tag("Org1", "CHANGED"))["status"] == "Blocked"
    assert (await manager.authorize_tag("Org1", "BRANDNEW"))["status"] == "Accepted"
    assert {t.id_tag for t in (await manager.get_tag_list("Org1")).tags} == {"KEEP", "CHANGED", "BRANDNEW"}


@pytest.mark.asyncio
async def test_sync_all_covers_every_organization_and_a_single_org_leaves_others_alone(db):
    manager = make_manager(db)
    await manager.add_tag("Org1", OCPPTag(id_tag="A", status="Accepted"))
    await manager.add_tag("Org2", OCPPTag(id_tag="B", status="Accepted"))
    external_insert(db, "Org1", "A2")
    external_insert(db, "Org2", "B2")
    external_insert(db, "Org3", "C1")  # an org this broker has not seen yet

    only_one = await manager.sync_from_mongodb("Org1")
    assert set(only_one) == {"Org1"}
    assert await manager.get_tag("Org1", "A2") and not manager._tags.get("Org2:B2")

    everything = await manager.sync_from_mongodb()
    assert set(everything) == {"Org1", "Org2", "Org3"}
    assert (await manager.authorize_tag("Org3", "C1"))["status"] == "Accepted"
    assert await manager.get_tag("Org2", "B2")


@pytest.mark.asyncio
async def test_org_with_nothing_stored_is_seeded_from_memory_not_wiped(db):
    """Config tags that were never persisted must survive the first sync."""
    config = {"organizations": [{"name": "Org1", "tags": [
        {"id_tag": "ADMIN001", "status": "Accepted"}, {"id_tag": "STAFF01", "status": "Blocked"}]}]}
    manager = make_manager(db, config)
    assert stored(db) == {}

    summary = await manager.sync_from_mongodb()

    assert summary == {"Org1": {"loaded": 0, "seeded": 2, "dropped": 0}}
    assert set(stored(db)) == {"ADMIN001", "STAFF01"}
    assert (await manager.authorize_tag("Org1", "ADMIN001"))["status"] == "Accepted"

    # Now MongoDB has them, so a second sync is a plain load.
    again = await manager.sync_from_mongodb()
    assert again == {"Org1": {"loaded": 2, "seeded": 0, "dropped": 0}}


@pytest.mark.asyncio
async def test_revoked_tag_does_not_come_back_once_mongodb_holds_the_org(db):
    manager = make_manager(db)
    await manager.add_tag("Org1", OCPPTag(id_tag="LOST", status="Accepted"))
    await manager.add_tag("Org1", OCPPTag(id_tag="OTHER", status="Accepted"))
    db["tags"].docs[:] = [d for d in db["tags"].docs if d["id_tag"] != "LOST"]

    await manager.sync_from_mongodb()
    await manager.sync_from_mongodb()

    assert "LOST" not in stored(db), "sync must never write a revoked tag back"
    assert (await manager.authorize_tag("Org1", "LOST"))["status"] == "Invalid"


@pytest.mark.asyncio
async def test_unreadable_stored_documents_are_skipped_not_fatal(db):
    manager = make_manager(db)
    external_insert(db, "Org1", "GOOD")
    external_insert(db, "Org1", "BAD", status="NotAStatus")

    summary = await manager.sync_from_mongodb("Org1")

    assert summary["Org1"]["loaded"] == 1
    assert await manager.get_tag("Org1", "GOOD") and not await manager.get_tag("Org1", "BAD")


@pytest.mark.asyncio
async def test_sync_does_not_deadlock_with_concurrent_writes(db):
    manager = make_manager(db)
    await manager.add_tag("Org1", OCPPTag(id_tag="A", status="Accepted"))

    results = await asyncio.wait_for(
        asyncio.gather(
            manager.sync_from_mongodb(),
            manager.add_tag("Org1", OCPPTag(id_tag="B", status="Accepted")),
        ),
        timeout=3,
    )

    assert results[1] is True
    assert await manager.get_tag("Org1", "B")


@pytest.mark.asyncio
async def test_sync_without_a_usable_mongodb_raises(db):
    with pytest.raises(TagSyncUnavailable):
        await TagManager({}).sync_from_mongodb()
    with pytest.raises(TagSyncUnavailable):
        await TagManager({}, mongodb_service=make_service(db, connected=False)).sync_from_mongodb()


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------
def _client(manager):
    broker = Mock(spec=OcppBroker)
    broker.tag_manager = manager
    broker.org_backends = {}
    broker.sessions = {}
    broker.config_data = {}
    return TestClient(create_api(broker), headers=AUTH_HEADERS)


def test_sync_route_reports_what_happened(db):
    manager = make_manager(db)
    asyncio.run(manager.add_tag("Org1", OCPPTag(id_tag="A", status="Accepted")))
    external_insert(db, "Org1", "B")

    response = _client(manager).post("/api/tags/sync")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True and "all organizations" in body["message"]
    assert body["organizations"] == {"Org1": {"loaded": 2, "seeded": 0, "dropped": 0}}


def test_sync_route_accepts_an_org_filter(db):
    manager = make_manager(db)
    external_insert(db, "Org1", "A")
    external_insert(db, "Org2", "B")

    body = _client(manager).post("/api/tags/sync?org_name=Org2").json()

    assert body["organizations"] == {"Org2": {"loaded": 1, "seeded": 0, "dropped": 0}}
    assert "for Org2" in body["message"]


def test_sync_route_is_503_without_mongodb_not_a_fake_success():
    response = _client(TagManager({})).post("/api/tags/sync")

    assert response.status_code == 503
    assert "MongoDB" in response.json()["detail"]


def test_sync_route_is_503_when_tag_management_is_off():
    assert _client(None).post("/api/tags/sync").status_code == 503
