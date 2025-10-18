"""
OCPP 1.6 Reservation Profile Command Handlers

Implements Reservation Profile commands:
- CancelReservation, ReserveNow
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from .base import BaseCommandHandler, OCPPCommandResult, OCPPError, OCPPErrorCode

logger = logging.getLogger("ocpp_broker.commands.reservation")


class CancelReservationHandler(BaseCommandHandler):
    """Handler for CancelReservation command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle CancelReservation request from charger"""
        try:
            reservation_id = request.get("reservationId")
            
            # Process reservation cancellation
            await self._cancel_reservation(charger_id, reservation_id)
            
            response = {"status": "Accepted"}
            
            self.log_command(charger_id, "CancelReservation", "request", True, 
                           f"Reservation ID: {reservation_id}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "CancelReservation", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Cancel reservation failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle CancelReservation response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _cancel_reservation(self, charger_id: str, reservation_id: int):
        """Cancel reservation for charger"""
        # Implement reservation cancellation logic
        logger.info(f"Cancelling reservation {reservation_id} for charger {charger_id}")


class ReserveNowHandler(BaseCommandHandler):
    """Handler for ReserveNow command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle ReserveNow request from charger"""
        try:
            connector_id = request.get("connectorId")
            expiry_date = request.get("expiryDate")
            id_tag = request.get("idTag")
            parent_id_tag = request.get("parentIdTag")
            reservation_id = request.get("reservationId")
            
            # Process reservation request
            await self._reserve_now(charger_id, connector_id, expiry_date, 
                                  id_tag, parent_id_tag, reservation_id)
            
            response = {"status": "Accepted"}
            
            self.log_command(charger_id, "ReserveNow", "request", True, 
                           f"Connector: {connector_id}, ID Tag: {id_tag}, "
                           f"Reservation: {reservation_id}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "ReserveNow", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Reserve now failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle ReserveNow response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _reserve_now(self, charger_id: str, connector_id: int, expiry_date: str,
                         id_tag: str, parent_id_tag: Optional[str], reservation_id: int):
        """Create reservation for charger"""
        # Implement reservation creation logic
        logger.info(f"Creating reservation {reservation_id} for charger {charger_id}: "
                   f"Connector={connector_id}, ID Tag={id_tag}, "
                   f"Expiry={expiry_date}, Parent={parent_id_tag}")


class ReservationHandler:
    """Main handler for Reservation Profile commands"""
    
    def __init__(self, broker=None):
        self.broker = broker
        self.handlers = {
            "CancelReservation": CancelReservationHandler(broker),
            "ReserveNow": ReserveNowHandler(broker),
        }
    
    async def handle_command(self, charger_id: str, action: str, 
                           request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle a Reservation Profile command"""
        handler = self.handlers.get(action)
        if not handler:
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.NOT_IMPLEMENTED,
                    description=f"Reservation Profile command {action} not implemented"
                )
            )
        
        return await handler.handle_request(charger_id, request)
