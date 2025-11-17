"""
Tests for DataTransfer command handler.
"""

import pytest
from unittest.mock import Mock, AsyncMock

from ocpp_broker.data_transfer_handler import DataTransferHandler, DataTransferStatus, create_data_transfer_handler
from ocpp_broker.broker import OcppBroker


@pytest.fixture
def mock_broker():
    """Create a mock broker with system-wide data_transfer config"""
    broker = Mock(spec=OcppBroker)
    broker.config_data = {
        "data_transfer": {
            "known_vendors": ["VendorA", "VendorB", "VendorC"],
            "known_message_ids": ["MSG001", "MSG002"],
            "vendors": {
                "VendorC": {"auto_accept": True, "require_message_id": True, "allowed_message_ids": ["MSG003"]},
                "VendorD": {"auto_accept": False}
            },
            "vendor_messages": {
                "VendorE:MSG004": {"auto_accept": True}
            }
        },
        "organizations": [
            {"name": "TestOrg"},
            {"name": "OrgWithoutDT"}
        ]
    }
    return broker


@pytest.fixture
def handler(mock_broker):
    """Create a DataTransferHandler instance"""
    return DataTransferHandler(mock_broker)


class TestDataTransferHandler:
    """Tests for DataTransferHandler"""
    
    @pytest.mark.asyncio
    async def test_handle_data_transfer_unknown_vendor(self, handler):
        """Test handling DataTransfer with unknown vendor"""
        status, response_data = await handler.handle_data_transfer(
            charger_id="CP_001",
            org_name="TestOrg",
            vendor_id="UnknownVendor",
            message_id="MSG001",
            data='{"test": "data"}'
        )
        
        assert status == DataTransferStatus.UNKNOWN_VENDOR_ID
        assert response_data is None
    
    @pytest.mark.asyncio
    async def test_handle_data_transfer_known_vendor(self, handler):
        """Test handling DataTransfer with known vendor"""
        status, response_data = await handler.handle_data_transfer(
            charger_id="CP_001",
            org_name="TestOrg",
            vendor_id="VendorA",
            message_id="MSG001",
            data='{"test": "data"}'
        )
        
        assert status == DataTransferStatus.ACCEPTED
        assert response_data is not None
    
    @pytest.mark.asyncio
    async def test_handle_data_transfer_with_custom_handler(self, handler):
        """Test handling DataTransfer with registered vendor handler"""
        async def custom_handler(charger_id, org_name, message_id, data):
            return DataTransferStatus.ACCEPTED, '{"processed": true}'
        
        handler.register_vendor_handler("CustomVendor", custom_handler)
        
        status, response_data = await handler.handle_data_transfer(
            charger_id="CP_001",
            org_name="TestOrg",
            vendor_id="CustomVendor",
            message_id="MSG001",
            data='{"test": "data"}'
        )
        
        assert status == DataTransferStatus.ACCEPTED
        assert response_data == '{"processed": true}'
    
    @pytest.mark.asyncio
    async def test_handle_data_transfer_json_data(self, handler):
        """Test handling DataTransfer with JSON data"""
        status, response_data = await handler.handle_data_transfer(
            charger_id="CP_001",
            org_name="TestOrg",
            vendor_id="VendorA",
            message_id="MSG001",
            data='{"key": "value", "number": 123}'
        )
        
        assert status == DataTransferStatus.ACCEPTED
        # Response should contain processed data
    
    @pytest.mark.asyncio
    async def test_handle_data_transfer_plain_text(self, handler):
        """Test handling DataTransfer with plain text data"""
        status, response_data = await handler.handle_data_transfer(
            charger_id="CP_001",
            org_name="TestOrg",
            vendor_id="VendorA",
            message_id="MSG001",
            data="plain text data"
        )
        
        assert status == DataTransferStatus.ACCEPTED
    
    @pytest.mark.asyncio
    async def test_handle_data_transfer_no_data(self, handler):
        """Test handling DataTransfer without data"""
        status, response_data = await handler.handle_data_transfer(
            charger_id="CP_001",
            org_name="TestOrg",
            vendor_id="VendorA",
            message_id="MSG001",
            data=None
        )
        
        assert status == DataTransferStatus.ACCEPTED
    
    def test_get_transfer_history(self, handler):
        """Test getting transfer history"""
        # Simulate some transfers
        handler.received_transfers = [
            {"charger_id": "CP_001", "vendor_id": "VendorA", "message_id": "MSG001", "data": "data1", "org_name": "TestOrg", "timestamp": None},
            {"charger_id": "CP_002", "vendor_id": "VendorB", "message_id": "MSG002", "data": "data2", "org_name": "TestOrg", "timestamp": None},
            {"charger_id": "CP_001", "vendor_id": "VendorA", "message_id": "MSG003", "data": "data3", "org_name": "TestOrg", "timestamp": None},
        ]
        
        # Get all transfers
        all_transfers = handler.get_transfer_history()
        assert len(all_transfers) == 3
        
        # Filter by charger
        charger_transfers = handler.get_transfer_history(charger_id="CP_001")
        assert len(charger_transfers) == 2
        
        # Filter by vendor
        vendor_transfers = handler.get_transfer_history(vendor_id="VendorA")
        assert len(vendor_transfers) == 2
        
        # Filter by message_id
        message_transfers = handler.get_transfer_history(message_id="MSG001")
        assert len(message_transfers) == 1
        
        # Filter by both
        filtered = handler.get_transfer_history(charger_id="CP_001", vendor_id="VendorA")
        assert len(filtered) == 2
        
        # Test limit
        limited = handler.get_transfer_history(limit=2)
        assert len(limited) == 2
    
    @pytest.mark.asyncio
    async def test_validate_vendor_and_message_unknown_vendor(self, handler):
        """Test validation with unknown vendor"""
        handler.broker.config_data = {
            "data_transfer": {
                "known_vendors": ["VendorA"],
                "known_message_ids": ["MSG001"]
            }
        }
        
        result = handler._validate_vendor_and_message("UnknownVendor", "MSG001")
        assert result["valid"] is False
        assert result["status"] == DataTransferStatus.UNKNOWN_VENDOR_ID
    
    @pytest.mark.asyncio
    async def test_validate_vendor_and_message_unknown_message(self, handler):
        """Test validation with unknown message ID"""
        handler.broker.config_data = {
            "data_transfer": {
                "known_vendors": ["VendorA"],
                "known_message_ids": ["MSG001"]
            }
        }
        
        result = handler._validate_vendor_and_message("VendorA", "UnknownMSG")
        assert result["valid"] is False
        assert result["status"] == DataTransferStatus.UNKNOWN_MESSAGE_ID
    
    @pytest.mark.asyncio
    async def test_validate_vendor_and_message_valid(self, handler):
        """Test validation with valid vendor and message"""
        handler.broker.config_data = {
            "data_transfer": {
                "known_vendors": ["VendorA"],
                "known_message_ids": ["MSG001"]
            }
        }
        
        result = handler._validate_vendor_and_message("VendorA", "MSG001")
        assert result["valid"] is True
        assert result["status"] is None
    
    def test_get_known_vendors(self, handler):
        """Test getting known vendors list from system-wide config"""
        vendors = handler.get_known_vendors()
        assert "VendorA" in vendors
        assert "VendorB" in vendors
        assert "VendorC" in vendors  # From vendors dict
    
    def test_get_known_message_ids(self, handler):
        """Test getting known message IDs list from system-wide config"""
        message_ids = handler.get_known_message_ids()
        assert "MSG001" in message_ids
        assert "MSG002" in message_ids
        assert "MSG003" in message_ids  # From VendorC config
        assert "MSG004" in message_ids  # From vendor_messages key


class TestDataTransferInChargePoint:
    """Tests for DataTransfer handler in BrokerChargePoint"""
    
    @pytest.mark.asyncio
    async def test_data_transfer_handler_integration(self):
        """Test DataTransfer handler integration with BrokerChargePoint"""
        from ocpp_broker.charge_point import BrokerChargePoint
        
        class DummyConnection:
            async def recv(self):
                raise RuntimeError("recv should not be used in unit tests")
            async def send(self, message: str):
                self.last_sent = message
            async def close(self, code: int = 1000, reason: str | None = None):
                self.closed_with = (code, reason)
        
        broker = Mock(spec=OcppBroker)
        broker.config_data = {
            "data_transfer": {
                "known_vendors": ["VendorA"],
                "known_message_ids": ["MSG001"]
            },
            "organizations": [
                {
                    "name": "TestOrg"
                }
            ]
        }
        broker.data_transfer_handler = None
        
        # Create handler
        handler = create_data_transfer_handler(broker)
        broker.data_transfer_handler = handler
        
        # Create charge point
        connection = DummyConnection()
        cp = BrokerChargePoint("CP_001", connection, broker, "TestOrg")
        
        # Call DataTransfer handler
        result = await cp.on_data_transfer(
            vendor_id="VendorA",
            message_id="MSG001",
            data='{"test": "data"}'
        )
        
        # Check result is OCPP-compliant
        assert result.status == DataTransferStatus.ACCEPTED
        assert hasattr(result, 'data')
    
    @pytest.mark.asyncio
    async def test_handle_data_transfer_not_implemented_when_disabled(self):
        """Test that DataTransfer returns NotImplemented when disabled in config"""
        from ocpp_broker.charge_point import BrokerChargePoint
        
        class DummyConnection:
            async def recv(self):
                raise RuntimeError("recv should not be used in unit tests")
            async def send(self, message: str):
                self.last_sent = message
            async def close(self, code: int = 1000, reason: str | None = None):
                self.closed_with = (code, reason)
        
        broker = Mock(spec=OcppBroker)
        broker.config_data = {
            "data_transfer": {
                "known_vendors": ["VendorA"],
                "known_message_ids": ["MSG001"],
                "enabled": False  # DataTransfer disabled
            },
            "organizations": [
                {
                    "name": "TestOrg"
                }
            ]
        }
        broker.data_transfer_handler = None
        
        # Create handler
        handler = create_data_transfer_handler(broker)
        broker.data_transfer_handler = handler
        
        # Create charge point
        connection = DummyConnection()
        cp = BrokerChargePoint("CP_001", connection, broker, "TestOrg")
        
        # Call DataTransfer handler
        result = await cp.on_data_transfer(
            vendor_id="VendorA",
            message_id="MSG001",
            data='{"test": "data"}'
        )
        
        # Check result is NotImplemented
        assert result.status == DataTransferStatus.NOT_IMPLEMENTED
        assert result.data is None

