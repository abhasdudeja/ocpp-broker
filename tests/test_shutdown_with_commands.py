"""
A command still waiting for the charger must not keep the broker from stopping.
"""

import asyncio
import time

import httpx
import pytest

from ocpp_broker import server

from .fakes import AUTH_HEADERS, ScriptedBackend, ScriptedCharger, wait_for


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=30)


async def stop(uvicorn_server, task):
    started = time.monotonic()
    uvicorn_server.should_exit = True
    await asyncio.wait_for(task, timeout=20)
    return time.monotonic() - started


async def start_server(config):
    import socket

    from ocpp_broker.events import EventBus
    from ocpp_broker.write_behind import WriteBehind

    server.broker.config_data = config
    server.broker.events = EventBus()
    server.broker.writes = WriteBehind()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    cfg = {"broker": {"host": "127.0.0.1", "port": port}, "security": {"websocket": {"ping_interval": 20, "ping_timeout": 20}}}
    uv = server.BrokerServer(server.build_uvicorn_config(server.app, cfg))
    uv.config.log_level = "warning"
    task = asyncio.create_task(uv.serve(sockets=[sock]))
    await wait_for(lambda: uv.started)
    return uv, task, port


@pytest.mark.asyncio
async def test_a_command_to_a_silent_charger_in_broker_mode_does_not_hold_up_shutdown():
    uv, task, port = await start_server({"organizations": [{"name": "Home", "connect_to_backend": False}]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        async with rest(port) as client:
            request = asyncio.ensure_future(
                client.post("/api/ocpp/organizations/Home/chargers/CP1/commands", json={"action": "ClearCache", "payload": {}, "timeout": 300})
            )
            await charger.recv()  # the command arrived; the charger never answers
            took = await stop(uv, task)
            assert took < 8, f"shutdown took {took:.1f} s with a command waiting"
            response = await request
            assert response.status_code in (503, 504)
    finally:
        server.broker.sessions.clear()


@pytest.mark.asyncio
async def test_a_command_to_a_silent_charger_in_relay_mode_does_not_hold_up_shutdown():
    backend = await ScriptedBackend().start()
    config = {"organizations": [{"name": "Fleet", "connect_to_backend": True, "backends": [{"id": "lead", "url": backend.url, "leader": True}]}]}
    uv, task, port = await start_server(config)
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await wait_for(lambda: ("Fleet", "CP1") in server.broker.sessions)
        async with rest(port) as client:
            request = asyncio.ensure_future(
                client.post("/api/ocpp/organizations/Fleet/chargers/CP1/commands", json={"action": "ClearCache", "payload": {}, "timeout": 300})
            )
            await charger.recv()
            took = await stop(uv, task)
            assert took < 8, f"shutdown took {took:.1f} s with a command waiting"
            response = await request
            assert response.status_code in (503, 504)
    finally:
        await backend.stop()
        server.broker.sessions.clear()
