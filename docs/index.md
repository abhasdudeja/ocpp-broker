# OCPP Broker

An OCPP 1.6 broker between chargers and your central system. Per organization it either:

- **answers chargers itself** (broker mode): authorization from a tag list, transaction ids, optional MongoDB storage; or
- **relays them to your own backends** (relay mode): frames are forwarded unchanged, buffered while a backend is down, copied to observer backends, and moved to another backend if the leader stays unreachable.

A REST API on the same port manages tags and sends OCPP commands to connected chargers, returning the charger's actual reply.

## Quick start

```bash
pip install ocpp-broker
export OCPP_BROKER_API_KEY=change-me      # needed for the REST API
ocpp-broker-server -c config.yaml
curl http://localhost:8765/health
```

Chargers connect to `ws://localhost:8765/{organization}/{charger id}`.

A minimal `config.yaml`:

```yaml
broker:
  port: 8765

organizations:
  - name: "MyOrg"
    connect_to_backend: false   # false = broker mode; true (the default) = relay mode, needs `backends`
    tags:
      - id_tag: "ADMIN001"
        status: "Accepted"
```

Continue with the [Quick Start](quick-start.md), or see the full [documentation index](README.md).
