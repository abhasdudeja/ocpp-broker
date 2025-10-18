"""
Base classes for OCPP 1.6 command handling.

Provides the foundation for all OCPP command handlers with common
functionality for request/response processing, validation, and error handling.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger("ocpp_broker.commands")


class OCPPErrorCode(Enum):
    """OCPP 1.6 Error Codes"""
    NOT_IMPLEMENTED = "NotImplemented"
    NOT_SUPPORTED = "NotSupported"
    INTERNAL_ERROR = "InternalError"
    PROTOCOL_ERROR = "ProtocolError"
    SECURITY_ERROR = "SecurityError"
    FORMATION_VIOLATION = "FormationViolation"
    PROPERTY_CONSTRAINT_VIOLATION = "PropertyConstraintViolation"
    OCCURENCE_CONSTRAINT_VIOLATION = "OccurenceConstraintViolation"
    TYPE_CONSTRAINT_VIOLATION = "TypeConstraintViolation"
    GENERIC_ERROR = "GenericError"


@dataclass
class OCPPError:
    """OCPP Error representation"""
    code: OCPPErrorCode
    description: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class OCPPCommandResult:
    """Result of OCPP command processing"""
    success: bool
    response: Optional[Dict[str, Any]] = None
    error: Optional[OCPPError] = None
    metadata: Optional[Dict[str, Any]] = None


class BaseCommandHandler(ABC):
    """
    Base class for all OCPP command handlers.
    
    Provides common functionality for:
    - Request validation
    - Response generation
    - Error handling
    - Logging
    """
    
    def __init__(self, broker=None):
        self.broker = broker
        self.logger = logging.getLogger(f"ocpp_broker.commands.{self.__class__.__name__}")
    
    @abstractmethod
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """
        Handle an incoming OCPP request from a charger.
        
        Args:
            charger_id: ID of the charger sending the request
            request: OCPP request payload
            
        Returns:
            OCPPCommandResult with success status and response/error
        """
        pass
    
    @abstractmethod
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """
        Handle a response from a charger for a previously sent request.
        
        Args:
            charger_id: ID of the charger sending the response
            response: OCPP response payload
            
        Returns:
            OCPPCommandResult with processing status
        """
        pass
    
    def validate_request(self, request: Dict[str, Any]) -> Optional[OCPPError]:
        """
        Validate an OCPP request payload.
        
        Args:
            request: Request payload to validate
            
        Returns:
            OCPPError if validation fails, None if valid
        """
        # Base validation - can be overridden by subclasses
        if not isinstance(request, dict):
            return OCPPError(
                code=OCPPErrorCode.FORMATION_VIOLATION,
                description="Request must be a JSON object"
            )
        return None
    
    def create_error_response(self, error: OCPPError, message_id: str) -> Dict[str, Any]:
        """
        Create an OCPP error response.
        
        Args:
            error: OCPP error details
            message_id: Original message ID
            
        Returns:
            OCPP error response payload
        """
        return {
            "errorCode": error.code.value,
            "errorDescription": error.description,
            "errorDetails": error.details or {}
        }
    
    def create_success_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create an OCPP success response.
        
        Args:
            payload: Response payload
            
        Returns:
            OCPP success response payload
        """
        return payload
    
    def log_command(self, charger_id: str, action: str, direction: str, 
                   success: bool, details: Optional[str] = None):
        """
        Log OCPP command activity.
        
        Args:
            charger_id: ID of the charger
            action: OCPP action name
            direction: "request" or "response"
            success: Whether the operation was successful
            details: Additional details to log
        """
        status = "✅" if success else "❌"
        direction_arrow = "→" if direction == "request" else "←"
        
        log_msg = f"[{charger_id}] {direction_arrow} {action} {direction}"
        if details:
            log_msg += f" - {details}"
        
        if success:
            self.logger.info(f"{status} {log_msg}")
        else:
            self.logger.warning(f"{status} {log_msg}")


class OCPPCommandRegistry:
    """
    Registry for OCPP command handlers.
    
    Manages registration and lookup of command handlers
    for different OCPP actions.
    """
    
    def __init__(self):
        self._handlers: Dict[str, BaseCommandHandler] = {}
        self._profiles: Dict[str, List[str]] = {}
        self.logger = logging.getLogger("ocpp_broker.commands.registry")
    
    def register_handler(self, action: str, handler: BaseCommandHandler, profile: str = "core"):
        """
        Register a command handler for an OCPP action.
        
        Args:
            action: OCPP action name (e.g., "Authorize", "BootNotification")
            handler: Command handler instance
            profile: OCPP profile name (e.g., "core", "smart_charging")
        """
        self._handlers[action] = handler
        if profile not in self._profiles:
            self._profiles[profile] = []
        if action not in self._profiles[profile]:
            self._profiles[profile].append(action)
        
        self.logger.info(f"Registered handler for {action} in {profile} profile")
    
    def get_handler(self, action: str) -> Optional[BaseCommandHandler]:
        """
        Get command handler for an OCPP action.
        
        Args:
            action: OCPP action name
            
        Returns:
            Command handler instance or None if not found
        """
        return self._handlers.get(action)
    
    def get_profile_actions(self, profile: str) -> List[str]:
        """
        Get all actions for a specific profile.
        
        Args:
            profile: OCPP profile name
            
        Returns:
            List of action names in the profile
        """
        return self._profiles.get(profile, [])
    
    def list_handlers(self) -> Dict[str, str]:
        """
        List all registered handlers.
        
        Returns:
            Dictionary mapping action names to profile names
        """
        result = {}
        for profile, actions in self._profiles.items():
            for action in actions:
                result[action] = profile
        return result
