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

    def __init__(self, websocket):
        self._ws = websocket

    async def recv(self) -> str:
        return await self._ws.receive_text()

    async def send(self, message: str):
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

        return call_result.BootNotification(
            current_time=self._now(),
            interval=interval,
            status="Accepted",
        )

    @on("Authorize")
    async def on_authorize(self, id_tag: str, **payload):
        tag_info = await self._authorize_tag(id_tag)
        self.logger.info("Authorize for %s → %s", id_tag, tag_info.status)
        return call_result.Authorize(id_tag_info=tag_info)

    @on("Heartbeat")
    async def on_heartbeat(self):
        return call_result.Heartbeat(current_time=self._now())

    @on("StatusNotification")
    async def on_status_notification(self, connector_id: int, status: str, **payload):
        self.logger.info(
            "StatusNotification connector=%s status=%s payload=%s",
            connector_id,
            status,
            payload,
        )
        return call_result.StatusNotification()

    @on("MeterValues")
    async def on_meter_values(self, connector_id: int, meter_value: Any, **payload):
        readings = len(meter_value) if isinstance(meter_value, list) else 0
        self.logger.info(
            "MeterValues connector=%s count=%s payload=%s", connector_id, readings, payload
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
        return call_result.StartTransaction(
            transaction_id=transaction_id,
            id_tag_info=tag_info,
        )

    @on("StopTransaction")
    async def on_stop_transaction(self, transaction_id: int, **payload):
        self.logger.info("StopTransaction transaction_id=%s payload=%s", transaction_id, payload)
        id_tag = payload.get("id_tag")
        tag_info = await self._authorize_tag(id_tag) if id_tag else datatypes.IdTagInfo(status="Invalid")
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

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

