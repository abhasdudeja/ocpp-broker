# OCPP Broker Documentation

An OCPP 1.6 broker built on the upstream [`ocpp`](https://github.com/mobilityhouse/ocpp) library. Chargers connect to the broker over WebSocket; per organization the broker either **answers them itself** (broker mode) or **relays them to your own backends** (relay mode, with store-and-forward, observer backends and leader failover). A REST API on the same port manages tags and sends commands to chargers.

## Start here

| I want to... | Read |
|--------------|------|
| Install it | [Installation Guide](installation.md) |
| See it work in five minutes | [Quick Start](quick-start.md) |
| Know every setting | [Configuration Guide](configuration.md) |
| Run it in production | [Deployment](deployment.md) |

## Guides

**Concepts**
- [Broker-as-Backend Mode](broker_as_backend.md): the broker is the central system
- [Leader/Follower](leader-follower.md): relaying to backends, buffering, observers, failover
- [OCPP 1.6 Support](ocpp16_features.md): which messages are handled, which commands can be sent
- [Architecture](architecture.md): components and message flow

**Features**
- [Tag Management](tag-management.md): authorization tags
- [MongoDB Integration](mongodb-integration.md): persistence, transaction ids

**Reference**
- [API Reference](api-reference.md): REST routes and the charger WebSocket
- [Configuration Guide](configuration.md)

**Operations**
- [Deployment](deployment.md)
- [Monitoring & Logging](monitoring.md)
- [Troubleshooting](troubleshooting.md)

**Examples**
- [Basic Examples](examples/basic.md)
- [Advanced Examples](examples/advanced.md)

The REST API also documents itself: with the broker running, open `http://localhost:8765/docs` (Swagger UI).

## At a glance

- One port (`broker.port`, default 8765) serves the charger WebSocket (`ws://HOST:8765/{org}/{charger id}`), the REST API, `/health` and the Swagger UI.
- The REST API needs an API key (`OCPP_BROKER_API_KEY`); without one it answers `503`.
- Chargers can be required to authenticate with HTTP Basic (OCPP security profile 1) per organization. There is no TLS in the broker itself; terminate TLS in a reverse proxy.
- MongoDB is optional. Without it nothing is persisted and transaction ids are not durable.
- Single process, sessions in memory.

## Development

```bash
pip install -e ".[tests,lint]"
pytest
ruff check src tests
mypy
```

## External links

- [OCPP 1.6 specification](https://www.openchargealliance.org/protocols/ocpp-16/)
- [Upstream `ocpp` library](https://github.com/mobilityhouse/ocpp)

## License

MIT, as stated in the repository README. (The repository does not currently contain a `LICENSE` file.)
