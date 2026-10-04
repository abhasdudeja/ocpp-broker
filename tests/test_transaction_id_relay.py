"""
Transaction id mapping in the relay path, over real sockets: a charger, a leader
and a follower that each number transactions their own way.
"""

import asyncio
import json

import pytest

from ocpp_broker import backend_manager
from ocpp_broker.broker import OcppBroker
from ocpp_broker.config import _apply_defaults

from .fakes import FakeCharger, ScriptedBackend, wait_for


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


async def start_relay(leader, follower, **org):
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
    await wait_for(
        lambda: session.backend_conn
        and session.follower_conns
        and all(c.is_ready() for c in [session.backend_conn, *session.follower_conns])
    )
    return broker, session, charger, task


async def finish(charger, task, *backends):
    await charger.close()
    await asyncio.wait_for(task, timeout=5)
    for backend in backends:
        await backend.stop()


def start_frame(mid, tag="A", meter=0, ts="2026-10-04T10:00:00Z", connector=1):
    return [2, mid, "StartTransaction", {"connectorId": connector, "idTag": tag, "meterStart": meter, "timestamp": ts}]


def meter_frame(tx, mid, value=1):
    return [2, mid, "MeterValues", {"connectorId": 1, "transactionId": tx, "meterValue": [{"timestamp": "t", "sampledValue": [{"value": str(value)}]}]}]


def stop_frame(tx, mid, meter_stop=100):
    return [2, mid, "StopTransaction", {"transactionId": tx, "meterStop": meter_stop, "timestamp": "2026-10-04T11:00:00Z"}]


async def begin(charger, mid, **kwargs):
    """Start a transaction; returns the id the charger is told."""
    charger.deliver(start_frame(mid, **kwargs))
    reply = await charger.next_reply()
    assert reply[1] == mid
    return reply[2]["transactionId"]


# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_each_backend_is_spoken_to_in_its_own_transaction_ids():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        tx = await begin(charger, "s1")
        assert tx == 10, "the charger holds the leader's id"

        meter, stop = meter_frame(tx, "mv1"), stop_frame(tx, "st1")
        charger.deliver(meter)
        charger.deliver(stop)
        await wait_for(lambda: follower.transactions.get(1, {}).get("stop"))

        assert follower.transactions[1]["stop"]["meterStop"] == 100
        assert len(follower.transactions[1]["meter_values"]) == 1
        assert follower.unknown == [] and leader.unknown == []
        assert json.dumps(meter) in leader.received and json.dumps(stop) in leader.received, "leader traffic is byte-identical"
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_follower_that_missed_a_start_is_not_made_to_end_someone_elses_transaction():
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    broker, session, charger, task = await start_relay(leader, follower, transaction_ids={"follower_wait": 0.3})
    try:
        a = await begin(charger, "sA", tag="A", meter=1)
        follower.skip_starts = 1  # the follower is down for B
        b = await begin(charger, "sB", tag="B", meter=2)
        c = await begin(charger, "sC", tag="C", meter=3)
        assert (a, b, c) == (1, 2, 3)

        charger.deliver(stop_frame(b, "stB", meter_stop=111))
        await asyncio.sleep(0.8)  # long enough for the wait for the follower's answer to run out
        assert follower.transactions[2]["start"]["idTag"] == "C"
        assert follower.transactions[2]["stop"] is None, "B's stop must not end the follower's transaction 2, which is C"

        charger.deliver(stop_frame(c, "stC", meter_stop=333))
        await wait_for(lambda: follower.transactions[2]["stop"])
        assert follower.transactions[2]["stop"]["meterStop"] == 333
        assert follower.unknown == []
        assert leader.transactions[b]["stop"]["meterStop"] == 111, "the leader still gets everything"
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_without_mapping_the_same_sequence_corrupts_the_followers_transaction():
    """Characterisation of the bug the table fixes: with mapping switched off, B's stop ends C on the follower."""
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    broker, session, charger, task = await start_relay(leader, follower, transaction_ids={"mapping": False})
    try:
        assert session._ids is None
        await begin(charger, "sA", tag="A", meter=1)
        follower.skip_starts = 1
        b = await begin(charger, "sB", tag="B", meter=2)
        await begin(charger, "sC", tag="C", meter=3)

        charger.deliver(stop_frame(b, "stB", meter_stop=111))
        await wait_for(lambda: follower.transactions[2]["stop"])
        assert follower.transactions[2]["start"]["idTag"] == "C"
        assert follower.transactions[2]["stop"]["meterStop"] == 111, "silent misattribution: C was ended with B's reading"
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_slow_follower_gets_its_copies_in_order_under_its_own_id():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1, reply_delay=0.3).start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        tx = await begin(charger, "s1")
        for i in range(3):
            charger.deliver(meter_frame(tx, f"mv{i}", value=i))
        charger.deliver([2, "hb", "Heartbeat", {}])
        charger.deliver(stop_frame(tx, "st"))

        await wait_for(lambda: follower.transactions.get(1, {}).get("stop"), timeout=3)
        assert [c[1] for c in follower.calls] == ["s1", "mv0", "mv1", "mv2", "hb", "st"], "order is kept"
        assert follower.unknown == []
        assert len(leader.calls) == 6, "the leader never waited for the follower"
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_follower_that_never_answers_does_not_hold_up_the_leader_or_receive_wrong_ids():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1, mute=True).start()
    broker, session, charger, task = await start_relay(leader, follower, transaction_ids={"follower_wait": 0.3})
    try:
        tx = await begin(charger, "s1")
        charger.deliver(meter_frame(tx, "mv1"))
        charger.deliver([2, "hb", "Heartbeat", {}])
        await wait_for(lambda: len(leader.calls) == 3, timeout=1)  # at once, not after the wait

        await asyncio.sleep(0.7)
        assert [c[2] for c in follower.calls] == ["StartTransaction", "Heartbeat"], "the meter values were dropped, not sent with the wrong id"
        assert follower.unknown == []
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_retried_start_gets_the_same_id_and_the_backends_see_one_transaction():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        first = await begin(charger, "s1")
        charger.deliver(start_frame("s1-retry"))
        retry = await charger.next_reply()
        assert retry == [3, "s1-retry", {"transactionId": first, "idTagInfo": {"status": "Accepted"}}]

        await asyncio.sleep(0.2)
        assert len(leader.transactions) == 1 and len(follower.transactions) == 1
        assert sum(1 for c in leader.calls if c[2] == "StartTransaction") == 1
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_the_leaders_reply_reaches_the_charger_unchanged_when_ids_do_not_collide():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        charger.deliver(start_frame("s1"))
        await wait_for(lambda: charger.sent)
        assert charger.sent[0] == json.dumps([3, "s1", {"transactionId": 10, "idTagInfo": {"status": "Accepted"}}])
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_follower_that_was_down_for_the_start_is_skipped_at_once_not_after_a_wait():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        await follower.stop()
        await wait_for(lambda: not session.follower_conns[0].is_ready())
        tx = await begin(charger, "s1")

        await follower.start(port=follower.port)
        await wait_for(lambda: session.follower_conns[0].is_ready())
        charger.deliver(meter_frame(tx, "mv1"))
        charger.deliver(stop_frame(tx, "st1"))
        await wait_for(lambda: len(leader.calls) == 3)
        await asyncio.sleep(0.2)

        assert follower.calls == [], "it never saw the start, so it must not be sent this transaction's readings"
        assert session._ids.next_expiry_in() is None, "nothing is held waiting for a follower that was never asked"
        assert session._ids.snapshot()[0]["degraded"] == ["follow"]
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_several_followers_each_get_their_own_ids():
    leader = await ScriptedBackend(first_id=10).start()
    one, two = await ScriptedBackend(first_id=100).start(), await ScriptedBackend(first_id=500, step=7).start()
    broker = OcppBroker()
    broker.config_data = {
        "organizations": [
            {
                "name": "OrgA",
                "connect_to_backend": True,
                "backends": [
                    {"id": "lead", "url": leader.url, "leader": True},
                    {"id": "one", "url": one.url},
                    {"id": "two", "url": two.url},
                ],
            }
        ]
    }
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    try:
        await wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
        session = broker.sessions[("OrgA", "CP1")]
        await wait_for(lambda: len(session.follower_conns) == 2 and all(c.is_ready() for c in [session.backend_conn, *session.follower_conns]))

        first = await begin(charger, "s1", tag="A", meter=1)
        second = await begin(charger, "s2", tag="B", meter=2)
        charger.deliver(stop_frame(second, "st2", meter_stop=222))
        charger.deliver(stop_frame(first, "st1", meter_stop=111))
        await wait_for(lambda: one.transactions[101]["stop"] and two.transactions[507]["stop"])

        assert (one.transactions[100]["stop"]["meterStop"], one.transactions[101]["stop"]["meterStop"]) == (111, 222)
        assert (two.transactions[500]["stop"]["meterStop"], two.transactions[507]["stop"]["meterStop"]) == (111, 222)
        assert one.unknown == [] and two.unknown == []
    finally:
        await charger.close()
        await asyncio.wait_for(task, timeout=5)
        for backend in (leader, one, two):
            await backend.stop()


# --------------------------------------------------------------------------
# Failover, reconnects and retries
# --------------------------------------------------------------------------
async def wait_promoted(session, follower_key="follow"):
    await wait_for(lambda: session.backend_conn is not None and session.backend_conn.key == follower_key, timeout=5)


@pytest.mark.asyncio
async def test_after_a_failover_the_new_leader_speaks_its_own_ids_and_a_collision_is_remapped():
    leader, follower = await ScriptedBackend(first_id=2).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower, leader_failover_timeout=0.3)
    try:
        a = await begin(charger, "sA", tag="A", meter=1)
        assert a == 2, "the charger holds the old leader's id"
        await wait_for(lambda: 1 in follower.transactions)
        await wait_for(lambda: session._ids.snapshot()[0]["backend_ids"].get("follow") == 1)

        await leader.stop()
        await wait_promoted(session)

        # the new leader's command quotes its own id; the charger must see its own
        await follower.command("RemoteStopTransaction", {"transactionId": 1})
        assert await charger.next_reply() == [2, "cmd-1", "RemoteStopTransaction", {"transactionId": 2}]

        # a transaction that starts on the new leader gets 2 from it, which the charger already holds for A
        b = await begin(charger, "sB", tag="B", meter=5)
        assert b != 2, "the charger must not hold id 2 for two live transactions"

        charger.deliver(stop_frame(a, "stA", meter_stop=111))
        charger.deliver(stop_frame(b, "stB", meter_stop=222))
        await wait_for(lambda: follower.transactions[1]["stop"] and follower.transactions[2]["stop"])
        assert follower.transactions[1]["stop"]["meterStop"] == 111, "A ends under the new leader's own number for it"
        assert follower.transactions[2]["stop"]["meterStop"] == 222
        assert follower.unknown == []
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_reconnecting_charger_keeps_its_transaction_mapping():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        tx = await begin(charger, "s1")
        await wait_for(lambda: session._ids.snapshot()[0]["backend_ids"].get("follow") == 1)
        table = session._ids
        await charger.close()
        await asyncio.wait_for(task, timeout=5)
        assert broker.transaction_tables[("OrgA", "CP1")] is table, "an open transaction keeps the table alive"

        charger = FakeCharger()
        task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
        await wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
        session = broker.sessions[("OrgA", "CP1")]
        await wait_for(lambda: session.backend_conn and session.follower_conns and all(c.is_ready() for c in [session.backend_conn, *session.follower_conns]))
        assert session._ids is table

        charger.deliver(stop_frame(tx, "st1", meter_stop=77))
        await wait_for(lambda: follower.transactions[1]["stop"])
        assert follower.transactions[1]["stop"]["meterStop"] == 77
        assert leader.transactions[10]["stop"]["meterStop"] == 77
        assert follower.unknown == [] and leader.unknown == []
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_late_answer_to_an_observer_copy_never_reaches_the_charger_after_a_failover():
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend(reply_delay=0.6).start()
    broker, session, charger, task = await start_relay(leader, follower, leader_failover_timeout=0.2)
    try:
        charger.deliver([2, "hb-1", "Heartbeat", {}])
        assert (await charger.next_reply())[1] == "hb-1"  # the leader's own answer
        await leader.stop()
        await wait_promoted(session)

        await asyncio.sleep(1.0)  # the new leader's answer to its observer copy arrives now, as a leader frame
        assert charger.sent == [], "the charger never asked that backend"
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_retry_while_the_leader_is_slow_is_answered_together_with_the_first_attempt():
    leader, follower = await ScriptedBackend(first_id=10, reply_delay=0.4).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        charger.deliver(start_frame("s1"))
        charger.deliver(start_frame("s1-retry"))
        first, second = await charger.next_reply(), await charger.next_reply()
        assert {first[1], second[1]} == {"s1", "s1-retry"}
        assert first[2]["transactionId"] == second[2]["transactionId"] == 10
        assert sum(1 for c in leader.calls if c[2] == "StartTransaction") == 1, "the leader started one transaction"
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_start_refused_because_the_leader_is_down_fails_its_waiting_retry_too_and_can_be_retried_cleanly():
    leader, follower = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(
        leader, follower, leader_failover_timeout=0, backend_outage_timeout=0.3
    )
    try:
        await leader.stop()
        await wait_for(lambda: not session.backend_conn.is_ready())
        charger.deliver(start_frame("s1"))  # held for the leader; the follower still gets its copy
        charger.deliver(start_frame("s1-retry"))  # waits on the first attempt
        await wait_for(lambda: 1 in follower.transactions)

        errors = [await charger.next_reply(), await charger.next_reply()]
        assert sorted((e[0], e[1], e[2]) for e in errors) == [(4, "s1", "InternalError"), (4, "s1-retry", "InternalError")]

        await leader.start(port=leader.port)
        await wait_for(lambda: session.backend_conn.is_ready())
        charger.deliver(start_frame("s1-retry-2"))
        reply = await charger.next_reply()
        assert reply[1] == "s1-retry-2" and reply[2]["transactionId"] == 10
        await asyncio.sleep(0.2)
        assert len(follower.transactions) == 1, "the follower already had this start; it must not get a second"
        assert len(leader.transactions) == 1
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_start_retried_after_a_failover_does_not_start_a_second_transaction_on_the_new_leader():
    leader, follower = await ScriptedBackend(first_id=10, mute=True).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower, leader_failover_timeout=0.3)
    try:
        charger.deliver(start_frame("s1"))  # the old leader takes it and never answers; the follower numbers it 1
        await wait_for(lambda: 1 in follower.transactions)
        await wait_for(lambda: session._ids.snapshot()[0]["backend_ids"].get("follow") == 1)
        await leader.stop()
        await wait_promoted(session)

        charger.deliver(start_frame("s1-retry"))
        reply = await charger.next_reply()
        assert reply == [3, "s1-retry", {"transactionId": 1, "idTagInfo": {"status": "Accepted"}}]
        await asyncio.sleep(0.2)
        assert len(follower.transactions) == 1, "the new leader already had this start; it must not get a second"
        assert sum(1 for c in follower.calls if c[2] == "StartTransaction") == 1
    finally:
        await finish(charger, task, leader, follower)


# --------------------------------------------------------------------------
# Reservation ids and charging profile ids
# --------------------------------------------------------------------------
def reserve_payload(rid):
    return {"connectorId": 1, "expiryDate": "2099-01-01T00:00:00Z", "idTag": "TAG", "reservationId": rid}


def profile_payload(pid):
    return {"connectorId": 1, "csChargingProfiles": {"chargingProfileId": pid, "stackLevel": 0, "chargingProfilePurpose": "TxDefaultProfile"}}


async def command_and_accept(backend, charger, action, payload, mid):
    """The backend sends a command, the charger receives it (returned) and answers Accepted."""
    await backend.command(action, payload, mid)
    frame = await charger.next_reply()
    assert frame[1] == mid
    charger.deliver([3, mid, {"status": "Accepted"}])
    return frame


@pytest.mark.asyncio
async def test_reservation_and_profile_ids_survive_a_failover_without_overwriting_each_other():
    leader, follower = await ScriptedBackend(first_id=2).start(), await ScriptedBackend(first_id=1).start()
    broker, session, charger, task = await start_relay(leader, follower, leader_failover_timeout=0.3)
    try:
        # the old leader sets reservation 5 and profile 1; the charger holds them under the same numbers
        assert (await command_and_accept(leader, charger, "ReserveNow", reserve_payload(5), "r1"))[3]["reservationId"] == 5
        assert (await command_and_accept(leader, charger, "SetChargingProfile", profile_payload(1), "p1"))[3]["csChargingProfiles"]["chargingProfileId"] == 1

        await leader.stop()
        await wait_promoted(session)

        # the new leader numbers its own reservation and profile the same way: they must not replace the old ones
        reserved = (await command_and_accept(follower, charger, "ReserveNow", reserve_payload(5), "r2"))[3]["reservationId"]
        profiled = (await command_and_accept(follower, charger, "SetChargingProfile", profile_payload(1), "p2"))[3]["csChargingProfiles"]["chargingProfileId"]
        assert reserved != 5 and profiled != 1

        # the charger starts on the new leader's reservation: the new leader is told its own number
        charger.deliver([2, "s1", "StartTransaction", {"connectorId": 1, "idTag": "A", "meterStart": 1, "timestamp": "t", "reservationId": reserved}])
        await wait_for(lambda: any(c[1] == "s1" for c in follower.calls))
        assert next(c for c in follower.calls if c[1] == "s1")[3]["reservationId"] == 5
        await charger.next_reply()

        # a start on the OLD leader's reservation must not name a reservation number to the new leader
        charger.deliver([2, "s2", "StartTransaction", {"connectorId": 1, "idTag": "B", "meterStart": 2, "timestamp": "t", "reservationId": 5}])
        await wait_for(lambda: any(c[1] == "s2" for c in follower.calls))
        assert "reservationId" not in next(c for c in follower.calls if c[1] == "s2")[3]
        await charger.next_reply()

        # clearing and cancelling use the charger's numbers
        await follower.command("ClearChargingProfile", {"id": 1}, "c1")
        assert (await charger.next_reply())[3]["id"] == profiled
        await follower.command("CancelReservation", {"reservationId": 5}, "c2")
        assert (await charger.next_reply())[3]["reservationId"] == reserved
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_reservation_made_through_the_rest_api_is_not_overwritten_by_a_backends():
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        sent = asyncio.create_task(session.send_command("ReserveNow", reserve_payload(5), timeout=3, message_id="rest-1"))
        frame = await charger.next_reply()
        assert frame[3]["reservationId"] == 5
        charger.deliver([3, "rest-1", {"status": "Accepted"}])
        assert (await sent).status == "success"

        frame = await command_and_accept(leader, charger, "ReserveNow", reserve_payload(5), "r1")
        assert frame[3]["reservationId"] != 5, "the REST API already holds reservation 5 on the charger"
    finally:
        await finish(charger, task, leader, follower)


# --------------------------------------------------------------------------
# Switching it on and off
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mapping_is_on_by_default_when_there_is_a_follower():
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    broker, session, charger, task = await start_relay(leader, follower)
    try:
        assert session._ids is not None
        assert broker.transaction_tables[("OrgA", "CP1")] is session._ids
    finally:
        await finish(charger, task, leader, follower)


@pytest.mark.asyncio
async def test_a_single_backend_has_no_table_and_nothing_changes():
    leader = await ScriptedBackend().start()
    broker = OcppBroker()
    broker.config_data = {
        "organizations": [
            {"name": "OrgA", "connect_to_backend": True, "backends": [{"id": "only", "url": leader.url, "leader": True}]}
        ]
    }
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    try:
        await wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
        session = broker.sessions[("OrgA", "CP1")]
        await wait_for(lambda: session.backend_conn and session.backend_conn.is_ready())
        assert session._ids is None and broker.transaction_tables == {}
        frame = start_frame("s1")
        charger.deliver(frame)
        await wait_for(lambda: leader.received)
        assert leader.received == [json.dumps(frame)]
    finally:
        await charger.close()
        await asyncio.wait_for(task, timeout=5)
        await leader.stop()


@pytest.mark.asyncio
async def test_mapping_can_be_forced_on_for_a_single_backend():
    leader = await ScriptedBackend().start()
    broker = OcppBroker()
    broker.config_data = {
        "organizations": [
            {
                "name": "OrgA",
                "connect_to_backend": True,
                "transaction_ids": {"mapping": True},
                "backends": [{"id": "only", "url": leader.url, "leader": True}],
            }
        ]
    }
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    try:
        await wait_for(lambda: ("OrgA", "CP1") in broker.sessions)
        session = broker.sessions[("OrgA", "CP1")]
        await wait_for(lambda: session.backend_conn and session.backend_conn.is_ready())
        assert session._ids is not None
        assert await begin(charger, "s1") == 1
    finally:
        await charger.close()
        await asyncio.wait_for(task, timeout=5)
        await leader.stop()


@pytest.mark.asyncio
async def test_the_table_is_dropped_when_the_charger_is_gone_and_nothing_is_left_in_it():
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    broker, session, charger, task = await start_relay(leader, follower)
    await charger.close()
    await asyncio.wait_for(task, timeout=5)
    try:
        assert broker.transaction_tables == {}
    finally:
        await leader.stop()
        await follower.stop()


@pytest.mark.parametrize(
    "block",
    [
        {"mapping": "yes"},
        {"dedupe_start": 1},
        {"follower_wait": 0},
        {"follower_wait": "fast"},
        {"retain_closed": -5},
        {"follower_wait": True},
        "on",
    ],
)
def test_bad_transaction_id_settings_are_rejected_at_load_time(block):
    with pytest.raises(ValueError, match="transaction_ids"):
        _apply_defaults({"organizations": [{"name": "A", "transaction_ids": block}]})


def test_good_and_missing_transaction_id_settings_load():
    cfg = {
        "organizations": [
            {"name": "A"},
            {"name": "B", "transaction_ids": None},
            {"name": "C", "transaction_ids": {"mapping": False, "follower_wait": 2.5, "dedupe_start": False, "retain_closed": 60}},
        ]
    }
    _apply_defaults(cfg)
    assert len(cfg["organizations"]) == 3
