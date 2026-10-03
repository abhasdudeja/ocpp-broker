"""Test doubles shared across the suite."""

import asyncio
import json

import websockets
from starlette.websockets import WebSocketDisconnect

API_KEY = "test-api-key"
AUTH_HEADERS = {"X-API-Key": API_KEY}


class FakeCharger:
    """Just enough of a Starlette WebSocket for a broker-mode session."""

    def __init__(self):
        self.headers = {"sec-websocket-protocol": "ocpp1.6"}
        self.client_state = type("S", (), {"name": "CONNECTED"})()
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[str] = []
        self.close_code = None

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            raise WebSocketDisconnect(code=1000)
        return item

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000, reason=None):
        self.close_code = code
        self.client_state = type("S", (), {"name": "DISCONNECTED"})()
        await self.inbox.put(None)

    def deliver(self, message):
        self.inbox.put_nowait(json.dumps(message))

    async def next_reply(self):
        for _ in range(100):
            if self.sent:
                return json.loads(self.sent.pop(0))
            await asyncio.sleep(0.01)
        raise AssertionError("no reply from broker")


class FakeBackend:
    """A real websockets server standing in for the central system."""

    def __init__(self, subprotocol="ocpp1.6"):
        self.subprotocol = subprotocol
        self.port = None
        self.received: list[str] = []  # every text frame, across connections
        self.paths: list[str] = []  # request path of each connection
        self.clients: list = []
        self._server = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ocpp"

    async def start(self, port: int = 0):
        self._server = await websockets.serve(
            self._handle, "127.0.0.1", port, subprotocols=[self.subprotocol]
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def _handle(self, ws):
        self.clients.append(ws)
        self.paths.append(ws.request.path)
        try:
            async for message in ws:
                self.received.append(message)
        except websockets.ConnectionClosed:
            pass

    async def stop(self):
        """Take the backend down: drops the listener and every open connection."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.clients.clear()

    async def send(self, frame):
        """Push a frame to the (most recent) connected client."""
        await self.clients[-1].send(json.dumps(frame))

    def received_frames(self) -> list:
        return [json.loads(m) for m in self.received]


async def wait_for(predicate, timeout: float = 3.0, interval: float = 0.01):
    """Poll until ``predicate()`` is truthy or fail the test."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not reached in time")


class ScriptedCharger:
    """A charge point driven by the test, speaking OCPP-J over a real WebSocket."""

    def __init__(self, ws):
        self.ws = ws
        self._ids = 0

    @classmethod
    async def connect(cls, port: int, org: str, charger_id: str, **kwargs):
        ws = await websockets.connect(
            f"ws://127.0.0.1:{port}/{org}/{charger_id}",
            subprotocols=["ocpp1.6"],
            ping_interval=None,
            **kwargs,
        )
        return cls(ws)

    def next_id(self, prefix="m") -> str:
        self._ids += 1
        return f"{prefix}-{self._ids}"

    async def send(self, frame) -> None:
        await self.ws.send(json.dumps(frame))

    async def call(self, action: str, payload: dict, message_id: str | None = None) -> str:
        """Send a CALL and return its id (read the answer with recv)."""
        message_id = message_id or self.next_id()
        await self.send([2, message_id, action, payload])
        return message_id

    async def recv(self, timeout: float = 5.0):
        return json.loads(await asyncio.wait_for(self.ws.recv(), timeout))

    async def close(self) -> None:
        await self.ws.close()


BOOT_PAYLOAD = {"chargePointVendor": "TestVendor", "chargePointModel": "TestModel"}
