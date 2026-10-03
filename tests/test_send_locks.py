"""
Every write to a charger or backend socket is serialised by a per-socket lock.
"""

import asyncio

import pytest

from ocpp_broker import sockets
from ocpp_broker.backend_manager import BackendConnection
from ocpp_broker.broker import OcppBroker
from ocpp_broker.charge_point import StarletteWebSocketAdapter
from ocpp_broker.session import ChargerSession

from .fakes import FakeCharger


class OverlapProbe:
    """Records the highest number of sends in flight at once."""

    def __init__(self):
        self.in_flight = 0
        self.max_in_flight = 0
        self.frames: list[str] = []

    async def write(self, message):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)  # a real socket yields here
        self.frames.append(message)
        self.in_flight -= 1


class ProbedCharger(FakeCharger):
    def __init__(self):
        super().__init__()
        self.probe = OverlapProbe()

    async def send_text(self, text):
        await self.probe.write(text)


def _session(charger):
    return ChargerSession(
        broker=OcppBroker(),
        charger_id="CP1",
        org_name="OrgA",
        org_entry={"name": "OrgA", "connect_to_backend": True},
        websocket=charger,
    )


@pytest.mark.asyncio
async def test_charger_socket_writers_never_overlap():
    charger = ProbedCharger()
    session = _session(charger)
    # Same wiring _run_local_charge_point uses: adapter shares the session's lock.
    adapter = StarletteWebSocketAdapter(charger, send_lock=session._send_lock)

    await asyncio.gather(
        *(adapter.send(f"ocpp-{i}") for i in range(5)),
        *(session.send_to_charger(f"relay-{i}") for i in range(5)),
    )

    assert charger.probe.max_in_flight == 1
    assert len(charger.probe.frames) == 10


@pytest.mark.asyncio
async def test_broker_mode_adapter_shares_the_session_lock():
    from .test_remote_commands import _wait_for

    broker = OcppBroker()
    broker.config_data = {"organizations": [{"name": "OrgA", "connect_to_backend": False}]}
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    await _wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
    session = broker.sessions[("OrgA", "CP1")]
    await _wait_for(lambda: session.charge_point is not None)

    assert session.charge_point._connection._send_lock is session._send_lock

    await charger.close()
    await task


class ProbedBackendSocket:
    def __init__(self):
        self.probe = OverlapProbe()
        self.close_code = None

    async def send(self, message):
        await self.probe.write(message)


@pytest.mark.asyncio
async def test_backend_socket_writers_never_overlap():
    backend = BackendConnection(OcppBroker(), "CP1", "ws://backend/ocpp", org="OrgA", is_leader=True)
    backend.websocket = ProbedBackendSocket()
    backend.connected_event.set()

    await asyncio.gather(*(backend.send(f"frame-{i}") for i in range(10)))

    assert backend.websocket.probe.max_in_flight == 1
    assert sorted(backend.websocket.probe.frames) == sorted(f"frame-{i}" for i in range(10))


@pytest.mark.asyncio
async def test_wedged_charger_socket_is_dropped_and_lock_released(monkeypatch):
    monkeypatch.setattr(sockets, "SEND_TIMEOUT", 0.1)

    class Wedged(FakeCharger):
        async def send_text(self, text):
            await asyncio.sleep(3600)

    charger = Wedged()
    session = _session(charger)

    await session.send_to_charger("never arrives")  # logs, does not raise or hang

    assert charger.close_code == 1011
    assert not session._send_lock.locked(), "a stuck send must not hold the lock forever"
    with pytest.raises(ConnectionError):
        await session._send_text("again")


@pytest.mark.asyncio
async def test_locked_send_times_out_instead_of_blocking_forever():
    lock = asyncio.Lock()

    async def never(_):
        await asyncio.sleep(3600)

    with pytest.raises(asyncio.TimeoutError):
        await sockets.locked_send(lock, never, "x", timeout=0.05)
    assert not lock.locked()
