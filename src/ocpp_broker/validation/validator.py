"""
OCPP 1.6 Message Validator

Validates OCPP messages according to the OCPP 1.6 specification,
including message format, payload structure, and business rules.
"""

import json
import logging
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("ocpp_broker.validation")


class ValidationSeverity(Enum):
    """Validation error severity levels"""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationError:
    """Individual validation error"""
    field: str
    message: str
    severity: ValidationSeverity
    code: Optional[str] = None
    value: Optional[Any] = None


@dataclass
class ValidationResult:
    """Result of message validation"""
    valid: bool
    errors: List[ValidationError]
    warnings: List[ValidationError]
    
    def has_errors(self) -> bool:
        """Check if validation has errors"""
        return any(error.severity == ValidationSeverity.ERROR for error in self.errors)
    
    def has_warnings(self) -> bool:
        """Check if validation has warnings"""
        return any(error.severity == ValidationSeverity.WARNING for error in self.warnings)


class OCPPValidator:
    """
    Validates OCPP 1.6 messages according to the specification.
    
    Provides validation for:
    - Message format and structure
    - Payload validation for each command
    - Business rule validation
    - Error reporting
    """
    
    def __init__(self):
        self.logger = logging.getLogger("ocpp_broker.validation.validator")
    
    def validate_message(self, message: str) -> ValidationResult:
        """
        Validate a complete OCPP message.
        
        Args:
            message: Raw OCPP message (JSON string)
            
        Returns:
            ValidationResult with validation status and errors
        """
        errors = []
        warnings = []
        
        try:
            # Parse JSON
            data = json.loads(message)
        except json.JSONDecodeError as e:
            errors.append(ValidationError(
                field="message",
                message=f"Invalid JSON: {str(e)}",
                severity=ValidationSeverity.ERROR,
                code="INVALID_JSON"
            ))
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Validate message structure
        structure_result = self._validate_message_structure(data)
        errors.extend(structure_result.errors)
        warnings.extend(structure_result.warnings)
        
        if not structure_result.valid:
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Validate based on message type
        message_type = data[0]
        if message_type == 2:  # Call
            payload_result = self._validate_call_payload(data)
        elif message_type == 3:  # CallResult
            payload_result = self._validate_call_result_payload(data)
        elif message_type == 4:  # CallError
            payload_result = self._validate_call_error_payload(data)
        else:
            errors.append(ValidationError(
                field="message_type",
                message=f"Invalid message type: {message_type}",
                severity=ValidationSeverity.ERROR,
                code="INVALID_MESSAGE_TYPE"
            ))
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        errors.extend(payload_result.errors)
        warnings.extend(payload_result.warnings)
        
        valid = len([e for e in errors if e.severity == ValidationSeverity.ERROR]) == 0
        return ValidationResult(valid=valid, errors=errors, warnings=warnings)
    
    def _validate_message_structure(self, data: Any) -> ValidationResult:
        """Validate basic OCPP message structure"""
        errors = []
        warnings = []
        
        if not isinstance(data, list):
            errors.append(ValidationError(
                field="message",
                message="OCPP message must be a JSON array",
                severity=ValidationSeverity.ERROR,
                code="INVALID_STRUCTURE"
            ))
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        if len(data) < 3:
            errors.append(ValidationError(
                field="message",
                message="OCPP message must have at least 3 elements",
                severity=ValidationSeverity.ERROR,
                code="INVALID_LENGTH"
            ))
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Validate message type
        message_type = data[0]
        if not isinstance(message_type, int) or message_type not in [2, 3, 4]:
            errors.append(ValidationError(
                field="message_type",
                message=f"Invalid message type: {message_type}. Must be 2, 3, or 4",
                severity=ValidationSeverity.ERROR,
                code="INVALID_MESSAGE_TYPE"
            ))
        
        # Validate message ID
        message_id = data[1]
        if not isinstance(message_id, str) or not message_id:
            errors.append(ValidationError(
                field="message_id",
                message="Message ID must be a non-empty string",
                severity=ValidationSeverity.ERROR,
                code="INVALID_MESSAGE_ID"
            ))
        
        valid = len([e for e in errors if e.severity == ValidationSeverity.ERROR]) == 0
        return ValidationResult(valid=valid, errors=errors, warnings=warnings)
    
    def _validate_call_payload(self, data: List[Any]) -> ValidationResult:
        """Validate OCPP Call payload"""
        errors = []
        warnings = []
        
        if len(data) < 4:
            errors.append(ValidationError(
                field="call",
                message="OCPP Call must have at least 4 elements",
                severity=ValidationSeverity.ERROR,
                code="INVALID_CALL_LENGTH"
            ))
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Validate action
        action = data[2]
        if not isinstance(action, str) or not action:
            errors.append(ValidationError(
                field="action",
                message="Action must be a non-empty string",
                severity=ValidationSeverity.ERROR,
                code="INVALID_ACTION"
            ))
        
        # Validate payload
        payload = data[3]
        if not isinstance(payload, dict):
            errors.append(ValidationError(
                field="payload",
                message="Payload must be a JSON object",
                severity=ValidationSeverity.ERROR,
                code="INVALID_PAYLOAD"
            ))
        else:
            # Validate specific action payload
            if isinstance(action, str):
                action_errors = self._validate_action_payload(action, payload)
                errors.extend(action_errors)
        
        valid = len([e for e in errors if e.severity == ValidationSeverity.ERROR]) == 0
        return ValidationResult(valid=valid, errors=errors, warnings=warnings)
    
    def _validate_call_result_payload(self, data: List[Any]) -> ValidationResult:
        """Validate OCPP CallResult payload"""
        errors = []
        warnings = []
        
        if len(data) < 3:
            errors.append(ValidationError(
                field="call_result",
                message="OCPP CallResult must have at least 3 elements",
                severity=ValidationSeverity.ERROR,
                code="INVALID_CALL_RESULT_LENGTH"
            ))
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Validate payload
        payload = data[2]
        if not isinstance(payload, dict):
            errors.append(ValidationError(
                field="payload",
                message="CallResult payload must be a JSON object",
                severity=ValidationSeverity.ERROR,
                code="INVALID_PAYLOAD"
            ))
        
        valid = len([e for e in errors if e.severity == ValidationSeverity.ERROR]) == 0
        return ValidationResult(valid=valid, errors=errors, warnings=warnings)
    
    def _validate_call_error_payload(self, data: List[Any]) -> ValidationResult:
        """Validate OCPP CallError payload"""
        errors = []
        warnings = []
        
        if len(data) < 5:
            errors.append(ValidationError(
                field="call_error",
                message="OCPP CallError must have at least 5 elements",
                severity=ValidationSeverity.ERROR,
                code="INVALID_CALL_ERROR_LENGTH"
            ))
            return ValidationResult(valid=False, errors=errors, warnings=warnings)
        
        # Validate error code
        error_code = data[2]
        valid_error_codes = [
            "NotImplemented", "NotSupported", "InternalError", "ProtocolError",
            "SecurityError", "FormationViolation", "PropertyConstraintViolation",
            "OccurenceConstraintViolation", "TypeConstraintViolation", "GenericError"
        ]
        if not isinstance(error_code, str) or error_code not in valid_error_codes:
            errors.append(ValidationError(
                field="error_code",
                message=f"Invalid error code: {error_code}",
                severity=ValidationSeverity.ERROR,
                code="INVALID_ERROR_CODE"
            ))
        
        # Validate error description
        error_description = data[3]
        if not isinstance(error_description, str):
            errors.append(ValidationError(
                field="error_description",
                message="Error description must be a string",
                severity=ValidationSeverity.ERROR,
                code="INVALID_ERROR_DESCRIPTION"
            ))
        
        # Validate error details (optional)
        if len(data) > 4:
            error_details = data[4]
            if not isinstance(error_details, dict):
                errors.append(ValidationError(
                    field="error_details",
                    message="Error details must be a JSON object",
                    severity=ValidationSeverity.ERROR,
                    code="INVALID_ERROR_DETAILS"
                ))
        
        valid = len([e for e in errors if e.severity == ValidationSeverity.ERROR]) == 0
        return ValidationResult(valid=valid, errors=errors, warnings=warnings)
    
    def _validate_action_payload(self, action: str, payload: Dict[str, Any]) -> List[ValidationError]:
        """Validate payload for specific OCPP actions"""
        errors = []
        
        # Define required fields for each action
        action_requirements = {
            "Authorize": ["idTag"],
            "BootNotification": ["chargePointModel", "chargePointVendor"],
            "ChangeAvailability": ["connectorId", "type"],
            "ChangeConfiguration": ["key", "value"],
            "ClearCache": [],
            "DataTransfer": ["vendorId"],
            "GetConfiguration": ["key"],
            "Heartbeat": [],
            "MeterValues": ["meterValue"],
            "RemoteStartTransaction": ["idTag"],
            "RemoteStopTransaction": ["transactionId"],
            "Reset": ["type"],
            "SendLocalList": ["listVersion", "localAuthorizationList"],
            "SetChargingProfile": ["connectorId", "csChargingProfiles"],
            "StatusNotification": ["connectorId", "errorCode", "status"],
            "StopTransaction": ["transactionId", "timestamp", "meterStop"],
            "UnlockConnector": ["connectorId"],
            "UpdateFirmware": ["location"]
        }
        
        required_fields = action_requirements.get(action, [])
        
        for field in required_fields:
            if field not in payload:
                errors.append(ValidationError(
                    field=field,
                    message=f"Required field '{field}' missing for action '{action}'",
                    severity=ValidationSeverity.ERROR,
                    code="MISSING_REQUIRED_FIELD"
                ))
        
        # Action-specific validation
        if action == "Authorize":
            errors.extend(self._validate_authorize_payload(payload))
        elif action == "BootNotification":
            errors.extend(self._validate_boot_notification_payload(payload))
        elif action == "StatusNotification":
            errors.extend(self._validate_status_notification_payload(payload))
        # Add more action-specific validations as needed
        
        return errors
    
    def _validate_authorize_payload(self, payload: Dict[str, Any]) -> List[ValidationError]:
        """Validate Authorize payload"""
        errors = []
        
        id_tag = payload.get("idTag")
        if id_tag and not isinstance(id_tag, str):
            errors.append(ValidationError(
                field="idTag",
                message="idTag must be a string",
                severity=ValidationSeverity.ERROR,
                code="INVALID_ID_TAG_TYPE"
            ))
        
        return errors
    
    def _validate_boot_notification_payload(self, payload: Dict[str, Any]) -> List[ValidationError]:
        """Validate BootNotification payload"""
        errors = []
        
        # Validate charge point model
        model = payload.get("chargePointModel")
        if model and not isinstance(model, str):
            errors.append(ValidationError(
                field="chargePointModel",
                message="chargePointModel must be a string",
                severity=ValidationSeverity.ERROR,
                code="INVALID_MODEL_TYPE"
            ))
        
        # Validate charge point vendor
        vendor = payload.get("chargePointVendor")
        if vendor and not isinstance(vendor, str):
            errors.append(ValidationError(
                field="chargePointVendor",
                message="chargePointVendor must be a string",
                severity=ValidationSeverity.ERROR,
                code="INVALID_VENDOR_TYPE"
            ))
        
        return errors
    
    def _validate_status_notification_payload(self, payload: Dict[str, Any]) -> List[ValidationError]:
        """Validate StatusNotification payload"""
        errors = []
        
        # Validate connector ID
        connector_id = payload.get("connectorId")
        if connector_id is not None and not isinstance(connector_id, int):
            errors.append(ValidationError(
                field="connectorId",
                message="connectorId must be an integer",
                severity=ValidationSeverity.ERROR,
                code="INVALID_CONNECTOR_ID_TYPE"
            ))
        
        # Validate status
        status = payload.get("status")
        valid_statuses = ["Available", "Preparing", "Charging", "SuspendedEVSE", 
                         "SuspendedEV", "Finishing", "Reserved", "Unavailable", "Faulted"]
        if status and status not in valid_statuses:
            errors.append(ValidationError(
                field="status",
                message=f"Invalid status: {status}. Must be one of {valid_statuses}",
                severity=ValidationSeverity.ERROR,
                code="INVALID_STATUS"
            ))
        
        return errors
