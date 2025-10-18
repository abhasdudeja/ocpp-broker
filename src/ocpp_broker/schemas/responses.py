"""
OCPP 1.6 Response Schemas

Pydantic models for all OCPP 1.6 response payloads,
ensuring proper validation and type safety.
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


# Core Profile Responses

class AuthorizeResponse(BaseModel):
    """Authorize response payload"""
    id_tag_info: Dict[str, Any] = Field(..., description="ID tag information")
    
    class Config:
        json_schema_extra = {
            "example": {
                "idTagInfo": {
                    "status": "Accepted",
                    "expiryDate": "2023-12-31T23:59:59Z",
                    "parentIdTag": "PARENT123"
                }
            }
        }


class BootNotificationResponse(BaseModel):
    """BootNotification response payload"""
    current_time: str = Field(..., description="Current time")
    interval: int = Field(..., description="Heartbeat interval in seconds")
    status: str = Field(..., description="Registration status")
    
    @classmethod
    def create_accepted(cls, interval: int = 300) -> 'BootNotificationResponse':
        """Create accepted boot notification response"""
        return cls(
            current_time=datetime.utcnow().isoformat() + "Z",
            interval=interval,
            status="Accepted"
        )
    
    class Config:
        json_schema_extra = {
            "example": {
                "currentTime": "2023-01-01T12:00:00Z",
                "interval": 300,
                "status": "Accepted"
            }
        }


class ChangeAvailabilityResponse(BaseModel):
    """ChangeAvailability response payload"""
    status: str = Field(..., description="Availability change status")
    
    @classmethod
    def create_accepted(cls) -> 'ChangeAvailabilityResponse':
        """Create accepted change availability response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


class ChangeConfigurationResponse(BaseModel):
    """ChangeConfiguration response payload"""
    status: str = Field(..., description="Configuration change status")
    
    @classmethod
    def create_accepted(cls) -> 'ChangeConfigurationResponse':
        """Create accepted change configuration response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


class ClearCacheResponse(BaseModel):
    """ClearCache response payload"""
    status: str = Field(..., description="Cache clear status")
    
    @classmethod
    def create_accepted(cls) -> 'ClearCacheResponse':
        """Create accepted clear cache response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


class DataTransferResponse(BaseModel):
    """DataTransfer response payload"""
    status: str = Field(..., description="Data transfer status")
    data: Optional[str] = Field(None, description="Response data")
    
    @classmethod
    def create_accepted(cls, data: Optional[str] = None) -> 'DataTransferResponse':
        """Create accepted data transfer response"""
        return cls(status="Accepted", data=data)
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted",
                "data": "Response data"
            }
        }


class GetConfigurationResponse(BaseModel):
    """GetConfiguration response payload"""
    configuration_key: List[Dict[str, Any]] = Field(..., description="Configuration keys")
    unknown_key: Optional[List[str]] = Field(None, description="Unknown keys")
    
    class Config:
        json_schema_extra = {
            "example": {
                "configurationKey": [
                    {
                        "key": "HeartbeatInterval",
                        "readonly": False,
                        "value": "300"
                    }
                ],
                "unknownKey": []
            }
        }


class HeartbeatResponse(BaseModel):
    """Heartbeat response payload"""
    current_time: str = Field(..., description="Current time")
    
    @classmethod
    def create_current_time(cls) -> 'HeartbeatResponse':
        """Create heartbeat response with current time"""
        return cls(current_time=datetime.utcnow().isoformat() + "Z")
    
    class Config:
        json_schema_extra = {
            "example": {
                "currentTime": "2023-01-01T12:00:00Z"
            }
        }


class MeterValuesResponse(BaseModel):
    """MeterValues response payload"""
    # MeterValues has no response payload
    pass


class RemoteStartTransactionResponse(BaseModel):
    """RemoteStartTransaction response payload"""
    status: str = Field(..., description="Remote start status")
    
    @classmethod
    def create_accepted(cls) -> 'RemoteStartTransactionResponse':
        """Create accepted remote start transaction response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


class RemoteStopTransactionResponse(BaseModel):
    """RemoteStopTransaction response payload"""
    status: str = Field(..., description="Remote stop status")
    
    @classmethod
    def create_accepted(cls) -> 'RemoteStopTransactionResponse':
        """Create accepted remote stop transaction response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


class ResetResponse(BaseModel):
    """Reset response payload"""
    status: str = Field(..., description="Reset status")
    
    @classmethod
    def create_accepted(cls) -> 'ResetResponse':
        """Create accepted reset response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


class SendLocalListResponse(BaseModel):
    """SendLocalList response payload"""
    status: str = Field(..., description="Local list update status")
    
    @classmethod
    def create_accepted(cls) -> 'SendLocalListResponse':
        """Create accepted send local list response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


class SetChargingProfileResponse(BaseModel):
    """SetChargingProfile response payload"""
    status: str = Field(..., description="Charging profile set status")
    
    @classmethod
    def create_accepted(cls) -> 'SetChargingProfileResponse':
        """Create accepted set charging profile response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


class StatusNotificationResponse(BaseModel):
    """StatusNotification response payload"""
    # StatusNotification has no response payload
    pass


class StartTransactionResponse(BaseModel):
    """StartTransaction response payload"""
    transaction_id: int = Field(..., description="Transaction ID")
    id_tag_info: Dict[str, Any] = Field(..., description="ID tag information")
    
    class Config:
        json_schema_extra = {
            "example": {
                "transactionId": 12345,
                "idTagInfo": {
                    "status": "Accepted"
                }
            }
        }


class StopTransactionResponse(BaseModel):
    """StopTransaction response payload"""
    id_tag_info: Optional[Dict[str, Any]] = Field(None, description="ID tag information")
    
    class Config:
        json_schema_extra = {
            "example": {
                "idTagInfo": {
                    "status": "Accepted"
                }
            }
        }


class UnlockConnectorResponse(BaseModel):
    """UnlockConnector response payload"""
    status: str = Field(..., description="Unlock status")
    
    @classmethod
    def create_unlocked(cls) -> 'UnlockConnectorResponse':
        """Create unlocked connector response"""
        return cls(status="Unlocked")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Unlocked"
            }
        }


class UpdateFirmwareResponse(BaseModel):
    """UpdateFirmware response payload"""
    status: str = Field(..., description="Firmware update status")
    
    @classmethod
    def create_accepted(cls) -> 'UpdateFirmwareResponse':
        """Create accepted update firmware response"""
        return cls(status="Accepted")
    
    class Config:
        json_schema_extra = {
            "example": {
                "status": "Accepted"
            }
        }


# Smart Charging Profile Responses

class ClearChargingProfileResponse(BaseModel):
    """ClearChargingProfile response payload"""
    status: str = Field(..., description="Clear charging profile status")
    
    @classmethod
    def create_accepted(cls) -> 'ClearChargingProfileResponse':
        """Create accepted clear charging profile response"""
        return cls(status="Accepted")


class GetCompositeScheduleResponse(BaseModel):
    """GetCompositeSchedule response payload"""
    status: str = Field(..., description="Composite schedule status")
    connector_id: Optional[int] = Field(None, description="Connector ID")
    schedule_start: Optional[str] = Field(None, description="Schedule start time")
    charging_schedule: Optional[Dict[str, Any]] = Field(None, description="Charging schedule")


class TriggerMessageResponse(BaseModel):
    """TriggerMessage response payload"""
    status: str = Field(..., description="Trigger message status")
    
    @classmethod
    def create_accepted(cls) -> 'TriggerMessageResponse':
        """Create accepted trigger message response"""
        return cls(status="Accepted")


# Firmware Management Profile Responses

class GetDiagnosticsResponse(BaseModel):
    """GetDiagnostics response payload"""
    status: str = Field(..., description="Diagnostics status")
    
    @classmethod
    def create_accepted(cls) -> 'GetDiagnosticsResponse':
        """Create accepted get diagnostics response"""
        return cls(status="Accepted")


# Local Authorization List Profile Responses

class GetLocalListVersionResponse(BaseModel):
    """GetLocalListVersion response payload"""
    list_version: int = Field(..., description="Local list version")
    
    class Config:
        json_schema_extra = {
            "example": {
                "listVersion": 1
            }
        }


# Reservation Profile Responses

class CancelReservationResponse(BaseModel):
    """CancelReservation response payload"""
    status: str = Field(..., description="Cancel reservation status")
    
    @classmethod
    def create_accepted(cls) -> 'CancelReservationResponse':
        """Create accepted cancel reservation response"""
        return cls(status="Accepted")


class ReserveNowResponse(BaseModel):
    """ReserveNow response payload"""
    status: str = Field(..., description="Reservation status")
    
    @classmethod
    def create_accepted(cls) -> 'ReserveNowResponse':
        """Create accepted reserve now response"""
        return cls(status="Accepted")
