"""
OCPP Message Validator

Validates OCPP messages according to the OCPP specification.
Supports validation of message format, structure, and basic business rules.
"""

import json
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("ocpp_broker.message_validator")


class ValidationError(Exception):
    """Raised when message validation fails"""
    pass


class OCPPMessageValidator:
    """Validates OCPP messages according to OCPP specification"""
    
    # OCPP message types
    CALL = 2  # Request from Central System to Charge Point
    CALLRESULT = 3  # Response to a CALL
    CALLERROR = 4  # Error response
    
    # Valid OCPP 1.6 actions (Core Profile)
    VALID_ACTIONS = {
        # From Charge Point to Central System
        "Authorize", "BootNotification", "DataTransfer", "DiagnosticsStatusNotification",
        "FirmwareStatusNotification", "Heartbeat", "MeterValues", "StartTransaction",
        "StatusNotification", "StopTransaction",
        # From Central System to Charge Point
        "CancelReservation", "ChangeAvailability", "ChangeConfiguration", "ClearCache",
        "ClearChargingProfile", "GetCompositeSchedule", "GetConfiguration", "GetDiagnostics",
        "GetLocalListVersion", "RemoteStartTransaction", "RemoteStopTransaction", "ReserveNow",
        "Reset", "SendLocalList", "SetChargingProfile", "TriggerMessage", "UnlockConnector",
        "UpdateFirmware"
    }
    
    def __init__(self, strict_mode: bool = True):
        """
        Initialize validator.
        
        Args:
            strict_mode: If True, reject messages that don't strictly conform to OCPP spec
        """
        self.strict_mode = strict_mode
    
    def validate_message(self, message: str) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Validate an OCPP message.
        
        Args:
            message: JSON string containing OCPP message
            
        Returns:
            Tuple of (is_valid, error_message, parsed_message)
            - is_valid: True if message is valid
            - error_message: Error description if invalid, None if valid
            - parsed_message: Parsed message dict/list if valid, None if invalid
        """
        try:
            # Parse JSON
            try:
                parsed = json.loads(message)
            except json.JSONDecodeError as e:
                return False, f"Invalid JSON: {str(e)}", None
            
            # Check if it's a list (OCPP message format)
            if not isinstance(parsed, list):
                return False, "OCPP message must be a JSON array", None
            
            # Check minimum length
            if len(parsed) < 2:
                return False, "OCPP message must have at least 2 elements [MessageType, UniqueID, ...]", None
            
            # Validate message type
            message_type = parsed[0]
            if message_type not in [self.CALL, self.CALLRESULT, self.CALLERROR]:
                return False, f"Invalid message type: {message_type}. Must be 2 (CALL), 3 (CALLRESULT), or 4 (CALLERROR)", None
            
            # Validate unique ID
            unique_id = parsed[1]
            if not isinstance(unique_id, str) and not isinstance(unique_id, (int, float)):
                return False, f"Invalid unique ID type: {type(unique_id).__name__}. Must be string or number", None
            
            # Validate based on message type
            if message_type == self.CALL:
                return self._validate_call(parsed)
            elif message_type == self.CALLRESULT:
                return self._validate_call_result(parsed)
            elif message_type == self.CALLERROR:
                return self._validate_call_error(parsed)
            
            return True, None, parsed
            
        except Exception as e:
            logger.error(f"Error validating message: {e}")
            return False, f"Validation error: {str(e)}", None
    
    def _validate_call(self, parsed: list) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Validate CALL message (Type 2)"""
        if len(parsed) < 4:
            return False, "CALL message must have at least 4 elements [2, UniqueID, Action, Payload]", None
        
        action = parsed[2]
        if not isinstance(action, str):
            return False, "Action must be a string", None
        
        if self.strict_mode and action not in self.VALID_ACTIONS:
            return False, f"Unknown action: {action}", None
        
        payload = parsed[3]
        if not isinstance(payload, dict):
            return False, "Payload must be a JSON object", None
        
        return True, None, {
            "type": "CALL",
            "message_id": parsed[1],
            "action": action,
            "payload": payload
        }
    
    def _validate_call_result(self, parsed: list) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Validate CALLRESULT message (Type 3)"""
        if len(parsed) < 3:
            return False, "CALLRESULT message must have at least 3 elements [3, UniqueID, Payload]", None
        
        payload = parsed[2]
        if not isinstance(payload, dict):
            return False, "Payload must be a JSON object", None
        
        return True, None, {
            "type": "CALLRESULT",
            "message_id": parsed[1],
            "payload": payload
        }
    
    def _validate_call_error(self, parsed: list) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Validate CALLERROR message (Type 4)"""
        if len(parsed) < 4:
            return False, "CALLERROR message must have at least 4 elements [4, UniqueID, ErrorCode, ErrorDescription, ErrorDetails]", None
        
        error_code = parsed[2]
        if not isinstance(error_code, str):
            return False, "Error code must be a string", None
        
        error_description = parsed[3]
        if not isinstance(error_description, str):
            return False, "Error description must be a string", None
        
        error_details = parsed[4] if len(parsed) > 4 else {}
        if not isinstance(error_details, dict):
            return False, "Error details must be a JSON object", None
        
        return True, None, {
            "type": "CALLERROR",
            "message_id": parsed[1],
            "error_code": error_code,
            "error_description": error_description,
            "error_details": error_details
        }


def validate_ocpp_message(message: str, strict_mode: bool = True) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Convenience function to validate an OCPP message.
    
    Args:
        message: JSON string containing OCPP message
        strict_mode: If True, reject messages that don't strictly conform to OCPP spec
        
    Returns:
        Tuple of (is_valid, error_message, parsed_message)
    """
    validator = OCPPMessageValidator(strict_mode=strict_mode)
    return validator.validate_message(message)

