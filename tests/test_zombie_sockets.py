"""
Ping/pong watchdog: a charger socket that stops answering pings is cleaned up.

These tests run the real uvicorn server (the keepalive lives in the server's
WebSocket layer) with a short ping interval, against a raw TCP client that can
play dead, and a normal websockets client that answers pings.
"""

import asyncio
import base64
import os
import socket

import pytest
import pytest_asyncio
import uvicorn
import websockets

from ocpp_broker import server

from .fakes import wait_for

ORGS = {"organizations": [{"name": "OrgA", "connect_to_backend": False}]}


def test_ping_settings_come_from_the_security_websocket_config():
    cfg = {
        "broker": {"host": "127.0.0.1", "port": 9999},
        "security": {"websocket": {"ping_interval": 7, "ping_timeout": 3}},
    }
    config = server.build_uvicorn_config(server.app, cfg)

    assert (config.host, config.port) == ("127.0.0.1", 9999)
    assert config.ws_ping_interval == 7
    assert config.ws_ping_timeout == 3


def test_ping_settings_default_to_20_seconds():
    config = server.build_uvicorn_config(server.app, {})
    assert (config.ws_ping_interval, config.ws_ping_timeout) == (20, 20)


@pytest_asyncio.fixture
async def live_server(monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", ORGS)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    cfg = {
        "broker": {"host": "127.0.0.1", "port": port},
        "security": {"websocket": {"ping_interval": 0.2, "ping_timeout": 0.2}},
    }
    uv = uvicorn.Server(server.build_uvicorn_config(server.app, cfg))
    uv.config.log_level = "warning"
    task = asyncio.create_task(uv.serve(sockets=[sock]))
    await wait_for(lambda: uv.started)
    yield port
    uv.should_exit = True
    await asyncio.wait_for(task, timeout=10)
    server.broker.sessions.clear()


async def _silent_client(port: int, charger_id: str):
    """Completes the WebSocket handshake by hand, then never reads or answers pings."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    key = base64.b64encode(os.urandom(16)).decode()
    writer.write(
        (
            f"GET /OrgA/{charger_id} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: ocpp1.6\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=3)
    assert b"101" in head.split(b"\r\n")[0]
    return writer


@pytest.mark.asyncio
async def test_socket_that_stops_answering_pings_is_dropped_and_its_session_removed(live_server):
    port = live_server
    writer = await _silent_client(port, "ZOMBIE")
    try:
        await wait_for(lambda: ("OrgA", "ZOMBIE") in server.broker.sessions)

        # No pong ever comes back: the watchdog must close it (~interval + timeout).
        await wait_for(lambda: ("OrgA", "ZOMBIE") not in server.broker.sessions, timeout=5)
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_responsive_charger_is_not_dropped_by_the_watchdog(live_server):
    port = live_server
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/OrgA/HEALTHY", subprotocols=["ocpp1.6"], ping_interval=None
    ) as ws:
        await wait_for(lambda: ("OrgA", "HEALTHY") in server.broker.sessions)

        await asyncio.sleep(1.2)  # several ping rounds; the client auto-answers each

        assert ("OrgA", "HEALTHY") in server.broker.sessions
        assert ws.close_code is None
