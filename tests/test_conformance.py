"""
One table-driven check per standard OCPP 1.6 message, in both directions, in each way a charger can be served:

* **broker**: the broker is the central system;
* **local_leader**: the broker is the leader and an external backend receives copies;
* **relay**: an external backend is the leader and the broker forwards.

Charger to central system (the ten messages a charger initiates): in the two modes where the broker
answers, the reply must be valid against the OCPP 1.6 response schema and mean what the standard says; in
relay mode the request must reach the backend untouched and the backend's reply must come back unchanged.

Central system to charger (the 19 commands of the catalog): the REST call must put exactly the payload on
the wire, accept the charger's valid reply and return it.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx
import pytest
import pytest_asyncio
from ocpp.messages import CallResult, validate_payload

from ocpp_broker import backend_manager, server
from ocpp_broker.commands import COMMANDS, schema_for

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger, wait_for

MODES = ["broker", "local_leader", "relay"]
TAGS = [{"id_tag": "TAG1", "status": "Accepted"}]


@dataclass
class Env:
    mode: str
    port: int
    charger: ScriptedCharger
    backends: List[ScriptedBackend]  # the leader (relay) or the follower (local_leader); empty in broker mode

    @property
    def answers_itself(self) -> bool:
        return self.mode != "relay"

    def rest(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.port}", headers=AUTH_HEADERS, timeout=10)


@pytest_asyncio.fixture(params=MODES)
async def env(request, run_server, monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)
    server.broker.tag_manager = None
    mode = request.param
    backends: List[ScriptedBackend] = []
    if mode == "broker":
        org: Dict[str, Any] = {"name": "Org", "connect_to_backend": False, "tags": TAGS}
    elif mode == "local_leader":
        backends = [await ScriptedBackend().start()]
        org = {
            "name": "Org",
            "connect_to_backend": True,
            "tags": TAGS,
            "backends": [{"id": "broker", "local": True}, {"id": "mirror", "url": backends[0].url}],
        }
    else:
        backends = [await ScriptedBackend().start()]
        org = {"name": "Org", "connect_to_backend": True, "tags": TAGS, "backends": [{"id": "lead", "url": backends[0].url, "leader": True}]}
    port = await run_server({"organizations": [org]})
    charger = await ScriptedCharger.connect(port, "Org", "CP1")
    if backends:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10) as client:
            for _ in range(100):
                detail = (await client.get("/api/chargers/Org/CP1")).json()
                if detail["leader"]["connected"] and detail["followers_connected"] == detail["followers_total"]:
                    break
                await asyncio.sleep(0.05)
    yield Env(mode, port, charger, backends)
    await charger.close()
    for backend in backends:
        await backend.stop()
    server.broker.tag_manager = None


async def valid_result(action: str, payload: Dict[str, Any]) -> None:
    """Raises if ``payload`` is not a valid OCPP 1.6 answer to ``action``."""
    await validate_payload(CallResult(unique_id="x", payload=payload, action=action), "1.6")


# ---------------------------------------------------------------------------
# Charger to central system
# ---------------------------------------------------------------------------
START = {"connectorId": 1, "idTag": "TAG1", "meterStart": 100, "timestamp": "2026-10-04T10:00:00Z"}
READING = [{"timestamp": "2026-10-04T10:05:00Z", "sampledValue": [{"value": "5"}]}]

# action, request payload, what the answering broker must say
CHARGER_MESSAGES = [
    ("BootNotification", BOOT_PAYLOAD, lambda r: r["status"] == "Accepted" and r["interval"] == 300),
    ("Heartbeat", {}, lambda r: "currentTime" in r),
    ("Authorize", {"idTag": "TAG1"}, lambda r: r["idTagInfo"]["status"] == "Accepted"),
    ("Authorize", {"idTag": "NOBODY"}, lambda r: r["idTagInfo"]["status"] == "Invalid"),
    ("StartTransaction", START, lambda r: isinstance(r["transactionId"], int) and r["idTagInfo"]["status"] == "Accepted"),
    ("StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"}, lambda r: r == {}),
    ("MeterValues", {"connectorId": 1, "meterValue": READING}, lambda r: r == {}),
    ("StopTransaction", {"transactionId": 1, "meterStop": 9, "timestamp": "2026-10-04T11:00:00Z"}, lambda r: True),
    ("DataTransfer", {"vendorId": "Acme"}, lambda r: r["status"] in ("Accepted", "Rejected", "UnknownMessageId", "UnknownVendorId")),
    ("DiagnosticsStatusNotification", {"status": "Idle"}, lambda r: r == {}),
    ("FirmwareStatusNotification", {"status": "Idle"}, lambda r: r == {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("action, payload, means", CHARGER_MESSAGES, ids=[f"{a}-{i}" for i, (a, _, _) in enumerate(CHARGER_MESSAGES)])
async def test_a_message_from_the_charger_is_answered_as_the_standard_says(env, action, payload, means):
    await env.charger.call(action, payload, "msg-1")
    reply = await env.charger.recv()
    assert reply[0] == 3 and reply[1] == "msg-1", f"a CALLRESULT to the same message id: {reply}"
    if env.answers_itself:
        await valid_result(action, reply[2])
        assert means(reply[2]), reply[2]
    else:
        backend = env.backends[0]
        await wait_for(lambda: backend.calls)
        assert backend.calls[-1][2:4] == [action, payload], "the backend got the request exactly as the charger sent it"
        expected = ScriptedBackend()._process([2, "x", action, payload])
        if action == "StartTransaction":  # the backend numbers the transaction itself; the rest is its reply
            assert reply[2]["idTagInfo"] == expected["idTagInfo"] and isinstance(reply[2]["transactionId"], int)
        else:
            assert reply[2] == expected, "and its reply came back unchanged"


@pytest.mark.asyncio
async def test_every_message_a_charger_can_send_is_in_the_table():
    from ocpp.v16 import call

    charger_initiated = {"Authorize", "BootNotification", "DataTransfer", "DiagnosticsStatusNotification", "FirmwareStatusNotification",
                         "Heartbeat", "MeterValues", "StartTransaction", "StatusNotification", "StopTransaction"}
    assert charger_initiated == {a for a, _, _ in CHARGER_MESSAGES}
    assert all(hasattr(call, a) for a in charger_initiated)


@pytest.mark.asyncio
async def test_a_request_that_breaks_the_schema_is_refused_by_the_broker_and_forwarded_by_a_relay(env):
    await env.charger.call("Authorize", {}, "bad-1")  # idTag is required
    reply = await env.charger.recv()
    if env.answers_itself:
        assert reply[0] == 4 and reply[1] == "bad-1" and reply[2] in ("ProtocolError", "FormationViolation", "OccurenceConstraintViolation", "PropertyConstraintViolation"), reply
    else:
        await wait_for(lambda: env.backends[0].calls)
        assert env.backends[0].calls[-1][3] == {}, "a relay does not judge; the backend does"
        assert reply[0] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("action, error", [("ReserveNow", "NotImplemented"), ("Bogus", "NotSupported")])
async def test_a_message_only_a_central_system_may_send_gets_an_error_from_the_broker(env, action, error):
    if not env.answers_itself:
        pytest.skip("a relay leaves the answer to its backend")
    await env.charger.call(action, {}, "odd-1")
    reply = await env.charger.recv()
    assert reply[0] == 4 and reply[1] == "odd-1" and reply[2] == error


# ---------------------------------------------------------------------------
# Central system to charger
# ---------------------------------------------------------------------------
def sample(prop: Dict[str, Any]) -> Any:
    if "enum" in prop:
        return prop["enum"][0]
    kind = prop.get("type")
    if kind in ("integer", "number"):
        return 1
    if kind == "string":
        if prop.get("format") == "date-time":
            return "2026-10-04T10:00:00Z"
        if prop.get("format") == "uri":
            return "ftp://host/file"
        return "x"
    if kind == "object":
        return {name: sample(prop["properties"][name]) for name in prop.get("required", [])}
    if kind == "array":
        return [sample(prop["items"])]
    raise AssertionError(prop)


def response_schema(action: str) -> Dict[str, Any]:
    import os

    import ocpp.v16

    path = os.path.join(os.path.dirname(ocpp.v16.__file__), "schemas", f"{action}Response.json")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def minimal(schema: Dict[str, Any]) -> Dict[str, Any]:
    return {name: sample(schema["properties"][name]) for name in schema.get("required", [])}


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [c.action for c in COMMANDS])
async def test_a_command_reaches_the_charger_as_sent_and_its_valid_answer_comes_back(env, command):
    payload = minimal(schema_for(command))
    answer = minimal(response_schema(command))
    await valid_result(command, answer)  # the answer this test gives is itself valid
    async with env.rest() as client:
        request = asyncio.ensure_future(
            client.post("/api/ocpp/organizations/Org/chargers/CP1/commands", json={"action": command, "payload": payload, "timeout": 5})
        )
        frame = await env.charger.recv()
        assert frame[0] == 2 and frame[2] == command, frame
        # exactly the payload that was asked for is on the wire; the ocpp library adds an empty list for an
        # omitted optional list (SendLocalList.localAuthorizationList), which means the same
        assert {k: v for k, v in frame[3].items() if k in payload} == payload, frame[3]
        assert all(v == [] for k, v in frame[3].items() if k not in payload), frame[3]
        await env.charger.send([3, frame[1], answer])
        result = (await request).json()
    assert result["status"] == "success" and result["response"] == answer
    assert result["message_id"] == frame[1]


@pytest.mark.asyncio
async def test_a_charger_that_refuses_a_command_is_reported_as_an_error_in_every_mode(env):
    async with env.rest() as client:
        request = asyncio.ensure_future(
            client.post("/api/ocpp/organizations/Org/chargers/CP1/commands", json={"action": "ClearCache", "payload": {}, "timeout": 5})
        )
        frame = await env.charger.recv()
        await env.charger.send([4, frame[1], "NotSupported", "no cache", {}])
        result = (await request).json()
    assert result["status"] == "error" and "NotSupported" in result["error"]


@pytest.mark.asyncio
async def test_the_catalog_is_exactly_what_the_conformance_table_covers():
    assert len(COMMANDS) == 19 and len({c.action for c in COMMANDS}) == 19
