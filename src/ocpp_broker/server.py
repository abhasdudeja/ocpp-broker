import asyncio
import argparse
import logging
from pathlib import Path
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocketDisconnect

from ocpp_broker._version import __version__
from ocpp_broker.api_server import mount_api_routers
from ocpp_broker.auth import authenticate_charger, configured_api_key
from ocpp_broker.broker import OcppBroker
from ocpp_broker.ui import create_ui_router

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logger = logging.getLogger("ocpp_broker.server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---------------------------------------------------------------------------
# FastAPI app creation
# ---------------------------------------------------------------------------
app = FastAPI(title="OCPP Broker", version=__version__)

broker = OcppBroker()  # global broker instance

# REST routers (tags, OCPP commands, MongoDB) share this app and port with the
# charger WebSocket endpoint. They resolve everything through ``broker`` at
# request time, so ``main_async`` must configure this instance, not replace it.
mount_api_routers(app, broker)

# The web console is static files and carries no secrets, so it is not behind the
# API key; it asks for the key itself and sends it with every API call.
app.include_router(create_ui_router(broker))


async def _deny_unauthorized(websocket: WebSocket) -> None:
    """Refuse the WebSocket upgrade with HTTP 401 (falls back to a plain close)."""
    try:
        await websocket.send_denial_response(
            PlainTextResponse(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="OCPP", charset="UTF-8"'},
            )
        )
    except RuntimeError:
        # The ASGI server does not support denial responses; close instead (HTTP 403).
        await websocket.close(code=1008, reason="Unauthorized")


# ---------------------------------------------------------------------------
# WebSocket endpoint for chargers
# ---------------------------------------------------------------------------
@app.websocket("/{org_name}/{charger_id}")
async def ocpp_entry(websocket: WebSocket, org_name: str, charger_id: str):
    """
    Main entrypoint for charger WebSocket connections.
    Accepts OCPP connections like /orgA/CHG001 and passes them to the broker handler.
    Validates sec-websocket-protocol header according to organization configuration.
    """
    # Load config if not already loaded
    if not broker.config_data:
        await broker.load_config()
    
    # Get organization configuration to determine expected subprotocol
    org_entry = next(
        (o for o in broker.config_data.get("organizations", []) if o.get("name") == org_name),
        None,
    )
    
    # OCPP security profile 1: HTTP Basic auth on the upgrade request. Checked
    # before anything else so an unauthenticated peer learns nothing about us.
    if not await authenticate_charger(org_entry, charger_id, websocket.headers.get("authorization")):
        logger.warning(
            f"❌ Rejected charger {charger_id} from org '{org_name}': authentication failed"
        )
        await _deny_unauthorized(websocket)
        return
    # Determine expected subprotocol (from org config or default to ocpp1.6)
    expected_subprotocol = "ocpp1.6"  # Default
    if org_entry:
        expected_subprotocol = org_entry.get("ocpp_subprotocol", "ocpp1.6")
    
    # Check sec-websocket-protocol header - REQUIRED for OCPP compliance
    requested_protocols_str = websocket.headers.get("sec-websocket-protocol", "")
    
    if not requested_protocols_str:
        # Reject connection if sec-websocket-protocol header is missing
        logger.error(
            f"❌ Rejected charger {charger_id} from org '{org_name}': "
            f"missing required sec-websocket-protocol header"
        )
        await websocket.close(code=1002, reason="Missing sec-websocket-protocol header (required for OCPP)")
        return
    
    # Parse comma-separated protocol list (e.g., "ocpp1.6, ocpp2.0.1")
    requested_protocols = [p.strip() for p in requested_protocols_str.split(",")]
    
    # Check if the expected subprotocol is in the requested protocols
    subprotocol = None
    if expected_subprotocol in requested_protocols:
        subprotocol = expected_subprotocol
        logger.debug(f"✅ Accepted subprotocol '{subprotocol}' for {org_name}/{charger_id}")
    else:
        # Protocol was requested but doesn't match expected - reject connection
        logger.error(
            f"❌ Rejected charger {charger_id} from org '{org_name}': "
            f"subprotocol mismatch - expected '{expected_subprotocol}', got '{requested_protocols_str}'"
        )
        await websocket.close(
            code=1002, 
            reason=f"Subprotocol mismatch: expected '{expected_subprotocol}', got '{requested_protocols_str}'"
        )
        return
    
    await websocket.accept(subprotocol=subprotocol)

    # Delegate to broker logic
    try:
        await broker.handle_charger(websocket, f"/{org_name}/{charger_id}")
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {org_name}/{charger_id}")
    except Exception as e:
        logger.exception(f"Error handling charger {org_name}/{charger_id}: {e}")


# ---------------------------------------------------------------------------
# Dummy WebSocket for subprotocol validation (optional)
# ---------------------------------------------------------------------------
@app.websocket("/ocpp-check")
async def ocpp_check(websocket: WebSocket):
    """Dummy endpoint to test OCPP subprotocol acceptance."""
    # Check sec-websocket-protocol header - REQUIRED for OCPP compliance
    requested_protocols = websocket.headers.get("sec-websocket-protocol", "")
    
    if not requested_protocols:
        # Reject connection if sec-websocket-protocol header is missing
        logger.error("❌ Rejected connection to /ocpp-check: missing required sec-websocket-protocol header")
        await websocket.close(code=1002, reason="Missing sec-websocket-protocol header (required for OCPP)")
        return
    
    # Accept common OCPP protocols
    subprotocol = None
    for proto in ["ocpp1.6", "ocpp2.0.1", "ocpp2.0"]:
        if proto in requested_protocols:
            subprotocol = proto
            break
    
    if not subprotocol:
        # Reject if no valid OCPP protocol was requested
        logger.error(f"❌ Rejected connection to /ocpp-check: no valid OCPP subprotocol in '{requested_protocols}'")
        await websocket.close(code=1002, reason=f"No valid OCPP subprotocol in '{requested_protocols}'")
        return
    
    await websocket.accept(subprotocol=subprotocol)
    await websocket.close()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
@app.get("/health", include_in_schema=False)
@app.head("/health", include_in_schema=False)
async def health_check():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
def apply_cors(application: FastAPI, cfg: dict) -> None:
    """
    Enable CORS for the explicit origins in ``security.cors.allow_origins``.

    With no origins configured no CORS headers are sent (same-origin only).
    ``"*"`` is accepted for credential-less use but can never be combined with
    ``allow_credentials``: browsers refuse that pairing and it would expose
    credentialed requests to any site.
    """
    cors = (cfg.get("security") or {}).get("cors") or {}
    origins = [str(o) for o in (cors.get("allow_origins") or []) if o]
    credentials = bool(cors.get("allow_credentials", False))
    if not origins:
        logger.info("CORS disabled (security.cors.allow_origins is empty)")
        return
    if "*" in origins:
        if credentials:
            logger.error("security.cors: '*' cannot be combined with allow_credentials; credentials disabled")
            credentials = False
        logger.warning("security.cors allows any origin; list explicit origins in production")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    )


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_broker_config(config_path: str | None) -> dict:
    """
    Load the unified broker configuration from YAML.
    - If -c is provided, use that path.
    - Otherwise, first check the current working directory for config.yaml.
    - If not found, fall back to the package root path.
    """
    if config_path:
        path = Path(config_path)
    else:
        # Prefer config.yaml from current working directory
        cwd_path = Path.cwd() / "config.yaml"
        if cwd_path.exists():
            path = cwd_path
        else:
            # Fallback to repo root (two levels above src/ocpp_broker/)
            path = Path(__file__).resolve().parents[2] / "config.yaml"

    if not path.exists():
        logger.warning(f"Configuration file not found at {path}, using unified defaults.")
        from .config import _get_default_config
        cfg = _get_default_config()
        cfg["_path"] = "default"
        return cfg

    # Use the optimized config loader
    from .config import load_config
    cfg = load_config(str(path))
    cfg["_path"] = str(path)
    
    logger.info(f"Loaded unified configuration from {path}")
    return cfg


# ---------------------------------------------------------------------------
# Server configuration
# ---------------------------------------------------------------------------
def build_uvicorn_config(application: FastAPI, cfg: dict) -> uvicorn.Config:
    """
    Build the uvicorn config from the broker configuration.

    ``security.websocket.ping_interval`` / ``ping_timeout`` (seconds, default 20
    each) drive the WebSocket keepalive on charger sockets: the server pings
    every interval and closes a socket that does not answer within the timeout,
    so half-open "zombie" connections are cleaned up instead of lingering.
    """
    broker_cfg = cfg.get("broker", {})
    ws_cfg = cfg.get("security", {}).get("websocket", {})
    return uvicorn.Config(
        application,
        host=broker_cfg.get("host", "0.0.0.0"),
        port=broker_cfg.get("port", 8765),
        log_level="info",
        loop="asyncio",
        ws_ping_interval=ws_cfg.get("ping_interval", 20),
        ws_ping_timeout=ws_cfg.get("ping_timeout", 20),
    )


def _log_api_security(cfg: dict) -> None:
    if configured_api_key(cfg):
        logger.info("REST API protected by API key")
    elif (cfg.get("security") or {}).get("allow_unauthenticated_api"):
        logger.warning("REST API is UNAUTHENTICATED (security.allow_unauthenticated_api is true)")
    else:
        logger.warning(
            "REST API is DISABLED: every /api request returns 503 until security.api_key "
            "(or OCPP_BROKER_API_KEY) is set"
        )


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------
async def main_async(cfg: dict):
    broker._cfg_path = cfg.get("_path", "config.yaml")
    await broker.load_config()
    apply_cors(app, broker.config_data)
    _log_api_security(broker.config_data)
    logger.info("OCPP Broker ready — waiting for chargers...")

    server = uvicorn.Server(build_uvicorn_config(app, cfg))
    await server.serve()


# ---------------------------------------------------------------------------
# CLI wrapper
# ---------------------------------------------------------------------------
def run_broker_server(config_path: str | None):
    cfg = load_broker_config(config_path)
    try:
        asyncio.run(main_async(cfg))
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down...")


def main():
    parser = argparse.ArgumentParser(description="Run the OCPP Broker server.")
    parser.add_argument(
        "-c", "--config", help="Path to configuration YAML file", default=None
    )
    args = parser.parse_args()
    run_broker_server(args.config)


if __name__ == "__main__":
    main()
