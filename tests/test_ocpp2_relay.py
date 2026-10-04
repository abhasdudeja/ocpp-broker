"""
OCPP 2.0.1 (and 2.1) in relay mode: the frames are relayed as they are, the console reads the facts it shows out of
2.x frames, and the configuration refuses what the broker cannot do (translate between versions, or answer a 2.x
charger itself).
"""

import asyncio
import json

import httpx
import pytest
import websockets
from websockets.exceptions import InvalidStatus

from ocpp_broker import backend_manager, server
from ocpp_broker.charger_state import ChargerState
from ocpp_broker.config import load_config
from ocpp_broker.transaction_ids import table_for_org

from .fakes import AUTH_HEADERS, ScriptedBackend, ScriptedCharger, wait_for

V201 = "ocpp2.0.1"

BOOT = {
    "reason": "PowerUp",
    "chargingStation": {"model": "Wallbox 9", "vendorName": "Acme", "serialNumber": "SN-9", "firmwareVersion": "3.1", "modem": {"iccid": "8931", "imsi": "2040"}},
}
STARTED = {
    "eventType": "Started",
    "timestamp": "2026-10-04T10:00:00Z",
    "triggerReason": "Authorized",
    "seqNo": 0,
    "transactionInfo": {"transactionId": "tx-abc-123", "chargingState": "Charging"},
    "evse": {"id": 2, "connectorId": 1},
    "idToken": {"idToken": "TAG1", "type": "ISO14443"},
}
ENDED = {
    "eventType": "Ended",
    "timestamp": "2026-10-04T10:30:00Z",
    "triggerReason": "EVCommunicationLost",
    "seqNo": 2,
    "transactionInfo": {"transactionId": "tx-abc-123", "stoppedReason": "EVDisconnected"},
}


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


def org(*backends, version=V201, **extra):
    return {
        "name": "Fleet",
        "connect_to_backend": True,
        "ocpp_subprotocol": version,
        "leader_failover_timeout": 0.3,
        "backends": [{"id": f"b{n}", "url": b.url, **({"leader": True} if n == 0 else {})} for n, b in enumerate(backends)],
        **extra,
    }


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


async def connect(port, version=V201):
    charger = await ScriptedCharger.connect(port, "Fleet", "CP-2", subprotocol=version)
    for _ in range(100):
        async with rest(port) as client:
            info = (await client.get("/api/chargers/Fleet/CP-2")).json()
        if info.get("leader", {}).get("connected") and info.get("followers_connected") == info.get("followers_total"):
            return charger
        await asyncio.sleep(0.05)
    raise AssertionError("the backends never connected")


async def exchange(charger, action, payload):
    await charger.call(action, payload)
    return await charger.recv()


# --------------------------------------------------------------------------- relaying
@pytest.mark.asyncio
async def test_a_201_charger_is_relayed_to_201_backends_untouched_and_followers_get_copies(run_server):
    leader, follower = await ScriptedBackend(subprotocol=V201).start(), await ScriptedBackend(subprotocol=V201).start()
    port = await run_server({"organizations": [org(leader, follower)]})
    charger = await connect(port)
    try:
        assert (await exchange(charger, "BootNotification", BOOT))[0] == 3
        assert (await exchange(charger, "TransactionEvent", STARTED))[0] == 3
        await wait_for(lambda: len(follower.received) >= 2)
        sent_to_leader = [f for f in leader.received_frames() if f[0] == 2]
        sent_to_follower = [f for f in follower.received_frames() if f[0] == 2]
        assert [f[2] for f in sent_to_leader] == ["BootNotification", "TransactionEvent"]
        assert sent_to_leader[1][3] == STARTED, "the leader gets the frame exactly as the charger sent it"
        assert sent_to_follower[1][3] == STARTED, "and so does the follower: both see the same transaction id"
        assert sent_to_follower[1][3]["transactionInfo"]["transactionId"] == "tx-abc-123"
        session = server.broker.sessions[("Fleet", "CP-2")]
        assert session._ids is None, "no transaction id table: the charger chose the id"
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_the_backends_are_reached_with_the_organizations_subprotocol(run_server):
    leader = await ScriptedBackend(subprotocol=V201).start()
    port = await run_server({"organizations": [org(leader)]})
    charger = await connect(port)
    try:
        await wait_for(lambda: leader.clients)
        assert leader.clients[0].subprotocol == V201
    finally:
        await charger.close()
        await leader.stop()


@pytest.mark.asyncio
async def test_a_charger_that_asks_for_another_version_is_refused(run_server):
    leader = await ScriptedBackend(subprotocol=V201).start()
    port = await run_server({"organizations": [org(leader)]})
    try:
        with pytest.raises(InvalidStatus) as refused:
            async with websockets.connect(f"ws://127.0.0.1:{port}/Fleet/CP-2", subprotocols=["ocpp1.6"], ping_interval=None):
                pass
        assert refused.value.response.status_code == 403
        async with websockets.connect(f"ws://127.0.0.1:{port}/Fleet/CP-2", subprotocols=["ocpp1.6", V201], ping_interval=None) as ws:
            assert ws.subprotocol == V201, "a charger that offers several gets the organization's"
    finally:
        await leader.stop()


@pytest.mark.asyncio
async def test_a_command_reaches_a_201_charger_and_its_answer_comes_back(run_server):
    leader = await ScriptedBackend(subprotocol=V201).start()
    port = await run_server({"organizations": [org(leader)]})
    charger = await connect(port)
    try:
        async with rest(port) as client:
            request = asyncio.ensure_future(
                client.post("/api/ocpp/organizations/Fleet/chargers/CP-2/commands", json={"action": "Reset", "payload": {"type": "OnIdle"}, "timeout": 5})
            )
            frame = await charger.recv()
            assert frame[0] == 2 and frame[2] == "Reset" and frame[3] == {"type": "OnIdle"}
            await charger.send([3, frame[1], {"status": "Accepted"}])
            answer = await request
        assert answer.status_code == 200 and answer.json()["response"] == {"status": "Accepted"}
    finally:
        await charger.close()
        await leader.stop()


@pytest.mark.asyncio
async def test_failover_works_for_201_too(run_server):
    leader, follower = await ScriptedBackend(subprotocol=V201).start(), await ScriptedBackend(subprotocol=V201).start()
    port = await run_server({"organizations": [org(leader, follower)]})
    charger = await connect(port)
    try:
        await leader.stop()
        for _ in range(100):
            async with rest(port) as client:
                info = (await client.get("/api/chargers/Fleet/CP-2")).json()
            if info["leader"]["key"] == "b1":
                break
            await asyncio.sleep(0.1)
        assert info["leader"]["key"] == "b1"
        before = len(follower.calls)
        assert (await exchange(charger, "Heartbeat", {}))[0] == 3 and len(follower.calls) > before
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


# --------------------------------------------------------------------------- the console
@pytest.mark.asyncio
async def test_the_console_shows_a_201_charger_with_its_version_boot_details_and_evses(run_server):
    leader = await ScriptedBackend(subprotocol=V201).start()
    port = await run_server({"organizations": [org(leader)]})
    charger = await connect(port)
    try:
        await exchange(charger, "BootNotification", BOOT)
        await exchange(charger, "StatusNotification", {"timestamp": "2026-10-04T09:59:00Z", "connectorStatus": "Available", "evseId": 1, "connectorId": 1})
        await exchange(charger, "StatusNotification", {"timestamp": "2026-10-04T10:00:00Z", "connectorStatus": "Occupied", "evseId": 2, "connectorId": 1})
        await exchange(charger, "Heartbeat", {})
        async with rest(port) as client:
            detail = (await client.get("/api/chargers/Fleet/CP-2")).json()
            orgs = (await client.get("/api/orgs")).json()
            listing = (await client.get("/api/chargers")).json()
        assert detail["ocpp_version"] == "2.0.1" and orgs[0]["ocpp_version"] == "2.0.1" and listing["chargers"][0]["ocpp_version"] == "2.0.1"
        assert (detail["boot"]["vendor"], detail["boot"]["model"], detail["boot"]["serial_number"], detail["boot"]["firmware_version"]) == ("Acme", "Wallbox 9", "SN-9", "3.1")
        assert (detail["boot"]["iccid"], detail["boot"]["imsi"]) == ("8931", "2040")
        assert [(c["connector_id"], c["status"]) for c in detail["connectors"]] == [(1, "Available"), (2, "Occupied")]
        assert detail["last_heartbeat_at"] is not None
        assert detail["transaction_id_mapping"] is False and detail["transactions"] == []
    finally:
        await charger.close()
        await leader.stop()


@pytest.mark.asyncio
async def test_status_changes_and_transactions_of_a_201_charger_are_events(run_server):
    leader = await ScriptedBackend(subprotocol=V201).start()
    port = await run_server({"organizations": [org(leader)]})
    charger = await connect(port)
    try:
        await exchange(charger, "BootNotification", BOOT)
        await exchange(charger, "StatusNotification", {"timestamp": "t", "connectorStatus": "Available", "evseId": 2, "connectorId": 1})
        await exchange(charger, "StatusNotification", {"timestamp": "t", "connectorStatus": "Occupied", "evseId": 2, "connectorId": 1})
        await exchange(charger, "TransactionEvent", STARTED)
        await exchange(charger, "TransactionEvent", {**STARTED, "eventType": "Updated", "seqNo": 1})
        await exchange(charger, "TransactionEvent", ENDED)
        events = [(e.type, e.data) for e in server.broker.events.replay(0).events if e.charger_id == "CP-2" and e.type.startswith(("charger.boot", "charger.status", "transaction."))]
        assert ("charger.boot", {"vendor": "Acme", "model": "Wallbox 9", "firmware_version": "3.1"}) in events
        assert [d["status"] for t, d in events if t == "charger.status"] == ["Available", "Occupied"]
        assert [d["previous"] for t, d in events if t == "charger.status"] == [None, "Available"]
        assert [t for t, _ in events if t.startswith("transaction.")] == ["transaction.started", "transaction.stopped"], "an update is not news"
        started = next(d for t, d in events if t == "transaction.started")
        stopped = next(d for t, d in events if t == "transaction.stopped")
        assert started["connector_id"] == 2 and started["transaction_id"] == "tx-abc-123"
        assert stopped["transaction_id"] == "tx-abc-123" and stopped["reason"] == "EVDisconnected"
    finally:
        await charger.close()
        await leader.stop()


# --------------------------------------------------------------------------- the state model
def observe(version, action, payload, state=None):
    state = state or ChargerState(ocpp_version=version)
    return state, state.observe(json.dumps([2, "m", action, payload]))


@pytest.mark.parametrize("version", ["2.0.1", "2.1"])
def test_201_and_21_frames_are_read_the_same_way(version):
    state, changes = observe(version, "BootNotification", BOOT)
    assert changes == [("charger.boot", {"vendor": "Acme", "model": "Wallbox 9", "firmware_version": "3.1"})]
    assert state.boot is not None and state.boot.serial_number == "SN-9"
    state, changes = observe(version, "StatusNotification", {"timestamp": "2026-10-04T10:00:00Z", "connectorStatus": "Faulted", "evseId": 3, "connectorId": 2}, state)
    assert changes == [("charger.status", {"connector_id": 3, "status": "Faulted", "previous": None, "error_code": None})]
    assert state.connectors[3].updated_at.year == 2026


def test_a_1_6_frame_means_nothing_to_a_2x_charger_and_the_reverse():
    _, changes = observe("2.0.1", "StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
    assert changes == []
    _, changes = observe("1.6", "StatusNotification", {"evseId": 1, "connectorStatus": "Occupied"})
    assert changes == []


def test_a_repeated_status_is_not_news_and_a_new_one_names_the_old():
    state, _ = observe("2.0.1", "StatusNotification", {"connectorStatus": "Available", "evseId": 1})
    _, changes = observe("2.0.1", "StatusNotification", {"connectorStatus": "Available", "evseId": 1}, state)
    assert changes == []
    _, changes = observe("2.0.1", "StatusNotification", {"connectorStatus": "Occupied", "evseId": 1}, state)
    assert changes[0][1]["previous"] == "Available"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"connectorStatus": 5, "evseId": 1},
        {"connectorStatus": "Available", "evseId": "1"},
        {"connectorStatus": "Available", "evseId": True},
    ],
)
def test_a_status_that_cannot_be_read_changes_nothing(payload):
    state, changes = observe("2.0.1", "StatusNotification", payload)
    assert changes == [] and state.connectors == {}


@pytest.mark.parametrize("payload", [{}, {"chargingStation": "text"}, {"chargingStation": {"modem": 7}}, {"chargingStation": {"model": 5}}])
def test_a_boot_with_odd_content_is_still_a_boot(payload):
    state, changes = observe("2.0.1", "BootNotification", payload)
    assert changes[0][0] == "charger.boot" and state.boot is not None


def test_transaction_events_that_cannot_be_read_do_not_break_the_model():
    _, changes = observe("2.0.1", "TransactionEvent", {"eventType": "Started", "transactionInfo": "x", "evse": [1]})
    assert changes == [("transaction.started", {"connector_id": None, "meter_start": None, "transaction_id": None})]
    _, changes = observe("2.0.1", "TransactionEvent", {"eventType": "Ended"})
    assert changes == [("transaction.stopped", {"transaction_id": None, "meter_stop": None, "reason": None})]
    _, changes = observe("2.0.1", "TransactionEvent", {"eventType": "Updated", "transactionInfo": {"transactionId": "x"}})
    assert changes == []


@pytest.mark.parametrize("evse", ["x", None, 1.5, True, [1]])
def test_a_transaction_on_an_evse_that_is_not_a_number_has_no_connector(evse):
    _, changes = observe("2.0.1", "TransactionEvent", {"eventType": "Started", "evse": {"id": evse}, "transactionInfo": {"transactionId": "t"}})
    assert changes[0][1]["connector_id"] is None


def test_a_heartbeat_is_noted():
    state, _ = observe("2.0.1", "Heartbeat", {})
    assert state.last_heartbeat_at is not None


# --------------------------------------------------------------------------- configuration
@pytest.fixture
def write(tmp_path):
    def make(body):
        path = tmp_path / "config.yaml"
        path.write_text(body, encoding="utf-8")
        return str(path)

    return make


RELAY_201 = "organizations:\n  - name: Fleet\n    ocpp_subprotocol: ocpp2.0.1\n    backends:\n      - {id: a, url: 'ws://x/o'}\n"


def test_a_201_organization_with_backends_is_accepted_and_its_backends_speak_201(write):
    [fleet] = load_config(write(RELAY_201))["organizations"]
    assert fleet["backends"][0]["ocpp_subprotocol"] == "ocpp2.0.1"
    load_config(write(RELAY_201.replace("ocpp2.0.1", "ocpp2.1")))


@pytest.mark.parametrize(
    "body, message",
    [
        (RELAY_201.replace("ocpp2.0.1", "ocpp9"), "ocpp_subprotocol must be one of"),
        (RELAY_201.replace("{id: a, url: 'ws://x/o'}", "{id: a, url: 'ws://x/o', ocpp_subprotocol: ocpp1.6}"), "does not translate between versions"),
        (RELAY_201.replace("ocpp2.0.1", "ocpp1.6").replace("{id: a, url: 'ws://x/o'}", "{id: a, url: 'ws://x/o', ocpp_subprotocol: ocpp2.0.1}"), "does not translate between versions"),
        (RELAY_201.replace("    backends:\n      - {id: a, url: 'ws://x/o'}\n", "    connect_to_backend: false\n"), "relayed to backends only"),
        (RELAY_201.replace("    backends:\n      - {id: a, url: 'ws://x/o'}\n", ""), "relayed to backends only"),
        (RELAY_201 + "      - {id: me, local: true}\n", "cannot be a backend for ocpp2.0.1"),
    ],
)
def test_what_the_broker_cannot_do_with_2x_is_refused_at_startup(write, body, message):
    with pytest.raises(ValueError, match=message):
        load_config(write(body))


def test_a_16_organization_may_still_answer_chargers_itself_and_have_a_local_backend(write):
    load_config(write("organizations:\n  - name: Home\n    connect_to_backend: false\n"))
    load_config(write("organizations:\n  - name: Hybrid\n    backends:\n      - {id: a, url: 'ws://x/o', leader: true}\n      - {id: me, local: true}\n"))


def test_forcing_the_id_table_on_for_201_is_ignored_with_a_warning(write, caplog):
    with caplog.at_level("WARNING", logger="ocpp_broker.config"):
        [fleet] = load_config(write(RELAY_201 + "    transaction_ids: {mapping: true}\n"))["organizations"]
    assert "transaction_ids.mapping is ignored" in caplog.text
    assert table_for_org(fleet) is None


def test_the_id_table_still_exists_for_16_with_several_backends():
    assert table_for_org({"ocpp_subprotocol": "ocpp1.6", "backends": [{}, {}]}) is not None
    assert table_for_org({"backends": [{}, {}]}) is not None
    assert table_for_org({"ocpp_subprotocol": "ocpp2.0.1", "backends": [{}, {}]}) is None
    assert table_for_org({"ocpp_subprotocol": "ocpp2.1", "backends": [{}, {}], "transaction_ids": {"mapping": True}}) is None
