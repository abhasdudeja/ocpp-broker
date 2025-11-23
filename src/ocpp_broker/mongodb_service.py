"""
MongoDB service for saving OCPP messages, statuses, metervalues, and configurations.
Organizes data by organization and charger.

Also provides REST API endpoints for external systems to save OCPP data to MongoDB.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from pydantic import BaseModel, Field

logger = logging.getLogger("ocpp_broker.mongodb")


class MongoDBService:
    """
    Service for saving OCPP data to MongoDB.
    Organizes data by organization and charger in separate collections.
    """
    
    def __init__(self, connection_string: str, database_name: str = "ocpp_broker"):
        """
        Initialize MongoDB service.
        
        Args:
            connection_string: MongoDB connection string (e.g., "mongodb://localhost:27017")
            database_name: Name of the database to use
        """
        self.connection_string = connection_string
        self.database_name = database_name
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None
        self._connected = False
        
    async def connect(self):
        """Connect to MongoDB."""
        try:
            self.client = AsyncIOMotorClient(
                self.connection_string,
                serverSelectionTimeoutMS=5000
            )
            # Test connection
            await self.client.admin.command('ping')
            self.db = self.client[self.database_name]
            self._connected = True
            logger.info(f"✅ Connected to MongoDB database: {self.database_name}")
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error(f"❌ Failed to connect to MongoDB: {e}")
            self._connected = False
            raise
    
    async def disconnect(self):
        """Disconnect from MongoDB."""
        if self.client:
            self.client.close()
            self._connected = False
            logger.info("Disconnected from MongoDB")
    
    def is_connected(self) -> bool:
        """Check if connected to MongoDB."""
        return self._connected
    
    def _get_collection(self, collection_name: str) -> AsyncIOMotorCollection:
        """Get a collection from the database."""
        if self.db is None:
            raise RuntimeError("MongoDB not connected. Call connect() first.")
        return self.db[collection_name]
    
    def _get_collection_name_for_action(self, action: str) -> str:
        """
        Get the collection name for a specific OCPP action.
        Converts action names to collection names (e.g., "BootNotification" -> "boot_notifications")
        """
        # Special mappings for all OCPP 1.6 actions
        # From Charge Point to Central System
        action_mappings = {
            # Core Profile
            "Authorize": "authorize_messages",
            "BootNotification": "boot_notifications",
            "DataTransfer": "data_transfers",
            "DiagnosticsStatusNotification": "diagnostics_status_notifications",
            "FirmwareStatusNotification": "firmware_status_notifications",
            # Heartbeat is handled separately - only latest timestamp is stored
            # "Heartbeat": "heartbeats",  # Not used - see update_heartbeat_timestamp()
            "MeterValues": "meter_values",
            "StartTransaction": "start_transactions",
            "StatusNotification": "status_notifications",
            "StopTransaction": "stop_transactions",
            # From Central System to Charge Point (commands)
            "CancelReservation": "cancel_reservations",
            "ChangeAvailability": "change_availabilities",
            "ChangeConfiguration": "change_configurations",
            "ClearCache": "clear_caches",
            "ClearChargingProfile": "clear_charging_profiles",
            "GetCompositeSchedule": "get_composite_schedules",
            "GetConfiguration": "get_configurations",
            "GetDiagnostics": "get_diagnostics",
            "GetLocalListVersion": "get_local_list_versions",
            "RemoteStartTransaction": "remote_start_transactions",
            "RemoteStopTransaction": "remote_stop_transactions",
            "ReserveNow": "reserve_nows",
            "Reset": "resets",
            "SendLocalList": "send_local_lists",
            "SetChargingProfile": "set_charging_profiles",
            "TriggerMessage": "trigger_messages",
            "UnlockConnector": "unlock_connectors",
            "UpdateFirmware": "update_firmwares",
        }
        
        # Use mapping if available
        if action in action_mappings:
            return action_mappings[action]
        
        # Otherwise, convert CamelCase to snake_case and pluralize
        import re
        # Insert underscore before capital letters
        snake_case = re.sub(r'(?<!^)(?=[A-Z])', '_', action).lower()
        
        # Handle pluralization
        if snake_case.endswith('y'):
            return snake_case[:-1] + 'ies'
        elif snake_case.endswith(('s', 'x', 'z', 'ch', 'sh')):
            return snake_case + 'es'
        elif snake_case.endswith('f'):
            return snake_case[:-1] + 'ves'
        elif snake_case.endswith('fe'):
            return snake_case[:-2] + 'ves'
        else:
            return snake_case + 's'
    
    async def save_ocpp_message(
        self,
        org_name: str,
        charger_id: str,
        message_type: str,  # "call", "call_result", "call_error"
        action: str,
        payload: Dict[str, Any],
        direction: str = "charger_to_broker",  # "charger_to_broker" or "broker_to_charger"
        message_id: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        """
        Save an OCPP message to MongoDB in an action-specific collection.
        
        Args:
            org_name: Organization name
            charger_id: Charger ID
            message_type: Type of message (call, call_result, call_error)
            action: OCPP action name (e.g., "BootNotification", "StatusNotification")
            payload: Message payload
            direction: Message direction
            message_id: Optional message ID
            timestamp: Optional timestamp (defaults to now)
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping message save")
            return
        
        try:
            # Get collection name based on action (e.g., "BootNotification" -> "boot_notifications")
            collection_name = self._get_collection_name_for_action(action)
            collection = self._get_collection(collection_name)
            
            document = {
                "org_name": org_name,
                "charger_id": charger_id,
                "message_type": message_type,
                "action": action,
                "payload": payload,
                "direction": direction,
                "timestamp": timestamp or datetime.now(timezone.utc),
                "message_id": message_id
            }
            await collection.insert_one(document)
            logger.debug(f"Saved OCPP message: {action} from {org_name}/{charger_id} to collection {collection_name}")
        except Exception as e:
            logger.error(f"Error saving OCPP message: {e}", exc_info=True)
    
    async def save_status_notification(
        self,
        org_name: str,
        charger_id: str,
        connector_id: int,
        status: str,
        error_code: Optional[str] = None,
        info: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        vendor_id: Optional[str] = None,
        vendor_error_code: Optional[str] = None
    ):
        """
        Save a StatusNotification to MongoDB.
        
        Args:
            org_name: Organization name
            charger_id: Charger ID
            connector_id: Connector ID
            status: Status value (Available, Preparing, Charging, etc.)
            error_code: Optional error code
            info: Optional info message
            timestamp: Optional timestamp (defaults to now)
            vendor_id: Optional vendor ID
            vendor_error_code: Optional vendor error code
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping status save")
            return
        
        try:
            collection = self._get_collection("charger_statuses")
            document = {
                "org_name": org_name,
                "charger_id": charger_id,
                "connector_id": connector_id,
                "status": status,
                "error_code": error_code,
                "info": info,
                "timestamp": timestamp or datetime.now(timezone.utc),
                "vendor_id": vendor_id,
                "vendor_error_code": vendor_error_code
            }
            await collection.insert_one(document)
            
            # Also update the latest status
            await self._update_latest_status(org_name, charger_id, connector_id, document)
            
            logger.debug(f"Saved status: {charger_id}/connector{connector_id} = {status}")
        except Exception as e:
            logger.error(f"Error saving status notification: {e}", exc_info=True)
    
    async def _update_latest_status(
        self,
        org_name: str,
        charger_id: str,
        connector_id: int,
        status_doc: Dict[str, Any]
    ):
        """Update the latest status for a connector."""
        try:
            collection = self._get_collection("charger_statuses_latest")
            # Create a copy of status_doc without _id (MongoDB _id is immutable)
            update_doc = {k: v for k, v in status_doc.items() if k != "_id"}
            update_doc["updated_at"] = datetime.now(timezone.utc)
            
            await collection.update_one(
                {
                    "org_name": org_name,
                    "charger_id": charger_id,
                    "connector_id": connector_id
                },
                {
                    "$set": update_doc
                },
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error updating latest status: {e}", exc_info=True)
    
    async def save_meter_values(
        self,
        org_name: str,
        charger_id: str,
        connector_id: int,
        transaction_id: Optional[int],
        meter_value: List[Dict[str, Any]],
        timestamp: Optional[datetime] = None
    ):
        """
        Save MeterValues to MongoDB.
        
        Args:
            org_name: Organization name
            charger_id: Charger ID
            connector_id: Connector ID
            transaction_id: Optional transaction ID
            meter_value: List of meter value readings
            timestamp: Optional timestamp (defaults to now)
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping meter values save")
            return
        
        try:
            collection = self._get_collection("meter_values")
            document = {
                "org_name": org_name,
                "charger_id": charger_id,
                "connector_id": connector_id,
                "transaction_id": transaction_id,
                "meter_value": meter_value,
                "timestamp": timestamp or datetime.now(timezone.utc)
            }
            await collection.insert_one(document)
            logger.debug(f"Saved meter values: {charger_id}/connector{connector_id}")
        except Exception as e:
            logger.error(f"Error saving meter values: {e}", exc_info=True)
    
    async def save_boot_notification(
        self,
        org_name: str,
        charger_id: str,
        charge_point_model: str,
        charge_point_vendor: str,
        firmware_version: Optional[str] = None,
        iccid: Optional[str] = None,
        imsi: Optional[str] = None,
        meter_type: Optional[str] = None,
        meter_serial_number: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        """
        Save BootNotification data to MongoDB.
        
        Args:
            org_name: Organization name
            charger_id: Charger ID
            charge_point_model: Charger model
            charge_point_vendor: Charger vendor
            firmware_version: Optional firmware version
            iccid: Optional ICCID
            imsi: Optional IMSI
            meter_type: Optional meter type
            meter_serial_number: Optional meter serial number
            timestamp: Optional timestamp (defaults to now)
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping boot notification save")
            return
        
        try:
            collection = self._get_collection("charger_configurations")
            document = {
                "org_name": org_name,
                "charger_id": charger_id,
                "charge_point_model": charge_point_model,
                "charge_point_vendor": charge_point_vendor,
                "firmware_version": firmware_version,
                "iccid": iccid,
                "imsi": imsi,
                "meter_type": meter_type,
                "meter_serial_number": meter_serial_number,
                "last_boot_time": timestamp or datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc)
            }
            await collection.update_one(
                {"org_name": org_name, "charger_id": charger_id},
                {"$set": document},
                upsert=True
            )
            logger.debug(f"Saved boot notification: {charger_id}")
        except Exception as e:
            logger.error(f"Error saving boot notification: {e}", exc_info=True)
    
    async def save_transaction(
        self,
        org_name: str,
        charger_id: str,
        transaction_id: int,
        connector_id: int,
        id_tag: str,
        meter_start: Optional[int] = None,
        timestamp: Optional[datetime] = None,
        reservation_id: Optional[int] = None,
        transaction_type: str = "start"  # "start" or "stop"
    ):
        """
        Save transaction data (StartTransaction or StopTransaction) to MongoDB.
        
        Args:
            org_name: Organization name
            charger_id: Charger ID
            transaction_id: Transaction ID
            connector_id: Connector ID
            id_tag: ID tag
            meter_start: Optional meter start value
            timestamp: Optional timestamp (defaults to now)
            reservation_id: Optional reservation ID
            transaction_type: "start" or "stop"
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping transaction save")
            return
        
        try:
            collection = self._get_collection("transactions")
            document = {
                "org_name": org_name,
                "charger_id": charger_id,
                "transaction_id": transaction_id,
                "connector_id": connector_id,
                "id_tag": id_tag,
                "meter_start": meter_start,
                "reservation_id": reservation_id,
                "transaction_type": transaction_type,
                "timestamp": timestamp or datetime.now(timezone.utc)
            }
            
            if transaction_type == "start":
                await collection.insert_one(document)
            else:  # stop
                # Update the existing transaction
                await collection.update_one(
                    {
                        "org_name": org_name,
                        "charger_id": charger_id,
                        "transaction_id": transaction_id,
                        "transaction_type": "start"
                    },
                    {
                        "$set": {
                            **document,
                            "transaction_type": "stop"
                        }
                    }
                )
            
            logger.debug(f"Saved transaction: {transaction_type} {transaction_id} for {charger_id}")
        except Exception as e:
            logger.error(f"Error saving transaction: {e}", exc_info=True)
    
    async def save_authorization(
        self,
        org_name: str,
        charger_id: str,
        id_tag: str,
        status: str,
        expiry_date: Optional[str] = None,
        parent_id_tag: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        """
        Save authorization data to MongoDB.
        
        Args:
            org_name: Organization name
            charger_id: Charger ID
            id_tag: ID tag
            status: Authorization status (Accepted, Blocked, Expired, Invalid, ConcurrentTx)
            expiry_date: Optional expiry date
            parent_id_tag: Optional parent ID tag
            timestamp: Optional timestamp (defaults to now)
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping authorization save")
            return
        
        try:
            collection = self._get_collection("authorizations")
            document = {
                "org_name": org_name,
                "charger_id": charger_id,
                "id_tag": id_tag,
                "status": status,
                "expiry_date": expiry_date,
                "parent_id_tag": parent_id_tag,
                "timestamp": timestamp or datetime.now(timezone.utc)
            }
            await collection.insert_one(document)
            logger.debug(f"Saved authorization: {id_tag} = {status}")
        except Exception as e:
            logger.error(f"Error saving authorization: {e}", exc_info=True)
    
    async def save_data_transfer(
        self,
        org_name: str,
        charger_id: str,
        vendor_id: str,
        message_id: Optional[str] = None,
        data: Optional[str] = None,
        status: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        """
        Save DataTransfer command to MongoDB.
        
        Args:
            org_name: Organization name
            charger_id: Charger ID
            vendor_id: Vendor ID
            message_id: Optional message ID
            data: Optional data payload
            status: Optional status response
            timestamp: Optional timestamp (defaults to now)
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping data transfer save")
            return
        
        try:
            collection = self._get_collection("data_transfers")
            document = {
                "org_name": org_name,
                "charger_id": charger_id,
                "vendor_id": vendor_id,
                "message_id": message_id,
                "data": data,
                "status": status,
                "timestamp": timestamp or datetime.now(timezone.utc)
            }
            await collection.insert_one(document)
            logger.debug(f"Saved data transfer: {vendor_id}/{message_id}")
        except Exception as e:
            logger.error(f"Error saving data transfer: {e}", exc_info=True)
    
    async def update_heartbeat_timestamp(
        self,
        org_name: str,
        charger_id: str,
        timestamp: Optional[datetime] = None
    ):
        """
        Update the latest heartbeat timestamp for a charger.
        This indicates when the charger was last available.
        Only stores the latest timestamp, not individual heartbeat messages.
        
        Args:
            org_name: Organization name
            charger_id: Charger ID
            timestamp: Optional timestamp (defaults to now)
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping heartbeat update")
            return
        
        try:
            collection = self._get_collection("charger_heartbeats_latest")
            now = timestamp or datetime.now(timezone.utc)
            
            await collection.update_one(
                {
                    "org_name": org_name,
                    "charger_id": charger_id
                },
                {
                    "$set": {
                        "org_name": org_name,
                        "charger_id": charger_id,
                        "last_heartbeat": now,
                        "updated_at": now
                    }
                },
                upsert=True
            )
            logger.debug(f"Updated heartbeat timestamp for {charger_id}: {now}")
        except Exception as e:
            logger.error(f"Error updating heartbeat timestamp: {e}", exc_info=True)
    
    # ============================================================================
    # Tag Management CRUD Operations
    # ============================================================================
    
    async def save_tag(
        self,
        org_name: str,
        tag_data: Dict[str, Any],
        timestamp: Optional[datetime] = None
    ) -> bool:
        """
        Save a tag to MongoDB.
        
        Args:
            org_name: Organization name
            tag_data: Tag data dictionary (should include id_tag, status, etc.)
            timestamp: Optional timestamp (defaults to now)
        """
        if not self._connected:
            logger.warning("MongoDB not connected, skipping tag save")
            return False
        
        try:
            collection = self._get_collection("tags")
            now = timestamp or datetime.now(timezone.utc)
            
            document = {
                "org_name": org_name,
                "id_tag": tag_data.get("id_tag"),
                **tag_data,
                "updated_at": now
            }
            
            # Set created_at if not present
            if "created_at" not in document:
                document["created_at"] = now
            
            await collection.update_one(
                {"org_name": org_name, "id_tag": tag_data.get("id_tag")},
                {"$set": document},
                upsert=True
            )
            logger.debug(f"Saved tag: {org_name}/{tag_data.get('id_tag')}")
            return True
        except Exception as e:
            logger.error(f"Error saving tag: {e}", exc_info=True)
            return False
    
    async def get_tag(self, org_name: str, id_tag: str) -> Optional[Dict[str, Any]]:
        """Get a tag by org and id_tag"""
        if not self._connected:
            return None
        
        try:
            collection = self._get_collection("tags")
            tag = await collection.find_one({"org_name": org_name, "id_tag": id_tag})
            if tag and "_id" in tag:
                tag.pop("_id")
            return tag
        except Exception as e:
            logger.error(f"Error getting tag: {e}", exc_info=True)
            return None
    
    async def list_tags(
        self,
        org_name: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """List tags, optionally filtered by organization and other criteria"""
        if not self._connected:
            return []
        
        try:
            collection = self._get_collection("tags")
            query = {}
            if org_name:
                query["org_name"] = org_name
            if filters:
                query.update(filters)
            
            cursor = collection.find(query)
            tags = []
            async for tag in cursor:
                if "_id" in tag:
                    tag.pop("_id")
                tags.append(tag)
            return tags
        except Exception as e:
            logger.error(f"Error listing tags: {e}", exc_info=True)
            return []
    
    async def delete_tag(self, org_name: str, id_tag: str) -> bool:
        """Delete a tag"""
        if not self._connected:
            return False
        
        try:
            collection = self._get_collection("tags")
            result = await collection.delete_one({"org_name": org_name, "id_tag": id_tag})
            logger.debug(f"Deleted tag: {org_name}/{id_tag}")
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"Error deleting tag: {e}", exc_info=True)
            return False
    
    async def get_tag_list_version(self, org_name: str) -> int:
        """Get the current tag list version for an organization"""
        if not self._connected:
            return 0
        
        try:
            collection = self._get_collection("tag_list_versions")
            doc = await collection.find_one({"org_name": org_name})
            if doc:
                return doc.get("list_version", 1)
            return 1
        except Exception as e:
            logger.error(f"Error getting tag list version: {e}", exc_info=True)
            return 1
    
    async def update_tag_list_version(self, org_name: str, list_version: int) -> bool:
        """Update the tag list version for an organization"""
        if not self._connected:
            return False
        
        try:
            collection = self._get_collection("tag_list_versions")
            await collection.update_one(
                {"org_name": org_name},
                {
                    "$set": {
                        "org_name": org_name,
                        "list_version": list_version,
                        "updated_at": datetime.now(timezone.utc)
                    }
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Error updating tag list version: {e}", exc_info=True)
            return False


# ============================================================================
# REST API Request Models
# ============================================================================
# Note: API endpoints are now in api_server.py
# These models are kept here for backward compatibility and for use by api_server.py

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


# Note: API endpoints (create_mongodb_api function) have been moved to api_server.py
# to consolidate all API endpoints in a single file.

