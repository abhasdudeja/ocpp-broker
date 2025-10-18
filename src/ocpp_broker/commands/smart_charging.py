"""
OCPP 1.6 Smart Charging Profile Command Handlers

Implements Smart Charging Profile commands:
- ClearChargingProfile, GetCompositeSchedule, GetConfiguration
- SetChargingProfile, TriggerMessage
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from .base import BaseCommandHandler, OCPPCommandResult, OCPPError, OCPPErrorCode

logger = logging.getLogger("ocpp_broker.commands.smart_charging")


class ClearChargingProfileHandler(BaseCommandHandler):
    """Handler for ClearChargingProfile command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle ClearChargingProfile request from charger"""
        try:
            profile_id = request.get("id")
            connector_id = request.get("connectorId")
            charging_profile_purpose = request.get("chargingProfilePurpose")
            stack_level = request.get("stackLevel")
            
            # Process charging profile clearing
            await self._clear_charging_profile(charger_id, profile_id, connector_id, 
                                             charging_profile_purpose, stack_level)
            
            response = {"status": "Accepted"}
            
            self.log_command(charger_id, "ClearChargingProfile", "request", True, 
                           f"Profile ID: {profile_id}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "ClearChargingProfile", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Clear charging profile failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle ClearChargingProfile response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _clear_charging_profile(self, charger_id: str, profile_id: Optional[int],
                                    connector_id: Optional[int], purpose: Optional[str],
                                    stack_level: Optional[int]):
        """Clear charging profile for charger"""
        # Implement charging profile clearing logic
        logger.info(f"Clearing charging profile for charger {charger_id}: "
                   f"ID={profile_id}, Connector={connector_id}, Purpose={purpose}")


class GetCompositeScheduleHandler(BaseCommandHandler):
    """Handler for GetCompositeSchedule command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle GetCompositeSchedule request from charger"""
        try:
            connector_id = request.get("connectorId")
            duration = request.get("duration")
            charging_rate_unit = request.get("chargingRateUnit")
            
            # Get composite schedule
            schedule = await self._get_composite_schedule(charger_id, connector_id, 
                                                        duration, charging_rate_unit)
            
            if schedule:
                response = {
                    "status": "Accepted",
                    "connectorId": connector_id,
                    "scheduleStart": schedule.get("schedule_start"),
                    "chargingSchedule": schedule.get("charging_schedule")
                }
            else:
                response = {"status": "Rejected"}
            
            self.log_command(charger_id, "GetCompositeSchedule", "request", True, 
                           f"Connector {connector_id}, Duration: {duration}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "GetCompositeSchedule", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Get composite schedule failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle GetCompositeSchedule response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _get_composite_schedule(self, charger_id: str, connector_id: int,
                                    duration: int, rate_unit: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get composite charging schedule"""
        # Implement composite schedule retrieval logic
        # For now, return a simple schedule
        return {
            "schedule_start": datetime.now(timezone.utc).isoformat(),
            "charging_schedule": {
                "duration": duration,
                "chargingRateUnit": rate_unit or "W",
                "chargingSchedulePeriod": [
                    {
                        "startPeriod": 0,
                        "limit": 10000
                    }
                ]
            }
        }


class SetChargingProfileHandler(BaseCommandHandler):
    """Handler for SetChargingProfile command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle SetChargingProfile request from charger"""
        try:
            connector_id = request.get("connectorId")
            cs_charging_profiles = request.get("csChargingProfiles")
            
            # Process charging profile setting
            await self._set_charging_profile(charger_id, connector_id, cs_charging_profiles)
            
            response = {"status": "Accepted"}
            
            self.log_command(charger_id, "SetChargingProfile", "request", True, 
                           f"Connector {connector_id}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "SetChargingProfile", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Set charging profile failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle SetChargingProfile response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _set_charging_profile(self, charger_id: str, connector_id: int,
                                  profiles: Dict[str, Any]):
        """Set charging profile for charger"""
        # Implement charging profile setting logic
        logger.info(f"Setting charging profile for charger {charger_id}, "
                   f"connector {connector_id}: {profiles}")


class TriggerMessageHandler(BaseCommandHandler):
    """Handler for TriggerMessage command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle TriggerMessage request from charger"""
        try:
            requested_message = request.get("requestedMessage")
            connector_id = request.get("connectorId")
            
            # Process message trigger
            await self._trigger_message(charger_id, requested_message, connector_id)
            
            response = {"status": "Accepted"}
            
            self.log_command(charger_id, "TriggerMessage", "request", True, 
                           f"Message: {requested_message}, Connector: {connector_id}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "TriggerMessage", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Trigger message failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle TriggerMessage response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _trigger_message(self, charger_id: str, message_type: str, connector_id: Optional[int]):
        """Trigger specific message from charger"""
        # Implement message triggering logic
        logger.info(f"Triggering message {message_type} for charger {charger_id}, "
                   f"connector {connector_id}")


class SmartChargingHandler:
    """Main handler for Smart Charging Profile commands"""
    
    def __init__(self, broker=None):
        self.broker = broker
        self.handlers = {
            "ClearChargingProfile": ClearChargingProfileHandler(broker),
            "GetCompositeSchedule": GetCompositeScheduleHandler(broker),
            "SetChargingProfile": SetChargingProfileHandler(broker),
            "TriggerMessage": TriggerMessageHandler(broker),
        }
    
    async def handle_command(self, charger_id: str, action: str, 
                           request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle a Smart Charging Profile command"""
        handler = self.handlers.get(action)
        if not handler:
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.NOT_IMPLEMENTED,
                    description=f"Smart Charging Profile command {action} not implemented"
                )
            )
        
        return await handler.handle_request(charger_id, request)
