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
    connect_to_backend: false  # Broker acts as backend (the default is true: relay mode)
    tags:
      - id_tag: "USER001"
        status: "Accepted"
        tag_type: "RFID"
```

## Security

**REST API.** The REST API (`/api/...`, `/orgs/...`) is served on the same port as the
charger WebSocket and requires an API key. Set `OCPP_BROKER_API_KEY` (or `security.api_key`)
and send it as `X-API-Key: <key>` or `Authorization: Bearer <key>`. With no key configured
every REST request returns `503`; `security.allow_unauthenticated_api: true` opens it up for
local development only. `/health` is always open.

**Chargers.** Add `charger_auth.credentials` to an organization to enforce HTTP Basic auth on
the WebSocket upgrade (OCPP 1.6 security profile 1). The username is the charger id in the URL
and the password its authorization key; rejected upgrades get HTTP `401`. Store keys as hashes:

```bash
python -m ocpp_broker.auth          # prompts for the key and prints a password_hash
```

```yaml
organizations:
  - name: "MyOrg"
    charger_auth:
      credentials:
        CP001:
          password_hash: "pbkdf2_sha256$200000$..."
```

An organization with no credentials accepts any charger (and logs a warning at startup).
Basic auth does not encrypt anything: terminate TLS in front of the broker (profile 2) if the
network is not trusted. Browsers can only call the REST API from origins listed in
`security.cors.allow_origins`.

## Relay mode behaviour

When an organization sets `connect_to_backend: true` the broker relays frames to its backends:

- Frames for an unreachable backend are buffered (`backend_buffer_size`, default 200) and
  delivered in order on reconnect. A CALL that waits longer than `backend_outage_timeout`
  (default 30 s) is answered with a `CALLERROR`.
- Follower backends receive a copy of every charger-initiated CALL (observe-only; their replies
  are discarded). If the leader is unreachable for `leader_failover_timeout` (default 15 s,
  `0` disables) the first healthy follower becomes leader; there is no automatic fail-back.
- The broker does not validate relayed frames; the backend does. In broker mode the `ocpp`
  library validates every message.
- In relay mode the backend assigns transaction ids. In broker mode they come from a
  per-organization MongoDB counter; without MongoDB the broker falls back to an in-memory
  counter and logs a loud warning, because those ids are not durable.

## Documentation

For complete documentation, see the [docs/](docs/README.md) directory:

- [Installation Guide](docs/installation.md)
- [Quick Start](docs/quick-start.md)
- [Configuration Guide](docs/configuration.md)
- [Broker-as-Backend Mode](docs/broker_as_backend.md)
- [Leader/Follower](docs/leader-follower.md)
- [OCPP 1.6 Support](docs/ocpp16_features.md)
- [Tag Management](docs/tag-management.md)
- [MongoDB Integration](docs/mongodb-integration.md)
- [API Reference](docs/api-reference.md)
- [Architecture Overview](docs/architecture.md)
- [Web Console](docs/web-console.md) (the browser UI at `/ui`)
- [Deployment](docs/deployment.md), [Monitoring](docs/monitoring.md), [Troubleshooting](docs/troubleshooting.md)

## Development

```bash
# Install with test and lint dependencies
pip install -e ".[tests,lint]"

# Run tests
pytest

# Lint and type-check (what CI runs)
ruff check src tests
mypy

# Build package
python -m build
```

## License

MIT License - see LICENSE file for details.

## Links

- [OCPP 1.6 Specification](https://www.openchargealliance.org/protocols/ocpp-16/)
- [Upstream ocpp Library](https://github.com/mobilityhouse/ocpp)

