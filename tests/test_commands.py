"""
The command catalog (what the console builds its forms from) and the per-charger command log: that the
catalog is exactly what the broker can send, and that a command shows up in the history and on the event
stream with its outcome, without leaking secrets.
"""

import asyncio
import json
import re
from datetime import timedelta

import httpx
import pytest
from ocpp.messages import Call, validate_payload
from ocpp.v16 import call

from ocpp_broker import server
from ocpp_broker.api_server import create_api
from ocpp_broker.commands import COMMANDS, LOG_SIZE, REDACTED, new_entry, redact_payload, redact_response, route_for, schema_for

from .fakes import ScriptedCharger
from .test_events_api import Stream, boot, broker_org, rest


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------
def test_the_catalog_is_the_nineteen_messages_a_central_system_sends():
    assert len(COMMANDS) == 19
    assert {c.action for c in COMMANDS} == {
        "CancelReservation", "ChangeAvailability", "ChangeConfiguration", "ClearCache", "ClearChargingProfile",
        "DataTransfer", "GetCompositeSchedule", "GetConfiguration", "GetDiagnostics", "GetLocalListVersion",
        "RemoteStartTransaction", "RemoteStopTransaction", "ReserveNow", "Reset", "SendLocalList",
        "SetChargingProfile", "TriggerMessage", "UnlockConnector", "UpdateFirmware",
    }
    assert len({c.action for c in COMMANDS}) == len(COMMANDS)


@pytest.mark.parametrize("spec", COMMANDS, ids=lambda s: s.action)
def test_each_command_has_the_schema_the_ocpp_library_validates_against(spec):
    schema = schema_for(spec.action)
    assert schema is not None and schema["type"] == "object"
    assert schema["title"] == f"{spec.action}Request" and "$schema" not in schema
    assert hasattr(call, spec.action), "the library has the request class"
    assert spec.summary and spec.risk in ("read", "change", "disruptive")


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", COMMANDS, ids=lambda s: s.action)
async def test_a_payload_that_fills_in_the_required_fields_passes_the_librarys_validation(spec):
    """The schema in the catalog is the one broker mode enforces: an example built from it is accepted."""
    schema = schema_for(spec.action)
    payload = {name: sample(schema["properties"][name]) for name in schema.get("required", [])}
    await validate_payload(Call(unique_id="1", action=spec.action, payload=payload), "1.6")
    if schema.get("required"):
        with pytest.raises(Exception):
            await validate_payload(Call(unique_id="1", action=spec.action, payload={}), "1.6")


def sample(prop):
    if "enum" in prop:
        return prop["enum"][0]
    kind = prop.get("type")
    if kind in ("integer", "number"):
        return 1
    if kind == "string":
        return "2026-10-04T10:00:00Z" if prop.get("format") == "date-time" else "x"
    if kind == "object":
        return {name: sample(prop["properties"][name]) for name in prop.get("required", [])}
    if kind == "array":
        return [sample(prop["items"])]
    raise AssertionError(f"no sample for {prop}")


def test_a_schema_is_returned_as_a_copy_so_callers_cannot_alter_the_catalog():
    schema = schema_for("Reset")
    schema["properties"]["type"]["enum"].append("Nonsense")
    assert "Nonsense" not in schema_for("Reset")["properties"]["type"]["enum"]
    assert schema_for("Authorize") is None, "only commands a central system sends"


def test_every_command_has_a_typed_route_in_the_api():
    paths = set(create_api(server.broker).openapi()["paths"])
    for spec in COMMANDS:
        assert route_for(spec.action) in paths, spec.action


def test_the_disruptive_commands_are_the_ones_that_can_interrupt_or_restart():
    risk = {c.action: c.risk for c in COMMANDS}
    assert {a for a, r in risk.items() if r == "disruptive"} >= {"Reset", "UpdateFirmware", "RemoteStopTransaction", "ChangeAvailability", "UnlockConnector"}
    assert {a for a, r in risk.items() if r == "read"} == {"GetConfiguration", "GetCompositeSchedule", "GetLocalListVersion", "TriggerMessage"}


# ---------------------------------------------------------------------------
# Secrets stay out of the log
# ---------------------------------------------------------------------------
def test_an_authorization_key_is_never_kept_in_the_log():
    secret = {"key": "AuthorizationKey", "value": "hunter2hunter2"}
    assert redact_payload("ChangeConfiguration", secret) == {"key": "AuthorizationKey", "value": REDACTED}
    assert redact_payload("ChangeConfiguration", {"key": "authorizationkey", "value": "x"})["value"] == REDACTED, "key names are case-insensitive"
    assert redact_payload("ChangeConfiguration", {"key": "HeartbeatInterval", "value": "60"}) == {"key": "HeartbeatInterval", "value": "60"}
    assert redact_payload("Reset", {"type": "Soft"}) == {"type": "Soft"}
    assert secret["value"] == "hunter2hunter2", "the original payload (the one sent) is untouched"
    assert redact_payload("ChangeConfiguration", "not a dict") == "not a dict"


def test_a_configuration_listing_hides_the_authorization_key_too():
    response = {"configurationKey": [{"key": "AuthorizationKey", "readonly": False, "value": "s3cret"}, {"key": "Foo", "readonly": True, "value": "1"}], "unknownKey": []}
    cleaned = redact_response("GetConfiguration", response)
    assert cleaned["configurationKey"][0]["value"] == REDACTED and cleaned["configurationKey"][1]["value"] == "1"
    assert response["configurationKey"][0]["value"] == "s3cret"
    assert redact_response("GetConfiguration", None) is None
    assert redact_response("GetConfiguration", {"configurationKey": "odd"}) == {"configurationKey": "odd"}


def test_an_entry_knows_how_long_the_command_took():
    entry = new_entry("m1", "Reset", {"type": "Soft"})
    assert entry.status == "pending" and entry.duration_ms is None and entry.finished_at is None
    entry.sent_at -= timedelta(seconds=2.5)
    entry.finish("success", {"status": "Accepted"})
    assert entry.status == "success" and entry.response == {"status": "Accepted"}
    assert 2500 <= entry.duration_ms < 3500, "measured from when it was sent"


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------
async def send(client, charger, org, charger_id, action, payload, reply, timeout=None):
    """POST a command, let the charger answer it with ``reply`` (None: stay silent), return the HTTP response."""
    body = {"action": action, "payload": payload, **({"timeout": timeout} if timeout else {})}
    request = asyncio.ensure_future(client.post(f"/api/ocpp/organizations/{org}/chargers/{charger_id}/commands", json=body))
    frame = await charger.recv()
    if reply is not None:
        await charger.send([3, frame[1], reply])
    return frame, await request


@pytest.mark.asyncio
async def test_the_catalog_endpoint_lists_every_command_with_its_schema(run_server):
    port = await run_server({"organizations": [broker_org()]})
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as anonymous:
        assert (await anonymous.get("/api/ocpp/commands/catalog")).status_code == 401
    async with rest(port) as client:
        body = (await client.get("/api/ocpp/commands/catalog")).json()
    assert body["ocpp_version"] == "1.6" and len(body["commands"]) == 19
    reset = next(c for c in body["commands"] if c["action"] == "Reset")
    assert reset["risk"] == "disruptive" and reset["json_schema"]["properties"]["type"]["enum"] == ["Hard", "Soft"]
    assert reset["route"] == "/api/ocpp/organizations/{org_name}/chargers/{charger_id}/commands/Reset"
    assert [c["action"] for c in body["commands"]] == [c.action for c in COMMANDS]


@pytest.mark.asyncio
async def test_a_command_appears_in_the_history_and_on_the_stream_with_its_outcome(run_server):
    port = await run_server({"organizations": [broker_org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client, Stream(client) as stream:
            await boot(charger)
            await stream.message()
            assert (await client.get("/api/chargers/Home/CP1/commands")).json() == {"commands": []}

            frame, response = await send(client, charger, "Home", "CP1", "ChangeAvailability", {"connectorId": 1, "type": "Inoperative"}, {"status": "Accepted"})
            assert response.status_code == 200 and response.json()["status"] == "success"
            event = await stream.event("command.result")
            assert event["data"] == {"message_id": frame[1], "action": "ChangeAvailability", "status": "success", "error": None}
            assert (event["org"], event["charger_id"]) == ("Home", "CP1")
            assert "Inoperative" not in json.dumps(event), "the stream says what happened, not what was sent"

            frame2, response2 = await send(client, charger, "Home", "CP1", "Reset", {"type": "Soft"}, None, timeout=1)
            assert response2.status_code == 504
            assert (await stream.event("command.result"))["data"]["status"] == "timeout"

            history = (await client.get("/api/chargers/Home/CP1/commands")).json()["commands"]
            assert [(h["action"], h["status"]) for h in history] == [("Reset", "timeout"), ("ChangeAvailability", "success")], "newest first"
            done = history[1]
            assert done["message_id"] == frame[1] and done["payload"] == {"connectorId": 1, "type": "Inoperative"}
            assert done["response"] == {"status": "Accepted"} and done["error"] is None
            assert done["duration_ms"] >= 0 and done["finished_at"] >= done["sent_at"]
            assert "No response within 1" in history[0]["error"]
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_charger_error_is_reported_in_the_history(run_server):
    port = await run_server({"organizations": [broker_org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client, Stream(client) as stream:
            await boot(charger)
            await stream.message()
            request = asyncio.ensure_future(client.post("/api/ocpp/organizations/Home/chargers/CP1/commands", json={"action": "ClearCache", "payload": {}}))
            frame = await charger.recv()
            await charger.send([4, frame[1], "NotSupported", "No cache here", {}])
            assert (await request).json()["status"] == "error"
            event = await stream.event("command.result")
            assert event["data"]["status"] == "error" and "NotSupported" in event["data"]["error"]
            [entry] = (await client.get("/api/chargers/Home/CP1/commands")).json()["commands"]
            assert entry["status"] == "error" and "No cache here" in entry["error"]
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_command_that_was_never_sent_leaves_no_trace(run_server):
    port = await run_server({"organizations": [broker_org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client, Stream(client) as stream:
            await boot(charger)
            await stream.message()
            rejected = await client.post("/api/ocpp/organizations/Home/chargers/CP1/commands", json={"action": "Reset", "payload": {"type": "Medium"}})
            assert rejected.status_code == 422
            assert (await client.get("/api/chargers/Home/CP1/commands")).json() == {"commands": []}
            assert "command.result" not in [e.type for e in server.broker.events.replay(0).events]
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_the_authorization_key_reaches_the_charger_but_not_the_history(run_server):
    port = await run_server({"organizations": [broker_org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client, Stream(client) as stream:
            await boot(charger)
            await stream.message()
            payload = {"key": "AuthorizationKey", "value": "hunter2hunter2"}
            frame, _ = await send(client, charger, "Home", "CP1", "ChangeConfiguration", payload, {"status": "Accepted"})
            assert frame[3] == payload, "the charger gets the real value"
            await stream.event("command.result")
            history = (await client.get("/api/chargers/Home/CP1/commands")).text
            assert "hunter2hunter2" not in history and REDACTED in history
            assert "hunter2hunter2" not in json.dumps([e.data for e in server.broker.events.replay(0).events])
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_the_history_belongs_to_the_connection_and_is_bounded(run_server):
    port = await run_server({"organizations": [broker_org()]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client:
            await boot(charger)
            assert (await client.get("/api/chargers/Home/nobody/commands")).status_code == 404
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as anonymous:
                assert (await anonymous.get("/api/chargers/Home/CP1/commands")).status_code == 401
            session = server.broker.sessions[("Home", "CP1")]
            assert session.command_log.maxlen == LOG_SIZE == 50
            for n in range(LOG_SIZE + 5):
                session.command_log.append(new_entry(f"m{n}", "Reset", {}))
            listed = (await client.get("/api/chargers/Home/CP1/commands")).json()["commands"]
            assert len(listed) == LOG_SIZE and listed[0]["message_id"] == f"m{LOG_SIZE + 4}", "the oldest are dropped"
    finally:
        await charger.close()
    assert re.fullmatch(r"[A-Za-z]+", COMMANDS[0].action)
