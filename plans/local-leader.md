# Plan: the broker as the leader of a charger (local backend)

Status (2026-10-04): **B2, the useful part of B3 and B5 are built; B4 is deferred.** Related: [ui-plan.md](ui-plan.md) (workstream B), [transaction-ids.md](transaction-ids.md), [roadmap.md](roadmap.md).

## The requirement

A charger can be served by the broker itself (broker mode) or by external backends (relay mode, one leader and observe-only followers). The new case: the broker is the **leader** and external backends are **followers**, so a charger gets a complete central system of its own and everything it says is still copied to external systems. When the broker is the leader it must respond to the charger like a real backend: every standard 1.6 message, both directions.

## Decisions (the defaults proposed in ui-plan.md, open questions 6 and 7)

| Question | Decision | Why |
|----------|----------|-----|
| How is it configured? | One `backends` entry with `local: true` (no `url`, optional `id`, default key `broker`). | Reuses the existing list, ids and the transaction id table's backend keys; no second switch to keep consistent. |
| Who may the local backend be? | Always the leader. A local follower, a second local backend, a `url` on it, or another entry marked leader are refused at load time with a reason. | A local *follower* needs the broker to process every message silently so it can take over (see B4). Refusing it is better than a half-working version. |
| Which mode is it? | `mode: broker` with followers. | "Mode" says who answers the charger. The console already shows a local leader and followers. |
| Does the broker fail over? | No (B4 deferred). The local leader cannot be unreachable, so nothing triggers it; `leader_failover_timeout` and the outbox settings do not apply. | Failover needs the local-follower machinery above and a hand-back story; neither was asked for. |

## How it works

The `ocpp` library's `ChargePoint` reads frames from, and writes frames to, an adapter. In a local-leader session the adapter gets two filters, so the library is just another backend of the transaction id table:

- **incoming** (`ChargerSession._local_incoming`): the charger's frame goes through `TransactionIdTable.from_charger` with the local key as the leader. That copies the frame to each follower in the follower's own ids, answers a retried start from the stored result without bothering the library, and returns the frame in the leader's (the broker's) ids for the library.
- **outgoing** (`_local_outgoing`): each frame the library sends goes through `TransactionIdTable.from_leader`, which turns the broker's ids into the charger's (they are the same unless the broker issues an id the charger already holds) and handles reservation and profile ids of broker-initiated commands exactly as for any leader.
- Followers are connected by `_ensure_backend_connection` as before; only the leader socket is skipped (`links["leader"] = None`).
- Without a table (`transaction_ids.mapping: false`) the charger's CALLs are copied to followers untouched.

Nothing about the table, the follower hold queues, the retry handling or the persistence needed to change: the leader's answer simply arrives from the library instead of a socket.

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

## Still open

- **B4, a local standby:** the broker taking over when an external leader fails, and handing back. Design sketch: a local follower runs the `ocpp` library in a *silent* mode (replies not sent) to keep its own state, becomes audible on promotion, and the id table treats it as another follower. This is the part that needs real care; do it only if wanted.
- **OCPP 2.0.1 / 2.1** local leader: a second `ChargePoint` per version ([roadmap.md](roadmap.md)); the adapter filters and the table's per-backend idea carry over, the id problem mostly disappears because the charger chooses transaction ids in 2.x.
