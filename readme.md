# ocpp-broker (prototype)

Simple prototype of an OCPP broker/proxy that:
- Accepts charger WebSocket connections
- Validates chargers against registry aggregated from multiple backends
- Forwards charger-origin messages to all connected backends
- Only allows leader backend to send commands to chargers (followers ignored)
- Broker can optionally act as leader

## Quick start

1. Create venv and install:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

2. Start Broker:
```bash
python examples/run_broker.py --host 0.0.0.0 --port 8765
