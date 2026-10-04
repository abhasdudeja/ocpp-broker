"""
The loader fills in the settings the broker reads and no others.
"""

import pytest

from ocpp_broker.config import load_config

INERT = ["api", "tag_management"]


@pytest.fixture
def empty_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("organizations: []\n", encoding="utf-8")
    return str(path)


def test_the_loader_fills_in_what_the_broker_reads(empty_config):
    cfg = load_config(empty_config)
    assert cfg["broker"] == {"host": "0.0.0.0", "port": 8765}
    assert cfg["ocpp"]["commands"]["core"]["heartbeat_interval"] == 300
    assert cfg["security"]["websocket"] == {"ping_interval": 20, "ping_timeout": 20}
    assert cfg["security"]["allow_unauthenticated_api"] is False
    assert cfg["security"]["cors"] == {"allow_origins": [], "allow_credentials": False}
    assert cfg["logging"] == {"level": "INFO"}
    assert cfg["ui"] == {"enabled": True}


def test_it_does_not_invent_settings_nothing_reads(empty_config):
    cfg = load_config(empty_config)
    for name in INERT:
        assert name not in cfg
    assert set(cfg["ocpp"]["commands"]) == {"core"} and "validation" not in cfg["ocpp"]
    assert not [k for k in cfg["broker"] if k.startswith("enable_") or k == "ocpp_version"]
    assert set(cfg["security"]) == {"websocket", "allow_unauthenticated_api", "cors"}


def test_an_organization_gets_only_the_defaults_the_broker_uses(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("organizations:\n  - name: Fleet\n    connect_to_backend: false\n", encoding="utf-8")
    [org] = load_config(str(path))["organizations"]
    for name in ("chargers", "ocpp_features", "tag_management"):
        assert name not in org
    assert org["backend_buffer_size"] == 200 and org["ocpp_subprotocol"] == "ocpp1.6" and org["tags"] == []


def test_a_file_that_still_has_the_old_settings_is_accepted_and_they_are_left_alone(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "api: {port: 9999}\ntag_management: {global: {enabled: true}}\n"
        "organizations:\n  - name: Fleet\n    connect_to_backend: false\n    chargers: [CP1]\n    tag_management: {enabled: false}\n",
        encoding="utf-8",
    )
    cfg = load_config(str(path))
    assert cfg["api"] == {"port": 9999} and cfg["tag_management"] == {"global": {"enabled": True}}
    assert cfg["organizations"][0]["chargers"] == ["CP1"]


def test_without_a_file_there_are_no_organizations(tmp_path):
    cfg = load_config(str(tmp_path / "missing.yaml"))
    assert cfg["organizations"] == [] and cfg["broker"]["port"] == 8765


def test_environment_overrides_still_apply_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_PORT", "9001")
    assert load_config(str(tmp_path / "missing.yaml"))["broker"]["port"] == 9001


def test_the_api_host_and_port_variables_are_gone(empty_config, monkeypatch):
    monkeypatch.setenv("API_PORT", "9002")
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    assert "api" not in load_config(empty_config)
