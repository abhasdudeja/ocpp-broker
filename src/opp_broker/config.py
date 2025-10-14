import os
import yaml
import logging

logger = logging.getLogger("ocpp_broker.config")

DEFAULT_CONFIG_PATH = os.environ.get("OCPP_BROKER_CONFIG", "config.yaml")

def load_config(path: str = None):
    """
    Load YAML configuration file with multi-organization support.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    if not os.path.exists(config_path):
        logger.warning(f"No config.yaml found at {config_path}, using defaults.")
        return {
            "broker": {"host": "0.0.0.0", "port": 8765},
            "organizations": []
        }

    with open(config_path, "r") as f:
        try:
            cfg = yaml.safe_load(f) or {}
        except Exception as e:
            raise RuntimeError(f"Failed to parse YAML config: {e}")

    cfg.setdefault("broker", {"host": "0.0.0.0", "port": 8765})
    cfg.setdefault("organizations", [])

    # Validate organizations
    for org in cfg["organizations"]:
        name = org.get("name")
        if not name:
            raise ValueError("Each organization must have a name.")
        org.setdefault("backends", [])

        leaders = [b for b in org["backends"] if b.get("leader")]
        if len(leaders) > 1:
            logger.warning(f"Organization {name} has multiple leaders; using first one only.")
            for b in org["backends"]:
                b["leader"] = (b == leaders[0])
        elif len(leaders) == 0 and org["backends"]:
            org["backends"][0]["leader"] = True
            logger.info(f"Organization {name}: auto-marked {org['backends'][0]['id']} as leader.")

    return cfg
