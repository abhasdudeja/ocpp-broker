"""
Transaction id mapping for relay mode (OCPP 1.6).

In OCPP 1.6 the *backend* chooses a transaction's id and the charger then
quotes it in later messages. With several backends there are several ids for
one charging session. This table keeps one record per transaction and speaks to
each backend in its own ids while the charger sees one stable id, so a follower
never receives an id it did not issue and a failover does not strand the
charger's transactions. See ``plans/transaction-ids.md``.

The charger-visible id is the leader's own id whenever that is free, so with a
single leader traffic is byte-identical to what it would be without the table;
a fresh id is only invented on a collision (which a failover can cause).

This module is pure logic: no sockets, no clock other than the injected one.
``session.ChargerSession`` feeds it frames and sends what it returns.
"""

from __future__ import annotations

import copy
import itertools
import json
import logging
import time
import uuid
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple, TypeGuard

logger = logging.getLogger("ocpp_broker.transaction_ids")

MAX_INT32 = 2**31 - 1

# Charger CALLs that quote a transaction id (the id sits at payload["transactionId"]).
_CHARGER_ID_ACTIONS = ("MeterValues", "StopTransaction")

# Backend CALLs that quote a transaction id, and where.
_BACKEND_ID_FIELDS: Dict[str, Tuple[str, ...]] = {
    "RemoteStopTransaction": ("transactionId",),
    "SetChargingProfile": ("csChargingProfiles", "transactionId"),
    "RemoteStartTransaction": ("chargingProfile", "transactionId"),
}

CALL, CALLRESULT, CALLERROR = 2, 3, 4

# Other ids a backend chooses and the charger stores. Both flow backend -> charger only (a reservation
# id is also echoed back in StartTransaction). Per action: (id space, where the id sits, what the
# message does with it): "assign" creates or replaces the object, "release" removes it.
RESERVATION, PROFILE = "reservation", "profile"
_SPACE_FIELDS: Dict[str, Tuple[str, Tuple[str, ...], str]] = {
    "ReserveNow": (RESERVATION, ("reservationId",), "assign"),
    "CancelReservation": (RESERVATION, ("reservationId",), "release"),
    "SetChargingProfile": (PROFILE, ("csChargingProfiles", "chargingProfileId"), "assign"),
    "RemoteStartTransaction": (PROFILE, ("chargingProfile", "chargingProfileId"), "assign"),
    "ClearChargingProfile": (PROFILE, ("id",), "release"),
}


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _dump(frame: Any) -> str:
    return json.dumps(frame, separators=(",", ":"))


def _read(payload: Any, path: Tuple[str, ...]) -> Optional[int]:
    node = payload
    for part in path:
        node = node.get(part) if isinstance(node, dict) else None
    return node if _is_int(node) else None


def _write(parsed: list, path: Tuple[str, ...], value: int, payload_index: int) -> str:
    """The frame with the id at ``path`` inside ``parsed[payload_index]`` replaced."""
    clone = copy.deepcopy(parsed)
    node = clone[payload_index]
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    return _dump(clone)


def _write_many(parsed: list, edits: List[Tuple[Tuple[str, ...], int]], payload_index: int) -> str:
    """The frame with several ids replaced at once."""
    clone = copy.deepcopy(parsed)
    for path, value in edits:
        node = clone[payload_index]
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value
    return _dump(clone)


def _start_key(payload: Dict[str, Any]) -> tuple:
    """What identifies a retry of the same StartTransaction."""
    return (
        payload.get("connectorId"),
        payload.get("idTag"),
        payload.get("meterStart"),
        payload.get("timestamp"),
    )


@dataclass
class TxRecord:
    """One transaction as the charger and each backend know it."""

    start_key: tuple
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)  # names the record in persistent storage
    tx_id: Optional[int] = None  # the id the charger holds; None until the leader answered
    backend_ids: Dict[str, int] = field(default_factory=dict)  # backend key -> its id
    queued: Set[str] = field(default_factory=set)  # followers with a start copy waiting in their queue, not yet sent
    awaiting: Set[str] = field(default_factory=set)  # followers that got the start and have not answered
    awaiting_since: Dict[str, float] = field(default_factory=dict)
    degraded: Set[str] = field(default_factory=set)  # backends that will never have an id for it
    state: str = "pending"  # pending -> open -> closed
    backend_confs: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # each backend's own start result
    copy_msgs: Dict[str, str] = field(default_factory=dict)  # follower -> message id of the start copy it got
    conf: Optional[Dict[str, Any]] = None  # the StartTransaction result the charger received
    sent: Set[str] = field(default_factory=set)  # start message ids forwarded to the leader, not yet answered
    sent_at: float = 0.0  # when the latest of them went out
    aliases: List[str] = field(default_factory=list)  # retries waiting on the same leader answer
    msgs: Set[str] = field(default_factory=set)  # every start message id registered for this record
    created: float = 0.0
    closed_at: Optional[float] = None
    touched: float = 0.0  # last time a frame used it
    saved_at: float = 0.0  # last time it was handed to ``on_change``


@dataclass
class IdRecord:
    """A reservation or charging profile the charger holds, and what each backend calls it."""

    space: str  # RESERVATION or PROFILE
    cid: int  # the id the charger holds
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    backend_ids: Dict[str, int] = field(default_factory=dict)  # backend key -> its id (empty: made via the REST API)
    expires: Optional[float] = None  # wall-clock seconds; a reservation ends at its expiryDate
    created: float = 0.0
    touched: float = 0.0
    saved_at: float = 0.0


@dataclass
class ChargerPlan:
    """What to do with one frame from the charger."""

    to_leader: Optional[str] = None
    to_followers: List[Tuple[str, str]] = field(default_factory=list)  # (backend key, frame)
    reply: Optional[str] = None  # a frame to send straight back to the charger


@dataclass
class LeaderPlan:
    """What to do with one frame from the leader."""

    frame: Optional[str]  # for the charger; None = swallow it (an answer to an observer copy)
    extra: List[str] = field(default_factory=list)  # further frames for the charger


@dataclass
class _Queued:
    rec: Optional[TxRecord]
    parsed: Any
    raw: str
    kind: str  # "start" | "id" | "plain"
    counted: bool = False  # already counted in stats["held"]


class TransactionIdTable:
    def __init__(
        self,
        *,
        follower_wait: float = 5.0,
        dedupe_start: bool = True,
        retain_closed: float = 86400.0,
        retain_open: float = 2_592_000.0,
        stale_start: float = 60.0,
        max_held: int = 500,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.follower_wait = follower_wait
        self.dedupe_start = dedupe_start
        self.retain_closed = retain_closed
        self.retain_open = retain_open
        self.stale_start = stale_start
        self.max_held = max_held
        self._clock = clock
        self._wall = wall_clock  # records are stored with wall-clock times; the table runs on a monotonic clock
        # Called as on_change(uid, document, expires_at) when a record worth keeping changes
        # (document None = forget it). expires_at is wall-clock seconds. Never blocks.
        self.on_change: Optional[Callable[[str, Optional[Dict[str, Any]], Optional[float]], None]] = None
        self._by_tx: Dict[int, TxRecord] = {}
        self._by_backend: Dict[Tuple[str, int], TxRecord] = {}
        self._by_start: Dict[tuple, TxRecord] = {}
        self._start_msgs: Dict[str, TxRecord] = {}
        self._held: Dict[str, Deque[_Queued]] = {}
        # (follower, message id) of every CALL copied to a follower and not yet answered. If that
        # follower is promoted its late answer arrives as a "leader" frame; the charger never
        # asked that backend, so it must not see it.
        self._copied: "OrderedDict[Tuple[str, str], None]" = OrderedDict()
        # reservations and charging profiles: space -> charger id -> record, and (space, backend, its id) -> record
        self._spaces: Dict[str, Dict[int, IdRecord]] = {}
        self._space_index: Dict[Tuple[str, str, int], IdRecord] = {}
        # message id of a backend CALL we translated -> (record, "assign" | "release", record was created by it);
        # the charger's answer decides whether the change took effect
        self._assigned: "OrderedDict[str, Tuple[IdRecord, str, bool]]" = OrderedDict()
        self.stats: Counter = Counter()

    # ------------------------------------------------------------------
    # Frames from the charger
    # ------------------------------------------------------------------
    def from_charger(
        self, parsed: Any, raw: str, leader: str, followers: Dict[str, bool]
    ) -> ChargerPlan:
        """
        Route one charger frame. ``followers`` maps each follower's key to whether
        it can be written to right now. Only CALLs are copied to followers.
        """
        self._prune()
        plan = ChargerPlan()
        if not (
            isinstance(parsed, list)
            and len(parsed) >= 4
            and parsed[0] == CALL
            and isinstance(parsed[2], str)
            and isinstance(parsed[3], dict)
        ):
            plan.to_leader = raw  # CALLRESULT / CALLERROR / anything odd: leader only, untouched
            if isinstance(parsed, list) and len(parsed) >= 3 and parsed[0] in (CALLRESULT, CALLERROR):
                self._settle_reply(parsed)
            return plan

        action, payload = parsed[2], parsed[3]
        if action == "StartTransaction":
            return self._charger_start(parsed, raw, leader, followers)

        rec: Optional[TxRecord] = None
        kind = "plain"
        if action in _CHARGER_ID_ACTIONS and _is_int(payload.get("transactionId")):
            kind = "id"
            rec = self._by_tx.get(payload["transactionId"])

        if kind == "id":
            if rec is not None:
                self._touch(rec)
            plan.to_leader = self._frame_for(leader, parsed, raw, rec, is_leader=True)
            if action == "StopTransaction" and rec is not None and rec.state != "closed":
                self._close(rec)
        else:
            plan.to_leader = raw

        for key, ready in followers.items():
            if not ready:
                continue
            plan.to_followers.extend((key, frame) for frame in self._enqueue(key, _Queued(rec, parsed, raw, kind)))
        return plan

    def _charger_start(self, parsed: list, raw: str, leader: str, followers: Dict[str, bool]) -> ChargerPlan:
        msg_id, payload = parsed[1], parsed[3]
        key = _start_key(payload)
        now = self._clock()
        plan = ChargerPlan()

        rec = self._by_start.get(key) if self.dedupe_start else None
        if rec is not None and rec.state == "open" and rec.conf is not None:
            # A retry of a start the leader already answered: one transaction, not two.
            self.stats["deduped"] += 1
            plan.reply = _dump([CALLRESULT, msg_id, rec.conf])
            return plan
        if rec is not None and rec.state == "pending" and not rec.sent and leader in rec.backend_ids and leader in rec.backend_confs:
            # The leader (a follower until just now) already holds this start: it saw the observer
            # copy and answered it. Starting again would make a second transaction there.
            self.stats["deduped"] += 1
            conf = self._open(rec, leader, rec.backend_ids[leader], rec.backend_confs[leader])
            plan.reply = _dump([CALLRESULT, msg_id, conf])
            return plan
        if rec is not None and rec.state == "pending" and not rec.sent and leader in rec.awaiting and leader in rec.copy_msgs:
            # Same, but the leader has not answered its copy yet: wait for that answer.
            self.stats["deduped"] += 1
            rec.sent.add(rec.copy_msgs[leader])
            rec.sent_at = now
            rec.aliases.append(msg_id)
            return plan
        if rec is not None and rec.sent and now - rec.sent_at < self.stale_start:
            # The leader is still working on the first attempt; answer both together.
            self.stats["deduped"] += 1
            rec.aliases.append(msg_id)
            return plan

        is_new = rec is None
        if rec is None:
            rec = TxRecord(start_key=key, created=now)
            if self.dedupe_start:
                self._by_start[key] = rec
        rec.sent.add(msg_id)
        rec.sent_at = now
        rec.msgs.add(msg_id)
        self._start_msgs[msg_id] = rec
        plan.to_leader = self._start_frame(leader, parsed, raw, is_leader=True)

        for follower, ready in followers.items():
            if not is_new and (follower in rec.backend_ids or follower in rec.awaiting or follower in rec.degraded):
                continue  # it already has this start (a retry must not create a second one)
            if not ready:
                rec.degraded.add(follower)
                continue
            rec.queued.add(follower)
            plan.to_followers.extend((follower, frame) for frame in self._enqueue(follower, _Queued(rec, parsed, raw, "start")))
        return plan

    # ------------------------------------------------------------------
    # Frames from the leader
    # ------------------------------------------------------------------
    def from_leader(self, leader: str, parsed: Any, raw: str) -> LeaderPlan:
        """Translate one frame from the leader on its way to the charger."""
        if not isinstance(parsed, list) or len(parsed) < 3:
            return LeaderPlan(raw)
        kind = parsed[0]

        if kind in (CALLRESULT, CALLERROR):
            msg_id = parsed[1]
            rec = self._start_msgs.get(msg_id)
            if rec is not None and msg_id in rec.sent:
                if rec.state == "pending":
                    if kind == CALLRESULT:
                        return self._leader_started(rec, leader, parsed, raw)
                    return LeaderPlan(raw, self._attempt_failed(rec, msg_id, parsed[2:]))
                # Another attempt at the same start (a retry after the stale timeout) answered after
                # the transaction already exists: the charger must hear the id it already holds.
                rec.sent.discard(msg_id)
                self._collect(rec)
                if kind == CALLRESULT and rec.conf is not None:
                    self.stats["duplicate_answers"] += 1
                    logger.warning(
                        "Leader answered a repeated StartTransaction with a second transaction of its own; the charger keeps id %s",
                        rec.tx_id,
                    )
                    return LeaderPlan(_dump([CALLRESULT, msg_id, rec.conf]))
                return LeaderPlan(raw)
            if rec is not None and rec.copy_msgs.get(leader) == msg_id and leader not in rec.backend_ids:
                # The promoted follower finally answering the observer copy it got as a follower
                # (even after we gave up waiting for it): the charger never asked this backend,
                # so only the table gets to read it.
                self._learn(rec, leader, parsed)
                self._copied.pop((leader, msg_id), None)
                return LeaderPlan(None)
            if (leader, msg_id) in self._copied:
                del self._copied[(leader, msg_id)]
                self.stats["swallowed"] += 1
                return LeaderPlan(None)  # a late answer to an observer copy, not to the charger
            return LeaderPlan(raw)

        if kind == CALL and len(parsed) >= 4 and isinstance(parsed[2], str) and isinstance(parsed[3], dict):
            return LeaderPlan(self._leader_call(leader, parsed, raw))
        return LeaderPlan(raw)

    def _leader_call(self, leader: str, parsed: list, raw: str) -> str:
        """A backend command that quotes ids the charger stores: put the charger's own ids in it."""
        action, payload = parsed[2], parsed[3]
        edits: List[Tuple[Tuple[str, ...], int]] = []

        path = _BACKEND_ID_FIELDS.get(action)
        if path is not None:
            issued = _read(payload, path)
            if issued is not None:
                held = self._reverse_id(leader, issued)
                if held is not None and held != issued:
                    edits.append((path, held))

        spec = _SPACE_FIELDS.get(action)
        if spec is not None:
            space, id_path, mode = spec
            value = _read(payload, id_path)
            if value is not None:
                rec: Optional[IdRecord]
                if mode == "assign":
                    rec, created = self._space_assign(space, leader, value, self._expiry_of(action, payload))
                    self._expect_reply(parsed[1], rec, "assign", created)
                else:
                    rec = self._space_find(space, leader, value)
                    if rec is None:
                        self.stats["unmapped"] += 1  # not made through this broker: pass it on as it is
                    else:
                        self._expect_reply(parsed[1], rec, "release", False)
                if rec is not None and rec.cid != value:
                    edits.append((id_path, rec.cid))
                    self.stats["rewritten"] += 1

        return _write_many(parsed, edits, payload_index=3) if edits else raw

    def _leader_started(self, rec: TxRecord, leader: str, parsed: list, raw: str) -> LeaderPlan:
        payload = parsed[2] if len(parsed) > 2 and isinstance(parsed[2], dict) else None
        issued = payload.get("transactionId") if payload is not None else None
        if payload is None or not _is_int(issued):
            logger.warning("StartTransaction result without a usable transactionId; passed through untouched")
            self._attempt_failed(rec, parsed[1], ["InternalError", "Backend sent no transaction id", {}])
            return LeaderPlan(raw)

        conf = self._open(rec, leader, issued, payload)
        frame = raw if conf["transactionId"] == issued else _dump([CALLRESULT, parsed[1], conf])
        extra = [_dump([CALLRESULT, alias, conf]) for alias in rec.aliases]
        rec.aliases.clear()
        rec.sent.discard(parsed[1])
        self._collect(rec)
        return LeaderPlan(frame, extra)

    def _open(self, rec: TxRecord, leader: str, issued: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """The leader numbered the transaction ``issued``: settle the charger's id and open the record."""
        tx_id = self._choose(issued)
        rec.tx_id, rec.state = tx_id, "open"
        rec.backend_ids[leader] = issued
        rec.degraded.discard(leader)
        rec.awaiting.discard(leader)
        rec.awaiting_since.pop(leader, None)
        self._by_tx[tx_id] = rec
        self._by_backend[(leader, issued)] = rec
        conf = dict(payload)
        conf["transactionId"] = tx_id
        rec.conf = conf
        rec.touched = self._clock()
        self._persist(rec)
        return conf

    def _learn(self, rec: TxRecord, backend: str, parsed: list) -> None:
        """A backend that was only observing answered its copy of a start: remember the id it issued."""
        rec.awaiting.discard(backend)
        rec.awaiting_since.pop(backend, None)
        payload = parsed[2] if parsed[0] == CALLRESULT and len(parsed) > 2 and isinstance(parsed[2], dict) else None
        issued = payload.get("transactionId") if payload is not None else None
        if payload is not None and _is_int(issued):
            rec.backend_ids[backend] = issued
            rec.backend_confs[backend] = dict(payload)
            rec.degraded.discard(backend)
            self._by_backend[(backend, issued)] = rec
        else:
            rec.degraded.add(backend)
            logger.warning("Backend %s gave no transaction id for a start; its copies for it are skipped", backend)
        self._persist(rec)
        self._collect(rec)

    def _attempt_failed(self, rec: TxRecord, msg_id: str, error: list) -> List[str]:
        """
        One attempt at the start failed. Retries waiting on it hear the same error, but only once no
        other attempt is still outstanding (that one may yet succeed). Returns the frames for them.
        """
        rec.sent.discard(msg_id)
        extra: List[str] = []
        if not rec.sent:
            extra = [_dump([CALLERROR, alias, *error]) for alias in rec.aliases]
            rec.aliases.clear()
        self._collect(rec)
        return extra

    def _leader_gave_up(self, rec: TxRecord) -> None:
        """Every outstanding attempt is void (the leader changed): the charger will retry."""
        rec.sent.clear()
        rec.aliases.clear()
        self._collect(rec)

    def _reverse_id(self, leader: str, issued: int) -> Optional[int]:
        """The id the charger holds for the transaction ``leader`` calls ``issued`` (None: not known to the table)."""
        rec = self._by_backend.get((leader, issued))
        if rec is None or rec.tx_id is None:
            self.stats["unmapped"] += 1
            logger.warning("Leader quoted transaction id %s that the id table does not know; passed through", issued)
            return None
        if rec.tx_id != issued:
            self.stats["rewritten"] += 1
        return rec.tx_id

    def start_failed(self, msg_id: str) -> List[str]:
        """
        The broker answered a start with its own CALLERROR (backend unavailable).
        Forget the attempt; returns CALLERRORs for any retries that were waiting on it.
        """
        rec = self._start_msgs.get(msg_id)
        if rec is None or msg_id not in rec.sent:
            return []
        return self._attempt_failed(rec, msg_id, ["InternalError", "Backend unavailable, please retry", {}])

    # ------------------------------------------------------------------
    # Frames from followers
    # ------------------------------------------------------------------
    def from_follower(self, follower: str, parsed: Any) -> List[Tuple[str, str]]:
        """
        A follower answered. Only its answer to a start copy matters (it tells us
        the id it issued); the answer is never forwarded. Returns frames now free
        to send to that follower: (backend key, frame).
        """
        if not (isinstance(parsed, list) and len(parsed) >= 3 and parsed[0] in (CALLRESULT, CALLERROR)):
            return []
        self._copied.pop((follower, parsed[1]), None)
        rec = self._start_msgs.get(parsed[1])
        # Also the demoted old leader answering a start it was sent as the leader: its id is worth keeping
        if rec is None or (follower not in rec.awaiting and follower in rec.backend_ids):
            return []
        self._learn(rec, follower, parsed)
        return [(follower, frame) for frame in self._drain(follower)]

    # ------------------------------------------------------------------
    # Per-follower ordered queue
    # ------------------------------------------------------------------
    def _enqueue(self, follower: str, item: _Queued) -> List[str]:
        queue = self._held.setdefault(follower, deque())
        if len(queue) >= self.max_held:
            self.stats["overflow"] += 1
            if item.rec is not None:
                item.rec.degraded.add(follower)
                item.rec.queued.discard(follower)
            logger.warning("Copy queue for follower %s is full; frame dropped", follower)
            return []
        queue.append(item)
        return self._drain(follower)

    def _drain(self, follower: str) -> List[str]:
        """Frames at the head of the follower's queue that can go out now, in order."""
        queue = self._held.get(follower)
        out: List[str] = []
        now = self._clock()
        while queue:
            item = queue[0]
            if item.kind == "id":
                rec = item.rec
                if rec is None:
                    self.stats["skipped"] += 1  # an id this table never saw: sending it could hit the wrong transaction
                    queue.popleft()
                    continue
                if follower in rec.backend_ids:
                    frame = self._frame_for(follower, item.parsed, item.raw, rec, is_leader=False)
                    assert frame is not None  # the follower has an id for it
                    out.append(frame)
                    self._note_copy(follower, item.parsed)
                    queue.popleft()
                    continue
                if follower in rec.awaiting:
                    if now - rec.awaiting_since.get(follower, now) < self.follower_wait:
                        if not item.counted:
                            item.counted = True
                            self.stats["held"] += 1
                        break  # wait for the follower's answer to the start
                    rec.awaiting.discard(follower)
                    rec.degraded.add(follower)
                    logger.warning("Follower %s never gave an id for a start; giving up on its copies", follower)
                self.stats["skipped"] += 1
                rec.degraded.add(follower)
                queue.popleft()
                continue
            if item.kind == "start" and item.rec is not None:
                item.rec.awaiting.add(follower)
                item.rec.awaiting_since[follower] = now
                item.rec.copy_msgs[follower] = item.parsed[1]
                item.rec.queued.discard(follower)
            out.append(self._start_frame(follower, item.parsed, item.raw, is_leader=False) if item.kind == "start" else item.raw)
            self._note_copy(follower, item.parsed)
            queue.popleft()
        return out

    def _note_copy(self, follower: str, parsed: Any) -> None:
        """Remember a CALL copied to ``follower`` so a late answer after its promotion can be recognised."""
        self._copied[(follower, parsed[1])] = None
        while len(self._copied) > 4096:
            self._copied.popitem(last=False)

    def expire_holds(self) -> List[Tuple[str, str]]:
        """Give up on followers that took too long; returns frames that are now free."""
        released: List[Tuple[str, str]] = []
        for follower in list(self._held):
            released.extend((follower, frame) for frame in self._drain(follower))
        return released

    def next_expiry_in(self) -> Optional[float]:
        """Seconds until the oldest blocked copy must be given up on, or None."""
        now = self._clock()
        soonest: Optional[float] = None
        for follower, queue in self._held.items():
            if not queue:
                continue
            item = queue[0]
            if item.kind == "id" and item.rec is not None and follower in item.rec.awaiting:
                due = item.rec.awaiting_since.get(follower, now) + self.follower_wait - now
                soonest = due if soonest is None else min(soonest, due)
        return None if soonest is None else max(soonest, 0.0)

    # ------------------------------------------------------------------
    # Leader changes
    # ------------------------------------------------------------------
    def leader_changed(self, new_leader: Optional[str] = None) -> None:
        """
        A follower was promoted. A start still waiting for the old leader will
        never be answered; forget it so the charger's retry goes to the new
        leader. (A mere link loss must not call this: a start sitting in the
        leader's outbox is still answered when the link returns.) The promoted
        follower stops being a follower: whatever was queued for it is given up
        on, because it is the leader now.
        """
        for rec in list(self._by_start.values()):
            if rec.state == "pending" and rec.sent:
                self._leader_gave_up(rec)
        if new_leader is not None:
            for item in self._held.pop(new_leader, deque()):
                if item.rec is not None:
                    item.rec.degraded.add(new_leader)
                    item.rec.awaiting.discard(new_leader)
                    item.rec.queued.discard(new_leader)
                    self.stats["skipped"] += 1

    # ------------------------------------------------------------------
    # Translation helpers
    # ------------------------------------------------------------------
    def _frame_for(self, backend: str, parsed: list, raw: str, rec: Optional[TxRecord], *, is_leader: bool) -> Optional[str]:
        """A MeterValues/StopTransaction frame in ``backend``'s own transaction id."""
        if rec is None:
            if is_leader:
                self.stats["unmapped"] += 1
                return raw  # unknown to us (e.g. started before the table existed): the leader may know it
            return None
        issued = rec.backend_ids.get(backend)
        if issued is None:
            if is_leader:
                rec.degraded.add(backend)
                self.stats["unmapped"] += 1
                logger.warning(
                    "Leader %s never issued an id for transaction %s (it did not see the start); passed through unchanged",
                    backend,
                    rec.tx_id,
                )
                return raw
            return None
        if issued == rec.tx_id:
            return raw
        self.stats["rewritten"] += 1
        return _write(parsed, ("transactionId",), issued, payload_index=3)

    def _choose(self, issued: int) -> int:
        """The charger-visible id for a transaction the leader numbered ``issued``."""
        if issued not in self._by_tx:
            return issued
        fresh = self._fresh()
        self.stats["remapped"] += 1
        logger.warning(
            "Leader issued transaction id %s, which the charger already holds; the charger will see %s instead",
            issued,
            fresh,
        )
        return fresh

    def _fresh(self) -> int:
        known = itertools.chain(self._by_tx, (i for _, i in self._by_backend))
        candidate = max(known, default=0) + 1
        if candidate > MAX_INT32:
            candidate = next(i for i in itertools.count(1) if i not in self._by_tx)
        return candidate

    # ------------------------------------------------------------------
    # Housekeeping and inspection
    # ------------------------------------------------------------------
    def _close(self, rec: TxRecord) -> None:
        rec.state, rec.closed_at = "closed", self._clock()
        self._by_start.pop(rec.start_key, None)
        self._persist(rec)
        self._collect(rec)

    def _collect(self, rec: TxRecord) -> None:
        """Forget message-id bookkeeping nobody is waiting on any more, and dead records."""
        if not rec.sent and not rec.awaiting and not rec.queued:
            for msg in rec.msgs:
                if self._start_msgs.get(msg) is rec:
                    del self._start_msgs[msg]
            rec.msgs.clear()
        if rec.state == "pending" and not rec.sent and not rec.backend_ids and not rec.awaiting and not rec.queued:
            if self._by_start.get(rec.start_key) is rec:
                del self._by_start[rec.start_key]

    def _prune(self) -> None:
        now = self._clock()
        for rec in list(self._by_tx.values()):
            if rec.state == "closed" and rec.closed_at is not None and now - rec.closed_at > self.retain_closed:
                self._forget(rec)
            elif rec.state == "open" and now - rec.touched > self.retain_open:
                logger.warning(
                    "Transaction %s was never stopped and has been idle for %.0f days; forgetting it",
                    rec.tx_id,
                    self.retain_open / 86400,
                )
                self._forget(rec)
        stale_pending = max(self.stale_start * 5, 300.0)
        for rec in list(self._by_start.values()):
            if rec.state == "pending" and now - rec.created > stale_pending:
                self._forget(rec)
        wall = self._wall()
        for space, records in list(self._spaces.items()):
            for idr in list(records.values()):
                if space == RESERVATION and idr.expires is not None:
                    gone = wall > idr.expires + self.retain_closed
                else:
                    gone = now - idr.touched > (self.retain_closed if space == RESERVATION else self.retain_open)
                if gone:
                    self._space_release(idr)

    def _forget(self, rec: TxRecord) -> None:
        if rec.tx_id is not None and self._by_tx.get(rec.tx_id) is rec:
            del self._by_tx[rec.tx_id]
        for pair in [p for p, r in self._by_backend.items() if r is rec]:
            del self._by_backend[pair]
        if self._by_start.get(rec.start_key) is rec:
            del self._by_start[rec.start_key]
        for msg in list(rec.msgs):
            if self._start_msgs.get(msg) is rec:
                del self._start_msgs[msg]
        self._unpersist(rec)

    # ------------------------------------------------------------------
    # Reservations and charging profiles
    #
    # Same rule as for transactions: the charger holds the backend's own number whenever no other
    # object of that kind already has it, and a fresh one otherwise. The same backend sending its
    # number again still replaces its own object (that is what the protocol means by it); another
    # backend's object with the same number is never overwritten.
    # ------------------------------------------------------------------
    def _space_find(self, space: str, backend: str, bid: int) -> Optional[IdRecord]:
        return self._space_index.get((space, backend, bid))

    def _space_assign(self, space: str, backend: str, bid: int, expires: Optional[float]) -> Tuple[IdRecord, bool]:
        """The record for ``backend``'s object numbered ``bid`` and whether this call created it."""
        now = self._clock()
        rec = self._space_index.get((space, backend, bid))
        if rec is not None:
            rec.touched = now
            if expires is not None:
                rec.expires = expires
            self._persist_space(rec)
            return rec, False
        records = self._spaces.setdefault(space, {})
        cid = bid
        if cid in records:
            cid = self._space_fresh(space)
            self.stats["remapped"] += 1
            logger.warning(
                "%s id %s from %s is already held by the charger for another %s; the charger will see %s instead",
                space.capitalize(),
                bid,
                backend,
                space,
                cid,
            )
        rec = IdRecord(space=space, cid=cid, expires=expires, created=now, touched=now)
        rec.backend_ids[backend] = bid
        records[cid] = rec
        self._space_index[(space, backend, bid)] = rec
        self._persist_space(rec)
        return rec, True

    def _space_occupy(self, space: str, cid: int, expires: Optional[float]) -> None:
        """The broker itself (REST API) gave the charger an object with this id: no backend's number may land on it."""
        records = self._spaces.setdefault(space, {})
        rec = records.get(cid)
        now = self._clock()
        if rec is None:
            rec = IdRecord(space=space, cid=cid, expires=expires, created=now, touched=now)
            records[cid] = rec
        else:
            rec.touched = now
            if expires is not None:
                rec.expires = expires
        self._persist_space(rec)

    def _space_fresh(self, space: str) -> int:
        taken = set(self._spaces.get(space, {}))
        known = taken | {bid for (s, _, bid) in self._space_index if s == space}
        candidate = max(known, default=0) + 1
        if candidate > MAX_INT32:
            candidate = next(i for i in itertools.count(1) if i not in taken)
        return candidate

    def _space_release(self, rec: IdRecord) -> None:
        records = self._spaces.get(rec.space, {})
        if records.get(rec.cid) is rec:
            records.pop(rec.cid)
        for backend, bid in rec.backend_ids.items():
            if self._space_index.get((rec.space, backend, bid)) is rec:
                self._space_index.pop((rec.space, backend, bid))
        self._unpersist_space(rec)

    def _expect_reply(self, msg_id: str, rec: IdRecord, mode: str, created: bool) -> None:
        self._assigned[msg_id] = (rec, mode, created)
        while len(self._assigned) > 1024:
            self._assigned.popitem(last=False)

    def _settle_reply(self, parsed: list) -> None:
        """The charger answered a reservation or profile command we translated: did it take effect?"""
        entry = self._assigned.pop(parsed[1], None)
        if entry is None:
            return
        rec, mode, created = entry
        status = parsed[2].get("status") if parsed[0] == CALLRESULT and isinstance(parsed[2], dict) else None
        accepted = status == "Accepted"
        if mode == "assign" and created and not accepted:
            self._space_release(rec)  # the charger refused it: nothing holds that id
        elif mode == "release" and accepted:
            self._space_release(rec)  # cancelled or cleared: the id is free again

    @staticmethod
    def _expiry_of(action: str, payload: Dict[str, Any]) -> Optional[float]:
        """Wall-clock end of a reservation (its ``expiryDate``), or None."""
        if action != "ReserveNow" or not isinstance(payload.get("expiryDate"), str):
            return None
        try:
            parsed = datetime.fromisoformat(payload["expiryDate"].replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    def _start_frame(self, backend: str, parsed: list, raw: str, *, is_leader: bool) -> str:
        """
        A StartTransaction in ``backend``'s own reservation id. The charger quotes the id it holds.
        A backend that did not make that reservation is not told about it: a number it cannot
        resolve might mean one of its own. (A reservation the table never heard of may predate
        the broker, so the leader still gets it as it is.)
        """
        payload = parsed[3]
        held = payload.get("reservationId")
        if not _is_int(held):
            return raw
        rec = self._spaces.get(RESERVATION, {}).get(held)
        if rec is None:
            return raw if is_leader else self._strip_reservation(parsed)
        rec.touched = self._clock()
        issued = rec.backend_ids.get(backend)
        if issued is None:
            return self._strip_reservation(parsed)
        if issued == held:
            return raw
        self.stats["rewritten"] += 1
        return _write(parsed, ("reservationId",), issued, payload_index=3)

    def _strip_reservation(self, parsed: list) -> str:
        self.stats["stripped"] += 1
        clone = copy.deepcopy(parsed)
        clone[3].pop("reservationId", None)
        return _dump(clone)

    def note_command(self, action: str, payload: Any) -> None:
        """
        The broker itself sent the charger a command (REST API). Its reservation and profile ids belong
        to no backend, but they are taken on the charger, so they are recorded as such.
        """
        spec = _SPACE_FIELDS.get(action)
        if spec is None or not isinstance(payload, dict):
            return
        space, path, mode = spec
        value = _read(payload, path)
        if value is None:
            return
        if mode == "assign":
            self._space_occupy(space, value, self._expiry_of(action, payload))
        else:
            rec = self._spaces.get(space, {}).get(value)
            if rec is not None:
                self._space_release(rec)

    def _persist_space(self, rec: IdRecord) -> None:
        if self.on_change is None:
            return
        rec.saved_at = self._clock()
        wall = self._wall()
        if rec.space == RESERVATION:
            expires = (rec.expires if rec.expires is not None else wall) + self.retain_closed
        else:
            expires = wall + self.retain_open
        doc = {
            "kind": rec.space,
            "uid": rec.uid,
            "cid": rec.cid,
            "backend_ids": [[key, issued] for key, issued in rec.backend_ids.items()],
            "expires": rec.expires,
            "updated_at": wall,
        }
        self.on_change(rec.uid, doc, expires)

    def _unpersist_space(self, rec: IdRecord) -> None:
        if self.on_change is not None and rec.saved_at:
            self.on_change(rec.uid, None, None)

    def _import_space(self, doc: Dict[str, Any], known: Optional[Set[str]]) -> bool:
        wall_now, mono_now = self._wall(), self._clock()
        space, cid = doc["kind"], doc["cid"]
        updated = float(doc.get("updated_at") or wall_now)
        expires = doc.get("expires")
        if space == RESERVATION:
            if wall_now > (float(expires) if expires is not None else updated) + self.retain_closed:
                return False
        elif wall_now - updated > self.retain_open:
            return False
        records = self._spaces.setdefault(space, {})
        if cid in records:
            return False  # already known (a live record wins)
        ids = {str(k): v for k, v in doc.get("backend_ids", [])}
        if known is not None:
            ids = {k: v for k, v in ids.items() if k in known}
        rec = IdRecord(
            space=space,
            cid=cid,
            uid=doc["uid"],
            backend_ids=ids,
            expires=None if expires is None else float(expires),
            created=mono_now - (wall_now - updated),
            touched=mono_now - (wall_now - updated),
        )
        rec.saved_at = rec.touched
        records[cid] = rec
        for backend, bid in ids.items():
            self._space_index[(space, backend, bid)] = rec
        return True

    def spaces_snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        """Reservations and charging profiles for display: the charger's id and each backend's."""
        return {
            space: [
                {"id": rec.cid, "backend_ids": dict(rec.backend_ids), "expires": rec.expires}
                for _, rec in sorted(records.items())
            ]
            for space, records in self._spaces.items()
            if records
        }

    # ------------------------------------------------------------------
    # Persistence: export and restore (the I/O lives in transaction_store.py)
    # ------------------------------------------------------------------
    def _touch(self, rec: TxRecord) -> None:
        """A frame used the record. Refresh its stored expiry now and then, not on every meter reading."""
        now = self._clock()
        rec.touched = now
        if now - rec.saved_at > 3600.0:
            self._persist(rec)

    def _persist(self, rec: TxRecord) -> None:
        if self.on_change is None or not (rec.tx_id is not None or rec.backend_ids):
            return  # nothing worth keeping yet
        now = self._clock()
        rec.saved_at = now
        if rec.state == "closed":
            expires = self._wall_of(rec.closed_at if rec.closed_at is not None else now) + self.retain_closed
        else:
            expires = self._wall() + self.retain_open
        self.on_change(rec.uid, self._export(rec), expires)

    def _unpersist(self, rec: TxRecord) -> None:
        if self.on_change is not None and rec.saved_at:
            self.on_change(rec.uid, None, None)

    def _wall_of(self, mono: float) -> float:
        return self._wall() - (self._clock() - mono)

    def _export(self, rec: TxRecord) -> Dict[str, Any]:
        """One record as a plain document. Backend keys may contain dots (URLs), so maps become pair lists."""
        return {
            "uid": rec.uid,
            "state": rec.state,
            "tx_id": rec.tx_id,
            "start_key": list(rec.start_key),
            "backend_ids": [[key, issued] for key, issued in rec.backend_ids.items()],
            "backend_confs": [[key, conf] for key, conf in rec.backend_confs.items()],
            "conf": rec.conf,
            "degraded": sorted(rec.degraded),
            "created_at": self._wall_of(rec.created),
            "closed_at": None if rec.closed_at is None else self._wall_of(rec.closed_at),
            "updated_at": self._wall(),
        }

    def restore(self, documents: Iterable[Dict[str, Any]], known_backends: Optional[Set[str]] = None) -> int:
        """
        Load records saved by an earlier run. Expired ones are skipped; ids of backends
        that are no longer configured are dropped (with a warning). In-flight state (what
        was sent and not yet answered) is not stored, so it starts empty. Returns how many
        records were restored.
        """
        restored = 0
        for document in documents:
            try:
                if "kind" in document:  # a reservation or a charging profile
                    imported = self._import_space(document, known_backends)
                else:
                    imported = self._import(document, known_backends)
                if imported:
                    restored += 1
            except Exception as exc:
                logger.warning("Skipped an unreadable transaction id record: %s", exc)
        return restored

    def _import(self, doc: Dict[str, Any], known: Optional[Set[str]]) -> bool:
        wall_now, mono_now = self._wall(), self._clock()
        state = doc["state"]
        updated = float(doc.get("updated_at") or wall_now)
        closed = doc.get("closed_at")
        if state == "closed":
            if closed is not None and wall_now - float(closed) > self.retain_closed:
                return False
        elif wall_now - updated > self.retain_open:
            return False
        tx_id = doc.get("tx_id")
        if tx_id is not None and tx_id in self._by_tx:
            return False  # already known (a live record wins)

        def mono(wall: float) -> float:
            return mono_now - (wall_now - wall)

        ids = {str(k): v for k, v in doc.get("backend_ids", [])}
        confs = {str(k): v for k, v in doc.get("backend_confs", [])}
        if known is not None:
            gone = sorted(set(ids) - known)
            if gone:
                logger.warning(
                    "Stored transaction %s names backend(s) no longer configured (%s); their ids are dropped",
                    tx_id,
                    ", ".join(gone),
                )
                self.stats["unknown_backend"] += len(gone)
            ids = {k: v for k, v in ids.items() if k in known}
            confs = {k: v for k, v in confs.items() if k in known}
        rec = TxRecord(
            start_key=tuple(doc["start_key"]),
            uid=doc["uid"],
            tx_id=tx_id,
            backend_ids=ids,
            backend_confs=confs,
            conf=doc.get("conf"),
            degraded=set(doc.get("degraded", [])),
            state=state,
            created=mono(float(doc.get("created_at") or wall_now)),
            closed_at=None if closed is None else mono(float(closed)),
            touched=mono(updated),
        )
        rec.saved_at = rec.touched
        if tx_id is not None:
            self._by_tx[tx_id] = rec
        for backend, issued in ids.items():
            self._by_backend[(backend, issued)] = rec
        if state != "closed" and self.dedupe_start:
            self._by_start[rec.start_key] = rec
        return True

    def is_idle(self) -> bool:
        """Nothing worth keeping: safe to drop the table."""
        self._prune()
        return not (
            self._by_tx
            or self._by_start
            or self._start_msgs
            or any(self._held.values())
            or any(self._spaces.values())
        )

    def snapshot(self) -> List[Dict[str, Any]]:
        """The table for display: one dict per transaction."""
        rows = []
        for rec in sorted(
            {id(r): r for r in itertools.chain(self._by_tx.values(), self._by_start.values())}.values(),
            key=lambda r: r.created,
        ):
            rows.append(
                {
                    "transaction_id": rec.tx_id,
                    "state": rec.state,
                    "backend_ids": dict(rec.backend_ids),
                    "degraded": sorted(rec.degraded),
                    "awaiting": sorted(rec.awaiting),
                }
            )
        return rows


def table_for_org(org_entry: Dict[str, Any]) -> Optional[TransactionIdTable]:
    """
    A table configured from the organization's ``transaction_ids`` block, or None
    when mapping is off. Mapping is on by default when the organization has at
    least one follower (more than one backend) and can be forced either way with
    ``transaction_ids.mapping``; with a single backend there is nothing to map.
    """
    settings = org_entry.get("transaction_ids") or {}
    mapping = settings.get("mapping")
    if mapping is None:
        mapping = len(org_entry.get("backends") or []) > 1
    if not mapping:
        return None
    return TransactionIdTable(
        follower_wait=float(settings.get("follower_wait", 5.0)),
        dedupe_start=bool(settings.get("dedupe_start", True)),
        retain_closed=float(settings.get("retain_closed", 86400.0)),
        retain_open=float(settings.get("retain_open", 2_592_000.0)),
    )


LOCAL_KEY = "broker"  # what the local backend (this broker itself) is called when it has no ``id``


def local_index(backends: Iterable[Dict[str, Any]]) -> Optional[int]:
    """Position of the ``local: true`` entry of an organization's backends (this broker as a backend), or None."""
    for index, backend in enumerate(backends):
        if backend.get("local"):
            return index
    return None


def backend_keys(backends: Iterable[Dict[str, Any]]) -> List[str]:
    """
    Stable, unique key per configured backend: its ``id`` if given, else its URL (a local backend: "broker").
    A repeated key gets ``#2``, ``#3``... so two backends never share a table entry.
    """
    seen: Counter = Counter()
    keys = []
    for backend in backends:
        base = str(backend.get("id") or backend.get("url") or (LOCAL_KEY if backend.get("local") else "backend"))
        seen[base] += 1
        keys.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return keys
