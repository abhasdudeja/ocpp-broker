"""
REST API key and CORS.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from ocpp_broker import server
from ocpp_broker.auth import API_KEY_ENV

from .fakes import API_KEY

PROBES = [
    "/api/tags/status",
    "/api/ocpp/organizations/OrgA/chargers",
    "/api/mongodb/health",
    "/orgs/OrgA/backends",
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": []})
    return TestClient(server.app)  # no default headers: tests choose what to send


@pytest.mark.parametrize("path", PROBES)
def test_every_router_rejects_a_missing_key(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("path", PROBES)
def test_every_router_rejects_a_wrong_key(client, path):
    assert client.get(path, headers={"X-API-Key": "nope"}).status_code == 401


def test_x_api_key_and_bearer_both_work(client):
    assert client.get("/api/mongodb/health", headers={"X-API-Key": API_KEY}).status_code == 200
    assert client.get("/api/mongodb/health", headers={"Authorization": f"Bearer {API_KEY}"}).status_code == 200


def test_no_rest_route_is_left_unprotected(client):
    """Sweep the whole OpenAPI surface: nothing under the REST routers answers without a key."""
    unprotected = []
    for path, methods in server.app.openapi()["paths"].items():
        for method in methods:
            url = path.replace("{", "").replace("}", "")  # path params become literals
            response = client.request(method.upper(), url)
            if response.status_code != 401:
                unprotected.append((method.upper(), path, response.status_code))
    assert not unprotected, unprotected


def test_health_endpoint_stays_open_for_probes(client):
    assert client.get("/health").status_code == 200


def test_key_is_read_from_config_when_env_is_unset(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV)
    monkeypatch.setattr(
        server.broker, "config_data", {"security": {"api_key": "from-config"}, "organizations": []}
    )
    client = TestClient(server.app)

    assert client.get("/api/mongodb/health").status_code == 401
    assert client.get("/api/mongodb/health", headers={"X-API-Key": "from-config"}).status_code == 200


def test_api_fails_closed_when_no_key_is_configured(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV)
    monkeypatch.setattr(server.broker, "config_data", {"organizations": []})
    client = TestClient(server.app)

    response = client.get("/api/mongodb/health", headers={"X-API-Key": "anything"})

    assert response.status_code == 503
    assert API_KEY_ENV in response.json()["detail"]


def test_unauthenticated_api_must_be_enabled_explicitly(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV)
    monkeypatch.setattr(
        server.broker,
        "config_data",
        {"security": {"allow_unauthenticated_api": True}, "organizations": []},
    )
    assert TestClient(server.app).get("/api/mongodb/health").status_code == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
def _app_with_cors(cors):
    app = FastAPI()
    server.apply_cors(app, {"security": {"cors": cors}})

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app)


def _preflight(client, origin):
    return client.options(
        "/ping",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-API-Key",
        },
    )


def test_no_cors_headers_unless_origins_are_configured():
    client = _app_with_cors({"allow_origins": []})
    assert "access-control-allow-origin" not in _preflight(client, "https://evil.example").headers


def test_explicit_origins_are_allowed_and_others_are_not():
    client = _app_with_cors({"allow_origins": ["https://dash.example.com"]})

    allowed = _preflight(client, "https://dash.example.com")
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://dash.example.com"
    assert "x-api-key" in allowed.headers["access-control-allow-headers"].lower()

    denied = _preflight(client, "https://evil.example")
    assert "access-control-allow-origin" not in denied.headers


def test_wildcard_with_credentials_is_refused(caplog):
    with caplog.at_level(logging.ERROR, logger="ocpp_broker.server"):
        client = _app_with_cors({"allow_origins": ["*"], "allow_credentials": True})

    response = _preflight(client, "https://anywhere.example")
    assert "access-control-allow-credentials" not in response.headers
    assert any("cannot be combined" in r.getMessage() for r in caplog.records)


def test_credentials_are_allowed_with_explicit_origins():
    client = _app_with_cors({"allow_origins": ["https://dash.example.com"], "allow_credentials": True})
    response = _preflight(client, "https://dash.example.com")
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-origin"] == "https://dash.example.com"


def test_server_app_has_no_import_time_wildcard_cors():
    assert not any(m.cls is CORSMiddleware for m in server.app.user_middleware)
