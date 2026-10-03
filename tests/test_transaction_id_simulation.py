"""
Randomised simulation of one charger and three backends (plans/transaction-ids.md, S9).

Backends number transactions differently, go down, answer late, get promoted;
the charger retries starts. After every step the invariants that matter must hold:

* a backend is never sent an id that belongs to a *different* charging session
  than the frame is about (the silent misattribution bug). The one exception is a
  leader the table has no id for (it missed the start, or its answer never
  arrived): it gets the charger's id unchanged, with a warning, by design;
* a start is never sent again to a backend whose answer the broker already holds
  (so no second transaction for one charging session; a backend that has not
  answered may be sent it again, because the broker cannot tell lost from slow);
* the charger never holds the same id for two live transactions, and a retried
  start is always answered with the id it already has;
* the charger gets at most one answer per message id.
"""

import json
import random
from collections import deque

import pytest

from ocpp_broker.transaction_ids import TransactionIdTable

KEYS = ("A", "B", "C")


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class Backend:
    def __init__(self, key, rng):
        self.key = key
        self.next_id = rng.randint(1, 5)
        self.step = rng.choice([1, 1, 2, 3])
        self.txs = {}  # its id -> charging session
        self.saw = set()  # charging sessions whose start it processed
        self.answered = set()  # of those, the ones whose answer reached the broker
        self.inbox = deque()
        self.outbox = deque()
        self.ready = True


class World:
    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.clock = Clock()
        self.table = TransactionIdTable(follower_wait=5.0, stale_start=30.0, retain_closed=10_000.0, clock=self.clock)
        self.backends = {k: Backend(k, self.rng) for k in KEYS}
        self.leader = "A"
        self.sessions = 0
        self.msgs = 0
        self.intent = {}  # message id -> charging session it concerns
        self.start_msg = {}  # message id of a start -> charging session
        self.held_id = {}  # charging session -> the id the charger holds
        self.stopped = set()
        self.unanswered = set()  # sessions the charger has started but not heard back about
        self.replied = set()
        self.unmapped_to_leader = set()  # (message id, backend): sent to the leader with no id known for it
        self.violations = []

    # --- helpers --------------------------------------------------------
    def new_msg(self, prefix):
        self.msgs += 1
        return f"{prefix}{self.msgs}"

    def followers(self):
        return {k: b.ready for k, b in self.backends.items() if k != self.leader}

    def fail(self, text):
        self.violations.append(text)

    # --- the charger ----------------------------------------------------
    def send(self, frame):
        raw = json.dumps(frame)
        plan = self.table.from_charger(frame, raw, self.leader, self.followers())
        if frame[2] == "StartTransaction":
            # A start may be sent again to a backend that has not answered (it may have lost the frame, or
            # be too slow: the broker cannot tell, and the charger must get an answer). Once the broker
            # holds a backend's answer it must never send that backend the same start again.
            session = frame[3]["meterStart"]
            if plan.to_leader is not None and session in self.backends[self.leader].answered:
                self.fail(f"start for session {session} sent again to {self.leader}, whose answer the broker holds")
            for key, out in plan.to_followers:
                if json.loads(out)[2] == "StartTransaction" and session in self.backends[key].answered:
                    self.fail(f"start for session {session} copied again to {key}, whose answer the broker holds")
        if plan.reply is not None:
            self.charger_receives(plan.reply)
        if plan.to_leader is not None:
            self.backends[self.leader].inbox.append(plan.to_leader)
            if self.leader_id_unknown(frame):
                self.unmapped_to_leader.add((frame[1], self.leader))
        for key, out in plan.to_followers:
            self.backends[key].inbox.append(out)

    def leader_id_unknown(self, frame):
        """True if the table has no id for the leader for the transaction this frame quotes."""
        tx = frame[3].get("transactionId") if frame[2] in ("MeterValues", "StopTransaction") else None
        if tx is None:
            return False
        rows = [r for r in self.table.snapshot() if r["transaction_id"] == tx]
        return not rows or self.leader not in rows[0]["backend_ids"]

    def charger_receives(self, frame):
        parsed = json.loads(frame)
        mid = parsed[1]
        if parsed[0] == 3 and mid in self.replied:
            self.fail(f"the charger got a second answer to {mid}")
        self.replied.add(mid)
        session = self.start_msg.get(mid)
        payload = parsed[2] if parsed[0] == 3 and isinstance(parsed[2], dict) else {}
        if session is None or "transactionId" not in payload:
            return
        tx = payload["transactionId"]
        if session in self.held_id and self.held_id[session] != tx:
            self.fail(f"session {session} was answered with {tx} after the charger held {self.held_id[session]}")
        self.held_id[session] = tx
        self.unanswered.discard(session)
        live = [self.held_id[s] for s in self.held_id if s not in self.stopped]
        if len(live) != len(set(live)):
            self.fail(f"the charger holds one id for two live transactions: {sorted(live)}")

    # --- backends -------------------------------------------------------
    def process(self, backend, frame):
        parsed = json.loads(frame)
        mid, action, payload = parsed[1], parsed[2], parsed[3]
        if action == "StartTransaction":
            session = payload["meterStart"]
            tx = backend.next_id
            backend.next_id += backend.step
            backend.txs[tx] = session
            backend.saw.add(session)
            backend.outbox.append([3, mid, {"transactionId": tx, "idTagInfo": {"status": "Accepted"}}])
        elif action in ("MeterValues", "StopTransaction"):
            intended = self.intent[mid]
            actual = backend.txs.get(payload["transactionId"])
            if actual != intended:
                # when the table has no id for the leader (it never saw the start, or its answer never
                # arrived) the frame goes through with the charger's id and a warning, by design
                if (mid, backend.key) not in self.unmapped_to_leader:
                    self.fail(f"{backend.key} was told id {payload['transactionId']} for session {intended}; it means {actual}")
            backend.outbox.append([3, mid, {}])
        else:
            backend.outbox.append([3, mid, {"currentTime": "t"}])

    def deliver_reply(self, backend):
        reply = backend.outbox.popleft()
        raw = json.dumps(reply)
        if "transactionId" in reply[2]:
            backend.answered.add(self.start_msg[reply[1]])
        if backend.key == self.leader:
            plan = self.table.from_leader(backend.key, reply, raw)
            for frame in [plan.frame, *plan.extra]:
                if frame is not None:
                    self.charger_receives(frame)
        else:
            for key, frame in self.table.from_follower(backend.key, reply):
                self.backends[key].inbox.append(frame)

    def release(self, pairs):
        for key, frame in pairs:
            self.backends[key].inbox.append(frame)

    # --- random events --------------------------------------------------
    def step(self):
        rng = self.rng
        event = rng.choices(
            ["start", "retry", "meter", "stop", "heartbeat", "process", "reply", "wait", "toggle", "promote"],
            weights=[6, 4, 8, 5, 2, 18, 16, 5, 4, 3],
        )[0]
        if event == "start" and self.sessions < 12:
            session = self.sessions
            self.sessions += 1
            mid = self.new_msg("s")
            self.intent[mid] = self.start_msg[mid] = session
            self.unanswered.add(session)
            self.send([2, mid, "StartTransaction", {"connectorId": 1, "idTag": f"t{session}", "meterStart": session, "timestamp": f"ts{session}"}])
        elif event == "retry" and self.unanswered:
            session = rng.choice(sorted(self.unanswered))
            mid = self.new_msg("r")
            self.intent[mid] = self.start_msg[mid] = session
            self.send([2, mid, "StartTransaction", {"connectorId": 1, "idTag": f"t{session}", "meterStart": session, "timestamp": f"ts{session}"}])
        elif event in ("meter", "stop"):
            live = [s for s in self.held_id if s not in self.stopped]
            if live:
                session = rng.choice(live)
                mid = self.new_msg("m" if event == "meter" else "x")
                self.intent[mid] = session
                if event == "stop":
                    self.stopped.add(session)
                action = "MeterValues" if event == "meter" else "StopTransaction"
                self.send([2, mid, action, {"transactionId": self.held_id[session]}])
        elif event == "heartbeat":
            self.send([2, self.new_msg("h"), "Heartbeat", {}])
        elif event == "process":
            backend = self.backends[rng.choice(KEYS)]
            if backend.ready and backend.inbox:
                self.process(backend, backend.inbox.popleft())
        elif event == "reply":
            backend = self.backends[rng.choice(KEYS)]
            if backend.ready and backend.outbox:
                self.deliver_reply(backend)
        elif event == "wait":
            self.clock.now += rng.uniform(0, 8)
            self.release(self.table.expire_holds())
        elif event == "toggle":
            backend = self.backends[rng.choice([k for k in KEYS if k != self.leader])]
            backend.ready = not backend.ready
            if not backend.ready:  # whatever was in flight on the socket is lost
                backend.inbox.clear()
                backend.outbox.clear()
        elif event == "promote":
            candidates = [k for k in KEYS if k != self.leader and self.backends[k].ready]
            if candidates:
                old, new = self.backends[self.leader], rng.choice(candidates)
                # the old leader's link is gone: what it had not yet answered is lost, and the
                # broker answers each start it was holding with a CALLERROR
                for frame in list(old.inbox):
                    parsed = json.loads(frame)
                    if parsed[2] == "StartTransaction":
                        self.table.start_failed(parsed[1])
                old.inbox.clear()
                old.outbox.clear()
                old.ready = False
                self.leader = new
                self.table.leader_changed(new)
                self.release(self.table.expire_holds())

    def settle(self):
        """Let every backend finish what it holds, as a quiet period would."""
        for _ in range(3):
            self.clock.now += 6
            self.release(self.table.expire_holds())
            for key in KEYS:
                backend = self.backends[key]
                backend.ready = True
                while backend.inbox:
                    self.process(backend, backend.inbox.popleft())
                while backend.outbox:
                    self.deliver_reply(backend)


@pytest.mark.parametrize("seed", range(400))
def test_random_runs_never_misattribute_a_transaction(seed):
    world = World(seed)
    for _ in range(90):
        world.step()
        assert not world.violations, f"seed {seed}: {world.violations[:3]}"
    world.settle()
    assert not world.violations, f"seed {seed}: {world.violations[:3]}"


def test_the_simulation_does_find_the_bug_when_the_table_is_crippled(monkeypatch):
    """A guard for the guard: with ids no longer translated for followers, some seed must fail."""
    monkeypatch.setattr(
        TransactionIdTable,
        "_frame_for",
        lambda self, backend, parsed, raw, rec, *, is_leader: raw if (rec is not None or is_leader) else None,
    )
    failures = 0
    for seed in range(60):
        world = World(seed)
        for _ in range(90):
            world.step()
        world.settle()
        failures += bool(world.violations)
    assert failures > 0
