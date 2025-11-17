"""
DataTransfer Command Handler

Handles DataTransfer commands sent from chargers to the broker/backend.
DataTransfer allows chargers to send custom vendor-specific data.
"""

import logging
import json
from typing import Dict, Any, Optional, Callable, Awaitable
from enum import Enum

logger = logging.getLogger("ocpp_broker.data_transfer_handler")


class DataTransferStatus(str, Enum):
    """DataTransfer response status values per OCPP 1.6"""
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    UNKNOWN_MESSAGE_ID = "UnknownMessageId"
    UNKNOWN_VENDOR_ID = "UnknownVendorId"
    NOT_IMPLEMENTED = "NotImplemented"


class DataTransferHandler:
    """
    Handler for processing DataTransfer commands from chargers.
    
    DataTransfer allows chargers to send vendor-specific data to the central system.
    Data is specified with Vendor ID and Message ID combinations.
    The handler routes messages based on vendor_id and message_id.
    """
    
    def __init__(self, broker):
        self.broker = broker
        self.logger = logging.getLogger("ocpp_broker.data_transfer_handler")
        
        # Register vendor-specific handlers (vendor_id -> handler)
        self.vendor_handlers: Dict[str, Callable[[str, str, Optional[str], Optional[str]], Awaitable[tuple[str, Optional[str]]]]] = {}
        
        # Register vendor+message_id specific handlers ((vendor_id, message_id) -> handler)
        self.vendor_message_handlers: Dict[tuple[str, str], Callable[[str, str, Optional[str], Optional[str]], Awaitable[tuple[str, Optional[str]]]]] = {}
        
        # Store received data transfers for logging/analysis
        self.received_transfers: list = []
    
    def register_vendor_handler(
        self, 
        vendor_id: str, 
        handler: Callable[[str, str, Optional[str], Optional[str]], Awaitable[tuple[str, Optional[str]]]],
        message_id: Optional[str] = None
    ):
        """
        Register a custom handler for a vendor ID, optionally for a specific message ID.
        
        Args:
            vendor_id: Vendor identifier
            handler: Async function that takes (charger_id, org_name, message_id, data) 
                     and returns (status, response_data)
            message_id: Optional message ID. If provided, handler is registered for this 
                       specific vendor+message_id combination. If None, handler is used 
                       for all messages from this vendor.
        """
        if message_id:
            self.vendor_message_handlers[(vendor_id, message_id)] = handler
            self.logger.info(f"Registered handler for vendor: {vendor_id}, message_id: {message_id}")
        else:
            self.vendor_handlers[vendor_id] = handler
            self.logger.info(f"Registered handler for vendor: {vendor_id} (all messages)")
    
    async def handle_data_transfer(
        self,
        charger_id: str,
        org_name: str,
        vendor_id: str,
        message_id: Optional[str] = None,
        data: Optional[str] = None
    ) -> tuple[str, Optional[str]]:
        """
        Handle a DataTransfer command from a charger.
        
        Args:
            charger_id: ID of the charger sending the data
            org_name: Organization name
            vendor_id: Vendor identifier
            message_id: Optional message identifier
            data: Optional data payload (can be JSON string or plain text)
        
        Returns:
            Tuple of (status, response_data)
            - status: "Accepted", "Rejected", "UnknownMessageId", "UnknownVendorId", or "NotImplemented"
            - response_data: Optional response data to send back to charger
        """
        self.logger.info(
            "DataTransfer from charger %s (org: %s, vendor: %s, message_id: %s)",
            charger_id,
            org_name,
            vendor_id,
            message_id or "none"
        )
        
        # Check if DataTransfer is enabled
        global_config = self._get_global_config()
        if global_config is not None:
            enabled = global_config.get("enabled", True)
            if not enabled:
                self.logger.info(
                    "DataTransfer is disabled in configuration for charger %s",
                    charger_id
                )
                return DataTransferStatus.NOT_IMPLEMENTED, None
        
        # Store the transfer for logging
        transfer_record = {
            "charger_id": charger_id,
            "org_name": org_name,
            "vendor_id": vendor_id,
            "message_id": message_id,
            "data": data,
            "timestamp": None  # Will be set by caller if needed
        }
        self.received_transfers.append(transfer_record)
        
        # First, check for vendor+message_id specific handler
        if message_id:
            handler_key = (vendor_id, message_id)
            if handler_key in self.vendor_message_handlers:
                try:
                    handler = self.vendor_message_handlers[handler_key]
                    status, response_data = await handler(charger_id, org_name, message_id, data)
                    self.logger.info(
                        "Vendor+Message handler processed DataTransfer (vendor=%s, message_id=%s): status=%s",
                        vendor_id,
                        message_id,
                        status
                    )
                    return status, response_data
                except Exception as e:
                    self.logger.error(
                        "Error in vendor+message handler for %s/%s: %s",
                        vendor_id,
                        message_id,
                        e,
                        exc_info=True
                    )
                    return DataTransferStatus.REJECTED, None
        
        # Check if we have a registered handler for this vendor (all messages)
        if vendor_id in self.vendor_handlers:
            try:
                handler = self.vendor_handlers[vendor_id]
                status, response_data = await handler(charger_id, org_name, message_id, data)
                self.logger.info(
                    "Vendor handler processed DataTransfer (vendor=%s): status=%s",
                    vendor_id,
                    status
                )
                return status, response_data
            except Exception as e:
                self.logger.error(
                    "Error in vendor handler for %s: %s",
                    vendor_id,
                    e,
                    exc_info=True
                )
                return DataTransferStatus.REJECTED, None
        
        # Validate vendor_id and message_id against known lists (system-wide)
        validation_result = self._validate_vendor_and_message(vendor_id, message_id)
        if not validation_result["valid"]:
            self.logger.warning(
                "DataTransfer validation failed: vendor=%s, message_id=%s, reason=%s",
                vendor_id,
                message_id or "none",
                validation_result["reason"]
            )
            return validation_result["status"], None
        
        # Check configuration for vendor/message_id mappings (system-wide)
        vendor_config = self._get_vendor_config(vendor_id)
        if vendor_config:
            # Vendor is configured - process according to config
            return await self._handle_configured_vendor(
                charger_id, org_name, vendor_id, message_id, data, vendor_config
            )
        
        # Check for vendor+message_id specific config (system-wide)
        if message_id:
            vendor_message_key = f"{vendor_id}:{message_id}"
            global_config = self._get_global_config()
            if global_config:
                vendor_messages = global_config.get("vendor_messages", {})
                if vendor_message_key in vendor_messages:
                    config = vendor_messages[vendor_message_key]
                    auto_accept = config.get("auto_accept", True)
                    if auto_accept:
                        processed_data = self._process_data(data)
                        return DataTransferStatus.ACCEPTED, processed_data
                    else:
                        return DataTransferStatus.REJECTED, None
        
        # Default handling: vendor and message are validated, accept with data processing
        processed_data = self._process_data(data)
        return DataTransferStatus.ACCEPTED, processed_data
    
    def _get_vendor_config(self, vendor_id: str) -> Optional[Dict[str, Any]]:
        """
        Get vendor-specific configuration from system-wide config.
        """
        global_config = self._get_global_config()
        if not global_config:
            return None
        
        # Check for vendor-specific config
        vendors = global_config.get("vendors", {})
        if vendor_id in vendors:
            return vendors[vendor_id]
        
        return None
    
    async def _handle_configured_vendor(
        self,
        charger_id: str,
        org_name: str,
        vendor_id: str,
        message_id: Optional[str],
        data: Optional[str],
        config: Dict[str, Any]
    ) -> tuple[str, Optional[str]]:
        """
        Handle DataTransfer for a configured vendor.
        """
        # Check if message_id is required for this vendor
        if config.get("require_message_id", False) and not message_id:
            self.logger.warning(
                "Message ID required for vendor %s but not provided",
                vendor_id
            )
            return DataTransferStatus.UNKNOWN_MESSAGE_ID, None
        
        # Check if specific message_id is allowed
        allowed_messages = config.get("allowed_message_ids", None)
        if allowed_messages is not None and message_id not in allowed_messages:
            self.logger.warning(
                "Message ID %s not allowed for vendor %s",
                message_id,
                vendor_id
            )
            return DataTransferStatus.UNKNOWN_MESSAGE_ID, None
        
        # Process data according to config
        auto_accept = config.get("auto_accept", True)
        if auto_accept:
            processed_data = self._process_data(data)
            return DataTransferStatus.ACCEPTED, processed_data
        else:
            return DataTransferStatus.REJECTED, None
    
    def _get_known_vendors(self) -> list[str]:
        """
        Get list of known vendor IDs from system-wide configuration.
        """
        global_config = self._get_global_config()
        if global_config:
            # Get vendors from vendor config
            vendors = global_config.get("vendors", {})
            if vendors:
                vendor_list = list(vendors.keys())
            else:
                vendor_list = []
            
            # Also include known_vendors list
            if "known_vendors" in global_config:
                known = global_config["known_vendors"]
                if isinstance(known, list):
                    # Merge and deduplicate
                    vendor_list = list(set(vendor_list + known))
            
            return vendor_list
        
        # Default known vendors (can be extended)
        return []
    
    def _get_known_message_ids(self) -> list[str]:
        """
        Get list of known message IDs from system-wide configuration.
        """
        global_config = self._get_global_config()
        if global_config:
            message_ids = []
            
            # Get message_ids from config
            if "known_message_ids" in global_config:
                known = global_config["known_message_ids"]
                if isinstance(known, list):
                    message_ids.extend(known)
            
            # Also check vendor_messages for all message IDs
            vendor_messages = global_config.get("vendor_messages", {})
            if vendor_messages:
                # Extract message IDs from vendor_messages keys (format: "vendor_id:message_id")
                for key in vendor_messages.keys():
                    if isinstance(key, str) and ":" in key:
                        _, msg_id = key.split(":", 1)
                        message_ids.append(msg_id)
            
            # Also check vendors config for allowed_message_ids
            vendors = global_config.get("vendors", {})
            for vendor_config in vendors.values():
                if isinstance(vendor_config, dict):
                    allowed = vendor_config.get("allowed_message_ids", [])
                    if isinstance(allowed, list):
                        message_ids.extend(allowed)
            
            # Deduplicate and return
            return list(set(message_ids))
        
        # Default known message IDs (can be extended)
        return []
    
    def _validate_vendor_and_message(
        self, 
        vendor_id: str, 
        message_id: Optional[str]
    ) -> Dict[str, Any]:
        """
        Validate that vendor_id and message_id are in the known lists.
        
        Returns:
            Dict with keys:
            - valid: bool
            - status: DataTransferStatus if invalid
            - reason: str describing why validation failed
        """
        # Get known vendors and message IDs from global config
        known_vendors = self._get_known_vendors()
        known_message_ids = self._get_known_message_ids()
        
        # Check if vendor validation is enabled (from global config)
        global_config = self._get_global_config()
        validate_vendors = global_config.get("validate_vendors", True) if global_config else True
        validate_messages = global_config.get("validate_message_ids", True) if global_config else True
        
        # Validate vendor ID
        if validate_vendors and known_vendors:
            if vendor_id not in known_vendors:
                return {
                    "valid": False,
                    "status": DataTransferStatus.UNKNOWN_VENDOR_ID,
                    "reason": f"Vendor ID '{vendor_id}' not in known vendors list"
                }
        
        # Validate message ID (if provided)
        if message_id and validate_messages and known_message_ids:
            if message_id not in known_message_ids:
                return {
                    "valid": False,
                    "status": DataTransferStatus.UNKNOWN_MESSAGE_ID,
                    "reason": f"Message ID '{message_id}' not in known message IDs list"
                }
        
        # Check vendor+message_id combination if both lists exist
        if known_vendors and known_message_ids and message_id:
            # Check if vendor allows any message or if message is in allowed list
            vendor_config = self._get_vendor_config(vendor_id)
            if vendor_config:
                allowed_messages = vendor_config.get("allowed_message_ids", None)
                if allowed_messages is not None and message_id not in allowed_messages:
                    return {
                        "valid": False,
                        "status": DataTransferStatus.UNKNOWN_MESSAGE_ID,
                        "reason": f"Message ID '{message_id}' not allowed for vendor '{vendor_id}'"
                    }
        
        return {
            "valid": True,
            "status": None,
            "reason": None
        }
    
    def _get_global_config(self) -> Optional[Dict[str, Any]]:
        """Get system-wide DataTransfer configuration"""
        try:
            if not hasattr(self.broker, "config_data"):
                return None
            
            config_data = getattr(self.broker, "config_data", None)
            if not config_data or not isinstance(config_data, dict):
                return None
            
            data_transfer_config = config_data.get("data_transfer", {})
            # Ensure it's a dict
            if isinstance(data_transfer_config, dict):
                return data_transfer_config
            return {}
        except Exception as e:
            self.logger.warning("Error getting global data_transfer config: %s", e)
            return None
    
    def _process_data(self, data: Optional[str]) -> Optional[str]:
        """
        Process the data payload.
        Attempts to parse JSON, validates structure, etc.
        """
        if not data:
            return None
        
        # Try to parse as JSON
        try:
            parsed = json.loads(data)
            self.logger.debug("DataTransfer data parsed as JSON: %s", parsed)
            # You can add validation/transformation logic here
            return json.dumps(parsed) if isinstance(parsed, (dict, list)) else data
        except json.JSONDecodeError:
            # Not JSON, return as-is
            self.logger.debug("DataTransfer data is plain text: %s", data[:100])
            return data
    
    def get_known_vendors(self) -> list[str]:
        """
        Get list of known vendor IDs from system-wide configuration.
        Public method to retrieve the vendor list.
        """
        return self._get_known_vendors()
    
    def get_known_message_ids(self) -> list[str]:
        """
        Get list of known message IDs from system-wide configuration.
        Public method to retrieve the message ID list.
        """
        return self._get_known_message_ids()
    
    def get_transfer_history(
        self,
        charger_id: Optional[str] = None,
        vendor_id: Optional[str] = None,
        message_id: Optional[str] = None,
        limit: int = 100
    ) -> list[Dict[str, Any]]:
        """
        Get history of received DataTransfer commands.
        
        Args:
            charger_id: Filter by charger ID (optional)
            vendor_id: Filter by vendor ID (optional)
            message_id: Filter by message ID (optional)
            limit: Maximum number of records to return
        
        Returns:
            List of transfer records
        """
        filtered = self.received_transfers
        
        if charger_id:
            filtered = [t for t in filtered if t["charger_id"] == charger_id]
        
        if vendor_id:
            filtered = [t for t in filtered if t["vendor_id"] == vendor_id]
        
        if message_id:
            filtered = [t for t in filtered if t.get("message_id") == message_id]
        
        return filtered[-limit:]


def create_data_transfer_handler(broker) -> DataTransferHandler:
    """Create and configure a DataTransferHandler instance"""
    handler = DataTransferHandler(broker)
    
    # Register any default handlers here
    # Example:
    # handler.register_vendor_handler("VendorX", custom_vendor_handler)
    
    return handler

