# ⚡ OCPP Multi-Organization Broker — Full Documentation

Welcome to the **official documentation** for the  
**OCPP Multi-Organization Broker** — a middleware layer between EV chargers and OCPP backends.  

This broker enables multiple organizations to connect their chargers through a single gateway, with independent backend hierarchies and a REST API for runtime control.

---

## 🧭 Overview

The **OCPP Multi-Organization Broker** acts as a WebSocket bridge:
- Each **organization** has its own set of **OCPP backends**.
- Each organization designates **one backend as a leader** (others are followers).
- Chargers connect to the broker via WebSocket URLs that identify their organization and charger ID.
- The broker accepts or rejects chargers based on their registration in backend registries.
- Only the **leader** backend may send control commands to chargers.

---

## ⚙️ Features

| Feature | Description |
|----------|-------------|
| 🏢 **Multi-organization** | Separate organizations, each with independent backends and registries |
| 👑 **Leader / Follower system** | Only leader backend issues control commands |
| ⚙️ **Dynamic REST API** | Add/remove backends, promote leaders, reload configuration |
| 🔁 **Config Reload** | Reload `config.yaml` at runtime |
| 💬 **OCPP message routing** | Chargers ↔ Backends via Broker |
| 🔒 **Org-specific routing** | Isolation between organizations |
| 🌐 **FastAPI integration** | REST management + OpenAPI docs |

---

## 🏗️ Architecture

```
               ┌──────────────────────────────┐
               │        REST API (FastAPI)     │
               │ Manage Orgs, Backends, Leaders│
               └──────────────┬───────────────┘
                              │
                      ┌───────▼────────┐
                      │ OCPP Broker     │
                      │ (Async Core)    │
                      │ - Manages orgs  │
                      │ - Routes msgs   │
                      └───────┬────────┘
                              │
           ┌──────────────────┼──────────────────┐
           │                  │                  │
     [OrgA Chargers]    [OrgB Chargers]    [OrgC Chargers]
           │                  │                  │
       ┌────┴────┐         ┌────┴────┐         ┌────┴────┐
       │Backends │         │Backends │         │Backends │
       │(Leader +│         │(Leader +│         │(Leader +│
       │Followers)│         │Followers)│         │Followers)│
       └──────────┘         └──────────┘         └──────────┘
```

---

## 🧾 Configuration (`config.yaml`)

Your configuration defines the broker host and per-organization backends.

```yaml
broker:
  host: 0.0.0.0
  port: 8765

organizations:
  - name: OrgA
    backends:
      - id: backendA1
        url: ws://localhost:9001
        leader: true
      - id: backendA2
        url: ws://localhost:9002
        leader: false

  - name: OrgB
    backends:
      - id: backendB1
        url: ws://localhost:9011
        leader: true
      - id: backendB2
        url: ws://localhost:9012
        leader: false
```

### Connection path format
Chargers connect using:
```
ws://<broker-host>:<port>/<org_name>/<charger_id>
```

Example:
```
ws://localhost:8765/OrgA/CP_001
```

---

## 🚀 Running the Broker

```bash
python -m ocpp_broker.server --config config.yaml
```

The broker automatically launches:
- WebSocket OCPP Server → `ws://0.0.0.0:8765`
- REST API (FastAPI) → `http://0.0.0.0:8080/docs`

---

## 🌐 REST API Reference

The REST API enables runtime configuration without restarting the broker.

### 📍 Base URL
```
http://localhost:8080
```

### 🧭 Endpoints

| Method | Endpoint | Description |
|---------|-----------|-------------|
| `GET` | `/orgs` | List all organizations with leaders |
| `GET` | `/orgs/{org}/backends` | List all backends of an organization |
| `POST` | `/orgs/{org}/backends` | Add a backend dynamically |
| `DELETE` | `/orgs/{org}/backends/{id}` | Remove a backend |
| `POST` | `/orgs/{org}/leader/{id}` | Promote a backend to leader |
| `POST` | `/reload` | Reload configuration from file |

---

### 📘 Examples

**List all orgs:**
```bash
curl http://localhost:8080/orgs
```

Response:
```json
[
  {"name": "OrgA", "num_backends": 2, "leader": "backendA1"},
  {"name": "OrgB", "num_backends": 2, "leader": "backendB1"}
]
```

**Add a new backend:**
```bash
curl -X POST http://localhost:8080/orgs/OrgA/backends   -H "Content-Type: application/json"   -d '{"id": "backendA3", "url": "ws://localhost:9003", "leader": false}'
```

**Promote a backend to leader:**
```bash
curl -X POST http://localhost:8080/orgs/OrgA/leader/backendA3
```

**Reload configuration:**
```bash
curl -X POST http://localhost:8080/reload
```

---

## 🔌 Charger Interaction

1. A charger connects via:
   ```
   ws://<broker>:<port>/<org>/<charger_id>
   ```
2. The broker checks if the charger ID is present in the backend registry.
3. If registered → ✅ connection accepted.
4. If not registered → ❌ connection rejected.

---

## 🧠 Backend Behavior

Each backend (connected via its own WebSocket) should:
- Send a registry update to the broker:
  ```json
  {"type": "registry", "chargers": ["CP_001", "CP_002"]}
  ```
- Send leader announcements (optional):
  ```json
  {"type": "leader", "leader_id": "backendA1"}
  ```
- Only the **leader** backend can send control commands to chargers, for example:
  ```json
  {"target": "CP_001", "payload": {"action": "RemoteStartTransaction"}}
  ```

---

## 🧩 REST + WebSocket Parallel Execution

Both services (REST API + WebSocket broker) run concurrently under `asyncio`,  
so you can modify backends while OCPP sessions remain active.

---

## 📚 Programmatic Wiki Access

You can view the docs from Python:

```python
from ocpp_broker import read_wiki
print(read_wiki("index"))
```

Output → renders this documentation text.

---

## 🧩 Packaging

To include documentation in the package:
```toml
[tool.setuptools.package-data]
"ocpp_broker" = ["docs/*.md"]
```

---

## 💡 Example Workflow

1. Configure backends and organizations in `config.yaml`.
2. Start the broker.
3. Connect chargers using `/org/charger_id`.
4. Verify connections via `/orgs` and `/orgs/{org}/backends`.
5. Send test commands via the leader backend.
6. Reload configuration or add new backends dynamically.

---

## 🏁 Summary

| Component | Technology | Description |
|------------|-------------|-------------|
| WebSocket Broker | `websockets` (asyncio) | Routes OCPP messages |
| REST API | `FastAPI + Uvicorn` | Dynamic management |
| Config Parser | `PyYAML` | Multi-org, leader/follower setup |
| Registry | Async in-memory | Tracks charger IDs |
| Docs | Markdown (repo + package) | Wiki-style documentation |

---

## 🔗 References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Websockets Library](https://websockets.readthedocs.io/)
- [Open Charge Alliance - OCPP Specs](https://www.openchargealliance.org/)
- [PyYAML Docs](https://pyyaml.org/wiki/PyYAMLDocumentation)

---

## 🏁 Conclusion

The **OCPP Multi-Organization Broker** bridges multiple OCPP systems with a single point of control.
It simplifies fleet, depot, and utility charger operations while enabling flexible management
through a unified REST API and multi-org architecture.

> “One broker. Infinite interoperability.”
