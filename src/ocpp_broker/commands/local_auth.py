"""
OCPP 1.6 Local Authorization List Profile Command Handlers

Implements Local Authorization List Profile commands:
- GetLocalListVersion, SendLocalList
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List

from .base import BaseCommandHandler, OCPPCommandResult, OCPPError, OCPPErrorCode

logger = logging.getLogger("ocpp_broker.commands.local_auth")


class GetLocalListVersionHandler(BaseCommandHandler):
    """Handler for GetLocalListVersion command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle GetLocalListVersion request from charger"""
        try:
            # Get local list version
            version = await self._get_local_list_version(charger_id)
            
            response = {"listVersion": version}
            
            self.log_command(charger_id, "GetLocalListVersion", "request", True, 
                           f"Version: {version}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "GetLocalListVersion", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Get local list version failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle GetLocalListVersion response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _get_local_list_version(self, charger_id: str) -> int:
        """Get local authorization list version for charger"""
        # Implement local list version retrieval logic
        # For now, return a default version
        return 1


class SendLocalListHandler(BaseCommandHandler):
    """Handler for SendLocalList command"""
    
    async def handle_request(self, charger_id: str, request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle SendLocalList request from charger"""
        try:
            list_version = request.get("listVersion")
            update_type = request.get("updateType")
            local_authorization_list = request.get("localAuthorizationList", [])
            
            # Process local list update
            await self._send_local_list(charger_id, list_version, update_type, 
                                      local_authorization_list)
            
            response = {"status": "Accepted"}
            
            self.log_command(charger_id, "SendLocalList", "request", True, 
                           f"Version: {list_version}, Type: {update_type}, "
                           f"Entries: {len(local_authorization_list)}")
            return OCPPCommandResult(success=True, response=response)
            
        except Exception as e:
            self.log_command(charger_id, "SendLocalList", "request", False, str(e))
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.INTERNAL_ERROR,
                    description=f"Send local list failed: {str(e)}"
                )
            )
    
    async def handle_response(self, charger_id: str, response: Dict[str, Any]) -> OCPPCommandResult:
        """Handle SendLocalList response from charger"""
        return OCPPCommandResult(success=True)
    
    async def _send_local_list(self, charger_id: str, list_version: int, 
                             update_type: str, authorization_list: List[Dict[str, Any]]):
        """Send local authorization list to charger"""
        # Implement local list sending logic
        logger.info(f"Sending local list to charger {charger_id}: "
                   f"Version={list_version}, Type={update_type}, "
                   f"Entries={len(authorization_list)}")
        
        # Process each authorization entry
        for entry in authorization_list:
            id_tag = entry.get("idTag")
            id_tag_info = entry.get("idTagInfo", {})
            logger.debug(f"Processing authorization entry: {id_tag} -> {id_tag_info}")


class LocalAuthHandler:
    """Main handler for Local Authorization List Profile commands"""
    
    def __init__(self, broker=None):
        self.broker = broker
        self.handlers = {
            "GetLocalListVersion": GetLocalListVersionHandler(broker),
            "SendLocalList": SendLocalListHandler(broker),
        }
    
    async def handle_command(self, charger_id: str, action: str, 
                           request: Dict[str, Any]) -> OCPPCommandResult:
        """Handle a Local Authorization List Profile command"""
        handler = self.handlers.get(action)
        if not handler:
            return OCPPCommandResult(
                success=False,
                error=OCPPError(
                    code=OCPPErrorCode.NOT_IMPLEMENTED,
                    description=f"Local Authorization List Profile command {action} not implemented"
                )
            )
        
        return await handler.handle_request(charger_id, request)
