"""
History against a real MongoDB: the indexes and retention, paging by cursor, and the endpoints reading back what
a charger and a command really wrote.

Skipped unless OCPP_TEST_MONGO_URI points at a MongoDB (see test_transaction_store_mongo.py). Each test uses
its own throw-away database.
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from ocpp_broker import server
from ocpp_broker.history import HistoryConfig, decode_cursor, encode_cursor
from ocpp_broker.mongodb_service import MongoDBService

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedCharger

URI = os.environ.get("OCPP_TEST_MONGO_URI")
pytestmark = pytest.mark.skipif(not URI, reason="set OCPP_TEST_MONGO_URI to run against a real MongoDB")

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def service():
    svc = MongoDBService(URI, f"ocpp_test_{uuid.uuid4().hex[:8]}")
    await svc.connect()
    yield svc
    await svc.client.drop_database(svc.database_name)
    await svc.disconnect()


@pytest.fixture
def tags():
    server.broker.tag_manager = None
    yield
    server.broker.tag_manager = None


async def indexes(service, collection):
    info = await service.db[collection].index_information()
    return {tuple(map(tuple, spec["key"])): spec for spec in info.values()}


# --------------------------------------------------------------------------- indexes and retention
@pytest.mark.asyncio
async def test_the_indexes_are_created_once_and_a_retention_becomes_a_ttl_index(service):
    retention = {"messages": 30, "commands": 365, "statuses": None}
    await service.ensure_history_indexes(retention)
    await service.ensure_history_indexes(retention)
    messages = await indexes(service, "ocpp_messages")
    assert messages[(("timestamp", 1),)]["expireAfterSeconds"] == 30 * 86400
    assert (("org_name", 1), ("charger_id", 1), ("timestamp", -1)) in messages
    assert len(messages) == 3, "_id, the query index and the TTL index, still after a second call"
    assert (await indexes(service, "commands"))[(("sent_at", 1),)]["expireAfterSeconds"] == 365 * 86400
    statuses = await indexes(service, "charger_statuses")
    assert (("timestamp", 1),) not in statuses, "no limit, no TTL"
    assert (("org_name", 1), ("charger_id", 1), ("timestamp", -1)) in statuses
    assert (("org_name", 1), ("charger_id", 1), ("transaction_id", 1)) in await indexes(service, "meter_values")
    assert (("org_name", 1), ("charger_id", 1), ("transaction_id", 1)) in await indexes(service, "transactions")


@pytest.mark.asyncio
async def test_a_changed_retention_is_applied_to_the_existing_ttl_index(service):
    await service.ensure_history_indexes({"messages": 30})
    await service.ensure_history_indexes({"messages": 7})
    messages = await indexes(service, "ocpp_messages")
    assert messages[(("timestamp", 1),)]["expireAfterSeconds"] == 7 * 86400
    assert len(messages) == 3


# --------------------------------------------------------------------------- paging
@pytest.mark.asyncio
async def test_pages_follow_one_another_without_gaps_or_repeats_even_with_equal_times(service):
    stamps = [NOW - timedelta(minutes=minute // 4) for minute in range(25)]  # four records share each time
    for number, stamp in enumerate(stamps):
        await service.save_command("O", "CP1", message_id=f"m{number}", action="Reset", status="success", sent_at=stamp)
    await service.save_command("O", "OTHER", message_id="x", action="Reset", status="success", sent_at=NOW)
    seen, cursor = [], None
    while True:
        rows, more = await service.history_page("commands", {"org_name": "O", "charger_id": "CP1"}, "sent_at", 7, cursor)
        seen += [row["message_id"] for row in rows]
        if more is None:
            break
        assert len(rows) == 7
        cursor = decode_cursor(encode_cursor(*more))  # as it travels through the API
    assert sorted(seen) == sorted(f"m{n}" for n in range(25)), "every record once"
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_a_page_that_ends_exactly_on_the_last_record_has_no_next_cursor(service):
    for number in range(6):
        await service.save_command("O", "CP1", message_id=f"m{number}", action="Reset", status="success", sent_at=NOW - timedelta(seconds=number))
    rows, more = await service.history_page("commands", {}, "sent_at", 6)
    assert len(rows) == 6 and more is None
    rows, more = await service.history_page("commands", {}, "sent_at", 5)
    assert len(rows) == 5 and more is not None


# --------------------------------------------------------------------------- the whole way round
START = {"connectorId": 1, "idTag": "TAG1", "meterStart": 1000, "timestamp": "2026-10-04T10:00:00Z"}


def org():
    return {"name": "Home", "connect_to_backend": False, "tags": [{"id_tag": "TAG1", "status": "Accepted"}]}


@pytest.mark.asyncio
async def test_what_a_charger_and_a_command_wrote_is_read_back_through_the_api(run_server, tags, service, monkeypatch):
    monkeypatch.setattr(server.broker, "mongodb_service", service, raising=False)
    port = await run_server({"organizations": [org()]})
    monkeypatch.setattr(server.broker, "history", HistoryConfig(messages=True))
    await service.ensure_history_indexes({})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async def ask(action, payload):
            await charger.call(action, payload)
            return await charger.recv()

        await ask("BootNotification", BOOT_PAYLOAD)
        await ask("StatusNotification", {"connectorId": 1, "status": "Preparing", "errorCode": "NoError", "timestamp": "2026-10-04T09:59:00Z"})
        await ask("Authorize", {"idTag": "TAG1"})
        transaction_id = (await ask("StartTransaction", START))[2]["transactionId"]
        await ask("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError", "timestamp": "2026-10-04T10:00:05Z"})
        for minute, energy, power in ((5, 1500, 7000), (10, 2100, 7100)):
            await ask(
                "MeterValues",
                {
                    "connectorId": 1,
                    "transactionId": transaction_id,
                    "meterValue": [
                        {
                            "timestamp": f"2026-10-04T10:{minute:02d}:00Z",
                            "sampledValue": [
                                {"value": str(energy), "measurand": "Energy.Active.Import.Register", "unit": "Wh"},
                                {"value": str(power), "measurand": "Power.Active.Import", "unit": "W"},
                            ],
                        }
                    ],
                },
            )
        await ask("StopTransaction", {"transactionId": transaction_id, "meterStop": 2600, "timestamp": "2026-10-04T10:20:00Z", "reason": "Local", "idTag": "TAG1"})
        await ask("Heartbeat", {})
        second = (await ask("StartTransaction", {**START, "meterStart": 2600, "timestamp": "2026-10-04T11:00:00Z"}))[2]["transactionId"]

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10) as client:
            command = asyncio.ensure_future(
                client.post(
                    "/api/ocpp/organizations/Home/chargers/CP1/commands",
                    json={"action": "ChangeConfiguration", "payload": {"key": "AuthorizationKey", "value": "s3cret"}, "timeout": 5},
                )
            )
            frame = await charger.recv()
            await charger.send([3, frame[1], {"status": "Accepted"}])
            assert (await command).status_code == 200
            assert await server.broker.writes.flush(10)

            info = (await client.get("/api/history/info")).json()
            assert info["available"] is True and info["messages_enabled"] is True
            assert info["counts"]["transactions"] == 2 and info["counts"]["commands"] == 1

            page = (await client.get("/api/history/transactions", params={"org": "Home"})).json()
            assert page["available"] is True and page["next_cursor"] is None
            assert [t["transaction_id"] for t in page["items"]] == [second, transaction_id], "newest first"
            done = page["items"][1]
            assert done["energy_wh"] == 1600 and done["open"] is False and done["stop_reason"] == "Local"
            assert done["id_tag"] == "TAG1" and done["connector_id"] == 1
            assert done["started_at"].startswith("2026-10-04T10:00:00") and done["stopped_at"].startswith("2026-10-04T10:20:00")
            assert page["items"][0]["open"] is True and page["items"][0]["energy_wh"] is None

            assert [t["transaction_id"] for t in (await client.get("/api/history/transactions", params={"state": "open"})).json()["items"]] == [second]
            assert [t["transaction_id"] for t in (await client.get("/api/history/transactions", params={"state": "closed"})).json()["items"]] == [transaction_id]
            assert (await client.get("/api/history/transactions", params={"charger_id": "nobody"})).json()["items"] == []
            window = (await client.get("/api/history/transactions", params={"since": "2026-10-04T10:30:00Z"})).json()["items"]
            assert [t["transaction_id"] for t in window] == [second]

            first_page = (await client.get("/api/history/transactions", params={"limit": 1})).json()
            assert len(first_page["items"]) == 1 and first_page["next_cursor"]
            next_page = (await client.get("/api/history/transactions", params={"limit": 1, "cursor": first_page["next_cursor"]})).json()
            assert [t["transaction_id"] for t in next_page["items"]] == [transaction_id] and next_page["next_cursor"] is None

            detail = (await client.get(f"/api/history/transactions/Home/CP1/{transaction_id}")).json()
            assert detail["transaction"]["energy_wh"] == 1600 and detail["readings_truncated"] is False
            power = [r for r in detail["readings"] if r["measurand"] == "Power.Active.Import"]
            assert [(r["value"], r["unit"]) for r in power] == [(7000.0, "W"), (7100.0, "W")]
            assert [r["value"] for r in detail["readings"] if r["measurand"] == "Energy.Active.Import.Register"] == [1500.0, 2100.0], "oldest first"
            assert power[0]["timestamp"].startswith("2026-10-04T10:05:00")
            assert (await client.get("/api/history/transactions/Home/CP1/999999")).status_code == 404

            readings = (await client.get("/api/history/meter-values", params={"transaction_id": transaction_id, "measurand": "Power.Active.Import"})).json()
            assert [r["value"] for r in readings["items"]] == [7100.0, 7000.0], "newest message first"

            statuses = (await client.get("/api/history/statuses", params={"charger_id": "CP1"})).json()
            assert [s["status"] for s in statuses["items"]] == ["Charging", "Preparing"]
            assert statuses["items"][1]["timestamp"].startswith("2026-10-04T09:59:00")
            only = (await client.get("/api/history/statuses", params={"status": "Preparing"})).json()["items"]
            assert [s["status"] for s in only] == ["Preparing"]

            commands = (await client.get("/api/history/commands", params={"org": "Home"})).json()["items"]
            assert len(commands) == 1 and commands[0]["action"] == "ChangeConfiguration" and commands[0]["status"] == "success"
            assert commands[0]["response"] == {"status": "Accepted"} and commands[0]["duration_ms"] >= 0
            assert "s3cret" not in str(commands), "a secret is not kept"
            assert (await client.get("/api/history/commands", params={"status": "timeout"})).json()["items"] == []

            messages = (await client.get("/api/history/messages", params={"charger_id": "CP1", "limit": 500})).json()["items"]
            assert not any(m["action"] == "Heartbeat" for m in messages), "heartbeats are left out"
            starts = [m for m in messages if m["action"] == "StartTransaction"]
            assert sorted((m["direction"], m["type"]) for m in starts) == [("in", "call")] * 2 + [("out", "result")] * 2
            by_id = {}
            for m in messages:
                by_id.setdefault(m["message_id"], []).append(m)
            assert all(len({m["action"] for m in group}) == 1 for group in by_id.values()), "a request and its reply carry the same action"
            auth = (await client.get("/api/history/messages", params={"action": "Authorize", "direction": "out"})).json()["items"]
            assert len(auth) == 1 and auth[0]["type"] == "result" and auth[0]["payload"]["idTagInfo"]["status"] == "Accepted"
            assert all(m["timestamp"].endswith("Z") or "+" in m["timestamp"] for m in messages), "times say they are UTC"
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_stop_with_no_start_on_record_is_still_listed(service):
    await service.save_transaction(org_name="O", charger_id="CP1", transaction_id=9, transaction_type="stop", meter_stop=50, timestamp=NOW)
    rows, _ = await service.history_page("transactions", {"org_name": "O"}, "timestamp", 10)
    assert len(rows) == 1 and rows[0]["timestamp"] is not None, "a partial document has a time to sort on"


@pytest.mark.asyncio
async def test_since_includes_its_moment_and_until_does_not(run_server, tags, service, monkeypatch):
    monkeypatch.setattr(server.broker, "mongodb_service", service, raising=False)
    port = await run_server({"organizations": [org()]})
    for number, offset in enumerate((-1, 0, 1)):
        await service.save_command("Home", "CP1", message_id=f"m{number}", action="Reset", status="success", sent_at=NOW + timedelta(seconds=offset))
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10) as client:

        async def ids(**params):
            items = (await client.get("/api/history/commands", params=params)).json()["items"]
            return [item["message_id"] for item in items]

        assert await ids(since=NOW.isoformat()) == ["m2", "m1"]
        assert await ids(until=NOW.isoformat()) == ["m0"]
        assert await ids(since=NOW.isoformat(), until=(NOW + timedelta(seconds=1)).isoformat()) == ["m1"]
        assert await ids(since="2026-10-04T12:00:00") == ["m2", "m1"], "no zone means UTC"


@pytest.mark.asyncio
async def test_a_transactions_readings_are_in_the_order_they_were_taken_not_received(run_server, tags, service, monkeypatch):
    monkeypatch.setattr(server.broker, "mongodb_service", service, raising=False)
    port = await run_server({"organizations": [org()]})
    await service.save_transaction(org_name="Home", charger_id="CP1", transaction_id=3, connector_id=1, id_tag="T", meter_start=0, timestamp=NOW)
    for value, taken in (("200", "2026-10-04T12:10:00Z"), ("100", "2026-10-04T12:05:00Z")):  # a late one arrives second
        await service.save_meter_values(
            org_name="Home", charger_id="CP1", connector_id=1, transaction_id=3,
            meter_value=[{"timestamp": taken, "sampled_value": [{"value": value}]}],
        )
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10) as client:
        detail = (await client.get("/api/history/transactions/Home/CP1/3")).json()
    assert [r["value"] for r in detail["readings"]] == [100.0, 200.0]


@pytest.mark.asyncio
async def test_a_transaction_with_more_readings_than_are_read_says_so(run_server, tags, service, monkeypatch):
    from ocpp_broker import history_api

    monkeypatch.setattr(history_api, "DETAIL_READINGS", 3)
    monkeypatch.setattr(server.broker, "mongodb_service", service, raising=False)
    port = await run_server({"organizations": [org()]})
    await service.save_transaction(org_name="Home", charger_id="CP1", transaction_id=3, connector_id=1, id_tag="T", meter_start=0, timestamp=NOW)
    for number in range(5):
        await service.save_meter_values(
            org_name="Home", charger_id="CP1", connector_id=1, transaction_id=3,
            meter_value=[{"timestamp": f"2026-10-04T12:0{number}:00Z", "sampled_value": [{"value": str(number)}]}],
            timestamp=NOW + timedelta(minutes=number),
        )
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10) as client:
        detail = (await client.get("/api/history/transactions/Home/CP1/3")).json()
        assert detail["readings_truncated"] is True and [r["value"] for r in detail["readings"]] == [0.0, 1.0, 2.0], "the oldest are kept"
        await service.db["meter_values"].delete_many({"meter_value.0.sampled_value.0.value": {"$in": ["3", "4"]}})
        again = (await client.get("/api/history/transactions/Home/CP1/3")).json()
        assert again["readings_truncated"] is False, "exactly as many as the limit is not truncated"


@pytest.mark.asyncio
async def test_an_error_other_than_a_changed_retention_is_not_swallowed(service, monkeypatch):
    from pymongo.errors import OperationFailure

    class Refusing:
        async def create_index(self, *args, **kwargs):
            if "expireAfterSeconds" in kwargs:  # the ordinary indexes go through; the retention one is refused
                raise OperationFailure("not allowed", code=13)

    monkeypatch.setattr(service, "_get_collection", lambda name: Refusing())
    with pytest.raises(OperationFailure) as raised:
        await service.ensure_history_indexes({"messages": 30})
    assert raised.value.code == 13, "the original error, not one from trying to repair it"
