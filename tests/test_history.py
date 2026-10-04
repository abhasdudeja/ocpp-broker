"""
History (history.py, history_api.py): what is recorded, how replies are labelled, cursors and the readers'
behaviour without MongoDB. The queries themselves run against a real MongoDB in test_history_mongo.py.
"""

import json
from datetime import datetime, timezone

import httpx
import pytest

from ocpp_broker import server
from ocpp_broker.config import _validate_config
from ocpp_broker.history import (
    BadCursor,
    HistoryConfig,
    MessageLabels,
    decode_cursor,
    encode_cursor,
    flatten_meter_values,
    history_config,
    message_document,
    transaction_row,
    validate_history_config,
)

from .fakes import BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger
from .test_background_writes import START, SlowMongo, org, rest, stored, tags  # noqa: F401  (tags is a fixture)

ON = HistoryConfig(messages=True)


def frame(*items):
    return json.dumps(list(items))


# --------------------------------------------------------------------------- configuration
def test_defaults_record_no_messages_and_keep_messages_a_month_and_commands_a_year():
    config = history_config({})
    assert config.messages is False and config.heartbeats is False
    assert config.retention_days == {"messages": 30, "commands": 365}


def test_a_retention_can_be_set_and_a_default_removed_with_null():
    config = history_config({"history": {"messages": True, "retention_days": {"messages": None, "meter_values": 90}}})
    assert config.messages is True
    assert config.retention_days == {"messages": None, "commands": 365, "meter_values": 90}


@pytest.mark.parametrize(
    "block, message",
    [
        ({"history": "yes"}, "must be a mapping"),
        ({"history": {"messages": "yes"}}, "messages must be true or false"),
        ({"history": {"heartbeats": 1}}, "heartbeats must be true or false"),
        ({"history": {"retention_days": [30]}}, "retention_days must be a mapping"),
        ({"history": {"retention_days": {"nothing": 3}}}, "not known"),
        ({"history": {"retention_days": {"messages": 0}}}, "1 or more"),
        ({"history": {"retention_days": {"messages": "30"}}}, "whole number"),
        ({"history": {"retention_days": {"messages": True}}}, "whole number"),
    ],
)
def test_a_history_block_that_cannot_be_used_stops_the_broker_at_startup(block, message):
    with pytest.raises(ValueError, match=message):
        validate_history_config(block)
    with pytest.raises(ValueError, match=message):
        _validate_config({"broker": {"port": 8000}, "organizations": [], "mongodb": block})


def test_a_missing_or_valid_block_is_accepted():
    validate_history_config({})
    validate_history_config({"history": {"messages": True, "retention_days": {"messages": 7, "commands": None}}})


# --------------------------------------------------------------------------- the message log
def test_a_call_is_recorded_with_its_payload_and_direction():
    doc = message_document("O", "CP1", "in", frame(2, "a1", "StatusNotification", {"connectorId": 1}), MessageLabels(), ON)
    assert doc["type"] == "call" and doc["direction"] == "in" and doc["action"] == "StatusNotification"
    assert doc["message_id"] == "a1" and doc["payload"] == {"connectorId": 1}
    assert doc["org_name"] == "O" and doc["charger_id"] == "CP1" and isinstance(doc["timestamp"], datetime)


def test_the_reply_to_a_charger_call_is_labelled_with_the_action_it_answers():
    labels = MessageLabels()
    message_document("O", "CP1", "in", frame(2, "a1", "Authorize", {"idTag": "T"}), labels, ON)
    reply = message_document("O", "CP1", "out", frame(3, "a1", {"idTagInfo": {"status": "Accepted"}}), labels, ON)
    assert reply["type"] == "result" and reply["action"] == "Authorize" and reply["direction"] == "out"
    assert reply["payload"] == {"idTagInfo": {"status": "Accepted"}}


def test_the_charger_reply_to_a_command_is_labelled_too_and_an_error_keeps_its_code():
    labels = MessageLabels()
    message_document("O", "CP1", "out", frame(2, "c1", "Reset", {"type": "Soft"}), labels, ON)
    message_document("O", "CP1", "out", frame(2, "c2", "ClearCache", {}), labels, ON)
    result = message_document("O", "CP1", "in", frame(3, "c1", {"status": "Accepted"}), labels, ON)
    error = message_document("O", "CP1", "in", frame(4, "c2", "NotSupported", "no", {"x": 1}), labels, ON)
    assert (result["type"], result["action"]) == ("result", "Reset")
    assert (error["type"], error["action"]) == ("error", "ClearCache")
    assert error["error"] == {"code": "NotSupported", "description": "no"} and error["payload"] == {"x": 1}


def test_a_charger_call_and_a_command_with_the_same_id_are_not_confused():
    labels = MessageLabels()
    message_document("O", "CP1", "in", frame(2, "same", "Authorize", {}), labels, ON)
    message_document("O", "CP1", "out", frame(2, "same", "Reset", {"type": "Hard"}), labels, ON)
    from_charger = message_document("O", "CP1", "in", frame(3, "same", {"status": "Accepted"}), labels, ON)
    to_charger = message_document("O", "CP1", "out", frame(3, "same", {"currentTime": "t"}), labels, ON)
    assert from_charger["action"] == "Reset" and to_charger["action"] == "Authorize"


def test_a_reply_whose_request_was_never_seen_has_no_action():
    doc = message_document("O", "CP1", "in", frame(3, "lost", {"status": "Accepted"}), MessageLabels(), ON)
    assert doc["type"] == "result" and doc["action"] is None


def test_a_reply_is_labelled_once_and_the_label_is_then_forgotten():
    labels = MessageLabels()
    message_document("O", "CP1", "in", frame(2, "a1", "Authorize", {}), labels, ON)
    assert message_document("O", "CP1", "out", frame(3, "a1", {}), labels, ON)["action"] == "Authorize"
    assert message_document("O", "CP1", "out", frame(3, "a1", {}), labels, ON)["action"] is None


def test_unanswered_calls_are_remembered_only_up_to_a_limit():
    labels = MessageLabels()
    for number in range(500):
        message_document("O", "CP1", "in", frame(2, f"m{number}", "Authorize", {}), labels, ON)
    assert message_document("O", "CP1", "out", frame(3, "m0", {}), labels, ON)["action"] is None, "the oldest was forgotten"
    assert message_document("O", "CP1", "out", frame(3, "m499", {}), labels, ON)["action"] == "Authorize"


def test_heartbeats_and_their_replies_are_left_out_unless_asked_for():
    labels = MessageLabels()
    assert message_document("O", "CP1", "in", frame(2, "h1", "Heartbeat", {}), labels, ON) is None
    assert message_document("O", "CP1", "out", frame(3, "h1", {"currentTime": "t"}), labels, ON) is None
    kept = HistoryConfig(messages=True, heartbeats=True)
    assert message_document("O", "CP1", "in", frame(2, "h2", "Heartbeat", {}), labels, kept)["action"] == "Heartbeat"
    assert message_document("O", "CP1", "out", frame(3, "h2", {}), labels, kept)["action"] == "Heartbeat"


@pytest.mark.parametrize("raw", ["not json", "{}", "[]", "[2]", '[2, "id"]', '[9, "id", "X", {}]', '[2, 5, "X", {}]', '[2, "id", 7, {}]', "null"])
def test_anything_that_is_not_an_ocpp_frame_is_not_recorded(raw):
    assert message_document("O", "CP1", "in", raw, MessageLabels(), ON) is None


def test_a_payload_over_the_limit_is_not_stored_only_its_size():
    big = {"data": "x" * 70_000}
    doc = message_document("O", "CP1", "in", frame(2, "b", "DataTransfer", big), MessageLabels(), ON)
    assert doc["payload"] is None and doc["truncated"] is True and doc["size"] > 70_000


# --------------------------------------------------------------------------- reading
def test_a_cursor_round_trips_and_a_naive_time_is_taken_as_utc():
    when = datetime(2026, 10, 4, 10, 0, 0, 123000)
    back, row_id = decode_cursor(encode_cursor(when, "507f1f77bcf86cd799439011"))
    assert back == when.replace(tzinfo=timezone.utc) and row_id == "507f1f77bcf86cd799439011"


@pytest.mark.parametrize("cursor", ["", "!!!", "e30", "W10", "WyJ4IiwgIngiXQ", "W1sxXSwgImEiXQ"])
def test_a_cursor_that_is_not_one_of_ours_is_refused(cursor):
    with pytest.raises(BadCursor):
        decode_cursor(cursor)


def _raw_cursor(*parts):
    import base64

    return base64.urlsafe_b64encode(json.dumps(list(parts)).encode()).decode().rstrip("=")


def test_a_cursor_whose_id_is_not_text_is_refused():
    with pytest.raises(BadCursor):
        decode_cursor(_raw_cursor("2026-10-04T10:00:00+00:00", 5))


def test_a_cursor_time_without_a_zone_is_read_as_utc():
    when, row_id = decode_cursor(_raw_cursor("2026-10-04T10:00:00", "abc"))
    assert when == datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc) and row_id == "abc"


def test_meter_readings_are_flattened_into_one_row_per_sampled_value():
    document = {
        "connector_id": 1,
        "transaction_id": 7,
        "timestamp": datetime(2026, 10, 4, 10, 5),
        "meter_value": [
            {
                "timestamp": "2026-10-04T10:05:00Z",
                "sampled_value": [
                    {"value": "1500", "measurand": "Power.Active.Import", "unit": "W", "phase": "L1", "context": "Sample.Periodic"},
                    {"value": "12345"},
                    {"value": "n/a", "measurand": "Temperature", "unit": "Celsius"},
                ],
            },
            {"timestamp": "2026-10-04T10:06:00Z", "sampledValue": [{"value": "5.5", "unit": "kWh"}]},
            "junk",
            {"sampled_value": ["junk", {"value": None}]},
        ],
    }
    rows = flatten_meter_values(document)
    assert [(r["measurand"], r["value"], r["unit"], r["phase"]) for r in rows] == [
        ("Power.Active.Import", 1500.0, "W", "L1"),
        ("Energy.Active.Import.Register", 12345.0, "Wh", None),
        ("Temperature", None, "Celsius", None),
        ("Energy.Active.Import.Register", 5.5, "kWh", None),
        ("Energy.Active.Import.Register", None, "Wh", None),
    ]
    assert rows[2]["raw_value"] == "n/a" and rows[4]["raw_value"] is None
    assert all(r["connector_id"] == 1 and r["transaction_id"] == 7 for r in rows)
    assert rows[4]["timestamp"] == datetime(2026, 10, 4, 10, 5), "a reading without a time takes the message's"


def test_a_transactions_energy_is_what_the_meter_advanced():
    stopped = transaction_row({"org_name": "O", "charger_id": "C", "transaction_id": 1, "meter_start": 100, "meter_stop": 1350, "stop_timestamp": datetime(2026, 1, 1)})
    assert stopped["energy_wh"] == 1250 and stopped["open"] is False and stopped["org"] == "O"
    running = transaction_row({"transaction_id": 2, "meter_start": 100})
    assert running["energy_wh"] is None and running["open"] is True
    assert transaction_row({"transaction_id": 3, "meter_start": 500, "meter_stop": 100})["energy_wh"] is None, "a meter that went backwards is not an energy"
    assert transaction_row({"transaction_id": 4, "meter_stop": 100})["energy_wh"] is None


# --------------------------------------------------------------------------- the endpoints without MongoDB
@pytest.mark.asyncio
async def test_every_history_endpoint_says_so_when_there_is_no_mongodb(run_server, tags, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server.broker, "mongodb_service", None, raising=False)
    port = await run_server({"organizations": [org()]})
    async with rest(port) as client:
        info = (await client.get("/api/history/info")).json()
        assert info["available"] is False and "not configured" in info["reason"]
        assert info["messages_enabled"] is False and info["retention_days"] == {"messages": 30, "commands": 365}
        for path in ("transactions", "meter-values", "statuses", "commands", "messages"):
            body = (await client.get(f"/api/history/{path}")).json()
            assert body["available"] is False and body["items"] == [] and body["next_cursor"] is None, path
        detail = (await client.get("/api/history/transactions/Home/CP1/1")).json()
        assert detail["available"] is False and detail["transaction"] is None


@pytest.mark.asyncio
async def test_history_needs_the_api_key_and_refuses_bad_arguments(run_server, tags, monkeypatch):  # noqa: F811
    monkeypatch.setattr(server.broker, "mongodb_service", SlowMongo(), raising=False)
    port = await run_server({"organizations": [org()]})
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as anonymous:
        assert (await anonymous.get("/api/history/info")).status_code == 401
        assert (await anonymous.get("/api/history/messages")).status_code == 401
    async with rest(port) as client:
        assert (await client.get("/api/history/messages?cursor=nonsense")).status_code == 422
        assert (await client.get("/api/history/messages?limit=0")).status_code == 422
        assert (await client.get("/api/history/messages?limit=501")).status_code == 422
        assert (await client.get("/api/history/messages?direction=sideways")).status_code == 422
        assert (await client.get("/api/history/commands?status=maybe")).status_code == 422
        assert (await client.get("/api/history/transactions?state=half")).status_code == 422
        assert (await client.get("/api/history/transactions?since=yesterday")).status_code == 422


@pytest.mark.asyncio
async def test_a_database_that_fails_is_reported_not_raised(run_server, tags, monkeypatch):  # noqa: F811
    class Broken(SlowMongo):
        async def history_page(self, *args, **kwargs):
            raise RuntimeError("boom")

        async def history_counts(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(server.broker, "mongodb_service", Broken(), raising=False)
    port = await run_server({"organizations": [org()]})
    async with rest(port) as client:
        body = (await client.get("/api/history/commands")).json()
        assert body["available"] is False and "boom" in body["reason"]
        info = (await client.get("/api/history/info")).json()
        assert info["available"] is False and "boom" in info["reason"]


# --------------------------------------------------------------------------- what gets recorded
def saved(mongo, method):
    return [kwargs for name, kwargs in mongo.calls if name == method]


@pytest.mark.asyncio
async def test_with_the_log_on_every_frame_in_both_directions_is_recorded_and_replies_are_labelled(run_server, tags, monkeypatch):  # noqa: F811
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    monkeypatch.setattr(server.broker, "history", ON)
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        await charger.call("BootNotification", BOOT_PAYLOAD, "boot-1")
        await charger.recv()
        await charger.call("Heartbeat", {}, "beat-1")
        await charger.recv()
        await charger.call("Authorize", {"idTag": "TAG1"}, "auth-1")
        await charger.recv()
        await stored()
    finally:
        await charger.close()
    messages = saved(mongo, "save_message")
    seen = [(m["direction"], m["type"], m["action"], m["message_id"]) for m in messages]
    assert seen == [
        ("in", "call", "BootNotification", "boot-1"),
        ("out", "result", "BootNotification", "boot-1"),
        ("in", "call", "Authorize", "auth-1"),
        ("out", "result", "Authorize", "auth-1"),
    ], "in order, replies labelled, heartbeats left out"
    assert all(m["org_name"] == "Home" and m["charger_id"] == "CP1" for m in messages)
    assert messages[0]["payload"]["chargePointVendor"] == "TestVendor"
    assert messages[3]["payload"]["idTagInfo"]["status"] == "Accepted"


@pytest.mark.asyncio
async def test_with_the_log_off_no_message_is_recorded(run_server, tags, monkeypatch):  # noqa: F811
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        await charger.call("Authorize", {"idTag": "TAG1"})
        await charger.recv()
        await stored()
    finally:
        await charger.close()
    assert saved(mongo, "save_message") == []
    assert "save_authorization" in mongo.methods()


@pytest.mark.asyncio
async def test_the_log_covers_a_relay_both_ways_and_a_command(run_server, tags, monkeypatch):  # noqa: F811
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    backend = await ScriptedBackend().start()
    port = await run_server(
        {"organizations": [{"name": "Fleet", "connect_to_backend": True, "backends": [{"id": "lead", "url": backend.url, "leader": True}]}]}
    )
    monkeypatch.setattr(server.broker, "history", ON)
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await wait(lambda: backend.clients)
        await charger.call("Authorize", {"idTag": "X"}, "auth-1")
        assert (await charger.recv())[1] == "auth-1"  # the scripted backend answers on its own
        await backend.send([2, "from-backend", "Reset", {"type": "Soft"}])
        assert (await charger.recv())[2] == "Reset"
        await charger.send([3, "from-backend", {"status": "Accepted"}])
        await wait(lambda: any(m for m in backend.received_frames() if m[1] == "from-backend"))
        await stored()
    finally:
        await charger.close()
        await backend.stop()
    seen = [(m["direction"], m["type"], m["action"]) for m in saved(mongo, "save_message")]
    assert ("in", "call", "Authorize") in seen and ("out", "result", "Authorize") in seen
    assert ("out", "call", "Reset") in seen and ("in", "result", "Reset") in seen


@pytest.mark.asyncio
async def test_a_command_is_recorded_once_with_its_outcome_and_without_secrets(run_server, tags, monkeypatch):  # noqa: F811
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client:
            import asyncio

            request = asyncio.ensure_future(
                client.post(
                    "/api/ocpp/organizations/Home/chargers/CP1/commands",
                    json={"action": "ChangeConfiguration", "payload": {"key": "AuthorizationKey", "value": "s3cret"}, "timeout": 5},
                )
            )
            frame_ = await charger.recv()
            await charger.send([3, frame_[1], {"status": "Accepted"}])
            sent = (await request).json()
        await stored()
    finally:
        await charger.close()
    [command] = saved(mongo, "save_command")
    assert command["org_name"] == "Home" and command["charger_id"] == "CP1"
    assert command["message_id"] == sent["message_id"] and command["action"] == "ChangeConfiguration"
    assert command["status"] == "success" and command["response"] == {"status": "Accepted"}
    assert "s3cret" not in json.dumps(command, default=str), "a secret never reaches the history"
    assert command["sent_at"] <= command["finished_at"] and command["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_a_secret_in_a_commands_answer_is_not_kept_either(run_server, tags, monkeypatch):  # noqa: F811
    import asyncio

    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client:
            request = asyncio.ensure_future(
                client.post("/api/ocpp/organizations/Home/chargers/CP1/commands", json={"action": "GetConfiguration", "payload": {}, "timeout": 5})
            )
            asked = await charger.recv()
            answer = {
                "configurationKey": [
                    {"key": "AuthorizationKey", "readonly": False, "value": "s3cret"},
                    {"key": "HeartbeatInterval", "readonly": False, "value": "300"},
                ]
            }
            await charger.send([3, asked[1], answer])
            assert (await request).status_code == 200
        await stored()
    finally:
        await charger.close()
    [command] = saved(mongo, "save_command")
    assert "s3cret" not in json.dumps(command, default=str)
    assert [k["value"] for k in command["response"]["configurationKey"]] == ["***", "300"]


@pytest.mark.asyncio
async def test_a_command_to_a_charger_that_stays_silent_is_recorded_as_timed_out(run_server, tags, monkeypatch):  # noqa: F811
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client:
            response = await client.post(
                "/api/ocpp/organizations/Home/chargers/CP1/commands", json={"action": "ClearCache", "payload": {}, "timeout": 1}
            )
            assert response.status_code == 504
        await stored()
    finally:
        await charger.close()
    [command] = saved(mongo, "save_command")
    assert command["status"] == "timeout" and command["response"] is None


@pytest.mark.asyncio
async def test_a_command_the_broker_refuses_is_not_recorded(run_server, tags, monkeypatch):  # noqa: F811
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client:
            response = await client.post(
                "/api/ocpp/organizations/Home/chargers/CP1/commands", json={"action": "Reset", "payload": {"type": "Medium"}}
            )
            assert response.status_code == 422
        await stored()
    finally:
        await charger.close()
    assert saved(mongo, "save_command") == []


@pytest.mark.asyncio
async def test_a_relay_records_the_status_changes_it_sees_because_no_handler_does(run_server, tags, monkeypatch):  # noqa: F811
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    backend = await ScriptedBackend().start()
    port = await run_server(
        {"organizations": [{"name": "Fleet", "connect_to_backend": True, "backends": [{"id": "lead", "url": backend.url, "leader": True}]}]}
    )
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await wait(lambda: backend.clients)
        for status in ("Available", "Available", "Charging"):
            await charger.call("StatusNotification", {"connectorId": 1, "status": status, "errorCode": "NoError", "timestamp": "2026-10-04T10:00:00Z"})
            await charger.recv()
        await stored()
    finally:
        await charger.close()
        await backend.stop()
    rows = saved(mongo, "save_status_notification")
    assert [(r["connector_id"], r["status"]) for r in rows] == [(1, "Available"), (1, "Charging")], "changes only, as the console sees them"
    assert rows[0]["org_name"] == "Fleet" and rows[0]["charger_id"] == "CP1" and rows[0]["error_code"] == "NoError"
    assert rows[0]["timestamp"] == datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_broker_that_answers_stores_statuses_once_not_twice(run_server, tags, monkeypatch):  # noqa: F811
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        await charger.call("StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"})
        await charger.recv()
        await stored()
    finally:
        await charger.close()
    assert len(saved(mongo, "save_status_notification")) == 1


@pytest.mark.asyncio
async def test_meter_values_are_stored_as_plain_documents(run_server, tags, monkeypatch):  # noqa: F811
    """What goes to MongoDB is made of dicts, lists and numbers or text: nothing the driver cannot encode."""
    mongo = SlowMongo()
    monkeypatch.setattr(server.broker, "mongodb_service", mongo, raising=False)
    port = await run_server({"organizations": [org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        await charger.call("MeterValues", {"connectorId": 1, "transactionId": 5, "meterValue": [{"timestamp": "2026-10-04T10:05:00Z", "sampledValue": [{"value": "5", "unit": "Wh"}]}]})
        await charger.recv()
        await stored()
    finally:
        await charger.close()
    [row] = saved(mongo, "save_meter_values")

    def plain(value):
        if isinstance(value, dict):
            return all(plain(v) for v in value.values())
        if isinstance(value, list):
            return all(plain(v) for v in value)
        return value is None or isinstance(value, (str, int, float, bool))

    assert plain(row["meter_value"]), row["meter_value"]
    assert row["meter_value"][0]["sampled_value"][0]["value"] == "5"


async def wait(condition, timeout=5.0):
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        assert loop.time() < deadline, "timed out waiting"
        await asyncio.sleep(0.02)


def test_history_is_not_something_that_can_be_written_over_rest():
    """The broker writes history itself; nothing lets a caller add to it."""
    paths = server.app.openapi()["paths"]
    assert not [p for p in paths if p.startswith("/api/mongodb/") and p != "/api/mongodb/health"]
    history = {p: set(methods) for p, methods in paths.items() if p.startswith("/api/history")}
    assert history and all(methods == {"get"} for methods in history.values()), history


@pytest.mark.asyncio
async def test_shutdown_cancels_an_index_set_up_that_is_still_running():
    import asyncio

    from ocpp_broker.broker import OcppBroker

    broker = OcppBroker()
    never = asyncio.ensure_future(asyncio.sleep(3600))
    broker._history_tasks.add(never)
    broker.stop_mongodb_retry()
    await asyncio.sleep(0)
    assert never.cancelled()
