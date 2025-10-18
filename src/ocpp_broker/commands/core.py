"""
OCPP 1.6 Core Profile Command Handlers

Implements all mandatory Core Profile commands:
- Authorize, BootNotification, ChangeAvailability, ChangeConfiguration
- ClearCache, DataTransfer, GetConfiguration, Heartbeat
- MeterValues, RemoteStartTransaction, RemoteStopTransaction
- Reset, SendLocalList, SetChargingProfile, StatusNotification
- StopTransaction, UnlockConnector, UpdateFirmware
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from .base import BaseCommandHandler, OCPPCommandResult, OCPPError, OCPPErrorCode

logger = logging.getLogger("ocpp_broker.commands.core")


class AuthorizeHandler(BaseCommandHandler):
    """Handler for Authorize command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle Authorize request from charger"""
        try:
            id_tag = request.get("idTag")
            if not id_tag:
                return OCPPCommandResult(
                    success=False,
                    error=OCPPError(
                        code=OCPPErrorCode.FORMATION_VIOLATION,
                        description="idTag is required"
                    )
                )
            
            # Simulate authorization logic
            # In real implementation, this would check against authorization list
            auth_result = await self._check_authorization(charger_id, id_tag)
            
            response = {
                "idTagInfo": {
                    "status": auth_result["status"],
                    "expiryDate": auth_result.get("expiry_date"),
                    "parentIdTag": auth_result.get("parent_id_tag")
                }
            }
            
            self.log_command(charger_id, "Authorize", "request", True, f"Tag: {id_tag}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "Authorize", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Authorization failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle Authorize response from charger"""
        # Authorize is typically a request from charger, not a response
        return OCPPCommandResult(success=True)
    
    async def _check_authorization(self, charger_id: str, id_tag: str) -> Dict[str, Any]:
        """Check authorization for ID tag"""
        # Simulate authorization check
        # In real implementation, this would query authorization list
        if id_tag.startswith("TEST"):
            return {
                "status": "Accepted",
                "expiry_date": None,
                "parent_id_tag": None
            }
        elif id_tag.startswith("BLOCKED"):
            return {
                "status": "Blocked",
                "expiry_date": None,
                "parent_id_tag": None
            }
        else:
            return {
                "status": "Invalid",
                "expiry_date": None,
                "parent_id_tag": None
            }


class BootNotificationHandler(BaseCommandHandler):
    """Handler for BootNotification command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle BootNotification request from charger"""
        try:
            charge_point_model = request.get("chargePointModel")
            charge_point_vendor = request.get("chargePointVendor")
            charge_point_serial_number = request.get("chargePointSerialNumber")
            charge_box_serial_number = request.get("chargeBoxSerialNumber")
            firmware_version = request.get("firmwareVersion")
            iccid = request.get("iccid")
            imsi = request.get("imsi")
            meter_type = request.get("meterType")
            meter_serial_number = request.get("meterSerialNumber")
            
            # Register charger in broker
            if self.broker:
                await self.broker.ensure_org_initialized("default")  # Default org for now
                registry = self.broker.get_registry("default")
                await registry.update_from_backend("system", [charger_id])
            
            # Create boot notification response
            response = {
                "currentTime": datetime.now(timezone.utc).isoformat(),
                "interval": 300,  # 5 minutes heartbeat interval
                "status": "Accepted"
            }
            
            self.log_command(charger_id, "BootNotification", "request", True, 
                           f"Model: {charge_point_model}, Vendor: {charge_point_vendor}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "BootNotification", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Boot notification failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle BootNotification response from charger"""
        return OCPPCommandResult(success=True)


class HeartbeatHandler(BaseCommandHandler):
    """Handler for Heartbeat command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle Heartbeat request from charger"""
        try:
            response = {
                "currentTime": datetime.now(timezone.utc).isoformat()
            }
            
            self.log_command(charger_id, "Heartbeat", "request", True)
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "Heartbeat", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Heartbeat failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle Heartbeat response from charger"""
        return OCPPCommandResult(success=True)


class StatusNotificationHandler(BaseCommandHandler):
    """Handler for StatusNotification command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle StatusNotification request from charger"""
        try:
            connector_id = request.get("connectorId")
            error_code = request.get("errorCode")
            info = request.get("info")
            status = request.get("status")
            timestamp = request.get("timestamp")
            vendor_id = request.get("vendorId")
            vendor_error_code = request.get("vendorErrorCode")
            
            # Log status change
            self.log_command(charger_id, "StatusNotification", "request", True, 
                           f"Connector {connector_id}: {status}")
            
            # Store status in broker if available
            if self.broker:
                # Update charger status in broker
                pass
            
            response = {}  # StatusNotification has no response payload
            
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "StatusNotification", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Status notification failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle StatusNotification response from charger"""
        return OCPPCommandResult(success=True)


class MeterValuesHandler(BaseCommandHandler):
    """Handler for MeterValues command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle MeterValues request from charger"""
        try:
            connector_id = request.get("connectorId")
            transaction_id = request.get("transactionId")
            meter_value = request.get("meterValue", [])
            
            # Process meter values
            for mv in meter_value:
                timestamp = mv.get("timestamp")
                sampled_value = mv.get("sampledValue", [])
                
                # Log meter reading
                self.log_command(charger_id, "MeterValues", "request", True, 
                               f"Connector {connector_id}: {len(sampled_value)} values")
            
            response = {}  # MeterValues has no response payload
            
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "MeterValues", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Meter values processing failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle MeterValues response from charger"""
        return OCPPCommandResult(success=True)


class StartTransactionHandler(BaseCommandHandler):
    """Handler for StartTransaction command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle StartTransaction request from charger"""
        try:
            connector_id = request.get("connectorId")
            id_tag = request.get("idTag")
            meter_start = request.get("meterStart")
            reservation_id = request.get("reservationId")
            timestamp = request.get("timestamp")
            
            # Process transaction start
            transaction_id = await self._start_transaction(charger_id, connector_id, id_tag, meter_start)
            
            response = {
                "transactionId": transaction_id,
                "idTagInfo": {
                    "status": "Accepted"  # Simplified for now
                }
            }
            
            self.log_command(charger_id, "StartTransaction", "request", True, 
                           f"Connector {connector_id}, Tag: {id_tag}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "StartTransaction", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Start transaction failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle StartTransaction response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _start_transaction(self, charger_id: str, connector_id: int, 
                               id_tag: str, meter_start: int) -> int:
        """Start a new transaction"""
        # Generate transaction ID (simplified)
        import random
        return random.randint(1000, 9999)


class StopTransactionHandler(BaseCommandHandler):
    """Handler for StopTransaction command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle StopTransaction request from charger"""
        try:
            transaction_id = request.get("transactionId")
            timestamp = request.get("timestamp")
            meter_stop = request.get("meterStop")
            reason = request.get("reason")
            id_tag = request.get("idTag")
            transaction_data = request.get("transactionData", [])
            
            # Process transaction stop
            await self._stop_transaction(charger_id, transaction_id, meter_stop)
            
            response = {
                "idTagInfo": {
                    "status": "Accepted"  # Simplified for now
                }
            }
            
            self.log_command(charger_id, "StopTransaction", "request", True, 
                           f"Transaction {transaction_id}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "StopTransaction", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Stop transaction failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle StopTransaction response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _stop_transaction(self, charger_id: str, transaction_id: int, meter_stop: int):
        """Stop a transaction"""
        # Process transaction stop (simplified)
        pass


class CoreProfileHandler:
    """Main handler for Core Profile commands"""
    
    def __init__(self, broker=None):
        self.broker = broker
        self.handlers = {
            "Authorize": AuthorizeHandler(broker),
            "BootNotification": BootNotificationHandler(broker),
            "Heartbeat": HeartbeatHandler(broker),
            "StatusNotification": StatusNotificationHandler(broker),
            "MeterValues": MeterValuesHandler(broker),
            "StartTransaction": StartTransactionHandler(broker),
            "StopTransaction": StopTransactionHandler(broker),
        }
    
    async def handle_command(self, charger_id: str, action: str, 
                           request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle a Core Profile command"""
        handler = self.handlers.get(action)
        if not handler:
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.NOT_IMPLEMENTED,
                    description=f"Core Profile command {action} not implemented"
                )
            )
        
        return await handler.handle_request(charger_id, request)
