import asyncio
import json
import logging
from .backend_manager import BackendConnection
from .registry import ChargerRegistry
from .command_router import CommandRouter
from .command_router_v2 import OCPPCommandRouter
from .middleware import process_charger_to_backend
from .config import load_config
from .validation import OCPPValidator

logger = logging.getLogger("ocpp_broker.broker")


class OcppBroker:
    """
    Bi-directional OCPP Broker.
    Each charger connection has its own backend WebSocket connection.
    The broker stays connected to both ends and relays messages both ways.
    """

    def __init__(self):
        self.org_backends = {}       # org -> {charger_id: BackendConnection}
        self.active_chargers = {}    # charger_id -> FastAPI WebSocket
        self.org_registries = {}     # org -> ChargerRegistry
        self.command_router = CommandRouter(self)  # Legacy router
        self.ocpp_router = OCPPCommandRouter(self)  # Enhanced OCPP 1.6 router
        self.validator = OCPPValidator()  # OCPP message validator
        self.config_data = {}
        self._cfg_path = "config.yaml"
        self._use_ocpp_router = True  # Flag to enable enhanced OCPP routing

    # ------------------------------------------------------------------
    # Configuration and initialization
    # ------------------------------------------------------------------
    async def load_config(self):
        self.config_data = load_config(self._cfg_path)
        logger.info(f"Loaded configuration for {len(self.config_data.get('organizations', []))} organizations.")

    async def ensure_org_initialized(self, org_name: str):
        if org_name not in self.org_registries:
            self.org_registries[org_name] = ChargerRegistry()

    # ------------------------------------------------------------------
    # Handle new charger connection
    # ------------------------------------------------------------------
    async def handle_charger(self, websocket, path):
        """Handle new charger connection and create backend link."""
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) != 2:
            await websocket.close(code=4000, reason="Invalid path format")
            return

        org_name, charger_id = parts
        if not self.config_data:
            await self.load_config()

        org_entry = next(
            (o for o in self.config_data.get("organizations", []) if o.get("name") == org_name),
            None
        )
        if not org_entry:
            logger.warning(f"❌ Rejected charger {charger_id}: unknown organization '{org_name}'.")
            await websocket.close(code=4002, reason="Unknown organization")
            return

        connect_to_backend = org_entry.get("connect_to_backend", False)
        await self.ensure_org_initialized(org_name)

        logger.info(f"✅ Accepted charger {charger_id} for org '{org_name}' "
                    f"(backend connection: {'enabled' if connect_to_backend else 'disabled'})")

        backend_conn = None
        if connect_to_backend:
            backend_config = org_entry["backends"][0]  # Get first backend config
            backend_url = backend_config["url"]
            is_leader = backend_config.get("leader", False)  # Get leader status from config
            backend_conn = BackendConnection(
                broker=self,
                charger_id=charger_id,
                url=backend_url,
                org=org_name,
                is_leader=is_leader
            )

            logger.info(f"🔗 Establishing backend connection for charger {charger_id}...")
            await backend_conn.connect()  # waits until backend connected
            self.org_backends.setdefault(org_name, {})[charger_id] = backend_conn
            logger.info(f"🔗 Backend ready for charger {charger_id}")

        # Register charger
        self.active_chargers[charger_id] = websocket

        try:
            if connect_to_backend:
                # Traditional mode: relay messages between charger and external backend
                await self._relay_messages(charger_id, websocket, backend_conn)
            else:
                # Broker-as-backend mode: handle OCPP commands directly
                await self._handle_charger_directly(charger_id, websocket, org_name)
        except Exception as e:
            logger.error(f"Error in message handling for {charger_id}: {e}")
        finally:
            await self._cleanup(charger_id, org_name, backend_conn)

    # ------------------------------------------------------------------
    # Broker-as-backend mode: handle OCPP commands directly
    # ------------------------------------------------------------------
    async def _handle_charger_directly(self, charger_id: str, charger_ws, org_name: str):
        """
        Handle charger messages directly when broker acts as backend.
        This mode processes OCPP commands locally without forwarding to external backends.
        """
        logger.info(f"🎯 Broker acting as backend for charger {charger_id} (org: {org_name})")
        
        while True:
            try:
                # Receive message from charger
                msg = await charger_ws.receive_text()
                logger.info(f"[{charger_id}] ← Received: {msg[:200]}...")
                
                # Process the OCPP message using the enhanced router
                if self._use_ocpp_router:
                    response = await self.ocpp_router.route_charger_message(charger_id, msg)
                    if response:
                        # Send response back to charger
                        await charger_ws.send_text(response)
                        logger.info(f"[{charger_id}] → Sent response: {response[:200]}...")
                else:
                    # Use legacy processing for backward compatibility
                    await self._process_legacy_message(charger_id, charger_ws, msg)
                    
            except Exception as e:
                logger.error(f"Error handling charger {charger_id} directly: {e}")
                break
    
    async def _process_legacy_message(self, charger_id: str, charger_ws, msg: str):
        """Process message using legacy logic when OCPP router is disabled"""
        try:
            import json
            data = json.loads(msg)
            
            if isinstance(data, list) and len(data) >= 3:
                message_type = data[0]
                message_id = data[1]
                
                if message_type == 2:  # OCPP Call
                    action = data[2]
                    payload = data[3] if len(data) > 3 else {}
                    
                    # Handle specific OCPP actions
                    response = await self._handle_ocpp_action(charger_id, action, payload)
                    if response:
                        # Send CallResult response
                        call_result = [3, message_id, response]
                        await charger_ws.send_text(json.dumps(call_result))
                        logger.info(f"[{charger_id}] → Legacy response sent for {action}")
                        
        except Exception as e:
            logger.error(f"Error processing legacy message for {charger_id}: {e}")
    
    async def _handle_ocpp_action(self, charger_id: str, action: str, payload: dict) -> dict:
        """Handle OCPP actions when broker acts as backend"""
        try:
            if action == "BootNotification":
                return {
                    "currentTime": "2023-01-01T12:00:00Z",
                    "interval": 300,
                    "status": "Accepted"
                }
            elif action == "Heartbeat":
                return {
                    "currentTime": "2023-01-01T12:00:00Z"
                }
            elif action == "Authorize":
                return {
                    "idTagInfo": {
                        "status": "Accepted"
                    }
                }
            elif action == "StatusNotification":
                return {}  # No response payload
            elif action == "MeterValues":
                return {}  # No response payload
            elif action == "StartTransaction":
                return {
                    "transactionId": 12345,
                    "idTagInfo": {
                        "status": "Accepted"
                    }
                }
            elif action == "StopTransaction":
                return {
                    "idTagInfo": {
                        "status": "Accepted"
                    }
                }
            else:
                logger.warning(f"Unhandled OCPP action: {action}")
                return {
                    "status": "NotImplemented"
                }
                
        except Exception as e:
            logger.error(f"Error handling OCPP action {action}: {e}")
            return {
                "status": "InternalError"
            }

    # ------------------------------------------------------------------
    # Relay messages both ways between charger and backend
    # ------------------------------------------------------------------
    async def _relay_messages(self, charger_id: str, charger_ws, backend_conn):
        """Relay messages both directions concurrently."""
        if backend_conn is None or backend_conn.websocket is None:
            logger.warning(f"⚠️ No backend connection for charger {charger_id}")
            return

        backend_ws = backend_conn.websocket
        logger.info(f"🚀 Relay active for charger {charger_id}")

        async def from_charger():
            """Forward charger → backend messages."""
            while True:
                try:
                    msg = await charger_ws.receive_text()
                    
                    if self._use_ocpp_router:
                        # Use enhanced OCPP 1.6 router
                        response = await self.ocpp_router.route_charger_message(charger_id, msg)
                        if response:
                            # Send response back to charger
                            await charger_ws.send_text(response)
                            logger.info(f"[{charger_id}] ← OCPP response sent")
                        
                        # Also forward to backend if connected
                        if backend_ws:
                            msg_out, parsed = await process_charger_to_backend(charger_id, msg)
                            if parsed and isinstance(parsed, list) and len(parsed) >= 3:
                                action = parsed[2]
                                logger.info(f"[{charger_id}] → {action} → backend")
                            await backend_ws.send(msg_out)
                    else:
                        # Use legacy router
                        msg_out, parsed = await process_charger_to_backend(charger_id, msg)
                        if parsed and isinstance(parsed, list) and len(parsed) >= 3:
                            action = parsed[2]
                            logger.info(f"[{charger_id}] → {action} → backend")
                        await backend_ws.send(msg_out)
                        
                except Exception as e:
                    logger.info(f"[{charger_id}] charger disconnected: {e}")
                    break

        async def from_backend():
            """Forward backend → charger messages."""
            while True:
                try:
                    msg = await backend_ws.recv()
                    
                    if self._use_ocpp_router:
                        # Use enhanced OCPP 1.6 router for backend messages
                        await self.ocpp_router.route_backend_message(backend_conn, msg)
                    else:
                        # Use legacy routing
                        await charger_ws.send_text(msg)
                        logger.info(f"[{charger_id}] ← from backend")
                        
                except Exception as e:
                    logger.info(f"[{charger_id}] backend disconnected: {e}")
                    break

        await asyncio.gather(from_charger(), from_backend())

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    async def _cleanup(self, charger_id, org_name, backend_conn):
        """Cleanup after disconnect."""
        self.active_chargers.pop(charger_id, None)
        if backend_conn:
            await backend_conn.close()
        if org_name in self.org_backends:
            self.org_backends[org_name].pop(charger_id, None)
        logger.info(f"🧹 Cleaned up charger {charger_id} session.")

    # ------------------------------------------------------------------
    # Registry + backend callbacks
    # ------------------------------------------------------------------
    def get_registry(self, org_name: str):
        return self.org_registries[org_name]

    def _on_backend_connected(self, backend):
        logger.info(f"✅ Backend connected for charger {backend.id} (org={backend.org})")

    def _on_backend_disconnected(self, backend):
        logger.info(f"⚠️ Backend disconnected for charger {backend.id} (org={backend.org})")
    
    # ------------------------------------------------------------------
    # OCPP 1.6 Enhanced Features
    # ------------------------------------------------------------------
    def enable_ocpp_router(self, enabled: bool = True):
        """Enable or disable the enhanced OCPP 1.6 router"""
        self._use_ocpp_router = enabled
        logger.info(f"OCPP 1.6 router {'enabled' if enabled else 'disabled'}")
    
    def get_supported_commands(self):
        """Get list of supported OCPP commands"""
        if self._use_ocpp_router:
            return self.ocpp_router.get_supported_commands()
        return []
    
    def get_commands_by_profile(self, profile: str):
        """Get commands for a specific OCPP profile"""
        if self._use_ocpp_router:
            return self.ocpp_router.get_commands_by_profile(profile)
        return []
    
    async def validate_ocpp_message(self, message: str):
        """Validate an OCPP message"""
        if self._use_ocpp_router:
            return self.validator.validate_message(message)
        return None
