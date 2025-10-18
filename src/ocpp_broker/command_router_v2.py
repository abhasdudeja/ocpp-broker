"""
Enhanced OCPP 1.6 Command Router

Extends the original command router with full OCPP 1.6 command support,
including proper message validation, command handling, and error management.
"""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, List

from .commands.base import OCPPCommandRegistry, BaseCommandHandler, OCPPCommandResult
from .commands.core import CoreProfileHandler
from .schemas.messages import OCPPCall, OCPPCallResult, OCPPCallError, OCPPErrorCode

logger = logging.getLogger("ocpp_broker.command_router_v2")


class OCPPCommandRouter:
    """
    Enhanced command router with full OCPP 1.6 support.
    
    Features:
    - Message validation and parsing
    - Command-specific handling
    - Error management
    - Response generation
    - Logging and monitoring
    """
    
    def __init__(self, broker):
        self.broker = broker
        self.registry = OCPPCommandRegistry()
        self._initialize_handlers()
    
    def _initialize_handlers(self):
        """Initialize all OCPP command handlers"""
        # Core Profile handlers
        core_handler = CoreProfileHandler(self.broker)
        
        # Register Core Profile commands
        for action, handler in core_handler.handlers.items():
            self.registry.register_handler(action, handler, "core")
        
        logger.info(f"Initialized OCPP command router with {len(self.registry.list_handlers())} handlers")
    
    async def route_charger_message(self, charger_id: str, message: str) -> Optional[str]:
        """
        Route a message from a charger to the appropriate handler.
        
        Args:
            charger_id: ID of the charger sending the message
            message: Raw OCPP message (JSON string)
            
        Returns:
            Response message to send back to charger (if any)
        """
        try:
            # Parse the OCPP message
            parsed_message = self._parse_ocpp_message(message)
            if not parsed_message:
                return None
            
            message_type = parsed_message.get("message_type")
            
            if message_type == 2:  # OCPP Call
                return await self._handle_ocpp_call(charger_id, parsed_message)
            elif message_type == 3:  # OCPP CallResult
                return await self._handle_ocpp_call_result(charger_id, parsed_message)
            elif message_type == 4:  # OCPP CallError
                return await self._handle_ocpp_call_error(charger_id, parsed_message)
            else:
                logger.warning(f"Unknown message type {message_type} from charger {charger_id}")
                return None
                
        except Exception as e:
            logger.error(f"Error routing message from charger {charger_id}: {e}")
            return None
    
    async def route_backend_message(self, backend, message: str):
        """
        Route a message from a backend to the appropriate charger.
        
        Args:
            backend: Backend connection object
            message: Raw message from backend
        """
        try:
            # Check if backend is leader - only leader backends can send commands
            if not getattr(backend, 'is_leader', False):
                logger.debug(f"Ignoring follower message from {backend.id} ({backend.org})")
                return
            
            # Parse backend message
            parsed_message = self._parse_backend_message(message)
            if not parsed_message:
                return
            
            # Extract target charger
            target_charger = self._extract_target_charger(parsed_message)
            if not target_charger:
                logger.warning(f"No target charger found in backend message: {message[:200]}")
                return
            
            # Get charger WebSocket connection
            charger_ws = self.broker.active_chargers.get(target_charger)
            if not charger_ws or charger_ws.closed:
                logger.warning(f"Cannot deliver message to charger {target_charger}: not connected")
                return
            
            # Send message to charger
            await charger_ws.send_text(message)
            logger.info(f"Delivered leader command from {backend.id} ({backend.org}) to charger {target_charger}")
            
        except Exception as e:
            logger.error(f"Error routing backend message: {e}")
    
    def _parse_ocpp_message(self, message: str) -> Optional[Dict[str, Any]]:
        """Parse and validate OCPP message"""
        try:
            data = json.loads(message)
            
            # Validate OCPP message structure
            if not isinstance(data, list) or len(data) < 3:
                logger.warning(f"Invalid OCPP message format: {message[:200]}")
                return None
            
            message_type = data[0]
            message_id = data[1]
            
            if message_type == 2:  # Call
                if len(data) < 4:
                    return None
                return {
                    "message_type": message_type,
                    "message_id": message_id,
                    "action": data[2],
                    "payload": data[3]
                }
            elif message_type == 3:  # CallResult
                if len(data) < 3:
                    return None
                return {
                    "message_type": message_type,
                    "message_id": message_id,
                    "payload": data[2]
                }
            elif message_type == 4:  # CallError
                if len(data) < 5:
                    return None
                return {
                    "message_type": message_type,
                    "message_id": message_id,
                    "error_code": data[2],
                    "error_description": data[3],
                    "error_details": data[4] if len(data) > 4 else {}
                }
            else:
                logger.warning(f"Unknown OCPP message type: {message_type}")
                return None
                
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in OCPP message: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing OCPP message: {e}")
            return None
    
    def _parse_backend_message(self, message: str) -> Optional[Dict[str, Any]]:
        """Parse backend message (can be JSON object or OCPP array)"""
        try:
            data = json.loads(message)
            
            # Check if it's an OCPP array format
            if isinstance(data, list) and len(data) >= 3:
                return self._parse_ocpp_message(message)
            
            # Check if it's a JSON object with target
            if isinstance(data, dict):
                return data
            
            return None
            
        except json.JSONDecodeError:
            return None
    
    def _extract_target_charger(self, parsed_message: Dict[str, Any]) -> Optional[str]:
        """Extract target charger ID from parsed message"""
        if isinstance(parsed_message, dict):
            # Check for target field
            if "target" in parsed_message:
                return str(parsed_message["target"])
            
            # Check for chargePointId in payload
            payload = parsed_message.get("payload", {})
            if isinstance(payload, dict):
                return payload.get("chargePointId")
        
        return None
    
    async def _handle_ocpp_call(self, charger_id: str, parsed_message: Dict[str, Any]) -> Optional[str]:
        """Handle OCPP Call message from charger"""
        action = parsed_message.get("action")
        payload = parsed_message.get("payload", {})
        message_id = parsed_message.get("message_id")
        
        if not action:
            logger.warning(f"No action specified in OCPP call from charger {charger_id}")
            return None
        
        # Get command handler
        handler = self.registry.get_handler(action)
        if not handler:
            logger.warning(f"No handler found for action {action} from charger {charger_id}")
            # Return error response
            return self._create_error_response(message_id, OCPPErrorCode.NOT_IMPLEMENTED, 
                                            f"Action {action} not implemented")
        
        try:
            # Process the command
            result = await handler.handle_request(charger_id, payload)
            
            if result.success:
                # Create success response
                return self._create_call_result_response(message_id, result.response or {})
            else:
                # Create error response
                error_code = result.error.code if result.error else OCPPErrorCode.INTERNAL_ERROR
                error_desc = result.error.description if result.error else "Unknown error"
                return self._create_error_response(message_id, error_code, error_desc)
                
        except Exception as e:
            logger.error(f"Error handling OCPP call {action} from charger {charger_id}: {e}")
            return self._create_error_response(message_id, OCPPErrorCode.INTERNAL_ERROR, 
                                            f"Internal error: {str(e)}")
    
    async def _handle_ocpp_call_result(self, charger_id: str, parsed_message: Dict[str, Any]) -> Optional[str]:
        """Handle OCPP CallResult message from charger"""
        # CallResult messages are typically responses to our requests
        # For now, just log them
        logger.info(f"Received CallResult from charger {charger_id}")
        return None
    
    async def _handle_ocpp_call_error(self, charger_id: str, parsed_message: Dict[str, Any]) -> Optional[str]:
        """Handle OCPP CallError message from charger"""
        error_code = parsed_message.get("error_code")
        error_description = parsed_message.get("error_description")
        logger.warning(f"Received CallError from charger {charger_id}: {error_code} - {error_description}")
        return None
    
    def _create_call_result_response(self, message_id: str, payload: Dict[str, Any]) -> str:
        """Create OCPP CallResult response"""
        response = [3, message_id, payload]
        return json.dumps(response)
    
    def _create_error_response(self, message_id: str, error_code: OCPPErrorCode, 
                             error_description: str) -> str:
        """Create OCPP CallError response"""
        response = [4, message_id, error_code.value, error_description, {}]
        return json.dumps(response)
    
    def get_supported_commands(self) -> List[str]:
        """Get list of supported OCPP commands"""
        return list(self.registry.list_handlers().keys())
    
    def get_commands_by_profile(self, profile: str) -> List[str]:
        """Get commands for a specific profile"""
        return self.registry.get_profile_actions(profile)
