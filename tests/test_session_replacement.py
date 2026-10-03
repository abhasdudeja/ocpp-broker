"""
Sessions are keyed by (org, charger_id); a duplicate connect replaces the old one.
"""

import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from ocpp_broker.broker import OcppBroker


class FakeCharger:
    """Just enough of a Starlette WebSocket for a broker-mode session."""

    def __init__(self):
        self.headers = {"sec-websocket-protocol": "ocpp1.6"}
        self.client_state = type("S", (), {"name": "CONNECTED"})()
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[str] = []
        self.close_code = None

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            raise WebSocketDisconnect(code=1000)
        return item

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000, reason=None):
        self.close_code = code
        self.client_state = type("S", (), {"name": "DISCONNECTED"})()
        await self.inbox.put(None)

    def deliver(self, message):
        self.inbox.put_nowait(json.dumps(message))

    async def next_reply(self):
        for _ in range(100):
            if self.sent:
                return json.loads(self.sent.pop(0))
            await asyncio.sleep(0.01)
        raise AssertionError("no reply from broker")


@pytest.fixture
def broker():
    b = OcppBroker()
    b.config_data = {
        "organizations": [
            {"name": "OrgA", "connect_to_backend": False},
            {"name": "OrgB", "connect_to_backend": False},
        ]
    }
    return b


async def _wait_for(predicate, timeout=2.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


@pytest.mark.asyncio
async def test_same_charger_id_in_two_orgs_gets_two_sessions(broker):
    a, b = FakeCharger(), FakeCharger()
    ta = asyncio.create_task(broker.handle_charger(a, "/OrgA/CP1"))
    tb = asyncio.create_task(broker.handle_charger(b, "/OrgB/CP1"))
    await _wait_for(lambda: len(broker.sessions) == 2)

    assert set(broker.sessions) == {("OrgA", "CP1"), ("OrgB", "CP1")}

    for ws, task in ((a, ta), (b, tb)):
        await ws.close()
        await task
    assert broker.sessions == {}


@pytest.mark.asyncio
async def test_duplicate_connect_closes_old_socket_and_keeps_new_session(broker):
    old, new = FakeCharger(), FakeCharger()
    old_task = asyncio.create_task(broker.handle_charger(old, "/OrgA/CP1"))
    await _wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
    old_session = broker.sessions[("OrgA", "CP1")]

    new_task = asyncio.create_task(broker.handle_charger(new, "/OrgA/CP1"))
    await asyncio.wait_for(old_task, timeout=3)

    assert old.close_code == 4003
    new_session = broker.sessions[("OrgA", "CP1")]
    assert new_session is not old_session, "old handler's cleanup must not pop the new session"

    # The replacement is fully functional.
    new.deliver([2, "m1", "Heartbeat", {}])
    reply = await new.next_reply()
    assert reply[0] == 3 and reply[1] == "m1"

    await new.close()
    await new_task
    assert broker.sessions == {}


@pytest.mark.asyncio
async def test_unresponsive_old_session_is_cancelled(broker, monkeypatch):
    class Zombie(FakeCharger):
        async def close(self, code=1000, reason=None):
            self.close_code = code  # never completes the handshake

    zombie, new = Zombie(), FakeCharger()
    zombie_task = asyncio.create_task(broker.handle_charger(zombie, "/OrgA/CP1"))
    await _wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
    old_session = broker.sessions[("OrgA", "CP1")]

    real_evict = old_session.evict
    monkeypatch.setattr(old_session, "evict", lambda: real_evict(grace=0.1))

    new_task = asyncio.create_task(broker.handle_charger(new, "/OrgA/CP1"))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(zombie_task, timeout=3)

    assert broker.sessions[("OrgA", "CP1")] is not old_session
    await new.close()
    await new_task
