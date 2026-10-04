"""
Keeps the transaction id table across broker restarts, in MongoDB.

``TransactionIdTable`` calls ``on_change`` whenever a record worth keeping
changes. ``TransactionStore.save`` / ``delete`` only note the change in memory
and a background task writes it, so a slow or unreachable database never delays
a charger. The queue is bounded, coalesces repeated changes to one record, and
retries with backoff. At a crash the last few changes can be lost: the record
then comes back one step stale, or not at all (the leader keeps working either
way, because its ids are the charger's ids).

Records expire in MongoDB through a TTL index, so nothing needs cleaning up.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import ConnectionFailure

logger = logging.getLogger("ocpp_broker.transaction_store")


class _Unreachable(Exception):
    """MongoDB is not connected; the write is kept and tried again."""


class TransactionStore:
    def __init__(
        self,
        mongodb: Any,
        *,
        max_pending: int = 5000,
        retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
        max_attempts: int = 5,
    ):
        self._mongo = mongodb
        self.max_pending = max_pending
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.max_retry_delay = max_retry_delay
        # uid -> ("save", org, charger, document, expires_at) or ("delete",)
        self._pending: "OrderedDict[str, Tuple[Any, ...]]" = OrderedDict()
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._indexed = False
        self.stats: Counter = Counter()

    # ------------------------------------------------------------------
    # Called from the table's on_change hook (synchronous, never blocks)
    # ------------------------------------------------------------------
    def save(self, org: str, charger: str, uid: str, document: Dict[str, Any], expires_at: float) -> None:
        self._enqueue(uid, ("save", org, charger, document, expires_at))

    def delete(self, uid: str) -> None:
        self._enqueue(uid, ("delete",))

    def _enqueue(self, uid: str, op: Tuple[Any, ...]) -> None:
        if uid in self._pending:
            del self._pending[uid]  # a newer change replaces an older one, and goes to the back
        elif len(self._pending) >= self.max_pending:
            self._pending.popitem(last=False)
            self.stats["dropped"] += 1
            logger.warning("Transaction id store is backed up (%d changes waiting); dropped the oldest", self.max_pending)
        self._pending[uid] = op
        self._idle.clear()
        self._wake.set()
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    async def load(self, org: str, charger: str) -> List[Dict[str, Any]]:
        """Stored records for one charger; an unreachable database yields none (never fails a session)."""
        if not self._mongo.is_connected():
            return []
        try:
            await self._ensure_indexes()
            return await self._mongo.load_transaction_map_docs(org, charger)
        except Exception as exc:
            logger.warning("Could not load stored transaction ids for %s/%s: %s", org, charger, exc)
            return []

    async def _ensure_indexes(self) -> None:
        if not self._indexed:
            await self._mongo.ensure_transaction_map_indexes()
            self._indexed = True

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    async def _run(self) -> None:
        delay = self.retry_delay
        rejected = 0  # consecutive failures of the record at the head while the database is reachable
        while True:
            await self._wake.wait()
            self._wake.clear()
            while self._pending:
                uid, op = next(iter(self._pending.items()))
                del self._pending[uid]
                try:
                    await self._write(uid, op)
                except asyncio.CancelledError:
                    raise
                except (_Unreachable, ConnectionFailure) as exc:
                    # An outage is waited out, never counted against the record
                    self.stats["deferred"] += 1
                    logger.warning("Transaction id store is waiting for MongoDB (retry in %.0fs): %s", delay, exc)
                except Exception as exc:
                    rejected += 1
                    self.stats["failed"] += 1
                    if rejected >= self.max_attempts:
                        # Something about this one record is refused; do not let it block the rest
                        self.stats["dropped"] += 1
                        logger.error("Giving up on a transaction id record MongoDB keeps rejecting: %s", exc)
                        rejected = 0
                        continue
                    logger.warning("Could not store a transaction id record (retry in %.0fs): %s", delay, exc)
                else:
                    delay, rejected = self.retry_delay, 0
                    self.stats["written"] += 1
                    continue
                if uid not in self._pending:  # unless a newer change superseded it
                    self._pending[uid] = op
                    self._pending.move_to_end(uid, last=False)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_retry_delay)
            self._idle.set()

    async def _write(self, uid: str, op: Tuple[Any, ...]) -> None:
        if not self._mongo.is_connected():
            raise _Unreachable("MongoDB is not connected")
        await self._ensure_indexes()
        if op[0] == "delete":
            await self._mongo.delete_transaction_map_doc(uid)
            return
        _, org, charger, document, expires_at = op
        await self._mongo.save_transaction_map_doc(
            org, charger, document, datetime.fromtimestamp(expires_at, tz=timezone.utc)
        )

    async def flush(self, timeout: float = 5.0) -> bool:
        """Wait until everything noted so far has been written. False if it did not finish in time."""
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def close(self, timeout: float = 5.0) -> None:
        await self.flush(timeout)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
