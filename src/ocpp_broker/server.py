import asyncio
import argparse
import logging
import yaml
from pathlib import Path
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from ocpp_broker.broker import OcppBroker

logger = logging.getLogger("ocpp_broker.server")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---------------------------------------------------------------------------
# FastAPI app creation
# ---------------------------------------------------------------------------

app = FastAPI(title="OCPP Broker", version="0.3.2")

# ✅ Explicitly accept OCPP subprotocol during WebSocket upgrade
@app.websocket("/ocpp-check")
async def ocpp_check(websocket: WebSocket):
    """
    Dummy endpoint for WebSocket subprotocol validation.
    Uvicorn uses this to accept the handshake for OCPP clients.
    """
    subprotocol = None
    if "ocpp1.6" in websocket.headers.get("sec-websocket-protocol", ""):
        subprotocol = "ocpp1.6"

    await websocket.accept(subprotocol=subprotocol)
    await websocket.close()

# ✅ Allow cross-origin for ngrok tunneling
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", include_in_schema=False)
@app.head("/health", include_in_schema=False)
async def health_check():
    return {"status": "ok"}



def load_broker_config(config_path: str | None) -> dict:
    project_root = Path(__file__).resolve().parents[2]
    default_path = project_root / "config.yaml"
    path = Path(config_path) if config_path else default_path

    if not path.exists():
        logger.warning(f"Configuration file not found at {path}, using defaults.")
        return {"broker": {"host": "0.0.0.0", "port": 8765}, "organizations": []}

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    logger.info(f"Loaded configuration from {path}")
    return cfg


async def main_async(cfg: dict):
    global broker
    broker = OcppBroker()
    broker._cfg_path = str(cfg.get("_path", "config.yaml"))
    await broker.load_config()
    logger.info("OCPP Broker ready — waiting for chargers...")

    host = cfg.get("broker", {}).get("host", "0.0.0.0")
    port = cfg.get("broker", {}).get("port", 8765)

    config = uvicorn.Config(app, host=host, port=port, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)
    await server.serve()


def run_broker_server(config_path: str | None):
    cfg = load_broker_config(config_path)
    cfg["_path"] = config_path
    try:
        asyncio.run(main_async(cfg))
    except KeyboardInterrupt:
        logger.info("Interrupted by user, shutting down...")


def main():
    parser = argparse.ArgumentParser(description="Run the OCPP Broker server.")
    parser.add_argument("-c", "--config", help="Path to configuration YAML file", default=None)
    args = parser.parse_args()
    run_broker_server(args.config)


if __name__ == "__main__":
    main()
