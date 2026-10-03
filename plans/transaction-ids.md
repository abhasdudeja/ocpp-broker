# Plan: transaction id mapping in relay mode

Status: research and proposal, nothing built. Written 2026-10-04.
Related: [ui-plan.md](ui-plan.md) (the console shows this table), [roadmap.md](roadmap.md) (OCPP 2.x changes the problem).

## Summary

In OCPP 1.6 the **backend** chooses a transaction's id and the charger then quotes it in later messages. With one charger and several backends there are several candidate ids for one charging session, and **the broker currently picks none of them deliberately**: the leader's id reaches the charger untouched, and followers get copies of later messages carrying an id they never issued. This is a data-integrity bug today, not only a failover problem, and the damage is silent (energy attributed to the wrong session).

Recommendation: an **internal id table per charger** (the `TransactionIdMap` below). The charger keeps seeing one stable id per transaction; each backend is always spoken to in its own ids; the broker rewrites the six id-carrying messages in both directions. Where possible the charger-visible id equals the leader's own id, so single-leader traffic is byte-identical to today and a lost table degrades gracefully.

Proposed priority: this is correctness of the product's core feature, so build stages T1 to T3 **before** more console work (see "Priority").

## 1. Evidence

### 1.1 Which messages carry ids (OCPP 1.6, read from the JSON Schemas shipped in the installed `ocpp` library)

| Message | Direction | Field | Id space |
|---------|-----------|-------|----------|
| `StartTransaction` response | backend to charger | `transactionId` | **transaction**, assigned by the backend |
| `MeterValues` | charger to backend | `transactionId` (optional) | transaction |
| `StopTransaction` | charger to backend | `transactionId` | transaction |
| `RemoteStopTransaction` | backend to charger | `transactionId` | transaction |
| `SetChargingProfile` | backend to charger | `csChargingProfiles.transactionId` (optional) | transaction |
| `RemoteStartTransaction` | backend to charger | `chargingProfile.transactionId` (optional) | transaction |
| `ReserveNow`, `CancelReservation` | backend to charger | `reservationId` | **reservation**, chosen by the backend |
| `StartTransaction` | charger to backend | `reservationId` (optional) | reservation (echo) |

Everything else (StatusNotification, Heartbeat, Authorize, BootNotification and so on) carries no transaction id. `DataTransfer` payloads are vendor-defined and cannot be inspected.

The schema types `transactionId` as an integer with no bound; the specification defines it as a 32-bit integer. Verify the exact wording and range against the OCPP 1.6 specification (not in the repository; the web search did not return the spec text).

### 1.2 OCPP 2.0.1 and 2.1 change this

In the 2.x schemas `transactionId` is a **string of at most 36 characters chosen by the charger** and carried in `TransactionEvent`, so every backend sees the same id and **the transaction-id conflict disappears**. `reservationId` and charging-profile ids are still integers chosen by the backend. The mapping is therefore a 1.6 component: build it behind an interface (`IdTranslator`) so 2.x can plug in a no-op for transactions. See [roadmap.md](roadmap.md).

### 1.3 What the code does today (traced)

| Step | Where | Behaviour |
|------|-------|-----------|
| Charger frame in | `session._relay_loop` | parsed by `middleware.process_charger_to_backend`, sent to the leader unchanged |
| Copy to followers | `session._fan_out_to_followers` | CALLs only, unbuffered, unchanged |
| Leader reply | `broker.forward_backend_message` to `session.send_to_charger` | forwarded unchanged: the charger's transaction id is the leader's id |
| Follower reply | same function, the `not is_leader` branch | **discarded**: a follower's own id for the transaction is thrown away |
| Id handling anywhere in the relay path | grep of `session`, `backend_manager`, `middleware`, `broker` | none (the only transaction-id code is the local counter used in broker mode) |

### 1.4 Prior art

A web search found two open-source splitters (the Joulo proxy and `scruysberghs/ocpp-proxy`). Neither documents how it handles transaction ids; the pages are silent on mapping, on secondary backends seeing unknown ids and on failover. No authoritative reference was found, so this plan rests on the specification, the schemas and our code. One of the repositories' source could be read if a comparison is wanted.

## 2. How it fails today

### 2.1 The lockstep illusion (silent misattribution)

Leader P and follower S each mint ids from 1. While both see every `StartTransaction` their ids happen to match, so everything looks correct, and tests that run two fake backends in lockstep pass. It breaks the first time they diverge, which any follower outage causes (copies to a down follower are dropped, not buffered):

1. Charger transactions A, B, C. P issues 1, 2, 3. S was down for B and issues 1 (A), 2 (C).
2. The charger sends `StopTransaction(2)` for B. P ends B correctly. S receives the copy and **ends its own transaction 2, which is C**.
3. The charger later sends `StopTransaction(3)` for C. S has no transaction 3.

S now shows B's stop applied to C and C still running or unknown. Nothing logs an error.

### 2.2 Failover

- The charger holds the old leader's ids for transactions in progress. The new leader knows them only under its own ids (if it saw the start at all). `StopTransaction(L)` arrives at a backend that has no transaction L: the session is never billed or closed.
- The new leader issues its own ids for new transactions, which can **equal an id the charger already holds** for a transaction from the old leader. The charger then has two live transactions with the same id.
- A command from the new leader quotes its own id (`RemoteStopTransaction(F)`); the charger does not know F.

### 2.3 Retries and duplicates

During a leader outage or a failover the broker answers a held `StartTransaction` with a CALLERROR so the charger retries. The retry is a new CALL; the leader mints a second transaction for the same charging session.

### 2.4 Charger-held state the broker does not own

The same class of problem exists for other identifiers the **backend** chooses and the **charger** stores (listed in section 8): reservation ids, charging-profile ids, the local authorization list version, configuration values.

## 3. Options

| | A. Pass the leader's id through, map followers only | B. Broker mints every charger-visible id | **C. Table always, leader-preferred (recommended)** |
|---|---|---|---|
| Charger sees | the leader's id | a broker-minted id, unrelated to any backend | the leader's id when it is free, else a fresh unique id |
| Leader traffic | untouched | rewritten both ways | untouched in the normal case |
| Followers | rewritten from a per-follower map | rewritten | rewritten |
| Failover | collisions at the charger possible | none | none (collisions are detected and remapped) |
| Lost table (restart, no persistence) | leader flow fine; followers lose sync | **active transactions cannot be translated** | leader flow fine (identity is correct); followers lose sync |
| Debuggability | ids match the leader's dashboard | charger log and backend dashboards disagree | match in the normal case |
| Complexity | low, but incomplete (no failover) | medium | medium |

Option A does not survive failover. Option B is the cleanest model but turns every transaction into something that depends on a table the broker can lose, and makes ids unreadable to operators. **Option C** keeps A's safe failure mode and B's correctness; the extra cost is one uniqueness check when a leader's id is first recorded.

## 4. Design (option C)

### 4.1 The table

Keyed by `(org, charger)`, **owned by the broker, not by the session**. A charger's WebSocket drops and reconnects during a charge routinely, and the session object is recreated each time, so the table must outlive it.

One record per transaction:

| Field | Meaning |
|-------|---------|
| `charger_id_value` | the id the charger holds (`T`) |
| `backend_ids` | map `backend key -> that backend's id for it` (`L`, `F`, ...) |
| `start_key` | `(connector_id, id_tag, meter_start, timestamp)`: identifies a retry of the same start |
| `state` | `pending` (start sent, leader id not yet known), `open`, `closed` |
| `degraded` | a backend never learned its id; copies to it are skipped |
| `created`, `closed_at` | for retention |

A **backend key** must be stable across reconnects and failover: `backends[].id` if given, else its URL. Today `id` is only a log label; it becomes meaningful and the docs must say so.

### 4.2 Learning ids

1. Charger `StartTransaction` CALL: create a `pending` record, remember `message id -> record`, forward to the leader and copy to followers as today.
2. Leader CALLRESULT for that message id: read `transactionId = L`. Choose `T = L` if no open record for this charger already uses `T`, else a fresh id from the broker's per-org counter (`next_transaction_id`). Record `backend_ids[leader] = L`. Forward to the charger with `T` (rewritten only if `T != L`). The record becomes `open`.
3. Follower CALLRESULT for its copy of the same message id (today discarded): record `backend_ids[follower] = F`.
4. A CALLERROR or timeout for the start: drop the pending record (or mark that backend as not having it).

### 4.3 Rewrite rules

To backend `b`, for charger frames:

| Frame | Action |
|-------|--------|
| `StartTransaction` | unchanged (`reservationId` mapped in stage T5) |
| `MeterValues`, `StopTransaction` with `transactionId = T` | replace with `backend_ids[b]`. If `b` has no id: **leader** gets the frame unchanged and a warning is logged (identity is the safe guess); a **follower** is skipped for this frame and the record is marked `degraded` |
| a `StopTransaction` | after delivery, mark the record `closed` (kept for `retain_closed` seconds so a retried stop still maps) |

From the leader, to the charger:

| Frame | Action |
|-------|--------|
| `StartTransaction` result | section 4.2 |
| `RemoteStopTransaction.transactionId`, `SetChargingProfile.csChargingProfiles.transactionId`, `RemoteStartTransaction.chargingProfile.transactionId` | reverse-map the leader's id to `T`. Unknown ids pass through unchanged with a warning |

Commands sent through the REST API use **charger-visible** ids (`T`), which are what `GET .../transactions` returns and what an operator sees on the charger.

Only these six actions are parsed and rewritten; a cheap substring check (`transactionId` in the text) keeps every other frame on the current untouched path. The relay documentation currently says frames are forwarded untouched; that sentence changes.

### 4.4 Ordering: waiting for a follower's id

A follower's reply to the `StartTransaction` copy is asynchronous and may arrive after the charger's first `MeterValues`. Copies of later messages for that transaction are **held in order per follower** until the id arrives, then released; after `follower_wait` seconds (default proposal 5) the held copies are dropped, the follower is marked `degraded` for that transaction and a warning is logged. The hold never touches the leader's path.

### 4.5 Failover

When a follower is promoted:

- Its ids for open transactions are already in the table (learned in step 3), so `StopTransaction(T)` is rewritten to its own id and its commands are reverse-mapped to `T`.
- Transactions it never saw (it was down for the start) are `degraded` for it: frames are forwarded unchanged and a warning is logged. This is a **known loss** and is surfaced in the console, not hidden.
- New transactions on the new leader follow section 4.2 step 2: its id is used if free, else remapped. The collision of section 2.2 cannot reach the charger.
- The old leader becomes a follower. It keeps its own ids for transactions it started; for transactions started after it fell away it is `degraded` and is skipped, not sent wrong ids.

### 4.6 Duplicate starts

A `StartTransaction` whose `start_key` matches an open record is a retry. Proposal: **do not forward it again**; answer the charger from the stored result (rewritten if needed) so the backend sees one transaction. This makes the broker answer on a backend's behalf for exact duplicates only. If you prefer not to, the alternative is to forward it and map both backend ids to one `T`; that leaves a duplicate transaction in the backend. See open question 3.

### 4.7 Persistence and restart

- **With MongoDB:** write-through to a `transaction_id_map` collection; open records are loaded when a charger connects, so a broker restart or a charger reconnect keeps them. The collection gets a TTL on `closed_at`.
- **Without MongoDB:** in memory only. After a restart the table is empty; leader traffic still works (identity), followers lose sync for transactions in progress, and a warning at startup says so. This is the reason for choosing option C over B.

### 4.8 Configuration (proposal)

```yaml
organizations:
  - name: orgA
    transaction_ids:
      mapping: true        # default: on when the organization has at least one follower
      follower_wait: 5     # seconds to hold copies for a follower's id
      dedupe_start: true   # answer retried StartTransactions from the stored result
      retain_closed: 86400 # seconds a closed record is kept
```

An organization with a single backend behaves exactly as today.

### 4.9 Where it plugs in

A `TransactionIdMap` (pure logic: records, allocation, rewrite decisions, no I/O) and a thin adapter in the relay path:

- `session._relay_loop`: before the leader send and the fan-out, ask the map for the frame to send to each backend.
- `broker.forward_backend_message`: leader frames pass through the map; follower frames are fed to it instead of being dropped.
- `session._promote` and the reconnect path tell the map about role changes.
- The local backend of the topology plan (workstream B2 in `ui-plan.md`) is just another backend key in the table.

### 4.10 Observability

Per charger: open transactions with `T`, each backend's id, state and `degraded`; counters for rewrites, held copies, skipped copies, remaps and unmapped frames. Events `transaction.mapped`, `transaction.degraded`, `transaction.remapped` on the console's event stream. The console shows both ids side by side so an operator can match a backend dashboard to a charger log.

## 5. Tests

The existing `FakeBackend` only records frames. Extend it (and keep `ScriptedCharger`) so a backend **mints ids by a script** (start value, offset, "skip the first start", a delay before replying) and can send commands.

| # | Scenario | Asserts |
|---|----------|---------|
| S1 | Two backends whose ids differ | each backend receives only ids it issued; the charger sees one id |
| S2 | Follower missed one start (section 2.1) | no frame carries a wrong id to the follower; **expected to fail on the current code** (write it first and confirm), which is the proof the test matters |
| S3 | Follower answers late, or never | copies held in order, then released; after the wait they are skipped and `degraded` |
| S4 | Failover during a transaction | `StopTransaction` reaches the new leader under its id; its `RemoteStopTransaction` reaches the charger under `T`; a new leader id that collides with a live `T` is remapped |
| S5 | Retried `StartTransaction` | one transaction at the backend, the same `T` to the charger |
| S6 | Charger reconnects mid-transaction | mapping survives the new session |
| S7 | Restart, with and without the fake MongoDB | restored with it; leader identity fallback and a warning without it |
| S8 | `SetChargingProfile` and `RemoteStartTransaction` profiles with ids | reverse-mapped; reservation ids once T5 lands |
| S9 | Randomised interleavings of starts, stops, outages and promotions (seeded) | invariants: ids unique among a charger's open transactions; every id-bearing frame reaches a backend carrying an id that backend issued, or is not delivered; with no conflicts the leader's frames are byte-identical to the input |

Mutation checks as before: skipping the follower rewrite, the collision check, the hold queue or the reverse mapping must each fail a test.

## 6. Stages

| Stage | Content | Size |
|-------|---------|------|
| T1 | `TransactionIdMap` core and unit tests, plus the scripted-id `FakeBackend` | M |
| T2 | Wire into the relay path: capture leader and follower results, rewrite both ways, hold queue; S1, S2, S3, S5 | M |
| T3 | Failover, reconnect and collision remapping; S4, S6 and the randomised test S9 | M |
| T4 | Persistence and restart, backend keys, configuration and docs (`leader-follower.md`, `configuration.md`, `architecture.md`); S7 | M |
| T5 | Reservation ids and charging-profile ids; S8 | S |
| T6 | `GET /api/chargers/{org}/{id}/transactions` with a typed response, events, console view | S |

## 7. Priority

T1 to T3 fix a silent data-integrity bug in the headline feature and are independent of the console's UI work. Proposal: do **T1 to T3 next**, before further console phases, then T4. T5 and T6 can follow the console's state model (B1) so they share it.

## 8. Related identifier spaces (not solved by this plan)

These are also chosen by a backend and stored by the charger, so they conflict in the same way after a failover. They need follow-up decisions, probably a post-failover reconciliation step:

| Space | Messages | Risk |
|-------|----------|------|
| Reservation ids | `ReserveNow`, `CancelReservation`, `StartTransaction.reservationId` | Same pattern as transactions; mapped in T5 |
| Charging profile ids | `SetChargingProfile`, `ClearChargingProfile` | A new leader that reuses an id with the same purpose and stack level silently **replaces** a profile the old leader installed |
| Local authorization list version | `SendLocalList`, `GetLocalListVersion` | The charger holds one list; each backend counts versions separately |
| Configuration | `ChangeConfiguration` | The charger holds one set of values the new leader has not seen |

After a promotion the broker can read the charger's state itself through its REST command path (`GetLocalListVersion`, `GetConfiguration`, `GetCompositeSchedule`), but the answers go to the broker, not to the new leader. How to tell the backend (a synthetic copy of the reply, an event, or nothing) is an open design question for a later plan.

## 9. Open questions

1. **Priority:** build T1 to T3 before more console work (recommended), or after the live console?
2. **Default:** mapping on whenever an organization has followers (proposed), or opt-in per organization?
3. **Retried `StartTransaction`:** answer from the stored result without forwarding (proposed), or forward and map both ids to one?
4. **Degraded transactions:** when a promoted follower never saw a transaction start, is forwarding the stop unchanged acceptable, or should the broker drop it and surface an alert?
5. **Backend keys:** require `backends[].id` (unique, stable) when an organization has followers, or fall back to the URL?
6. **Non-MongoDB deployments:** is "mapping lost on restart, leader keeps working" acceptable, or should a small local file store be an option?

## 10. Risks

| Risk | Mitigation |
|------|------------|
| Rewriting frames in a proxy that was transparent: a bug corrupts a live transaction | Only six actions, identity in the normal case, the randomised invariant test, mutation checks |
| The follower's reply arrives after the charger's next frame | Hold queue with a timeout, tested in S3 |
| Table lost or stale | Option C degrades to today's behaviour for the leader; persistence in T4 |
| Backend keys change in the config between restarts | Persisted records are looked up by key; unknown keys are ignored with a warning |
| Specification details not verified from the source | Check section 1.1's wording against the OCPP 1.6 spec before T2 |
