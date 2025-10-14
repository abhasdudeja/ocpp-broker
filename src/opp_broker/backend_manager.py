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
        if self.websocket and not self.websocket.closed:
            await self.websocket.close()
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
                    await self._reader_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Backend {self.id} ({self.org}) connection error: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                self.websocket = None
                await asyncio.sleep(1)

    async def _reader_loop(self):
        async for msg in self.websocket:
            await self._handle_message(msg)

    async def _handle_message(self, msg: str):
        try:
            data = json.loads(msg)
        except Exception:
            data = None

        if isinstance(data, dict) and data.get("type") == "registry":
            chargers = data.get("chargers", [])
            await self.broker.get_registry(self.org).update_from_backend(self.id, chargers)
        else:
            await self.broker.command_router.route_backend_message(self, msg)

    async def send(self, message: str):
        if self.websocket and not self.websocket.closed:
            await self.websocket.send(message)
        else:
            logger.warning(f"Cannot send to backend {self.id}: not connected.")
