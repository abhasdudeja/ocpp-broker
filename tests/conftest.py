import asyncio
import socket

import pytest
import pytest_asyncio
import uvicorn

from ocpp_broker import server

from .fakes import API_KEY, wait_for


@pytest.fixture(autouse=True)
def api_key_env(monkeypatch):
    """REST routes require a key; give every test the same one (clients send AUTH_HEADERS)."""
    monkeypatch.setenv("OCPP_BROKER_API_KEY", API_KEY)


@pytest_asyncio.fixture
async def run_server(monkeypatch):
    """
    Start the real uvicorn server in-process on a free port.

    ``await run_server(config_data, ping_interval=..., ping_timeout=...)`` returns the
    port; ``config_data`` becomes the broker's configuration.
    """
    started = []

    async def start(config_data, ping_interval=20, ping_timeout=20):
        monkeypatch.setattr(server.broker, "config_data", config_data)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        cfg = {
            "broker": {"host": "127.0.0.1", "port": port},
            "security": {"websocket": {"ping_interval": ping_interval, "ping_timeout": ping_timeout}},
        }
        uv = uvicorn.Server(server.build_uvicorn_config(server.app, cfg))
        uv.config.log_level = "warning"
        task = asyncio.create_task(uv.serve(sockets=[sock]))
        await wait_for(lambda: uv.started)
        started.append((uv, task))
        return port

    yield start

    for uv, task in started:
        uv.should_exit = True
        await asyncio.wait_for(task, timeout=10)
    server.broker.sessions.clear()
