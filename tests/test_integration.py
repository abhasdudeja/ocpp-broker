"""
End-to-end: real broker (uvicorn), a real WebSocket backend, a scripted charger
and the REST API over HTTP. Nothing in the data path is mocked.

Covers the boot round-trip, backend drop/reconnect with buffered delivery, and a
remote command round-trip, in both operating modes.
"""

import asyncio

import httpx
import pytest

from ocpp_broker import backend_manager, server

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, FakeBackend, ScriptedCharger, wait_for

ACCEPTED = {"status": "Accepted", "currentTime": "2026-01-01T00:00:00Z", "interval": 300}


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


def _broker_mode_config():
    return {
        "organizations": [{"name": "OrgA", "connect_to_backend": False}],
        "ocpp": {"commands": {"core": {"heartbeat_interval": 42}}},
    }


def _relay_config(backend: FakeBackend):
    return {
        "organizations": [
            {
                "name": "OrgA",
                "connect_to_backend": True,
                "leader_failover_timeout": 0,
                "backends": [{"id": "primary", "url": backend.url, "leader": True}],
            }
        ]
    }


def _rest(port: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


async def _session(key=("OrgA", "CP1")):
    await wait_for(lambda: key in server.broker.sessions)
    return server.broker.sessions[key]


# ---------------------------------------------------------------------------
# Broker mode: the broker is the central system
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_broker_mode_boot_and_remote_command_round_trip(run_server):
    port = await run_server(_broker_mode_config())
    charger = await ScriptedCharger.connect(port, "OrgA", "CP1")
    try:
        # Boot: answered by the broker itself, with the configured heartbeat interval.
        boot_id = await charger.call("BootNotification", BOOT_PAYLOAD)
        reply = await charger.recv()
        assert reply[:2] == [3, boot_id]
        assert reply[2]["status"] == "Accepted" and reply[2]["interval"] == 42

        # Remote command: REST -> broker -> charger -> broker -> REST.
        async with _rest(port) as rest:
            pending = asyncio.create_task(
                rest.post(
                    "/api/ocpp/organizations/OrgA/chargers/CP1/commands",
                    json={"action": "Reset", "payload": {"type": "Soft"}, "timeout": 5},
                )
            )
            call = await charger.recv()
            assert call[0] == 2 and call[2] == "Reset" and call[3] == {"type": "Soft"}
            await charger.send([3, call[1], {"status": "Accepted"}])

            response = await pending
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "success" and body["response"] == {"status": "Accepted"}
            assert body["message_id"] == call[1]

            # ...and a charger that refuses is reported as such, not as "sent".
            pending = asyncio.create_task(
                rest.post(
                    "/api/ocpp/organizations/OrgA/chargers/CP1/commands",
                    json={"action": "Reset", "payload": {"type": "Hard"}, "timeout": 5},
                )
            )
            call = await charger.recv()
            await charger.send([4, call[1], "NotSupported", "Hard reset not supported", {}])
            body = (await pending).json()
            assert body["status"] == "error"
            assert body["error"] == "NotSupported: Hard reset not supported"
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_broker_mode_remote_command_to_an_unknown_charger_is_404(run_server):
    port = await run_server(_broker_mode_config())
    async with _rest(port) as rest:
        response = await rest.post(
            "/api/ocpp/organizations/OrgA/chargers/NOBODY/commands",
            json={"action": "Reset", "payload": {"type": "Soft"}},
        )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Relay mode: the broker sits between charger and backend
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_relay_mode_boot_round_trip(run_server):
    backend = await FakeBackend().start()
    port = await run_server(_relay_config(backend))
    charger = await ScriptedCharger.connect(port, "OrgA", "CP1")
    try:
        boot_id = await charger.call("BootNotification", BOOT_PAYLOAD)

        await wait_for(lambda: len(backend.received) == 1)
        frame = backend.received_frames()[0]
        assert frame == [2, boot_id, "BootNotification", BOOT_PAYLOAD]
        assert backend.paths == ["/ocpp/CP1"], "backend sees the charger's own id"

        await backend.send([3, boot_id, ACCEPTED])
        assert await charger.recv() == [3, boot_id, ACCEPTED]
    finally:
        await charger.close()
        await backend.stop()


@pytest.mark.asyncio
async def test_relay_mode_backend_drop_and_reconnect_delivers_buffered_frames(run_server):
    backend = await FakeBackend().start()
    port = await run_server(_relay_config(backend))
    charger = await ScriptedCharger.connect(port, "OrgA", "CP1")
    try:
        # Healthy path first.
        boot_id = await charger.call("BootNotification", BOOT_PAYLOAD)
        await wait_for(lambda: len(backend.received) == 1)
        await backend.send([3, boot_id, ACCEPTED])
        assert (await charger.recv())[1] == boot_id

        # The backend goes away. The charger keeps talking.
        session = await _session()
        backend_port = backend.port
        await backend.stop()
        await wait_for(lambda: not session.backend_conn.is_ready())

        ids = [await charger.call("StatusNotification", {"connectorId": 1, "status": s, "errorCode": "NoError"})
               for s in ("Preparing", "Charging", "Finishing")]
        await wait_for(lambda: session.backend_conn.buffered_count == 3)

        # The backend returns: everything arrives, in order, then live traffic resumes behind it.
        await backend.start(backend_port)
        await wait_for(lambda: len(backend.received) == 4)
        live_id = await charger.call("Heartbeat", {})
        await wait_for(lambda: len(backend.received) == 5)

        assert [f[1] for f in backend.received_frames()] == [boot_id, *ids, live_id]
        assert [f[3]["status"] for f in backend.received_frames()[1:4]] == ["Preparing", "Charging", "Finishing"]

        # Replies make it back to the charger: nothing was lost, so nothing was rejected.
        for message_id in [*ids, live_id]:
            await backend.send([3, message_id, {}])
        replies = [await charger.recv() for _ in range(4)]
        assert [r[1] for r in replies] == [*ids, live_id]
        assert all(r[0] == 3 for r in replies), "no CallErrors expected"
    finally:
        await charger.close()
        await backend.stop()


@pytest.mark.asyncio
async def test_relay_mode_remote_command_round_trip(run_server):
    backend = await FakeBackend().start()
    port = await run_server(_relay_config(backend))
    charger = await ScriptedCharger.connect(port, "OrgA", "CP1")
    try:
        await charger.call("Heartbeat", {})  # make sure the relay is up end to end
        await wait_for(lambda: len(backend.received) == 1)
        before = len(backend.received)

        async with _rest(port) as rest:
            pending = asyncio.create_task(
                rest.post(
                    "/api/ocpp/organizations/OrgA/chargers/CP1/commands",
                    json={"action": "Reset", "payload": {"type": "Soft"}, "timeout": 5},
                )
            )
            call = await charger.recv()
            assert call[0] == 2 and call[2] == "Reset"
            await charger.send([3, call[1], {"status": "Accepted"}])

            response = await pending
            assert response.status_code == 200
            assert response.json()["status"] == "success"
            assert response.json()["response"] == {"status": "Accepted"}

            fetched = await rest.get(f"/api/ocpp/commands/{call[1]}/response")
            assert fetched.json()["status"] == "success"

        # The reply belongs to the REST caller: the backend must never see it.
        await charger.call("Heartbeat", {})
        await wait_for(lambda: len(backend.received) == before + 1)
        assert call[1] not in " ".join(backend.received)
    finally:
        await charger.close()
        await backend.stop()


@pytest.mark.asyncio
async def test_relay_mode_backend_commands_still_reach_the_charger(run_server):
    """The backend's own CALLs go to the charger and the charger's reply goes back to it."""
    backend = await FakeBackend().start()
    port = await run_server(_relay_config(backend))
    charger = await ScriptedCharger.connect(port, "OrgA", "CP1")
    try:
        await charger.call("Heartbeat", {})
        await wait_for(lambda: len(backend.received) == 1)

        await backend.send([2, "backend-1", "GetConfiguration", {"key": ["HeartbeatInterval"]}])
        call = await charger.recv()
        assert call == [2, "backend-1", "GetConfiguration", {"key": ["HeartbeatInterval"]}]

        await charger.send([3, "backend-1", {"configurationKey": []}])
        await wait_for(lambda: len(backend.received) == 2)
        assert backend.received_frames()[1] == [3, "backend-1", {"configurationKey": []}]
    finally:
        await charger.close()
        await backend.stop()
