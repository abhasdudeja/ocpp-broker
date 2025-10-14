import json
import logging

logger = logging.getLogger("ocpp_broker.middleware")

async def process_charger_to_backend(charger_id: str, message: str):
    try:
        parsed = json.loads(message)
    except Exception:
        parsed = None
    logger.debug(f"[{charger_id}] -> broker: {parsed or message}")
    return message, parsed
