"""Test doubles shared across the suite."""

import asyncio
import json

from starlette.websockets import WebSocketDisconnect


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
