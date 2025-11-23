"""
Unified OCPP Broker API Server

All REST API endpoints for the OCPP Broker:
- Tag Management API
- OCPP Command API (sends commands to chargers)
- MongoDB OCPP Data API (saves OCPP data to MongoDB)
- Broker Management API (orgs, backends, etc.)
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, HTTPException, APIRouter, Query, Path, Body
from pydantic import BaseModel, Field
import uvicorn

# Import schemas and services
from .schemas.tags import (
    OCPPTag, TagList, TagSearchRequest, TagSearchResponse,
    TagStatus, TagType
)
from .mongodb_service import (
    MongoDBService,
    StatusNotificationRequest,
    MeterValuesRequest,
    BootNotificationRequest,
    TransactionRequest as MongoDBTransactionRequest,
    AuthorizationRequest as MongoDBAuthorizationRequest,
    DataTransferRequest as MongoDBDataTransferRequest,
    OCPPMessageRequest
)

logger = logging.getLogger("ocpp_broker.api")
tag_logger = logging.getLogger("ocpp_broker.tag_api")
ocpp_logger = logging.getLogger("ocpp_broker.ocpp_command_api")
mongodb_logger = logging.getLogger("ocpp_broker.mongodb_api")


# ============================================================================
# Request Models for OCPP Commands
# ============================================================================

class BackendModel(BaseModel):
    id: str
    url: str
    leader: Optional[bool] = False


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


# ============================================================================
# API Router Creation Functions
# ============================================================================

def create_tag_api(broker) -> APIRouter:
    """Create tag management API router"""
    router = APIRouter(prefix="/api/tags", tags=["Tag Management"])
    
    # Get tag manager from broker
    tag_manager = broker.tag_manager if broker and hasattr(broker, 'tag_manager') else None
    
    if not tag_manager:
        @router.get("/status")
        async def tag_management_status():
            return {"enabled": False, "message": "Tag management not enabled"}
        
        return router
    
    @router.get("/status")
    async def tag_management_status():
        """Get tag management status"""
        mongodb_enabled = (
            tag_manager.mongodb_service is not None and 
            tag_manager.mongodb_service.is_connected()
        )
        return {
            "enabled": True,
            "message": "Tag management is active",
            "mongodb_persistence": mongodb_enabled,
            "organizations": list(tag_manager._tag_lists.keys())
        }
    
    @router.post("/organizations/{org_name}/tags")
    async def add_tag(
        org_name: str = Path(..., description="Organization name"),
        tag: OCPPTag = Body(..., description="Tag to add")
    ):
        """Add a new tag to an organization"""
        try:
            success = await tag_manager.add_tag(org_name, tag)
            if success:
                return {"success": True, "message": f"Tag {tag.id_tag} added successfully"}
            else:
                raise HTTPException(status_code=400, detail="Failed to add tag")
        except Exception as e:
            tag_logger.error(f"Error adding tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations/{org_name}/tags/{id_tag}")
    async def get_tag(
        org_name: str = Path(..., description="Organization name"),
        id_tag: str = Path(..., description="Tag ID")
    ):
        """Get a specific tag"""
        try:
            tag = await tag_manager.get_tag(org_name, id_tag)
            if tag:
                return tag.model_dump()
            else:
                raise HTTPException(status_code=404, detail="Tag not found")
        except Exception as e:
            tag_logger.error(f"Error getting tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.put("/organizations/{org_name}/tags/{id_tag}")
    async def update_tag(
        org_name: str = Path(..., description="Organization name"),
        id_tag: str = Path(..., description="Tag ID"),
        tag: OCPPTag = Body(..., description="Updated tag data")
    ):
        """Update an existing tag"""
        try:
            success = await tag_manager.update_tag(org_name, id_tag, tag)
            if success:
                return {"success": True, "message": f"Tag {id_tag} updated successfully"}
            else:
                raise HTTPException(status_code=400, detail="Failed to update tag")
        except Exception as e:
            tag_logger.error(f"Error updating tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.delete("/organizations/{org_name}/tags/{id_tag}")
    async def delete_tag(
        org_name: str = Path(..., description="Organization name"),
        id_tag: str = Path(..., description="Tag ID")
    ):
        """Delete a tag"""
        try:
            success = await tag_manager.delete_tag(org_name, id_tag)
            if success:
                return {"success": True, "message": f"Tag {id_tag} deleted successfully"}
            else:
                raise HTTPException(status_code=400, detail="Failed to delete tag")
        except Exception as e:
            tag_logger.error(f"Error deleting tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations/{org_name}/tags")
    async def search_tags(
        org_name: str = Path(..., description="Organization name"),
        id_tag: Optional[str] = Query(None, description="Filter by ID tag"),
        status: Optional[TagStatus] = Query(None, description="Filter by status"),
        tag_type: Optional[TagType] = Query(None, description="Filter by tag type"),
        parent_id_tag: Optional[str] = Query(None, description="Filter by parent tag"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
        offset: int = Query(0, ge=0, description="Number of results to skip")
    ):
        """Search tags with filters"""
        try:
            search_request = TagSearchRequest(
                id_tag=id_tag,
                status=status,
                tag_type=tag_type,
                parent_id_tag=parent_id_tag,
                limit=limit,
                offset=offset
            )
            result = await tag_manager.search_tags(org_name, search_request)
            return result.dict()
        except Exception as e:
            tag_logger.error(f"Error searching tags: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations/{org_name}/list")
    async def get_tag_list(
        org_name: str = Path(..., description="Organization name")
    ):
        """Get complete tag list for an organization"""
        try:
            tag_list = await tag_manager.get_tag_list(org_name)
            if tag_list:
                return tag_list.dict()
            else:
                return {"listVersion": 0, "tags": []}
        except Exception as e:
            tag_logger.error(f"Error getting tag list: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/organizations/{org_name}/tags/authorize")
    async def authorize_tag(
        org_name: str = Path(..., description="Organization name"),
        id_tag: str = Body(..., description="Tag ID to authorize")
    ):
        """Authorize a tag (same as OCPP Authorize command)"""
        try:
            result = await tag_manager.authorize_tag(org_name, id_tag)
            return {
                "idTag": id_tag,
                "idTagInfo": {
                    "status": result["status"],
                    "expiryDate": result.get("expiry_date"),
                    "parentIdTag": result.get("parent_id_tag")
                }
            }
        except Exception as e:
            tag_logger.error(f"Error authorizing tag: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/organizations")
    async def list_organizations():
        """List all organizations with tag management"""
        try:
            organizations = list(tag_manager._tag_lists.keys())
            return {"organizations": organizations}
        except Exception as e:
            tag_logger.error(f"Error listing organizations: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/sync")
    async def sync_tags_from_mongodb(org_name: Optional[str] = Query(None, description="Organization name (optional, syncs all if not provided)")):
        """Sync tags from MongoDB to cache"""
        try:
            await tag_manager.sync_from_mongodb(org_name)
            return {
                "success": True,
                "message": f"Synced tags from MongoDB{' for ' + org_name if org_name else ' (all organizations)'}"
            }
        except Exception as e:
            tag_logger.error(f"Error syncing tags from MongoDB: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    return router


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
            # Save command to MongoDB before sending
            mongodb = getattr(broker, "mongodb_service", None)
            if mongodb and mongodb.is_connected():
                try:
                    await mongodb.save_ocpp_message(
                        org_name=org_name,
                        charger_id=charger_id,
                        message_type="call",
                        action=action,
                        payload=payload,
                        direction="broker_to_charger",
                        message_id=message_id
                    )
                except Exception as e:
                    ocpp_logger.warning(f"Failed to save command {action} to MongoDB: {e}")
            
            # Send command to charger
            await session.send_to_charger(message_json)
            ocpp_logger.info(f"Sent OCPP command {action} to charger {org_name}/{charger_id} (message_id: {message_id})")
            
            return {
                "message_id": message_id,
                "organization": org_name,
                "charger_id": charger_id,
                "action": action,
                "status": "sent",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            ocpp_logger.error(f"Error sending OCPP command to {org_name}/{charger_id}: {e}")
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


def create_mongodb_api(broker) -> APIRouter:
    """
    Create FastAPI router for MongoDB OCPP data APIs.
    These APIs allow external systems to send OCPP data to MongoDB.
    """
    router = APIRouter(prefix="/api/mongodb", tags=["MongoDB OCPP Data"])
    
    @router.post("/status-notification", summary="Save StatusNotification to MongoDB")
    async def save_status_notification(request: StatusNotificationRequest = Body(...)):
        """Save a StatusNotification to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_status_notification(
                org_name=request.org_name,
                charger_id=request.charger_id,
                connector_id=request.connector_id,
                status=request.status,
                error_code=request.error_code,
                info=request.info,
                timestamp=request.timestamp,
                vendor_id=request.vendor_id,
                vendor_error_code=request.vendor_error_code
            )
            return {"status": "success", "message": "StatusNotification saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving status notification: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/meter-values", summary="Save MeterValues to MongoDB")
    async def save_meter_values(request: MeterValuesRequest = Body(...)):
        """Save MeterValues to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_meter_values(
                org_name=request.org_name,
                charger_id=request.charger_id,
                connector_id=request.connector_id,
                transaction_id=request.transaction_id,
                meter_value=request.meter_value,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "MeterValues saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving meter values: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/boot-notification", summary="Save BootNotification to MongoDB")
    async def save_boot_notification(request: BootNotificationRequest = Body(...)):
        """Save BootNotification to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_boot_notification(
                org_name=request.org_name,
                charger_id=request.charger_id,
                charge_point_model=request.charge_point_model,
                charge_point_vendor=request.charge_point_vendor,
                firmware_version=request.firmware_version,
                iccid=request.iccid,
                imsi=request.imsi,
                meter_type=request.meter_type,
                meter_serial_number=request.meter_serial_number,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "BootNotification saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving boot notification: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/transaction", summary="Save Transaction to MongoDB")
    async def save_transaction(request: MongoDBTransactionRequest = Body(...)):
        """Save StartTransaction or StopTransaction to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        if request.transaction_type not in ["start", "stop"]:
            raise HTTPException(status_code=400, detail="transaction_type must be 'start' or 'stop'")
        
        try:
            await mongodb.save_transaction(
                org_name=request.org_name,
                charger_id=request.charger_id,
                transaction_id=request.transaction_id,
                connector_id=request.connector_id,
                id_tag=request.id_tag,
                meter_start=request.meter_start,
                timestamp=request.timestamp,
                reservation_id=request.reservation_id,
                transaction_type=request.transaction_type
            )
            return {"status": "success", "message": f"{request.transaction_type.capitalize()}Transaction saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving transaction: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/authorization", summary="Save Authorization to MongoDB")
    async def save_authorization(request: MongoDBAuthorizationRequest = Body(...)):
        """Save Authorization to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_authorization(
                org_name=request.org_name,
                charger_id=request.charger_id,
                id_tag=request.id_tag,
                status=request.status,
                expiry_date=request.expiry_date,
                parent_id_tag=request.parent_id_tag,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "Authorization saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving authorization: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/data-transfer", summary="Save DataTransfer to MongoDB")
    async def save_data_transfer(request: MongoDBDataTransferRequest = Body(...)):
        """Save DataTransfer to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_data_transfer(
                org_name=request.org_name,
                charger_id=request.charger_id,
                vendor_id=request.vendor_id,
                message_id=request.message_id,
                data=request.data,
                status=request.status,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "DataTransfer saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving data transfer: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/ocpp-message", summary="Save generic OCPP message to MongoDB")
    async def save_ocpp_message(request: OCPPMessageRequest = Body(...)):
        """Save a generic OCPP message to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        if request.message_type not in ["call", "call_result", "call_error"]:
            raise HTTPException(status_code=400, detail="message_type must be 'call', 'call_result', or 'call_error'")
        
        try:
            await mongodb.save_ocpp_message(
                org_name=request.org_name,
                charger_id=request.charger_id,
                message_type=request.message_type,
                action=request.action,
                payload=request.payload,
                direction=request.direction,
                message_id=request.message_id,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "OCPP message saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving OCPP message: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/health", summary="Check MongoDB connection status")
    async def mongodb_health():
        """Check if MongoDB service is connected."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb:
            return {"status": "not_configured", "connected": False}
        
        return {
            "status": "connected" if mongodb.is_connected() else "disconnected",
            "connected": mongodb.is_connected(),
            "database": mongodb.database_name
        }
    
    return router
    
    @router.post("/status-notification", summary="Save StatusNotification to MongoDB")
    async def save_status_notification(request: StatusNotificationRequest = Body(...)):
        """Save a StatusNotification to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_status_notification(
                org_name=request.org_name,
                charger_id=request.charger_id,
                connector_id=request.connector_id,
                status=request.status,
                error_code=request.error_code,
                info=request.info,
                timestamp=request.timestamp,
                vendor_id=request.vendor_id,
                vendor_error_code=request.vendor_error_code
            )
            return {"status": "success", "message": "StatusNotification saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving status notification: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/meter-values", summary="Save MeterValues to MongoDB")
    async def save_meter_values(request: MeterValuesRequest = Body(...)):
        """Save MeterValues to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_meter_values(
                org_name=request.org_name,
                charger_id=request.charger_id,
                connector_id=request.connector_id,
                transaction_id=request.transaction_id,
                meter_value=request.meter_value,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "MeterValues saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving meter values: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/boot-notification", summary="Save BootNotification to MongoDB")
    async def save_boot_notification(request: BootNotificationRequest = Body(...)):
        """Save BootNotification to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_boot_notification(
                org_name=request.org_name,
                charger_id=request.charger_id,
                charge_point_model=request.charge_point_model,
                charge_point_vendor=request.charge_point_vendor,
                firmware_version=request.firmware_version,
                iccid=request.iccid,
                imsi=request.imsi,
                meter_type=request.meter_type,
                meter_serial_number=request.meter_serial_number,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "BootNotification saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving boot notification: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/transaction", summary="Save Transaction to MongoDB")
    async def save_transaction(request: MongoDBTransactionRequest = Body(...)):
        """Save StartTransaction or StopTransaction to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        if request.transaction_type not in ["start", "stop"]:
            raise HTTPException(status_code=400, detail="transaction_type must be 'start' or 'stop'")
        
        try:
            await mongodb.save_transaction(
                org_name=request.org_name,
                charger_id=request.charger_id,
                transaction_id=request.transaction_id,
                connector_id=request.connector_id,
                id_tag=request.id_tag,
                meter_start=request.meter_start,
                timestamp=request.timestamp,
                reservation_id=request.reservation_id,
                transaction_type=request.transaction_type
            )
            return {"status": "success", "message": f"{request.transaction_type.capitalize()}Transaction saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving transaction: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/authorization", summary="Save Authorization to MongoDB")
    async def save_authorization(request: MongoDBAuthorizationRequest = Body(...)):
        """Save Authorization to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_authorization(
                org_name=request.org_name,
                charger_id=request.charger_id,
                id_tag=request.id_tag,
                status=request.status,
                expiry_date=request.expiry_date,
                parent_id_tag=request.parent_id_tag,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "Authorization saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving authorization: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/data-transfer", summary="Save DataTransfer to MongoDB")
    async def save_data_transfer(request: MongoDBDataTransferRequest = Body(...)):
        """Save DataTransfer to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        try:
            await mongodb.save_data_transfer(
                org_name=request.org_name,
                charger_id=request.charger_id,
                vendor_id=request.vendor_id,
                message_id=request.message_id,
                data=request.data,
                status=request.status,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "DataTransfer saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving data transfer: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/ocpp-message", summary="Save generic OCPP message to MongoDB")
    async def save_ocpp_message(request: OCPPMessageRequest = Body(...)):
        """Save a generic OCPP message to MongoDB."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb or not mongodb.is_connected():
            raise HTTPException(status_code=503, detail="MongoDB service not available")
        
        if request.message_type not in ["call", "call_result", "call_error"]:
            raise HTTPException(status_code=400, detail="message_type must be 'call', 'call_result', or 'call_error'")
        
        try:
            await mongodb.save_ocpp_message(
                org_name=request.org_name,
                charger_id=request.charger_id,
                message_type=request.message_type,
                action=request.action,
                payload=request.payload,
                direction=request.direction,
                message_id=request.message_id,
                timestamp=request.timestamp
            )
            return {"status": "success", "message": "OCPP message saved"}
        except Exception as e:
            mongodb_logger.error(f"Error saving OCPP message: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/health", summary="Check MongoDB connection status")
    async def mongodb_health():
        """Check if MongoDB service is connected."""
        mongodb = getattr(broker, "mongodb_service", None)
        if not mongodb:
            return {"status": "not_configured", "connected": False}
        
        return {
            "status": "connected" if mongodb.is_connected() else "disconnected",
            "connected": mongodb.is_connected(),
            "database": mongodb.database_name
        }
    
    return router


# ============================================================================
# Main API Creation
# ============================================================================

def create_api(broker):
    """
    Create FastAPI app bound to a running OcppBroker instance.
    Includes all API endpoints: Tag Management, OCPP Commands, MongoDB, and Broker Management.
    """
    app = FastAPI(title="OCPP Broker API", version="1.0")
    
    # Include tag management API
    tag_router = create_tag_api(broker)
    app.include_router(tag_router)
    
    # Include OCPP command API
    ocpp_command_router = create_ocpp_command_api(broker)
    app.include_router(ocpp_command_router)
    
    # Include MongoDB API
    mongodb_router = create_mongodb_api(broker)
    app.include_router(mongodb_router)

    # Broker Management API
    @app.get("/orgs")
    async def list_orgs():
        return [
            {
                "name": org,
                "num_backends": len(broker.org_backends.get(org, {})),
                "leader": getattr(broker.org_leaders.get(org), "id", None)
            }
            for org in broker.org_backends.keys()
        ]

    @app.get("/orgs/{org}/backends")
    async def list_backends(org: str):
        backs = broker.org_backends.get(org)
        if backs is None:
            raise HTTPException(status_code=404, detail="Organization not found")
        return [
            {
                "id": b.id,
                "url": b.url,
                "leader": b.is_leader,
                "connected": (b.websocket is not None and not getattr(b.websocket, "closed", True))
            }
            for b in backs.values()
        ]

    @app.post("/orgs/{org}/backends")
    async def add_backend(org: str, backend: BackendModel):
        if org not in broker.org_backends:
            raise HTTPException(status_code=404, detail="Organization not found")
        if backend.id in broker.org_backends[org]:
            raise HTTPException(status_code=400, detail="Backend already exists")

        # add backend asynchronously
        await broker.add_backend_dynamic(org, backend.model_dump())
        return {"status": "created", "backend": backend.id}

    @app.delete("/orgs/{org}/backends/{backend_id}")
    async def remove_backend(org: str, backend_id: str):
        if org not in broker.org_backends:
            raise HTTPException(status_code=404, detail="Organization not found")
        removed = await broker.remove_backend_dynamic(org, backend_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Backend not found")
        return {"status": "removed", "backend": backend_id}

    @app.post("/orgs/{org}/leader/{backend_id}")
    async def set_leader(org: str, backend_id: str):
        if org not in broker.org_backends:
            raise HTTPException(status_code=404, detail="Organization not found")
        success = broker.promote_leader(org, backend_id)
        if not success:
            raise HTTPException(status_code=404, detail="Backend not found")
        return {"status": "leader_updated", "leader": backend_id}

    @app.post("/reload")
    async def reload_config():
        await broker.reload_from_config()
        return {"status": "config_reloaded"}

    return app


async def start_api(broker, host="0.0.0.0", port=8080):
    """
    Launch FastAPI in background using uvicorn Server. This returns immediately and runs the server task.
    """
    app = create_api(broker)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)

    loop = asyncio.get_event_loop()
    # run uvicorn in background
    loop.create_task(server.serve())
    logger.info(f"API Server running at http://{host}:{port}")
