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

    async def route_backend_message(self, backend, msg: str):
        """
        Called when a backend sends a message that might go to a charger.
        Only leader backends send actionable commands.
        """
        # If backend not leader, ignore for command routing
        if not backend.is_leader:
            logger.debug(f"Ignoring follower message from {backend.id} ({backend.org})")
            return

        # Parse message and identify charger target
        try:
            data = json.loads(msg)
        except Exception:
            logger.warning(f"Invalid JSON from backend {backend.id}: {msg}")
            return

        charger_id = None
        # Accept two forms:
        # 1) { "target": "<charger_id>", "payload": ... }
        # 2) OCPP array style [2, "msgId", "Action", {"chargePointId":"CP_001", ...}]
        if isinstance(data, dict):
            charger_id = data.get("target") or data.get("chargePointId") or data.get("idTag")
            to_send = json.dumps(data.get("payload", data))
        elif isinstance(data, list) and len(data) >= 4:
            payload = data[3]
            charger_id = payload.get("chargePointId") or payload.get("idTag")
            to_send = json.dumps(data)
        else:
            to_send = msg

        if not charger_id:
            logger.warning(f"Leader message missing charger target: {msg}")
            return

        ws = self.broker.active_chargers.get(str(charger_id))
        if not ws or ws.closed:
            logger.warning(f"Cannot deliver command to {charger_id}: not connected.")
            return

        try:
            await ws.send(to_send)
            logger.info(f"Delivered leader command from {backend.id} ({backend.org}) to charger {charger_id}")
        except Exception as e:
            logger.warning(f"Error sending command to charger {charger_id}: {e}")
