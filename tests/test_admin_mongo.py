"""
Organizations kept in MongoDB (admin.store: mongodb), against a real MongoDB: the compare-and-set on the revision,
the history of earlier versions, two instances sharing the organizations, and the shared audit log.

Skipped unless OCPP_TEST_MONGO_URI points at a MongoDB (see test_transaction_store_mongo.py).
"""

import asyncio
import os
import uuid

import httpx
import pytest
import pytest_asyncio
import yaml

from ocpp_broker import server
from ocpp_broker.broker import OcppBroker
from ocpp_broker.config import _validate_config
from ocpp_broker.config_store import Conflict, ConfigError, NotWritable
from ocpp_broker.mongo_config_store import MongoConfigStore, encode
from ocpp_broker.mongodb_service import MongoDBService

from .fakes import AUTH_HEADERS, wait_for

URI = os.environ.get("OCPP_TEST_MONGO_URI")


def change(**org):
    return {"op": "upsert", "org": org}


# --------------------------------------------------------------------------- configuration (no MongoDB needed)
@pytest.mark.parametrize(
    "admin, message",
    [
        ({"store": "redis"}, "admin.store must be file or mongodb"),
        ({"poll_seconds": 0}, "admin.poll_seconds must be a number of seconds, 1 or more"),
        ({"poll_seconds": "5"}, "admin.poll_seconds must be a number"),
        ({"poll_seconds": True}, "admin.poll_seconds must be a number"),
    ],
)
def test_store_settings_that_cannot_be_used_stop_the_broker_at_startup(admin, message):
    with pytest.raises(ValueError, match=message):
        _validate_config({"broker": {"port": 8000}, "organizations": [], "admin": admin})


def test_the_mongodb_store_needs_mongodb():
    with pytest.raises(ValueError, match="needs MongoDB"):
        _validate_config({"broker": {"port": 8000}, "organizations": [], "admin": {"store": "mongodb"}})
    _validate_config({"broker": {"port": 8000}, "organizations": [], "mongodb": {"enabled": True}, "admin": {"store": "mongodb", "poll_seconds": 2}})
    _validate_config({"broker": {"port": 8000}, "organizations": [], "admin": {"store": "file"}})


def test_the_organizations_are_stored_as_text_so_key_names_do_not_matter():
    organizations = [{"name": "A", "charger_auth": {"credentials": {"CP.1": {"password_hash": "x"}, "$odd": {"password_hash": "y"}}}}]
    assert encode(organizations) == encode(organizations)
    import json

    assert json.loads(encode(organizations)) == organizations


def test_a_date_written_without_quotes_in_a_file_is_stored_as_text():
    import datetime
    import json

    assert json.loads(encode([{"name": "A", "tags": [{"expiry_date": datetime.date(2027, 1, 31)}]}]))[0]["tags"][0]["expiry_date"] == "2027-01-31"


pytestmark_mongo = pytest.mark.skipif(not URI, reason="set OCPP_TEST_MONGO_URI to run against a real MongoDB")


@pytest_asyncio.fixture
async def service():
    svc = MongoDBService(URI, f"ocpp_test_{uuid.uuid4().hex[:8]}")
    await svc.connect()
    yield svc
    await svc.client.drop_database(svc.database_name)
    await svc.disconnect()


async def seeded(service, organizations):
    from ocpp_broker.mongo_config_store import new_revision

    await service.seed_organizations(encode(organizations), new_revision())
    return MongoConfigStore(service, keep_backups=3)


# --------------------------------------------------------------------------- the store
@pytestmark_mongo
@pytest.mark.asyncio
async def test_nothing_is_stored_until_somebody_stores_it(service):
    store = MongoConfigStore(service)
    with pytest.raises(ConfigError, match="No organizations are stored"):
        await store.snapshot()


@pytestmark_mongo
@pytest.mark.asyncio
async def test_the_first_seed_wins_and_the_second_changes_nothing(service):
    assert await service.seed_organizations(encode([{"name": "First"}]), "r1") is True
    assert await service.seed_organizations(encode([{"name": "Second"}]), "r2") is False
    snapshot = await MongoConfigStore(service).snapshot()
    assert snapshot.revision == "r1" and [o["name"] for o in snapshot.organizations] == ["First"]
    assert snapshot.modified.tzinfo is not None and "config_organizations" in snapshot.path


@pytestmark_mongo
@pytest.mark.asyncio
async def test_a_change_is_stored_with_a_new_revision_and_read_back_exactly(service):
    store = await seeded(service, [{"name": "Fleet", "connect_to_backend": False}])
    snapshot = await store.snapshot()
    new = [
        *snapshot.organizations,
        {"name": "Newco", "connect_to_backend": False, "charger_auth": {"credentials": {"CP.1": {"password_hash": "h"}}}, "tags": [{"id_tag": "T", "status": "Accepted"}]},
    ]
    applied = await store.apply(await store.plan(snapshot.revision, new), "alice")
    assert applied.revision != snapshot.revision
    again = await store.snapshot()
    assert again.revision == applied.revision and again.organizations == new, "a dotted charger id survives"
    assert [o["name"] for o in applied.runtime["organizations"]] == ["Fleet", "Newco"]
    stored = await service.get_organizations()
    assert stored["updated_by"] == "alice"


@pytestmark_mongo
@pytest.mark.asyncio
async def test_two_changes_made_from_the_same_revision_cannot_both_be_applied(service):
    store = await seeded(service, [{"name": "Fleet", "connect_to_backend": False}])
    snapshot = await store.snapshot()
    first = await store.plan(snapshot.revision, [{"name": "One", "connect_to_backend": False}])
    second = await store.plan(snapshot.revision, [{"name": "Two", "connect_to_backend": False}])
    await store.apply(first)
    with pytest.raises(Conflict):
        await store.apply(second)
    assert [o["name"] for o in (await store.snapshot()).organizations] == ["One"]


@pytestmark_mongo
@pytest.mark.asyncio
async def test_changes_made_at_the_same_moment_let_exactly_one_through(service):
    store = await seeded(service, [{"name": "Fleet", "connect_to_backend": False}])
    snapshot = await store.snapshot()
    plans = [await store.plan(snapshot.revision, [{"name": f"O{n}", "connect_to_backend": False}]) for n in range(5)]
    outcomes = await asyncio.gather(*(store.apply(p) for p in plans), return_exceptions=True)
    assert sum(1 for o in outcomes if not isinstance(o, Exception)) == 1
    assert all(isinstance(o, Conflict) for o in outcomes if isinstance(o, Exception))


@pytestmark_mongo
@pytest.mark.asyncio
async def test_a_plan_made_from_an_old_revision_says_so(service):
    store = await seeded(service, [{"name": "Fleet", "connect_to_backend": False}])
    plan = await store.plan("not-the-revision", [{"name": "Fleet", "connect_to_backend": False}])
    assert not plan.ok and plan.base_revision != plan.current_revision and "have changed" in plan.errors[0]


@pytestmark_mongo
@pytest.mark.asyncio
async def test_a_plan_has_the_loaders_errors_and_only_the_warnings_a_change_adds(service):
    store = await seeded(service, [{"name": "Fleet", "connect_to_backend": False}])
    snapshot = await store.snapshot()
    bad = await store.plan(snapshot.revision, [{"name": "X", "backends": [{"local": True}, {"local": True}]}])
    assert not bad.ok and "only one backend can be local" in bad.errors[0]
    with pytest.raises(ConfigError):
        await store.apply(bad)
    unchanged = await store.plan(snapshot.revision, snapshot.organizations)
    assert unchanged.warnings == []
    added = await store.plan(snapshot.revision, [*snapshot.organizations, {"name": "Third", "connect_to_backend": False}])
    assert len(added.warnings) == 1 and "Third" in added.warnings[0]


@pytestmark_mongo
@pytest.mark.asyncio
async def test_earlier_versions_are_kept_but_only_the_latest_few(service):
    store = await seeded(service, [{"name": "Start", "connect_to_backend": False}])
    for number in range(6):
        snapshot = await store.snapshot()
        await store.apply(await store.plan(snapshot.revision, [{"name": f"V{number}", "connect_to_backend": False}]))
    history = await service.db[service.ORGANIZATIONS_HISTORY].find({}).sort("was_current_until", 1).to_list(length=None)
    assert len(history) == 3, "keep_backups is 3"
    names = [[o["name"] for o in __import__("json").loads(h["organizations_json"])] for h in history]
    assert names == [["V2"], ["V3"], ["V4"]], "the three before the current V5"


@pytestmark_mongo
@pytest.mark.asyncio
async def test_a_database_that_fails_is_reported_as_such(service, monkeypatch):
    store = await seeded(service, [{"name": "Fleet", "connect_to_backend": False}])

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "get_organizations", broken)
    with pytest.raises(NotWritable, match="did not answer"):
        await store.snapshot()
    monkeypatch.undo()
    snapshot = await store.snapshot()
    plan = await store.plan(snapshot.revision, snapshot.organizations)
    monkeypatch.setattr(service, "replace_organizations", broken)
    with pytest.raises(NotWritable, match="they were not changed"):
        await store.apply(plan)


@pytestmark_mongo
@pytest.mark.asyncio
async def test_it_is_not_writable_while_mongodb_is_not_connected(service):
    store = MongoConfigStore(service)
    assert store.writable() == (True, None)
    service._connected = False
    writable, reason = store.writable()
    assert writable is False and "not connected" in reason


# --------------------------------------------------------------------------- instances sharing the organizations
def instance(service, file_organizations, tmp_path, name, poll=1):
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump({"organizations": file_organizations}), encoding="utf-8")
    broker = OcppBroker()
    broker._cfg_path = str(path)
    broker.config_data = {"organizations": [], "admin": {"enabled": True, "store": "mongodb", "poll_seconds": poll}, "mongodb": {"enabled": True}}
    broker.mongodb_service = service
    return broker


@pytestmark_mongo
@pytest.mark.asyncio
async def test_the_first_instance_stores_its_file_and_the_second_adopts_what_is_stored(service, tmp_path):
    first = instance(service, [{"name": "FromFirst", "connect_to_backend": False}], tmp_path, "first")
    second = instance(service, [{"name": "FromSecond", "connect_to_backend": False}], tmp_path, "second")
    assert await first.sync_organizations() is True
    assert [o["name"] for o in first.config_data["organizations"]] == ["FromFirst"]
    assert await second.sync_organizations() is True
    assert [o["name"] for o in second.config_data["organizations"]] == ["FromFirst"], "the file of the second instance is no longer read"
    assert second.organizations_revision == first.organizations_revision
    assert await second.sync_organizations() is False, "nothing new"


@pytestmark_mongo
@pytest.mark.asyncio
async def test_a_file_that_is_missing_seeds_an_empty_list(service, tmp_path):
    broker = instance(service, [], tmp_path, "gone")
    os.remove(broker._cfg_path)
    await broker.sync_organizations()
    assert broker.config_data["organizations"] == []
    assert (await MongoConfigStore(service).snapshot()).organizations == []


@pytestmark_mongo
@pytest.mark.asyncio
async def test_a_change_made_through_one_instance_reaches_the_other_by_itself(service, tmp_path):
    first = instance(service, [{"name": "Fleet", "connect_to_backend": False}], tmp_path, "first")
    second = instance(service, [], tmp_path, "second")
    await first._start_organization_sync()
    await second._start_organization_sync()
    try:
        store = MongoConfigStore(service)
        snapshot = await store.snapshot()
        await store.apply(await store.plan(snapshot.revision, [*snapshot.organizations, {"name": "Added", "connect_to_backend": False}]))
        await wait_for(lambda: [o["name"] for o in second.config_data["organizations"]] == ["Fleet", "Added"], timeout=6)
        await wait_for(lambda: [o["name"] for o in first.config_data["organizations"]] == ["Fleet", "Added"], timeout=6)
    finally:
        first.stop_mongodb_retry()
        second.stop_mongodb_retry()


@pytestmark_mongo
@pytest.mark.asyncio
async def test_organizations_that_cannot_be_used_are_not_adopted_and_are_reported_once(service, tmp_path, caplog):
    broker = instance(service, [{"name": "Fleet", "connect_to_backend": False}], tmp_path, "one")
    await broker.sync_organizations()
    bad = encode([{"name": "Bad", "backends": [{"local": True}, {"local": True}]}])
    await service.db[service.ORGANIZATIONS].update_one({"_id": "organizations"}, {"$set": {"revision": "hand-edited", "organizations_json": bad}})
    with caplog.at_level("ERROR", logger="ocpp_broker.broker"):
        assert await broker.sync_organizations() is False
        assert await broker.sync_organizations() is False
    assert [o["name"] for o in broker.config_data["organizations"]] == ["Fleet"]
    assert caplog.text.count("cannot be used") == 1
    fixed = encode([{"name": "Good", "connect_to_backend": False}])
    await service.db[service.ORGANIZATIONS].update_one({"_id": "organizations"}, {"$set": {"revision": "fixed", "organizations_json": fixed}})
    assert await broker.sync_organizations() is True
    assert [o["name"] for o in broker.config_data["organizations"]] == ["Good"]


@pytestmark_mongo
@pytest.mark.asyncio
async def test_a_revision_that_is_bad_again_after_being_fixed_is_reported_again(service, tmp_path, caplog):
    broker = instance(service, [{"name": "Fleet", "connect_to_backend": False}], tmp_path, "one")
    await broker.sync_organizations()
    bad = encode([{"name": "Bad", "backends": [{"local": True}, {"local": True}]}])
    good = encode([{"name": "Good", "connect_to_backend": False}])
    collection = service.db[service.ORGANIZATIONS]
    with caplog.at_level("ERROR", logger="ocpp_broker.broker"):
        await collection.update_one({"_id": "organizations"}, {"$set": {"revision": "x", "organizations_json": bad}})
        await broker.sync_organizations()
        await collection.update_one({"_id": "organizations"}, {"$set": {"revision": "y", "organizations_json": good}})
        assert await broker.sync_organizations() is True
        await collection.update_one({"_id": "organizations"}, {"$set": {"revision": "x", "organizations_json": bad}})
        await broker.sync_organizations()
    assert caplog.text.count("cannot be used") == 2


@pytestmark_mongo
@pytest.mark.asyncio
async def test_what_is_stored_must_be_a_list_of_mappings(service):
    for stored in ('"just text"', "[1, 2]", "{}", "not json"):
        await service.db[service.ORGANIZATIONS].delete_many({})
        await service.db[service.ORGANIZATIONS].insert_one({"_id": "organizations", "revision": "r", "organizations_json": stored, "updated_at": __import__("datetime").datetime.now()})
        with pytest.raises(ConfigError, match="cannot be read|not a list of mappings"):
            await MongoConfigStore(service).snapshot()


@pytestmark_mongo
@pytest.mark.asyncio
async def test_the_watcher_ends_when_the_broker_stops(service, tmp_path):
    broker = instance(service, [{"name": "Fleet", "connect_to_backend": False}], tmp_path, "one")
    await broker._start_organization_sync()
    watcher = broker._org_watch
    assert watcher is not None and not watcher.done()
    broker.stop_mongodb_retry()
    await wait_for(lambda: watcher.done(), timeout=3)


@pytestmark_mongo
@pytest.mark.asyncio
async def test_without_mongodb_connected_an_instance_keeps_what_it_has(tmp_path):
    broker = OcppBroker()
    broker.config_data = {"organizations": [{"name": "Kept"}], "admin": {"store": "mongodb"}}
    broker.mongodb_service = None
    assert await broker.sync_organizations() is False
    assert [o["name"] for o in broker.config_data["organizations"]] == ["Kept"]


# --------------------------------------------------------------------------- through the API
def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


@pytest.fixture
def served(service, tmp_path, run_server, monkeypatch):
    async def start(file_organizations=None):
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump({"organizations": file_organizations or [{"name": "Fleet", "connect_to_backend": False}]}), encoding="utf-8")
        config = {
            "organizations": [{"name": "Fleet", "connect_to_backend": False}],
            "admin": {"enabled": True, "store": "mongodb", "keep_backups": 3},
            "mongodb": {"enabled": True},
        }
        port = await run_server(config)
        monkeypatch.setattr(server.broker, "mongodb_service", service, raising=False)
        monkeypatch.setattr(server.broker, "_cfg_path", str(path), raising=False)
        monkeypatch.setattr(server.broker, "admin_store", None, raising=False)
        monkeypatch.setattr(server.broker, "audit", None, raising=False)
        monkeypatch.setattr(server.broker, "organizations_revision", None, raising=False)
        await server.broker.sync_organizations()
        return port

    return start


@pytestmark_mongo
@pytest.mark.asyncio
async def test_the_api_reads_and_changes_the_organizations_in_mongodb_and_not_the_file(served, tmp_path):
    port = await served()
    file_before = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    async with rest(port) as client:
        config = (await client.get("/api/admin/config")).json()
        assert config["writable"] is True and "config_organizations" in config["path"]
        assert [o["name"] for o in config["organizations"]] == ["Fleet"]
        response = await client.post(
            "/api/admin/config/apply",
            json={"revision": config["revision"], "changes": [change(name="Newco", connect_to_backend=False, credentials=[{"charger_id": "NC.1", "password": "a-long-key-0123456789"}])]},
        )
        assert response.status_code == 200, response.text
        applied = response.json()
        assert applied["revision"] != config["revision"] and "history entry" in applied["backup"]
        assert "a-long-key-0123456789" not in response.text
        again = (await client.get("/api/admin/config")).json()
        assert again["revision"] == applied["revision"] and [o["name"] for o in again["organizations"]] == ["Fleet", "Newco"]
    assert (tmp_path / "config.yaml").read_text(encoding="utf-8") == file_before, "the file is not touched"
    assert {o["name"] for o in server.broker.config_data["organizations"]} == {"Fleet", "Newco"}
    assert server.broker.organizations_revision == applied["revision"]


@pytestmark_mongo
@pytest.mark.asyncio
async def test_a_change_from_a_stale_page_is_refused_here_too(served):
    port = await served()
    async with rest(port) as client:
        revision = (await client.get("/api/admin/config")).json()["revision"]
        ok = await client.post("/api/admin/config/apply", json={"revision": revision, "changes": [change(name="A", connect_to_backend=False)]})
        assert ok.status_code == 200
        stale = await client.post("/api/admin/config/apply", json={"revision": revision, "changes": [change(name="B", connect_to_backend=False)]})
        assert stale.status_code == 409 and "have changed" in stale.json()["detail"]


@pytestmark_mongo
@pytest.mark.asyncio
async def test_the_audit_log_is_shared_through_mongodb(served, service):
    port = await served()
    async with rest(port) as client:
        revision = (await client.get("/api/admin/config")).json()["revision"]
        await client.post("/api/admin/config/apply", json={"revision": revision, "changes": [change(name="A", connect_to_backend=False)]}, headers={"X-API-Key": AUTH_HEADERS["X-API-Key"]})
        assert await server.broker.writes.flush(10)
        # An entry another instance wrote
        from datetime import datetime, timezone

        await service.save_admin_audit(
            {"time": datetime(2020, 1, 1, tzinfo=timezone.utc), "action": "config.apply", "outcome": "applied", "source": "10.9.9.9", "key_label": "bob", "organizations": ["Elsewhere"], "summary": ["Elsewhere: x"], "detail": None, "revision": "r"}
        )
        await service.save_admin_audit({"not": "an entry"})
        page = (await client.get("/api/admin/audit")).json()
    assert [e["key_label"] for e in page["entries"]] == ["api-key", "bob"], "newest first; the line that is not an entry is skipped"
    assert "admin_audit" in page["persisted_to"]


@pytestmark_mongo
@pytest.mark.asyncio
async def test_the_api_says_so_when_mongodb_is_not_there(run_server, monkeypatch, tmp_path):
    port = await run_server({"organizations": [], "admin": {"enabled": True, "store": "mongodb"}})
    monkeypatch.setattr(server.broker, "mongodb_service", None, raising=False)
    monkeypatch.setattr(server.broker, "admin_store", None, raising=False)
    async with rest(port) as client:
        response = await client.get("/api/admin/config")
        assert response.status_code == 503 and "MongoDB is not connected" in response.json()["detail"]
