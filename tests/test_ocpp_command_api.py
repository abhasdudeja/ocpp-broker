"""
Tests for the OCPP command API endpoints.
"""

import pytest
import json
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock, MagicMock

from ocpp_broker.api_server import create_api
from ocpp_broker.broker import OcppBroker
from ocpp_broker.session import ChargerSession, SessionMode


@pytest.fixture
def mock_broker_with_sessions():
    """Create a mock broker with charger sessions"""
    broker = Mock(spec=OcppBroker)
    broker.sessions = {}
    broker.org_backends = {}
    broker.org_leaders = {}
    broker.config_data = {}
    
    return broker


@pytest.fixture
def mock_session():
    """Create a mock charger session"""
    session = Mock(spec=ChargerSession)
    session.org_name = "TestOrg"
    session.mode = SessionMode.BROKER
    session.backend_conn = None
    session.send_to_charger = AsyncMock()
    return session


@pytest.fixture
def api_client(mock_broker_with_sessions):
    """Create API client"""
    app = create_api(mock_broker_with_sessions)
    return TestClient(app)


class TestChargerEndpoints:
    """Tests for charger listing and status endpoints"""
    
    def test_list_chargers_empty(self, api_client, mock_broker_with_sessions):
        """Test listing chargers when none exist"""
        mock_broker_with_sessions.sessions = {}
        response = api_client.get("/api/ocpp/organizations/TestOrg/chargers")
        assert response.status_code == 200
        data = response.json()
        assert data["organization"] == "TestOrg"
        assert data["chargers"] == []
    
    def test_list_chargers_with_data(self, api_client, mock_broker_with_sessions, mock_session):
        """Test listing chargers with data"""
        mock_session.charger_id = "CP_001"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        
        response = api_client.get("/api/ocpp/organizations/TestOrg/chargers")
        assert response.status_code == 200
        data = response.json()
        assert len(data["chargers"]) == 1
        assert data["chargers"][0]["charger_id"] == "CP_001"
        assert data["chargers"][0]["organization"] == "TestOrg"
    
    def test_get_charger_status_success(self, api_client, mock_broker_with_sessions, mock_session):
        """Test getting charger status successfully"""
        mock_session.charger_id = "CP_001"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        
        response = api_client.get("/api/ocpp/organizations/TestOrg/chargers/CP_001/status")
        assert response.status_code == 200
        data = response.json()
        assert data["charger_id"] == "CP_001"
        assert data["organization"] == "TestOrg"
        assert data["connected"] is True
    
    def test_get_charger_status_not_found(self, api_client, mock_broker_with_sessions):
        """Test getting status for non-existent charger"""
        mock_broker_with_sessions.sessions = {}
        response = api_client.get("/api/ocpp/organizations/TestOrg/chargers/NonExistent/status")
        assert response.status_code == 404
    
    def test_get_charger_status_wrong_org(self, api_client, mock_broker_with_sessions, mock_session):
        """Test getting charger status with wrong organization"""
        mock_session.charger_id = "CP_001"
        mock_session.org_name = "OtherOrg"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        
        response = api_client.get("/api/ocpp/organizations/TestOrg/chargers/CP_001/status")
        assert response.status_code == 404


class TestGenericCommand:
    """Tests for generic command endpoint"""
    
    def test_send_generic_command_success(self, api_client, mock_broker_with_sessions, mock_session):
        """Test sending a generic OCPP command"""
        mock_session.charger_id = "CP_001"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        
        command_data = {
            "action": "Reset",
            "payload": {"type": "Hard"},
            "timeout": 30
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands",
            json=command_data
        )
        assert response.status_code == 200
        data = response.json()
        assert data["charger_id"] == "CP_001"
        assert data["organization"] == "TestOrg"
        assert data["action"] == "Reset"
        assert data["status"] == "sent"
        assert "message_id" in data
        mock_session.send_to_charger.assert_called_once()
    
    def test_send_command_charger_not_found(self, api_client, mock_broker_with_sessions):
        """Test sending command to non-existent charger"""
        mock_broker_with_sessions.sessions = {}
        
        command_data = {
            "action": "Reset",
            "payload": {"type": "Hard"}
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/NonExistent/commands",
            json=command_data
        )
        assert response.status_code == 404
    
    def test_send_command_wrong_org(self, api_client, mock_broker_with_sessions, mock_session):
        """Test sending command with wrong organization"""
        mock_session.charger_id = "CP_001"
        mock_session.org_name = "OtherOrg"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        
        command_data = {
            "action": "Reset",
            "payload": {"type": "Hard"}
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands",
            json=command_data
        )
        assert response.status_code == 404


class TestCoreProfileCommands:
    """Tests for Core Profile OCPP commands"""
    
    @pytest.fixture
    def setup_charger(self, mock_broker_with_sessions, mock_session):
        """Setup a charger session for testing"""
        mock_session.charger_id = "CP_001"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        return mock_session
    
    def test_change_availability(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test ChangeAvailability command"""
        command_data = {
            "connector_id": 1,
            "type": "Inoperative"
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/ChangeAvailability",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "ChangeAvailability"
        setup_charger.send_to_charger.assert_called_once()
    
    def test_change_configuration(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test ChangeConfiguration command"""
        command_data = {
            "key": "HeartbeatInterval",
            "value": "300"
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/ChangeConfiguration",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "ChangeConfiguration"
    
    def test_clear_cache(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test ClearCache command"""
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/ClearCache"
        )
        assert response.status_code == 200
        assert response.json()["action"] == "ClearCache"
    
    def test_data_transfer(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test DataTransfer command"""
        command_data = {
            "vendor_id": "VendorX",
            "message_id": "MSG001",
            "data": "test data"
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/DataTransfer",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "DataTransfer"
    
    def test_get_configuration(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test GetConfiguration command"""
        command_data = {
            "key": ["HeartbeatInterval", "MeterValueSampleInterval"]
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/GetConfiguration",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "GetConfiguration"
    
    def test_remote_start_transaction(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test RemoteStartTransaction command"""
        command_data = {
            "id_tag": "TAG001",
            "connector_id": 1
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/RemoteStartTransaction",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "RemoteStartTransaction"
    
    def test_remote_stop_transaction(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test RemoteStopTransaction command"""
        command_data = {
            "transaction_id": 12345
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/RemoteStopTransaction",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "RemoteStopTransaction"
    
    def test_reset(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test Reset command"""
        command_data = {
            "type": "Hard"
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/Reset",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "Reset"
    
    def test_send_local_list(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test SendLocalList command"""
        command_data = {
            "list_version": 1,
            "update_type": "Full",
            "local_authorization_list": []
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/SendLocalList",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "SendLocalList"
    
    def test_set_charging_profile(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test SetChargingProfile command"""
        command_data = {
            "connector_id": 1,
            "cs_charging_profiles": {
                "chargingProfileId": 1,
                "stackLevel": 0
            }
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/SetChargingProfile",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "SetChargingProfile"
    
    def test_unlock_connector(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test UnlockConnector command"""
        command_data = {
            "connector_id": 1
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/UnlockConnector",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "UnlockConnector"
    
    def test_update_firmware(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test UpdateFirmware command"""
        command_data = {
            "location": "http://example.com/firmware.bin",
            "retrieve_date": "2024-01-01T00:00:00Z",
            "retry_interval": 60
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/UpdateFirmware",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "UpdateFirmware"


class TestSmartChargingCommands:
    """Tests for Smart Charging Profile commands"""
    
    @pytest.fixture
    def setup_charger(self, mock_broker_with_sessions, mock_session):
        """Setup a charger session for testing"""
        mock_session.charger_id = "CP_001"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        return mock_session
    
    def test_clear_charging_profile(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test ClearChargingProfile command"""
        command_data = {
            "id": 1,
            "connector_id": 1
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/ClearChargingProfile",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "ClearChargingProfile"
    
    def test_get_composite_schedule(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test GetCompositeSchedule command"""
        command_data = {
            "connector_id": 1,
            "duration": 3600,
            "charging_rate_unit": "W"
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/GetCompositeSchedule",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "GetCompositeSchedule"
    
    def test_trigger_message(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test TriggerMessage command"""
        command_data = {
            "requested_message": "StatusNotification",
            "connector_id": 1
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/TriggerMessage",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "TriggerMessage"


class TestFirmwareManagementCommands:
    """Tests for Firmware Management Profile commands"""
    
    @pytest.fixture
    def setup_charger(self, mock_broker_with_sessions, mock_session):
        """Setup a charger session for testing"""
        mock_session.charger_id = "CP_001"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        return mock_session
    
    def test_get_diagnostics(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test GetDiagnostics command"""
        command_data = {
            "location": "http://example.com/diagnostics",
            "start_time": "2024-01-01T00:00:00Z",
            "stop_time": "2024-01-02T00:00:00Z"
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/GetDiagnostics",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "GetDiagnostics"


class TestLocalAuthListCommands:
    """Tests for Local Authorization List Profile commands"""
    
    @pytest.fixture
    def setup_charger(self, mock_broker_with_sessions, mock_session):
        """Setup a charger session for testing"""
        mock_session.charger_id = "CP_001"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        return mock_session
    
    def test_get_local_list_version(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test GetLocalListVersion command"""
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/GetLocalListVersion"
        )
        assert response.status_code == 200
        assert response.json()["action"] == "GetLocalListVersion"


class TestReservationCommands:
    """Tests for Reservation Profile commands"""
    
    @pytest.fixture
    def setup_charger(self, mock_broker_with_sessions, mock_session):
        """Setup a charger session for testing"""
        mock_session.charger_id = "CP_001"
        mock_broker_with_sessions.sessions = {"CP_001": mock_session}
        return mock_session
    
    def test_cancel_reservation(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test CancelReservation command"""
        command_data = {
            "reservation_id": 1
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/CancelReservation",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "CancelReservation"
    
    def test_reserve_now(self, api_client, mock_broker_with_sessions, setup_charger):
        """Test ReserveNow command"""
        command_data = {
            "connector_id": 1,
            "expiry_date": "2024-12-31T23:59:59Z",
            "id_tag": "TAG001",
            "reservation_id": 1
        }
        
        response = api_client.post(
            "/api/ocpp/organizations/TestOrg/chargers/CP_001/commands/ReserveNow",
            json=command_data
        )
        assert response.status_code == 200
        assert response.json()["action"] == "ReserveNow"


class TestCommandResponse:
    """Tests for command response retrieval"""
    
    def test_get_command_response_not_found(self, api_client):
        """Test getting response for non-existent command"""
        response = api_client.get("/api/ocpp/commands/non-existent-id/response")
        assert response.status_code == 404

