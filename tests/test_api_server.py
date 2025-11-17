"""
Tests for the main API server endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock, MagicMock
from typing import Dict, Any

from ocpp_broker.api_server import create_api
from ocpp_broker.broker import OcppBroker
from ocpp_broker.session import ChargerSession, SessionMode
from ocpp_broker.backend_manager import BackendConnection


@pytest.fixture
def mock_broker():
    """Create a mock broker for testing"""
    broker = Mock(spec=OcppBroker)
    broker.org_backends = {}
    broker.org_leaders = {}
    broker.sessions = {}
    broker.config_data = {}
    
    # Mock methods
    broker.add_backend_dynamic = AsyncMock(return_value=True)
    broker.remove_backend_dynamic = AsyncMock(return_value=True)
    broker.promote_leader = Mock(return_value=True)
    broker.reload_from_config = AsyncMock()
    
    return broker


@pytest.fixture
def api_client(mock_broker):
    """Create a test client for the API"""
    app = create_api(mock_broker)
    return TestClient(app)


class TestOrganizationEndpoints:
    """Tests for organization-related endpoints"""
    
    def test_list_orgs_empty(self, api_client, mock_broker):
        """Test listing organizations when none exist"""
        mock_broker.org_backends = {}
        response = api_client.get("/orgs")
        assert response.status_code == 200
        assert response.json() == []
    
    def test_list_orgs_with_data(self, api_client, mock_broker):
        """Test listing organizations with data"""
        # Setup mock data
        mock_backend = Mock()
        mock_backend.id = "backend1"
        mock_backend.is_leader = True
        mock_backend.websocket = Mock()
        mock_backend.websocket.closed = False
        
        mock_broker.org_backends = {
            "Org1": {"backend1": mock_backend},
            "Org2": {}
        }
        mock_broker.org_leaders = {
            "Org1": mock_backend
        }
        
        response = api_client.get("/orgs")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "Org1"
        assert data[0]["num_backends"] == 1
        assert data[0]["leader"] == "backend1"
        assert data[1]["name"] == "Org2"
        assert data[1]["num_backends"] == 0


class TestBackendEndpoints:
    """Tests for backend management endpoints"""
    
    def test_list_backends_not_found(self, api_client, mock_broker):
        """Test listing backends for non-existent organization"""
        mock_broker.org_backends = {}
        response = api_client.get("/orgs/NonExistent/backends")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()
    
    def test_list_backends_success(self, api_client, mock_broker):
        """Test listing backends successfully"""
        mock_backend = Mock()
        mock_backend.id = "backend1"
        mock_backend.url = "ws://example.com/ocpp"
        mock_backend.is_leader = True
        mock_backend.websocket = Mock()
        mock_backend.websocket.closed = False
        
        mock_broker.org_backends = {
            "Org1": {"backend1": mock_backend}
        }
        
        response = api_client.get("/orgs/Org1/backends")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "backend1"
        assert data[0]["url"] == "ws://example.com/ocpp"
        assert data[0]["leader"] is True
        assert data[0]["connected"] is True
    
    def test_add_backend_success(self, api_client, mock_broker):
        """Test adding a backend successfully"""
        mock_broker.org_backends = {"Org1": {}}
        mock_broker.add_backend_dynamic = AsyncMock(return_value=True)
        
        backend_data = {
            "id": "new_backend",
            "url": "ws://new-backend.com/ocpp",
            "leader": False
        }
        
        response = api_client.post("/orgs/Org1/backends", json=backend_data)
        assert response.status_code == 200
        assert response.json()["status"] == "created"
        assert response.json()["backend"] == "new_backend"
        mock_broker.add_backend_dynamic.assert_called_once()
    
    def test_add_backend_org_not_found(self, api_client, mock_broker):
        """Test adding backend to non-existent organization"""
        mock_broker.org_backends = {}
        
        backend_data = {
            "id": "new_backend",
            "url": "ws://new-backend.com/ocpp",
            "leader": False
        }
        
        response = api_client.post("/orgs/NonExistent/backends", json=backend_data)
        assert response.status_code == 404
    
    def test_add_backend_already_exists(self, api_client, mock_broker):
        """Test adding a backend that already exists"""
        mock_backend = Mock()
        mock_broker.org_backends = {"Org1": {"existing_backend": mock_backend}}
        
        backend_data = {
            "id": "existing_backend",
            "url": "ws://example.com/ocpp",
            "leader": False
        }
        
        response = api_client.post("/orgs/Org1/backends", json=backend_data)
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"].lower()
    
    def test_remove_backend_success(self, api_client, mock_broker):
        """Test removing a backend successfully"""
        mock_broker.org_backends = {"Org1": {"backend1": Mock()}}
        mock_broker.remove_backend_dynamic = AsyncMock(return_value=True)
        
        response = api_client.delete("/orgs/Org1/backends/backend1")
        assert response.status_code == 200
        assert response.json()["status"] == "removed"
        assert response.json()["backend"] == "backend1"
        mock_broker.remove_backend_dynamic.assert_called_once()
    
    def test_remove_backend_not_found(self, api_client, mock_broker):
        """Test removing a non-existent backend"""
        mock_broker.org_backends = {"Org1": {}}
        mock_broker.remove_backend_dynamic = AsyncMock(return_value=False)
        
        response = api_client.delete("/orgs/Org1/backends/non_existent")
        assert response.status_code == 404
    
    def test_set_leader_success(self, api_client, mock_broker):
        """Test setting a backend as leader"""
        mock_broker.org_backends = {"Org1": {"backend1": Mock()}}
        mock_broker.promote_leader = Mock(return_value=True)
        
        response = api_client.post("/orgs/Org1/leader/backend1")
        assert response.status_code == 200
        assert response.json()["status"] == "leader_updated"
        assert response.json()["leader"] == "backend1"
        mock_broker.promote_leader.assert_called_once_with("Org1", "backend1")
    
    def test_set_leader_backend_not_found(self, api_client, mock_broker):
        """Test setting non-existent backend as leader"""
        mock_broker.org_backends = {"Org1": {}}
        mock_broker.promote_leader = Mock(return_value=False)
        
        response = api_client.post("/orgs/Org1/leader/non_existent")
        assert response.status_code == 404


class TestConfigEndpoints:
    """Tests for configuration endpoints"""
    
    def test_reload_config(self, api_client, mock_broker):
        """Test reloading configuration"""
        mock_broker.reload_from_config = AsyncMock()
        
        response = api_client.post("/reload")
        assert response.status_code == 200
        assert response.json()["status"] == "config_reloaded"
        mock_broker.reload_from_config.assert_called_once()

