"""
The admin API: off by default, and what it does when it is on: read, check, apply, the audit log, and what a
connected or a connecting charger sees afterwards.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import websockets
import yaml
from fastapi.testclient import TestClient
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ocpp_broker import server
from ocpp_broker.auth import hash_password

from .fakes import API_KEY, AUTH_HEADERS, wait_for

LABELLED = "ops-key-0123456789"
CONFIG = """# a note the admin API will not keep
broker:
  host: 0.0.0.0
  port: 8765
mongodb:
  enabled: false
security:
  allow_unauthenticated_api: false
organizations:
  - name: Fleet
    connect_to_backend: false
    tags:
      - id_tag: T1
        status: Accepted
    charger_auth:
      credentials:
        CP1:
          password_hash: "%s"
"""
HASH = hash_password("fleet-key-0123456789", iterations=1000)


def runtime(cfg_path, **extra):
    """The configuration the broker runs on, with the admin API on and pointing at ``cfg_path``."""
    return {
        "organizations": [{"name": "Fleet", "connect_to_backend": False}],
        "security": {"api_keys": [{"label": "ops", "key": LABELLED}]},
        "admin": {"enabled": True, **extra},
    }


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG % HASH, encoding="utf-8")
    return path


@pytest.fixture
def admin(config_file, monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", runtime(config_file))
    monkeypatch.setattr(server.broker, "_cfg_path", str(config_file), raising=False)
    monkeypatch.setattr(server.broker, "admin_store", None, raising=False)
    monkeypatch.setattr(server.broker, "audit", None, raising=False)
    return TestClient(server.app, client=("203.0.113.5", 40000), headers=AUTH_HEADERS)


def revision(client):
    return client.get("/api/admin/config").json()["revision"]


def change(**org):
    return {"op": "upsert", "org": org}


def body(client, *changes, **extra):
    return {"revision": revision(client), "changes": list(changes), **extra}


# --------------------------------------------------------------------------- off by default
ROUTES = [
    ("get", "/api/admin/config", None),
    ("post", "/api/admin/config/validate", {"revision": "x", "changes": [{"op": "delete", "name": "Fleet"}]}),
    ("post", "/api/admin/config/apply", {"revision": "x", "changes": [{"op": "delete", "name": "Fleet"}]}),
    ("get", "/api/admin/audit", None),
]


@pytest.mark.parametrize("method, path, payload", ROUTES)
def test_the_admin_api_is_off_unless_it_is_switched_on(config_file, monkeypatch, method, path, payload):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": []})
    monkeypatch.setattr(server.broker, "_cfg_path", str(config_file), raising=False)
    client = TestClient(server.app, headers=AUTH_HEADERS)
    response = client.request(method, path, json=payload)
    assert response.status_code == 403 and "admin.enabled: true" in response.json()["detail"]
    assert config_file.read_text(encoding="utf-8") == CONFIG % HASH


@pytest.mark.parametrize("method, path, payload", ROUTES)
def test_the_admin_api_needs_an_api_key_even_when_it_is_on(admin, method, path, payload):
    response = TestClient(server.app).request(method, path, json=payload)
    assert response.status_code == 401


def test_system_info_says_whether_the_admin_api_is_on(admin, monkeypatch):
    assert admin.get("/api/system/info").json()["admin_enabled"] is True
    monkeypatch.setattr(server.broker, "config_data", {"organizations": []})
    assert admin.get("/api/system/info").json()["admin_enabled"] is False


# --------------------------------------------------------------------------- reading
def test_the_configuration_is_listed_without_a_password_or_a_hash(admin, config_file):
    config = admin.get("/api/admin/config").json()
    assert config["path"] == str(config_file) and config["writable"] is True and config["keeps_backups"] == 10
    [fleet] = config["organizations"]
    assert fleet["name"] == "Fleet" and fleet["tags"] == 1 and fleet["connect_to_backend"] is False
    assert fleet["credentials"] == [{"charger_id": "CP1", "storage": "hash"}]
    assert fleet["backend_buffer_size"] == 200
    text = json.dumps(config)
    assert HASH not in text and "fleet-key" not in text and "password" not in text


def test_the_revision_names_the_file_as_it_is(admin, config_file):
    first = revision(admin)
    assert revision(admin) == first
    config_file.write_text(config_file.read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8")
    assert revision(admin) != first


def test_without_a_configuration_file_it_says_there_is_nothing_to_change(admin, tmp_path, monkeypatch):
    monkeypatch.setattr(server.broker, "_cfg_path", str(tmp_path / "none.yaml"), raising=False)
    config = admin.get("/api/admin/config").json()
    assert config["writable"] is False and "without a configuration file" in config["writable_reason"] and config["organizations"] == []
    response = admin.post("/api/admin/config/apply", json={"revision": "", "changes": [change(name="X", connect_to_backend=False)]})
    assert response.status_code == 409


def test_a_configuration_file_that_has_become_unreadable_is_reported(admin, config_file):
    config_file.write_text("a: [unclosed", encoding="utf-8")
    response = admin.get("/api/admin/config")
    assert response.status_code == 409 and "not valid YAML" in response.json()["detail"]


# --------------------------------------------------------------------------- checking
def test_checking_says_what_would_change_and_writes_nothing(admin, config_file):
    payload = body(admin, change(name="Fleet", connect_to_backend=False, backend_buffer_size=50, credentials=[{"charger_id": "CP1"}]))
    plan = admin.post("/api/admin/config/validate", json=payload).json()
    assert plan["ok"] is True and plan["errors"] == [] and plan["conflict"] is False
    assert plan["changes"] == [{"org": "Fleet", "kind": "changed", "lines": ["backend_buffer_size: 200 → 50"]}]
    assert config_file.read_text(encoding="utf-8") == CONFIG % HASH
    assert not [p for p in config_file.parent.iterdir() if ".bak-" in p.name or p.name.endswith(".jsonl")]


def test_checking_counts_the_chargers_that_are_connected_now(admin, monkeypatch):
    monkeypatch.setattr(server.broker, "sessions", {("Fleet", "CP1"): object(), ("Fleet", "CP2"): object(), ("Other", "X"): object()})
    payload = body(admin, change(name="Fleet", connect_to_backend=True, backends=[{"url": "ws://x/o", "leader": True}], credentials=[{"charger_id": "CP1"}]))
    plan = admin.post("/api/admin/config/validate", json=payload).json()
    assert plan["connected_chargers"] == {"Fleet": 2}


def test_checking_lists_errors_and_warnings(admin):
    payload = body(
        admin,
        change(name="Fleet", connect_to_backend=True, backends=[{"local": True}, {"local": True}]),
        change(name="Other one", connect_to_backend=False),
        {"op": "delete", "name": "Ghost"},
    )
    plan = admin.post("/api/admin/config/validate", json=payload).json()
    assert plan["ok"] is False
    assert any("only one backend can be local" in e for e in plan["errors"])
    assert any("not a usable organization name" in e for e in plan["errors"])
    assert any("no organization named 'Ghost'" in e for e in plan["errors"])


def test_checking_says_when_the_file_is_not_the_one_the_changes_were_made_from(admin, config_file):
    payload = body(admin, change(name="Fleet", connect_to_backend=False, backend_buffer_size=50))
    config_file.write_text(config_file.read_text(encoding="utf-8") + "\n# edited by hand\n", encoding="utf-8")
    plan = admin.post("/api/admin/config/validate", json=payload).json()
    assert plan["conflict"] is True and plan["ok"] is False and "has changed" in plan["errors"][0]


def test_checking_gives_the_loaders_warnings(admin):
    payload = body(admin, change(name="Fresh", connect_to_backend=False))
    plan = admin.post("/api/admin/config/validate", json=payload).json()
    assert plan["ok"] is True and any("UNAUTHENTICATED" in w for w in plan["warnings"])
    assert plan["changes"][0]["kind"] == "added"


def test_a_short_password_is_an_error_not_a_stored_password(admin, config_file):
    payload = body(admin, change(name="Fleet", connect_to_backend=False, credentials=[{"charger_id": "CP1"}, {"charger_id": "CP2", "password": "tinypw1"}]))
    plan = admin.post("/api/admin/config/validate", json=payload).json()
    assert plan["ok"] is False and any("too short" in e for e in plan["errors"])
    assert "tinypw1" not in json.dumps(plan)


# --------------------------------------------------------------------------- applying
def test_applying_writes_the_file_keeps_a_copy_and_changes_what_the_broker_runs_on(admin, config_file):
    password = "brand-new-key-0123456789"
    payload = body(
        admin,
        change(name="Newco", connect_to_backend=False, credentials=[{"charger_id": "NC1", "password": password}]),
        change(name="Fleet", connect_to_backend=False, backend_buffer_size=50, credentials=[{"charger_id": "CP1"}]),
    )
    response = admin.post("/api/admin/config/apply", json=payload)
    assert response.status_code == 200
    applied = response.json()
    assert applied["revision"] == revision(admin) and applied["dropped_connections"] == 0
    assert [(c["org"], c["kind"]) for c in applied["changes"]] == [("Newco", "added"), ("Fleet", "changed")]
    assert password not in response.text and "password_hash" not in response.text and "pbkdf2" not in response.text

    written = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert [o["name"] for o in written["organizations"]] == ["Fleet", "Newco"]
    assert written["organizations"][0]["tags"] == [{"id_tag": "T1", "status": "Accepted"}], "what the console does not edit is kept"
    assert written["organizations"][0]["charger_auth"]["credentials"]["CP1"] == {"password_hash": HASH}
    assert password not in config_file.read_text(encoding="utf-8")
    assert written["security"] == {"allow_unauthenticated_api": False} and written["broker"]["port"] == 8765

    copy = config_file.parent / applied["backup"]
    assert copy.read_text(encoding="utf-8") == CONFIG % HASH

    running = {o["name"]: o for o in server.broker.config_data["organizations"]}
    assert set(running) == {"Fleet", "Newco"} and running["Fleet"]["backend_buffer_size"] == 50
    assert server.broker.config_data["admin"]["enabled"] is True, "the rest of the configuration is untouched"


def test_applying_something_that_cannot_work_changes_nothing(admin, config_file):
    payload = body(admin, change(name="Fleet", connect_to_backend=True, backends=[{"local": True}, {"local": True}]))
    response = admin.post("/api/admin/config/apply", json=payload)
    assert response.status_code == 422 and "only one backend can be local" in response.json()["detail"]
    assert config_file.read_text(encoding="utf-8") == CONFIG % HASH
    assert [o["name"] for o in server.broker.config_data["organizations"]] == ["Fleet"]


def test_a_file_edited_by_hand_since_the_page_loaded_is_not_overwritten(admin, config_file):
    payload = body(admin, change(name="Newco", connect_to_backend=False))
    edited = CONFIG % HASH + "# edited by hand\n"
    config_file.write_text(edited, encoding="utf-8")
    response = admin.post("/api/admin/config/apply", json=payload)
    assert response.status_code == 409 and "has changed" in response.json()["detail"]
    assert config_file.read_text(encoding="utf-8") == edited


def test_dropping_connections_of_an_organization_the_changes_do_not_touch_is_refused(admin, config_file):
    payload = body(admin, change(name="Newco", connect_to_backend=False), drop_connections=["Fleet"])
    response = admin.post("/api/admin/config/apply", json=payload)
    assert response.status_code == 422 and "Fleet" in response.json()["detail"]
    assert config_file.read_text(encoding="utf-8") == CONFIG % HASH


def test_an_unwritable_file_is_a_conflict_and_the_failure_is_logged(admin, config_file, monkeypatch):
    import os

    payload = body(admin, change(name="Newco", connect_to_backend=False))

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    with monkeypatch.context() as patched:
        patched.setattr(os, "replace", refuse)
        response = admin.post("/api/admin/config/apply", json=payload)
    assert response.status_code == 409 and "it was not changed" in response.json()["detail"]
    assert config_file.read_text(encoding="utf-8") == CONFIG % HASH
    entry = admin.get("/api/admin/audit").json()["entries"][0]
    assert entry["outcome"] == "failed" and entry["summary"]


def test_a_request_that_cannot_be_read_does_not_echo_the_password_back(admin):
    payload = {"revision": revision(admin), "changes": [{"op": "upsert", "org": {"credentials": [{"charger_id": "CP9", "password": "never-echo-this-0123456789"}]}}]}
    response = admin.post("/api/admin/config/apply", json=payload)
    assert response.status_code == 422
    assert "never-echo-this" not in response.text
    assert any(e["loc"][-1] == "name" for e in response.json()["detail"]), "it still says where the problem is"


def test_other_routes_keep_the_normal_validation_answer(admin):
    response = admin.get("/api/history/messages?limit=0")
    assert response.status_code == 422 and "input" in response.json()["detail"][0]


def test_an_empty_list_of_changes_is_refused(admin):
    assert admin.post("/api/admin/config/validate", json={"revision": revision(admin), "changes": []}).status_code == 422


# --------------------------------------------------------------------------- the audit log
def test_every_change_is_logged_with_who_and_from_where_and_never_a_secret(admin):
    secret = "audited-secret-0123456789"
    payload = body(admin, change(name="Newco", connect_to_backend=False, credentials=[{"charger_id": "NC1", "password": secret}]))
    assert admin.post("/api/admin/config/apply", json=payload, headers={"X-API-Key": LABELLED}).status_code == 200
    page = admin.get("/api/admin/audit").json()
    [entry] = page["entries"]
    assert entry["action"] == "config.apply" and entry["outcome"] == "applied"
    assert entry["source"] == "203.0.113.5" and entry["key_label"] == "ops"
    assert entry["organizations"] == ["Newco"] and "Newco: + credential for NC1" in entry["summary"]
    assert entry["revision"] == revision(admin)
    assert secret not in json.dumps(page) and page["persisted_to"].endswith("config.yaml.audit.jsonl")
    assert secret not in Path(page["persisted_to"]).read_text(encoding="utf-8")


def test_a_refused_change_is_logged_too(admin):
    admin.post("/api/admin/config/apply", json=body(admin, change(name="Fleet", connect_to_backend=True, backends=[{"local": True}, {"local": True}])))
    admin.post("/api/admin/config/apply", json={"revision": "stale", "changes": [{"op": "delete", "name": "Fleet"}]})
    entries = admin.get("/api/admin/audit").json()["entries"]
    assert [e["outcome"] for e in entries] == ["refused", "refused"]
    assert entries[0]["detail"].startswith("The configuration file has changed") and entries[0]["key_label"] == "api-key"
    assert "only one backend can be local" in entries[1]["detail"]


def test_checking_is_not_logged(admin):
    admin.post("/api/admin/config/validate", json=body(admin, change(name="Newco", connect_to_backend=False)))
    assert admin.get("/api/admin/audit").json()["entries"] == []


def test_the_audit_log_can_be_limited_and_goes_where_the_configuration_says(admin, monkeypatch, tmp_path):
    where = tmp_path / "elsewhere.jsonl"
    monkeypatch.setattr(server.broker, "config_data", runtime(None, audit_log=str(where)))
    for n in range(3):
        admin.post("/api/admin/config/apply", json=body(admin, change(name=f"O{n}", connect_to_backend=False)))
    page = admin.get("/api/admin/audit?limit=2").json()
    assert len(page["entries"]) == 2 and page["persisted_to"] == str(where)
    assert len(where.read_text(encoding="utf-8").splitlines()) == 3
    assert admin.get("/api/admin/audit?limit=0").status_code == 422
    assert admin.get("/api/admin/audit?limit=501").status_code == 422


# --------------------------------------------------------------------------- what chargers see
def basic(user, password):
    import base64

    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def rest(port):
    return httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS, timeout=10)


@pytest.fixture
def served(config_file, run_server, monkeypatch):
    async def start():
        port = await run_server(runtime(config_file))
        monkeypatch.setattr(server.broker, "_cfg_path", str(config_file), raising=False)
        monkeypatch.setattr(server.broker, "admin_store", None, raising=False)
        monkeypatch.setattr(server.broker, "audit", None, raising=False)
        return port

    return start


@pytest.mark.asyncio
async def test_a_charger_can_connect_to_an_organization_added_a_moment_ago_with_the_password_it_was_given(served):
    port = await served()
    password = "charger-key-0123456789"
    with pytest.raises(ConnectionClosed):  # no such organization yet
        async with websockets.connect(f"ws://127.0.0.1:{port}/Newco/NC1", subprotocols=["ocpp1.6"], ping_interval=None) as ws:
            await ws.recv()
    async with rest(port) as client:
        revision_ = (await client.get("/api/admin/config")).json()["revision"]
        response = await client.post(
            "/api/admin/config/apply",
            json={"revision": revision_, "changes": [change(name="Newco", connect_to_backend=False, credentials=[{"charger_id": "NC1", "password": password}])]},
        )
        assert response.status_code == 200
    with pytest.raises(InvalidStatus) as refused:
        async with websockets.connect(f"ws://127.0.0.1:{port}/Newco/NC1", subprotocols=["ocpp1.6"], additional_headers=basic("NC1", "wrong-key-0123456789"), ping_interval=None):
            pass
    assert refused.value.response.status_code == 401
    async with websockets.connect(f"ws://127.0.0.1:{port}/Newco/NC1", subprotocols=["ocpp1.6"], additional_headers=basic("NC1", password), ping_interval=None):
        await wait_for(lambda: ("Newco", "NC1") in server.broker.sessions)


@pytest.mark.asyncio
async def test_a_rotated_password_stops_the_old_one_from_working_for_new_connections(served):
    port = await served()
    async with rest(port) as client:
        revision_ = (await client.get("/api/admin/config")).json()["revision"]
        await client.post(
            "/api/admin/config/apply",
            json={"revision": revision_, "changes": [change(name="Fleet", connect_to_backend=False, credentials=[{"charger_id": "CP1", "password": "rotated-key-0123456789"}])]},
        )
    with pytest.raises(InvalidStatus):
        async with websockets.connect(f"ws://127.0.0.1:{port}/Fleet/CP1", subprotocols=["ocpp1.6"], additional_headers=basic("CP1", "fleet-key-0123456789"), ping_interval=None):
            pass
    async with websockets.connect(f"ws://127.0.0.1:{port}/Fleet/CP1", subprotocols=["ocpp1.6"], additional_headers=basic("CP1", "rotated-key-0123456789"), ping_interval=None):
        await wait_for(lambda: ("Fleet", "CP1") in server.broker.sessions)


@pytest.mark.asyncio
async def test_a_connected_charger_stays_connected_unless_its_organization_is_named(served):
    port = await served()
    async with rest(port) as client:
        revision_ = (await client.get("/api/admin/config")).json()["revision"]
        await client.post(
            "/api/admin/config/apply",
            json={"revision": revision_, "changes": [change(name="Open", connect_to_backend=False), change(name="Bystander", connect_to_backend=False)]},
        )
        ws = await websockets.connect(f"ws://127.0.0.1:{port}/Open/CP1", subprotocols=["ocpp1.6"], ping_interval=None)
        bystander = await websockets.connect(f"ws://127.0.0.1:{port}/Bystander/CP9", subprotocols=["ocpp1.6"], ping_interval=None)
        await wait_for(lambda: ("Open", "CP1") in server.broker.sessions and ("Bystander", "CP9") in server.broker.sessions)
        try:
            revision_ = (await client.get("/api/admin/config")).json()["revision"]
            changed = await client.post(
                "/api/admin/config/apply",
                json={"revision": revision_, "changes": [change(name="Open", connect_to_backend=False, backend_buffer_size=5)]},
            )
            assert changed.json()["dropped_connections"] == 0
            await asyncio.sleep(0.3)
            assert ws.close_code is None, "it keeps what it connected with"

            revision_ = (await client.get("/api/admin/config")).json()["revision"]
            dropped = await client.post(
                "/api/admin/config/apply",
                json={"revision": revision_, "changes": [change(name="Open", connect_to_backend=False, backend_buffer_size=6)], "drop_connections": ["Open"]},
            )
            assert dropped.json()["dropped_connections"] == 1
            await asyncio.wait_for(ws.wait_closed(), 5)
            assert ws.close_code == 1012 and ws.close_reason == "Configuration changed"
            assert bystander.close_code is None, "another organization's charger is left alone"
            audited = (await client.get("/api/admin/audit")).json()["entries"][0]
            assert "disconnected 1 charger(s) of Open" in audited["summary"]
        finally:
            await ws.close()
            await bystander.close()


@pytest.mark.asyncio
async def test_removing_an_organization_refuses_new_connections_and_can_drop_the_old(served):
    port = await served()
    async with rest(port) as client:
        revision_ = (await client.get("/api/admin/config")).json()["revision"]
        await client.post("/api/admin/config/apply", json={"revision": revision_, "changes": [change(name="Gone", connect_to_backend=False)]})
        ws = await websockets.connect(f"ws://127.0.0.1:{port}/Gone/CP1", subprotocols=["ocpp1.6"], ping_interval=None)
        await wait_for(lambda: ("Gone", "CP1") in server.broker.sessions)
        revision_ = (await client.get("/api/admin/config")).json()["revision"]
        response = await client.post("/api/admin/config/apply", json={"revision": revision_, "changes": [{"op": "delete", "name": "Gone"}]})
        assert response.status_code == 200 and response.json()["changes"][0]["kind"] == "removed"
        await asyncio.sleep(0.2)
        assert ws.close_code is None
        await ws.close()
    with pytest.raises(ConnectionClosed):
        async with websockets.connect(f"ws://127.0.0.1:{port}/Gone/CP2", subprotocols=["ocpp1.6"], ping_interval=None) as late:
            await late.recv()


def test_the_key_is_what_it_was(admin):
    assert API_KEY and LABELLED != API_KEY


# --------------------------------------------------------------------------- configuration
@pytest.mark.parametrize(
    "admin_section, message",
    [
        ("on", "admin must be a mapping"),
        ({"enabled": "yes"}, "admin.enabled must be true or false"),
        ({"audit_log": ""}, "admin.audit_log must be a file path"),
        ({"audit_log": 5}, "admin.audit_log must be a file path"),
        ({"keep_backups": 0}, "admin.keep_backups must be a whole number"),
        ({"keep_backups": 2.5}, "admin.keep_backups must be a whole number"),
        ({"keep_backups": True}, "admin.keep_backups must be a whole number"),
        ({"enable": True}, "admin.enable is not known"),
    ],
)
def test_admin_settings_that_cannot_be_used_stop_the_broker_at_startup(admin_section, message):
    from ocpp_broker.config import _validate_config

    with pytest.raises(ValueError, match=message):
        _validate_config({"broker": {"port": 8000}, "organizations": [], "admin": admin_section})


def test_good_admin_settings_are_accepted():
    from ocpp_broker.config import _validate_config

    _validate_config({"broker": {"port": 8000}, "organizations": [], "admin": {"enabled": True, "audit_log": "/tmp/audit.jsonl", "keep_backups": 3}})
