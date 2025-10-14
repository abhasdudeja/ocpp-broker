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
            self._per_backend[backend_id] = set(new_ids)
            combined = set()
            for s in self._per_backend.values():
                combined |= s
            self._charger_ids = combined
            logger.info(f"Updated registry from {backend_id}: {len(new_ids)} chargers (total {len(self._charger_ids)})")

    async def is_registered(self, charger_id: str) -> bool:
        async with self._lock:
            return charger_id in self._charger_ids
