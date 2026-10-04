"""
The broker as a silent standby: an external backend leads, and the broker itself (a backend marked
``local: true`` that is not the leader) follows. It sees copies of the charger's requests and keeps its own
state, but never speaks to the charger. If the external leader fails the standby is promoted and from then
on answers the charger, already knowing what is running.
"""

import asyncio
import logging

import httpx
import pytest

from ocpp_broker import backend_manager, server

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger, wait_for

START = {"connectorId": 1, "idTag": "TAG1", "meterStart": 100, "timestamp": "2026-10-04T10:00:00Z"}


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


@pytest.fixture(autouse=True)
def fresh_tags():
    server.broker.tag_manager = None
    yield
    server.broker.tag_manager = None


def standby_org(*backends, failover=0.3, **extra):
    return {
        "name": "Org",
        "connect_to_backend": True,
        "leader_failover_timeout": failover,
        "tags": [{"id_tag": "TAG1", "status": "Accepted"}],
        "backends": list(backends),
        **extra,
    }


def external(backend, key="primary", leader=False):
    return {"id": key, "url": backend.url, **({"leader": True} if leader else {})}


LOCAL = {"id": "broker", "local": True}


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


async def detail(port, charger_id="CP1"):
    async with rest(port) as client:
        return (await client.get(f"/api/chargers/Org/{charger_id}")).json()


async def connect(port, charger_id="CP1"):
    charger = await ScriptedCharger.connect(port, "Org", charger_id)
    for _ in range(100):
        info = await detail(port, charger_id)
        if info.get("leader", {}).get("connected") and info.get("followers_connected") == info.get("followers_total"):
            return charger
        await asyncio.sleep(0.05)
    raise AssertionError("the backends never connected")


async def exchange(charger, action, payload):
    await charger.call(action, payload)
    return await charger.recv()


async def leader_is(port, key, timeout=6.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        info = await detail(port)
        if info["leader"]["key"] == key:
            return info
        await asyncio.sleep(0.05)
    raise AssertionError(f"the leader never became {key}: {info['leader']}")


def local_log():
    return server.broker.local_transactions_for("Org", "CP1").snapshot()


# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_while_the_external_leader_is_healthy_only_it_answers_and_the_standby_stays_silent(run_server):
    primary = await ScriptedBackend(first_id=100).start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        boot = await exchange(charger, "BootNotification", BOOT_PAYLOAD)
        assert boot[2]["currentTime"] == "2026-10-04T00:00:00Z", "the external leader's answer, not the broker's"
        nobody = await exchange(charger, "Authorize", {"idTag": "NOBODY"})
        assert nobody[2]["idTagInfo"]["status"] == "Accepted", "the leader says Accepted; the broker's own tags would say Invalid, and that is never heard"
        started = await exchange(charger, "StartTransaction", START)
        assert started[2]["transactionId"] == 100
        await charger.call("Heartbeat", {})
        beat = await charger.recv()
        assert beat[0] == 3 and beat[2]["currentTime"] == "2026-10-04T00:00:00Z", "one answer per request: nothing from the standby is waiting in front"
        with pytest.raises(asyncio.TimeoutError):
            await charger.recv(timeout=0.4)
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_the_standby_keeps_its_own_state_meanwhile(run_server):
    primary = await ScriptedBackend(first_id=100).start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        await exchange(charger, "BootNotification", BOOT_PAYLOAD)
        await exchange(charger, "StartTransaction", START)
        await wait_for(lambda: local_log())
        [row] = local_log()
        assert row["state"] == "open" and row["transaction_id"] != 100, "the broker numbered it itself"
        info = await detail(port)
        [transaction] = info["transactions"]
        assert transaction["transaction_id"] == 100 and set(transaction["backend_ids"]) == {"primary", "broker"}
        assert transaction["backend_ids"]["primary"] == 100 and transaction["backend_ids"]["broker"] == row["transaction_id"]
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_the_console_shows_the_standby_as_a_local_follower_and_the_external_backend_as_the_leader(run_server):
    primary = await ScriptedBackend().start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        info = await detail(port)
        assert [(b["key"], b["role"], b["local"], b["connected"]) for b in info["backends"]] == [("primary", "leader", False, True), ("broker", "follower", True, True)]
        assert info["mode"] == "relay"
        async with rest(port) as client:
            [org] = (await client.get("/api/orgs")).json()
            assert org["mode"] == "relay", "an external backend answers"
            assert [(b["key"], b["local"], b["leader"]) for b in org["backends"]] == [("primary", False, True), ("broker", True, False)]
            [fleet] = (await client.get("/api/backends")).json()
            local = next(b for b in fleet["backends"] if b["local"])
            assert (local["following"], local["leading"], local["links_up"]) == (1, 0, 1)
            assert len((await client.get("/orgs/Org/backends")).json()) == 1, "only the external link is listed as a link"
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_when_the_external_leader_fails_the_standby_takes_over_and_answers_with_its_own_rules(run_server):
    primary = await ScriptedBackend(first_id=100).start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        await exchange(charger, "BootNotification", BOOT_PAYLOAD)
        await primary.stop()
        info = await leader_is(port, "broker")
        assert [(b["key"], b["role"]) for b in info["backends"]] == [("broker", "leader"), ("primary", "follower")]
        assert info["backends"][1]["connected"] is False
        assert info["mode"] == "broker", "the broker answers this charger now"

        assert (await exchange(charger, "Authorize", {"idTag": "NOBODY"}))[2]["idTagInfo"]["status"] == "Invalid", "the broker's own tags decide"
        assert (await exchange(charger, "Authorize", {"idTag": "TAG1"}))[2]["idTagInfo"]["status"] == "Accepted"
        boot = await exchange(charger, "BootNotification", BOOT_PAYLOAD)
        assert boot[2]["interval"] == 300 and boot[2]["currentTime"] != "2026-10-04T00:00:00Z"
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_the_failover_is_an_event(run_server):
    primary = await ScriptedBackend().start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        await primary.stop()
        await leader_is(port, "broker")
        events = [e for e in server.broker.events.replay(0).events if e.type == "backend.failover"]
        assert [e.data for e in events] == [{"old_leader": "primary", "new_leader": "broker", "reason": "failover"}]
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_a_transaction_running_at_the_failover_is_stopped_under_the_standbys_own_number(run_server, caplog):
    primary = await ScriptedBackend(first_id=100).start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        await exchange(charger, "BootNotification", BOOT_PAYLOAD)
        tx = (await exchange(charger, "StartTransaction", START))[2]["transactionId"]
        assert tx == 100, "the charger holds the external leader's number"
        await wait_for(lambda: local_log())
        local_id = local_log()[0]["transaction_id"]
        await primary.stop()
        await leader_is(port, "broker")

        with caplog.at_level(logging.WARNING, logger="ocpp_broker.charge_point.CP1"):
            await charger.call("MeterValues", {"connectorId": 1, "transactionId": tx, "meterValue": [{"timestamp": "2026-10-04T10:05:00Z", "sampledValue": [{"value": "5"}]}]})
            assert (await charger.recv())[0] == 3
            stop = await exchange(charger, "StopTransaction", {"transactionId": tx, "idTag": "TAG1", "meterStop": 900, "timestamp": "2026-10-04T11:00:00Z"})
        assert stop[0] == 3 and stop[2]["idTagInfo"]["status"] == "Accepted"
        assert "did not start" not in caplog.text, "the broker knew the transaction: it had been following it"
        [row] = local_log()
        assert row["transaction_id"] == local_id and row["state"] == "closed"
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_a_start_the_dead_leader_never_answered_is_answered_once_by_the_standby(run_server):
    primary = await ScriptedBackend(first_id=100, mute=True).start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        await exchange_nowait(charger, "BootNotification", BOOT_PAYLOAD)
        await charger.call("StartTransaction", START, "start-1")
        await wait_for(lambda: local_log())
        await primary.stop()
        await leader_is(port, "broker")
        # the held request was answered with an error so the charger retries; the retry reaches the standby
        errors = []
        while True:
            try:
                frame = await charger.recv(timeout=0.5)
            except asyncio.TimeoutError:
                break
            errors.append(frame)
        await charger.call("StartTransaction", START, "start-1-retry")
        retry = await charger.recv()
        assert retry[0] == 3 and retry[1] == "start-1-retry" and retry[2]["transactionId"]
        assert len(local_log()) == 1, "one transaction, not two"
        assert retry[2]["transactionId"] == local_log()[0]["transaction_id"], "the standby's own answer, given once"
    finally:
        await charger.close()
        await primary.stop()


async def exchange_nowait(charger, action, payload):
    """Send a request whose answer may never come (the external leader is muted)."""
    await charger.call(action, payload)


@pytest.mark.asyncio
async def test_commands_follow_whoever_answers(run_server):
    primary = await ScriptedBackend().start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        async with rest(port) as client:
            # an external leader answers: the command is sent as it is, unchecked, as in relay mode
            odd = asyncio.ensure_future(client.post("/api/ocpp/organizations/Org/chargers/CP1/commands", json={"action": "Reset", "payload": {"type": "Medium"}, "timeout": 5}))
            frame = await charger.recv()
            assert frame[2] == "Reset" and frame[3] == {"type": "Medium"}
            await charger.send([3, frame[1], {"status": "Accepted"}])
            assert (await odd).json()["status"] == "success"

            await primary.stop()
            await leader_is(port, "broker")
            # the broker answers now: its library checks the payload first, and nothing is sent when it is wrong
            refused = await client.post("/api/ocpp/organizations/Org/chargers/CP1/commands", json={"action": "Reset", "payload": {"type": "Medium"}})
            assert refused.status_code == 422
            good = asyncio.ensure_future(client.post("/api/ocpp/organizations/Org/chargers/CP1/commands", json={"action": "Reset", "payload": {"type": "Soft"}, "timeout": 5}))
            frame = await charger.recv()
            assert frame[2] == "Reset" and frame[3] == {"type": "Soft"}
            await charger.send([3, frame[1], {"status": "Accepted"}])
            assert (await good).json()["status"] == "success"
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_there_is_no_fail_back_when_the_old_leader_returns_it_follows(run_server):
    primary = await ScriptedBackend(first_id=100).start()
    port_of_primary = primary.port
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await connect(port)
    try:
        await exchange(charger, "BootNotification", BOOT_PAYLOAD)
        await primary.stop()
        await leader_is(port, "broker")
        await primary.start(port_of_primary)
        for _ in range(100):
            info = await detail(port)
            if info["backends"][1]["connected"]:
                break
            await asyncio.sleep(0.05)
        assert [(b["key"], b["role"], b["connected"]) for b in info["backends"]] == [("broker", "leader", True), ("primary", "follower", True)]
        before = len(primary.calls)
        assert (await exchange(charger, "Authorize", {"idTag": "NOBODY"}))[2]["idTagInfo"]["status"] == "Invalid", "still the broker answering"
        await wait_for(lambda: len(primary.calls) > before)
        assert primary.calls[-1][2] == "Authorize", "the old leader receives copies again"
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_the_first_healthy_follower_in_the_configured_order_is_promoted(run_server):
    primary, mirror = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL, external(mirror, "mirror"))]})
    charger = await connect(port)
    try:
        await primary.stop()
        info = await leader_is(port, "broker")
        assert [b["key"] for b in info["backends"]][0] == "broker", "listed before the mirror, so it goes first"
    finally:
        await charger.close()
        await primary.stop()
        await mirror.stop()

    primary, mirror = await ScriptedBackend().start(), await ScriptedBackend().start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), external(mirror, "mirror"), LOCAL)]})
    charger = await connect(port)
    try:
        await primary.stop()
        info = await leader_is(port, "mirror")
        assert [b["key"] for b in info["backends"]][0] == "mirror"
    finally:
        await charger.close()
        await primary.stop()
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_local_leader_that_fails_ends_the_chargers_connection_so_it_reconnects(run_server, monkeypatch):
    from ocpp_broker.charge_point import BrokerChargePoint

    async def broken(self):
        raise RuntimeError("the library broke")

    monkeypatch.setattr(BrokerChargePoint, "start", broken)
    mirror = await ScriptedBackend().start()
    port = await run_server({"organizations": [standby_org({"id": "broker", "local": True, "leader": True}, external(mirror, "mirror"))]})
    charger = await ScriptedCharger.connect(port, "Org", "CP1")
    try:
        await asyncio.wait_for(charger.ws.wait_closed(), 5)
        assert charger.ws.close_code == 1011
    finally:
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_standby_that_fails_does_not_disturb_the_charger_or_the_leader(run_server, monkeypatch):
    from ocpp_broker.charge_point import BrokerChargePoint

    async def broken(self):
        raise RuntimeError("the library broke")

    monkeypatch.setattr(BrokerChargePoint, "start", broken)
    primary = await ScriptedBackend().start()
    port = await run_server({"organizations": [standby_org(external(primary, leader=True), LOCAL)]})
    charger = await ScriptedCharger.connect(port, "Org", "CP1")
    try:
        for _ in range(100):
            if (await detail(port)).get("leader", {}).get("connected"):
                break
            await asyncio.sleep(0.05)
        assert (await exchange(charger, "BootNotification", BOOT_PAYLOAD))[0] == 3
        info = await detail(port)
        assert info["backends"][1]["connected"] is False, "the standby is shown as down"
    finally:
        await charger.close()
        await primary.stop()


@pytest.mark.asyncio
async def test_nothing_is_queued_for_a_local_backend_that_has_stopped():
    from types import SimpleNamespace

    from ocpp_broker.local_backend import LocalBackend

    member = LocalBackend(SimpleNamespace(), "CP1", "Org", "broker", is_leader=False)
    assert member.is_ready() is False, "not started yet"
    await member.send("[2,\"1\",\"Heartbeat\",{}]")
    assert member._connection._queue.qsize() == 0
    member._failed = True
    await member.send("[2,\"1\",\"Heartbeat\",{}]")
    assert member._connection._queue.qsize() == 0
    assert member.buffered_count == 0 and member.disconnected_since is None
    member.reject_buffered()  # nothing to reject, and nothing to fail
