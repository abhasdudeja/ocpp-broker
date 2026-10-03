"""
Follower backends: observe-only fan-out, and leader failover.
"""

import asyncio

import pytest

from ocpp_broker import backend_manager
from ocpp_broker.broker import OcppBroker

from .fakes import FakeBackend, FakeCharger, wait_for

BOOT = [2, "boot-1", "BootNotification", {"chargePointVendor": "V", "chargePointModel": "M"}]


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


async def _start_relay(leader: FakeBackend, follower: FakeBackend, **org):
    broker = OcppBroker()
    broker.config_data = {
        "organizations": [
            {
                "name": "OrgA",
                "connect_to_backend": True,
                "backends": [
                    {"id": "lead", "url": leader.url, "leader": True},
                    {"id": "follow", "url": follower.url},
                ],
                **org,
            }
        ]
    }
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    await wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
    session = broker.sessions[("OrgA", "CP1")]
    await wait_for(lambda: session.backend_conn and all(
        c.is_ready() for c in [session.backend_conn, *session.follower_conns]
    ) and session.follower_conns)
    return broker, session, charger, task


async def _finish(charger, task, *backends):
    await charger.close()
    await asyncio.wait_for(task, timeout=5)
    for backend in backends:
        await backend.stop()


@pytest.mark.asyncio
async def test_followers_receive_a_copy_of_charger_calls_but_not_results():
    leader, follower = await FakeBackend().start(), await FakeBackend().start()
    broker, session, charger, task = await _start_relay(leader, follower)
    try:
        charger.deliver(BOOT)
        charger.deliver([3, "answer-to-leader", {"status": "Accepted"}])
        charger.deliver([2, "hb-1", "Heartbeat", {}])

        await wait_for(lambda: len(leader.received) == 3)
        await wait_for(lambda: len(follower.received) == 2)

        assert [f[1] for f in leader.received_frames()] == ["boot-1", "answer-to-leader", "hb-1"]
        assert [f[1] for f in follower.received_frames()] == ["boot-1", "hb-1"]
    finally:
        await _finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_follower_replies_never_reach_the_charger_but_leader_replies_do():
    leader, follower = await FakeBackend().start(), await FakeBackend().start()
    broker, session, charger, task = await _start_relay(leader, follower)
    try:
        charger.deliver(BOOT)
        await wait_for(lambda: leader.received and follower.received)

        await follower.send([3, "boot-1", {"status": "Accepted", "interval": 1, "currentTime": "x"}])
        await asyncio.sleep(0.2)
        assert charger.sent == [], "a follower must not be able to answer the charger"

        await leader.send([3, "boot-1", {"status": "Accepted", "interval": 300, "currentTime": "y"}])
        reply = await charger.next_reply()
        assert reply[1] == "boot-1" and reply[2]["interval"] == 300
    finally:
        await _finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_dead_follower_does_not_disturb_the_leader_path():
    leader, follower = await FakeBackend().start(), await FakeBackend().start()
    broker, session, charger, task = await _start_relay(leader, follower, leader_failover_timeout=0)
    try:
        await follower.stop()
        await wait_for(lambda: not session.follower_conns[0].is_ready())

        for n in range(5):
            charger.deliver([2, f"hb-{n}", "Heartbeat", {}])
        await wait_for(lambda: len(leader.received) == 5)

        assert session.follower_conns[0].buffered_count == 0, "followers are never buffered"
    finally:
        await _finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_leader_failover_promotes_the_first_healthy_follower():
    leader, follower = await FakeBackend().start(), await FakeBackend().start()
    broker, session, charger, task = await _start_relay(leader, follower, leader_failover_timeout=0.3)
    old_leader_conn = session.backend_conn
    follower_conn = session.follower_conns[0]
    leader_port = leader.port
    try:
        await leader.stop()
        await wait_for(lambda: not old_leader_conn.is_ready())

        # A CALL sent while the leader is down is held, then answered when we fail over.
        charger.deliver([2, "stuck-1", "StatusNotification", {}])
        await wait_for(lambda: old_leader_conn.buffered_count == 1)

        await wait_for(lambda: session.backend_conn is follower_conn, timeout=5)

        assert follower_conn.is_leader and not old_leader_conn.is_leader
        assert session.follower_conns == [old_leader_conn]
        links = broker.org_backends["OrgA"]["CP1"]
        assert links["leader"] is follower_conn and links["followers"] == [old_leader_conn]

        error = await charger.next_reply()
        assert error[:3] == [4, "stuck-1", "InternalError"], "held CALL is answered, not replayed"
        assert old_leader_conn.buffered_count == 0

        # Traffic now goes to the new leader, and its replies reach the charger.
        before = len(follower.received)
        charger.deliver(BOOT)
        await wait_for(lambda: len(follower.received) > before)
        await follower.send([3, "boot-1", {"status": "Accepted", "interval": 7, "currentTime": "z"}])
        reply = await charger.next_reply()
        assert reply[1] == "boot-1" and reply[2]["interval"] == 7

        # The old leader returns as an observer: it gets copies, its replies are ignored.
        await leader.start(leader_port)
        await wait_for(old_leader_conn.is_ready)
        charger.deliver([2, "hb-9", "Heartbeat", {}])
        await wait_for(lambda: any(f[1] == "hb-9" for f in leader.received_frames()))
        await leader.send([3, "hb-9", {"currentTime": "q"}])
        await asyncio.sleep(0.2)
        assert charger.sent == []
        assert session.backend_conn is follower_conn, "no automatic fail-back"
    finally:
        await _finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_no_failover_while_the_leader_recovers_within_the_grace_period():
    leader, follower = await FakeBackend().start(), await FakeBackend().start()
    broker, session, charger, task = await _start_relay(leader, follower, leader_failover_timeout=1.0)
    leader_conn = session.backend_conn
    port = leader.port
    try:
        await leader.stop()
        await wait_for(lambda: not leader_conn.is_ready())
        await leader.start(port)
        await wait_for(leader_conn.is_ready)
        await asyncio.sleep(1.4)

        assert session.backend_conn is leader_conn and leader_conn.is_leader
    finally:
        await _finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_failover_waits_until_a_follower_is_healthy():
    leader, follower = await FakeBackend().start(), await FakeBackend().start()
    broker, session, charger, task = await _start_relay(leader, follower, leader_failover_timeout=0.2)
    leader_conn, follower_conn = session.backend_conn, session.follower_conns[0]
    follower_port = follower.port
    try:
        await follower.stop()
        await leader.stop()
        await wait_for(lambda: not leader_conn.is_ready() and not follower_conn.is_ready())
        await asyncio.sleep(0.6)
        assert session.backend_conn is leader_conn, "nobody to promote yet"

        await follower.start(follower_port)
        await wait_for(lambda: session.backend_conn is follower_conn, timeout=5)
    finally:
        await _finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_failover_can_be_disabled():
    leader, follower = await FakeBackend().start(), await FakeBackend().start()
    broker, session, charger, task = await _start_relay(leader, follower, leader_failover_timeout=0)
    leader_conn = session.backend_conn
    try:
        await leader.stop()
        await wait_for(lambda: not leader_conn.is_ready())
        await asyncio.sleep(0.5)
        assert session.backend_conn is leader_conn
    finally:
        await _finish(charger, task, leader, follower)
