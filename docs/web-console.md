# Web Console

The broker serves a web console at `/ui` on its own port, next to the charger WebSocket and the REST API. It is an early version: it shows what is connected, updates as things happen, can send commands and manages tags. The pages that exist are listed below; more (history, administration) are planned, see `plans/ui-plan.md` in the repository.

## Opening it

```
http://localhost:8765/ui/
```

Sign in with the broker's API key (`security.api_key` or `OCPP_BROKER_API_KEY`, see [Configuration](configuration.md#security-section)). There are no user accounts: everyone who has the key can do everything the API allows, and the sign-in page says so.

What the sign-in does:

- It checks the key with `GET /api/system/info` ([API Reference](api-reference.md#system-information)). A wrong key says so; a broker with no key configured says that instead (the API answers `503` until one is set); an unreachable broker says that.
- The key is kept in the browser tab's `sessionStorage` only, so it is gone when the tab closes. It is sent in the `X-API-Key` header and never in a URL. If the broker later stops accepting it (the key was changed), the console signs out and says why.

## The overview

Figures from `GET /api/system/info`, refreshed every 10 seconds: broker version, instance id, uptime, organizations, chargers connected to **this instance** (sessions are per process), whether the API needs a key, whether MongoDB answers right now, and whether the console is built into this installation. It warns when the API accepts requests without a key, when MongoDB is configured but not answering, when MongoDB is not accepting writes (with how many records are waiting to be stored, see [Writes happen after the reply](mongodb-integration.md#writes-happen-after-the-reply)), and when records were dropped because too many were waiting. The MongoDB figure also says how many records are waiting to be written. If a refresh fails it keeps the last figures on screen and says so.

The overview also lists **recent events** (see [Live updates](#live-updates)).

Below the figures, a table of the **organizations** from `GET /api/orgs`: mode (broker or relay) and OCPP version, how many chargers are connected (a link to the chargers page filtered to that organization), the backends with the configured leader marked, whether transaction ids are translated for them, and whether chargers must authenticate.

## Chargers

The chargers connected to this broker instance (`GET /api/chargers`, refreshed every 5 seconds). One row per charger: its id (a link to the charger's page), vendor and model once it has booted, organization, mode, the **leader** it currently talks to with a dot for the link state (in broker mode the leader is "this broker"), how many **followers** are connected, the status of each connector, how many transactions are running, and how long ago it last sent a message.

Things that need attention are marked: a leader that is down, charger messages **waiting** for it, followers that are not all connected, a **Faulted** connector, and running transactions in which a backend is being **skipped** (the broker never learned that backend's transaction id).

Search by charger id and filter by organization; both are kept in the address, so a filtered list can be bookmarked or shared.

Below the table, **Not connected now** lists chargers this broker has seen before that are not connected to this instance (last seen, last boot, vendor and model), most recent first. It needs MongoDB, which remembers each charger when it connects, boots and disconnects (one small document per charger, never per message); without MongoDB the page says so. A charger that is connected to a *different* broker instance also appears here, because sessions are per process.

## A charger

`/ui/chargers/{org}/{charger id}` (`GET /api/chargers/{org}/{charger_id}`, refreshed every 3 seconds):

- **Connections:** a diagram of charger, broker and the backends for this charger, the leader first. Each backend shows its role, address, whether the link is up and for how long it has been down, and how many charger messages are waiting for it. After a failover the new leader is the first box. A text description is there for screen readers.
- **Connection** and **Identity:** the address the broker sees, when it connected, last message and last heartbeat, message counts, and what the charger said in its `BootNotification` (vendor, model, serial number, firmware, ICCID, IMSI, meter).
- **Connectors:** each connector's status, error code and text, and how long ago it changed.
- **Transactions:** one row per transaction with the id the **charger holds**, its state, and **each backend's own id** in its own column, so a backend's dashboard can be matched with the charger's log. A backend marked *skipped* never learned its id and is not sent that transaction's messages; *waiting for its id* means a follower has not yet answered. When transaction ids are not translated for this charger the page says so.
- **Commands:** send a command to the charger and see what was sent before, see [Sending a command](#sending-a-command).
- **Reservations** and **charging profiles** the charger holds, with each backend's own number, and a collapsed list of what the transaction id table has done (ids rewritten, remapped, skipped).
- **Recent events for this charger:** what has happened to it since the page was opened (and the last few events the broker remembers).

If the charger is not connected to this instance the page says so and keeps checking; if it disconnects while open, the page keeps what it last showed and says that it is out of date. If a page fails to render something it was sent, the console shows a message and the navigation still works.

## Backends

`/ui/backends` (`GET /api/backends`, refreshed every 5 seconds and when a backend link or a charger changes): for each organization that has backends, one row per backend summed over the chargers connected to this instance: whether it is the configured leader, for how many chargers it leads and follows **now** (a failover moves chargers from one to another; the configuration does not change), how many links are up and down, the charger messages waiting for it, and which chargers lost it (linked, at most 20 named). A local backend is marked *this broker*. Below the table, the recent `backend.link` and `backend.failover` events.

## History

`/ui/history` (`GET /api/history/...`, see [MongoDB Integration](mongodb-integration.md#history)): what the broker recorded, newest first, in four tabs. Everything needs MongoDB; without it, or while it does not answer, the page says why instead of showing an empty table.

- **Transactions:** the charger, connector and id tag, when it started, how long it ran, the energy (meter at the end minus meter at the start) and how it ended, or *running*. Each transaction opens its own page. Only transactions the broker answered itself are here (broker mode, a local leader); in relay mode the backend has them.
- **Status changes:** each connector status change with its error code, with the charger's own time.
- **Commands:** what was sent through the API or this console, the outcome (`success`, `error`, `timeout`, `cancelled`), how long it took, and what was sent and answered (secrets are `***`).
- **Messages:** every OCPP frame, in both directions, replies labelled with the action they answer. Empty unless `mongodb.history.messages` is on; the tab says so and how to turn it on.

Filter by organization, charger id and time range (last hour, 24 hours, 7 or 30 days, or all time), and on each tab by what fits it (transaction state and id tag, status, command and outcome, action and direction). The filters are kept in the address. A time range counts back from the moment it was chosen and stays fixed while you read; **Refresh** starts again from now. Each tab loads 50 rows and **Load older** adds the next 50.

A transaction's page shows its facts and a chart of its meter readings, one measurement at a time (the energy register first), with the readings as a table under it. A running transaction refreshes every 15 seconds.

A charger's page has a **Recent status changes** list from the history (left out when there is none), with a link to the full list.

## Tags

`/ui/tags`: the id tags the broker authorizes when it answers a charger itself. Pick an organization (only those where the broker answers or may take over: broker mode, a local leader, or a local standby); the list is the organization's tag list ([Tag Management](tag-management.md)).

- **Search and filters** by id tag, status and type, and paging (25 per page); all are kept in the address.
- **Counts:** tags, active, expired and blocked.
- **Add and change** a tag. The form asks the broker to check the tag first (`/tags/validate`) and shows its errors and warnings, such as "already exists" or an expiry date in the past; nothing is saved until the check passes. Editing keeps the id and any metadata the form does not show.
- **Delete** asks for confirmation. **Select** tags on the page to set them all to Accepted or Blocked, or delete them; items that fail are named.
- **Import** JSON or CSV, pasted or from a file. **Check** reports what would be added, updated, left alone and rejected (with the reason for each rejected record) without changing anything; **Import** then applies it, and only for the text and options that were checked.
- **Export** JSON or CSV, which can be imported again.
- **Sync with MongoDB** reloads the organization's tags from MongoDB and reports how many were loaded, pushed to MongoDB and dropped; it says when MongoDB is not connected.

Changing tags here is as powerful as the API key: there are no separate permissions.

## Live updates

The top bar shows whether the console is receiving events as they happen:

| Shown | Meaning |
|-------|---------|
| **Live** | The event stream ([`GET /api/events`](api-reference.md#live-events)) is open. A charger connecting, a connector changing status, a backend link going down or a failover appears within about a third of a second. |
| **Connecting…** | The first attempt is under way. |
| **Reconnecting…** | The stream dropped. The console retries (after 1 s, then 2, 4, 8, up to 30 s) and asks for what it missed. The pages keep refreshing by themselves meanwhile. |
| **Polling** | This broker has no event stream (an older version), so the pages only refresh on their timers. |

Events do not replace the periodic refresh, they make it immediate: when an event arrives that concerns what a page shows, the page reads its data again at once (a burst of events causes one read). The overview, the chargers list and each charger's page do this; the periodic refresh stays as a safety net, and counters such as "last message" still move on it. After a broker restart, or if the console was away for longer than the broker remembers (the last 1000 events), the pages reload everything.

**Recent events** on the overview (everything, naming each charger with a link) and on a charger's page (only that charger) list, newest first, what the stream reported: connections and disconnections, boots, connector status changes, transactions asked for or ended, backend links lost or restored, failovers and command results. The list holds what arrived since the page was opened plus the latest events the broker replays on connecting; it is not a history. History is a later phase, and needs the database.

The stream says what happened, never what was said: it carries no OCPP payloads, no id tags and no command payloads.

## Sending a command

A charger's page has a **Commands** section: pick a command, fill in its fields and send it. The list is the broker's command catalog ([`GET /api/ocpp/commands/catalog`](api-reference.md#command-catalog)): the 19 commands a central system can send under OCPP 1.6, in three groups by what they can do, and each form is generated from the JSON Schema of that command's payload, the same schema the broker validates against in broker mode.

- **Form:** required fields are marked `*`; mistakes are named beside the field (an id that is not a whole number, a date that is not like `2026-10-04T12:00:00Z`, a value that is not one of the listed choices, text longer than allowed) and nothing is sent until they are fixed. Dates have a **Now** button. Optional parts (a nested object such as `idTagInfo`, or a list) are added and removed with buttons. The form does not check everything the schema can say (`multipleOf`, for one); the broker and the charger do.
- **JSON:** the payload as text, for anything the form cannot express. It is sent exactly as written; it is only checked for being a JSON object, not against the schema (in relay mode the broker does not validate commands either). Switching between Form and JSON keeps the values; JSON that is not valid cannot be shown as a form.
- **Confirmation:** commands that can interrupt the charger (`Reset`, `UpdateFirmware`, `RemoteStopTransaction`, `RemoteStartTransaction`, `ChangeAvailability`, `UnlockConnector`) ask first. Read-only and settings commands are sent at once.
- **Waiting:** the console waits for the charger's answer up to the chosen number of seconds (1 to 300, default 30). The result is shown below the form: the charger's answer, its refusal with its reason, or "no answer within N s". Errors from the broker are explained too: the charger is no longer connected, the payload was rejected (nothing was sent), or the broker could not be reached (the command may or may not have been sent, so check the history).
- **Sent to this charger:** the last 50 commands sent through the API **or** the console while the charger stayed connected, newest first, each with its outcome and how long it took. **Details** shows what was sent and answered; **Use again** puts it back in the form as JSON. The history is kept in the broker's memory and starts again when the charger reconnects. The value of an `AuthorizationKey` configuration change (and the same key in a `GetConfiguration` answer) is hidden (`***`) in the history, and secrets are never put in an event. The result card shown right after sending is the charger's answer as it came, unredacted, like the API's own reply.

Sending a command is as powerful as the API key: there are no separate permissions yet.

## Security

The console is static files that need no key to download. They are served with a strict `Content-Security-Policy` (no inline script or style, nothing loaded from other origins, `connect-src 'self'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`, because the key lives in the page's JavaScript and the main risk is something running in the page that should not. Pages are never cached (`Cache-Control: no-store`); the hashed script and style files are cached for a year.

The console shares the charger port, so keep `/ui` off the public internet exactly as you do `/api` ([Deployment](deployment.md)). To turn it off, set `ui.enabled: false` ([Configuration](configuration.md#web-console)); every `/ui` path then answers `404`.

## From a release package or from source

A release package of `ocpp-broker` includes the built console. A source checkout does not (the build is not committed), and `/ui/` answers `503` with a short explanation until you build it:

```bash
cd ui
npm ci
npm run build        # writes src/ocpp_broker/ui_dist
```

Node 20.19 or later (or 22.12+) is needed for the build only; running the broker needs no Node.

## Developing the console

```bash
# terminal 1: a broker with an API key
OCPP_BROKER_API_KEY=dev-key ocpp-broker-server -c config.yaml

# terminal 2: the console with hot reload, proxying /api to the broker on :8765
cd ui
npm run dev          # http://localhost:5173/ui/
```

Checks (all run in CI): `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`.

The console's types come from the broker's OpenAPI schema. After changing the API, run `python scripts/export_openapi.py` (writes `ui/openapi.json`) and then `npm run api` in `ui/` (writes `schema.d.ts` in the console's `api` source folder), and commit both. A test (`tests/test_openapi_contract.py`) fails when `ui/openapi.json` no longer matches the API. The same script writes `ui/src/test-fixtures/command-catalog.json`, the real command catalog the console's form tests run against; that file is checked too.

## Related documentation

- [API Reference](api-reference.md)
- [Configuration](configuration.md)
- [Deployment](deployment.md)
