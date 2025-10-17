import asyncio
import logging
import websockets

from .registry import ChargerRegistry
from .backend_manager import BackendConnection
from .command_router import CommandRouter
from .middleware import process_charger_to_backend
from .config import load_config

logger = logging.getLogger("ocpp_broker.broker")


class OcppBroker:
    """
    OCPP Broker that only connects to backends if explicitly allowed in config.yaml.
    """

    def __init__(self):
        self.org_backends = {}       # org -> {backend_id: BackendConnection}
        self.org_leaders = {}        # org -> leader backend
        self.org_registries = {}     # org -> ChargerRegistry
        self.active_chargers = {}    # charger_id -> websocket
        self.command_router = CommandRouter(self)
        self._send_lock = asyncio.Lock()
        self._cfg_path = "config.yaml"
        self.config_data = {}        # Cached config

    async def load_config(self):
        """Load YAML config once (no connections)."""
        self.config_data = load_config(self._cfg_path)
        logger.info(f"Loaded configuration for {len(self.config_data.get('organizations', []))} organizations.")

    async def ensure_org_initialized(self, org_name: str):
        """Lazily initialize org and optionally connect to backend if configured."""
        if org_name in self.org_backends:
            return True

        orgs = self.config_data.get("organizations", [])
        org_entry = next((o for o in orgs if o.get("name") == org_name), None)
        if not org_entry:
            logger.warning(f"Unknown organization '{org_name}' attempted connection.")
            return False

        connect_to_backend = org_entry.get("connect_to_backend", False)
        self.org_backends[org_name] = {}
        self.org_registries[org_name] = ChargerRegistry()

        if not connect_to_backend:
            logger.info(f"Organization '{org_name}' configured to skip backend connections.")
            return True

        logger.info(f"Connecting backends for organization '{org_name}'...")
        for b in org_entry.get("backends", []):
            backend = BackendConnection(
                broker=self,
                backend_id=b["id"],
                url=b["url"],
                leader=b.get("leader", False),
                org=org_name
            )
            await backend.connect()
            self.org_backends[org_name][b["id"]] = backend
            if backend.is_leader:
                self.org_leaders[org_name] = backend

        # Auto-select leader if missing
        if org_name not in self.org_leaders and self.org_backends[org_name]:
            leader = list(self.org_backends[org_name].values())[0]
            leader.is_leader = True
            self.org_leaders[org_name] = leader
            logger.info(f"Auto-selected {leader.id} as leader for '{org_name}'.")

        return True

    async def handle_charger(self, websocket, path):
        """Handle new charger WebSocket connection."""
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) != 2:
            await websocket.close(code=4000, reason="Invalid path format")
            return

        org_name, charger_id = parts
        if not self.config_data:
            await self.load_config()

        # ✅ Validate org
        orgs = self.config_data.get("organizations", [])
        org_entry = next((o for o in orgs if o.get("name") == org_name), None)
        if not org_entry:
            logger.warning(f"Rejected charger {charger_id}: unknown organization '{org_name}'.")
            await websocket.close(code=4002, reason="Unknown organization")
            return

        # ✅ Allow dummy/test chargers
        if charger_id.lower() in {"test", "dummy", "check"}:
            logger.info(f"Received test connection for '{org_name}' (dummy charger '{charger_id}').")
            ok = await self.ensure_org_initialized(org_name)
            if ok:
                await websocket.send(f"Organization '{org_name}' valid. Backend connection mode: {org_entry.get('connect_to_backend', False)}.")
                await asyncio.sleep(1)
            await websocket.close(code=1000, reason="Test connection successful")
            return

        # ✅ Validate real charger
        known_chargers = set()
        for b in org_entry.get("backends", []):
            chargers = b.get("chargers", [])
            known_chargers |= set(map(str, chargers))

        if charger_id not in known_chargers:
            logger.warning(f"Rejected charger {charger_id}: not listed in config for '{org_name}'.")
            await websocket.close(code=4001, reason="Unregistered charger")
            return

        # ✅ Initialize org (conditionally connect to backend)
        ok = await self.ensure_org_initialized(org_name)
        if not ok:
            await websocket.close(code=4003, reason="Backend initialization failed")
            return

        logger.info(f"Accepted charger {charger_id} for org '{org_name}'")
        self.active_chargers[charger_id] = websocket

        try:
            async for msg in websocket:
                msg_out, parsed = await process_charger_to_backend(charger_id, msg)
                await self._broadcast_to_org_backends(org_name, msg_out)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Charger {charger_id} disconnected.")
        finally:
            self.active_chargers.pop(charger_id, None)

    async def _broadcast_to_org_backends(self, org_name, message: str):
        """Send charger message to all active backends for that org."""
        async with self._send_lock:
            org_backs = self.org_backends.get(org_name, {})
            await asyncio.gather(
                *[b.send(message) for b in org_backs.values()],
                return_exceptions=True
            )

    def get_registry(self, org_name: str):
        return self.org_registries[org_name]

    def promote_leader(self, org_name: str, backend_id: str) -> bool:
        backs = self.org_backends.get(org_name)
        if not backs or backend_id not in backs:
            return False
        for b in backs.values():
            b.is_leader = False
        backs[backend_id].is_leader = True
        self.org_leaders[org_name] = backs[backend_id]
        logger.info(f"Backend {backend_id} promoted to leader for org {org_name}")
        return True

    def _on_backend_connected(self, backend):
        self.org_backends.setdefault(backend.org, {})[backend.id] = backend
        if backend.is_leader:
            self.org_leaders[backend.org] = backend
        logger.info(f"Backend connected: {backend.id} (org={backend.org})")

    def _on_backend_disconnected(self, backend):
        try:
            if backend.org in self.org_backends and backend.id in self.org_backends[backend.org]:
                logger.info(f"Backend disconnected: {backend.id} (org={backend.org})")
        except Exception:
            pass

    async def close_all_backends(self):
        for org, backs in list(self.org_backends.items()):
            for b in list(backs.values()):
                try:
                    await b.close()
                except Exception:
                    pass
