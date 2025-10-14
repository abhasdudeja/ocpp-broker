import asyncio
import logging
import websockets

from .registry import ChargerRegistry
from .backend_manager import BackendConnection
from .command_router import CommandRouter
from .middleware import process_charger_to_backend

logger = logging.getLogger("ocpp_broker.broker")

class OcppBroker:
    """
    Multi-organization OCPP Broker with dynamic API control.
    """

    def __init__(self):
        self.org_backends = {}       # org -> {backend_id: BackendConnection}
        self.org_leaders = {}        # org -> leader backend
        self.org_registries = {}     # org -> ChargerRegistry
        self.active_chargers = {}    # charger_id -> websocket
        self.command_router = CommandRouter(self)
        self._send_lock = asyncio.Lock()
        self._cfg_path = "config.yaml"

    async def add_organization(self, org_name: str, backends: list[dict]):
        self.org_backends[org_name] = {}
        self.org_registries[org_name] = ChargerRegistry()
        logger.info(f"Adding organization {org_name} with {len(backends)} backends")

        for b in backends:
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

        if org_name not in self.org_leaders and backends:
            self.org_leaders[org_name] = list(self.org_backends[org_name].values())[0]
            logger.info(f"Auto-assigned leader for {org_name}: {self.org_leaders[org_name].id}")

    async def add_backend_dynamic(self, org_name: str, backend_data: dict):
        """
        Add new backend dynamically (via API).
        """
        if org_name not in self.org_backends:
            self.org_backends[org_name] = {}
            self.org_registries[org_name] = ChargerRegistry()

        backend = BackendConnection(
            broker=self,
            backend_id=backend_data["id"],
            url=backend_data["url"],
            leader=backend_data.get("leader", False),
            org=org_name
        )
        await backend.connect()
        self.org_backends[org_name][backend.id] = backend
        if backend.is_leader:
            self.promote_leader(org_name, backend.id)
        logger.info(f"Added backend {backend.id} to org {org_name}")
        return backend

    async def remove_backend_dynamic(self, org_name: str, backend_id: str) -> bool:
        backs = self.org_backends.get(org_name, {})
        backend = backs.pop(backend_id, None)
        if not backend:
            return False
        await backend.close()
        # remove registry entries
        try:
            await self.org_registries[org_name].remove_backend(backend_id)
        except Exception:
            pass
        logger.info(f"Removed backend {backend_id} from org {org_name}")
        # if removed backend was leader, auto-select another
        if self.org_leaders.get(org_name) and getattr(self.org_leaders[org_name], "id", None) == backend_id:
            remaining = list(backs.values())
            if remaining:
                self.promote_leader(org_name, remaining[0].id)
            else:
                self.org_leaders[org_name] = None
        return True

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

    async def reload_from_config(self):
        """
        Reload all orgs and backends from config.yaml.
        """
        from .config import load_config
        cfg = load_config(self._cfg_path)
        logger.info("Reloading configuration from file...")
        await self.close_all_backends()
        self.org_backends.clear()
        self.org_leaders.clear()
        self.org_registries.clear()

        for org in cfg.get("organizations", []):
            await self.add_organization(org["name"], org["backends"])
        logger.info("Configuration reload complete.")

    def get_registry(self, org_name: str):
        return self.org_registries[org_name]

    def _on_backend_connected(self, backend):
        # Called by BackendConnection when it becomes connected
        # ensure it's registered in the broker backends map (may already be)
        self.org_backends.setdefault(backend.org, {})[backend.id] = backend
        if backend.is_leader:
            self.org_leaders[backend.org] = backend
        logger.info(f"Backend connected: {backend.id} (org={backend.org})")

    def _on_backend_disconnected(self, backend):
        # Called by BackendConnection when disconnected
        try:
            if backend.org in self.org_backends and backend.id in self.org_backends[backend.org]:
                # keep entry but mark websocket None; actual removal is done by remove_backend_dynamic
                logger.info(f"Backend disconnected: {backend.id} (org={backend.org})")
        except Exception:
            pass

    async def handle_charger(self, websocket, path):
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) != 2:
            await websocket.close(code=4000, reason="Invalid path")
            return
        org_name, charger_id = parts
        if org_name not in self.org_registries:
            await websocket.close(code=4002, reason="Unknown organization")
            return

        registry = self.org_registries[org_name]
        if not await registry.is_registered(charger_id):
            await websocket.close(code=4001, reason="Unregistered charger")
            return

        logger.info(f"Accepted charger {charger_id} for org {org_name}")
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
        async with self._send_lock:
            org_backs = self.org_backends.get(org_name, {})
            await asyncio.gather(
                *[b.send(message) for b in org_backs.values()],
                return_exceptions=True
            )

    async def close_all_backends(self):
        for org, backs in list(self.org_backends.items()):
            for b in list(backs.values()):
                try:
                    await b.close()
                except Exception:
                    pass
