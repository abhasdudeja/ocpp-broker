import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("ocpp_broker.middleware")

async def process_charger_to_backend(
    charger_id: str, 
    message: str, 
    validate: bool = False,
    org_entry: Optional[Dict[str, Any]] = None
):
    """
    Process messages from charger before forwarding to backend.
    Optionally validates OCPP messages if validation is enabled.
    
    Args:
        charger_id: ID of the charger sending the message
        message: Raw message string from charger
        validate: Whether to validate the message
        org_entry: Organization configuration entry (for validation settings)
        
    Returns:
        Tuple of (message_to_forward, parsed_json_or_none)
    """
    try:
        parsed = json.loads(message)
    except Exception:
        parsed = None
    
    # Validate message if enabled
    if validate and parsed is not None:
        from .message_validator import validate_ocpp_message
        
        # Get strict mode from org config if available
        strict_mode = True
        if org_entry:
            validation_config = org_entry.get("validation", {})
            strict_mode = validation_config.get("strict_mode", True)
        
        is_valid, error_msg, validated_msg = validate_ocpp_message(message, strict_mode=strict_mode)
        
        if not is_valid:
            logger.warning(
                f"❌ Message validation failed for charger {charger_id}: {error_msg}. "
                f"Message: {message[:200]}"
            )
            # In strict mode, reject invalid messages
            if strict_mode:
                raise ValueError(f"Invalid OCPP message: {error_msg}")
            # In non-strict mode, log warning but forward anyway
            logger.warning(f"⚠️ Forwarding invalid message in non-strict mode")
    
    logger.debug(f"[{charger_id}] -> broker: {parsed or message}")
    return message, parsed
