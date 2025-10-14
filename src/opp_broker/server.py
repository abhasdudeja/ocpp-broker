import asyncio
import logging
import signal
import argparse
import websockets
from .broker import OcppBroker
from .config import load_config
from .api_server import start_api

logger = logging.getLogger("ocpp_broker.server")

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s"
    )

async def init_orgs_from_config(broker: OcppBroker, cfg: dict):
    for org in cfg.get("organizations", []):
        await broker.add_organization(org["name"], org["backends"])

async def start_ws_server(broker: OcppBroker, host: str, port: int):
    server = await websockets.serve(broker.handle_charger, host, port)
    logger.info(f"OCPP Broker listening on ws://{host}:{port}")
    return server

def run_broker_server(config_path: str = None):
    setup_logging()
    cfg = load_config(config_path)
    broker_cfg = cfg["broker"]
    host, port = broker_cfg.get("host", "0.0.0.0"), broker_cfg.get("port", 8765)
    broker = OcppBroker()
    broker._cfg_path = config_path or "config.yaml"
    loop = asyncio.get_event_loop()

    async def _run():
        await init_orgs_from_config(broker, cfg)
        await start_api(broker, port=8080)
        ws_server = await start_ws_server(broker, host, port)
        logger.info("Multi-org OCPP Broker + REST API running. Press Ctrl+C to stop.")
        await ws_server.wait_closed()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, loop.stop)

    try:
        loop.run_until_complete(_run())
    finally:
        loop.run_until_complete(broker.close_all_backends())
        loop.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()
    run_broker_server(args.config)
