"""
Reservation ids and charging profile ids (plans/transaction-ids.md, T5).

A backend chooses these and the charger stores them, exactly like transaction ids:
two backends can number different objects the same, and a backend that takes over
after a failover must not overwrite what the old leader set. Covers the table's
rules, a seeded simulation, and storage.
"""

import copy
import json
import logging
import random

import pytest

from ocpp_broker.transaction_ids import MAX_INT32, TransactionIdTable

P, S, Q = "P", "S", "Q"
FAR = "2099-01-01T00:00:00Z"


class Clocks:
    def __init__(self, mono=1000.0, wall=1_800_000_000.0):
        self.mono, self.wall = mono, wall

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds


@pytest.fixture
def clocks():
    return Clocks()


@pytest.fixture
def table(clocks):
    return TransactionIdTable(
        clock=lambda: clocks.mono, wall_clock=lambda: clocks.wall, retain_closed=100.0, retain_open=1000.0
    )


# --- frame builders --------------------------------------------------------
def reserve(mid, rid, expiry=FAR):
    return [2, mid, "ReserveNow", {"connectorId": 1, "expiryDate": expiry, "idTag": "TAG", "reservationId": rid}]


def cancel(mid, rid):
    return [2, mid, "CancelReservation", {"reservationId": rid}]


def set_profile(mid, pid, tx=None):
    profile = {"chargingProfileId": pid, "stackLevel": 0, "chargingProfilePurpose": "TxDefaultProfile"}
    if tx is not None:
        profile["transactionId"] = tx
    return [2, mid, "SetChargingProfile", {"connectorId": 1, "csChargingProfiles": profile}]


def clear(mid, pid=None):
    return [2, mid, "ClearChargingProfile", {"id": pid} if pid is not None else {"connectorId": 1}]


def remote_start(mid, pid):
    return [2, mid, "RemoteStartTransaction", {"idTag": "TAG", "chargingProfile": {"chargingProfileId": pid, "stackLevel": 0}}]


def start(mid, reservation=None, tag="TAG", meter=0):
    payload = {"connectorId": 1, "idTag": tag, "meterStart": meter, "timestamp": "t0"}
    if reservation is not None:
        payload["reservationId"] = reservation
    return [2, mid, "StartTransaction", payload]


def from_backend(table, backend, frame):
    """What the charger receives for a CALL from ``backend`` (the leader), as a parsed frame."""
    parsed_before = copy.deepcopy(frame)
    plan = table.from_leader(backend, frame, json.dumps(frame))
    assert frame == parsed_before, "input frames are never mutated"
    return json.loads(plan.frame)


def reply(table, mid, status="Accepted", leader=P):
    answer = [3, mid, {"status": status}]
    table.from_charger(answer, json.dumps(answer), leader, {})


def from_charger(table, frame, leader=P, followers=None):
    return table.from_charger(frame, json.dumps(frame), leader, {S: True} if followers is None else followers)


def backends_see(plan):
    """The reservationId (or None) each backend is sent in a StartTransaction."""
    seen = {"leader": json.loads(plan.to_leader)[3].get("reservationId")}
    for key, frame in plan.to_followers:
        seen[key] = json.loads(frame)[3].get("reservationId")
    return seen


# --------------------------------------------------------------------------
# Reservations
# --------------------------------------------------------------------------
def test_the_leaders_reservation_id_reaches_the_charger_unchanged_when_it_is_free(table):
    frame = reserve("r1", 5)
    assert table.from_leader(P, frame, json.dumps(frame)).frame == json.dumps(frame)
    assert table.spaces_snapshot() == {"reservation": [{"id": 5, "backend_ids": {P: 5}, "expires": pytest.approx(4070908800.0)}]}


def test_a_start_names_the_reservation_to_the_backend_that_made_it_and_not_to_the_others(table):
    from_backend(table, P, reserve("r1", 5))
    plan = from_charger(table, start("s1", reservation=5))
    assert backends_see(plan) == {"leader": 5, S: None}, "S never made reservation 5, so it is not told about it"
    assert table.stats["stripped"] == 1


def test_a_reservation_the_table_never_saw_is_passed_to_the_leader_but_not_to_followers(table):
    plan = from_charger(table, start("s1", reservation=9))
    assert backends_see(plan) == {"leader": 9, S: None}, "it may predate the broker, and the leader may know it"


def test_a_start_without_a_reservation_is_untouched(table):
    plan = from_charger(table, start("s1"))
    assert plan.to_leader == json.dumps(start("s1"))
    assert backends_see(plan) == {"leader": None, S: None}
    assert table.stats["stripped"] == 0


def test_a_new_leaders_reservation_that_collides_does_not_overwrite_the_old_leaders(table):
    from_backend(table, P, reserve("r1", 5))
    reply(table, "r1")
    shown = from_backend(table, S, reserve("r2", 5))  # S takes over and numbers its own reservation 5 as well
    assert shown[3]["reservationId"] != 5, "the charger already holds a reservation numbered 5"
    fresh = shown[3]["reservationId"]
    assert table.stats["remapped"] == 1

    # the charger starts on S's reservation: S hears its own number, P (an observer) hears nothing about it
    on_s = from_charger(table, start("s1", reservation=fresh), leader=S, followers={P: True})
    assert backends_see(on_s) == {"leader": 5, P: None}
    # and on P's old reservation: P hears its own number, S does not hear a number that would mean its own
    on_p = from_charger(table, start("s2", reservation=5, tag="OTHER", meter=7), leader=S, followers={P: True})
    assert backends_see(on_p) == {"leader": None, P: 5}


def test_the_same_backend_sending_its_number_again_replaces_its_own_reservation(table):
    from_backend(table, P, reserve("r1", 5))
    reply(table, "r1")
    again = from_backend(table, P, reserve("r2", 5, expiry="2099-06-01T00:00:00Z"))
    assert again[3]["reservationId"] == 5
    assert len(table.spaces_snapshot()["reservation"]) == 1
    assert table.stats["remapped"] == 0


def test_cancelling_names_the_charger_s_reservation_and_frees_the_id_once_accepted(table):
    from_backend(table, P, reserve("r1", 5))
    reply(table, "r1")
    from_backend(table, S, reserve("r2", 5))  # remapped to a fresh id on the charger
    reply(table, "r2")
    fresh = next(r["id"] for r in table.spaces_snapshot()["reservation"] if r["backend_ids"] == {S: 5})

    shown = from_backend(table, S, cancel("c1", 5))
    assert shown[3]["reservationId"] == fresh, "S's number 5 is the charger's reservation `fresh`, not P's 5"
    reply(table, "c1", "Rejected", leader=S)
    assert len(table.spaces_snapshot()["reservation"]) == 2, "refused: still held"
    from_backend(table, S, cancel("c2", 5))
    reply(table, "c2", "Accepted", leader=S)
    assert [r["id"] for r in table.spaces_snapshot()["reservation"]] == [5], "accepted: gone, P's untouched"


def test_cancelling_something_the_table_does_not_know_is_passed_through(table):
    assert from_backend(table, P, cancel("c1", 77))[3]["reservationId"] == 77
    assert table.stats["unmapped"] == 1


def test_a_reservation_the_charger_refuses_is_forgotten_but_a_refused_replacement_keeps_the_old_one(table):
    from_backend(table, P, reserve("r1", 5))
    reply(table, "r1", "Faulted")
    assert table.spaces_snapshot() == {}, "nothing holds id 5"

    from_backend(table, P, reserve("r2", 5))
    reply(table, "r2")
    from_backend(table, P, reserve("r3", 5))  # an attempt to replace it
    reply(table, "r3", "Rejected")
    assert [r["id"] for r in table.spaces_snapshot()["reservation"]] == [5], "the earlier reservation still stands"


def test_a_reservation_answered_with_a_callerror_is_forgotten(table):
    from_backend(table, P, reserve("r1", 5))
    error = [4, "r1", "NotSupported", "", {}]
    table.from_charger(error, json.dumps(error), P, {})
    assert table.spaces_snapshot() == {}


def test_a_reply_nobody_asked_about_changes_nothing(table):
    from_backend(table, P, reserve("r1", 5))
    reply(table, "something-else", "Rejected")
    assert len(table.spaces_snapshot()["reservation"]) == 1


def test_ids_the_broker_gave_the_charger_itself_are_taken(table):
    table.note_command("ReserveNow", {"connectorId": 1, "expiryDate": FAR, "idTag": "T", "reservationId": 5})
    shown = from_backend(table, P, reserve("r1", 5))
    assert shown[3]["reservationId"] != 5, "the REST API already made reservation 5 on the charger"
    plan = from_charger(table, start("s1", reservation=5))
    assert backends_see(plan) == {"leader": None, S: None}, "no backend knows that reservation"

    table.note_command("CancelReservation", {"reservationId": 5})
    assert all(r["id"] != 5 for r in table.spaces_snapshot()["reservation"])


def test_commands_without_ids_and_odd_payloads_are_ignored_by_note_command(table):
    table.note_command("Reset", {"type": "Soft"})
    table.note_command("ReserveNow", "not a dict")
    table.note_command("ReserveNow", {"reservationId": True})
    assert table.spaces_snapshot() == {}


def test_a_reservation_is_forgotten_after_it_expires(table, clocks):
    from_backend(table, P, reserve("r1", 5, expiry="2027-01-15T08:00:00Z"))  # 1_800_000_000 is 2027-01-15 08:00:00 UTC
    clocks.advance(50)
    from_charger(table, start("s1"))
    assert "reservation" in table.spaces_snapshot()
    clocks.advance(200)  # past the expiry date plus retain_closed
    from_charger(table, start("s2", tag="B", meter=1))
    assert table.spaces_snapshot() == {}


@pytest.mark.parametrize("expiry", ["tomorrow", 5, None, "2099-13-45T00:00:00Z"])
def test_an_unreadable_expiry_date_is_ignored(table, expiry):
    frame = reserve("r1", 5)
    frame[3]["expiryDate"] = expiry
    assert from_backend(table, P, frame)[3]["reservationId"] == 5
    assert table.spaces_snapshot()["reservation"][0]["expires"] is None


def test_expiry_dates_without_a_zone_count_as_utc(table):
    from_backend(table, P, reserve("r1", 5, expiry="2099-01-01T00:00:00"))
    assert table.spaces_snapshot()["reservation"][0]["expires"] == pytest.approx(4070908800.0)


# --------------------------------------------------------------------------
# Charging profiles
# --------------------------------------------------------------------------
def test_a_profile_keeps_the_backends_id_and_is_cleared_by_it(table):
    assert from_backend(table, P, set_profile("p1", 1))[3]["csChargingProfiles"]["chargingProfileId"] == 1
    reply(table, "p1")
    assert from_backend(table, P, clear("c1", 1))[3]["id"] == 1
    reply(table, "c1", "Unknown")
    assert len(table.spaces_snapshot()["profile"]) == 1, "the charger did not clear anything"
    from_backend(table, P, clear("c2", 1))
    reply(table, "c2", "Accepted")
    assert table.spaces_snapshot() == {}


def test_a_new_leaders_profile_does_not_replace_the_old_leaders(table):
    from_backend(table, P, set_profile("p1", 1))
    reply(table, "p1")
    shown = from_backend(table, S, set_profile("p2", 1))
    fresh = shown[3]["csChargingProfiles"]["chargingProfileId"]
    assert fresh != 1, "id 1 is P's profile; the same number would silently replace it on the charger"
    reply(table, "p2", leader=S)

    cleared = from_backend(table, S, clear("c1", 1))
    assert cleared[3]["id"] == fresh, "S's `1` is the charger's `fresh`"
    reply(table, "c1", "Accepted", leader=S)
    assert [r["id"] for r in table.spaces_snapshot()["profile"]] == [1], "P's profile is untouched"


def test_clearing_by_criteria_needs_no_translation(table):
    from_backend(table, P, set_profile("p1", 1))
    frame = clear("c1")
    assert table.from_leader(S, frame, json.dumps(frame)).frame == json.dumps(frame)


def test_a_profile_naming_a_transaction_has_both_ids_translated_in_one_frame(table):
    # a transaction the charger holds as 7 that S (the new leader) calls 3
    table.from_charger(start("s1"), json.dumps(start("s1")), P, {S: True})
    table.from_leader(P, [3, "s1", {"transactionId": 7, "idTagInfo": {"status": "Accepted"}}], "x")
    table.from_follower(S, [3, "s1", {"transactionId": 3, "idTagInfo": {"status": "Accepted"}}])
    table.leader_changed(S)
    from_backend(table, P, set_profile("p0", 1))  # P (still known) holds profile 1
    reply(table, "p0")

    shown = from_backend(table, S, set_profile("p1", 1, tx=3))
    profile = shown[3]["csChargingProfiles"]
    assert profile["transactionId"] == 7 and profile["chargingProfileId"] != 1


def test_remote_start_assigns_its_charging_profile_id_like_set_charging_profile(table):
    from_backend(table, P, set_profile("p1", 1))
    reply(table, "p1")
    shown = from_backend(table, S, remote_start("rs1", 1))
    assert shown[3]["chargingProfile"]["chargingProfileId"] != 1
    assert shown[3]["idTag"] == "TAG", "nothing else is touched"


def test_a_refused_profile_is_forgotten(table):
    from_backend(table, P, set_profile("p1", 1))
    reply(table, "p1", "Rejected")
    assert table.spaces_snapshot() == {}


@pytest.mark.parametrize("bad", [True, "1", 1.5, None])
def test_ids_that_are_not_integers_are_left_alone(table, bad):
    frame = set_profile("p1", bad)
    assert table.from_leader(P, frame, json.dumps(frame)).frame == json.dumps(frame)
    frame = reserve("r1", bad)
    assert table.from_leader(P, frame, json.dumps(frame)).frame == json.dumps(frame)
    assert table.spaces_snapshot() == {}


def test_fresh_ids_stay_in_the_32_bit_range(table):
    from_backend(table, P, reserve("r1", MAX_INT32))
    shown = from_backend(table, S, reserve("r2", MAX_INT32))
    assert 0 < shown[3]["reservationId"] < MAX_INT32


def test_messages_that_quote_no_such_id_are_untouched(table):
    for frame in ([2, "x", "Reset", {"type": "Soft"}], [2, "y", "UnlockConnector", {"connectorId": 1}], [2, "z", "GetConfiguration", {}]):
        assert table.from_leader(P, frame, json.dumps(frame)).frame == json.dumps(frame)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
class Journal:
    def __init__(self, table):
        self.docs, self.expires = {}, {}
        table.on_change = self.on_change

    def on_change(self, uid, doc, expires):
        if doc is None:
            self.docs.pop(uid, None)
        else:
            self.docs[uid], self.expires[uid] = doc, expires


def test_reservations_and_profiles_are_stored_and_come_back(clocks):
    table = TransactionIdTable(clock=lambda: clocks.mono, wall_clock=lambda: clocks.wall, retain_closed=100.0, retain_open=1000.0)
    journal = Journal(table)
    from_backend(table, P, reserve("r1", 5, expiry="2027-01-15T09:00:00Z"))
    from_backend(table, S, reserve("r2", 5))
    from_backend(table, P, set_profile("p1", 1))
    assert {d["kind"] for d in journal.docs.values()} == {"reservation", "profile"}
    assert all(isinstance(pair, list) for d in journal.docs.values() for pair in d["backend_ids"])

    fresh = TransactionIdTable(clock=lambda: 3.0, wall_clock=lambda: clocks.wall + 10, retain_closed=100.0, retain_open=1000.0)
    assert fresh.restore(list(journal.docs.values()), {P, S}) == 3
    after = from_backend(fresh, S, cancel("c1", 5))
    assert after[3]["reservationId"] != 5, "S's reservation 5 is still the one the charger knows under another number"
    assert from_backend(fresh, P, clear("c2", 1))[3]["id"] == 1


def test_stored_expiry_follows_the_kind_of_record(clocks):
    table = TransactionIdTable(clock=lambda: clocks.mono, wall_clock=lambda: clocks.wall, retain_closed=100.0, retain_open=1000.0)
    journal = Journal(table)
    from_backend(table, P, reserve("r1", 5, expiry="2027-01-15T09:00:00Z"))
    from_backend(table, P, set_profile("p1", 1))
    by_kind = {d["kind"]: journal.expires[uid] for uid, d in journal.docs.items()}
    assert by_kind["reservation"] == pytest.approx(1_800_003_600.0 + 100.0), "its expiry date plus retain_closed"
    assert by_kind["profile"] == pytest.approx(clocks.wall + 1000.0), "retain_open from now"


def test_expired_and_foreign_records_are_not_restored(clocks, caplog):
    table = TransactionIdTable(clock=lambda: clocks.mono, wall_clock=lambda: clocks.wall, retain_closed=100.0, retain_open=1000.0)
    journal = Journal(table)
    from_backend(table, P, reserve("r1", 5, expiry="2027-01-15T08:30:00Z"))
    from_backend(table, P, set_profile("p1", 1))
    from_backend(table, S, set_profile("p2", 2))
    docs = list(journal.docs.values())

    later = TransactionIdTable(clock=lambda: 1.0, wall_clock=lambda: clocks.wall + 5000, retain_closed=100.0, retain_open=10_000.0)
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.transaction_ids"):
        assert later.restore(docs, {P}) == 2, "the reservation ended long ago; S is no longer configured"
    snapshot = later.spaces_snapshot()
    assert "reservation" not in snapshot
    assert [r["backend_ids"] for r in snapshot["profile"]] == [{P: 1}, {}], "S's number is dropped, the profile stays taken"


def test_a_live_record_wins_over_a_stored_one(clocks):
    old = TransactionIdTable(clock=lambda: clocks.mono, wall_clock=lambda: clocks.wall)
    journal = Journal(old)
    from_backend(old, P, set_profile("p1", 1))
    fresh = TransactionIdTable(clock=lambda: 1.0, wall_clock=lambda: clocks.wall + 1)
    from_backend(fresh, S, set_profile("p2", 1))
    assert fresh.restore(list(journal.docs.values()), {P, S}) == 0
    assert fresh.spaces_snapshot()["profile"][0]["backend_ids"] == {S: 1}


def test_released_and_forgotten_records_are_removed_from_storage(clocks):
    table = TransactionIdTable(clock=lambda: clocks.mono, wall_clock=lambda: clocks.wall, retain_closed=100.0, retain_open=1000.0)
    journal = Journal(table)
    from_backend(table, P, set_profile("p1", 1))
    reply(table, "p1")
    assert len(journal.docs) == 1
    from_backend(table, P, clear("c1", 1))
    reply(table, "c1", "Accepted")
    assert journal.docs == {}

    from_backend(table, P, set_profile("p2", 2))
    clocks.advance(1500)  # nobody used it for longer than retain_open
    assert table.is_idle()
    assert journal.docs == {}


def test_a_table_is_not_idle_while_it_holds_reservations_or_profiles(table):
    from_backend(table, P, set_profile("p1", 1))
    assert not table.is_idle()


# --------------------------------------------------------------------------
# Simulation: the charger must never have one backend's object overwritten by another's,
# and a backend must only ever be told the number it gave its own reservation
# --------------------------------------------------------------------------
class World:
    KEYS = (P, S, Q)

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.table = TransactionIdTable(clock=lambda: 1000.0, wall_clock=lambda: 1_800_000_000.0)
        self.leader = P
        self.objects = {}  # (space, charger id) -> (backend, its number): what the charger stores
        self.sent_numbers = {}  # message id -> the number the backend put in that command
        self.msgs = 0
        self.violations = []

    def mid(self):
        self.msgs += 1
        return f"m{self.msgs}"

    def holder_ids(self, space, backend):
        return [bid for (sp, _), (b, bid) in self.objects.items() if sp == space and b == backend]

    def numbers(self):
        return self.rng.randint(1, 4)  # a tiny range, so different backends keep colliding

    def charger_applies(self, frame, backend, space, path_value, mode):
        """The charger receives ``frame``; returns the status it answers with."""
        mid = frame[1]
        if mode == "assign":
            holder = self.objects.get((space, path_value))
            sent = self.sent_numbers[mid]
            if holder is not None and holder != (backend, sent):
                self.violations.append(f"{backend}'s {space} {sent} overwrote {holder[0]}'s {holder[1]} (charger id {path_value})")
            if self.rng.random() < 0.2:
                return "Rejected"
            self.objects[(space, path_value)] = (backend, sent)
            return "Accepted"
        holder = self.objects.get((space, path_value))
        sent = self.sent_numbers[mid]
        if holder != (backend, sent):
            self.violations.append(f"{backend}'s cancel of {space} {sent} hit {holder} (charger id {path_value})")
            return "Rejected"
        self.objects.pop((space, path_value))
        return "Accepted"

    def step(self):
        rng = self.rng
        event =rng.choices(["reserve", "cancel", "profile", "clear", "start", "promote"], weights=[6, 3, 6, 3, 5, 2])[0]
        leader = self.leader
        if event in ("reserve", "profile"):
            space = "reservation" if event == "reserve" else "profile"
            number = self.numbers()
            mid = self.mid()
            frame = reserve(mid, number) if event == "reserve" else set_profile(mid, number)
            self.sent_numbers[mid] = number
            out = json.loads(self.table.from_leader(leader, frame, json.dumps(frame)).frame)
            cid = out[3]["reservationId"] if event == "reserve" else out[3]["csChargingProfiles"]["chargingProfileId"]
            status = self.charger_applies(out, leader, space, cid, "assign")
            answer = [3, mid, {"status": status}]
            self.table.from_charger(answer, json.dumps(answer), leader, {})
        elif event in ("cancel", "clear"):
            space = "reservation" if event == "cancel" else "profile"
            own = self.holder_ids(space, leader)
            if not own:
                return
            number = rng.choice(sorted(own))
            mid = self.mid()
            frame = cancel(mid, number) if event == "cancel" else clear(mid, number)
            self.sent_numbers[mid] = number
            out = json.loads(self.table.from_leader(leader, frame, json.dumps(frame)).frame)
            cid = out[3]["reservationId"] if event == "cancel" else out[3]["id"]
            status = self.charger_applies(out, leader, space, cid, "release")
            answer = [3, mid, {"status": status}]
            self.table.from_charger(answer, json.dumps(answer), leader, {})
        elif event == "start":
            live = sorted(cid for (sp, cid) in self.objects if sp == "reservation")
            if not live:
                return
            cid = rng.choice(live)
            holder = self.objects[("reservation", cid)]
            self.msgs += 1
            frame = start(f"s{self.msgs}", reservation=cid, tag=f"t{self.msgs}", meter=self.msgs)
            plan = self.table.from_charger(frame, json.dumps(frame), leader, {k: True for k in self.KEYS if k != leader})
            received = {leader: json.loads(plan.to_leader)[3].get("reservationId")}
            received.update({k: json.loads(f)[3].get("reservationId") for k, f in plan.to_followers})
            for backend, told in received.items():
                if told is None:
                    continue
                if holder != (backend, told):
                    self.violations.append(f"{backend} was told reservation {told} for {holder} (charger id {cid})")
        elif event == "promote":
            self.leader = rng.choice([k for k in self.KEYS if k != leader])
            self.table.leader_changed(self.leader)


@pytest.mark.parametrize("seed", range(300))
def test_random_runs_never_overwrite_or_misname_a_reservation_or_profile(seed):
    world = World(seed)
    for _ in range(80):
        world.step()
        assert not world.violations, f"seed {seed}: {world.violations[:3]}"


def test_the_simulation_finds_the_problem_when_ids_are_not_translated(monkeypatch):
    """A guard for the guard: with every id passed straight through, some seed must fail."""
    monkeypatch.setattr(TransactionIdTable, "_space_fresh", lambda self, space: 1)  # collisions land on id 1
    failures = 0
    for seed in range(60):
        world = World(seed)
        for _ in range(80):
            world.step()
        failures += bool(world.violations)
    assert failures > 0
