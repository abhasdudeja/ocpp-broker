"""
Store-and-forward when the backend is unreachable (relay mode).
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from ocpp_broker.backend_manager import BackendConnection
from ocpp_broker.broker import OcppBroker

from .fakes import FakeBackend, FakeCharger, wait_for


def _call(n: int) -> str:
    return json.dumps([2, f"id-{n}", "StatusNotification", {"n": n}])


def _make_connection(backend, **kwargs):
    broker = OcppBroker()
    broker.sessions[("OrgA", "CP1")] = SimpleNamespace(websocket=FakeCharger())
    conn = BackendConnection(
        broker, "CP1", backend.url if backend.port else "ws://127.0.0.1:1/ocpp",
        org="OrgA", is_leader=True, **kwargs,
    )
    conn.reconnect_delay = 0.05
    return conn


@pytest.mark.asyncio
async def test_frames_sent_during_an_outage_arrive_in_order_after_reconnect():
    backend = await FakeBackend().start()
    conn = _make_connection(backend)
    await conn.connect()
    try:
        await conn.send(_call(1))
        await wait_for(lambda: len(backend.received) == 1)

        port = backend.port
        await backend.stop()
        await wait_for(lambda: not conn.is_ready())

        await conn.send(_call(2))
        await conn.send(_call(3))
        assert conn.buffered_count == 2
        assert len(backend.received) == 1

        await backend.start(port)
        await wait_for(lambda: len(backend.received) >= 3)
        await conn.send(_call(4))  # live traffic resumes behind the backlog
        await wait_for(lambda: len(backend.received) == 4)

        assert [json.loads(m)[3]["n"] for m in backend.received] == [1, 2, 3, 4]
        assert conn.buffered_count == 0
    finally:
        await conn.close()
        await backend.stop()


@pytest.mark.asyncio
async def test_frames_sent_before_the_first_connection_are_delivered_once_it_is_up():
    backend = FakeBackend()
    await backend.start()
    port = backend.port
    await backend.stop()  # port is now known but nothing listens

    conn = _make_connection(backend)
    conn.url = f"ws://127.0.0.1:{port}/ocpp"
    await asyncio.wait_for(conn.connect(wait=False), timeout=1)  # must not block
    try:
        await conn.send(_call(1))
        await conn.send(_call(2))
        assert conn.buffered_count == 2

        await backend.start(port)
        await wait_for(lambda: len(backend.received) == 2)

        assert [json.loads(m)[3]["n"] for m in backend.received] == [1, 2]
        assert backend.paths == ["/ocpp/CP1"]
    finally:
        await conn.close()
        await backend.stop()


@pytest.mark.asyncio
async def test_a_failed_write_is_buffered_not_lost():
    backend = await FakeBackend().start()
    conn = _make_connection(backend)
    await conn.connect()
    try:
        async def broken(_message):
            raise ConnectionError("socket died mid-write")

        conn.websocket.send = broken
        await conn.send(_call(1))
        assert conn.buffered_count == 1, "frame must be kept for retry"

        # The broken socket is dropped; the reconnect delivers the frame.
        await wait_for(lambda: len(backend.received) == 1)
        assert json.loads(backend.received[0])[3]["n"] == 1
    finally:
        await conn.close()
        await backend.stop()


@pytest.mark.asyncio
async def test_full_outbox_refuses_new_frames_instead_of_dropping_old_ones():
    refused = []

    async def on_undeliverable(message):
        refused.append(message)

    conn = _make_connection(FakeBackend(), max_buffered=2, on_undeliverable=on_undeliverable)
    # Never started: the backend is unreachable.
    await conn.send(_call(1))
    await conn.send(_call(2))
    await conn.send(_call(3))
    await wait_for(lambda: refused)

    assert refused == [_call(3)]
    assert [e.message for e in conn._outbox] == [_call(1), _call(2)]
    await conn.close()


@pytest.mark.asyncio
async def test_frames_waiting_past_the_outage_timeout_are_handed_back():
    refused = []

    async def on_undeliverable(message):
        refused.append(message)

    conn = _make_connection(FakeBackend(), outage_timeout=0.15, on_undeliverable=on_undeliverable)
    await conn.send(_call(1))
    await asyncio.sleep(0.05)
    await conn.send(_call(2))

    await wait_for(lambda: len(refused) == 2)

    assert refused == [_call(1), _call(2)]
    assert conn.buffered_count == 0
    await conn.close()


@pytest.mark.asyncio
async def test_follower_style_connection_without_buffer_just_drops():
    conn = _make_connection(FakeBackend(), max_buffered=0)
    await conn.send(_call(1))  # no callback, no buffer: must not raise or hang
    assert conn.buffered_count == 0
    await conn.close()


# ---------------------------------------------------------------------------
# Session level: the charger is answered, not left hanging
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_charger_gets_a_call_error_when_the_backend_stays_down():
    broker = OcppBroker()
    broker.config_data = {
        "organizations": [
            {
                "name": "OrgA",
                "connect_to_backend": True,
                "backend_outage_timeout": 0.2,
                "backends": [{"id": "b1", "url": "ws://127.0.0.1:1/ocpp", "leader": True}],
            }
        ]
    }
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    try:
        charger.deliver([2, "boot-1", "BootNotification", {"chargePointVendor": "V", "chargePointModel": "M"}])
        charger.deliver([3, "reply-to-backend-call", {"status": "Accepted"}])  # cannot be answered

        reply = await charger.next_reply()

        assert reply[:3] == [4, "boot-1", "InternalError"], reply
        await asyncio.sleep(0.4)
        assert charger.sent == [], "only CALLs are answered; the stale CALLRESULT is just dropped"
    finally:
        await charger.close()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_session_buffers_for_a_backend_that_comes_up_late():
    backend = FakeBackend()
    await backend.start()
    port = backend.port
    await backend.stop()

    broker = OcppBroker()
    broker.config_data = {
        "organizations": [
            {
                "name": "OrgA",
                "connect_to_backend": True,
                "backend_outage_timeout": 30,
                "backends": [{"id": "b1", "url": f"ws://127.0.0.1:{port}/ocpp", "leader": True}],
            }
        ]
    }
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    try:
        charger.deliver([2, "boot-1", "BootNotification", {"chargePointVendor": "V", "chargePointModel": "M"}])
        await wait_for(lambda: broker.sessions.get(("OrgA", "CP1")) and broker.sessions[("OrgA", "CP1")].backend_conn.buffered_count == 1)

        await backend.start(port)
        await wait_for(lambda: len(backend.received) == 1, timeout=10)  # default 1s reconnect delay

        assert backend.received_frames()[0][:3] == [2, "boot-1", "BootNotification"]
        assert charger.sent == [], "nothing was lost, so nothing needed rejecting"
    finally:
        await charger.close()
        await asyncio.wait_for(task, timeout=5)
        await backend.stop()
