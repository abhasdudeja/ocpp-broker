# Roadmap

Status: direction, nothing built. Written 2026-10-04. The web console plan is in [ui-plan.md](ui-plan.md).

## Future: OCPP 2.0.1, then 2.1

The broker speaks **OCPP 1.6 only** today. Support for **OCPP 2.0.1**, and later **2.1**, is planned. This is a direction, not a commitment to a date. Scope and order are open.

### What is true today

- The upstream `ocpp` library (2.1.0 is installed) already ships `v16`, `v201` and `v21` packages, so message parsing and validation are available for all three.
- Our code assumes 1.6 in about 16 places across `config.py`, `server.py`, `session.py`, `backend_manager.py` and `charge_point.py` (the subprotocol default, `ocpp.v16` imports, the 19 typed command routes, the 10 charger handlers).
- `pyproject.toml` pins `ocpp>=1.0.0`. Raise the minimum when this work starts.
- The protocol is chosen **per organization** (`ocpp_subprotocol`, default `ocpp1.6`); a backend can override it.

### How hard each part is

| Part | Effort | Why |
|------|--------|-----|
| **Relay mode** (leader/follower, outbox, failover) | Small | Frames are forwarded untouched and the logic only looks at the message type (CALL, CALLRESULT, CALLERROR), which is the same in every version. Needs the subprotocol allow-list widened and the version carried per session. |
| **Console state model** (connectors, boot info, transactions) from relayed frames | Medium | Frame parsing is per version. 2.x has `TransactionEvent` instead of Start/StopTransaction and EVSE/connector instead of connector id. |
| **Broker as the backend (local leader)** | Large | A separate `ChargePoint` implementation per version. 2.0.1 has about 64 actions; the data model differs (device model with Get/SetVariables, `TransactionEvent`, `idToken` types, remote start and stop renamed `RequestStartTransaction` / `RequestStopTransaction`). 2.1 adds more (bidirectional charging, DER control, tariffs, and others). |
| **Transaction id mapping** ([transaction-ids.md](transaction-ids.md)) | Mostly disappears | In 2.x the charger chooses the transaction id (a string of up to 36 characters), so every backend sees the same one. Reservation and charging-profile ids remain backend-chosen integers. |
| **Tags** | Medium | `idToken` has a type; authorization data and the local list differ. |
| **Charger authentication** | Medium | 2.0.1 defines security profiles 2 and 3 (TLS, client certificates). Today only profile 1 (HTTP Basic) exists, and the broker runs plain `ws://` behind a proxy. |
| **MongoDB storage and history** | Medium | Collections are named after 1.6 actions; transactions need a version-neutral shape. |
| **Version translation** (a 1.6 charger talking to a 2.x backend or the reverse) | Very large | Not planned. Relay requires the charger and its backends to speak the same version. |

### Suggested order

1. **R1, groundwork (during the console phases, almost free).** Make what we are about to build version-aware, so it needs no rework later (rules below).
2. **R2, 2.0.1 in relay mode.** Accept `ocpp2.0.1` per organization; the console shows the topology for these chargers. Needs no CSMS logic.
3. **R3, 2.0.1 as the local backend.** A 2.0.1 `ChargePoint`, tag/idToken handling, state model, command catalog; the same conformance-test approach as workstream B in the UI plan.
4. **R4, 2.1.** Built on R3, as a superset where possible.

### Rules to follow now (so R1 costs nothing)

1. **Protocol-neutral state model.** Name things `evse` and `connector` in the console's state model, not 1.6's single `connector_id`, and keep transactions version-neutral.
2. **Version in the API.** Responses for organizations and chargers carry `ocpp_version`; the command catalog endpoint takes a `version` parameter and, for now, only serves `1.6`.
3. **No "1.6" in console text.** Show the protocol as a badge taken from the data.
4. **Catalog from JSON Schemas.** The planned generated command forms already work this way; the 2.x schemas are in the same library.
5. **Tests parametrized by version** wherever a conformance table is written, with one entry today.
6. **Do not add more 1.6-only assumptions** to the relay path (message-type checks only, as now).

### Open questions

1. Which comes first for you, 2.0.1 relay (R2) or 2.0.1 as the local backend (R3)?
2. Do you need 2.0.1 security profiles 2 and 3 (TLS with client certificates) in the broker itself, or is TLS always terminated at a proxy?
3. Is mixing versions inside one organization needed (some chargers on 1.6, some on 2.0.1), or is one version per organization enough? (One per organization is the current model and the simplest.)
4. Is OCPP certification a goal? It changes how strictly each behaviour must follow the specification.
