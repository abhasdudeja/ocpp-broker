import asyncio
import argparse
import logging
import yaml
from pathlib import Path
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketDisconnect

from ocpp_broker.broker import OcppBroker

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
app = FastAPI(title="OCPP Broker", version="0.3.3")

broker = OcppBroker()  # global broker instance


# ---------------------------------------------------------------------------
# WebSocket endpoint for chargers
# ---------------------------------------------------------------------------
@app.websocket("/{org_name}/{charger_id}")
async def ocpp_entry(websocket: WebSocket, org_name: str, charger_id: str):
    """
    Main entrypoint for charger WebSocket connections.
    Accepts OCPP connections like /orgA/CHG001 and passes them to the broker handler.
    """
    # Accept the WebSocket handshake first
    subprotocol = None
    if "ocpp1.6" in websocket.headers.get("sec-websocket-protocol", ""):
        subprotocol = "ocpp1.6"
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
    subprotocol = None
    if "ocpp1.6" in websocket.headers.get("sec-websocket-protocol", ""):
        subprotocol = "ocpp1.6"
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
# CORS (useful for ngrok testing)
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
# Main async runner
# ---------------------------------------------------------------------------
async def main_async(cfg: dict):
    global broker
    broker = OcppBroker()
    broker._cfg_path = cfg.get("_path", "config.yaml")
    await broker.load_config()
    logger.info("OCPP Broker ready — waiting for chargers...")

    host = cfg.get("broker", {}).get("host", "0.0.0.0")
    port = cfg.get("broker", {}).get("port", 8765)

    config = uvicorn.Config(app, host=host, port=port, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
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
