import json
import logging

logger = logging.getLogger("ocpp_broker.middleware")

async def process_charger_to_backend(charger_id: str, message: str):
    """
    Optional hook to inspect/modify messages sent by chargers.
    Returns (message_to_forward, parsed_json_or_none)
    """
    try:
        parsed = json.loads(message)
    except Exception:
        parsed = None
    logger.debug(f"[{charger_id}] -> broker: {parsed or message}")
    return message, parsed
