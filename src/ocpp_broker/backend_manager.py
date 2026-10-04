import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Deque, Optional, Set

import websockets
from websockets.typing import Subprotocol

from .sockets import locked_send

logger = logging.getLogger("ocpp_broker.backend_manager")

# Frames held for a backend that is down; beyond this new frames are refused.
DEFAULT_MAX_BUFFERED = 200
# Seconds a frame may wait for the backend before it is given up on.
DEFAULT_OUTAGE_TIMEOUT = 30.0
# Pause before reconnecting (doubles on consecutive failures, up to 30s).
RECONNECT_DELAY = 1.0


@dataclass
class _Buffered:
    message: str
    queued_at: float


class BackendConnection:
    """
    Represents a 1-to-1 persistent WebSocket link between the broker (acting as a charger)
    and the upstream OCPP backend. Each charger has its own BackendConnection.

    Frames sent while the backend is unreachable are held in a bounded outbox and
    delivered, in order, when the link comes back. A frame that waits longer than
    ``outage_timeout`` (or does not fit in the outbox) is handed to
    ``on_undeliverable`` so the caller can answer the charger instead of leaving it
    waiting.
    """

    def __init__(
        self,
        broker,
        charger_id: str,
        url: str,
        org: str = "default",
        is_leader: bool = False,
        subprotocol: str = "ocpp1.6",
        max_buffered: int = DEFAULT_MAX_BUFFERED,
        outage_timeout: float = DEFAULT_OUTAGE_TIMEOUT,
        on_undeliverable: Optional[Callable[[str], Awaitable[None]]] = None,
        on_disconnected: Optional[Callable[["BackendConnection"], None]] = None,
        on_connected: Optional[Callable[["BackendConnection"], None]] = None,
        key: Optional[str] = None,
    ):
        self.broker = broker
        self.id = charger_id            # Charger ID used for backend identification
        self.url = url.rstrip("/")      # Base URL (from config)
        # Names this backend in the transaction id table; stable across reconnects
        # and failover (the configured ``id``, else the URL).
        self.key = key or self.url
        self.org = org
        self.is_leader = is_leader     # Leader/follower status
        self.subprotocol = subprotocol  # OCPP subprotocol (e.g., "ocpp1.6", "ocpp2.0.1")
        self.websocket: Any = None
        self._connect_task: Optional[asyncio.Task] = None
        self._running = False
        self.connected_event = asyncio.Event()  # signals when backend connection is ready
        self._send_lock = asyncio.Lock()  # one writer at a time on the backend socket

        self.max_buffered = max_buffered
        self.outage_timeout = outage_timeout
        self.on_undeliverable = on_undeliverable
        self.on_disconnected = on_disconnected
        self.on_connected = on_connected
        self.reconnect_delay = RECONNECT_DELAY
        # monotonic time the link was lost; None while connected (or never lost)
        self.disconnected_since: Optional[float] = None
        self._outage_notified = False  # on_disconnected fires once per outage

        self._outbox: Deque[_Buffered] = deque()
        self._flushing = False
        self._expiry_handle: Optional[asyncio.TimerHandle] = None
        self._tasks: Set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self, wait: bool = True):
        """Start the backend connection loop; optionally wait until it is connected."""
        if self._running:
            return
        self._running = True
        self.disconnected_since = time.monotonic()
        self._connect_task = asyncio.create_task(self._run_connect_loop())
        if wait:
            await self.connected_event.wait()

    def _is_websocket_closed(self):
        """Check if websocket is closed, handling different websocket types."""
        if not self.websocket:
            return True
        try:
            # Try the 'closed' attribute first (for some websocket implementations)
            if hasattr(self.websocket, 'closed'):
                return self.websocket.closed
            # For websockets library, check close_code (None means open)
            if hasattr(self.websocket, 'close_code'):
                return self.websocket.close_code is not None
            # Default: assume open if we can't determine
            return False
        except AttributeError:
            # If attribute doesn't exist, assume open
            return False

    def is_ready(self) -> bool:
        """True when a frame can be written to the backend right now."""
        return (
            self.connected_event.is_set()
            and self.websocket is not None
            and not self._is_websocket_closed()
        )

    async def close(self):
        """Close active backend connection gracefully."""
        logger.info(f"Closing backend connection for charger {self.id}")
        self._running = False  # Stop the connection loop
        self.connected_event.clear()  # Signal that connection is no longer ready
        self._discard_outbox()

        # Cancel the connection task if it's running
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Error cancelling connect task for {self.id}: {e}")

        # Close the websocket if it's open
        try:
            if self.websocket and not self._is_websocket_closed():
                await self.websocket.close()
        except Exception as e:
            logger.debug(f"Error closing websocket for {self.id}: {e}")

        self.websocket = None
        for task in list(self._tasks):
            task.cancel()
        logger.debug(f"Backend connection closed for charger {self.id}")

    def _is_charger_still_connected(self) -> bool:
        """Check if the charger is still connected to the broker before connecting to backend."""
        session = self.broker.sessions.get((self.org, self.id))
        if not session:
            return False
        # Check if charger websocket is still connected
        if not session.websocket:
            return False
        try:
            client_state = getattr(session.websocket, "client_state", None)
            if client_state:
                state_name = getattr(client_state, "name", None)
                return state_name != "DISCONNECTED"
            return True
        except Exception:
            return False

    async def _run_connect_loop(self):
        """Continuously try to connect to backend until stopped."""
        backoff = self.reconnect_delay
        while self._running:
            # Verify charger is still connected before attempting backend connection
            if not self._is_charger_still_connected():
                logger.warning(f"⚠️ Charger {self.id} disconnected, stopping backend connection attempt")
                break

            keep_going = True
            try:
                target_url = f"{self.url}/{self.id}"  # ✅ append charger_id
                logger.info(f"Connecting to backend for charger {self.id} ({self.org}) -> {target_url}")

                async with websockets.connect(
                    target_url,
                    subprotocols=[Subprotocol(self.subprotocol)],  # Use configured subprotocol
                    ping_interval=20,  # Send ping every 20 seconds (OCPP compatible)
                    ping_timeout=20,  # Wait 20 seconds for pong response
                    close_timeout=5,  # Wait 5 seconds for close handshake
                    max_size=2**20,  # 1MB max message size (OCPP recommendation)
                    max_queue=32  # Max queued messages
                ) as ws:
                    self.websocket = ws

                    # Verify configured subprotocol was negotiated
                    negotiated_subprotocol = getattr(ws, 'subprotocol', None)
                    if negotiated_subprotocol != self.subprotocol:
                        logger.error(
                            f"❌ OCPP subprotocol not negotiated for {self.id}! "
                            f"Expected '{self.subprotocol}', got '{negotiated_subprotocol}'. "
                            f"Connection may not be OCPP compliant."
                        )
                        # Continue anyway, but log the warning
                    else:
                        logger.info(f"✅ OCPP subprotocol '{self.subprotocol}' verified for charger {self.id}")

                    # Verify charger is still connected before marking backend as ready
                    if not self._is_charger_still_connected():
                        logger.warning(f"⚠️ Charger {self.id} disconnected during backend connection, closing backend")
                        break

                    backoff = self.reconnect_delay
                    self.disconnected_since = None
                    self._outage_notified = False
                    self.connected_event.set()  # signal broker that backend connection is ready
                    logger.info(f"✅ Connected to backend for charger {self.id} ({self.org}) via {self.subprotocol}")
                    self.broker._on_backend_connected(self)
                    if self.on_connected is not None:
                        try:
                            self.on_connected(self)
                        except Exception:
                            logger.exception("on_connected callback failed for %s", self.id)

                    # Deliver anything that queued up while the backend was away
                    await self._flush_outbox()

                    # Run reader loop - it will exit if charger disconnects
                    await self._reader_loop()

                    # If we exit the reader loop, charger likely disconnected
                    if not self._is_charger_still_connected():
                        logger.info(f"Charger {self.id} disconnected, backend connection terminated")
                        break

            except asyncio.CancelledError:
                keep_going = False
            except Exception as e:
                # Check if charger disconnected during connection attempt
                if not self._is_charger_still_connected():
                    logger.info(f"Charger {self.id} disconnected during backend connection attempt, stopping")
                    keep_going = False
                else:
                    logger.warning(f"⚠️ Backend connection error for {self.id} ({self.org}): {e}")
                    backoff = min(backoff * 2, 30)
            finally:
                self._link_lost()
                try:
                    self.broker._on_backend_disconnected(self)
                except Exception:
                    pass
                self.websocket = None

            if not keep_going or not self._running or not self._is_charger_still_connected():
                break
            await asyncio.sleep(backoff)

    def _link_lost(self):
        """Mark the backend unavailable and tell the owner (once per outage)."""
        self.connected_event.clear()
        if self.disconnected_since is None:
            self.disconnected_since = time.monotonic()
        first_loss = not self._outage_notified
        self._outage_notified = True
        if first_loss and self._running and self.on_disconnected is not None:
            try:
                self.on_disconnected(self)
            except Exception:
                logger.exception("on_disconnected callback failed for %s", self.id)

    async def _reader_loop(self):
        """Receive messages from backend and forward to broker."""
        assert self.websocket is not None
        try:
            async for msg in self.websocket:
                # Check if charger is still connected before processing messages
                if not self._is_charger_still_connected():
                    logger.info(f"⚠️ Charger {self.id} disconnected, stopping backend message reader")
                    break
                await self._handle_message(msg)
        except Exception as e:
            if not self._is_charger_still_connected():
                logger.info(f"Charger {self.id} disconnected, backend reader loop stopped")
            else:
                logger.warning(f"Backend reader loop error for {self.id}: {e}")

    async def _handle_message(self, msg: str):
        """Handle messages coming from backend."""
        # Forward every backend message to broker (to send to charger)
        logger.info(f"[{self.id}] ← Message from backend: {msg[:200]}")  # log truncated message
        await self.broker.forward_backend_message(self, msg)

    # ------------------------------------------------------------------
    # Sending with store-and-forward
    # ------------------------------------------------------------------
    async def send(self, message: str):
        """
        Deliver an OCPP frame to the backend.

        If the backend is not reachable the frame is buffered and sent when the
        link returns; it is never silently dropped. Frames that cannot be
        buffered (outbox full) or that wait longer than ``outage_timeout`` go to
        ``on_undeliverable``.
        """
        # Keep order: anything already queued (or being flushed) goes first.
        if self._outbox or self._flushing or not self.is_ready():
            self._buffer(message)
            return

        try:
            await self._write(message)
            logger.debug(f"[{self.id}] → backend: {message[:200]}")
        except Exception as e:
            logger.warning(f"⚠️ Error sending to backend for {self.id}: {e}; buffering for retry")
            self.connected_event.clear()
            self._buffer(message)
            await self._drop_socket()  # make the reader loop notice and reconnect

    async def _write(self, message: str):
        await locked_send(self._send_lock, self.websocket.send, message)

    async def _drop_socket(self):
        try:
            if self.websocket is not None:
                await self.websocket.close()
        except Exception as e:
            logger.debug(f"Error closing broken backend socket for {self.id}: {e}")

    def _buffer(self, message: str):
        if self.max_buffered <= 0:
            logger.debug(f"Backend {self.url} for {self.id} is unreachable and unbuffered; dropping frame")
            return
        if len(self._outbox) >= self.max_buffered:
            logger.warning(
                f"⚠️ Backend unavailable for {self.id} and outbox full ({len(self._outbox)}/"
                f"{self.max_buffered}); refusing frame"
            )
            self._reject(message)
            return
        self._outbox.append(_Buffered(message, time.monotonic()))
        logger.info(f"⏳ Backend unavailable for {self.id}; buffered frame ({len(self._outbox)} waiting)")
        self._arm_expiry()

    async def _flush_outbox(self):
        """Send buffered frames, oldest first. Stops (keeping the rest) on the first failure."""
        if self._flushing:
            return
        self._flushing = True
        sent = 0
        try:
            while self._outbox and self.is_ready():
                entry = self._outbox.popleft()
                try:
                    await self._write(entry.message)
                except Exception as e:
                    self._outbox.appendleft(entry)
                    logger.warning(f"⚠️ Flush to backend for {self.id} interrupted: {e}")
                    break
                sent += 1
        finally:
            self._flushing = False
        if sent:
            logger.info(f"📤 Delivered {sent} buffered frame(s) to backend for {self.id}")
        if not self._outbox:
            self._cancel_expiry()

    def _arm_expiry(self):
        if self._expiry_handle is not None or not self._outbox:
            return
        delay = max(0.0, self._outbox[0].queued_at + self.outage_timeout - time.monotonic())
        self._expiry_handle = asyncio.get_running_loop().call_later(delay, self._expire)

    def _cancel_expiry(self):
        if self._expiry_handle is not None:
            self._expiry_handle.cancel()
            self._expiry_handle = None

    def _expire(self):
        self._expiry_handle = None
        now = time.monotonic()
        while self._outbox and now - self._outbox[0].queued_at >= self.outage_timeout:
            entry = self._outbox.popleft()
            logger.warning(
                f"⚠️ Backend still unavailable for {self.id} after {self.outage_timeout:.0f}s; giving up on frame"
            )
            self._reject(entry.message)
        self._arm_expiry()

    def _reject(self, message: str):
        # Capture the callback now: the owner may reassign it (role change) before
        # the notification task gets to run.
        callback = self.on_undeliverable
        if callback is None:
            return
        task = asyncio.ensure_future(self._notify_undeliverable(callback, message))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _notify_undeliverable(self, callback, message: str):
        try:
            await callback(message)
        except Exception:
            logger.exception("on_undeliverable callback failed for %s", self.id)

    def _discard_outbox(self):
        self._cancel_expiry()
        self._outbox.clear()

    def reject_buffered(self):
        """Give up on everything waiting (e.g. when another backend takes over)."""
        self._cancel_expiry()
        while self._outbox:
            self._reject(self._outbox.popleft().message)

    @property
    def buffered_count(self) -> int:
        return len(self._outbox)
