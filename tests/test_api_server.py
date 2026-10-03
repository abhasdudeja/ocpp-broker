"""
Tests for the broker management endpoints.
"""

import re
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from ocpp_broker import api_server
from ocpp_broker.api_server import create_api
from ocpp_broker.broker import OcppBroker


@pytest.fixture
def mock_broker():
    """Create a mock broker for testing"""
    broker = Mock(spec=OcppBroker)
    broker.org_backends = {}
    broker.sessions = {}
    broker.config_data = {}
    return broker


@pytest.fixture
def api_client(mock_broker):
    """Create a test client for the API"""
    app = create_api(mock_broker)
    return TestClient(app)


def _backend(url, leader, connected):
    conn = Mock()
    conn.url = url
    conn.is_leader = leader
    conn.connected_event.is_set.return_value = connected
    return conn


class TestBackendEndpoints:
    def test_list_backends_unknown_org(self, api_client, mock_broker):
        mock_broker.org_backends = {}
        response = api_client.get("/orgs/NonExistent/backends")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_list_backends_uses_the_real_org_backends_shape(self, api_client, mock_broker):
        """org_backends is {org: {charger_id: {"leader": conn, "followers": [conn]}}}."""
        mock_broker.org_backends = {
            "Org1": {
                "CP1": {
                    "leader": _backend("ws://a/ocpp", True, True),
                    "followers": [_backend("ws://b/ocpp", False, False)],
                },
                "CP2": {"leader": _backend("ws://a/ocpp", True, True), "followers": []},
            }
        }

        response = api_client.get("/orgs/Org1/backends")

        assert response.status_code == 200
        assert response.json() == [
            {"charger_id": "CP1", "url": "ws://a/ocpp", "leader": True, "connected": True},
            {"charger_id": "CP1", "url": "ws://b/ocpp", "leader": False, "connected": False},
            {"charger_id": "CP2", "url": "ws://a/ocpp", "leader": True, "connected": True},
        ]


class TestRemovedRoutes:
    """These routes called broker methods that never existed and always 500'd."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/orgs"),
            ("post", "/orgs/Org1/backends"),
            ("delete", "/orgs/Org1/backends/b1"),
            ("post", "/orgs/Org1/leader/b1"),
            ("post", "/reload"),
        ],
    )
    def test_route_is_gone(self, api_client, method, path):
        response = getattr(api_client, method)(path)
        assert response.status_code in (404, 405)


def test_api_only_references_broker_attributes_that_exist():
    """Guards against routes calling methods OcppBroker does not define."""
    source = Path(api_server.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r"\bbroker\.([A-Za-z_]\w*)", source))
    real = OcppBroker()
    missing = sorted(name for name in used if not hasattr(real, name))
    assert not missing, f"api_server uses attributes OcppBroker lacks: {missing}"
