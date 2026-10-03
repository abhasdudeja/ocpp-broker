"""
The REST routers must be served by the same app/port as the charger WebSocket.
"""

from unittest.mock import Mock

from fastapi.testclient import TestClient

from ocpp_broker import api_server, server

from .fakes import AUTH_HEADERS


def test_all_routers_are_mounted_on_server_app():
    paths = set(server.app.openapi()["paths"])
    assert "/api/tags/status" in paths
    assert "/api/mongodb/health" in paths
    assert any(p.startswith("/api/ocpp/organizations/") for p in paths)

    client = TestClient(server.app, headers=AUTH_HEADERS)
    assert client.get("/health").status_code == 200
    assert client.get("/api/mongodb/health").status_code == 200


def test_start_api_is_gone():
    assert not hasattr(api_server, "start_api")


def test_tag_router_sees_tag_manager_created_after_startup(monkeypatch):
    """The tag manager is built on config load, long after the router exists."""
    client = TestClient(server.app, headers=AUTH_HEADERS)

    monkeypatch.setattr(server.broker, "tag_manager", None)
    assert client.get("/api/tags/status").json()["enabled"] is False
    assert client.get("/api/tags/organizations").status_code == 503

    manager = Mock()
    manager.mongodb_service = None
    manager._tag_lists = {"OrgA": {}}
    monkeypatch.setattr(server.broker, "tag_manager", manager)

    status = client.get("/api/tags/status").json()
    assert status["enabled"] is True
    assert status["organizations"] == ["OrgA"]
    assert client.get("/api/tags/organizations").json() == {"organizations": ["OrgA"]}


def test_main_async_configures_the_module_broker_instead_of_replacing_it():
    import inspect

    source = inspect.getsource(server.main_async)
    assert "OcppBroker()" not in source
    assert "global broker" not in source
