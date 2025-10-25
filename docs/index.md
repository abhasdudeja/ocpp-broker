# ⚡ OCPP Multi-Organization Broker

Welcome to the **OCPP Multi-Organization Broker** documentation.

This broker:
- Acts as an OCPP proxy between chargers and multiple backends
- Supports multiple organizations with independent configurations
- Provides comprehensive tag management for authorization
- Offers both traditional proxy mode and broker-as-backend mode
- Includes a REST API for dynamic control and runtime management
- Supports OCPP 1.6 with full command coverage

## Quick Start

1. Configure `config.yaml` with your organizations and backends.
2. Create and activate a virtual environment, then install requirements:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
