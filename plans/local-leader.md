# Plan: the broker as the leader of a charger (local backend)

Status (2026-10-04): **B2, B4, the useful part of B3 and B5 are built.** Related: [ui-plan.md](ui-plan.md) (workstream B), [transaction-ids.md](transaction-ids.md), [roadmap.md](roadmap.md).

## The requirement

A charger can be served by the broker itself (broker mode) or by external backends (relay mode, one leader and observe-only followers). The new case: the broker is the **leader** and external backends are **followers**, so a charger gets a complete central system of its own and everything it says is still copied to external systems. When the broker is the leader it must respond to the charger like a real backend: every standard 1.6 message, both directions.

## Decisions (the defaults proposed in ui-plan.md, open questions 6 and 7)

| Question | Decision | Why |
|----------|----------|-----|
| How is it configured? | One `backends` entry with `local: true` (no `url`, optional `id`, default key `broker`). | Reuses the existing list, ids and the transaction id table's backend keys; no second switch to keep consistent. |
| Who may the local backend be? | The leader unless another entry is marked `leader: true`, then a standby. A second local backend, a `url` on it, two entries marked leader, or a lone non-leading local backend are refused at load time with a reason. | One rule that also covers the standby of B4. |
| Which mode is it? | `mode: broker` with followers. | "Mode" says who answers the charger. The console already shows a local leader and followers. |
| Does the broker fail over? | A local *leader* cannot be unreachable, so nothing triggers it. A local *standby* (a local backend that is not the leader) takes over when the external leader fails (B4, below). | Same failover machinery as relay mode; no automatic fail-back, as there is none for external followers either. |

## How it works

`LocalBackend` (`local_backend.py`) runs the `ocpp` library's `ChargePoint` on a loopback queue instead of a WebSocket and offers what the session uses of a `BackendConnection`: `send` (a frame for it), `is_ready`, `close`, a key, a role. What it says comes back through the same `OcppBroker.forward_backend_message` as for any backend: from the leader, to the charger (with the id table translating ids); from a follower, read by the id table and dropped. So the relay loop, the id table, the follower hold queues, retry handling, persistence and failover all treat this broker as one more backend and did not need to change.

- **Local leader:** `session.backend_conn` is a `LocalBackend`; the session runs in BROKER mode (REST commands go through the library) and the relay loop reads the charger's socket and sends its frames to the local backend and, as copies, to the followers.
- **Local standby:** the same object as a follower. Its library runs on copies of the charger's CALLs and its answers are dropped. On promotion `_promote` swaps roles as for any follower and, because the new leader is local, switches the session to BROKER mode and hands the library to the command path.
- **Failure:** if the library stops with an error, a local leader closes the charger's socket (1011) so it reconnects; a local standby is just shown as down.
- Without a table (`transaction_ids.mapping: false`) the charger's CALLs are copied to followers untouched.

(An earlier version of the local leader used two filters on the library's socket adapter. Running it as a backend made the standby almost free, so the filters were removed.)

## B3: backend depth, what was and was not built

Built (`local_transactions.py`, in the `ocpp` handlers, so broker mode and the local leader both get it):

- **Idempotent StartTransaction:** a retry (same connector, tag, meter start and timestamp) gets the same transaction id and is stored once; the tag is judged again.
- **StopTransaction:** always answered (a central system cannot refuse one); a repeat is recognised and stored once; a stop for a transaction the broker did not start is logged; `transactionData` is stored.
- **Open transactions are known**, per charger, bounded (100 open, 200 finished, finished ones for 24 hours), and the console lists them with the broker's own id.

Not built, and why:

| Item | Reason |
|------|--------|
| Boot policy (`Pending` / `Rejected`) | Needs a notion of "known charger" the broker does not have (`charger_auth.credentials` already refuses unknown ones at connect time). Add when someone needs `Pending` to configure a charger before accepting it. |
| Offline detection | The WebSocket ping already closes a dead socket within 40 s. Detecting chargers that are *not connected* is the console's "offline chargers" item and needs MongoDB. |
| Tag, concurrent-transaction and reservation checks | `ConcurrentTx` needs a per-tag policy decision; reservations the broker sets are not yet tracked as such, so a start cannot be checked against them. |
| Post-boot configuration (trigger status, push the local list) | Wanted behaviour varies by deployment; it belongs behind explicit settings. |
| Persistence of the registry | A start retried across a *restart* gets a new id. Relay mode's id table is persisted (T4); this could reuse the same store later. |

## B5: conformance tests

`tests/test_conformance.py`, three modes (broker, local leader, relay) by two directions. Charger to central system: each of the ten messages (and a refused request, and messages only a central system may send), with every reply validated against the OCPP 1.6 response schema in the two modes where the broker answers, and a relay shown to pass requests and replies through unchanged. Central system to charger: each of the 19 catalog commands through the REST API, checking the payload on the wire, the charger's valid answer coming back, and a refusal reported as an error. Finding: in broker mode the `ocpp` library adds an empty `localAuthorizationList` to `SendLocalList` when it is omitted; the table allows it (it means the same).

## Test checks

`tests/test_local_leader.py` and `tests/test_local_transactions.py` hold the behaviour above; deliberate breaks of the new code each fail a test (see the build log).

## Build log

| Piece | Notes |
|-------|-------|
| `local: true` configuration and validation | Refusals have reasons; the local entry is made leader and the others followers. |
| Session, adapter filters, id table integration | One bug-shaped thing found while building: a replaced session's `close()` removed the new session's backend links when both had no leader socket (`None is None`); `close()` now also compares the followers list. |
| Console: orgs, charger links, topology, overview, transactions | Local transactions are listed from the registry when there is no id table. |
| Local transactions registry, idempotent start, stop handling | In memory; see above for what is not covered. |
| Conformance table | About 100 tests, about 100 s because each starts a server; it can move to a shared server if CI time matters. |

## B4: the local standby, what was built

A local backend that is not the leader (another entry is marked `leader: true`) is a silent standby: the same `LocalBackend` as a follower. Tests (`tests/test_local_standby.py`) cover: only the external leader is heard while it is healthy; the standby keeps its own state (the transactions it numbered, in the id table next to the leader's); after the leader fails the standby is promoted and answers with its own rules; a running transaction is stopped under the standby's own number without an "unknown transaction" warning; a start the dead leader never answered is answered exactly once; commands switch from the relay path to the validating library path on promotion; there is no fail-back (the old leader follows when it returns); the first healthy follower in configured order is promoted; a failing standby disturbs nobody and a failing local leader closes the charger's connection.

## Still open

- **Fail-back / hand-back** to the configured external leader when it returns. None exists for external followers either; add it for both together if it is wanted, with the same hold-down rules as the id table needs after a leader change.
- **OCPP 2.0.1 / 2.1** local leader and standby: a second `ChargePoint` per version ([roadmap.md](roadmap.md)); `LocalBackend` and the table's per-backend idea carry over, the id problem mostly disappears because the charger chooses transaction ids in 2.x.
