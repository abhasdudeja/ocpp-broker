import logging
import random
from typing import Dict, Optional

from .backend_manager import BackendConnection
from .registry import ChargerRegistry
from .config import load_config
from .tag_manager import TagManager
from .session import ChargerSession

logger = logging.getLogger("ocpp_broker.broker")


class OcppBroker:
    """
    Bi-directional OCPP Broker backed by the upstream `ocpp` library.
    Each charger connection can either be relayed to an upstream backend or be
    handled locally via a BrokerChargePoint instance.
    """

    def __init__(self):
        self.org_backends: Dict[str, Dict[str, BackendConnection]] = {}
        self.org_registries: Dict[str, ChargerRegistry] = {}
        self.sessions: Dict[str, ChargerSession] = {}
        self.tag_manager: Optional[TagManager] = None
        self.data_transfer_handler = None  # Will be created on first use
        self.config_data: Dict[str, object] = {}
        self._cfg_path = "config.yaml"
        self._transaction_seed = random.randint(1000, 9999)

    # ------------------------------------------------------------------
    # Configuration and initialization
    # ------------------------------------------------------------------
    async def load_config(self):
        self.config_data = load_config(self._cfg_path)
        logger.info(
            "Loaded configuration for %s organizations.",
            len(self.config_data.get("organizations", [])),
        )

    async def ensure_org_initialized(self, org_name: str):
        if org_name not in self.org_registries:
            self.org_registries[org_name] = ChargerRegistry()

        if self.tag_manager is None:
            self.tag_manager = TagManager(self.config_data)
            if self.tag_manager.is_enabled():
                logger.info("Tag management enabled and initialized")
            else:
                logger.info(
                    "Tag management disabled - no organizations with tag management enabled"
                )

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
            None,
        )
        if not org_entry:
            logger.warning(
                "❌ Rejected charger %s: unknown organization '%s'.", charger_id, org_name
            )
            await websocket.close(code=4002, reason="Unknown organization")
            return

        connect_to_backend = org_entry.get("connect_to_backend", False)
        await self.ensure_org_initialized(org_name)

        logger.info(
            "✅ Accepted charger %s for org '%s' (backend connection: %s)",
            charger_id,
            org_name,
            "enabled" if connect_to_backend else "disabled",
        )

        session = ChargerSession(
            broker=self,
            charger_id=charger_id,
            org_name=org_name,
            org_entry=org_entry,
            websocket=websocket,
        )
        self.sessions[charger_id] = session

        try:
            await session.start()
        except Exception as exc:
            logger.exception("Error in message handling for %s: %s", charger_id, exc)
        finally:
            await session.close()
            self.sessions.pop(charger_id, None)
            logger.info("🧹 Cleaned up charger %s session.", charger_id)

    # ------------------------------------------------------------------
    # Helpers + backend callbacks
    # ------------------------------------------------------------------
    def get_registry(self, org_name: str) -> ChargerRegistry:
        return self.org_registries[org_name]

    def get_org_for_charger(self, charger_id: str) -> Optional[str]:
        session = self.sessions.get(charger_id)
        return session.org_name if session else None

    def next_transaction_id(self) -> int:
        self._transaction_seed += 1
        return self._transaction_seed

    async def forward_backend_message(self, backend_conn: BackendConnection, message: str):
        """Deliver backend messages to the connected charger websocket."""
        session = self.sessions.get(backend_conn.id)
        if not session:
            logger.warning(
                "Cannot deliver backend message to %s: charger not connected", backend_conn.id
            )
            return

        if not backend_conn.is_leader:
            logger.warning(
                "Blocked follower command from backend %s (org=%s)", backend_conn.id, backend_conn.org
            )
            return

        try:
            await session.send_to_charger(message)
            logger.info("[%s] ← from backend", backend_conn.id)
        except Exception as exc:
            logger.warning(
                "Error sending backend message to charger %s: %s", backend_conn.id, exc
            )

    def _on_backend_connected(self, backend):
        logger.info("✅ Backend connected for charger %s (org=%s)", backend.id, backend.org)

    def _on_backend_disconnected(self, backend):
        logger.info("⚠️ Backend disconnected for charger %s (org=%s)", backend.id, backend.org)
