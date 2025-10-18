"""
OCPP 1.6 Base Message Schemas

Defines the fundamental OCPP message types and structures
used throughout the protocol.
"""

from typing import Any, Dict, List, Optional, Union, Literal
from pydantic import BaseModel, Field, field_validator
from enum import Enum


class OCPPMessageType(Enum):
    """OCPP Message Types"""
    CALL = 2
    CALL_RESULT = 3
    CALL_ERROR = 4


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


class OCPPMessage(BaseModel):
    """Base OCPP message"""
    message_type: int = Field(..., description="OCPP message type (2, 3, or 4)")
    message_id: str = Field(..., description="Unique message identifier")
    
    @field_validator('message_type')
    @classmethod
    def validate_message_type(cls, v):
        if v not in [2, 3, 4]:
            raise ValueError('Message type must be 2 (Call), 3 (CallResult), or 4 (CallError)')
        return v


class OCPPRequest(OCPPMessage):
    """Base OCPP request message"""
    action: str = Field(..., description="OCPP action name")
    payload: Dict[str, Any] = Field(..., description="Request payload")
    
    class Config:
        json_schema_extra = {
            "example": {
                "message_type": 2,
                "message_id": "12345",
                "action": "Authorize",
                "payload": {
                    "idTag": "ABCD1234"
                }
            }
        }


class OCPPResponse(OCPPMessage):
    """Base OCPP response message"""
    pass


class OCPPCall(OCPPRequest):
    """OCPP Call message (Type 2)"""
    message_type: Literal[2] = Field(default=2)
    
    class Config:
        json_schema_extra = {
            "example": {
                "message_type": 2,
                "message_id": "12345",
                "action": "Authorize",
                "payload": {
                    "idTag": "ABCD1234"
                }
            }
        }


class OCPPCallResult(OCPPResponse):
    """OCPP CallResult message (Type 3)"""
    message_type: Literal[3] = Field(default=3)
    payload: Dict[str, Any] = Field(..., description="Response payload")
    
    class Config:
        json_schema_extra = {
            "example": {
                "message_type": 3,
                "message_id": "12345",
                "payload": {
                    "idTagInfo": {
                        "status": "Accepted"
                    }
                }
            }
        }


class OCPPCallError(OCPPResponse):
    """OCPP CallError message (Type 4)"""
    message_type: Literal[4] = Field(default=4)
    error_code: OCPPErrorCode = Field(..., description="OCPP error code")
    error_description: str = Field(..., description="Human-readable error description")
    error_details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")
    
    class Config:
        json_schema_extra = {
            "example": {
                "message_type": 4,
                "message_id": "12345",
                "error_code": "NotImplemented",
                "error_description": "The requested action is not implemented",
                "error_details": {}
            }
        }


class OCPPChargingProfile(BaseModel):
    """OCPP Charging Profile"""
    charging_profile_id: int = Field(..., description="Unique identifier for this profile")
    transaction_id: Optional[int] = Field(None, description="Transaction ID if profile applies to specific transaction")
    stack_level: int = Field(..., description="Stack level for this profile")
    charging_profile_purpose: str = Field(..., description="Purpose of this profile")
    charging_profile_kind: str = Field(..., description="Kind of charging profile")
    recurrency_kind: Optional[str] = Field(None, description="Recurrency kind")
    valid_from: Optional[str] = Field(None, description="Profile valid from timestamp")
    valid_to: Optional[str] = Field(None, description="Profile valid to timestamp")
    charging_schedule: Dict[str, Any] = Field(..., description="Charging schedule details")


class OCPPChargingSchedule(BaseModel):
    """OCPP Charging Schedule"""
    duration: Optional[int] = Field(None, description="Duration in seconds")
    start_schedule: Optional[str] = Field(None, description="Start time of schedule")
    charging_rate_unit: str = Field(..., description="Unit of charging rate")
    charging_schedule_period: List[Dict[str, Any]] = Field(..., description="Charging schedule periods")


class OCPPMeterValue(BaseModel):
    """OCPP Meter Value"""
    timestamp: str = Field(..., description="Timestamp of the meter value")
    sampled_value: List[Dict[str, Any]] = Field(..., description="Sampled values")


class OCPPIdTagInfo(BaseModel):
    """OCPP ID Tag Information"""
    status: str = Field(..., description="Authorization status")
    expiry_date: Optional[str] = Field(None, description="Expiry date if applicable")
    parent_id_tag: Optional[str] = Field(None, description="Parent ID tag if applicable")


class OCPPConfigurationKey(BaseModel):
    """OCPP Configuration Key"""
    key: str = Field(..., description="Configuration key name")
    readonly: bool = Field(..., description="Whether key is readonly")
    value: Optional[str] = Field(None, description="Configuration value")


class OCPPKeyValue(BaseModel):
    """OCPP Key-Value pair"""
    key: str = Field(..., description="Key name")
    value: Optional[str] = Field(None, description="Value")
    readonly: bool = Field(default=False, description="Whether key is readonly")
