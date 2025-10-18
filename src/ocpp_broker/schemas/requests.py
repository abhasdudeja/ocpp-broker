"""
OCPP 1.6 Request Schemas

Pydantic models for all OCPP 1.6 request payloads,
ensuring proper validation and type safety.
"""

from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, field_validator
from datetime import datetime


# Core Profile Requests

class AuthorizeRequest(BaseModel):
    """Authorize request payload"""
    id_tag: str = Field(..., description="ID tag to authorize")
    
    class Config:
        json_schema_extra = {
            "example": {
                "idTag": "ABCD1234"
            }
        }


class BootNotificationRequest(BaseModel):
    """BootNotification request payload"""
    charge_point_model: str = Field(..., description="Charge point model")
    charge_point_vendor: str = Field(..., description="Charge point vendor")
    charge_point_serial_number: Optional[str] = Field(None, description="Charge point serial number")
    charge_box_serial_number: Optional[str] = Field(None, description="Charge box serial number")
    firmware_version: Optional[str] = Field(None, description="Firmware version")
    iccid: Optional[str] = Field(None, description="ICCID")
    imsi: Optional[str] = Field(None, description="IMSI")
    meter_type: Optional[str] = Field(None, description="Meter type")
    meter_serial_number: Optional[str] = Field(None, description="Meter serial number")
    
    class Config:
        json_schema_extra = {
            "example": {
                "chargePointModel": "SingleSocketCharger",
                "chargePointVendor": "VendorX",
                "chargePointSerialNumber": "CP001",
                "firmwareVersion": "1.0.0"
            }
        }


class ChangeAvailabilityRequest(BaseModel):
    """ChangeAvailability request payload"""
    connector_id: int = Field(..., description="Connector ID (0 for entire charge point)")
    type: str = Field(..., description="Availability type")
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v):
        valid_types = ["Operative", "Inoperative"]
        if v not in valid_types:
            raise ValueError(f"Type must be one of {valid_types}")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "connectorId": 1,
                "type": "Operative"
            }
        }


class ChangeConfigurationRequest(BaseModel):
    """ChangeConfiguration request payload"""
    key: str = Field(..., description="Configuration key")
    value: str = Field(..., description="Configuration value")
    
    class Config:
        json_schema_extra = {
            "example": {
                "key": "HeartbeatInterval",
                "value": "300"
            }
        }


class ClearCacheRequest(BaseModel):
    """ClearCache request payload"""
    # No fields required for ClearCache
    pass


class DataTransferRequest(BaseModel):
    """DataTransfer request payload"""
    vendor_id: str = Field(..., description="Vendor ID")
    message_id: Optional[str] = Field(None, description="Message ID")
    data: Optional[str] = Field(None, description="Data payload")
    
    class Config:
        json_schema_extra = {
            "example": {
                "vendorId": "VendorX",
                "messageId": "MSG001",
                "data": "Custom data"
            }
        }


class GetConfigurationRequest(BaseModel):
    """GetConfiguration request payload"""
    key: Optional[List[str]] = Field(None, description="Configuration keys to retrieve")
    
    class Config:
        json_schema_extra = {
            "example": {
                "key": ["HeartbeatInterval", "MeterValueSampleInterval"]
            }
        }


class HeartbeatRequest(BaseModel):
    """Heartbeat request payload"""
    # No fields required for Heartbeat
    pass


class MeterValuesRequest(BaseModel):
    """MeterValues request payload"""
    connector_id: int = Field(..., description="Connector ID")
    transaction_id: Optional[int] = Field(None, description="Transaction ID")
    meter_value: List[Dict[str, Any]] = Field(..., description="Meter values")
    
    class Config:
        json_schema_extra = {
            "example": {
                "connectorId": 1,
                "transactionId": 12345,
                "meterValue": [
                    {
                        "timestamp": "2023-01-01T12:00:00Z",
                        "sampledValue": [
                            {
                                "value": "100.5",
                                "context": "Sample.Periodic",
                                "format": "Raw",
                                "measurand": "Energy.Active.Import.Register",
                                "phase": "L1",
                                "location": "Outlet",
                                "unit": "Wh"
                            }
                        ]
                    }
                ]
            }
        }


class RemoteStartTransactionRequest(BaseModel):
    """RemoteStartTransaction request payload"""
    id_tag: str = Field(..., description="ID tag for authorization")
    connector_id: Optional[int] = Field(None, description="Connector ID")
    charging_profile: Optional[Dict[str, Any]] = Field(None, description="Charging profile")
    
    class Config:
        json_schema_extra = {
            "example": {
                "idTag": "ABCD1234",
                "connectorId": 1
            }
        }


class RemoteStopTransactionRequest(BaseModel):
    """RemoteStopTransaction request payload"""
    transaction_id: int = Field(..., description="Transaction ID to stop")
    
    class Config:
        json_schema_extra = {
            "example": {
                "transactionId": 12345
            }
        }


class ResetRequest(BaseModel):
    """Reset request payload"""
    type: str = Field(..., description="Reset type")
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v):
        valid_types = ["Hard", "Soft"]
        if v not in valid_types:
            raise ValueError(f"Type must be one of {valid_types}")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "type": "Hard"
            }
        }


class SendLocalListRequest(BaseModel):
    """SendLocalList request payload"""
    list_version: int = Field(..., description="List version")
    update_type: str = Field(..., description="Update type")
    local_authorization_list: Optional[List[Dict[str, Any]]] = Field(None, description="Local authorization list")
    
    @field_validator('update_type')
    @classmethod
    def validate_update_type(cls, v):
        valid_types = ["Differential", "Full"]
        if v not in valid_types:
            raise ValueError(f"Update type must be one of {valid_types}")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "listVersion": 1,
                "updateType": "Full",
                "localAuthorizationList": [
                    {
                        "idTag": "ABCD1234",
                        "idTagInfo": {
                            "status": "Accepted"
                        }
                    }
                ]
            }
        }


class SetChargingProfileRequest(BaseModel):
    """SetChargingProfile request payload"""
    connector_id: int = Field(..., description="Connector ID")
    cs_charging_profiles: Dict[str, Any] = Field(..., description="CS charging profiles")
    
    class Config:
        json_schema_extra = {
            "example": {
                "connectorId": 1,
                "csChargingProfiles": {
                    "chargingProfileId": 1,
                    "stackLevel": 0,
                    "chargingProfilePurpose": "TxProfile",
                    "chargingProfileKind": "Absolute",
                    "chargingSchedule": {
                        "chargingRateUnit": "W",
                        "chargingSchedulePeriod": [
                            {
                                "startPeriod": 0,
                                "limit": 10000
                            }
                        ]
                    }
                }
            }
        }


class StatusNotificationRequest(BaseModel):
    """StatusNotification request payload"""
    connector_id: int = Field(..., description="Connector ID")
    error_code: str = Field(..., description="Error code")
    info: Optional[str] = Field(None, description="Additional info")
    status: str = Field(..., description="Status")
    timestamp: Optional[str] = Field(None, description="Timestamp")
    vendor_id: Optional[str] = Field(None, description="Vendor ID")
    vendor_error_code: Optional[str] = Field(None, description="Vendor error code")
    
    @field_validator('error_code')
    @classmethod
    def validate_error_code(cls, v):
        valid_codes = ["ConnectorLockFailure", "EVCommunicationError", "GroundFailure",
                      "HighTemperature", "InternalError", "LocalListConflict", "NoError",
                      "OtherError", "OverCurrentFailure", "PowerMeterFailure", "PowerSwitchFailure",
                      "ReaderFailure", "ResetFailure", "UnderVoltage", "VoltageFailure"]
        if v not in valid_codes:
            raise ValueError(f"Error code must be one of {valid_codes}")
        return v
    
    @field_validator('status')
    @classmethod
    def validate_status(cls, v):
        valid_statuses = ["Available", "Preparing", "Charging", "SuspendedEVSE",
                         "SuspendedEV", "Finishing", "Reserved", "Unavailable", "Faulted"]
        if v not in valid_statuses:
            raise ValueError(f"Status must be one of {valid_statuses}")
        return v
    
    class Config:
        json_schema_extra = {
            "example": {
                "connectorId": 1,
                "errorCode": "NoError",
                "status": "Available",
                "timestamp": "2023-01-01T12:00:00Z"
            }
        }


class StartTransactionRequest(BaseModel):
    """StartTransaction request payload"""
    connector_id: int = Field(..., description="Connector ID")
    id_tag: str = Field(..., description="ID tag")
    meter_start: int = Field(..., description="Meter start value")
    reservation_id: Optional[int] = Field(None, description="Reservation ID")
    timestamp: str = Field(..., description="Timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "connectorId": 1,
                "idTag": "ABCD1234",
                "meterStart": 1000,
                "timestamp": "2023-01-01T12:00:00Z"
            }
        }


class StopTransactionRequest(BaseModel):
    """StopTransaction request payload"""
    transaction_id: int = Field(..., description="Transaction ID")
    timestamp: str = Field(..., description="Timestamp")
    meter_stop: int = Field(..., description="Meter stop value")
    reason: Optional[str] = Field(None, description="Stop reason")
    id_tag: Optional[str] = Field(None, description="ID tag")
    transaction_data: Optional[List[Dict[str, Any]]] = Field(None, description="Transaction data")
    
    class Config:
        json_schema_extra = {
            "example": {
                "transactionId": 12345,
                "timestamp": "2023-01-01T12:30:00Z",
                "meterStop": 2000,
                "reason": "EVDisconnected"
            }
        }


class UnlockConnectorRequest(BaseModel):
    """UnlockConnector request payload"""
    connector_id: int = Field(..., description="Connector ID")
    
    class Config:
        json_schema_extra = {
            "example": {
                "connectorId": 1
            }
        }


class UpdateFirmwareRequest(BaseModel):
    """UpdateFirmware request payload"""
    location: str = Field(..., description="Firmware location URL")
    retrieve_date: str = Field(..., description="Retrieve date")
    retry_interval: Optional[int] = Field(None, description="Retry interval in seconds")
    retry_count: Optional[int] = Field(None, description="Retry count")
    
    class Config:
        json_schema_extra = {
            "example": {
                "location": "https://example.com/firmware.bin",
                "retrieveDate": "2023-01-01T12:00:00Z",
                "retryInterval": 300,
                "retryCount": 3
            }
        }


# Smart Charging Profile Requests

class ClearChargingProfileRequest(BaseModel):
    """ClearChargingProfile request payload"""
    id: Optional[int] = Field(None, description="Charging profile ID")
    connector_id: Optional[int] = Field(None, description="Connector ID")
    charging_profile_purpose: Optional[str] = Field(None, description="Charging profile purpose")
    stack_level: Optional[int] = Field(None, description="Stack level")


class GetCompositeScheduleRequest(BaseModel):
    """GetCompositeSchedule request payload"""
    connector_id: int = Field(..., description="Connector ID")
    duration: int = Field(..., description="Duration in seconds")
    charging_rate_unit: Optional[str] = Field(None, description="Charging rate unit")


class TriggerMessageRequest(BaseModel):
    """TriggerMessage request payload"""
    requested_message: str = Field(..., description="Requested message type")
    connector_id: Optional[int] = Field(None, description="Connector ID")


# Firmware Management Profile Requests

class GetDiagnosticsRequest(BaseModel):
    """GetDiagnostics request payload"""
    location: str = Field(..., description="Diagnostics location URL")
    start_time: Optional[str] = Field(None, description="Start time")
    stop_time: Optional[str] = Field(None, description="Stop time")


# Local Authorization List Profile Requests

class GetLocalListVersionRequest(BaseModel):
    """GetLocalListVersion request payload"""
    # No fields required for GetLocalListVersion
    pass


# Reservation Profile Requests

class CancelReservationRequest(BaseModel):
    """CancelReservation request payload"""
    reservation_id: int = Field(..., description="Reservation ID")


class ReserveNowRequest(BaseModel):
    """ReserveNow request payload"""
    connector_id: int = Field(..., description="Connector ID")
    expiry_date: str = Field(..., description="Expiry date")
    id_tag: str = Field(..., description="ID tag")
    parent_id_tag: Optional[str] = Field(None, description="Parent ID tag")
    reservation_id: int = Field(..., description="Reservation ID")
