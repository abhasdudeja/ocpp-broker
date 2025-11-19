"""
REST API endpoints for saving OCPP data to MongoDB.
These APIs allow external systems to send OCPP data to MongoDB.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field

logger = logging.getLogger("ocpp_broker.mongodb_api")


# Request models
class StatusNotificationRequest(BaseModel):
    org_name: str = Field(..., description="Organization name")
    charger_id: str = Field(..., description="Charger ID")
    connector_id: int = Field(..., description="Connector ID")
    status: str = Field(..., description="Status (Available, Preparing, Charging, etc.)")
    error_code: Optional[str] = Field(None, description="Error code")
    info: Optional[str] = Field(None, description="Info message")
    vendor_id: Optional[str] = Field(None, description="Vendor ID")
    vendor_error_code: Optional[str] = Field(None, description="Vendor error code")
    timestamp: Optional[datetime] = Field(None, description="Timestamp (defaults to now)")


class MeterValuesRequest(BaseModel):
    org_name: str = Field(..., description="Organization name")
    charger_id: str = Field(..., description="Charger ID")
    connector_id: int = Field(..., description="Connector ID")
    transaction_id: Optional[int] = Field(None, description="Transaction ID")
    meter_value: List[Dict[str, Any]] = Field(..., description="List of meter value readings")
    timestamp: Optional[datetime] = Field(None, description="Timestamp (defaults to now)")


class BootNotificationRequest(BaseModel):
    org_name: str = Field(..., description="Organization name")
    charger_id: str = Field(..., description="Charger ID")
    charge_point_model: str = Field(..., description="Charger model")
    charge_point_vendor: str = Field(..., description="Charger vendor")
    firmware_version: Optional[str] = Field(None, description="Firmware version")
    iccid: Optional[str] = Field(None, description="ICCID")
    imsi: Optional[str] = Field(None, description="IMSI")
    meter_type: Optional[str] = Field(None, description="Meter type")
    meter_serial_number: Optional[str] = Field(None, description="Meter serial number")
    timestamp: Optional[datetime] = Field(None, description="Timestamp (defaults to now)")


class TransactionRequest(BaseModel):
    org_name: str = Field(..., description="Organization name")
    charger_id: str = Field(..., description="Charger ID")
    transaction_id: int = Field(..., description="Transaction ID")
    connector_id: int = Field(..., description="Connector ID")
    id_tag: str = Field(..., description="ID tag")
    meter_start: Optional[int] = Field(None, description="Meter start value")
    reservation_id: Optional[int] = Field(None, description="Reservation ID")
    transaction_type: str = Field("start", description="Transaction type: 'start' or 'stop'")
    timestamp: Optional[datetime] = Field(None, description="Timestamp (defaults to now)")


class AuthorizationRequest(BaseModel):
    org_name: str = Field(..., description="Organization name")
    charger_id: str = Field(..., description="Charger ID")
    id_tag: str = Field(..., description="ID tag")
    status: str = Field(..., description="Authorization status")
    expiry_date: Optional[str] = Field(None, description="Expiry date")
    parent_id_tag: Optional[str] = Field(None, description="Parent ID tag")
    timestamp: Optional[datetime] = Field(None, description="Timestamp (defaults to now)")


class DataTransferRequest(BaseModel):
    org_name: str = Field(..., description="Organization name")
    charger_id: str = Field(..., description="Charger ID")
    vendor_id: str = Field(..., description="Vendor ID")
    message_id: Optional[str] = Field(None, description="Message ID")
    data: Optional[str] = Field(None, description="Data payload")
    status: Optional[str] = Field(None, description="Status response")
    timestamp: Optional[datetime] = Field(None, description="Timestamp (defaults to now)")


class OCPPMessageRequest(BaseModel):
    org_name: str = Field(..., description="Organization name")
    charger_id: str = Field(..., description="Charger ID")
    message_type: str = Field(..., description="Message type: 'call', 'call_result', 'call_error'")
    action: str = Field(..., description="OCPP action name")
    payload: Dict[str, Any] = Field(..., description="Message payload")
    direction: str = Field("charger_to_broker", description="Message direction")
    message_id: Optional[str] = Field(None, description="Message ID")
    timestamp: Optional[datetime] = Field(None, description="Timestamp (defaults to now)")


def create_mongodb_api(broker) -> APIRouter:
    """
    Create FastAPI router for MongoDB OCPP data APIs.
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
            logger.error(f"Error saving status notification: {e}", exc_info=True)
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
            logger.error(f"Error saving meter values: {e}", exc_info=True)
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
            logger.error(f"Error saving boot notification: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/transaction", summary="Save Transaction to MongoDB")
    async def save_transaction(request: TransactionRequest = Body(...)):
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
            logger.error(f"Error saving transaction: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/authorization", summary="Save Authorization to MongoDB")
    async def save_authorization(request: AuthorizationRequest = Body(...)):
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
            logger.error(f"Error saving authorization: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/data-transfer", summary="Save DataTransfer to MongoDB")
    async def save_data_transfer(request: DataTransferRequest = Body(...)):
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
            logger.error(f"Error saving data transfer: {e}", exc_info=True)
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
            logger.error(f"Error saving OCPP message: {e}", exc_info=True)
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

