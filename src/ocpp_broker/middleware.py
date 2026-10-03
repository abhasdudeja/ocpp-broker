import json
import logging

logger = logging.getLogger("ocpp_broker.middleware")


async def process_charger_to_backend(charger_id: str, message: str):
    """
    Parse a charger frame on its way to the backend.

    The frame is returned unchanged: in relay mode the broker does not validate
    (the transaction id table in ``transaction_ids`` is the one place frames are
    rewritten), and in broker mode the ocpp library validates.

    Returns:
        Tuple of (message_to_forward, parsed_json_or_none)
    """
    try:
        parsed = json.loads(message)
    except Exception:
        parsed = None

    logger.debug(f"[{charger_id}] -> broker: {parsed or message}")
    return message, parsed
