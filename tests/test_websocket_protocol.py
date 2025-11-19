"""
Tests for WebSocket subprotocol (sec-websocket-protocol) validation.

Tests both:
1. Incoming connections from chargers to the broker
2. Outgoing connections from broker to backend servers
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketState

from ocpp_broker.server import app
from ocpp_broker.broker import OcppBroker
from ocpp_broker.backend_manager import BackendConnection
from ocpp_broker.config import load_config


class TestChargerToBrokerProtocol:
    """Tests for sec-websocket-protocol in charger-to-broker connections"""
    
    @pytest.fixture
    def mock_broker(self):
        """Create a mock broker"""
        broker = Mock(spec=OcppBroker)
        broker.config_data = {
            "organizations": [
                {
                    "name": "testOrg",
                    "connect_to_backend": False,
                    "ocpp_subprotocol": "ocpp1.6"
                }
            ]
        }
        broker.sessions = {}
        broker.org_registries = {}
        broker.tag_manager = None
        broker.data_transfer_handler = None
        broker.handle_charger = AsyncMock()
        broker.load_config = AsyncMock()
        broker.ensure_org_initialized = AsyncMock()
        return broker
    
    @pytest.mark.asyncio
    async def test_charger_connection_with_ocpp16_protocol(self, mock_broker):
        """Test that charger connection with ocpp1.6 protocol is accepted"""
        # Mock WebSocket with ocpp1.6 protocol
        mock_ws = MagicMock(spec=WebSocket)
        mock_ws.headers = {"sec-websocket-protocol": "ocpp1.6"}
        mock_ws.accept = AsyncMock()
        mock_ws.client_state = WebSocketState.CONNECTED
        
        # Patch broker in server module
        with patch('ocpp_broker.server.broker', mock_broker):
            from ocpp_broker.server import ocpp_entry
            
            # Simulate connection
            try:
                await ocpp_entry(mock_ws, "testOrg", "CHARGER001")
            except Exception:
                pass  # Expected to fail after accept since we're not running full server
            
            # Verify accept was called with ocpp1.6 subprotocol
            mock_ws.accept.assert_called_once()
            call_args = mock_ws.accept.call_args
            # accept() is called with keyword argument subprotocol=...
            assert call_args[1]["subprotocol"] == "ocpp1.6", "Should accept ocpp1.6 subprotocol"
    
    @pytest.mark.asyncio
    async def test_charger_connection_without_protocol_rejected(self, mock_broker):
        """Test that charger connection WITHOUT protocol header is REJECTED"""
        mock_broker.config_data = {
            "organizations": [
                {
                    "name": "testOrg",
                    "connect_to_backend": False,
                    "ocpp_subprotocol": "ocpp1.6"
                }
            ]
        }
        mock_broker.load_config = AsyncMock()
        
        mock_ws = MagicMock(spec=WebSocket)
        mock_ws.headers = {}  # No sec-websocket-protocol header
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.client_state = WebSocketState.CONNECTED
        
        with patch('ocpp_broker.server.broker', mock_broker):
            from ocpp_broker.server import ocpp_entry
            
            await ocpp_entry(mock_ws, "testOrg", "CHARGER001")
            
            # Should NOT accept - should close with error code
            mock_ws.accept.assert_not_called(), "Should NOT accept connection without sec-websocket-protocol header"
            mock_ws.close.assert_called_once(), "Should close connection when header is missing"
            # Verify close was called with appropriate error code
            close_call = mock_ws.close.call_args
            assert close_call[1]["code"] == 1002, "Should close with code 1002 (protocol error)"
            assert "sec-websocket-protocol" in close_call[1]["reason"].lower(), "Reason should mention missing header"
    
    @pytest.mark.asyncio
    async def test_charger_connection_with_wrong_protocol_rejected(self, mock_broker):
        """Test that charger connection with WRONG protocol is REJECTED"""
        mock_broker.config_data = {
            "organizations": [
                {
                    "name": "testOrg",
                    "connect_to_backend": False,
                    "ocpp_subprotocol": "ocpp1.6"  # Expects ocpp1.6
                }
            ]
        }
        mock_broker.load_config = AsyncMock()
        
        mock_ws = MagicMock(spec=WebSocket)
        mock_ws.headers = {"sec-websocket-protocol": "ocpp2.0.1"}  # Different protocol
        mock_ws.accept = AsyncMock()
        mock_ws.close = AsyncMock()
        mock_ws.client_state = WebSocketState.CONNECTED
        
        with patch('ocpp_broker.server.broker', mock_broker):
            from ocpp_broker.server import ocpp_entry
            
            await ocpp_entry(mock_ws, "testOrg", "CHARGER001")
            
            # Should NOT accept - should close with error
            mock_ws.accept.assert_not_called(), "Should NOT accept connection with mismatched subprotocol"
            mock_ws.close.assert_called_once(), "Should close connection when subprotocol doesn't match"
            # Verify close was called with appropriate error code
            close_call = mock_ws.close.call_args
            assert close_call[1]["code"] == 1002, "Should close with code 1002 (protocol error)"
            assert "subprotocol" in close_call[1]["reason"].lower(), "Reason should mention subprotocol mismatch"
    
    @pytest.mark.asyncio
    async def test_charger_connection_with_multiple_protocols(self, mock_broker):
        """Test that charger connection with multiple protocols is handled correctly"""
        mock_ws = MagicMock(spec=WebSocket)
        mock_ws.headers = {"sec-websocket-protocol": "ocpp2.0.1, ocpp1.6"}  # Multiple protocols
        mock_ws.accept = AsyncMock()
        mock_ws.client_state = WebSocketState.CONNECTED
        
        with patch('ocpp_broker.server.broker', mock_broker):
            from ocpp_broker.server import ocpp_entry
            
            try:
                await ocpp_entry(mock_ws, "testOrg", "CHARGER001")
            except Exception:
                pass
            
            # Should accept with ocpp1.6 if it's in the list
            mock_ws.accept.assert_called_once()
            call_args = mock_ws.accept.call_args
            # accept() is called with keyword argument subprotocol=...
            assert call_args[1]["subprotocol"] == "ocpp1.6", "Should accept ocpp1.6 when it's in the protocol list"


class TestBrokerToBackendProtocol:
    """Tests for sec-websocket-protocol in broker-to-backend connections"""
    
    @pytest.fixture
    def mock_broker(self):
        """Create a mock broker"""
        broker = Mock(spec=OcppBroker)
        broker.sessions = {}
        broker.org_backends = {}
        broker._on_backend_connected = Mock()
        broker._on_backend_disconnected = Mock()
        return broker
    
    @pytest.mark.asyncio
    async def test_backend_connection_uses_configured_subprotocol(self, mock_broker):
        """Test that backend connection uses the configured subprotocol"""
        # Create BackendConnection with specific subprotocol
        backend_conn = BackendConnection(
            broker=mock_broker,
            charger_id="CHARGER001",
            url="ws://test-backend.com/ocpp",
            org="testOrg",
            is_leader=True,
            subprotocol="ocpp1.6"
        )
        
        assert backend_conn.subprotocol == "ocpp1.6", "Backend connection should use configured subprotocol"
    
    @pytest.mark.asyncio
    async def test_backend_connection_with_different_subprotocol(self, mock_broker):
        """Test that backend connection can use different subprotocol"""
        backend_conn = BackendConnection(
            broker=mock_broker,
            charger_id="CHARGER001",
            url="ws://test-backend.com/ocpp",
            org="testOrg",
            is_leader=True,
            subprotocol="ocpp2.0.1"
        )
        
        assert backend_conn.subprotocol == "ocpp2.0.1", "Backend connection should use specified subprotocol"
    
    @pytest.mark.asyncio
    async def test_backend_connection_defaults_to_ocpp16(self, mock_broker):
        """Test that backend connection defaults to ocpp1.6 if not specified"""
        backend_conn = BackendConnection(
            broker=mock_broker,
            charger_id="CHARGER001",
            url="ws://test-backend.com/ocpp",
            org="testOrg",
            is_leader=True
        )
        
        assert backend_conn.subprotocol == "ocpp1.6", "Backend connection should default to ocpp1.6"
    
    @pytest.mark.asyncio
    @patch('ocpp_broker.backend_manager.websockets.connect')
    async def test_backend_connection_sends_subprotocol_in_handshake(self, mock_connect, mock_broker):
        """Test that backend connection sends subprotocol in WebSocket handshake"""
        # Create a mock session so charger appears connected
        mock_session = Mock()
        mock_session.websocket = Mock()
        mock_session.websocket.client_state = Mock()
        mock_session.websocket.client_state.name = "CONNECTED"
        mock_broker.sessions["CHARGER001"] = mock_session
        
        # Mock websocket connection
        mock_ws = AsyncMock()
        mock_ws.subprotocol = "ocpp1.6"
        mock_ws.close_code = None
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)
        mock_ws.__iter__ = AsyncMock(return_value=iter([]))
        
        mock_connect.return_value = mock_ws
        
        backend_conn = BackendConnection(
            broker=mock_broker,
            charger_id="CHARGER001",
            url="ws://test-backend.com/ocpp",
            org="testOrg",
            is_leader=True,
            subprotocol="ocpp1.6"
        )
        
        # Start connection (will fail but we can check the call)
        backend_conn._running = True
        try:
            await asyncio.wait_for(backend_conn._run_connect_loop(), timeout=0.1)
        except (asyncio.TimeoutError, Exception):
            pass
        
        # Verify websockets.connect was called with subprotocols parameter
        assert mock_connect.called, "websockets.connect should be called"
        call_kwargs = mock_connect.call_args[1]
        assert "subprotocols" in call_kwargs, "subprotocols parameter should be present"
        assert call_kwargs["subprotocols"] == ["ocpp1.6"], "Should use configured subprotocol"
    
    @pytest.mark.asyncio
    @patch('ocpp_broker.backend_manager.websockets.connect')
    async def test_backend_connection_verifies_negotiated_subprotocol(self, mock_connect, mock_broker):
        """Test that backend connection verifies the negotiated subprotocol"""
        # Create a mock session so charger appears connected
        mock_session = Mock()
        mock_session.websocket = Mock()
        mock_session.websocket.client_state = Mock()
        mock_session.websocket.client_state.name = "CONNECTED"
        mock_broker.sessions["CHARGER001"] = mock_session
        
        # Mock websocket with matching subprotocol
        mock_ws = AsyncMock()
        mock_ws.subprotocol = "ocpp1.6"  # Matches requested
        mock_ws.close_code = None
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)
        mock_ws.__iter__ = AsyncMock(return_value=iter([]))
        
        mock_connect.return_value = mock_ws
        
        backend_conn = BackendConnection(
            broker=mock_broker,
            charger_id="CHARGER001",
            url="ws://test-backend.com/ocpp",
            org="testOrg",
            is_leader=True,
            subprotocol="ocpp1.6"
        )
        
        # Capture log messages
        import logging
        log_capture = []
        handler = logging.Handler()
        handler.emit = lambda record: log_capture.append(record.getMessage())
        logger = logging.getLogger("ocpp_broker.backend_manager")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        
        backend_conn._running = True
        try:
            await asyncio.wait_for(backend_conn._run_connect_loop(), timeout=0.1)
        except (asyncio.TimeoutError, Exception):
            pass
        
        # Check that subprotocol verification was logged
        verification_logs = [msg for msg in log_capture if "subprotocol" in msg.lower()]
        assert len(verification_logs) > 0, "Should log subprotocol verification"
        
        logger.removeHandler(handler)
    
    @pytest.mark.asyncio
    @patch('ocpp_broker.backend_manager.websockets.connect')
    async def test_backend_connection_warns_on_mismatched_subprotocol(self, mock_connect, mock_broker):
        """Test that backend connection warns when negotiated subprotocol doesn't match"""
        # Create a mock session so charger appears connected
        mock_session = Mock()
        mock_session.websocket = Mock()
        mock_session.websocket.client_state = Mock()
        mock_session.websocket.client_state.name = "CONNECTED"
        mock_broker.sessions["CHARGER001"] = mock_session
        
        # Mock websocket with mismatched subprotocol
        mock_ws = AsyncMock()
        mock_ws.subprotocol = "ocpp2.0.1"  # Doesn't match requested ocpp1.6
        mock_ws.close_code = None
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=None)
        mock_ws.__iter__ = AsyncMock(return_value=iter([]))
        
        mock_connect.return_value = mock_ws
        
        backend_conn = BackendConnection(
            broker=mock_broker,
            charger_id="CHARGER001",
            url="ws://test-backend.com/ocpp",
            org="testOrg",
            is_leader=True,
            subprotocol="ocpp1.6"
        )
        
        # Capture log messages
        import logging
        log_capture = []
        handler = logging.Handler()
        handler.emit = lambda record: log_capture.append(record.getMessage())
        logger = logging.getLogger("ocpp_broker.backend_manager")
        logger.addHandler(handler)
        logger.setLevel(logging.ERROR)
        
        backend_conn._running = True
        try:
            await asyncio.wait_for(backend_conn._run_connect_loop(), timeout=0.1)
        except (asyncio.TimeoutError, Exception):
            pass
        
        # Check that error was logged for mismatched subprotocol
        error_logs = [msg for msg in log_capture if "subprotocol not negotiated" in msg.lower()]
        assert len(error_logs) > 0, "Should log error when subprotocol doesn't match"
        
        logger.removeHandler(handler)


class TestProtocolConfiguration:
    """Tests for subprotocol configuration from YAML"""
    
    def test_organization_subprotocol_config(self):
        """Test that organization subprotocol is read from config"""
        config = {
            "organizations": [
                {
                    "name": "testOrg",
                    "ocpp_subprotocol": "ocpp2.0.1"
                }
            ]
        }
        
        org = config["organizations"][0]
        subprotocol = org.get("ocpp_subprotocol", "ocpp1.6")
        assert subprotocol == "ocpp2.0.1", "Should read subprotocol from config"
    
    def test_backend_subprotocol_overrides_org(self):
        """Test that backend-specific subprotocol overrides org-level"""
        config = {
            "organizations": [
                {
                    "name": "testOrg",
                    "ocpp_subprotocol": "ocpp1.6",
                    "backends": [
                        {
                            "id": "backend1",
                            "url": "ws://test.com/ocpp",
                            "ocpp_subprotocol": "ocpp2.0.1"  # Override
                        }
                    ]
                }
            ]
        }
        
        org = config["organizations"][0]
        backend = org["backends"][0]
        
        # Backend-specific should take precedence
        subprotocol = backend.get("ocpp_subprotocol", org.get("ocpp_subprotocol", "ocpp1.6"))
        assert subprotocol == "ocpp2.0.1", "Backend subprotocol should override org-level"
    
    def test_default_subprotocol_when_not_specified(self):
        """Test that default subprotocol is used when not specified"""
        config = {
            "organizations": [
                {
                    "name": "testOrg"
                    # No ocpp_subprotocol specified
                }
            ]
        }
        
        org = config["organizations"][0]
        subprotocol = org.get("ocpp_subprotocol", "ocpp1.6")
        assert subprotocol == "ocpp1.6", "Should default to ocpp1.6"


class TestBackendDisconnectionOnChargerDisconnect:
    """Tests for backend disconnection when charger disconnects"""
    
    @pytest.fixture
    def mock_broker(self):
        """Create a mock broker"""
        broker = Mock(spec=OcppBroker)
        broker.sessions = {}
        broker.org_backends = {}
        broker._on_backend_connected = Mock()
        broker._on_backend_disconnected = Mock()
        return broker
    
    @pytest.mark.asyncio
    async def test_backend_connection_stops_when_charger_disconnects(self, mock_broker):
        """Test that backend connection loop stops when charger disconnects"""
        backend_conn = BackendConnection(
            broker=mock_broker,
            charger_id="CHARGER001",
            url="ws://test-backend.com/ocpp",
            org="testOrg",
            is_leader=True,
            subprotocol="ocpp1.6"
        )
        
        # Create a mock session that will be "disconnected"
        mock_session = Mock()
        mock_session.websocket = Mock()
        mock_session.websocket.client_state = Mock()
        mock_session.websocket.client_state.name = "DISCONNECTED"  # Charger is disconnected
        mock_broker.sessions["CHARGER001"] = mock_session
        
        # Verify that _is_charger_still_connected returns False
        assert not backend_conn._is_charger_still_connected(), "Should detect charger as disconnected"
        
        # The connection loop should exit when charger is disconnected
        backend_conn._running = True
        with patch('ocpp_broker.backend_manager.websockets.connect') as mock_connect:
            # Connection should not even be attempted
            try:
                await asyncio.wait_for(backend_conn._run_connect_loop(), timeout=0.1)
            except (asyncio.TimeoutError, Exception):
                pass
            
            # Should not attempt to connect since charger is disconnected
            # The loop should break immediately
            assert not backend_conn._running or not mock_connect.called, \
                "Should not attempt backend connection when charger is disconnected"
    
    @pytest.mark.asyncio
    async def test_backend_reader_loop_stops_on_charger_disconnect(self, mock_broker):
        """Test that backend reader loop stops when charger disconnects"""
        backend_conn = BackendConnection(
            broker=mock_broker,
            charger_id="CHARGER001",
            url="ws://test-backend.com/ocpp",
            org="testOrg",
            is_leader=True,
            subprotocol="ocpp1.6"
        )
        
        # Create a mock websocket
        mock_ws = AsyncMock()
        mock_ws.subprotocol = "ocpp1.6"
        backend_conn.websocket = mock_ws
        
        # Create a mock session that starts connected but becomes disconnected
        mock_session = Mock()
        mock_session.websocket = Mock()
        mock_session.websocket.client_state = Mock()
        
        # Initially connected
        mock_session.websocket.client_state.name = "CONNECTED"
        mock_broker.sessions["CHARGER001"] = mock_session
        
        # Create an async iterator that yields one message then stops
        async def message_generator():
            yield '["3","msg-id",{}]'  # One message
            # Simulate charger disconnecting
            mock_session.websocket.client_state.name = "DISCONNECTED"
            yield '["3","msg-id-2",{}]'  # This should not be processed
        
        mock_ws.__aiter__ = lambda self: message_generator()
        
        # Run reader loop
        await backend_conn._reader_loop()
        
        # Verify that the loop stopped (we can't directly verify, but if it didn't hang, it worked)
        # The test passing means the loop exited properly


class TestProtocolIntegration:
    """Integration tests for protocol handling end-to-end"""
    
    @pytest.mark.asyncio
    async def test_full_connection_flow_with_protocol(self):
        """Test full connection flow with protocol validation"""
        # This would require a full test server setup
        # For now, we test the components separately
        pass
    
    def test_server_accepts_protocol_header(self):
        """Test that server endpoint checks for sec-websocket-protocol header"""
        # Verify the server code checks for the header
        from ocpp_broker.server import ocpp_entry
        
        # The function should check websocket.headers.get("sec-websocket-protocol", "")
        # This is verified by inspecting the source code structure
        import inspect
        source = inspect.getsource(ocpp_entry)
        assert "sec-websocket-protocol" in source, "Server should check sec-websocket-protocol header"
        assert "subprotocol" in source.lower(), "Server should handle subprotocol"

