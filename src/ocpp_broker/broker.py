import asyncio
import json
import logging
from .backend_manager import BackendConnection
from .registry import ChargerRegistry
from .command_router import CommandRouter
from .middleware import process_charger_to_backend
from .config import load_config

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
        self.command_router = CommandRouter(self)
        self.config_data = {}
        self._cfg_path = "config.yaml"

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
            backend_url = org_entry["backends"][0]["url"]  # full URL from config
            backend_conn = BackendConnection(
                broker=self,
                charger_id=charger_id,
                url=backend_url,
                org=org_name
            )

            logger.info(f"🔗 Establishing backend connection for charger {charger_id}...")
            await backend_conn.connect()  # waits until backend connected
            self.org_backends.setdefault(org_name, {})[charger_id] = backend_conn
            logger.info(f"🔗 Backend ready for charger {charger_id}")

        # Register charger
        self.active_chargers[charger_id] = websocket

        try:
            await self._relay_messages(charger_id, websocket, backend_conn)
        except Exception as e:
            logger.error(f"Error in relay loop for {charger_id}: {e}")
        finally:
            await self._cleanup(charger_id, org_name, backend_conn)

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
