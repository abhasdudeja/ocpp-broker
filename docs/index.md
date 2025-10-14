# ⚡ OCPP Multi-Organization Broker

Welcome to the **OCPP Multi-Organization Broker** documentation.

This broker:
- Acts as an OCPP proxy between chargers and multiple backends
- Supports multiple organizations
- Provides a REST API for dynamic control and runtime management

## Quick Start

1. Configure `config.yaml` with your organizations and backends.
2. Create and activate a virtual environment, then install requirements:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
