import os
import yaml
import logging
from pathlib import Path

logger = logging.getLogger("ocpp_broker.config")

DEFAULT_CONFIG_PATH = os.environ.get("OCPP_BROKER_CONFIG", "config.yaml")


def _load_env_file():
    """Load environment variables from .env file if it exists."""
    try:
        from dotenv import load_dotenv
        
        # Try to find .env file in current directory or project root
        env_paths = [
            Path.cwd() / ".env",
            Path(__file__).resolve().parents[2] / ".env",  # Project root
        ]
        
        for env_path in env_paths:
            if env_path.exists():
                load_dotenv(env_path)
                logger.info(f"Loaded environment variables from {env_path}")
                return
        
        logger.debug("No .env file found, using system environment variables only")
    except ImportError:
        logger.debug("python-dotenv not available, skipping .env file loading")
    except Exception as e:
        logger.warning(f"Error loading .env file: {e}")


def load_config(path: str = None):
    """
    Load unified YAML configuration file with all broker features.
    Environment variables from .env file or system can override YAML values.
    """
    # Load .env file first
    _load_env_file()
    
    config_path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(config_path):
        logger.warning(f"No config.yaml found at {config_path}, using defaults.")
        cfg = _get_default_config()
    else:
        with open(config_path, "r") as f:
            try:
                cfg = yaml.safe_load(f) or {}
            except Exception as e:
                raise RuntimeError(f"Failed to parse YAML config: {e}")

    # Apply defaults and validate configuration
    cfg = _apply_defaults(cfg)
    
    # Override with environment variables
    cfg = _apply_env_overrides(cfg)
    
    _validate_config(cfg)
    
    logger.info(f"Loaded unified configuration with {len(cfg.get('organizations', []))} organizations")
    return cfg


def _get_default_config():
    """Get default configuration with all features"""
    return {
        "broker": {
            "host": "0.0.0.0", 
            "port": 8765,
            "ocpp_version": "1.6",
            "enable_validation": True,
            "enable_smart_charging": True,
            "enable_firmware_management": True,
            "enable_local_auth": True,
            "enable_reservations": True,
            "enable_tag_management": True
        },
        "api": {
            "host": "0.0.0.0",
            "port": 8080,
            "enable_swagger": True
        },
        "organizations": [],
        "tag_management": {
            "global": {"enabled": False},
            "validation": {"strict_mode": True},
            "import_export": {"supported_formats": ["json", "csv", "xml"]},
            "monitoring": {"enable_statistics": True}
        },
        "ocpp": {
            "validation": {"strict_mode": True},
            "commands": {
                "core": {"heartbeat_interval": 300},
                "smart_charging": {"max_charging_profiles": 10},
                "firmware": {"max_retry_count": 3},
                "local_auth": {"max_list_size": 1000},
                "reservations": {"max_reservation_duration": 86400}
            }
        },
        "logging": {
            "level": "INFO",
            "ocpp_commands": True,
            "tag_management": True
        },
        "security": {
            "websocket": {"ping_interval": 20},
            "ocpp": {"validate_message_ids": True},
            "tags": {"audit_tag_changes": True}
        }
    }


def _apply_defaults(cfg):
    """Apply default values to configuration"""
    # Broker defaults
    cfg.setdefault("broker", {})
    cfg["broker"].setdefault("host", "0.0.0.0")
    cfg["broker"].setdefault("port", 8765)
    cfg["broker"].setdefault("ocpp_version", "1.6")
    cfg["broker"].setdefault("enable_validation", True)
    cfg["broker"].setdefault("enable_smart_charging", True)
    cfg["broker"].setdefault("enable_firmware_management", True)
    cfg["broker"].setdefault("enable_local_auth", True)
    cfg["broker"].setdefault("enable_reservations", True)
    cfg["broker"].setdefault("enable_tag_management", True)

    # API defaults
    cfg.setdefault("api", {})
    cfg["api"].setdefault("host", "0.0.0.0")
    cfg["api"].setdefault("port", 8080)
    cfg["api"].setdefault("enable_swagger", True)

    # Organizations defaults
    cfg.setdefault("organizations", [])
    
    # Tag management defaults
    cfg.setdefault("tag_management", {})
    cfg["tag_management"].setdefault("global", {"enabled": False})
    cfg["tag_management"].setdefault("validation", {"strict_mode": True})
    cfg["tag_management"].setdefault("import_export", {"supported_formats": ["json", "csv", "xml"]})
    cfg["tag_management"].setdefault("monitoring", {"enable_statistics": True})

    # OCPP defaults
    cfg.setdefault("ocpp", {})
    cfg["ocpp"].setdefault("validation", {"strict_mode": True})
    cfg["ocpp"].setdefault("commands", {})
    cfg["ocpp"]["commands"].setdefault("core", {"heartbeat_interval": 300})
    cfg["ocpp"]["commands"].setdefault("smart_charging", {"max_charging_profiles": 10})
    cfg["ocpp"]["commands"].setdefault("firmware", {"max_retry_count": 3})
    cfg["ocpp"]["commands"].setdefault("local_auth", {"max_list_size": 1000})
    cfg["ocpp"]["commands"].setdefault("reservations", {"max_reservation_duration": 86400})

    # Logging defaults
    cfg.setdefault("logging", {})
    cfg["logging"].setdefault("level", "INFO")
    cfg["logging"].setdefault("ocpp_commands", True)
    cfg["logging"].setdefault("tag_management", True)

    # Security defaults
    cfg.setdefault("security", {})
    cfg["security"].setdefault("websocket", {"ping_interval": 20})
    cfg["security"].setdefault("ocpp", {"validate_message_ids": True})
    cfg["security"].setdefault("tags", {"audit_tag_changes": True})

    # Organization defaults
    for org in cfg["organizations"]:
        name = org.get("name")
        if not name:
            raise ValueError("Each organization must have a name.")
        
        # Set organization defaults
        org.setdefault("connect_to_backend", True)
        org.setdefault("backends", [])
        org.setdefault("chargers", [])
        org.setdefault("ocpp_features", ["core_profile"])
        org.setdefault("ocpp_subprotocol", "ocpp1.6")  # Default OCPP subprotocol
        org.setdefault("validate_messages_when_backend_leader", False)  # Default: no validation when backend is leader
        org.setdefault("validate_messages_when_broker_backend", False)  # Default: no validation when broker acts as backend
        org.setdefault("validation", {"strict_mode": True})  # Validation settings
        org.setdefault("tag_management", {"enabled": False})
        org.setdefault("tags", [])
        
        # Set backend defaults
        for backend in org.get("backends", []):
            backend.setdefault("ocpp_subprotocol", org.get("ocpp_subprotocol", "ocpp1.6"))

        # Validate and fix backend leaders
        leaders = [b for b in org["backends"] if b.get("leader")]
        if len(leaders) > 1:
            logger.warning(f"Organization {name} has multiple leaders; using first one only.")
            for b in org["backends"]:
                b["leader"] = (b == leaders[0])
        elif len(leaders) == 0 and org["backends"]:
            org["backends"][0]["leader"] = True
            logger.info(f"Organization {name}: auto-marked {org['backends'][0]['id']} as leader.")

    return cfg


def _validate_config(cfg):
    """Validate configuration structure and values"""
    # Validate broker settings
    broker = cfg.get("broker", {})
    if not isinstance(broker.get("port"), int) or broker.get("port", 0) <= 0:
        raise ValueError("Broker port must be a positive integer")
    
    # Validate organizations
    organizations = cfg.get("organizations", [])
    if not isinstance(organizations, list):
        raise ValueError("Organizations must be a list")
    
    org_names = [org.get("name") for org in organizations]
    if len(org_names) != len(set(org_names)):
        raise ValueError("Organization names must be unique")
    
    # Validate tag management
    tag_mgmt = cfg.get("tag_management", {})
    if tag_mgmt.get("global", {}).get("enabled") and not any(
        org.get("tag_management", {}).get("enabled") for org in organizations
    ):
        logger.warning("Global tag management enabled but no organizations have tag management enabled")
    
    logger.info("Configuration validation passed")


def _apply_env_overrides(cfg):
    """
    Override configuration values with environment variables.
    Environment variables take precedence over YAML config.
    """
    # Broker settings
    if "BROKER_HOST" in os.environ:
        cfg.setdefault("broker", {})["host"] = os.environ["BROKER_HOST"]
    if "BROKER_PORT" in os.environ:
        try:
            cfg.setdefault("broker", {})["port"] = int(os.environ["BROKER_PORT"])
        except ValueError:
            logger.warning(f"Invalid BROKER_PORT value: {os.environ['BROKER_PORT']}")
    
    # API settings
    if "API_HOST" in os.environ:
        cfg.setdefault("api", {})["host"] = os.environ["API_HOST"]
    if "API_PORT" in os.environ:
        try:
            cfg.setdefault("api", {})["port"] = int(os.environ["API_PORT"])
        except ValueError:
            logger.warning(f"Invalid API_PORT value: {os.environ['API_PORT']}")
    
    # MongoDB settings
    if "MONGODB_ENABLED" in os.environ:
        enabled = os.environ["MONGODB_ENABLED"].lower() in ("true", "1", "yes", "on")
        cfg.setdefault("mongodb", {})["enabled"] = enabled
    
    if "MONGODB_CONNECTION_STRING" in os.environ:
        cfg.setdefault("mongodb", {})["connection_string"] = os.environ["MONGODB_CONNECTION_STRING"]
    
    if "MONGODB_DATABASE_NAME" in os.environ:
        cfg.setdefault("mongodb", {})["database_name"] = os.environ["MONGODB_DATABASE_NAME"]
    
    # Logging
    if "LOG_LEVEL" in os.environ:
        log_level = os.environ["LOG_LEVEL"].upper()
        if log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            cfg.setdefault("logging", {})["level"] = log_level
    
    return cfg
