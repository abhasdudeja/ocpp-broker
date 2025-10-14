import asyncio
import json
import logging

logger = logging.getLogger("ocpp_broker.command_router")

class CommandRouter:
    """
    Handles routing of backend-originated messages to chargers.
    """

    def __init__(self, broker):
        self.broker = broker
        self._queues = {}

    async def route_backend_message(self, backend, msg: str):
        """
        Called when a backend sends a message that might go to a charger.
        Only leader backends send actionable commands.
        """
        if not backend.is_leader:
            logger.debug(f"Ignoring follower message from {backend.id} ({backend.org})")
            return

        # Parse message and identify charger target
        try:
            data = json.loads(msg)
        except Exception:
            logger.warning(f"Invalid JSON from backend {backend.id}")
            return

        charger_id = None
        if isinstance(data, dict):
            charger_id = data.get("target") or data.get("chargePointId")
        elif isinstance(data, list) and len(data) >= 4:
            charger_id = data[3].get("chargePointId")

        if not charger_id:
            logger.warning(f"Leader message missing charger target: {msg}")
            return

        ws = self.broker.active_chargers.get(charger_id)
        if not ws or ws.closed:
            logger.warning(f"Cannot deliver command to {charger_id}: not connected.")
            return

        await ws.send(msg)
        logger.info(f"Delivered leader command from {backend.id} ({backend.org}) to charger {charger_id}")
