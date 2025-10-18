"""
OCPP 1.6 Message Schemas

This module provides Pydantic models for all OCPP 1.6 message types,
ensuring proper validation and serialization of OCPP communications.
"""

from .messages import (
    OCPPMessage,
    OCPPRequest,
    OCPPResponse,
    OCPPCall,
    OCPPCallResult,
    OCPPCallError
)
from .requests import (
    # Core Profile Requests
    AuthorizeRequest,
    BootNotificationRequest,
    ChangeAvailabilityRequest,
    ChangeConfigurationRequest,
    ClearCacheRequest,
    DataTransferRequest,
    GetConfigurationRequest,
    HeartbeatRequest,
    MeterValuesRequest,
    RemoteStartTransactionRequest,
    RemoteStopTransactionRequest,
    ResetRequest,
    SendLocalListRequest,
    SetChargingProfileRequest,
    StatusNotificationRequest,
    StopTransactionRequest,
    UnlockConnectorRequest,
    UpdateFirmwareRequest,
    
    # Smart Charging Profile Requests
    ClearChargingProfileRequest,
    GetCompositeScheduleRequest,
    TriggerMessageRequest,
    
    # Firmware Management Profile Requests
    GetDiagnosticsRequest,
    
    # Local Authorization List Profile Requests
    GetLocalListVersionRequest,
    
    # Reservation Profile Requests
    CancelReservationRequest,
    ReserveNowRequest
)
from .responses import (
    # Core Profile Responses
    AuthorizeResponse,
    BootNotificationResponse,
    ChangeAvailabilityResponse,
    ChangeConfigurationResponse,
    ClearCacheResponse,
    DataTransferResponse,
    GetConfigurationResponse,
    HeartbeatResponse,
    MeterValuesResponse,
    RemoteStartTransactionResponse,
    RemoteStopTransactionResponse,
    ResetResponse,
    SendLocalListResponse,
    SetChargingProfileResponse,
    StatusNotificationResponse,
    StopTransactionResponse,
    UnlockConnectorResponse,
    UpdateFirmwareResponse,
    
    # Smart Charging Profile Responses
    ClearChargingProfileResponse,
    GetCompositeScheduleResponse,
    TriggerMessageResponse,
    
    # Firmware Management Profile Responses
    GetDiagnosticsResponse,
    
    # Local Authorization List Profile Responses
    GetLocalListVersionResponse,
    
    # Reservation Profile Responses
    CancelReservationResponse,
    ReserveNowResponse
)

__all__ = [
    # Base message types
    'OCPPMessage',
    'OCPPRequest',
    'OCPPResponse',
    'OCPPCall',
    'OCPPCallResult',
    'OCPPCallError',
    
    # Request schemas
    'AuthorizeRequest',
    'BootNotificationRequest',
    'ChangeAvailabilityRequest',
    'ChangeConfigurationRequest',
    'ClearCacheRequest',
    'DataTransferRequest',
    'GetConfigurationRequest',
    'HeartbeatRequest',
    'MeterValuesRequest',
    'RemoteStartTransactionRequest',
    'RemoteStopTransactionRequest',
    'ResetRequest',
    'SendLocalListRequest',
    'SetChargingProfileRequest',
    'StatusNotificationRequest',
    'StopTransactionRequest',
    'UnlockConnectorRequest',
    'UpdateFirmwareRequest',
    'ClearChargingProfileRequest',
    'GetCompositeScheduleRequest',
    'TriggerMessageRequest',
    'GetDiagnosticsRequest',
    'GetLocalListVersionRequest',
    'CancelReservationRequest',
    'ReserveNowRequest',
    
    # Response schemas
    'AuthorizeResponse',
    'BootNotificationResponse',
    'ChangeAvailabilityResponse',
    'ChangeConfigurationResponse',
    'ClearCacheResponse',
    'DataTransferResponse',
    'GetConfigurationResponse',
    'HeartbeatResponse',
    'MeterValuesResponse',
    'RemoteStartTransactionResponse',
    'RemoteStopTransactionResponse',
    'ResetResponse',
    'SendLocalListResponse',
    'SetChargingProfileResponse',
    'StatusNotificationResponse',
    'StopTransactionResponse',
    'UnlockConnectorResponse',
    'UpdateFirmwareResponse',
    'ClearChargingProfileResponse',
    'GetCompositeScheduleResponse',
    'TriggerMessageResponse',
    'GetDiagnosticsResponse',
    'GetLocalListVersionResponse',
    'CancelReservationResponse',
    'ReserveNowResponse'
]
