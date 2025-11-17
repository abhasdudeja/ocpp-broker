# OCPP Broker

A comprehensive OCPP 1.6 broker built on the upstream [`ocpp`](https://github.com/mobilityhouse/ocpp) Python library, providing bi-directional message routing, tag management, and multi-backend support.

## Features

- ✅ **OCPP 1.6 Compliant** - Powered by the upstream `ocpp` library for spec-compliant parsing and validation
- ✅ **Multi-Backend Support** - Relay messages to upstream backends with leader/follower logic
- ✅ **Broker-as-Backend Mode** - Handle OCPP commands locally without external backends
- ✅ **Tag Management** - Comprehensive tag authorization with REST API
- ✅ **Multi-Organization** - Support multiple organizations with isolated configurations
- ✅ **Session Management** - Clean session lifecycle with automatic cleanup

## Quick Start

### Installation

```bash
pip install ocpp-broker
```

### Basic Usage

```bash
# Start the broker server
ocpp-broker-server

# Or with custom config
ocpp-broker-server -c /path/to/config.yaml
```

### Configuration

Create a `config.yaml` file:

```yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: "MyOrg"
    connect_to_backend: false  # Broker acts as backend
    tag_management:
      enabled: true
    tags:
      - id_tag: "USER001"
        status: "Accepted"
        tag_type: "RFID"
```

## Documentation

For complete documentation, see the [docs/](docs/) directory:

- [Installation Guide](docs/installation.md)
- [Quick Start](docs/quick-start.md)
- [Configuration Guide](docs/configuration.md)
- [Architecture Overview](docs/architecture.md)
- [Tag Management](docs/tag-management.md)
- [API Reference](docs/api-reference.md)

## Development

```bash
# Install with dev dependencies
pip install -e .[tests]

# Run tests
pytest

# Build package
python -m build
```

## License

MIT License - see LICENSE file for details.

## Links

- [OCPP 1.6 Specification](https://www.openchargealliance.org/protocols/ocpp-16/)
- [Upstream ocpp Library](https://github.com/mobilityhouse/ocpp)

