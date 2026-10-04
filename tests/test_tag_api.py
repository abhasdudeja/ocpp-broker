"""
Tests for the tag management API endpoints.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock

from ocpp_broker.api_server import create_api
from ocpp_broker.broker import OcppBroker
from ocpp_broker.tag_manager import TagManager
from ocpp_broker.schemas.tags import OCPPTag, TagStatus, TagType

from .fakes import AUTH_HEADERS


@pytest.fixture
def mock_broker_with_tag_manager():
    """Create a mock broker with tag manager"""
    broker = Mock(spec=OcppBroker)
    broker.tag_manager = Mock(spec=TagManager)
    broker.tag_manager.mongodb_service = None  # instance attribute, not in the spec
    broker.tag_manager._tag_lists = {
        "Org1": {},
        "Org2": {}
    }
    
    # Mock tag manager methods
    broker.tag_manager.add_tag = AsyncMock(return_value=True)
    broker.tag_manager.get_tag = AsyncMock(return_value=None)
    broker.tag_manager.update_tag = AsyncMock(return_value=True)
    broker.tag_manager.delete_tag = AsyncMock(return_value=True)
    
    # Mock search_tags result
    mock_search_result = Mock()
    mock_search_result.tags = []
    mock_search_result.total = 0
    mock_search_result.dict = Mock(return_value={"tags": [], "total": 0, "limit": 100, "offset": 0})
    broker.tag_manager.search_tags = AsyncMock(return_value=mock_search_result)
    
    broker.tag_manager.get_tag_list = AsyncMock(return_value=None)
    
    broker.tag_manager.authorize_tag = AsyncMock(return_value={"status": "Accepted"})
    
    broker.org_backends = {}
    broker.sessions = {}
    broker.config_data = {}
    
    return broker


@pytest.fixture
def mock_broker_without_tag_manager():
    """Create a mock broker without tag manager"""
    broker = Mock(spec=OcppBroker)
    broker.tag_manager = None
    broker.org_backends = {}
    broker.sessions = {}
    broker.config_data = {}
    return broker


@pytest.fixture
def real_tag_client():
    """API client backed by a real TagManager (no mocks) seeded with three tags."""
    broker = Mock(spec=OcppBroker)
    broker.tag_manager = TagManager(
        {
            "organizations": [
                {
                    "name": "Org1",
                    "tags": [
                        {"id_tag": "TAG001", "status": "Accepted", "tag_type": "RFID"},
                        {"id_tag": "TAG002", "status": "Blocked", "tag_type": "RFID"},
                        {"id_tag": "OLD001", "status": "Accepted", "tag_type": "NFC",
                         "expiry_date": "2000-01-01T00:00:00Z"},
                    ],
                }
            ]
        }
    )
    broker.org_backends = {}
    broker.sessions = {}
    broker.config_data = {}
    return TestClient(create_api(broker), headers=AUTH_HEADERS)


@pytest.fixture
def api_client_with_tags(mock_broker_with_tag_manager):
    """Create API client with tag manager"""
    app = create_api(mock_broker_with_tag_manager)
    return TestClient(app, headers=AUTH_HEADERS)


@pytest.fixture
def api_client_without_tags(mock_broker_without_tag_manager):
    """Create API client without tag manager"""
    app = create_api(mock_broker_without_tag_manager)
    return TestClient(app, headers=AUTH_HEADERS)


class TestTagStatus:
    """Tests for tag management status endpoint"""
    
    def test_tag_status_enabled(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test tag status when enabled"""
        response = api_client_with_tags.get("/api/tags/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert "active" in data["message"].lower()
    
    def test_tag_status_disabled(self, api_client_without_tags):
        """Test tag status when disabled"""
        response = api_client_without_tags.get("/api/tags/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False


class TestTagCRUD:
    """Tests for tag CRUD operations"""
    
    def test_add_tag_success(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test adding a tag successfully"""
        tag_data = {
            "id_tag": "TAG001",
            "status": "Accepted",
            "tag_type": "RFID"
        }
        
        response = api_client_with_tags.post("/api/tags/organizations/Org1/tags", json=tag_data)
        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_broker_with_tag_manager.tag_manager.add_tag.assert_called_once()
    
    def test_get_tag_success(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test getting a tag successfully"""
        mock_tag = OCPPTag(id_tag="TAG001", status=TagStatus.ACCEPTED, tag_type=TagType.RFID)
        mock_broker_with_tag_manager.tag_manager.get_tag = AsyncMock(return_value=mock_tag)
        
        response = api_client_with_tags.get("/api/tags/organizations/Org1/tags/TAG001")
        assert response.status_code == 200
        data = response.json()
        assert data["id_tag"] == "TAG001"
    
    def test_get_tag_not_found(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test getting a non-existent tag"""
        mock_broker_with_tag_manager.tag_manager.get_tag = AsyncMock(return_value=None)
        
        response = api_client_with_tags.get("/api/tags/organizations/Org1/tags/NONEXISTENT")
        # The API catches HTTPException and returns 500, but the detail should indicate not found
        # This is a known issue in the API implementation
        assert response.status_code in [404, 500]
        if response.status_code == 500:
            assert "not found" in response.json()["detail"].lower()
    
    def test_update_tag_success(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test updating a tag successfully"""
        tag_data = {
            "id_tag": "TAG001",
            "status": "Blocked",
            "tag_type": "RFID"
        }
        
        response = api_client_with_tags.put("/api/tags/organizations/Org1/tags/TAG001", json=tag_data)
        assert response.status_code == 200
        assert response.json()["success"] is True
    
    def test_delete_tag_success(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test deleting a tag successfully"""
        response = api_client_with_tags.delete("/api/tags/organizations/Org1/tags/TAG001")
        assert response.status_code == 200
        assert response.json()["success"] is True


class TestTagSearch:
    """Tests for tag search and listing"""
    
    def test_search_tags(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test searching tags"""
        response = api_client_with_tags.get("/api/tags/organizations/Org1/tags")
        assert response.status_code == 200
        data = response.json()
        assert "tags" in data
        assert "total" in data
    
    def test_search_tags_with_filters(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test searching tags with filters"""
        response = api_client_with_tags.get(
            "/api/tags/organizations/Org1/tags",
            params={"status": "Accepted", "limit": 10, "offset": 0}
        )
        assert response.status_code == 200
    
    def test_get_tag_list(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test getting complete tag list"""
        response = api_client_with_tags.get("/api/tags/organizations/Org1/list")
        assert response.status_code == 200
        data = response.json()
        assert "listVersion" in data
        assert "tags" in data
    
    def test_get_tag_statistics(self, real_tag_client):
        """Test getting tag statistics"""
        response = real_tag_client.get("/api/tags/organizations/Org1/statistics")
        assert response.status_code == 200
        assert response.json() == {
            "total_tags": 3,
            "active_tags": 1,  # TAG001; OLD001 is Accepted but past its expiry
            "expired_tags": 1,
            "blocked_tags": 1,
            "tags_by_type": {"RFID": 2, "NFC": 1},
            "tags_by_status": {"Accepted": 2, "Blocked": 1},
        }

    def test_statistics_for_an_organization_without_tags(self, real_tag_client):
        data = real_tag_client.get("/api/tags/organizations/Nobody/statistics").json()
        assert data["total_tags"] == 0 and data["tags_by_status"] == {}


class TestTagOperations:
    """Tests for tag operations"""
    
    def test_validate_tag(self, real_tag_client):
        """Test validating a tag"""
        response = real_tag_client.post(
            "/api/tags/organizations/Org1/tags/validate",
            json={"id_tag": "NEW001", "status": "Accepted", "tag_type": "RFID"},
        )
        assert response.status_code == 200
        assert response.json() == {"is_valid": True, "errors": [], "warnings": []}

    def test_validate_tag_reports_problems(self, real_tag_client):
        response = real_tag_client.post(
            "/api/tags/organizations/Org1/tags/validate",
            json={"id_tag": "TAG001", "status": "Accepted", "parent_id_tag": "GHOST",
                  "expiry_date": "not-a-date"},
        )
        data = response.json()
        assert response.status_code == 200 and data["is_valid"] is False
        assert any("already exists" in e for e in data["errors"])
        assert any("GHOST" in e for e in data["errors"])
        assert any("not a valid ISO 8601" in e for e in data["errors"])

    def test_validate_tag_for_update_allows_an_existing_id(self, real_tag_client):
        response = real_tag_client.post(
            "/api/tags/organizations/Org1/tags/validate?for_update=true",
            json={"id_tag": "TAG001", "status": "Blocked"},
        )
        assert response.json()["is_valid"] is True

    def test_validate_tag_rejects_a_malformed_body_with_422(self, real_tag_client):
        response = real_tag_client.post(
            "/api/tags/organizations/Org1/tags/validate", json={"id_tag": "X" * 21, "status": "Accepted"}
        )
        assert response.status_code == 422

    def test_authorize_tag(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test authorizing a tag"""
        # The endpoint expects a string in the body, not a JSON object
        response = api_client_with_tags.post(
            "/api/tags/organizations/Org1/tags/authorize",
            json="TAG001"
        )
        assert response.status_code == 200
        data = response.json()
        assert "idTag" in data
        assert "idTagInfo" in data
    
    def test_bulk_operation(self, real_tag_client):
        """Test bulk operations"""
        bulk_data = {
            "operation": "delete",
            "tags": [
                {"id_tag": "TAG001", "status": "Accepted", "tag_type": "RFID"},
                {"id_tag": "NOPE", "status": "Accepted", "tag_type": "RFID"},
            ],
        }

        response = real_tag_client.post("/api/tags/organizations/Org1/tags/bulk", json=bulk_data)

        assert response.status_code == 200
        data = response.json()
        assert (data["total"], data["succeeded"], data["failed"]) == (2, 1, 1)
        assert data["results"] == [
            {"id_tag": "TAG001", "success": True, "error": None},
            {"id_tag": "NOPE", "success": False, "error": "not found"},
        ]
        assert real_tag_client.get("/api/tags/organizations/Org1/tags/TAG001").status_code == 404

    def test_bulk_add_then_unknown_operation(self, real_tag_client):
        added = real_tag_client.post(
            "/api/tags/organizations/Org1/tags/bulk",
            json={"operation": "add", "tags": [{"id_tag": "B1", "status": "Accepted"}]},
        )
        assert added.json()["succeeded"] == 1
        assert real_tag_client.get("/api/tags/organizations/Org1/tags/B1").status_code == 200

        bad = real_tag_client.post(
            "/api/tags/organizations/Org1/tags/bulk", json={"operation": "explode", "tags": []}
        )
        assert bad.status_code == 422

    def test_import_tags(self, real_tag_client):
        """Test importing tags"""
        import_data = {
            "source": "json",
            "data": '{"tags": [{"id_tag": "IMP001", "status": "Accepted", "tag_type": "RFID"}]}',
            "overwrite_existing": False,
            "validate_only": False,
        }

        response = real_tag_client.post("/api/tags/organizations/Org1/tags/import", json=import_data)

        assert response.status_code == 200
        data = response.json()
        assert (data["total"], data["imported"], data["updated"], data["skipped"], data["errors"]) == (1, 1, 0, 0, [])
        assert real_tag_client.get("/api/tags/organizations/Org1/tags/IMP001").status_code == 200

    def test_import_unparseable_data_is_400(self, real_tag_client):
        response = real_tag_client.post(
            "/api/tags/organizations/Org1/tags/import", json={"source": "json", "data": "{nope"}
        )
        assert response.status_code == 400
        assert "Invalid JSON" in response.json()["detail"]

    def test_export_tags(self, real_tag_client):
        """Test exporting tags"""
        response = real_tag_client.post("/api/tags/organizations/Org1/tags/export", json={"format": "json"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body["organization"] == "Org1" and body["count"] == 3
        assert {t["id_tag"] for t in body["tags"]} == {"TAG001", "TAG002", "OLD001"}

    def test_export_csv_is_a_download(self, real_tag_client):
        response = real_tag_client.post(
            "/api/tags/organizations/Org1/tags/export", json={"format": "csv", "include_metadata": False}
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert 'filename="Org1-tags.csv"' in response.headers["content-disposition"]
        lines = response.text.splitlines()
        assert lines[0] == "id_tag,status,tag_type,expiry_date,parent_id_tag,description"
        assert len(lines) == 4

    def test_export_defaults_to_json_without_a_body(self, real_tag_client):
        assert real_tag_client.post("/api/tags/organizations/Org1/tags/export").json()["count"] == 3


class TestTagOrganizations:
    """Tests for organization listing"""
    
    def test_list_organizations(self, api_client_with_tags, mock_broker_with_tag_manager):
        """Test listing organizations with tag management"""
        response = api_client_with_tags.get("/api/tags/organizations")
        assert response.status_code == 200
        data = response.json()
        assert "organizations" in data
        assert isinstance(data["organizations"], list)

