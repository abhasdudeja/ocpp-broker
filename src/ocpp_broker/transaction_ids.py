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
from collections import Counter, deque
from dataclasses import dataclass, field
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
    tx_id: Optional[int] = None  # the id the charger holds; None until the leader answered
    backend_ids: Dict[str, int] = field(default_factory=dict)  # backend key -> its id
    awaiting: Set[str] = field(default_factory=set)  # followers that got the start and have not answered
    awaiting_since: Dict[str, float] = field(default_factory=dict)
    degraded: Set[str] = field(default_factory=set)  # backends that will never have an id for it
    state: str = "pending"  # pending -> open -> closed
    conf: Optional[Dict[str, Any]] = None  # the StartTransaction result the charger received
    leader_msg: Optional[str] = None  # message id of the start forwarded to the leader, unanswered
    leader_msg_at: float = 0.0
    aliases: List[str] = field(default_factory=list)  # retries waiting on the same leader answer
    msgs: Set[str] = field(default_factory=set)  # every start message id registered for this record
    created: float = 0.0
    closed_at: Optional[float] = None


@dataclass
class ChargerPlan:
    """What to do with one frame from the charger."""

    to_leader: Optional[str] = None
    to_followers: List[Tuple[str, str]] = field(default_factory=list)  # (backend key, frame)
    reply: Optional[str] = None  # a frame to send straight back to the charger


@dataclass
class LeaderPlan:
    """What to do with one frame from the leader."""

    frame: str  # for the charger
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
        stale_start: float = 60.0,
        max_held: int = 500,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.follower_wait = follower_wait
        self.dedupe_start = dedupe_start
        self.retain_closed = retain_closed
        self.stale_start = stale_start
        self.max_held = max_held
        self._clock = clock
        self._by_tx: Dict[int, TxRecord] = {}
        self._by_backend: Dict[Tuple[str, int], TxRecord] = {}
        self._by_start: Dict[tuple, TxRecord] = {}
        self._start_msgs: Dict[str, TxRecord] = {}
        self._held: Dict[str, Deque[_Queued]] = {}
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
        if rec is not None and rec.leader_msg is not None and now - rec.leader_msg_at < self.stale_start:
            # The leader is still working on the first attempt; answer both together.
            self.stats["deduped"] += 1
            rec.aliases.append(msg_id)
            return plan

        is_new = rec is None
        if rec is None:
            rec = TxRecord(start_key=key, created=now)
            if self.dedupe_start:
                self._by_start[key] = rec
        rec.leader_msg, rec.leader_msg_at = msg_id, now
        rec.msgs.add(msg_id)
        self._start_msgs[msg_id] = rec
        plan.to_leader = raw

        for follower, ready in followers.items():
            if not is_new and (follower in rec.backend_ids or follower in rec.awaiting or follower in rec.degraded):
                continue  # it already has this start (a retry must not create a second one)
            if not ready:
                rec.degraded.add(follower)
                continue
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
            rec = self._start_msgs.get(parsed[1])
            if rec is None or rec.leader_msg != parsed[1] or rec.state != "pending":
                return LeaderPlan(raw)
            if kind == CALLRESULT:
                return self._leader_started(rec, leader, parsed, raw)
            extra = [_dump([CALLERROR, alias, *parsed[2:]]) for alias in rec.aliases]
            self._leader_gave_up(rec)
            return LeaderPlan(raw, extra)

        if kind == CALL and len(parsed) >= 4 and isinstance(parsed[2], str):
            path = _BACKEND_ID_FIELDS.get(parsed[2])
            if path is not None and isinstance(parsed[3], dict):
                value = _read(parsed[3], path)
                if value is not None:
                    return LeaderPlan(self._reverse(leader, parsed, raw, path, value))
        return LeaderPlan(raw)

    def _leader_started(self, rec: TxRecord, leader: str, parsed: list, raw: str) -> LeaderPlan:
        payload = parsed[2] if len(parsed) > 2 and isinstance(parsed[2], dict) else None
        issued = payload.get("transactionId") if payload is not None else None
        if payload is None or not _is_int(issued):
            logger.warning("StartTransaction result without a usable transactionId; passed through untouched")
            self._leader_gave_up(rec)
            return LeaderPlan(raw)

        tx_id = self._choose(issued)
        rec.tx_id, rec.state = tx_id, "open"
        rec.backend_ids[leader] = issued
        rec.degraded.discard(leader)
        self._by_tx[tx_id] = rec
        self._by_backend[(leader, issued)] = rec
        conf = dict(payload)
        conf["transactionId"] = tx_id
        rec.conf = conf

        frame = raw if tx_id == issued else _dump([CALLRESULT, parsed[1], conf])
        extra = [_dump([CALLRESULT, alias, conf]) for alias in rec.aliases]
        rec.aliases.clear()
        rec.leader_msg = None
        self._collect(rec)
        return LeaderPlan(frame, extra)

    def _leader_gave_up(self, rec: TxRecord) -> None:
        rec.leader_msg = None
        rec.aliases.clear()
        self._collect(rec)

    def _reverse(self, leader: str, parsed: list, raw: str, path: Tuple[str, ...], issued: int) -> str:
        rec = self._by_backend.get((leader, issued))
        if rec is None or rec.tx_id is None:
            self.stats["unmapped"] += 1
            logger.warning("Leader quoted transaction id %s that the id table does not know; passed through", issued)
            return raw
        if rec.tx_id == issued:
            return raw
        self.stats["rewritten"] += 1
        return _write(parsed, path, rec.tx_id, payload_index=3)

    def start_failed(self, msg_id: str) -> List[str]:
        """
        The broker answered a start with its own CALLERROR (backend unavailable).
        Forget the attempt; returns CALLERRORs for any retries that were waiting on it.
        """
        rec = self._start_msgs.get(msg_id)
        if rec is None or rec.leader_msg != msg_id:
            return []
        extra = [
            _dump([CALLERROR, alias, "InternalError", "Backend unavailable, please retry", {}])
            for alias in rec.aliases
        ]
        self._leader_gave_up(rec)
        return extra

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
        rec = self._start_msgs.get(parsed[1])
        if rec is None or follower not in rec.awaiting:
            return []
        rec.awaiting.discard(follower)
        rec.awaiting_since.pop(follower, None)
        issued = parsed[2].get("transactionId") if parsed[0] == CALLRESULT and isinstance(parsed[2], dict) else None
        if _is_int(issued):
            rec.backend_ids[follower] = issued
            self._by_backend[(follower, issued)] = rec
        else:
            rec.degraded.add(follower)
            logger.warning("Follower %s gave no transaction id for a start; its copies for it are skipped", follower)
        self._collect(rec)
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
            out.append(item.raw)
            queue.popleft()
        return out

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
            if rec.state == "pending" and rec.leader_msg is not None:
                self._leader_gave_up(rec)
        if new_leader is not None:
            for item in self._held.pop(new_leader, deque()):
                if item.rec is not None:
                    item.rec.degraded.add(new_leader)
                    item.rec.awaiting.discard(new_leader)
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
        self._collect(rec)

    def _collect(self, rec: TxRecord) -> None:
        """Forget message-id bookkeeping nobody is waiting on any more, and dead records."""
        if rec.leader_msg is None and not rec.awaiting:
            for msg in rec.msgs:
                if self._start_msgs.get(msg) is rec:
                    del self._start_msgs[msg]
            rec.msgs.clear()
        if rec.state == "pending" and rec.leader_msg is None and not rec.backend_ids and not rec.awaiting:
            if self._by_start.get(rec.start_key) is rec:
                del self._by_start[rec.start_key]

    def _prune(self) -> None:
        now = self._clock()
        for rec in list(self._by_tx.values()):
            if rec.state == "closed" and rec.closed_at is not None and now - rec.closed_at > self.retain_closed:
                self._forget(rec)
        stale_pending = max(self.stale_start * 5, 300.0)
        for rec in list(self._by_start.values()):
            if rec.state == "pending" and now - rec.created > stale_pending:
                self._forget(rec)

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

    def is_idle(self) -> bool:
        """Nothing worth keeping: safe to drop the table."""
        self._prune()
        return not (self._by_tx or self._by_start or self._start_msgs or any(self._held.values()))

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


def backend_keys(backends: Iterable[Dict[str, Any]]) -> List[str]:
    """
    Stable, unique key per configured backend: its ``id`` if given, else its URL.
    A repeated key gets ``#2``, ``#3``... so two backends never share a table entry.
    """
    seen: Counter = Counter()
    keys = []
    for backend in backends:
        base = str(backend.get("id") or backend.get("url") or "backend")
        seen[base] += 1
        keys.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return keys
