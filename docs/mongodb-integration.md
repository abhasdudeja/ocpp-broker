# MongoDB Integration

MongoDB is optional. When enabled the broker stores charger data and uses MongoDB for durable transaction ids and tags. Without it the broker still works, but nothing is persisted: tags added at runtime are lost on restart and transaction ids come from a non-durable counter.

## Configuration

In `config.yaml`:

```yaml
mongodb:
  enabled: true
  connection_string: "mongodb://localhost:27017"
  database_name: "ocpp_broker"
```

or with environment variables (a `.env` file in the working directory is loaded automatically). **Environment variables win over the file.**

| Variable | Meaning |
|----------|---------|
| `MONGODB_ENABLED` | `true`, `1`, `yes` or `on` enables MongoDB (also enables it when the file says `enabled: false`) |
| `MONGODB_CONNECTION_STRING` | Default `mongodb://localhost:27017` |
| `MONGODB_DATABASE_NAME` | Default `ocpp_broker` |

Connection strings are standard MongoDB URIs: `mongodb://user:password@host:27017`, a replica set (`mongodb://h1,h2,h3/?replicaSet=rs0`) or Atlas (`mongodb+srv://...`).

### Startup behaviour

The broker connects at startup and pings the server (5 second timeout). If that fails, an error `Failed to initialize MongoDB service` is logged and the broker **continues without MongoDB and tries again** after 2 seconds, doubling the wait up to a minute (later failures are logged at debug level only). When MongoDB answers, persistence is switched on from then (`MongoDB is available now`): the transaction id counter and the background writes use it, and the tags loaded from the configuration are pushed into it (or its own tags are loaded, if it has any). Records the broker could not store while it was away are not kept; start MongoDB first if you can. If MongoDB goes away later, charger traffic carries on and what is waiting to be stored is kept and tried again ([below](#writes-happen-after-the-reply)).

If MongoDB is disabled, the broker logs `MongoDB not configured or disabled: transaction ids will come from a non-durable in-memory counter and nothing will be persisted.`

The broker creates the indexes it needs when it connects: for `transaction_id_map` (see below), `charger_presence`, and the [history](#history) collections (queries by charger and time). Creating them does not wait for or block anything; if it fails a warning is logged and history still works, more slowly. Retention is set with `mongodb.history.retention_days` ([below](#retention)).

## Writes happen after the reply

What a charger says is stored **after the charger has been answered**, by a background writer, never before. A database that is slow (a cloud cluster a few hundred milliseconds away) or not answering therefore does not slow or stall the charger's replies: with the database in line, a reply took 150 to 250 ms against a remote cluster and would have waited for the driver's timeout (5 s, per call) during an outage.

- **In order, side by side.** Records are stored by eight workers; one charger's records always go to the same worker, one after another, so a transaction's start is stored before its stop, while different chargers' records are written at the same time. (Over a link with 100 ms latency one worker would store about ten records a second.)
- **Heartbeats are merged.** A heartbeat time is stored once per charger however many heartbeats arrive while a write is waiting.
- **Bounded.** At most 10,000 records wait per worker. Beyond that the newest are **dropped and counted**; a lost status record is better than a broker out of memory.
- **An outage is waited out.** A write that gets no answer within 5 seconds is kept and tried again; after three failures in a row the writer tries once every 5 seconds instead of hammering the server, and stores the backlog in order when it answers again. A cut-off write may have reached the database, so after an outage a record can be stored **twice**. A record MongoDB keeps *rejecting* is dropped after 3 tries so it cannot block the rest.
- **At shutdown** the broker waits up to 5 seconds for what is waiting, and logs how many records it did not store. A crash loses what was waiting.
- **What is still in line.** The transaction id counter (the charger needs the number in its `StartTransaction` reply) is read from MongoDB with a 3 second limit; after that the broker uses its in-memory counter, as it does when MongoDB is down ([Transaction ids](#transaction-ids)). Tag changes made through the REST API also wait for the database, because the caller is waiting for the outcome.
- **See it.** `GET /api/system/info` reports `mongodb.pending_writes`, `written`, `failed_writes`, `dropped_writes` and `writes_degraded` (true while recent writes keep failing), and the console's overview warns about the last two.

## What is stored, and when

Data is written for chargers served in **broker mode** (and by a local leader), and for commands sent through the REST API or the console. In **relay mode** the backend answers the charger, so its transactions and meter values are the backend's to store; the broker records the connector status changes it sees (`charger_statuses`) and, if switched on, the message log ([History](#history)). Nothing writes to these collections but the broker: there are no REST routes that add records.

| Collection | Written when | Contents |
|------------|--------------|----------|
| `charger_configurations` | `BootNotification` (broker mode) | One document per charger, updated on each boot: model, vendor, firmware, ICCID, IMSI, meter details, `last_boot_time`, `updated_at` |
| `charger_heartbeats_latest` | `Heartbeat` | One document per charger: `last_heartbeat`, `updated_at`. Individual heartbeats are not stored. |
| `charger_statuses` | `StatusNotification` (broker mode); each status change seen in relay mode | Every notification: connector, status, error code, info, vendor fields, `timestamp` (the charger's own time when it sent one), `received_at` |
| `charger_statuses_latest` | `StatusNotification` | One document per connector with its latest status |
| `meter_values` | `MeterValues` | Connector, `transaction_id`, the readings (`meter_value`), `timestamp` |
| `transactions` | `StartTransaction`, `StopTransaction` | One document per transaction (see below) |
| `authorizations` | `Authorize` | Each authorization: `id_tag`, `status`, `expiry_date`, `parent_id_tag`, `timestamp` |
| `data_transfers` | `DataTransfer` | Vendor, message id, data, the status the broker answered |
| `diagnostics_status_notifications`, `firmware_status_notifications` | those two messages | The raw message in `payload` |
| `commands` | a command sent through the API or the console, when it finishes | the command, its (redacted) payload and what came back: see [History](#history) |
| `ocpp_messages` | every OCPP frame, **only if** `mongodb.history.messages` is on | see [History](#history) |
| `tags`, `tag_list_versions` | tag changes | the tags of every organization and a list version per organization |
| `counters` | `StartTransaction` | transaction id counters |
| `transaction_id_map` | relay mode with several backends | the transaction id table: one document per transaction (see below) |

Earlier versions also wrote a `call_results` / `call_errors` collection (one document for every reply, with no action name) and one collection per command action (`resets`, `get_configurations`, ...). They are no longer written, and are replaced by `commands` and `ocpp_messages`. Existing ones are left alone; drop them when you no longer need them.

Raw per-message collections for incoming requests (for example `start_transactions`, `status_notifications`, `boot_notifications`) are **not** written: the structured collections above replace them.

### Document shapes

Every document has `org_name`, `charger_id` and a `timestamp` (UTC) unless noted.

**`transactions`**: a start inserts the document; a stop updates only the stop fields on the same document (`org_name` + `charger_id` + `transaction_id`), and creates a partial document if no start was stored:

```json
{
  "org_name": "orgA", "charger_id": "CP001", "transaction_id": 1,
  "connector_id": 1, "id_tag": "USER123", "meter_start": 1000, "reservation_id": null,
  "transaction_type": "stop", "timestamp": "2026-01-01T10:00:00Z",
  "meter_stop": 1250, "stop_timestamp": "2026-01-01T11:00:00Z",
  "stop_reason": "EVDisconnected", "stop_id_tag": "USER123"
}
```

`transaction_type` is `start` until the stop arrives. `connector_id`, `id_tag` and `meter_start` of a stop-only document are absent.

**`charger_statuses`**

```json
{"org_name": "orgA", "charger_id": "CP001", "connector_id": 1, "status": "Available",
 "error_code": "NoError", "info": null, "vendor_id": null, "vendor_error_code": null,
 "timestamp": "2026-01-01T12:00:00Z"}
```

**`charger_heartbeats_latest`**

```json
{"org_name": "orgA", "charger_id": "CP001", "last_heartbeat": "2026-01-01T12:00:00Z", "updated_at": "2026-01-01T12:00:00Z"}
```

**`transactions`** also have `received_at`, when the broker stored the start. `timestamp` is the charger's own start time, so a start that arrives late (a charger that was offline) is still listed where it happened.

## Transaction ids

`StartTransaction` replies carry an id from a **per-organization counter** stored in `counters` (`_id: "transaction_id:<org>"`, field `seq`). It is incremented atomically, so ids are unique and increasing across restarts and across several broker instances sharing the database; the first id is 1.

If MongoDB is unavailable (disabled, or a call fails) the broker uses an in-memory counter seeded from the clock and logs `!!! TRANSACTION IDS FOR ORG '<org>' ARE NOT DURABLE !!!` once per organization per outage. Those ids are only unique within the process.

### The transaction id table (`transaction_id_map`)

In relay mode with several backends the broker keeps a table that maps the transaction id a charger holds to the id each backend issued ([how it works](leader-follower.md#transaction-ids)). With MongoDB enabled the table is stored here so a broker restart does not lose it; without MongoDB it is memory-only.

- One document per transaction, `_id` a random record id. Fields: `org_name`, `charger_id`, `data` (the record: state `pending`/`open`/`closed`, the charger's `tx_id`, `backend_ids` as a list of `[backend, id]` pairs, the start key, the start result sent to the charger, timestamps in epoch seconds), `updated_at` and `expires_at`. Reservations and charging profiles are stored the same way, with `data.kind` set to `reservation` or `profile` and the charger's id in `data.cid`.
- The records of one charger are loaded when its first session starts after a restart. Ids of backends that are no longer configured are dropped with a warning.
- Writes are queued and sent in the background, so a slow or unreachable MongoDB never delays a charger. Repeated changes to one record are merged; during an outage the writes wait and are retried (1 s, doubling up to 30 s); at most 5000 are kept, and a record MongoDB keeps rejecting is dropped after 5 tries. A crash can lose the last few changes.
- `expires_at` carries a **TTL index** (`expireAfterSeconds: 0`), so MongoDB removes finished transactions `transaction_ids.retain_closed` seconds after they ended and unfinished ones `transaction_ids.retain_open` seconds after their last activity. The index, and one on `org_name` + `charger_id`, are created the first time the collection is used. MongoDB's TTL monitor runs about once a minute, so expired records can linger briefly.

## Tags

Tags (see [Tag Management](tag-management.md)) are kept in memory and mirrored to the `tags` collection:

- Adding, updating or deleting a tag through the API writes it to MongoDB as well as the in-memory list.
- When the broker has no tags in memory for an organization it loads them from MongoDB on first use.
- Tags from `config.yaml` are only in memory until pushed to MongoDB by a sync.
- `POST /api/tags/sync` makes MongoDB authoritative: for each organization that has stored tags the in-memory list is replaced with what is stored (a tag removed in MongoDB disappears). An organization that has tags in memory but none stored is pushed to MongoDB instead. Without MongoDB the call returns `503`.

## History

What the broker recorded is read back with `GET /api/history/...` (see the [API Reference](api-reference.md#history-apihistory)) and in the console's History page. It is **written by the broker only**: earlier versions had `POST /api/mongodb/...` routes that let anyone holding the API key add records; they are gone, so the records can be trusted as a log.

| Kind | Collection | Recorded |
|------|------------|----------|
| Transactions | `transactions` | broker mode and local leader |
| Connector status changes | `charger_statuses` | broker mode; in relay mode each change the broker sees |
| Meter readings | `meter_values` | broker mode and local leader |
| Commands | `commands` | every command sent through the API or the console, with its outcome |
| OCPP messages | `ocpp_messages` | every frame to and from a charger, in every mode, **if switched on** |

**Commands.** One document per command, written when it finishes: `message_id`, `action`, `payload`, `status` (`success`, `error`, `timeout`, `cancelled`), `response`, `error`, `sent_at`, `finished_at`, `duration_ms`. Secrets are replaced by `***` before they are stored, as in the console (an `AuthorizationKey`). A command the broker refuses as invalid (`422`) was never sent and is not recorded.

**Messages.** Off by default, because it is one write per frame. With `mongodb.history.messages: true` every OCPP frame is stored as it passes, in both directions and in every mode: `direction` (`in` from the charger, `out` to it), `type` (`call`, `result`, `error`), `action`, `message_id`, `payload`, `timestamp`. A reply carries only a message id on the wire; the broker remembers each request (200 per charger) to put the action on its reply, and a reply it cannot match (after a restart) has `action: null`. Heartbeats and their replies are left out unless `mongodb.history.heartbeats: true`. A payload over 64 KB is not stored, only its size (`truncated: true`). Payloads are stored as sent and can hold id tags: restrict who can read them (`GET /api/history/messages` needs the API key, like every route) and use a retention.

### Retention

```yaml
mongodb:
  history:
    messages: false
    heartbeats: false
    retention_days:          # a MongoDB TTL index per collection; omit or null: kept for ever
      messages: 30           # default 30
      commands: 365          # default 365
      statuses: null
      meter_values: null
      transactions: null
```

MongoDB deletes records older than the limit in the background (its TTL monitor runs about once a minute). `statuses`, `meter_values` and `transactions` are measured from the record's `timestamp`, `commands` from `sent_at`. An open transaction older than its limit is deleted too. A limit that is changed is applied to the existing index when the broker connects. **A limit that is removed does not remove the TTL index** MongoDB already has: drop it yourself (`db.ocpp_messages.dropIndex("timestamp_1")`). `retention_days` rejects unknown names, zero and non-integers at startup.

### Reading

All lists are newest first, paged with an opaque `cursor` (pass the `next_cursor` of one page to get the next), and filtered by organization, charger and time. Without MongoDB, or while it does not answer, each answers `available: false` with a `reason` instead of an error. `GET /api/history/info` says whether history is available, what is switched on, the retention and roughly how many records each collection holds.

## Failure handling

- A write never delays or fails a charger's request: it is queued and written after the reply ([above](#writes-happen-after-the-reply)). A write that fails is logged (`Error saving ...`, or a warning from the writer, at most one every 30 s) and counted in `GET /api/system/info`.
- A broker started without a reachable MongoDB runs without persistence until restarted.
- `GET /api/mongodb/health` reports `not_configured`, `connected` or `disconnected`.

## Related documentation

- [Configuration Guide](configuration.md)
- [Broker-as-Backend Mode](broker_as_backend.md)
- [Tag Management](tag-management.md)
- [API Reference](api-reference.md)
