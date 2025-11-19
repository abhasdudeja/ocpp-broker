import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint, call_result, datatypes

logger = logging.getLogger("ocpp_broker.charge_point")


class StarletteWebSocketAdapter:
    """
    Lightweight adapter that exposes the interface expected by the `ocpp`
    library (`recv`, `send`, `close`) while delegating to a FastAPI/Starlette
    WebSocket instance.
    """

    def __init__(self, websocket, validate_messages: bool = False, org_entry: Optional[Dict[str, Any]] = None):
        self._ws = websocket
        self.validate_messages = validate_messages
        self.org_entry = org_entry

    async def recv(self) -> str:
        message = await self._ws.receive_text()
        
        # Validate message if enabled
        if self.validate_messages:
            from .message_validator import validate_ocpp_message
            
            # Get strict mode from org config
            strict_mode = True
            if self.org_entry:
                validation_config = self.org_entry.get("validation", {})
                strict_mode = validation_config.get("strict_mode", True)
            
            is_valid, error_msg, validated_msg = validate_ocpp_message(message, strict_mode=strict_mode)
            
            if not is_valid:
                logger = logging.getLogger("ocpp_broker.charge_point")
                logger.warning(
                    f"❌ Message validation failed: {error_msg}. "
                    f"Message: {message[:200]}"
                )
                # In strict mode, reject invalid messages
                if strict_mode:
                    raise ValueError(f"Invalid OCPP message: {error_msg}")
                # In non-strict mode, log warning but continue
                logger.warning(f"⚠️ Processing invalid message in non-strict mode")
        
        return message

    async def send(self, message: str):
        # Save call results/errors to MongoDB when broker is leader
        try:
            import json
            parsed = json.loads(message)
            if isinstance(parsed, list) and len(parsed) >= 2:
                message_type = parsed[0]
                # Type 3 = CALLRESULT, Type 4 = CALLERROR
                if message_type in [3, 4]:
                    # Get charge_point from the websocket adapter context
                    # We need to find which charge_point this adapter belongs to
                    charge_point = getattr(self, "_charge_point", None)
                    if charge_point:
                        mongodb = getattr(charge_point.broker, "mongodb_service", None)
                        if mongodb and mongodb.is_connected():
                            try:
                                # For call results, we need to track which action this is a response to
                                # This is tricky - we'd need to maintain a mapping of message_id -> action
                                # For now, save with a generic action name
                                action = "CallResult" if message_type == 3 else "CallError"
                                payload = parsed[2] if len(parsed) > 2 else {}
                                
                                if message_type == 4:  # CALLERROR
                                    error_code = parsed[2] if len(parsed) > 2 else "Unknown"
                                    error_description = parsed[3] if len(parsed) > 3 else ""
                                    error_details = parsed[4] if len(parsed) > 4 else {}
                                    payload = {
                                        "error_code": error_code,
                                        "error_description": error_description,
                                        "error_details": error_details
                                    }
                                
                                await mongodb.save_ocpp_message(
                                    org_name=charge_point.org_name,
                                    charger_id=charge_point.id,
                                    message_type="call_result" if message_type == 3 else "call_error",
                                    action=action,
                                    payload=payload,
                                    direction="broker_to_charger",
                                    message_id=parsed[1] if len(parsed) > 1 else None
                                )
                            except Exception as e:
                                logger.debug(f"Failed to save call result/error to MongoDB: {e}")
        except Exception:
            pass  # Ignore parsing errors
        
        await self._ws.send_text(message)

    async def close(self, code: int = 1000, reason: str | None = None):
        await self._ws.close(code=code, reason=reason)

    @property
    def subprotocol(self) -> Optional[str]:
        return self._ws.headers.get("sec-websocket-protocol")

    @property
    def closed(self) -> bool:
        client_state = getattr(self._ws, "client_state", None)
        if client_state is None:
            return False
        return getattr(client_state, "name", None) == "DISCONNECTED"


class BrokerChargePoint(OcppChargePoint):
    """
    ChargePoint implementation that relies on the upstream `ocpp` library for
    message parsing/validation while delegating business logic to broker
    services (tag manager, registry, etc.).
    """

    def __init__(self, charge_point_id: str, websocket, broker, org_name: str):
        super().__init__(charge_point_id, websocket)
        self.broker = broker
        self.org_name = org_name
        self.logger = logging.getLogger(f"ocpp_broker.charge_point.{charge_point_id}")

    # ------------------------------------------------------------------
    # Core Profile handlers
    # ------------------------------------------------------------------
    @on("BootNotification")
    async def on_boot_notification(self, charge_point_model: str, charge_point_vendor: str, **payload):
        await self._register_charger()
        interval = (
            self.broker.config_data.get("ocpp", {})
            .get("commands", {})
            .get("core", {})
            .get("heartbeat_interval", 300)
        )

        self.logger.info(
            "BootNotification received (%s / %s) payload=%s",
            charge_point_vendor,
            charge_point_model,
            payload,
        )

        # Save to MongoDB when broker is leader
        await self._save_to_mongodb_if_enabled(
            "boot_notification",
            {
                "charge_point_model": charge_point_model,
                "charge_point_vendor": charge_point_vendor,
                **payload
            }
        )
        
        # Save boot notification data
        mongodb = getattr(self.broker, "mongodb_service", None)
        if mongodb and mongodb.is_connected():
            await mongodb.save_boot_notification(
                org_name=self.org_name,
                charger_id=self.id,
                charge_point_model=charge_point_model,
                charge_point_vendor=charge_point_vendor,
                firmware_version=payload.get("firmware_version"),
                iccid=payload.get("iccid"),
                imsi=payload.get("imsi"),
                meter_type=payload.get("meter_type"),
                meter_serial_number=payload.get("meter_serial_number")
            )

        return call_result.BootNotification(
            current_time=self._now(),
            interval=interval,
            status="Accepted",
        )

    @on("Authorize")
    async def on_authorize(self, id_tag: str, **payload):
        tag_info = await self._authorize_tag(id_tag)
        self.logger.info("Authorize for %s → %s", id_tag, tag_info.status)
        
        # Save to MongoDB when broker is leader
        await self._save_to_mongodb_if_enabled(
            "authorize",
            {"id_tag": id_tag, **payload}
        )
        
        # Save authorization data
        mongodb = getattr(self.broker, "mongodb_service", None)
        if mongodb and mongodb.is_connected():
            # Handle expiry_date - it might be a datetime object or string
            expiry_date_str = None
            if tag_info.expiry_date:
                if hasattr(tag_info.expiry_date, "isoformat"):
                    expiry_date_str = tag_info.expiry_date.isoformat()
                else:
                    expiry_date_str = str(tag_info.expiry_date)
            
            await mongodb.save_authorization(
                org_name=self.org_name,
                charger_id=self.id,
                id_tag=id_tag,
                status=tag_info.status,
                expiry_date=expiry_date_str,
                parent_id_tag=tag_info.parent_id_tag
            )
        
        return call_result.Authorize(id_tag_info=tag_info)

    @on("Heartbeat")
    async def on_heartbeat(self):
        # Update latest heartbeat timestamp (don't save individual messages)
        mongodb = getattr(self.broker, "mongodb_service", None)
        if mongodb and mongodb.is_connected():
            await mongodb.update_heartbeat_timestamp(
                org_name=self.org_name,
                charger_id=self.id
            )
        
        return call_result.Heartbeat(current_time=self._now())
    
    @on("DiagnosticsStatusNotification")
    async def on_diagnostics_status_notification(self, status: str, **payload):
        """
        Handle DiagnosticsStatusNotification from charger.
        Sent when diagnostics upload status changes.
        """
        self.logger.info(
            "DiagnosticsStatusNotification status=%s payload=%s",
            status,
            payload,
        )
        
        # Save to MongoDB when broker is leader
        await self._save_to_mongodb_if_enabled(
            "diagnostics_status_notification",
            {"status": status, **payload}
        )
        
        return call_result.DiagnosticsStatusNotification()
    
    @on("FirmwareStatusNotification")
    async def on_firmware_status_notification(self, status: str, **payload):
        """
        Handle FirmwareStatusNotification from charger.
        Sent when firmware update status changes.
        """
        self.logger.info(
            "FirmwareStatusNotification status=%s payload=%s",
            status,
            payload,
        )
        
        # Save to MongoDB when broker is leader
        await self._save_to_mongodb_if_enabled(
            "firmware_status_notification",
            {"status": status, **payload}
        )
        
        return call_result.FirmwareStatusNotification()

    @on("StatusNotification")
    async def on_status_notification(self, connector_id: int, status: str, **payload):
        self.logger.info(
            "StatusNotification connector=%s status=%s payload=%s",
            connector_id,
            status,
            payload,
        )
        
        # Save to MongoDB when broker is leader
        await self._save_to_mongodb_if_enabled(
            "status_notification",
            {"connector_id": connector_id, "status": status, **payload}
        )
        
        # Save status notification
        mongodb = getattr(self.broker, "mongodb_service", None)
        if mongodb and mongodb.is_connected():
            await mongodb.save_status_notification(
                org_name=self.org_name,
                charger_id=self.id,
                connector_id=connector_id,
                status=status,
                error_code=payload.get("error_code"),
                info=payload.get("info"),
                vendor_id=payload.get("vendor_id"),
                vendor_error_code=payload.get("vendor_error_code")
            )
        
        return call_result.StatusNotification()

    @on("MeterValues")
    async def on_meter_values(self, connector_id: int, meter_value: Any, **payload):
        readings = len(meter_value) if isinstance(meter_value, list) else 0
        self.logger.info(
            "MeterValues connector=%s count=%s payload=%s", connector_id, readings, payload
        )
        
        # Save to MongoDB when broker is leader
        await self._save_to_mongodb_if_enabled(
            "meter_values",
            {"connector_id": connector_id, "meter_value": meter_value, **payload}
        )
        
        # Save meter values
        mongodb = getattr(self.broker, "mongodb_service", None)
        if mongodb and mongodb.is_connected():
            # Convert meter_value to dict if needed
            meter_value_list = []
            if isinstance(meter_value, list):
                for mv in meter_value:
                    if hasattr(mv, "__dict__"):
                        meter_value_list.append(mv.__dict__)
                    elif isinstance(mv, dict):
                        meter_value_list.append(mv)
                    else:
                        meter_value_list.append({"value": str(mv)})
            else:
                meter_value_list = [{"value": str(meter_value)}]
            
            await mongodb.save_meter_values(
                org_name=self.org_name,
                charger_id=self.id,
                connector_id=connector_id,
                transaction_id=payload.get("transaction_id"),
                meter_value=meter_value_list
            )
        
        return call_result.MeterValues()

    @on("StartTransaction")
    async def on_start_transaction(self, connector_id: int, id_tag: str, **payload):
        transaction_id = self.broker.next_transaction_id()
        self.logger.info(
            "StartTransaction connector=%s id_tag=%s transaction=%s payload=%s",
            connector_id,
            id_tag,
            transaction_id,
            payload,
        )
        tag_info = await self._authorize_tag(id_tag)
        
        # Save to MongoDB when broker is leader
        await self._save_to_mongodb_if_enabled(
            "start_transaction",
            {"connector_id": connector_id, "id_tag": id_tag, "transaction_id": transaction_id, **payload}
        )
        
        # Save transaction
        mongodb = getattr(self.broker, "mongodb_service", None)
        if mongodb and mongodb.is_connected():
            await mongodb.save_transaction(
                org_name=self.org_name,
                charger_id=self.id,
                transaction_id=transaction_id,
                connector_id=connector_id,
                id_tag=id_tag,
                meter_start=payload.get("meter_start"),
                reservation_id=payload.get("reservation_id"),
                transaction_type="start"
            )
        
        return call_result.StartTransaction(
            transaction_id=transaction_id,
            id_tag_info=tag_info,
        )

    @on("StopTransaction")
    async def on_stop_transaction(self, transaction_id: int, **payload):
        self.logger.info("StopTransaction transaction_id=%s payload=%s", transaction_id, payload)
        id_tag = payload.get("id_tag")
        tag_info = await self._authorize_tag(id_tag) if id_tag else datatypes.IdTagInfo(status="Invalid")
        
        # Save to MongoDB when broker is leader
        await self._save_to_mongodb_if_enabled(
            "stop_transaction",
            {"transaction_id": transaction_id, **payload}
        )
        
        # Save transaction stop
        mongodb = getattr(self.broker, "mongodb_service", None)
        if mongodb and mongodb.is_connected():
            connector_id = payload.get("connector_id", 0)
            await mongodb.save_transaction(
                org_name=self.org_name,
                charger_id=self.id,
                transaction_id=transaction_id,
                connector_id=connector_id,
                id_tag=id_tag or "",
                meter_start=payload.get("meter_stop"),
                transaction_type="stop"
            )
        
        return call_result.StopTransaction(id_tag_info=tag_info)

    @on("DataTransfer")
    async def on_data_transfer(self, vendor_id: str, message_id: str = None, data: str = None, **payload):
        """
        Handle DataTransfer command from charger.
        DataTransfer allows chargers to send vendor-specific data to the central system.
        """
        self.logger.info(
            "DataTransfer received from %s: vendor=%s message_id=%s data_length=%s",
            self.id,
            vendor_id,
            message_id or "none",
            len(data) if data else 0
        )
        
        try:
            # Get or create DataTransfer handler
            handler = None
            if hasattr(self.broker, "data_transfer_handler"):
                handler = getattr(self.broker, "data_transfer_handler", None)
            
            if handler is None:
                try:
                    from .data_transfer_handler import create_data_transfer_handler
                    handler = create_data_transfer_handler(self.broker)
                    self.broker.data_transfer_handler = handler
                    self.logger.debug("Created DataTransfer handler for charger %s", self.id)
                except Exception as create_error:
                    self.logger.error(
                        "Failed to create DataTransfer handler for charger %s: %s",
                        self.id,
                        create_error,
                        exc_info=True
                    )
                    return call_result.DataTransfer(
                        status="Rejected",
                        data=None
                    )
            
            # Safety check: ensure handler was created successfully
            if handler is None:
                self.logger.error(
                    "DataTransfer handler is None for charger %s after creation attempt",
                    self.id
                )
                return call_result.DataTransfer(
                    status="Rejected",
                    data=None
                )
            
            # Process the DataTransfer
            # Additional safety check before calling handle_data_transfer
            if handler is None or not hasattr(handler, "handle_data_transfer"):
                self.logger.error(
                    "DataTransfer handler for charger %s is invalid (handler=%s)",
                    self.id,
                    type(handler).__name__ if handler else "None"
                )
                return call_result.DataTransfer(
                    status="Rejected",
                    data=None
                )
            
            status, response_data = await handler.handle_data_transfer(
                charger_id=self.id,
                org_name=self.org_name,
                vendor_id=vendor_id,
                message_id=message_id,
                data=data
            )
            
            # Save to MongoDB when broker is leader
            await self._save_to_mongodb_if_enabled(
                "data_transfer",
                {
                    "vendor_id": vendor_id,
                    "message_id": message_id,
                    "data": data,
                    "status": status,
                    **payload
                }
            )
            
            # Save data transfer
            mongodb = getattr(self.broker, "mongodb_service", None)
            if mongodb and mongodb.is_connected():
                await mongodb.save_data_transfer(
                    org_name=self.org_name,
                    charger_id=self.id,
                    vendor_id=vendor_id,
                    message_id=message_id,
                    data=data,
                    status=status
                )
            
            # Return OCPP-compliant response
            return call_result.DataTransfer(
                status=status,
                data=response_data
            )
        except AttributeError as attr_error:
            self.logger.error(
                "AttributeError processing DataTransfer from %s: %s (handler=%s)",
                self.id,
                attr_error,
                type(handler).__name__ if handler else "None",
                exc_info=True
            )
            # Return rejected status on error instead of letting it bubble up
            return call_result.DataTransfer(
                status="Rejected",
                data=None
            )
        except Exception as e:
            self.logger.error(
                "Error processing DataTransfer from %s: %s",
                self.id,
                e,
                exc_info=True
            )
            # Return rejected status on error instead of letting it bubble up
            return call_result.DataTransfer(
                status="Rejected",
                data=None
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _register_charger(self):
        try:
            registry = self.broker.get_registry(self.org_name)
            await registry.update_from_backend(f"{self.org_name}-local", [self.id])
        except Exception as exc:
            self.logger.warning("Unable to register charger in registry: %s", exc)

    async def _authorize_tag(self, id_tag: Optional[str]) -> datatypes.IdTagInfo:
        """
        Authorize a tag. Tags must exist in the system - no fallback mechanism.
        Returns Invalid status if tag manager is unavailable or tag is not found.
        """
        if not id_tag:
            self.logger.warning("Authorization attempted with empty id_tag")
            return datatypes.IdTagInfo(status="Invalid")

        tag_manager = getattr(self.broker, "tag_manager", None)
        if not tag_manager:
            self.logger.error(
                "Tag manager not available for org %s - cannot authorize tag %s",
                self.org_name,
                id_tag
            )
            return datatypes.IdTagInfo(status="Invalid")

        result = await tag_manager.authorize_tag(self.org_name, id_tag)
        return datatypes.IdTagInfo(
            status=result.get("status", "Invalid"),
            expiry_date=result.get("expiry_date"),
            parent_id_tag=result.get("parent_id_tag"),
        )

    async def _save_to_mongodb_if_enabled(self, action: str, payload: Dict[str, Any]):
        """
        Save OCPP message to MongoDB if MongoDB is enabled and connected.
        This is called when broker is acting as leader (backend).
        Note: Heartbeat messages are handled separately and not saved here.
        """
        # Skip heartbeat - it's handled separately with update_heartbeat_timestamp()
        if action.lower() in ["heartbeat", "heartbeats"]:
            return
        
        mongodb = getattr(self.broker, "mongodb_service", None)
        if mongodb and mongodb.is_connected():
            try:
                await mongodb.save_ocpp_message(
                    org_name=self.org_name,
                    charger_id=self.id,
                    message_type="call",
                    action=action,
                    payload=payload,
                    direction="charger_to_broker"
                )
            except Exception as e:
                self.logger.warning(f"Failed to save {action} to MongoDB: {e}")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

