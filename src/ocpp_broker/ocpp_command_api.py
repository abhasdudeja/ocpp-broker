"""
OCPP Command API

REST API endpoints for sending OCPP commands to chargers.
Supports all OCPP 1.6 commands from Central System to Charge Point.
"""

import json
import logging
import uuid
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Path, Body, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("ocpp_broker.ocpp_command_api")


class OCPPCommandRequest(BaseModel):
    """Generic OCPP command request"""
    action: str = Field(..., description="OCPP action name")
    payload: Dict[str, Any] = Field(..., description="Command payload")
    timeout: Optional[int] = Field(30, description="Response timeout in seconds", ge=1, le=300)


class OCPPCommandResponse(BaseModel):
    """OCPP command response"""
    message_id: str
    charger_id: str
    action: str
    status: str  # "sent", "timeout", "error", "success"
    response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: str


# Core Profile Commands
class ChangeAvailabilityRequest(BaseModel):
    connector_id: int = Field(..., description="Connector ID (0 for entire charge point)")
    type: str = Field(..., description="Availability type: Inoperative or Operative")


class ChangeConfigurationRequest(BaseModel):
    key: str = Field(..., description="Configuration key")
    value: str = Field(..., description="Configuration value")


class ClearCacheRequest(BaseModel):
    pass  # No parameters


class DataTransferRequest(BaseModel):
    vendor_id: str = Field(..., description="Vendor identifier")
    message_id: Optional[str] = Field(None, description="Message identifier")
    data: Optional[str] = Field(None, description="Data to transfer")


class GetConfigurationRequest(BaseModel):
    key: Optional[list[str]] = Field(None, description="List of configuration keys (empty for all)")


class RemoteStartTransactionRequest(BaseModel):
    id_tag: str = Field(..., description="Authorization tag")
    connector_id: Optional[int] = Field(None, description="Connector ID")
    charging_profile: Optional[Dict[str, Any]] = Field(None, description="Charging profile")


class RemoteStopTransactionRequest(BaseModel):
    transaction_id: int = Field(..., description="Transaction ID to stop")


class ResetRequest(BaseModel):
    type: str = Field(..., description="Reset type: Hard or Soft")


class SendLocalListRequest(BaseModel):
    list_version: int = Field(..., description="Version number of the list")
    local_authorization_list: Optional[list[Dict[str, Any]]] = Field(None, description="List of authorization entries")
    update_type: str = Field(..., description="Update type: Full or Differential")


class SetChargingProfileRequest(BaseModel):
    connector_id: int = Field(..., description="Connector ID")
    cs_charging_profiles: Dict[str, Any] = Field(..., description="Charging profile")


class UnlockConnectorRequest(BaseModel):
    connector_id: int = Field(..., description="Connector ID to unlock")


class UpdateFirmwareRequest(BaseModel):
    location: str = Field(..., description="URL of firmware location")
    retrieve_date: str = Field(..., description="ISO 8601 date when to retrieve firmware")
    retry_interval: Optional[int] = Field(None, description="Retry interval in seconds")


# Smart Charging Profile Commands
class ClearChargingProfileRequest(BaseModel):
    id: Optional[int] = Field(None, description="Charging profile ID")
    connector_id: Optional[int] = Field(None, description="Connector ID")
    charging_profile_purpose: Optional[str] = Field(None, description="Charging profile purpose")
    stack_level: Optional[int] = Field(None, description="Stack level")


class GetCompositeScheduleRequest(BaseModel):
    connector_id: int = Field(..., description="Connector ID")
    duration: int = Field(..., description="Duration in seconds")
    charging_rate_unit: Optional[str] = Field(None, description="Charging rate unit: W or A")


class TriggerMessageRequest(BaseModel):
    requested_message: str = Field(..., description="Message to trigger")
    connector_id: Optional[int] = Field(None, description="Connector ID (required for some messages)")


# Firmware Management Profile Commands
class GetDiagnosticsRequest(BaseModel):
    location: str = Field(..., description="URL where diagnostics should be uploaded")
    start_time: Optional[str] = Field(None, description="ISO 8601 start time")
    stop_time: Optional[str] = Field(None, description="ISO 8601 stop time")
    retry_interval: Optional[int] = Field(None, description="Retry interval in seconds")
    retries: Optional[int] = Field(None, description="Number of retries")


# Local Authorization List Profile Commands
class GetLocalListVersionRequest(BaseModel):
    pass  # No parameters


# Reservation Profile Commands
class CancelReservationRequest(BaseModel):
    reservation_id: int = Field(..., description="Reservation ID to cancel")


class ReserveNowRequest(BaseModel):
    connector_id: int = Field(..., description="Connector ID")
    expiry_date: str = Field(..., description="ISO 8601 expiry date")
    id_tag: str = Field(..., description="Authorization tag")
    parent_id_tag: Optional[str] = Field(None, description="Parent authorization tag")
    reservation_id: int = Field(..., description="Reservation ID")


def create_ocpp_command_api(broker) -> APIRouter:
    """Create OCPP command API router"""
    router = APIRouter(prefix="/api/ocpp", tags=["OCPP Commands"])
    
    # Store pending command responses
    pending_responses: Dict[str, Dict[str, Any]] = {}
    
    async def send_ocpp_command(org_name: str, charger_id: str, action: str, payload: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
        """Send OCPP command to charger and wait for response"""
        session = broker.sessions.get(charger_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Charger {charger_id} not connected")
        
        # Verify charger belongs to the specified organization
        if session.org_name != org_name:
            raise HTTPException(
                status_code=404, 
                detail=f"Charger {charger_id} does not belong to organization {org_name}"
            )
        
        # Generate unique message ID
        message_id = str(uuid.uuid4())
        
        # Create OCPP message: [MessageType, UniqueID, Action, Payload]
        # Type 2 = CALL (from Central System to Charge Point)
        ocpp_message = [2, message_id, action, payload]
        message_json = json.dumps(ocpp_message)
        
        # Store pending response
        pending_responses[message_id] = {
            "organization": org_name,
            "charger_id": charger_id,
            "action": action,
            "status": "pending",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        try:
            # Send command to charger
            await session.send_to_charger(message_json)
            logger.info(f"Sent OCPP command {action} to charger {org_name}/{charger_id} (message_id: {message_id})")
            
            # Wait for response (simplified - in production, you'd want proper async response handling)
            # For now, we'll return immediately and the response would need to be polled
            # In a real implementation, you'd use asyncio.Event or similar for proper async waiting
            
            return {
                "message_id": message_id,
                "organization": org_name,
                "charger_id": charger_id,
                "action": action,
                "status": "sent",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Error sending OCPP command to {org_name}/{charger_id}: {e}")
            pending_responses.pop(message_id, None)
            raise HTTPException(status_code=500, detail=f"Failed to send command: {str(e)}")
    
    @router.get("/organizations/{org_name}/chargers")
    async def list_chargers(org_name: str = Path(..., description="Organization name")):
        """List all connected chargers for an organization"""
        chargers = []
        for charger_id, session in broker.sessions.items():
            if session.org_name == org_name:
                chargers.append({
                    "charger_id": charger_id,
                    "organization": org_name,
                    "mode": session.mode.value,
                    "connected": True
                })
        return {"organization": org_name, "chargers": chargers}
    
    @router.get("/organizations/{org_name}/chargers/{charger_id}/status")
    async def get_charger_status(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID")
    ):
        """Get status of a specific charger"""
        session = broker.sessions.get(charger_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Charger {charger_id} not connected")
        
        if session.org_name != org_name:
            raise HTTPException(
                status_code=404,
                detail=f"Charger {charger_id} does not belong to organization {org_name}"
            )
        
        return {
            "charger_id": charger_id,
            "organization": org_name,
            "mode": session.mode.value,
            "connected": True,
            "has_backend": session.backend_conn is not None
        }
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands")
    async def send_command(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: OCPPCommandRequest = Body(..., description="OCPP command request")
    ):
        """Send a generic OCPP command to a charger"""
        return await send_ocpp_command(org_name, charger_id, request.action, request.payload, request.timeout)
    
    @router.get("/commands/{message_id}/response")
    async def get_command_response(message_id: str = Path(..., description="Message ID")):
        """Get response for a sent command"""
        response = pending_responses.get(message_id)
        if not response:
            raise HTTPException(status_code=404, detail="Command not found or expired")
        return response
    
    # Core Profile Commands
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/ChangeAvailability")
    async def change_availability(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: ChangeAvailabilityRequest = Body(..., description="Change availability request")
    ):
        """Change availability of a connector or charge point"""
        payload = {
            "connectorId": request.connector_id,
            "type": request.type
        }
        return await send_ocpp_command(org_name, charger_id, "ChangeAvailability", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/ChangeConfiguration")
    async def change_configuration(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: ChangeConfigurationRequest = Body(..., description="Change configuration request")
    ):
        """Change configuration parameter"""
        payload = {
            "key": request.key,
            "value": request.value
        }
        return await send_ocpp_command(org_name, charger_id, "ChangeConfiguration", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/ClearCache")
    async def clear_cache(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID")
    ):
        """Clear authorization cache"""
        return await send_ocpp_command(org_name, charger_id, "ClearCache", {})
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/DataTransfer")
    async def data_transfer(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: DataTransferRequest = Body(..., description="Data transfer request")
    ):
        """Send custom data to charger"""
        payload = {
            "vendorId": request.vendor_id
        }
        if request.message_id:
            payload["messageId"] = request.message_id
        if request.data:
            payload["data"] = request.data
        return await send_ocpp_command(org_name, charger_id, "DataTransfer", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/GetConfiguration")
    async def get_configuration(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: GetConfigurationRequest = Body(..., description="Get configuration request")
    ):
        """Get configuration parameters"""
        payload = {}
        if request.key:
            payload["key"] = request.key
        return await send_ocpp_command(org_name, charger_id, "GetConfiguration", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/RemoteStartTransaction")
    async def remote_start_transaction(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: RemoteStartTransactionRequest = Body(..., description="Remote start transaction request")
    ):
        """Remotely start a transaction"""
        payload = {
            "idTag": request.id_tag
        }
        if request.connector_id is not None:
            payload["connectorId"] = request.connector_id
        if request.charging_profile:
            payload["chargingProfile"] = request.charging_profile
        return await send_ocpp_command(org_name, charger_id, "RemoteStartTransaction", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/RemoteStopTransaction")
    async def remote_stop_transaction(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: RemoteStopTransactionRequest = Body(..., description="Remote stop transaction request")
    ):
        """Remotely stop a transaction"""
        payload = {
            "transactionId": request.transaction_id
        }
        return await send_ocpp_command(org_name, charger_id, "RemoteStopTransaction", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/Reset")
    async def reset(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: ResetRequest = Body(..., description="Reset request")
    ):
        """Reset the charge point"""
        payload = {
            "type": request.type
        }
        return await send_ocpp_command(org_name, charger_id, "Reset", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/SendLocalList")
    async def send_local_list(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: SendLocalListRequest = Body(..., description="Send local list request")
    ):
        """Send local authorization list"""
        payload = {
            "listVersion": request.list_version,
            "updateType": request.update_type
        }
        if request.local_authorization_list:
            payload["localAuthorizationList"] = request.local_authorization_list
        return await send_ocpp_command(org_name, charger_id, "SendLocalList", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/SetChargingProfile")
    async def set_charging_profile(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: SetChargingProfileRequest = Body(..., description="Set charging profile request")
    ):
        """Set charging profile"""
        payload = {
            "connectorId": request.connector_id,
            "csChargingProfiles": request.cs_charging_profiles
        }
        return await send_ocpp_command(org_name, charger_id, "SetChargingProfile", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/UnlockConnector")
    async def unlock_connector(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: UnlockConnectorRequest = Body(..., description="Unlock connector request")
    ):
        """Unlock a connector"""
        payload = {
            "connectorId": request.connector_id
        }
        return await send_ocpp_command(org_name, charger_id, "UnlockConnector", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/UpdateFirmware")
    async def update_firmware(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: UpdateFirmwareRequest = Body(..., description="Update firmware request")
    ):
        """Update firmware"""
        payload = {
            "location": request.location,
            "retrieveDate": request.retrieve_date
        }
        if request.retry_interval is not None:
            payload["retryInterval"] = request.retry_interval
        return await send_ocpp_command(org_name, charger_id, "UpdateFirmware", payload)
    
    # Smart Charging Profile Commands
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/ClearChargingProfile")
    async def clear_charging_profile(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: ClearChargingProfileRequest = Body(..., description="Clear charging profile request")
    ):
        """Clear charging profile"""
        payload = {}
        if request.id is not None:
            payload["id"] = request.id
        if request.connector_id is not None:
            payload["connectorId"] = request.connector_id
        if request.charging_profile_purpose:
            payload["chargingProfilePurpose"] = request.charging_profile_purpose
        if request.stack_level is not None:
            payload["stackLevel"] = request.stack_level
        return await send_ocpp_command(org_name, charger_id, "ClearChargingProfile", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/GetCompositeSchedule")
    async def get_composite_schedule(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: GetCompositeScheduleRequest = Body(..., description="Get composite schedule request")
    ):
        """Get composite charging schedule"""
        payload = {
            "connectorId": request.connector_id,
            "duration": request.duration
        }
        if request.charging_rate_unit:
            payload["chargingRateUnit"] = request.charging_rate_unit
        return await send_ocpp_command(org_name, charger_id, "GetCompositeSchedule", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/TriggerMessage")
    async def trigger_message(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: TriggerMessageRequest = Body(..., description="Trigger message request")
    ):
        """Trigger a message from charger"""
        payload = {
            "requestedMessage": request.requested_message
        }
        if request.connector_id is not None:
            payload["connectorId"] = request.connector_id
        return await send_ocpp_command(org_name, charger_id, "TriggerMessage", payload)
    
    # Firmware Management Profile Commands
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/GetDiagnostics")
    async def get_diagnostics(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: GetDiagnosticsRequest = Body(..., description="Get diagnostics request")
    ):
        """Get diagnostics"""
        payload = {
            "location": request.location
        }
        if request.start_time:
            payload["startTime"] = request.start_time
        if request.stop_time:
            payload["stopTime"] = request.stop_time
        if request.retry_interval is not None:
            payload["retryInterval"] = request.retry_interval
        if request.retries is not None:
            payload["retries"] = request.retries
        return await send_ocpp_command(org_name, charger_id, "GetDiagnostics", payload)
    
    # Local Authorization List Profile Commands
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/GetLocalListVersion")
    async def get_local_list_version(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID")
    ):
        """Get local authorization list version"""
        return await send_ocpp_command(org_name, charger_id, "GetLocalListVersion", {})
    
    # Reservation Profile Commands
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/CancelReservation")
    async def cancel_reservation(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: CancelReservationRequest = Body(..., description="Cancel reservation request")
    ):
        """Cancel a reservation"""
        payload = {
            "reservationId": request.reservation_id
        }
        return await send_ocpp_command(org_name, charger_id, "CancelReservation", payload)
    
    @router.post("/organizations/{org_name}/chargers/{charger_id}/commands/ReserveNow")
    async def reserve_now(
        org_name: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger ID"),
        request: ReserveNowRequest = Body(..., description="Reserve now request")
    ):
        """Create a reservation"""
        payload = {
            "connectorId": request.connector_id,
            "expiryDate": request.expiry_date,
            "idTag": request.id_tag,
            "reservationId": request.reservation_id
        }
        if request.parent_id_tag:
            payload["parentIdTag"] = request.parent_id_tag
        return await send_ocpp_command(org_name, charger_id, "ReserveNow", payload)
    
    # Store the pending_responses dict in the router for access from response handlers
    router.pending_responses = pending_responses
    
    return router

