"""
The bundled web console: static serving under /ui, its security headers, the
ui.enabled switch, and GET /api/system/info (the call the console signs in with).
"""

import mimetypes

import pytest
from fastapi.testclient import TestClient

from ocpp_broker import server, ui
from ocpp_broker._version import __version__
from ocpp_broker.config import _apply_defaults

from .fakes import AUTH_HEADERS

INDEX = "<!doctype html><title>console</title><script type=module src=/ui/assets/app-abc123.js></script>"


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A fake build next to a file that must never be served."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "app-abc123.js").write_text("console.log('app')")
    (root / "assets" / "app-abc123.css").write_text("body{margin:0}")
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (tmp_path / "secret.txt").write_text("do not serve")
    monkeypatch.setattr(ui, "DIST_DIR", root)
    return root


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": []})
    return TestClient(server.app, follow_redirects=False)  # no key: /ui must not need one


def test_root_redirects_to_the_trailing_slash(client, dist):
    response = client.get("/ui")
    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


def test_index_is_served_without_an_api_key_and_is_never_cached(client, dist):
    response = client.get("/ui/")
    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"


def test_hashed_assets_are_immutable_and_have_the_right_types(client, dist):
    js = client.get("/ui/assets/app-abc123.js")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("text/javascript")  # nosniff would block text/plain
    assert "immutable" in js.headers["cache-control"]
    css = client.get("/ui/assets/app-abc123.css")
    assert css.headers["content-type"].startswith("text/css")
    svg = client.get("/ui/favicon.svg")
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.headers["cache-control"] == "no-cache"  # not hashed: revalidate


def test_every_response_carries_the_security_headers(client, dist):
    for path in ("/ui/", "/ui/assets/app-abc123.js", "/ui/assets/nope.js", "/ui/chargers/x", "/ui"):
        headers = client.get(path).headers
        csp = headers["content-security-policy"]
        assert "script-src 'self'" in csp, path
        assert "connect-src 'self'" in csp, path
        assert "frame-ancestors 'none'" in csp, path
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp, path
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["referrer-policy"] == "no-referrer", path


def test_client_side_routes_get_the_app_shell(client, dist):
    response = client.get("/ui/chargers/orgA/CHG001")
    assert response.status_code == 200
    assert response.text == INDEX


def test_a_missing_file_is_a_404_not_the_app_shell(client, dist):
    response = client.get("/ui/assets/missing.js")
    assert response.status_code == 404
    assert "<html" not in response.text.lower()


@pytest.mark.parametrize("path", ["/ui/..%2fsecret.txt", "/ui/assets/..%2f..%2fsecret.txt", "/ui/%2e%2e%2fsecret.txt"])
def test_path_traversal_cannot_leave_the_build_directory(client, dist, path):
    response = client.get(path)
    assert "do not serve" not in response.text
    assert response.status_code in (200, 404)  # the shell or nothing, never the file


def test_resolve_refuses_escapes_and_directories(dist):
    assert ui._resolve("../secret.txt") is None
    assert ui._resolve("assets") is None  # a directory
    assert ui._resolve("assets/app-abc123.js") == (dist / "assets" / "app-abc123.js").resolve()
    assert ui._resolve("bad\x00name") is None


def test_unbuilt_install_explains_itself(client, tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "DIST_DIR", tmp_path / "missing")
    response = client.get("/ui/")
    assert response.status_code == 503
    assert "npm" in response.text
    assert "content-security-policy" in response.headers


@pytest.mark.parametrize("path", ["/ui", "/ui/", "/ui/assets/app-abc123.js", "/ui/chargers/x"])
def test_ui_enabled_false_hides_everything(monkeypatch, dist, path):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": [], "ui": {"enabled": False}})
    response = TestClient(server.app, follow_redirects=False).get(path)
    assert response.status_code == 404
    assert "console" not in response.text


def test_ui_is_on_before_the_configuration_is_loaded(monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", {})
    assert ui.ui_enabled(server.broker) is True


def test_ui_setting_defaults_to_enabled_even_for_an_empty_block():
    for cfg in ({}, {"ui": None}, {"ui": {}}):
        _apply_defaults(cfg)
        assert cfg["ui"]["enabled"] is True
    off = {"ui": {"enabled": False}}
    _apply_defaults(off)
    assert off["ui"]["enabled"] is False


def test_the_ui_does_not_shadow_the_api(client, dist):
    assert client.get("/api/system/info").status_code == 401  # still behind the key
    assert client.get("/health").json() == {"status": "ok"}


def test_javascript_mime_type_is_registered():
    assert mimetypes.guess_type("app.js")[0] == "text/javascript"


# --------------------------------------------------------------------------
# GET /api/system/info
# --------------------------------------------------------------------------
class FakeMongo:
    database_name = "ocpp_test"

    def __init__(self, connected=True, reachable=True):
        self._connected, self._reachable = connected, reachable

    def is_connected(self):
        return self._connected

    async def ping(self):
        return self._reachable


def test_system_info_requires_the_key(client):
    assert client.get("/api/system/info").status_code == 401
    assert client.get("/api/system/info", headers={"X-API-Key": "wrong"}).status_code == 401


def test_system_info_describes_the_process(client, monkeypatch):
    monkeypatch.setattr(server.broker, "mongodb_service", None)
    body = client.get("/api/system/info", headers=AUTH_HEADERS).json()
    assert body["version"] == __version__
    assert body["instance_id"] == server.broker.instance_id
    assert body["api_auth"] == "api_key"
    assert body["ui_enabled"] is True
    assert body["organizations"] == 0
    assert body["connected_chargers"] == 0
    assert body["uptime_seconds"] >= 0
    assert body["mongodb"] == {"configured": False, "connected": False, "reachable": False, "database": None}


def test_system_info_counts_organizations_and_sessions(client, monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": [{"name": "A"}, {"name": "B"}]})
    monkeypatch.setattr(server.broker, "sessions", {("A", "c1"): object(), ("B", "c1"): object()})
    body = client.get("/api/system/info", headers=AUTH_HEADERS).json()
    assert body["organizations"] == 2
    assert body["connected_chargers"] == 2


@pytest.mark.parametrize("connected,reachable", [(True, True), (True, False), (False, False)])
def test_system_info_pings_mongodb_instead_of_trusting_the_startup_flag(client, monkeypatch, connected, reachable):
    monkeypatch.setattr(server.broker, "mongodb_service", FakeMongo(connected, reachable))
    mongo = client.get("/api/system/info", headers=AUTH_HEADERS).json()["mongodb"]
    assert mongo == {"configured": True, "connected": connected, "reachable": reachable, "database": "ocpp_test"}


def test_system_info_reports_ui_state(client, dist, monkeypatch):
    body = client.get("/api/system/info", headers=AUTH_HEADERS).json()
    assert body["ui_built"] is True
    monkeypatch.setattr(ui, "DIST_DIR", dist / "gone")
    assert client.get("/api/system/info", headers=AUTH_HEADERS).json()["ui_built"] is False


def test_system_info_is_in_the_openapi_schema_with_a_typed_response():
    operation = server.app.openapi()["paths"]["/api/system/info"]["get"]
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/SystemInfo"}
