"""
Remote (REST-issued) commands return the charger's real reply.

Broker mode goes through BrokerChargePoint.call(); relay mode intercepts the
matching CallResult by message id so it never reaches the backend.
"""

import asyncio

import pytest
import pytest_asyncio

from ocpp_broker.broker import OcppBroker
from ocpp_broker.session import ChargerSession, CommandRejected, SessionMode

from .fakes import FakeCharger


async def _wait_for(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


# ---------------------------------------------------------------------------
# Broker mode
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def connected():
    """A real broker-mode session with a scripted charger on the other end."""
    broker = OcppBroker()
    broker.config_data = {"organizations": [{"name": "OrgA", "connect_to_backend": False}]}
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    await _wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
    session = broker.sessions[("OrgA", "CP1")]
    await _wait_for(lambda: session.charge_point is not None)
    yield session, charger
    await charger.close()
    await asyncio.wait_for(task, timeout=3)


@pytest.mark.asyncio
async def test_broker_mode_returns_the_real_call_result(connected):
    session, charger = connected

    pending = asyncio.create_task(session.send_command("Reset", {"type": "Hard"}, timeout=2))
    frame = await charger.next_reply()
    assert frame[0] == 2 and frame[2] == "Reset" and frame[3] == {"type": "Hard"}
    charger.deliver([3, frame[1], {"status": "Accepted"}])

    result = await pending
    assert (result.status, result.response) == ("success", {"status": "Accepted"})
    assert result.message_id == frame[1]


@pytest.mark.asyncio
async def test_broker_mode_nested_call_result_comes_back_camel_cased(connected):
    session, charger = connected

    pending = asyncio.create_task(session.send_command("GetConfiguration", {"key": ["A"]}, timeout=2))
    frame = await charger.next_reply()
    charger.deliver([3, frame[1], {"configurationKey": [{"key": "A", "readonly": False, "value": "1"}]}])

    result = await pending
    assert result.response == {"configurationKey": [{"key": "A", "readonly": False, "value": "1"}]}


@pytest.mark.asyncio
async def test_broker_mode_reports_a_charger_call_error(connected):
    session, charger = connected

    pending = asyncio.create_task(session.send_command("Reset", {"type": "Soft"}, timeout=2))
    frame = await charger.next_reply()
    charger.deliver([4, frame[1], "NotSupported", "Reset is not supported", {}])

    result = await pending
    assert result.status == "error"
    assert result.error == "NotSupported: Reset is not supported"


@pytest.mark.asyncio
async def test_broker_mode_times_out_and_keeps_working(connected):
    session, charger = connected

    result = await session.send_command("Reset", {"type": "Hard"}, timeout=0.2)
    assert result.status == "timeout"
    await charger.next_reply()  # the CALL did go out

    # A late reply to the abandoned call is ignored; the next command works.
    pending = asyncio.create_task(session.send_command("ClearCache", {}, timeout=2))
    frame = await charger.next_reply()
    charger.deliver([3, frame[1], {"status": "Accepted"}])
    assert (await pending).status == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,payload",
    [
        ("Reset", {"type": "Bogus"}),  # violates the OCPP 1.6 schema
        ("Reset", {}),  # missing required field
        ("Reset", {"type": "Hard", "nope": 1}),  # unknown field
        ("NotARealAction", {}),
        ("__builtins__", {}),
    ],
)
async def test_broker_mode_rejects_invalid_commands_without_sending(connected, action, payload):
    session, charger = connected

    with pytest.raises(CommandRejected):
        await session.send_command(action, payload, timeout=1)

    assert charger.sent == []


@pytest.mark.asyncio
async def test_broker_mode_charger_dropping_mid_call_fails_fast(connected):
    session, charger = connected

    pending = asyncio.create_task(session.send_command("Reset", {"type": "Hard"}, timeout=30))
    await charger.next_reply()
    await charger.close()

    with pytest.raises(ConnectionError):
        await asyncio.wait_for(pending, timeout=3)


# ---------------------------------------------------------------------------
# Relay mode
# ---------------------------------------------------------------------------
class FakeBackend:
    def __init__(self):
        self.connected_event = asyncio.Event()
        self.connected_event.set()
        self.sent: list[str] = []

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        pass


@pytest_asyncio.fixture
async def relaying():
    broker = OcppBroker()
    charger = FakeCharger()
    session = ChargerSession(
        broker=broker,
        charger_id="CP1",
        org_name="OrgA",
        org_entry={"name": "OrgA", "connect_to_backend": True},
        websocket=charger,
    )
    session.backend_conn = FakeBackend()
    assert session.mode is SessionMode.RELAY
    loop_task = asyncio.create_task(session._relay_loop())
    yield session, charger, session.backend_conn
    await charger.close()
    await asyncio.wait_for(loop_task, timeout=3)


@pytest.mark.asyncio
async def test_relay_mode_intercepts_the_matching_reply(relaying):
    session, charger, backend = relaying

    pending = asyncio.create_task(session.send_command("Reset", {"type": "Hard"}, timeout=2))
    frame = await charger.next_reply()
    assert frame[0] == 2 and frame[2] == "Reset"

    # Interleave ordinary charger traffic with the reply to our command.
    charger.deliver([2, "hb-1", "Heartbeat", {}])
    charger.deliver([3, frame[1], {"status": "Accepted"}])
    charger.deliver([2, "hb-2", "Heartbeat", {}])

    result = await pending
    assert (result.status, result.response) == ("success", {"status": "Accepted"})

    await _wait_for(lambda: len(backend.sent) == 2)
    assert [m for m in backend.sent if frame[1] in m] == [], "reply must not leak to the backend"
    assert all("Heartbeat" in m for m in backend.sent)


@pytest.mark.asyncio
async def test_relay_mode_reports_call_error_and_still_relays_unknown_replies(relaying):
    session, charger, backend = relaying

    pending = asyncio.create_task(session.send_command("Reset", {"type": "Hard"}, timeout=2))
    frame = await charger.next_reply()
    # A CallResult for a call the *backend* made is not ours: it must be relayed.
    charger.deliver([3, "backend-call-7", {"status": "Accepted"}])
    charger.deliver([4, frame[1], "InternalError", "boom", {}])

    result = await pending
    assert (result.status, result.error) == ("error", "InternalError: boom")
    await _wait_for(lambda: len(backend.sent) == 1)
    assert "backend-call-7" in backend.sent[0]


@pytest.mark.asyncio
async def test_relay_mode_times_out(relaying):
    session, charger, backend = relaying

    result = await session.send_command("Reset", {"type": "Hard"}, timeout=0.2)

    assert result.status == "timeout"
    assert session._pending_calls == {}


@pytest.mark.asyncio
async def test_relay_mode_close_fails_waiting_commands(relaying):
    session, charger, backend = relaying

    pending = asyncio.create_task(session.send_command("Reset", {"type": "Hard"}, timeout=30))
    await charger.next_reply()
    await session.close()

    with pytest.raises(ConnectionError):
        await asyncio.wait_for(pending, timeout=3)
