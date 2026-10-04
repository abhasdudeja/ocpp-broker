# Leader-Follower Logic

How the broker behaves when an organization has more than one backend. This applies to **relay mode** (`connect_to_backend: true`, which is the default), and to broker mode with **a local leader**: the broker itself as the leader and other backends as observers ([below](#the-broker-as-the-leader-local-backend)). In plain broker mode there are no backends.

## Overview

For every connected charger the broker opens its own set of WebSocket connections, one to each configured backend, at `{backend.url}/{charger_id}`. One backend is the **leader**; the rest are **followers**.

| | Leader | Follower |
|---|---|---|
| Receives charger frames | Every frame (CALLs, CALLRESULTs, CALLERRORs) | A copy of each charger-initiated CALL only |
| Can talk to the charger | Yes: every frame it sends is forwarded to the charger | No: anything it sends is ignored (debug log) |
| Delivery when unreachable | Store-and-forward outbox | Nothing is buffered; the copy is dropped |

Followers are **observe-only**. The charger only ever has one conversation, with the leader. Followers are useful as a passive tap (audit, analytics) or as a standby that can take over (see [Leader failover](#leader-failover)).

```
                        +--> leader backend   (all frames, both directions)
charger <--> broker ----+
                        +--> follower backend (copy of charger CALLs only; replies discarded)
                        +--> follower backend (same)
```

## Configuration

```yaml
organizations:
  - name: "orgA"
    connect_to_backend: true          # the default; shown for clarity
    ocpp_subprotocol: "ocpp1.6"
    backend_buffer_size: 200          # frames held while the leader is unreachable
    backend_outage_timeout: 30        # seconds a held frame may wait
    leader_failover_timeout: 15       # seconds the leader may be down before a follower takes over (0 = never)
    leader_failback: false            # true: give the charger back to the configured leader after a failover
    leader_failback_delay: 60         # seconds the configured leader must stay connected before that
    backends:
      - id: primary
        url: ws://primary.example.com/ocpp
        leader: true
      - id: standby
        url: ws://standby.example.com/ocpp
      - id: audit
        url: ws://audit.example.com/ocpp
        ocpp_subprotocol: "ocpp1.6"   # optional; defaults to the organization's
```

Keys the code reads for this feature:

| Key | Default | Meaning |
|---|---|---|
| `backends[].local` | `false` | `true` marks this broker itself as a backend: it answers the charger and every other backend is an observe-only follower. See [The broker as the leader](#the-broker-as-the-leader-local-backend). A local backend has no `url`. |
| `backends[].url` | required (not for `local`) | WebSocket base URL. The charger id is appended: `ws://primary.example.com/ocpp/CP001`. A trailing `/` is stripped. |
| `backends[].leader` | none | Marks the leader. |
| `backends[].ocpp_subprotocol` | the org's `ocpp_subprotocol` (default `ocpp1.6`) | Subprotocol requested from that backend. |
| `backends[].id` | the URL | Names the backend in the transaction id table and in log lines. Keep it stable and unique; if two backends share a name the second becomes `name#2`. |
| `backend_buffer_size` | `200` | Maximum frames held for the leader while it is unreachable. |
| `backend_outage_timeout` | `30` | Seconds a held frame may wait before it is given up on. |
| `leader_failover_timeout` | `15` | Seconds the leader may stay unreachable before a follower is promoted. `0` disables failover. |
| `leader_failback` | `false` | After a failover, hand the charger back to the configured leader once it is back ([Fail-back](#fail-back)). |
| `leader_failback_delay` | `60` | Seconds the configured leader must stay connected, without a break, before the charger is handed back. Above 0. |
| `transaction_ids.mapping` | on when there is more than one backend | Translate transaction ids per backend; see [Transaction ids](#transaction-ids). `true` or `false` forces it. |
| `transaction_ids.follower_wait` | `5` | Seconds the broker holds copies for a follower that has not yet said which id it issued. |
| `transaction_ids.dedupe_start` | `true` | Answer a retried `StartTransaction` from the stored result instead of starting a second transaction. |
| `transaction_ids.retain_closed` | `86400` | Seconds a finished transaction stays in the table, so a retried `StopTransaction` still maps. |
| `transaction_ids.retain_open` | `2592000` | Seconds an unfinished transaction is kept without any activity. |

The per-charger lists `backends[].chargers`, `chargers:` and similar `id` lists are **not read**. Every charger that connects to the organization gets the same set of backends.

### Choosing the leader

When the configuration is loaded:

- If exactly one backend has `leader: true`, it is the leader.
- If none is flagged, the first backend in the list is marked leader.
- If several are flagged, a warning is logged and only the first flagged one stays leader.

Every other backend is a follower. A relay-mode organization with an empty `backends` list cannot start sessions; the charger's session fails with "has no backend definition".

## What happens to each frame

**Charger to backends.** Each text frame from the charger is forwarded to the leader without validation, unchanged except for transaction ids when the organization has followers (see [Transaction ids](#transaction-ids)). If the frame is a CALL (message type 2), a copy is also sent to every follower. CALLRESULTs and CALLERRORs are not copied: they answer the leader's own calls, and followers did not make any.

**Leader to charger.** Every frame from the leader is forwarded to the charger. If MongoDB is connected, CALLs from the leader are also recorded.

**Follower to charger.** Ignored. A follower's replies to the copied CALLs, and any command it tries to send, never reach the charger.

**Copies to followers are best-effort.** They are sent in the background, never block the leader's path, are not buffered when the follower is down (the copy is dropped), and a failure to deliver one never affects the charger or the leader.

**Commands from the REST API** (`POST /api/ocpp/organizations/{org}/chargers/{id}/commands`) go straight to the charger regardless of leader state. In relay mode the CALL is sent exactly as given (no payload validation, any action name accepted), and the charger's reply is matched by message id and handed to the REST caller. It is **not** forwarded to any backend.

## Transaction ids

*(This section also covers reservation and charging profile ids, [below](#reservations-and-charging-profiles).)*

In OCPP 1.6 the **backend** chooses a transaction's id (in its `StartTransaction` reply) and the charger then quotes it in `MeterValues` and `StopTransaction`. With several backends there are several ids for one charging session, and a follower that is sent the leader's id would attach the reading to a different transaction of its own, or find none.

The broker therefore keeps a **transaction id table** for each charger and speaks to every backend in its own ids. It is on by default when an organization has more than one backend and has no effect with a single backend. The table lives in the broker, not in the socket, so it survives the charger reconnecting in the middle of a charge.

- **What the charger sees.** The leader's own id, whenever that id is not already in use for another of the charger's transactions. Leader traffic is then byte-identical to a setup without the table. If the leader issues an id the charger already holds (this can happen after a failover), the charger is given a fresh, unused id instead.
- **Learning ids.** A follower's answer to its copy of a `StartTransaction` tells the broker which id the follower issued. Answers from followers are still never forwarded to the charger.
- **Copies to followers.** In `MeterValues` and `StopTransaction` the id is replaced by the one that follower issued. A follower for which the broker has no id (it was down for the start, or never answered) is **skipped for that transaction**: it is never sent an id that might belong to another of its transactions. A warning is logged when this happens.
- **A slow follower.** Copies for a follower are queued, in order, until it has said which id it issued, for at most `transaction_ids.follower_wait` seconds. The leader is never delayed. After the wait the queued copies for that transaction are dropped and the follower is skipped for it.
- **Retried starts.** If the charger sends the same `StartTransaction` again (same connector, tag, meter reading and timestamp), the broker answers it with the id it already issued and does not send a second start to the leader or the followers. This covers a retry after the broker answered with `Backend unavailable, please retry`. A retry that arrives while the first attempt is still being answered waits and is answered together with it (and gets the same error if the attempt fails). `transaction_ids.dedupe_start: false` turns this off.
- **After a failover.** The promoted follower already numbered each transaction it saw as an observer, so the table knows its ids: the charger's `StopTransaction` for a running transaction reaches it under the new leader's own number, and its commands (`RemoteStopTransaction`, and a charging profile that names a transaction) reach the charger under the id the charger holds. A start the old leader never answered, and that the new leader already holds from its observer copy, is answered from the new leader's own result instead of being started a second time. A new leader that numbers a fresh transaction with an id the charger already holds for a different one is given a fresh id on the charger's side (a warning is logged), so the charger never holds one id for two live transactions.
- **Late answers.** A promoted follower's slow answer to a copy it received before it was promoted is read by the table and not forwarded to the charger: the charger never sent that message to that backend. (Without the table, such answers reached the charger as results for message ids it was no longer waiting on.)
- **Other messages.** Only `StartTransaction` replies, `MeterValues` and `StopTransaction` (charger side) and `RemoteStopTransaction`, `SetChargingProfile` and `RemoteStartTransaction` (backend side, in the nested charging profile) quote a transaction id. Reservation and charging profile ids are handled the same way, [below](#reservations-and-charging-profiles). Everything else is forwarded as before.

Known limits:

- **A backend the table has no id for.** If the leader never learned which id a promoted follower issued for a running transaction (it was down for the start, or its answer never arrived), the charger's `MeterValues` and `StopTransaction` for it are sent to that leader with the charger's own id, unchanged, and a warning is logged. If that number happens to belong to another transaction on that backend, the reading lands on the wrong one. The console and the API do not show this yet.
- **A start whose answer was lost.** The broker only knows a backend has a start once it has seen the backend's answer. If a backend processed a start and the answer never arrived (the link dropped), the charger's retry can reach it again and it may start a second transaction. Once the broker holds the answer it never sends that start to that backend again. A first attempt that has not been answered after 60 seconds is treated as lost, so a retry after that is sent on as well.
- **Not translated:** the local authorization list version and configuration values are also chosen by a backend and stored by the charger, and a promoted leader does not know what the old leader set. `DataTransfer` payloads are vendor-defined and are never inspected.

### Reservations and charging profiles

Reservation ids (`ReserveNow`, `CancelReservation`, and the `reservationId` the charger quotes in `StartTransaction`) and charging profile ids (`SetChargingProfile`, `RemoteStartTransaction` with a profile, `ClearChargingProfile`) are also chosen by the backend and stored by the charger. The charger treats a repeated id as "replace", so after a failover a new leader that numbers its own reservation or profile `1` would silently overwrite the one the old leader set. The same table prevents that, with the same rule as for transactions:

- The charger holds the backend's own number whenever no other reservation (or profile) has it, and a fresh one when another does (a warning is logged). The same backend sending its number again still replaces its own, which is what the protocol means.
- `CancelReservation` and `ClearChargingProfile` by id are rewritten to the number the charger holds. Clearing by criteria (no id) is passed on as it is.
- The charger's answer decides whether the change took effect: a refused new reservation or profile is forgotten, an accepted cancel or clear frees the id.
- In `StartTransaction`, each backend is given its own reservation id. A backend that did not make that reservation does not get the field at all, so it never receives a number that might mean one of its own reservations. A reservation the table never heard of may predate the broker or have been made elsewhere, so the leader is still given it unchanged (followers are not).
- A reservation or profile made through the broker's own REST API (`/api/ocpp/.../commands`) belongs to no backend, but its id is taken on the charger and is recorded as such.
- Reservations are forgotten `transaction_ids.retain_closed` seconds after their `expiryDate`; profiles after `transaction_ids.retain_open` seconds without use. With MongoDB both are stored with the transactions.

A command from a backend that quotes an id the table does not know (for example a `CancelReservation` for a reservation made before the broker started) is passed on unchanged.

**What this does not prevent for profiles:** OCPP 1.6 also lets a charging profile replace an existing one that has the same stack level and purpose, whatever its id (verify the exact rule in the specification for your chargers). A new leader that sets a profile with the same stack level and purpose as the old leader's will still displace it. Translating ids cannot change that, and the broker does not rewrite stack levels because that would change what the profile means.

**Across restarts.** With MongoDB enabled the table is stored in the `transaction_id_map` collection ([details](mongodb-integration.md#the-transaction-id-table-transaction_id_map)) and loaded when a charger connects, so a broker restart, or a second broker instance taking over a charger, keeps every mapping. Backends are matched to stored records by `backends[].id` (the URL if there is none), so keep ids stable. Without MongoDB the table is held in memory only: after a restart it is empty, the leader keeps working (its ids are the charger's ids), but followers lose track of transactions that were already running, and the broker logs a warning at startup.

A transaction that finished is kept for `transaction_ids.retain_closed` seconds so a retried `StopTransaction` still maps. One that was never stopped is forgotten after `transaction_ids.retain_open` seconds without any frame for it. Once a charger has disconnected and nothing is left to remember, its table is dropped from memory (the stored copy stays until it expires).

## The broker as the leader (local backend)

Mark one backend `local: true` and **this broker is that backend**: it answers the charger itself, with all the behaviour of [broker mode](broker_as_backend.md) (its own tags, transaction ids, validation, and every standard OCPP 1.6 message in both directions), while the other backends receive copies of the charger's requests exactly as followers do in relay mode. It is the way to give a charger a complete central system of its own and still feed an external system (audit, analytics, a vendor platform) with everything the charger does.

```yaml
organizations:
  - name: "Depot"
    connect_to_backend: true          # the default
    tags:
      - id_tag: "ADMIN001"
        status: "Accepted"
    backends:
      - id: broker                    # optional; "broker" is used if missing
        local: true                   # this broker answers the charger
      - id: analytics
        url: ws://analytics.example.com/ocpp
```

```text
                          +--> follower backend (copy of charger CALLs; replies discarded)
charger <--> broker ------+
 (answered by the broker) +--> follower backend (same)
```

What differs from relay mode:

- The leader is never unreachable and has no outbox, and there is **no failover**: the broker does not hand the charger to a follower. `leader_failover_timeout`, `backend_buffer_size` and `backend_outage_timeout` have no effect.
- The local backend leads unless another backend is marked `leader: true`, in which case it is a standby ([below](#the-broker-as-a-standby-local-follower)). A second local backend, a `url` on a local backend, two entries marked leader, or a local backend marked `leader: false` with nothing else to lead is refused when the file is loaded, with a message saying why. A local backend alone behaves like broker mode.
- The console and the API show the organization as `mode: broker` with a local leader and its followers; each charger lists `broker` (marked *this broker*) first, then the followers.
- Transaction ids are translated per backend as in relay mode ([Transaction ids](#transaction-ids)): the charger holds the id the broker issued, and each follower is spoken to in the id it issued itself. `transaction_ids.mapping: false` sends the followers the charger's frames as they are instead. A retried `StartTransaction` is answered from the table and a follower is not given a second transaction.
- **Commands** (`POST /api/ocpp/...`) work as in broker mode: the broker validates the payload (`422` if invalid), sends it and returns the charger's answer. They are not copied to followers, and neither are the charger's answers to them. A reservation or charging profile the broker sets is numbered like any other leader's ([Reservations and charging profiles](#reservations-and-charging-profiles)).
- Followers receive copies only while their link is up and are never sent what came before they connected: connect the charger after the followers are reachable, or accept that a follower may miss the first messages (the same as in relay mode).
- A follower that sends the charger a command is ignored, as in relay mode.

### The broker as a standby (local follower)

Mark an external backend `leader: true` and add a `local: true` backend that is not the leader: the external backend answers the charger, and **this broker follows silently** and takes over if the external leader fails.

```yaml
backends:
  - id: primary
    url: ws://primary.example.com/ocpp
    leader: true
  - id: broker                 # the standby
    local: true
  - id: analytics
    url: ws://analytics.example.com/ocpp
```

- **While the external leader is healthy** the standby gets a copy of every charger request, as any follower does, and processes it with the same code as broker mode: it numbers the transactions it sees (in its own ids, which the [id table](#transaction-ids) knows), records them, and stores what it would store. Its answers are read by the id table and thrown away. **The charger never hears from it**: with `primary` saying `Accepted` and the broker's own tags saying `Invalid`, the charger is told `Accepted`.
- **When the external leader stays down for `leader_failover_timeout`** the first healthy follower in the configured order is promoted, as in relay mode. List the local backend before other followers if it should be preferred. From then on the broker answers the charger with its own rules, already knowing the transactions that were running: a running transaction is stopped under the id the charger holds, translated to the broker's own, without a warning about an unknown transaction; a start the old leader never answered is answered once, with the standby's own answer. The old leader becomes an ordinary follower (and receives copies again when it reconnects).
- **Commands** follow whoever answers: while the external leader leads they are sent to the charger as they are (unchecked, as in relay mode); after the standby is promoted the broker validates them first (`422` if invalid) and sends them itself.
- **Fail-back is off by default**: when the external leader returns, the broker keeps answering and the external backend follows. With `leader_failback: true` the charger is handed back after `leader_failback_delay` ([Fail-back](#fail-back)): the broker goes back to being a silent standby and the organization is a relay for that charger again. An operator can also change the leader by hand.
- **The console and the API** show the standby as a follower marked *this broker* (connected while its library runs), and after a promotion as the leader. The organization is `relay` mode as long as the *configured* leader is external.
- If the standby's library fails, the standby is shown as not connected and the charger and the external leader are not affected. If the **local leader's** library fails, the broker closes the charger's connection (code 1011) so the charger reconnects to a fresh session.
- **Cost:** every charger message is handled twice (by the leader and by the standby), and the standby writes to MongoDB what a leader would, so the database holds the standby's view as well.

## Leader outage: store-and-forward

Session start does not wait for backends. If the leader is not connected, frames from the charger go into a bounded outbox on the leader's connection and are flushed **in order** when the link returns.

- The outbox holds at most `backend_buffer_size` frames. When it is full, the **new** frame is refused.
- A frame waiting longer than `backend_outage_timeout` is given up on.
- A refused or expired **CALL** is answered to the charger with `[4, "<id>", "InternalError", "Backend unavailable, please retry", {}]` so the charger is not left waiting and can retry.
- A refused or expired **CALLRESULT** (the charger's answer to a backend command) is dropped with a warning.
- Frames are also buffered if a write to a live backend socket fails; the broker then drops that socket so it reconnects.

Followers have no outbox.

## Leader failover

If the leader link is lost, the broker starts a watcher (only when the organization has at least one follower and `leader_failover_timeout` is not `0`):

1. It waits `leader_failover_timeout` seconds. If the leader is back, nothing happens.
2. Otherwise the **first healthy follower** (config order, currently connected) is promoted to leader.
3. If no follower is healthy, a warning is logged and the watcher waits another `leader_failover_timeout` before checking again, until the leader returns or a follower becomes ready.

On promotion:

- Frames held for the old leader are **not replayed**. Each held CALL is answered to the charger with the `InternalError` CallError above (held CALLRESULTs are dropped). The charger's retry goes to the new leader. The new leader has already seen an observed copy of those CALLs, so it may see the same CALL twice; for a `StartTransaction` the broker prevents that (see [Transaction ids](#transaction-ids)).
- The old leader becomes a follower (unbuffered, observe-only).
- The new leader gets the store-and-forward outbox settings.
- Unless `leader_failback` is on, there is **no automatic fail-back**: when the old leader returns it stays a follower. Only a failure of the current leader triggers another promotion.

### Fail-back

With `leader_failback: true`, after a failover the charger goes back to the **configured leader** (the one marked `leader: true`, or the local backend that leads) once it has been connected for `leader_failback_delay` seconds **without a break**: a leader that keeps dropping is not handed the charger just to lose it again, and its timer starts over each time it drops. The same machinery as a failover does it (`reason: failback` in the `backend.failover` event): the current leader becomes a follower, the frames held for it are answered with a CALLERROR, and the [transaction id table](#transaction-ids) follows the change. If the broker itself was the standby that took over, it hands the charger back and returns to being a silent standby.

- It only follows a **failover**. A leader an operator chose by hand stays until an operator says otherwise.
- A message the current leader has received but not yet answered when the charger is handed back is never answered by it (its late answer is discarded), so the charger may time out and send it again, to the new leader.
- The setting is per organization; each charger session decides for itself.

### Changing the leader by hand

`POST /api/chargers/{org}/{charger_id}/leader` with `{"backend": "<key>"}` (or **Make … the leader** on the charger's page in the console) makes a **connected follower** the leader now. It answers `{"old_leader", "new_leader"}`, or `409` with the reason if that backend already leads, is not a backend of this charger or is not connected, and `404` if the charger is not connected to this instance with backends. The change is not written to the configuration: the charger goes back to the configured leader when it reconnects. The same remark about unanswered messages applies.

`leader_failover_timeout` and `backend_outage_timeout` interact: held CALLs are answered with a CallError after `backend_outage_timeout` (default 30s) even if failover has not happened yet (default 15s). With the defaults, failover occurs first and held frames are rejected at promotion. Failover is per charger session: each charger decides independently, and it applies to that session only.

### Reconnecting

Each backend connection reconnects on its own: it waits 1s after a failure, doubling on each consecutive failure up to 30s, and resets to 1s after a successful connection. Backend sockets send a WebSocket ping every 20s and drop the connection if no pong arrives within 20s. If the subprotocol the backend negotiates differs from the one requested, an error is logged but the connection continues.

When a charger disconnects, all of its backend connections are closed and their outboxes discarded.

## Inspecting the backend links

```bash
curl -H "X-API-Key: $OCPP_BROKER_API_KEY" http://localhost:8765/orgs/orgA/backends
```

```json
[
  {"charger_id": "CP001", "url": "ws://primary.example.com/ocpp", "leader": true,  "connected": true},
  {"charger_id": "CP001", "url": "ws://standby.example.com/ocpp", "leader": false, "connected": true}
]
```

One entry per backend per **currently connected relay-mode charger**; `leader` reflects the current role (it changes after a failover). The route returns `404` when the broker has no backend links recorded for that organization, which includes a configured organization for which no relay-mode charger has connected since the broker started. It is read-only.

Failover and outage events are in the log (`FAILOVER: leader ... unreachable, promoting follower ...`, `Backend unavailable ... buffered frame`, `answering ... with CallError`). The log level is fixed at INFO.

## What does not exist

There is no health-check-driven election beyond the connection state described above, no weights or priorities, no comparison or voting between backend responses, and no metrics endpoint. Backends are added and removed with the [admin API](admin.md) (or by editing `config.yaml` and restarting); a charger that is already connected keeps the backends it connected with until it reconnects.

## Related Documentation

- [Configuration Guide](configuration.md)
- [Broker-as-Backend Mode](broker_as_backend.md)
- [API Reference](api-reference.md)
- [Architecture](architecture.md)
- [Troubleshooting](troubleshooting.md)
