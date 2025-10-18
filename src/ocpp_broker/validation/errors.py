"""
OCPP 1.6 Validation Errors

Defines OCPP-specific error types and error codes for validation failures.
"""

from enum import Enum
from typing import Optional, Dict, Any


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


class OCPPValidationError(Exception):
    """OCPP-specific validation error"""
    
    def __init__(self, message: str, error_code: OCPPErrorCode, 
                 field: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.error_code = error_code
        self.field = field
        self.details = details or {}
        super().__init__(message)
    
    def to_ocpp_error(self) -> Dict[str, Any]:
        """Convert to OCPP error format"""
        return {
            "errorCode": self.error_code.value,
            "errorDescription": self.message,
            "errorDetails": self.details
        }


class OCPPFormationError(OCPPValidationError):
    """OCPP message formation error"""
    
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(
            message=message,
            error_code=OCPPErrorCode.FORMATION_VIOLATION,
            field=field
        )


class OCPPPropertyConstraintError(OCPPValidationError):
    """OCPP property constraint violation error"""
    
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(
            message=message,
            error_code=OCPPErrorCode.PROPERTY_CONSTRAINT_VIOLATION,
            field=field
        )


class OCPPTypeConstraintError(OCPPValidationError):
    """OCPP type constraint violation error"""
    
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(
            message=message,
            error_code=OCPPErrorCode.TYPE_CONSTRAINT_VIOLATION,
            field=field
        )


class OCPPOccurenceConstraintError(OCPPValidationError):
    """OCPP occurrence constraint violation error"""
    
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(
            message=message,
            error_code=OCPPErrorCode.OCCURENCE_CONSTRAINT_VIOLATION,
            field=field
        )
