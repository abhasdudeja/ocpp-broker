"""
The broker as the leader of a charger that also has external followers (a backend marked ``local: true``).

The broker answers the charger itself, through the same code as broker mode. Every other backend
receives a copy of the charger's requests, each in its own transaction ids, and whatever it answers is
discarded. Real server, scripted charger, scripted backends.
"""

import asyncio

import httpx
import pytest

from ocpp_broker import backend_manager, server
from ocpp_broker.config import load_config

from .fakes import AUTH_HEADERS, BOOT_PAYLOAD, ScriptedBackend, ScriptedCharger, wait_for


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backend_manager, "RECONNECT_DELAY", 0.05)


def hybrid_org(*followers, name="Hybrid", **extra):
    backends = [{"id": "broker", "local": True, "leader": True}]
    backends += [{"id": f"mirror{i}" if len(followers) > 1 else "mirror", "url": f.url} for i, f in enumerate(followers, 1)]
    return {
        "name": name,
        "connect_to_backend": True,
        "backends": backends,
        "tags": [{"id_tag": "TAG1", "status": "Accepted"}],
        **extra,
    }


@pytest.fixture
def fresh_tags():
    server.broker.tag_manager = None  # rebuilt from each test's configuration
    yield
    server.broker.tag_manager = None


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


async def exchange(charger, action, payload):
    """Send one request and return the single reply."""
    await charger.call(action, payload)
    return await charger.recv()


async def boot(charger):
    return await exchange(charger, "BootNotification", BOOT_PAYLOAD)


async def followers_up(port, org, charger_id, count=1):
    """Wait until the broker's links to the followers are up: followers are never sent what came before."""
    async with rest(port) as client:
        for _ in range(100):
            response = await client.get(f"/api/chargers/{org}/{charger_id}")
            if response.status_code == 200 and response.json()["followers_connected"] >= count:
                return
            await asyncio.sleep(0.05)
    raise AssertionError("the followers never connected")


async def connect(port, org="Hybrid", charger_id="CP1", followers=1):
    charger = await ScriptedCharger.connect(port, org, charger_id)
    await followers_up(port, org, charger_id, followers)
    return charger


START = {"connectorId": 1, "idTag": "TAG1", "meterStart": 100, "timestamp": "2026-10-04T10:00:00Z"}


# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_broker_answers_the_charger_and_the_follower_gets_a_copy(run_server, fresh_tags):
    mirror = await ScriptedBackend().start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        reply = await boot(charger)
        assert reply[0] == 3 and reply[2]["status"] == "Accepted" and reply[2]["interval"] == 300, "the broker's own answer"
        reply = await exchange(charger, "Authorize", {"idTag": "TAG1"})
        assert reply[2] == {"idTagInfo": {"status": "Accepted"}}
        reply = await exchange(charger, "Authorize", {"idTag": "NOBODY"})
        assert reply[2] == {"idTagInfo": {"status": "Invalid"}}, "the broker's tags decide, not the follower's 'Accepted'"

        await wait_for(lambda: len(mirror.calls) == 3)
        assert [c[2] for c in mirror.calls] == ["BootNotification", "Authorize", "Authorize"]
        assert mirror.calls[1][3] == {"idTag": "TAG1"}
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_the_follower_never_talks_to_the_charger(run_server, fresh_tags):
    mirror = await ScriptedBackend().start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        await wait_for(lambda: mirror.calls)
        # its answers were discarded, and a command it sends is not forwarded either
        await mirror.command("Reset", {"type": "Hard"}, "from-the-mirror")
        with pytest.raises(asyncio.TimeoutError):
            await charger.recv(timeout=0.5)
        await charger.call("Heartbeat", {})
        reply = await charger.recv()
        assert reply[0] == 3 and "currentTime" in reply[2], "only the broker's reply; nothing queued from the follower"
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_each_backend_has_its_own_transaction_ids(run_server, fresh_tags):
    mirror = await ScriptedBackend(first_id=7000).start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        started = await exchange(charger, "StartTransaction", START)
        tx = started[2]["transactionId"]
        assert started[2]["idTagInfo"]["status"] == "Accepted"
        assert tx != 7000, "the charger holds the broker's id, not the follower's"

        await wait_for(lambda: mirror.transactions)
        await charger.call("MeterValues", {"connectorId": 1, "transactionId": tx, "meterValue": [{"timestamp": "2026-10-04T10:05:00Z", "sampledValue": [{"value": "5"}]}]})
        await charger.recv()
        await exchange(charger, "StopTransaction", {"transactionId": tx, "meterStop": 4000, "timestamp": "2026-10-04T11:00:00Z"})
        await wait_for(lambda: mirror.transactions[7000]["stop"] is not None)
        assert mirror.unknown == [], "the follower was spoken to in its own ids"
        assert mirror.transactions[7000]["meter_values"][0]["transactionId"] == 7000
        assert mirror.transactions[7000]["stop"]["transactionId"] == 7000

        async with rest(port) as client:
            detail = (await client.get("/api/chargers/Hybrid/CP1")).json()
            assert detail["transaction_id_mapping"] is True
            [row] = detail["transactions"]
            assert row["transaction_id"] == tx and row["backend_ids"] == {"broker": tx, "mirror": 7000}
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_retried_start_is_one_transaction_for_the_broker_and_the_follower(run_server, fresh_tags):
    mirror = await ScriptedBackend(first_id=500).start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        first = await exchange(charger, "StartTransaction", START)
        again = await exchange(charger, "StartTransaction", START)
        assert again[2]["transactionId"] == first[2]["transactionId"], "the charger asked again because it missed the answer"
        beat = await exchange(charger, "Heartbeat", {})
        assert "currentTime" in beat[2], "one answer to the retry, not two: nothing stray is waiting in front of the next reply"
        await asyncio.sleep(0.2)
        assert len(mirror.transactions) == 1, "the follower was not given a second transaction"
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_commands_go_to_the_charger_in_the_chargers_own_ids(run_server, fresh_tags):
    mirror = await ScriptedBackend(first_id=900).start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        tx = (await exchange(charger, "StartTransaction", START))[2]["transactionId"]
        async with rest(port) as client:
            request = asyncio.ensure_future(
                client.post("/api/ocpp/organizations/Hybrid/chargers/CP1/commands", json={"action": "RemoteStopTransaction", "payload": {"transactionId": tx}})
            )
            frame = await charger.recv()
            assert frame[2] == "RemoteStopTransaction" and frame[3] == {"transactionId": tx}
            await charger.send([3, frame[1], {"status": "Accepted"}])
            response = await request
        assert response.json()["status"] == "success"
        await asyncio.sleep(0.2)
        assert all(c[2] != "RemoteStopTransaction" for c in mirror.calls), "the follower sees the charger's requests, not the broker's commands"
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_follower_that_is_down_does_not_slow_or_break_the_broker(run_server, fresh_tags):
    mirror = await ScriptedBackend().start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        await mirror.stop()
        await asyncio.sleep(0.3)
        for _ in range(3):
            reply = await exchange(charger, "Heartbeat", {})
            assert reply[0] == 3
        started = await exchange(charger, "StartTransaction", START)
        assert started[0] == 3 and started[2]["transactionId"]

        async with rest(port) as client:
            detail = (await client.get("/api/chargers/Hybrid/CP1")).json()
            assert detail["leader"]["connected"] is True and detail["followers_connected"] == 0
            [row] = detail["transactions"]
            assert row["degraded"] == ["mirror"], "the follower never heard of this transaction and is skipped for it"
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_several_followers_each_get_their_own_copy_and_ids(run_server, fresh_tags):
    one, two = await ScriptedBackend(first_id=10).start(), await ScriptedBackend(first_id=20_000).start()
    port = await run_server({"organizations": [hybrid_org(one, two)]})
    charger = await connect(port, followers=2)
    try:
        await boot(charger)
        tx = (await exchange(charger, "StartTransaction", START))[2]["transactionId"]
        await exchange(charger, "StopTransaction", {"transactionId": tx, "meterStop": 1, "timestamp": "2026-10-04T11:00:00Z"})
        await wait_for(lambda: one.transactions.get(10, {}).get("stop") and two.transactions.get(20_000, {}).get("stop"))
        assert one.unknown == [] and two.unknown == []
    finally:
        await charger.close()
        await one.stop()
        await two.stop()


@pytest.mark.asyncio
async def test_the_console_shows_the_broker_as_leader_and_the_others_as_followers(run_server, fresh_tags):
    mirror = await ScriptedBackend().start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        await wait_for(lambda: mirror.calls)
        async with rest(port) as client:
            [org] = (await client.get("/api/orgs")).json()
            assert org["mode"] == "broker" and org["transaction_id_mapping"] is True
            assert [(b["key"], b["local"], b["leader"], b["url"]) for b in org["backends"]] == [
                ("broker", True, True, None), ("mirror", False, False, mirror.url)
            ]
            for _ in range(50):
                row = (await client.get("/api/chargers")).json()["chargers"][0]
                if row["followers_connected"] == 1:
                    break
                await asyncio.sleep(0.05)
            assert row["mode"] == "broker" and row["leader"]["local"] is True and row["leader"]["key"] == "broker"
            assert (row["followers_total"], row["followers_connected"]) == (1, 1)
            detail = (await client.get("/api/chargers/Hybrid/CP1")).json()
            assert [(b["key"], b["role"], b["local"]) for b in detail["backends"]] == [("broker", "leader", True), ("mirror", "follower", False)]
            links = (await client.get("/orgs/Hybrid/backends")).json()
            assert [(link["url"], link["leader"]) for link in links] == [(mirror.url, False)], "only external links are listed there"
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_reconnecting_charger_replaces_the_old_session_cleanly(run_server, fresh_tags):
    mirror = await ScriptedBackend().start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    first = await ScriptedCharger.connect(port, "Hybrid", "CP1")
    await boot(first)
    second = await ScriptedCharger.connect(port, "Hybrid", "CP1")
    try:
        await first.ws.wait_closed()
        reply = await boot(second)
        assert reply[0] == 3
        async with rest(port) as client:
            assert len((await client.get("/orgs/Hybrid/backends")).json()) == 1, "the new session's links survived the old one closing"
    finally:
        await second.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_local_backend_alone_behaves_like_broker_mode(run_server, fresh_tags):
    port = await run_server({"organizations": [{"name": "Solo", "connect_to_backend": True, "backends": [{"local": True}], "tags": [{"id_tag": "TAG1", "status": "Accepted"}]}]})
    charger = await ScriptedCharger.connect(port, "Solo", "CP1")
    try:
        assert (await boot(charger))[2]["status"] == "Accepted"
        assert (await exchange(charger, "Authorize", {"idTag": "TAG1"}))[2]["idTagInfo"]["status"] == "Accepted"
        started = await exchange(charger, "StartTransaction", START)
        assert started[2]["transactionId"]
    finally:
        await charger.close()


@pytest.mark.asyncio
async def test_a_start_the_broker_refuses_as_invalid_does_not_upset_the_followers(run_server, fresh_tags):
    mirror = await ScriptedBackend(first_id=300).start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        broken = {k: v for k, v in START.items() if k != "meterStart"}
        error = await exchange(charger, "StartTransaction", broken)
        assert error[0] == 4, "the broker's own validation answers, as in broker mode"
        good = {**START, "timestamp": "2026-10-04T10:00:05Z"}
        tx = (await exchange(charger, "StartTransaction", good))[2]["transactionId"]
        await exchange(charger, "StopTransaction", {"transactionId": tx, "meterStop": 5, "timestamp": "2026-10-04T11:00:00Z"})
        await wait_for(lambda: any(t["stop"] for t in mirror.transactions.values()))
        assert mirror.unknown == []
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_transaction_survives_the_charger_reconnecting_for_the_broker_and_the_follower(run_server, fresh_tags):
    mirror = await ScriptedBackend(first_id=4000).start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    first = await connect(port)
    await boot(first)
    tx = (await exchange(first, "StartTransaction", START))[2]["transactionId"]
    await wait_for(lambda: mirror.transactions)
    await first.close()
    for _ in range(100):
        if ("Hybrid", "CP1") not in server.broker.sessions:
            break
        await asyncio.sleep(0.02)

    second = await connect(port)
    try:
        await exchange(second, "StopTransaction", {"transactionId": tx, "idTag": "TAG1", "meterStop": 900, "timestamp": "2026-10-04T11:00:00Z"})
        await wait_for(lambda: mirror.transactions[4000]["stop"] is not None)
        assert mirror.unknown == [], "the follower was told about its own transaction after the reconnect"
    finally:
        await second.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_without_id_translation_the_followers_get_the_charger_frames_as_they_are(run_server, fresh_tags):
    mirror = await ScriptedBackend().start()
    port = await run_server({"organizations": [hybrid_org(mirror, transaction_ids={"mapping": False})]})
    charger = await connect(port)
    try:
        await boot(charger)
        tx = (await exchange(charger, "StartTransaction", START))[2]["transactionId"]
        await exchange(charger, "StopTransaction", {"transactionId": tx, "meterStop": 9, "timestamp": "2026-10-04T11:00:00Z"})
        await wait_for(lambda: len(mirror.calls) == 3)
        assert [c[2] for c in mirror.calls] == ["BootNotification", "StartTransaction", "StopTransaction"]
        assert mirror.calls[2][3]["transactionId"] == tx, "untranslated: its own numbering is its own problem (documented)"
        assert mirror.calls[1][3] == START
        async with rest(port) as client:
            assert (await client.get("/api/chargers/Hybrid/CP1")).json()["transaction_id_mapping"] is False
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_what_the_charger_says_in_answer_to_the_brokers_commands_is_not_copied_to_followers(run_server, fresh_tags):
    mirror = await ScriptedBackend().start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        async with rest(port) as client:
            request = asyncio.ensure_future(client.post("/api/ocpp/organizations/Hybrid/chargers/CP1/commands", json={"action": "ClearCache", "payload": {}}))
            frame = await charger.recv()
            await charger.send([3, frame[1], {"status": "Accepted"}])
            assert (await request).json()["status"] == "success"
        await charger.call("Heartbeat", {})
        await charger.recv()
        await wait_for(lambda: len(mirror.calls) == 2)
        kinds = {frame[0] for frame in mirror.received_frames()}
        assert kinds == {2}, "only the charger's requests are copied, never its answers to the broker"
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_follower_that_comes_back_is_given_the_next_transaction_in_full(run_server, fresh_tags):
    mirror = await ScriptedBackend(first_id=60).start()
    port = await run_server({"organizations": [hybrid_org(mirror)]})
    charger = await connect(port)
    try:
        await boot(charger)
        port_of_mirror = mirror.port
        await mirror.stop()
        await asyncio.sleep(0.2)
        first = (await exchange(charger, "StartTransaction", START))[2]["transactionId"]  # the follower misses this one
        await mirror.start(port_of_mirror)
        await followers_up(port, "Hybrid", "CP1")
        await exchange(charger, "StopTransaction", {"transactionId": first, "meterStop": 1, "timestamp": "2026-10-04T10:30:00Z"})
        second = (await exchange(charger, "StartTransaction", {**START, "timestamp": "2026-10-04T12:00:00Z"}))[2]["transactionId"]
        await exchange(charger, "StopTransaction", {"transactionId": second, "meterStop": 2, "timestamp": "2026-10-04T13:00:00Z"})
        await wait_for(lambda: mirror.transactions and all(t["stop"] for t in mirror.transactions.values()))
        assert list(mirror.transactions) == [60], "it knows only the transaction that started while it was there"
        assert mirror.unknown == [], "and was never sent a message about the one it missed"
    finally:
        await charger.close()
        await mirror.stop()


@pytest.mark.asyncio
async def test_a_session_that_ends_late_does_not_remove_the_links_of_the_one_that_replaced_it():
    """Two sessions without a leader socket would both look like 'leader None'; the followers tell them apart."""
    from types import SimpleNamespace

    from ocpp_broker.broker import OcppBroker
    from ocpp_broker.session import ChargerSession

    broker = OcppBroker()
    entry = {"name": "Hybrid", "connect_to_backend": True, "backends": [{"local": True}, {"url": "ws://x/ocpp"}]}
    socket = SimpleNamespace(client=None, headers={})
    old = ChargerSession(broker, "CP1", "Hybrid", entry, socket)
    new = ChargerSession(broker, "CP1", "Hybrid", entry, socket)
    old.follower_conns, new.follower_conns = [], []
    broker.org_backends = {"Hybrid": {"CP1": {"followers": new.follower_conns}}}
    await old.close()
    assert broker.org_backends["Hybrid"].get("CP1") is not None, "the new session's links are still there"
    await new.close()
    assert "CP1" not in broker.org_backends["Hybrid"], "and go when their own session ends"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def configured(*backends, **org):
    return load_config_dict({"organizations": [{"name": "O", "connect_to_backend": True, "backends": list(backends), **org}]})["organizations"][0]


def load_config_dict(data):
    import tempfile
    import pathlib
    import yaml

    with tempfile.TemporaryDirectory() as folder:
        path = pathlib.Path(folder) / "config.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return load_config(str(path))


def test_a_local_backend_is_the_leader_and_needs_no_url():
    org = configured({"local": True}, {"id": "a", "url": "ws://a/ocpp"})
    assert [(b.get("local"), b["leader"]) for b in org["backends"]] == [(True, True), (None, False)]
    assert "ocpp_subprotocol" not in org["backends"][0], "nothing to negotiate with this broker"


def test_the_local_backend_wins_even_if_it_is_listed_last_or_not_marked():
    org = configured({"id": "a", "url": "ws://a/ocpp"}, {"id": "me", "local": True})
    assert [b["leader"] for b in org["backends"]] == [False, True]


@pytest.mark.parametrize(
    "backends, message",
    [
        ([{"local": True}, {"local": True}], "only one backend can be local"),
        ([{"local": True, "url": "ws://x/ocpp"}], "has no url"),
        ([{"local": True, "leader": True}, {"url": "ws://x/ocpp", "leader": True}], "only one backend can be marked leader"),
        ([{"local": True, "leader": False}], "needs a leader to follow"),
        ([{"id": "nourl"}], "needs a url"),
        ([{"local": "yes"}], "local must be true or false"),
    ],
)
def test_a_wrong_local_backend_is_refused_with_a_reason(backends, message):
    with pytest.raises(ValueError, match=message):
        configured(*backends)


def test_an_external_leader_makes_the_local_backend_a_standby():
    org = configured({"id": "primary", "url": "ws://a/ocpp", "leader": True}, {"id": "broker", "local": True})
    assert [b["leader"] for b in org["backends"]] == [True, False]


def test_a_local_backend_that_says_it_is_not_the_leader_leaves_the_lead_to_the_first_other_backend():
    org = configured({"id": "broker", "local": True, "leader": False}, {"id": "a", "url": "ws://a/ocpp"}, {"id": "b", "url": "ws://b/ocpp"})
    assert [b["leader"] for b in org["backends"]] == [False, True, False]


def test_which_backend_leads():
    from ocpp_broker.transaction_ids import leader_index

    assert leader_index([{"url": "a"}, {"url": "b"}]) == 0
    assert leader_index([{"url": "a"}, {"url": "b", "leader": True}]) == 1
    assert leader_index([{"url": "a"}, {"local": True}]) == 1, "unmarked: the local backend"
    assert leader_index([{"url": "a", "leader": True}, {"local": True}]) == 0
    assert leader_index([{"local": True, "leader": False}, {"url": "a"}]) == 1


def test_the_keys_of_a_local_backend():
    from ocpp_broker.transaction_ids import backend_keys

    assert backend_keys([{"local": True}, {"url": "ws://a/ocpp"}]) == ["broker", "ws://a/ocpp"]
    assert backend_keys([{"local": True, "id": "me"}, {"id": "broker", "url": "ws://a/ocpp"}]) == ["me", "broker"]
    assert backend_keys([{"id": "broker", "url": "ws://a/ocpp"}, {"local": True}]) == ["broker", "broker#2"]
