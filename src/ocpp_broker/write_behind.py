"""
Writes to MongoDB that must never make a charger wait.

The broker stores what chargers say (statuses, meter values, transactions, heartbeats ...). Doing that before
answering ties every reply to the database: with a database a few hundred milliseconds away each reply takes
that much longer, and with one that is down the library, which handles a charger's messages one at a time, waits
for the driver's timeout before it answers anything. So the answer goes out first and the write happens here, in
the background, in order.

* **Bounded.** At most ``max_pending`` writes wait. Beyond that the newest is dropped and counted: losing a
  status record is better than holding memory (or a charger) hostage. ``dropped`` and ``failed`` are in the
  stats, which ``GET /api/system/info`` shows.
* **Coalescing.** A write submitted with a ``key`` replaces a still-waiting write with the same key, so a
  heartbeat per charger is stored once however long the database was away.
* **In order, per charger.** A lane's worker runs its writes one after the other, and one charger's writes always share a
  lane, so a transaction's start is stored before its stop; different chargers' writes run side by side in the
  other lanes (see ``WriteBehind``).
* **Calm during an outage.** A write that takes longer than ``timeout`` (the database is not answering) is kept
  and tried again; after ``breaker`` consecutive failures the worker waits ``cooldown`` seconds between attempts
  instead of hammering a database that is down, and carries on in order when it answers again (a write that was
  cut off may have reached the database, so a retry can store it twice). A write that fails for another reason is
  tried ``max_attempts`` times and then dropped, so one bad record cannot block the rest. Failures are counted and
  logged, but not every one.
* **Flushed on shutdown.** ``flush`` waits (briefly) for what is pending.

Anything whose answer the charger needs (a transaction id from the MongoDB counter) is not a write-behind and is
awaited, with a time limit, where it is used.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, Hashable, Optional

logger = logging.getLogger("ocpp_broker.write_behind")

DEFAULT_MAX_PENDING = 10_000
DEFAULT_TIMEOUT = 5.0  # seconds one write may take
DEFAULT_BREAKER = 3  # consecutive failures after which the worker slows down
DEFAULT_COOLDOWN = 5.0  # seconds between attempts while the database seems to be down
DEFAULT_LANES = 8  # independent in-order queues; one charger's writes always share one
LOG_EVERY = 30.0  # seconds between warnings about failing writes

Call = Callable[[], Awaitable[Any]]


class _Item:
    __slots__ = ("call", "label", "attempts")

    def __init__(self, call: Call, label: str):
        self.call = call
        self.label = label
        self.attempts = 0  # failures other than a timeout; a record the database keeps rejecting is dropped


class _Lane:
    """One in-order queue with its own worker (see WriteBehind)."""

    def __init__(
        self,
        max_pending: int = DEFAULT_MAX_PENDING,
        timeout: float = DEFAULT_TIMEOUT,
        breaker: int = DEFAULT_BREAKER,
        cooldown: float = DEFAULT_COOLDOWN,
        max_attempts: int = 3,
    ):
        self.max_attempts = max_attempts
        self.max_pending = max_pending
        self.timeout = timeout
        self.breaker = breaker
        self.cooldown = cooldown
        self._pending: "OrderedDict[Any, _Item]" = OrderedDict()
        self._serial = 0  # keys of writes without a key of their own
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: Optional["asyncio.Task[None]"] = None
        self._closed = False
        self._consecutive_failures = 0
        self._last_warning = 0.0
        self.written = 0
        self.failed = 0
        self.dropped = 0
        self.coalesced = 0

    @property
    def pending(self) -> int:
        return len(self._pending)

    @property
    def degraded(self) -> bool:
        """The database looks to be down: recent writes failed one after another."""
        return self._consecutive_failures >= self.breaker

    def stats(self) -> Dict[str, Any]:
        return {
            "pending": self.pending,
            "written": self.written,
            "failed": self.failed,
            "dropped": self.dropped,
            "coalesced": self.coalesced,
            "degraded": self.degraded,
        }

    def submit(self, call: Call, label: str = "write", key: Optional[Hashable] = None) -> bool:
        """
        Queue ``call`` (a function that returns the awaitable to run) and return at once. ``False`` means it
        was dropped because the queue is full or closed. A ``key`` replaces a waiting write with the same key.
        """
        self._bind()
        if self._closed:
            return False
        if key is not None and key in self._pending:
            self._pending[key] = _Item(call, label)  # same place in the line, newest content
            self.coalesced += 1
            return True
        if len(self._pending) >= self.max_pending:
            self.dropped += 1
            self._warn(f"MongoDB writes are backing up ({self.max_pending} waiting): dropping the newest ({label})")
            return False
        if key is None:
            self._serial += 1
            key = ("write", self._serial)
        self._pending[key] = _Item(call, label)
        self._idle.clear()
        self._ensure_worker()
        self._wake.set()
        return True

    def _bind(self) -> None:
        """Use the running event loop. (A process has one; only tests, which make a loop each, ever change it.)"""
        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return
        if self._loop is not None:  # a new loop: whatever belonged to the old one is gone, and so is its shutdown
            self._pending.clear()
            self._worker = None
            self._closed = False
            self._consecutive_failures = 0
        self._loop = loop
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        while True:
            if not self._pending:
                self._idle.set()
                self._wake.clear()
                if self._closed:
                    return
                await self._wake.wait()
                continue
            if self.degraded:
                await asyncio.sleep(self.cooldown)  # one attempt per cooldown while the database is down
            key, item = next(iter(self._pending.items()))
            call, label = item.call, item.label
            try:
                await asyncio.wait_for(call(), self.timeout)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                # The database did not answer: an outage, not a bad record. Keep the write and try again later.
                self.failed += 1
                self._consecutive_failures += 1
                self._warn(f"A MongoDB write ({label}) did not finish within {self.timeout:g} s; it will be tried again")
                continue
            except Exception as exc:
                self.failed += 1
                self._consecutive_failures += 1
                item.attempts += 1
                self._warn(f"A MongoDB write ({label}) failed: {exc.__class__.__name__}: {exc}")
                if item.attempts < self.max_attempts:
                    continue
            else:
                self.written += 1
                if self._consecutive_failures >= self.breaker:
                    logger.info("MongoDB answers again; writing the %d waiting record(s)", len(self._pending))
                self._consecutive_failures = 0
            # Done (or given up on): forget it. If a write with its key replaced the content while it ran, that one goes next.
            if self._pending.get(key) is item:
                del self._pending[key]

    def _warn(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_warning >= LOG_EVERY:
            self._last_warning = now
            logger.warning(message)
        else:
            logger.debug(message)

    async def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait until everything submitted so far is written (or given up on). True if the queue emptied in time."""
        if not self._pending:
            return True
        try:
            await asyncio.wait_for(self._idle.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def close(self, flush_timeout: float = 5.0) -> None:
        """Stop accepting writes, give what is waiting ``flush_timeout`` seconds, then stop the worker."""
        self._closed = True
        flushed = await self.flush(flush_timeout)
        if not flushed:
            logger.warning("Stopping with %d MongoDB write(s) not yet stored", len(self._pending))
        self._wake.set()
        worker, self._worker = self._worker, None
        if worker is not None:
            if flushed:
                await worker
            else:
                worker.cancel()
                try:
                    await worker
                except (asyncio.CancelledError, Exception):
                    pass


class WriteBehind:
    """
    The broker's write-behind queue: ``lanes`` independent in-order queues, each with its own worker. Writes that
    carry the same ``shard`` (a charger) always go to the same lane, so each charger's records are stored in
    order, while different chargers' writes go to MongoDB side by side: over a link with 100 ms latency one worker
    could store only ten records a second.

    ``max_pending`` applies to each lane. Everything else is as described in the module docstring.
    """

    def __init__(
        self,
        max_pending: int = DEFAULT_MAX_PENDING,
        timeout: float = DEFAULT_TIMEOUT,
        breaker: int = DEFAULT_BREAKER,
        cooldown: float = DEFAULT_COOLDOWN,
        max_attempts: int = 3,
        lanes: int = DEFAULT_LANES,
    ):
        self._lanes = [_Lane(max_pending, timeout, breaker, cooldown, max_attempts) for _ in range(max(1, lanes))]

    def submit(self, call: Call, label: str = "write", key: Optional[Hashable] = None, shard: Optional[Hashable] = None) -> bool:
        lane = self._lanes[hash(shard) % len(self._lanes)] if shard is not None else self._lanes[0]
        return lane.submit(call, label, key)

    @property
    def pending(self) -> int:
        return sum(lane.pending for lane in self._lanes)

    @property
    def written(self) -> int:
        return sum(lane.written for lane in self._lanes)

    @property
    def failed(self) -> int:
        return sum(lane.failed for lane in self._lanes)

    @property
    def dropped(self) -> int:
        return sum(lane.dropped for lane in self._lanes)

    @property
    def coalesced(self) -> int:
        return sum(lane.coalesced for lane in self._lanes)

    @property
    def degraded(self) -> bool:
        """MongoDB looks to be down: recent writes failed one after another in some lane."""
        return any(lane.degraded for lane in self._lanes)

    def stats(self) -> Dict[str, Any]:
        return {
            "pending": self.pending,
            "written": self.written,
            "failed": self.failed,
            "dropped": self.dropped,
            "coalesced": self.coalesced,
            "degraded": self.degraded,
        }

    async def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait until everything submitted so far is written (or given up on). True if all lanes emptied in time."""
        results = await asyncio.gather(*(lane.flush(timeout) for lane in self._lanes))
        return all(results)

    async def close(self, flush_timeout: float = 5.0) -> None:
        await asyncio.gather(*(lane.close(flush_timeout) for lane in self._lanes))


class _Background:
    """
    The MongoDB service as the message handlers see it: a call such as ``save_status_notification(...)`` is
    queued on the broker's ``WriteBehind`` and returns at once, so the handler can answer the charger.
    Heartbeat times are coalesced per charger.
    """

    def __init__(self, service: Any, writes: WriteBehind):
        self._service = service
        self._writes = writes

    def is_connected(self) -> bool:
        return bool(self._service.is_connected())

    def __getattr__(self, method: str) -> Callable[..., Awaitable[None]]:
        function = getattr(self._service, method)

        async def queue(**kwargs: Any) -> None:
            key = ("heartbeat", kwargs.get("org_name"), kwargs.get("charger_id")) if method == "update_heartbeat_timestamp" else None
            shard = (kwargs.get("org_name"), kwargs.get("charger_id"))
            self._writes.submit(lambda: function(**kwargs), label=method, key=key, shard=shard if shard != (None, None) else None)

        return queue


def background(broker: Any) -> Any:
    """
    ``broker.mongodb_service`` with its writes queued (see ``_Background``), the service itself where the broker has
    no writer (a stand-in broker), or None when MongoDB is not in use.
    """
    service = getattr(broker, "mongodb_service", None)
    if service is None:
        return None
    writes = getattr(broker, "writes", None)
    return _Background(service, writes) if isinstance(writes, WriteBehind) else service
