"""
The transactions the broker itself numbered for one charger (broker mode, and the local leader).

A real central system knows which transactions are running. This remembers, per charger and for as long
as the broker runs:

* a ``StartTransaction`` that was already answered, so a charger that retries it (it missed the answer)
  gets the same transaction id and not a second transaction;
* which transactions are open, so a ``StopTransaction`` can be recognised as a duplicate or as quoting
  a transaction this broker never started, and so the web console can list them.

It is in memory only and bounded. After a restart it is empty, so a start retried across a restart gets a
new id. (With several backends the transaction id table does the same job for the charger's start and
more; see transaction_ids.py.)
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_OPEN = 100  # open transactions remembered per charger; the oldest is forgotten beyond this
MAX_CLOSED = 200  # finished ones remembered per charger
RETAIN_CLOSED = 86_400.0  # seconds a finished transaction is remembered, to recognise a repeated stop or start


def start_key(connector_id: Any, id_tag: Any, meter_start: Any, timestamp: Any) -> Tuple[Any, ...]:
    """What identifies a retry of the same StartTransaction: all four are fixed by the charger."""
    return (connector_id, id_tag, meter_start, timestamp)


@dataclass
class LocalTransaction:
    tx_id: int
    key: Tuple[Any, ...]
    connector_id: Optional[int]
    id_tag: Optional[str]
    meter_start: Optional[int]
    state: str = "open"  # open | closed
    meter_stop: Optional[int] = None
    reason: Optional[str] = None
    closed_at: float = 0.0


class LocalTransactions:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._by_id: "OrderedDict[int, LocalTransaction]" = OrderedDict()
        self._by_key: Dict[Tuple[Any, ...], LocalTransaction] = {}
        self.stats: Dict[str, int] = {"started": 0, "retried_starts": 0, "stopped": 0, "repeated_stops": 0, "unknown_stops": 0}

    def find_start(self, key: Tuple[Any, ...]) -> Optional[LocalTransaction]:
        """The transaction a start with this key already created (a retry), or None."""
        self._prune()
        found = self._by_key.get(key)
        if found is not None:
            self.stats["retried_starts"] += 1
        return found

    def started(
        self, key: Tuple[Any, ...], tx_id: int, connector_id: Optional[int], id_tag: Optional[str], meter_start: Optional[int]
    ) -> LocalTransaction:
        self._prune()
        tx = LocalTransaction(tx_id, key, connector_id, id_tag, meter_start)
        self._by_id[tx_id] = tx
        self._by_key[key] = tx
        self.stats["started"] += 1
        while sum(1 for t in self._by_id.values() if t.state == "open") > MAX_OPEN:
            oldest = next(t for t in self._by_id.values() if t.state == "open")
            self._forget(oldest)
        return tx

    def stopped(self, tx_id: int, meter_stop: Optional[int], reason: Optional[str]) -> Tuple[Optional[LocalTransaction], bool]:
        """
        Record a stop. Returns the transaction and whether this stop repeats an earlier one;
        the transaction is None when this broker never started it (or has forgotten it).
        """
        self._prune()
        tx = self._by_id.get(tx_id)
        if tx is None:
            self.stats["unknown_stops"] += 1
            return None, False
        if tx.state == "closed":
            self.stats["repeated_stops"] += 1
            return tx, True
        tx.state, tx.meter_stop, tx.reason, tx.closed_at = "closed", meter_stop, reason, self._clock()
        self.stats["stopped"] += 1
        return tx, False

    def open_count(self) -> int:
        return sum(1 for t in self._by_id.values() if t.state == "open")

    def is_idle(self) -> bool:
        """Nothing worth keeping if the charger goes away: no transaction is running."""
        return self.open_count() == 0

    def snapshot(self) -> List[Dict[str, Any]]:
        """The transactions for display, oldest first (shape of the transaction id table's rows)."""
        self._prune()
        return [{"transaction_id": t.tx_id, "state": t.state, "connector_id": t.connector_id} for t in self._by_id.values()]

    def _forget(self, tx: LocalTransaction) -> None:
        self._by_id.pop(tx.tx_id, None)
        if self._by_key.get(tx.key) is tx:
            del self._by_key[tx.key]

    def _prune(self) -> None:
        now = self._clock()
        closed = [t for t in self._by_id.values() if t.state == "closed"]
        for tx in closed:
            if now - tx.closed_at > RETAIN_CLOSED:
                self._forget(tx)
        closed = [t for t in self._by_id.values() if t.state == "closed"]
        for tx in closed[: max(0, len(closed) - MAX_CLOSED)]:
            self._forget(tx)
