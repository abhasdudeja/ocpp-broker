"""
TransactionIdTable: the pure logic behind transaction id mapping in relay mode.

Frames are driven through the table by hand, with a fake clock, so every rule of
plans/transaction-ids.md can be checked without sockets.
"""

import copy
import json

import pytest

from ocpp_broker.transaction_ids import TransactionIdTable, backend_keys

LEADER, FOLLOW = "P", "S"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def table(clock):
    return TransactionIdTable(follower_wait=5.0, stale_start=60.0, retain_closed=100.0, clock=clock)


def frame(*parts):
    return json.dumps(list(parts))


def start_call(mid, tag="TAG", connector=1, meter=0, ts="2026-10-04T10:00:00Z"):
    return [2, mid, "StartTransaction", {"connectorId": connector, "idTag": tag, "meterStart": meter, "timestamp": ts}]


def charger(table, parsed, followers=None, leader=LEADER):
    """Feed a charger frame to the table; returns the plan."""
    return table.from_charger(parsed, json.dumps(parsed), leader, {FOLLOW: True} if followers is None else followers)


def leader_result(table, mid, tx_id, leader=LEADER, status="Accepted"):
    parsed = [3, mid, {"transactionId": tx_id, "idTagInfo": {"status": status}}]
    return table.from_leader(leader, parsed, json.dumps(parsed))


def follower_result(table, mid, tx_id, follower=FOLLOW):
    return table.from_follower(follower, [3, mid, {"transactionId": tx_id, "idTagInfo": {"status": "Accepted"}}])


def begin(table, mid, leader_id, follower_id=None, **kwargs):
    """A transaction started on both backends; returns the id the charger holds."""
    charger(table, start_call(mid, **kwargs))
    answer = leader_result(table, mid, leader_id)
    if follower_id is not None:
        follower_result(table, mid, follower_id)
    return json.loads(answer.frame)[2]["transactionId"]


def meter(tx, mid="mv"):
    return [2, mid, "MeterValues", {"connectorId": 1, "transactionId": tx, "meterValue": []}]


def stop(tx, mid="stop"):
    return [2, mid, "StopTransaction", {"transactionId": tx, "meterStop": 10, "timestamp": "t"}]


def ids_in(frames):
    return [json.loads(f)[3].get("transactionId") for f in frames]


# --------------------------------------------------------------------------
# The charger sees the leader's id; each backend is spoken to in its own
# --------------------------------------------------------------------------
def test_the_leaders_id_reaches_the_charger_untouched_when_it_is_free(table):
    parsed = start_call("s1")
    plan = charger(table, parsed)
    assert plan.to_leader == json.dumps(parsed)
    answer = leader_result(table, "s1", 7)
    assert json.loads(answer.frame) == [3, "s1", {"transactionId": 7, "idTagInfo": {"status": "Accepted"}}]
    assert table.stats["remapped"] == 0 and table.stats["rewritten"] == 0


def test_a_follower_is_told_its_own_id_not_the_leaders(table):
    tx = begin(table, "s1", leader_id=7, follower_id=3)
    assert tx == 7
    plan = charger(table, meter(7))
    assert plan.to_leader == json.dumps(meter(7))  # leader traffic is byte-identical
    assert ids_in(f for _, f in plan.to_followers) == [3]
    stop_plan = charger(table, stop(7))
    assert ids_in(f for _, f in stop_plan.to_followers) == [3]


def test_input_frames_are_never_mutated(table):
    begin(table, "s1", leader_id=7, follower_id=3)
    parsed = meter(7)
    before = copy.deepcopy(parsed)
    charger(table, parsed)
    assert parsed == before


def test_frames_without_a_transaction_id_are_copied_unchanged(table):
    parsed = [2, "hb", "Heartbeat", {}]
    plan = charger(table, parsed)
    assert plan.to_leader == json.dumps(parsed)
    assert plan.to_followers == [(FOLLOW, json.dumps(parsed))]


def test_results_and_errors_from_the_charger_go_to_the_leader_only(table):
    for parsed in ([3, "x", {"status": "Accepted"}], [4, "x", "NotImplemented", "", {}]):
        plan = charger(table, parsed)
        assert plan.to_leader == json.dumps(parsed) and plan.to_followers == []


def test_meter_values_without_a_transaction_id_are_plain_copies(table):
    parsed = [2, "mv", "MeterValues", {"connectorId": 0, "meterValue": []}]
    plan = charger(table, parsed)
    assert plan.to_followers == [(FOLLOW, json.dumps(parsed))]


# --------------------------------------------------------------------------
# The "lockstep illusion": a follower that missed a start must not be sent ids
# that belong to other transactions
# --------------------------------------------------------------------------
def test_a_follower_that_missed_a_start_is_not_sent_the_wrong_transaction(table):
    a = begin(table, "sA", leader_id=1, follower_id=1, tag="A")
    # the follower is down for B: it never sees the start
    charger(table, start_call("sB", tag="B", meter=5), followers={FOLLOW: False})
    b = json.loads(leader_result(table, "sB", 2).frame)[2]["transactionId"]
    c = begin(table, "sC", leader_id=3, follower_id=2, tag="C", meter=9)  # the follower numbers C as 2
    assert (a, b, c) == (1, 2, 3)

    stop_b = charger(table, stop(b))
    assert stop_b.to_leader == json.dumps(stop(b))
    assert stop_b.to_followers == [], "the copy would have ended the follower's own transaction 2, which is C"

    stop_c = charger(table, stop(c))
    assert ids_in(f for _, f in stop_c.to_followers) == [2]
    assert table.stats["skipped"] >= 1


def test_an_id_the_table_never_saw_is_not_copied_to_followers(table):
    plan = charger(table, stop(99))
    assert plan.to_leader == json.dumps(stop(99))  # the leader may know it (started before the table)
    assert plan.to_followers == []


# --------------------------------------------------------------------------
# A follower that answers late: copies wait, in order
# --------------------------------------------------------------------------
def test_copies_wait_for_the_followers_answer_and_keep_their_order(table):
    start = charger(table, start_call("s1"))
    assert [json.loads(f)[1] for _, f in start.to_followers] == ["s1"]
    leader_result(table, "s1", 7)

    held = charger(table, meter(7, "mv1"))
    status = charger(table, [2, "sn", "StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"}])
    stopped = charger(table, stop(7, "st"))
    assert held.to_followers == [] and status.to_followers == [] and stopped.to_followers == []
    assert table.stats["held"] == 1  # counted once, not once per attempt

    released = follower_result(table, "s1", 41)
    assert [json.loads(f)[1] for _, f in released] == ["mv1", "sn", "st"]
    assert ids_in([released[0][1], released[2][1]]) == [41, 41]


def test_giving_up_on_a_silent_follower_releases_what_is_queued_behind_it(table, clock):
    charger(table, start_call("s1"))
    leader_result(table, "s1", 7)
    charger(table, meter(7, "mv1"))
    charger(table, [2, "hb", "Heartbeat", {}])
    assert table.next_expiry_in() == pytest.approx(5.0)

    clock.now += 5.1
    assert table.next_expiry_in() == 0.0
    released = table.expire_holds()
    assert [json.loads(f)[1] for _, f in released] == ["hb"], "the blocked MeterValues is dropped, the heartbeat goes on"
    assert table.next_expiry_in() is None

    later = charger(table, stop(7, "st"))
    assert later.to_followers == [], "a follower with no id is skipped, not sent the leader's id"
    assert table.snapshot()[0]["degraded"] == [FOLLOW]


def test_a_follower_that_answers_with_an_error_is_marked_degraded(table):
    charger(table, start_call("s1"))
    leader_result(table, "s1", 7)
    charger(table, meter(7, "mv1"))
    released = table.from_follower(FOLLOW, [4, "s1", "InternalError", "no", {}])
    assert released == []
    assert charger(table, stop(7)).to_followers == []


def test_a_follower_answer_to_something_else_is_ignored(table):
    charger(table, start_call("s1"))
    assert table.from_follower(FOLLOW, [3, "unknown", {"transactionId": 5}]) == []
    assert table.from_follower(FOLLOW, [2, "cmd", "Reset", {}]) == []
    assert table.from_follower("other", [3, "s1", {"transactionId": 5}]) == []


def test_the_copy_queue_is_bounded(clock):
    small = TransactionIdTable(max_held=2, clock=clock)
    charger(small, start_call("s1"))
    leader_result(small, "s1", 7)
    for i in range(4):
        charger(small, meter(7, f"mv{i}"))
    assert small.stats["overflow"] >= 1


# --------------------------------------------------------------------------
# Failover: the promoted follower speaks its own ids, the charger keeps its
# --------------------------------------------------------------------------
def test_after_promotion_the_new_leader_gets_its_own_id_and_its_commands_map_back(table):
    tx = begin(table, "s1", leader_id=7, follower_id=3)
    table.leader_changed("S")

    stop_plan = table.from_charger(stop(tx), json.dumps(stop(tx)), "S", {"P": True})
    assert ids_in([stop_plan.to_leader]) == [3]  # the new leader knows it as 3

    command = [2, "rs", "RemoteStopTransaction", {"transactionId": 3}]
    mapped = table.from_leader("S", command, json.dumps(command))
    assert json.loads(mapped.frame)[3]["transactionId"] == 7  # the charger holds 7


def test_a_new_leader_id_that_the_charger_already_holds_is_remapped(table):
    old = begin(table, "s1", leader_id=7, follower_id=3)
    table.leader_changed("S")
    table.from_charger(start_call("s2", tag="B", meter=50), json.dumps(start_call("s2", tag="B", meter=50)), "S", {"P": True})
    answer = leader_result(table, "s2", 7, leader="S")
    shown = json.loads(answer.frame)[2]["transactionId"]
    assert old == 7 and shown != 7, "the charger must not hold two live transactions numbered 7"
    assert table.stats["remapped"] == 1

    again = table.from_charger(stop(shown), json.dumps(stop(shown)), "S", {"P": True})
    assert ids_in([again.to_leader]) == [7]  # the new leader's own number for its transaction
    first = table.from_charger(stop(old), json.dumps(stop(old)), "S", {"P": True})
    assert ids_in([first.to_leader]) == [3]


def test_a_transaction_the_new_leader_never_saw_is_forwarded_unchanged_and_flagged(table):
    tx = begin(table, "s1", leader_id=7)  # the follower was down for the start
    table.leader_changed("S")
    plan = table.from_charger(stop(tx), json.dumps(stop(tx)), "S", {"P": True})
    assert plan.to_leader == json.dumps(stop(tx))
    assert table.snapshot()[0]["degraded"] == ["S"]


def test_the_old_leader_that_comes_back_as_a_follower_is_not_sent_unknown_ids(table):
    begin(table, "s1", leader_id=7, follower_id=3)
    table.leader_changed("S")
    # a transaction that starts on the new leader while the old one is away
    start = start_call("s2", tag="B", meter=50)
    table.from_charger(start, json.dumps(start), "S", {"P": False})
    new_tx = json.loads(leader_result(table, "s2", 4, leader="S").frame)[2]["transactionId"]
    plan = table.from_charger(stop(new_tx), json.dumps(stop(new_tx)), "S", {"P": True})
    assert plan.to_followers == [], "the old leader never saw this start"


def test_promotion_gives_up_on_copies_queued_for_the_promoted_follower(table):
    charger(table, start_call("s1"))
    leader_result(table, "s1", 7)
    charger(table, meter(7))  # queued behind the follower's missing answer
    table.leader_changed("S")
    assert table.expire_holds() == []
    assert table.next_expiry_in() is None


def test_a_start_waiting_for_the_old_leader_is_forgotten_on_promotion(table):
    charger(table, start_call("s1"))  # the old leader never answers
    table.leader_changed("S")
    retry = table.from_charger(start_call("s1b"), json.dumps(start_call("s1b")), "S", {"P": True})
    assert retry.to_leader == json.dumps(start_call("s1b")), "the retry must reach the new leader, not wait for the old one"


# --------------------------------------------------------------------------
# Retries
# --------------------------------------------------------------------------
def test_a_retried_start_after_the_leader_answered_gets_the_same_id_without_a_second_start(table):
    first = begin(table, "s1", leader_id=7, follower_id=3)
    retry = charger(table, start_call("s1-retry"))
    assert retry.to_leader is None and retry.to_followers == []
    assert json.loads(retry.reply) == [3, "s1-retry", {"transactionId": first, "idTagInfo": {"status": "Accepted"}}]
    assert table.stats["deduped"] == 1


def test_a_retry_while_the_leader_is_still_working_is_answered_together(table):
    charger(table, start_call("s1"))
    retry = charger(table, start_call("s1-retry"))
    assert retry.to_leader is None and retry.reply is None
    answer = leader_result(table, "s1", 7)
    assert [json.loads(f)[1] for f in [answer.frame, *answer.extra]] == ["s1", "s1-retry"]
    assert {json.loads(f)[2]["transactionId"] for f in [answer.frame, *answer.extra]} == {7}


def test_a_leader_error_answers_the_waiting_retry_too_and_forgets_the_attempt(table):
    charger(table, start_call("s1"))
    charger(table, start_call("s1-retry"))
    error = [4, "s1", "InternalError", "boom", {}]
    answer = table.from_leader(LEADER, error, json.dumps(error))
    assert answer.frame == json.dumps(error)
    assert json.loads(answer.extra[0]) == [4, "s1-retry", "InternalError", "boom", {}]


def test_a_start_the_broker_gave_up_on_can_be_retried_without_a_second_follower_copy(table):
    first = charger(table, start_call("s1"))
    assert len(first.to_followers) == 1  # the follower already has this start
    assert table.start_failed("s1") == []  # the broker answered the charger with a CALLERROR
    retry = charger(table, start_call("s1-retry"))
    assert retry.to_leader == json.dumps(start_call("s1-retry"))
    assert retry.to_followers == [], "the follower must not get a second start for the same charging session"


def test_start_failed_answers_waiting_retries(table):
    charger(table, start_call("s1"))
    charger(table, start_call("s1-retry"))
    extra = table.start_failed("s1")
    assert [json.loads(f)[:3] for f in extra] == [[4, "s1-retry", "InternalError"]]


def test_start_failed_for_an_unknown_message_is_a_no_op(table):
    assert table.start_failed("nope") == []


def test_a_stale_first_attempt_does_not_swallow_a_retry_forever(table, clock):
    charger(table, start_call("s1"))
    clock.now += 61
    retry = charger(table, start_call("s1-retry"))
    assert retry.to_leader is not None


def test_different_starts_are_different_transactions(table):
    a = begin(table, "s1", leader_id=1, follower_id=1, tag="A")
    b = begin(table, "s2", leader_id=2, follower_id=2, tag="B")
    assert (a, b) == (1, 2)


def test_without_dedupe_a_retry_is_just_another_start(clock):
    plain = TransactionIdTable(dedupe_start=False, clock=clock)
    charger(plain, start_call("s1"))
    leader_result(plain, "s1", 7)
    retry = charger(plain, start_call("s1-retry"))
    assert retry.to_leader is not None and retry.reply is None


def test_a_malformed_start_result_passes_through_untouched(table):
    charger(table, start_call("s1"))
    odd = [3, "s1", {"idTagInfo": {"status": "Accepted"}}]
    assert table.from_leader(LEADER, odd, json.dumps(odd)).frame == json.dumps(odd)
    again = charger(table, start_call("s1-retry"))
    assert again.to_leader is not None  # nothing was recorded, so a retry goes through


# --------------------------------------------------------------------------
# Backend commands that quote a transaction id
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "action,payload,path",
    [
        ("RemoteStopTransaction", {"transactionId": 3}, ("transactionId",)),
        ("SetChargingProfile", {"connectorId": 1, "csChargingProfiles": {"chargingProfileId": 1, "transactionId": 3}}, ("csChargingProfiles", "transactionId")),
        ("RemoteStartTransaction", {"idTag": "X", "chargingProfile": {"chargingProfileId": 1, "transactionId": 3}}, ("chargingProfile", "transactionId")),
    ],
)
def test_leader_commands_quote_the_charger_visible_id(table, action, payload, path):
    begin(table, "s1", leader_id=7, follower_id=3)
    table.leader_changed("S")  # the follower, which knows the transaction as 3, leads now
    call = [2, "cmd", action, payload]
    mapped = json.loads(table.from_leader("S", call, json.dumps(call)).frame)
    node = mapped[3]
    for part in path[:-1]:
        node = node[part]
    assert node[path[-1]] == 7
    assert call[3] == payload  # untouched input


def test_a_command_quoting_an_unknown_id_passes_through(table):
    call = [2, "cmd", "RemoteStopTransaction", {"transactionId": 55}]
    assert table.from_leader(LEADER, call, json.dumps(call)).frame == json.dumps(call)
    assert table.stats["unmapped"] == 1


def test_commands_without_ids_and_other_leader_frames_pass_through(table):
    for parsed in ([2, "c", "Reset", {"type": "Soft"}], [3, "nothing", {}], [2, "c"], "junk"):
        raw = json.dumps(parsed)
        assert table.from_leader(LEADER, parsed, raw).frame == raw


# --------------------------------------------------------------------------
# Lifetime
# --------------------------------------------------------------------------
def test_a_stop_closes_the_transaction_but_a_retried_stop_still_maps(table):
    tx = begin(table, "s1", leader_id=7, follower_id=3)
    charger(table, stop(tx))
    assert table.snapshot()[0]["state"] == "closed"
    retried = charger(table, stop(tx, "stop-retry"))
    assert ids_in(f for _, f in retried.to_followers) == [3]


def test_closed_transactions_are_forgotten_after_the_retention_period(table, clock):
    tx = begin(table, "s1", leader_id=7, follower_id=3)
    charger(table, stop(tx))
    assert not table.is_idle()
    clock.now += 101
    assert table.is_idle()
    assert table.snapshot() == []


def test_a_closed_transaction_keeps_its_id_reserved_while_it_is_retained(table):
    tx = begin(table, "s1", leader_id=7, follower_id=3)
    charger(table, stop(tx))
    second = begin(table, "s2", leader_id=7, follower_id=4, tag="B", meter=99)
    assert second != 7, "a late retry of the old stop must not land on the new transaction"


def test_stale_unanswered_starts_are_dropped(table, clock):
    charger(table, start_call("s1"))
    assert not table.is_idle()
    clock.now += 400
    assert table.is_idle()


def test_snapshot_describes_each_transaction(table):
    begin(table, "s1", leader_id=7, follower_id=3)
    assert table.snapshot() == [
        {"transaction_id": 7, "state": "open", "backend_ids": {"P": 7, "S": 3}, "degraded": [], "awaiting": []}
    ]


def test_a_fresh_id_stays_in_the_32_bit_range(table):
    begin(table, "s1", leader_id=2**31 - 1, follower_id=1)
    table.leader_changed("S")
    start = start_call("s2", tag="B", meter=1)
    table.from_charger(start, json.dumps(start), "S", {"P": True})
    shown = json.loads(leader_result(table, "s2", 2**31 - 1, leader="S").frame)[2]["transactionId"]
    assert 0 < shown <= 2**31 - 1 and shown != 2**31 - 1


# --------------------------------------------------------------------------
# Backend keys
# --------------------------------------------------------------------------
def test_backend_keys_prefer_the_id_then_the_url_and_stay_unique():
    keys = backend_keys(
        [
            {"id": "primary", "url": "ws://a"},
            {"url": "ws://b"},
            {"id": "primary", "url": "ws://c"},
            {"url": "ws://b"},
        ]
    )
    assert keys == ["primary", "ws://b", "primary#2", "ws://b#2"]
