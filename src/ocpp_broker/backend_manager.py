import asyncio
import json
import logging
import websockets

logger = logging.getLogger("ocpp_broker.backend_manager")


class BackendConnection:
    """
    Represents a 1-to-1 persistent WebSocket link between the broker (acting as a charger)
    and the upstream OCPP backend. Each charger has its own BackendConnection.
    """

    def __init__(self, broker, charger_id: str, url: str, org: str = "default", is_leader: bool = False):
        self.broker = broker
        self.id = charger_id            # Charger ID used for backend identification
        self.url = url.rstrip("/")      # Base URL (from config)
        self.org = org
        self.is_leader = is_leader     # Leader/follower status
        self.websocket = None
        self._connect_task = None
        self._running = False
        self.connected_event = asyncio.Event()  # signals when backend connection is ready

    async def connect(self):
        """Start backend connection loop and wait until it's connected."""
        if self._running:
            return
        self._running = True
        self._connect_task = asyncio.create_task(self._run_connect_loop())
        # Wait for connection signal before continuing
        await self.connected_event.wait()

    async def close(self):
        """Close active backend connection gracefully."""
        self._running = False
        try:
            if self.websocket and not self.websocket.closed:
                await self.websocket.close()
        except Exception:
            pass
        if self._connect_task:
            self._connect_task.cancel()

    async def _run_connect_loop(self):
        """Continuously try to connect to backend until stopped."""
        backoff = 1
        while self._running:
            try:
                target_url = f"{self.url}/{self.id}"  # ✅ append charger_id
                logger.info(f"Connecting to backend for charger {self.id} ({self.org}) -> {target_url}")

                async with websockets.connect(
                    target_url,
                    subprotocols=["ocpp1.6"],
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5
                ) as ws:
                    self.websocket = ws
                    self.connected_event.set()  # signal broker that backend connection is ready
                    logger.info(f"✅ Connected to backend for charger {self.id} ({self.org}) via OCPP 1.6")
                    self.broker._on_backend_connected(self)
                    await self._reader_loop()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"⚠️ Backend connection error for {self.id} ({self.org}): {e}")
                self.connected_event.clear()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                try:
                    self.broker._on_backend_disconnected(self)
                except Exception:
                    pass
                self.websocket = None
                await asyncio.sleep(1)

    async def _reader_loop(self):
        """Receive messages from backend and forward to broker."""
        assert self.websocket is not None
        async for msg in self.websocket:
            await self._handle_message(msg)

    async def _handle_message(self, msg: str):
        """Handle messages coming from backend."""
        try:
            data = json.loads(msg)
        except Exception:
            data = None

        # Forward every backend message to broker (to send to charger)
        logger.info(f"[{self.id}] ← Message from backend: {msg[:200]}")  # log truncated message
        await self.broker.forward_backend_message(self, msg)

    async def send(self, message: str):
        """Send OCPP message to backend if connected."""
        if self.websocket and not self.websocket.closed:
            try:
                await self.websocket.send(message)
                logger.debug(f"[{self.id}] → backend: {message[:200]}")
            except Exception as e:
                logger.warning(f"⚠️ Error sending to backend for {self.id}: {e}")
        else:
            logger.warning(f"⚠️ Cannot send to backend for {self.id}: not connected.")
