import asyncio
import json
import logging
import websockets

logger = logging.getLogger("ocpp_broker.backend_manager")

class BackendConnection:
    """
    Represents a persistent websocket connection to an upstream OCPP backend.
    """

    def __init__(self, broker, backend_id: str, url: str, leader: bool = False, org: str = "default"):
        self.broker = broker
        self.id = backend_id
        self.url = url
        self.org = org
        self.is_leader = leader
        self.websocket = None
        self._connect_task = None
        self._running = False

    async def connect(self):
        if self._running:
            return
        self._running = True
        self._connect_task = asyncio.create_task(self._run_connect_loop())

    async def close(self):
        self._running = False
        try:
            if self.websocket and not self.websocket.closed:
                await self.websocket.close()
        except Exception:
            pass
        if self._connect_task:
            self._connect_task.cancel()

    async def _run_connect_loop(self):
        backoff = 1
        while self._running:
            try:
                logger.info(f"Connecting to backend {self.id} ({self.org}) -> {self.url}")
                async with websockets.connect(self.url) as ws:
                    self.websocket = ws
                    logger.info(f"Connected to backend {self.id} ({self.org})")
                    # notify broker about connection
                    self.broker._on_backend_connected(self)
                    await self._reader_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Backend {self.id} ({self.org}) connection error: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                # notify broker about disconnect
                try:
                    self.broker._on_backend_disconnected(self)
                except Exception:
                    pass
                self.websocket = None
                await asyncio.sleep(1)

    async def _reader_loop(self):
        assert self.websocket is not None
        async for msg in self.websocket:
            await self._handle_message(msg)

    async def _handle_message(self, msg: str):
        try:
            data = json.loads(msg)
        except Exception:
            data = None

        # If message is a registry update
        if isinstance(data, dict) and data.get("type") == "registry":
            chargers = data.get("chargers", [])
            try:
                await self.broker.get_registry(self.org).update_from_backend(self.id, chargers)
            except Exception as e:
                logger.exception(f"Error updating registry from backend {self.id}: {e}")
        # If message is leader toggle
        elif isinstance(data, dict) and data.get("type") == "leader":
            leader_id = data.get("leader_id")
            if leader_id:
                self.broker.promote_leader(self.org, leader_id)
        else:
            # delegate to command router for possible forwarding
            await self.broker.command_router.route_backend_message(self, msg)

    async def send(self, message: str):
        if self.websocket and not self.websocket.closed:
            try:
                await self.websocket.send(message)
            except Exception as e:
                logger.warning(f"Error sending to backend {self.id}: {e}")
        else:
            logger.warning(f"Cannot send to backend {self.id}: not connected.")
