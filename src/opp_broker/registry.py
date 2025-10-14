import asyncio
import logging

logger = logging.getLogger("ocpp_broker.registry")

class ChargerRegistry:
    """
    Keeps a list of registered chargers from multiple backends.
    """

    def __init__(self):
        self._charger_ids = set()
        self._per_backend = {}
        self._lock = asyncio.Lock()

    async def update_from_backend(self, backend_id: str, new_ids):
        async with self._lock:
            new_set = set(map(str, new_ids or []))
            self._per_backend[backend_id] = new_set
            combined = set()
            for s in self._per_backend.values():
                combined |= s
            self._charger_ids = combined
            logger.info(f"Updated registry from {backend_id}: {len(new_set)} chargers (total {len(self._charger_ids)})")

    async def remove_backend(self, backend_id: str):
        async with self._lock:
            if backend_id in self._per_backend:
                self._per_backend.pop(backend_id)
                combined = set()
                for s in self._per_backend.values():
                    combined |= s
                self._charger_ids = combined
                logger.info(f"Removed backend {backend_id} from registry (total {len(self._charger_ids)})")

    async def is_registered(self, charger_id: str) -> bool:
        async with self._lock:
            return str(charger_id) in self._charger_ids

    async def list_all(self):
        async with self._lock:
            return set(self._charger_ids)
