import os
import yaml
import logging
from pathlib import Path
from typing import Optional

from .auth import DEFAULT_KEY_LABEL, LABEL_PATTERN, THROTTLE_DEFAULTS
from .history import validate_history_config
from .transaction_ids import leader_index, local_index

logger = logging.getLogger("ocpp_broker.config")

DEFAULT_CONFIG_PATH = os.environ.get("OCPP_BROKER_CONFIG", "config.yaml")

# Per-organization settings that used to drive the (removed) custom message validator.
_REMOVED_ORG_KEYS = (
    "validate_messages_when_backend_leader",
    "validate_messages_when_broker_backend",
    "validation",
)


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


def load_config(path: Optional[str] = None):
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

    cfg = build_config(cfg)
    logger.info(f"Loaded unified configuration with {len(cfg.get('organizations', []))} organizations")
    return cfg


def build_config(cfg, apply_env=True):
    """
    The configuration the broker runs on, made from what a configuration file holds: defaults applied, the
    environment's overrides folded in (unless ``apply_env`` is false) and every rule checked. ``cfg`` is changed
    in place; raises ValueError for a configuration that cannot be used. The admin API checks a changed
    configuration with this before it writes it.
    """
    cfg = _apply_defaults(cfg)
    if apply_env:
        cfg = _apply_env_overrides(cfg)
    _validate_config(cfg)
    return cfg


def _get_default_config():
    """The configuration used when there is no file: no organizations, so every charger is refused."""
    return {"organizations": []}


def _apply_defaults(cfg):
    """Apply default values to configuration"""
    # Broker defaults
    cfg.setdefault("broker", {})
    cfg["broker"].setdefault("host", "0.0.0.0")
    cfg["broker"].setdefault("port", 8765)
    # Web console (served at /ui)
    if not isinstance(cfg.get("ui"), dict):  # absent, or an empty `ui:` block
        cfg["ui"] = {}
    cfg["ui"].setdefault("enabled", True)

    # Organizations defaults
    cfg.setdefault("organizations", [])

    # OCPP defaults
    cfg.setdefault("ocpp", {})
    cfg["ocpp"].setdefault("commands", {})
    cfg["ocpp"]["commands"].setdefault("core", {"heartbeat_interval": 300})

    # Logging defaults
    cfg.setdefault("logging", {})
    cfg["logging"].setdefault("level", "INFO")

    # Security defaults
    cfg.setdefault("security", {})
    cfg["security"].setdefault("websocket", {})
    cfg["security"]["websocket"].setdefault("ping_interval", 20)
    cfg["security"]["websocket"].setdefault("ping_timeout", 20)
    # REST API: needs security.api_key (or OCPP_BROKER_API_KEY) unless explicitly opened up
    cfg["security"].setdefault("allow_unauthenticated_api", False)
    cfg["security"].setdefault("cors", {})
    cfg["security"]["cors"].setdefault("allow_origins", [])  # explicit origins only
    cfg["security"]["cors"].setdefault("allow_credentials", False)

    # Organization defaults
    for org in cfg["organizations"]:
        name = org.get("name")
        if not name:
            raise ValueError("Each organization must have a name.")
        
        # Set organization defaults
        org.setdefault("connect_to_backend", True)
        org.setdefault("backends", [])
        org.setdefault("ocpp_subprotocol", "ocpp1.6")  # Default OCPP subprotocol
        for removed in _REMOVED_ORG_KEYS:
            if removed in org:
                logger.warning(
                    "Organization %s: '%s' is no longer supported and is ignored. Messages are validated "
                    "by the ocpp library when the broker acts as the backend; relay mode forwards them without validating.",
                    name,
                    removed,
                )
        org.setdefault("backend_buffer_size", 200)  # frames held while the backend is down
        org.setdefault("backend_outage_timeout", 30)  # seconds before a held CALL is answered with a CallError
        org.setdefault("leader_failover_timeout", 15)  # seconds the leader may be down before a follower takes over (0 = never)
        org.setdefault("leader_failback", False)  # give the charger back to the configured leader after a failover
        org.setdefault("leader_failback_delay", 60)  # seconds the configured leader must stay connected first
        org.setdefault("tags", [])
        _normalize_charger_auth(org)
        _validate_failback(org)
        _validate_transaction_ids(org)

        _validate_backends(org)

        # Set backend defaults
        for backend in org.get("backends", []):
            if not backend.get("local"):
                backend.setdefault("ocpp_subprotocol", org.get("ocpp_subprotocol", "ocpp1.6"))

        # Validate and fix backend leaders
        leaders = [b for b in org["backends"] if b.get("leader")]
        if local_index(org["backends"]) is not None:
            # Exactly one leads: the one marked, else the local backend (the broker answers, the others observe)
            chosen = org["backends"][leader_index(org["backends"])]
            for b in org["backends"]:
                b["leader"] = b is chosen
        elif len(leaders) > 1:
            logger.warning(f"Organization {name} has multiple leaders; using first one only.")
            for b in org["backends"]:
                b["leader"] = b is leaders[0]  # identity: two identical entries are still two backends
        elif len(leaders) == 0 and org["backends"]:
            org["backends"][0]["leader"] = True
            first = org["backends"][0]
            logger.info(f"Organization {name}: auto-marked {first.get('id') or first.get('url')} as leader.")
        _check_backend_ids(org)

    return cfg


def _validate_backends(org):
    """
    Check an organization's ``backends``: every external one needs a ``url``, and at most one may be
    ``local: true``, which means "this broker is a backend" (see docs/leader-follower.md). A local backend
    leads (the broker answers the charger and the others receive copies) unless another entry is marked
    ``leader: true``, in which case it is a silent standby that takes over if that leader fails.
    """
    name = org["name"]
    backends = org.get("backends") or []
    if not isinstance(backends, list) or not all(isinstance(b, dict) for b in backends):
        raise ValueError(f"Organization {name}: backends must be a list of mappings")
    locals_ = [b for b in backends if b.get("local")]
    for backend in backends:
        if backend.get("local") not in (None, True, False):
            raise ValueError(f"Organization {name}: a backend's local must be true or false")
    if len(locals_) > 1:
        raise ValueError(f"Organization {name}: only one backend can be local (this broker)")
    for backend in backends:
        if backend.get("local"):
            if backend.get("url"):
                raise ValueError(f"Organization {name}: a local backend is this broker and has no url")
        elif not backend.get("url"):
            raise ValueError(f"Organization {name}: every backend needs a url (or local: true for this broker)")
    if locals_ and sum(1 for b in backends if b.get("leader")) > 1:
        raise ValueError(f"Organization {name}: with a local backend, only one backend can be marked leader")
    if locals_ and len(backends) == 1 and locals_[0].get("leader") is False:
        raise ValueError(f"Organization {name}: a local backend that is not the leader needs a leader to follow")
    if locals_ and org.get("connect_to_backend") is False:
        logger.warning("Organization %s: connect_to_backend is false, so its backends (including the local one) are ignored", name)


def _normalize_charger_auth(org):
    """
    Normalise ``charger_auth`` (OCPP security profile 1 credentials) for one org.

    ``credentials`` maps charger id -> ``{password_hash: ...}`` or
    ``{password: ...}`` (a bare string means password). Authentication is
    required when the org lists credentials, unless ``required`` says otherwise.
    """
    name = org["name"]
    auth = org.setdefault("charger_auth", {})
    credentials = {}
    for charger_id, entry in (auth.get("credentials") or {}).items():
        if isinstance(entry, str):
            entry = {"password": entry}
        if not isinstance(entry, dict) or not (entry.get("password_hash") or entry.get("password")):
            raise ValueError(
                f"Organization {name}: credentials for charger {charger_id} need password_hash or password"
            )
        credentials[str(charger_id)] = entry
    auth["credentials"] = credentials
    auth.setdefault("required", bool(credentials))

    plaintext = [cid for cid, entry in credentials.items() if "password_hash" not in entry]
    if plaintext:
        logger.warning(
            "Organization %s: %d charger credential(s) are stored as plaintext 'password'; "
            "prefer 'password_hash' (run: python -m ocpp_broker.auth).",
            name,
            len(plaintext),
        )
    if auth["required"] and not credentials:
        logger.warning(
            "Organization %s requires charger authentication but lists no credentials: "
            "every charger will be rejected.",
            name,
        )
    if not auth["required"]:
        logger.warning(
            "Organization %s accepts UNAUTHENTICATED chargers. Add charger_auth.credentials to enforce "
            "HTTP Basic auth (OCPP security profile 1).",
            name,
        )


def _validate_failback(org):
    """``leader_failback`` (true or false) and ``leader_failback_delay`` (seconds, above 0)."""
    name = org["name"]
    if not isinstance(org["leader_failback"], bool):
        raise ValueError(f"Organization {name}: leader_failback must be true or false")
    delay = org["leader_failback_delay"]
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay <= 0:
        raise ValueError(f"Organization {name}: leader_failback_delay must be a number of seconds above 0")


def _validate_transaction_ids(org):
    """Check the organization's optional ``transaction_ids`` block (see transaction_ids.table_for_org)."""
    name = org["name"]
    section = org.get("transaction_ids")
    if section is None:
        return
    if not isinstance(section, dict):
        raise ValueError(f"Organization {name}: transaction_ids must be a mapping")
    for key in ("mapping", "dedupe_start"):
        if section.get(key) is not None and not isinstance(section[key], bool):
            raise ValueError(f"Organization {name}: transaction_ids.{key} must be true or false")
    for key in ("follower_wait", "retain_closed", "retain_open"):
        value = section.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0):
            raise ValueError(f"Organization {name}: transaction_ids.{key} must be a positive number of seconds")
    unknown = sorted(set(section) - {"mapping", "follower_wait", "dedupe_start", "retain_closed", "retain_open"})
    if unknown:
        logger.warning("Organization %s: unknown transaction_ids setting(s) %s are ignored", name, ", ".join(unknown))


def _check_backend_ids(org):
    """
    With several backends the transaction id table names each backend by its ``id`` (else its URL),
    and keeps those names in MongoDB across restarts. A missing id is fine until the URL changes;
    two backends sharing an id would be told apart only by order.
    """
    backends = org.get("backends") or []
    settings = org.get("transaction_ids") or {}
    mapping = settings.get("mapping")
    if not (mapping if mapping is not None else len(backends) > 1):
        return
    name = org["name"]
    anonymous = [b.get("url") for b in backends if not b.get("id")]
    if anonymous:
        logger.warning(
            "Organization %s: backend(s) without an id (%s) are identified by their URL in the transaction id "
            "table; set an id so a later URL change does not orphan stored transactions.",
            name,
            ", ".join(str(u) for u in anonymous),
        )
    ids = [b["id"] for b in backends if b.get("id")]
    repeated = sorted({i for i in ids if ids.count(i) > 1})
    if repeated:
        logger.warning(
            "Organization %s: backend id(s) %s are used more than once; give each backend its own id.",
            name,
            ", ".join(map(str, repeated)),
        )


def _validate_admin(admin):
    """``admin``: whether the admin API may change organizations, where its audit log goes, how many backups are kept."""
    if not isinstance(admin, dict):
        raise ValueError("admin must be a mapping")
    unknown = sorted(set(admin) - {"enabled", "audit_log", "keep_backups", "store", "poll_seconds"})
    if unknown:
        raise ValueError(f"admin.{unknown[0]} is not known; use enabled, audit_log, keep_backups, store or poll_seconds")
    if admin.get("store", "file") not in ("file", "mongodb"):
        raise ValueError("admin.store must be file or mongodb")
    poll = admin.get("poll_seconds")
    if poll is not None and (isinstance(poll, bool) or not isinstance(poll, (int, float)) or poll < 1):
        raise ValueError("admin.poll_seconds must be a number of seconds, 1 or more")
    if "enabled" in admin and not isinstance(admin["enabled"], bool):
        raise ValueError("admin.enabled must be true or false")
    if "audit_log" in admin and (not isinstance(admin["audit_log"], str) or not admin["audit_log"].strip()):
        raise ValueError("admin.audit_log must be a file path")
    keep = admin.get("keep_backups")
    if keep is not None and (isinstance(keep, bool) or not isinstance(keep, int) or keep < 1):
        raise ValueError("admin.keep_backups must be a whole number, 1 or more")


def _validate_security(security):
    """``security.api_keys`` (labelled keys) and ``security.api_key_throttle``."""
    keys = security.get("api_keys")
    if keys is not None:
        if not isinstance(keys, list):
            raise ValueError("security.api_keys must be a list of {label, key}")
        labels = set()
        for entry in keys:
            if not isinstance(entry, dict) or not entry.get("key") or not isinstance(entry.get("key"), str):
                raise ValueError("Each security.api_keys entry needs a key (text)")
            label = entry.get("label")
            if not isinstance(label, str) or not LABEL_PATTERN.match(label):
                raise ValueError("Each security.api_keys entry needs a label of 1 to 40 letters, digits, dots, dashes or underscores")
            if label == DEFAULT_KEY_LABEL or label in labels:
                raise ValueError(f"security.api_keys label {label!r} is used twice (or is the name of the main key)")
            labels.add(label)
    throttle = security.get("api_key_throttle")
    if throttle is not None:
        if not isinstance(throttle, dict):
            raise ValueError("security.api_key_throttle must be a mapping")
        for name, value in throttle.items():
            if name not in THROTTLE_DEFAULTS:
                raise ValueError(f"security.api_key_throttle.{name} is not known; use {', '.join(THROTTLE_DEFAULTS)}")
            minimum = 0 if name == "max_failures" else 1
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < minimum:
                raise ValueError(f"security.api_key_throttle.{name} must be a number, {minimum} or more")


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
    
    validate_history_config(cfg.get("mongodb") or {})
    _validate_admin(cfg.get("admin") or {})
    if (cfg.get("admin") or {}).get("store") == "mongodb" and not (cfg.get("mongodb") or {}).get("enabled"):
        raise ValueError("admin.store: mongodb needs MongoDB (mongodb.enabled: true)")
    _validate_security(cfg.get("security") or {})

    level = (cfg.get("logging") or {}).get("level", "INFO")
    if str(level).upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ValueError(f"logging.level must be DEBUG, INFO, WARNING, ERROR or CRITICAL, not {level!r}")

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
