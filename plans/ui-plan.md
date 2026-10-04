# Plan: web UI for the OCPP broker

Status (2026-10-04): **Phase 0 is built, and the first slice of Phase 1.** Built: `/ui` serving and security headers, sign-in, overview with organizations, the chargers list and the charger page (topology of leader and followers, connectors, identity, the transaction table with each backend's own id, reservations and profiles), `GET /api/system/info`, `/api/orgs`, `/api/chargers`, `/api/chargers/{org}/{id}`, the per-charger state model, the OpenAPI contract check, and the CI job. **Not yet built from Phase 1:** the live event stream (the pages poll every 3 to 10 seconds instead), the command panel and command catalog, the backends page, offline chargers. Phases 2 to 6 are unchanged. See "Build log" below.

Transaction id mapping between the charger and its several backends (a correctness problem in relay mode, shown in the console's charger detail) has its own plan: [transaction-ids.md](transaction-ids.md). Its stages T1 to T5 are built; T6 (the console's view of the table) is the charger page's Transactions section.

Future OCPP 2.0.1 / 2.1 support is planned; see [roadmap.md](roadmap.md). The "Rules to follow now" there apply to everything in this plan (protocol-neutral state model, `ocpp_version` in responses, no hard-coded "1.6" in the UI).

## Decisions already made

| Topic | Choice |
|-------|--------|
| Scope | Full admin and operator console **with history** |
| Delivery | Bundled with the broker: served at `/ui` on the same port, no CORS |
| Frontend | React + TypeScript + Vite |
| Sign-in | Paste the existing API key (`OCPP_BROKER_API_KEY`); no user accounts in v1 |

## What exists, what does not

The broker already has a REST API on the same port. The UI can use these routes as they are:

| UI need | Existing route |
|---------|----------------|
| Connected chargers of one org | `GET /api/ocpp/organizations/{org}/chargers`, `.../chargers/{id}/status` |
| Send a command, get the real reply | `POST .../chargers/{id}/commands` and 19 typed routes |
| Look up a past command | `GET /api/ocpp/commands/{message_id}/response` (last 1000, in memory) |
| Backend links | `GET /orgs/{org}/backends` |
| Tags | CRUD, search, `list`, `statistics`, `validate`, `bulk`, `import`, `export`, `sync` |
| MongoDB health | `GET /api/mongodb/health` (only reflects startup, never re-pings) |

What a UI needs that **does not exist** (each is a work item below):

1. **No list of organizations** and no cross-org charger list.
2. **No per-charger facts**: sessions store no connect time, last-message time, remote address, boot details (vendor/model/firmware) or connector statuses. `ChargerSession` has none of this.
3. **No live updates.** Nothing is pushed; a UI could only poll.
4. **No history API.** MongoDB has `charger_statuses`, `transactions`, `meter_values`, `charger_heartbeats_latest`, but no endpoint reads them, **no indexes exist**, and there is **no retention**. Incoming OCPP requests are not logged as raw messages, and the one message log that exists (`call_results`/`call_errors`) has no action name.
5. **No config write path.** Organizations, backends and charger credentials live in `config.yaml`, loaded once at startup. There is no reload, no way to change a live session's backends, and PyYAML would drop the file's comments if we rewrote it.
6. **No audit trail** of who sent which command or changed what, and **no throttling of failed API-key attempts**.
7. **No command catalog.** The typed routes exist, but a UI needs one list of actions with their field schemas.

## Assessment findings (2026-10-04)

Checked against the code before starting Phase 0. These change the plan above:

1. **The OpenAPI schema has almost no response types.** None of the 47 operations declares a `response_model`; every 200 response is untyped. A client generated from `/openapi.json` would be `unknown` everywhere. **Each route the UI uses gets a Pydantic response model in the same change that first uses it** (tags in Phase 1, chargers and events in Phase 2), and the drift check compares the generated client against that schema. This is real extra backend work in every phase, not a one-off.
2. **The existing `/api/mongodb/*` POST routes can forge history.** Anyone holding the API key can write arbitrary status, meter-value, transaction and message documents into the collections the History pages read. Before Phase 3 these routes are removed (or made read-only), otherwise history cannot be trusted as an audit record. They are not used by the broker itself.
3. **`GET /api/tags/organizations` already exists**, but it lists organizations that have tag state, not the configured organizations, so a real `GET /api/orgs` is still needed.
4. **No collision between `/ui` and the charger endpoint.** The charger route is a WebSocket route (`/{org_name}/{charger_id}`), which only matches WebSocket upgrades; plain HTTP `/ui/...` never reaches it. A reserved organization name is therefore not needed in the broker. It only matters for a reverse proxy that routes by path, and goes in the deployment guide.
5. **`ui.enabled` must be checked per request.** The FastAPI app and the routers are created at import time, before the configuration is read, and Starlette cannot unmount a route. The `/ui` routes are always registered and answer 404 when the setting is off.
6. **Command history today is one in-memory dict** (`pending_responses`, last 1000, not indexed by charger), so "command history per charger" needs the `commands` collection or an in-memory per-session ring, not the existing store.
7. **Toolchain.** Node is installed on the development machine (v26, npm 11); CI needs `actions/setup-node`. Target the current LTS in CI rather than the newest release.

## Architecture

```
Browser (React SPA, /ui)  --- HTTPS --->  reverse proxy  --->  broker :8765
   |  API key in sessionStorage                                   |-- /ui/*          static build + SPA fallback
   |  X-API-Key on every call                                      |-- /api/*, /orgs  REST (existing + new)
   |  fetch-based event stream  <---------------- /api/events ---- |-- /api/events   live events (new)
                                                                   '-- MongoDB        history (optional)
```

- **Packaging.** The SPA lives in `ui/` at the repo root. Its build output goes to `src/ocpp_broker/ui_dist/` (git-ignored) and ships in the wheel (`[tool.setuptools.package-data]` is empty today and must list it). A new `ocpp_broker/ui.py` mounts the files and serves `index.html` for any unknown `/ui/...` path. Source installs without a build get a plain page explaining how to build the UI instead of a 404.
- **Typed API client** generated from `/openapi.json` (`openapi-typescript` + a thin fetch wrapper). CI regenerates it and fails on drift, so a backend change cannot silently break the UI.
- **Command forms** generated from the `ocpp` library's 78 JSON Schemas (`ocpp/v16/schemas/*Request.json`, JSON Schema draft-04) through a new catalog endpoint, instead of 19 hand-written forms. Check that the chosen form library handles draft-04 (react-jsonschema-form needs a draft-04 validator) before committing to it.
- **Live updates.** An in-process event bus (`asyncio` publish/subscribe) fed by the session code, exposed as a Server-Sent-Events stream. `EventSource` cannot send an `X-API-Key` header and the key must not go in a URL, so the UI reads the stream with `fetch` and a stream reader. Slow subscribers get a bounded queue and are dropped, never blocking OCPP traffic. Reverse proxies must not buffer it (`proxy_buffering off`); this goes in the deployment guide.
- **A feature flag**, `ui.enabled` (setting, default on until you decide otherwise), so a deployment that does not want a console on the charger-facing port can switch it off. See finding 5 for why it is checked per request.
- **One process, in-memory sessions** is unchanged: the UI shows the instance it is served from. Say so on the dashboard when more than one instance runs.

## New broker endpoints

Names are a proposal. All sit behind the existing API key and appear in the OpenAPI schema; each needs docs (the docs test will enforce that).

| Area | Endpoint | Notes |
|------|----------|-------|
| System | `GET /api/system/info` | version, uptime, instance id, MongoDB state (live ping), `ui.enabled` |
| Orgs | `GET /api/orgs` | name, mode, subprotocol, auth required, connected count, backend summary |
| Chargers | `GET /api/chargers?org=&q=&online=` | online ones from sessions; with MongoDB also offline ones (`charger_configurations`, `charger_heartbeats_latest`) |
| Chargers | `GET /api/chargers/{org}/{id}` | online flag, connected_at, last_seen, remote address, boot info, connector statuses, backend links, buffered count |
| Events | `GET /api/events` (SSE) | `charger.connected/disconnected/replaced`, `charger.status`, `backend.link`, `backend.failover`, `command.result`, `transaction.started/stopped` |
| Commands | `GET /api/ocpp/commands/catalog` | action name, direction, JSON Schema, typed route |
| History | `GET /api/history/status`, `/transactions`, `/transactions/{org}/{id}`, `/meter-values`, `/messages`, `/commands` | cursor pagination, time-range and charger filters; `503` without MongoDB |
| Admin | organization and backend CRUD, credential set/rotate/delete, `POST /api/admin/config/validate`, `POST /api/admin/config/apply`, `GET /api/admin/audit` | see "Admin" below |

## UI pages

1. **Sign-in**: paste the API key; validated with a harmless authenticated call; kept in `sessionStorage` (cleared when the tab closes), never in the URL or `localStorage`.
2. **Overview**: orgs, online charger counts, backend link health, MongoDB state, recent events.
3. **Chargers**: filterable table (org, online, text search), live status dots, replaced/dropped markers.
4. **Charger detail**: identity and boot info, connector cards with live status, command panel (schema form, result card showing `success`/`error`/`timeout`), command history, status timeline, transactions, meter-value chart, live message log (if capture is on).
5. **Backends**: per org and charger, leader/follower, connected, buffered frames, failover events.
6. **Tags**: table with search and paging, add/edit/delete, bulk, **import wizard** (upload, `validate_only` preview with per-record errors, then apply), export, statistics, MongoDB **Sync** with the `loaded/seeded/dropped` summary.
7. **Transactions**: list with filters; detail with start/stop, energy (`meter_stop - meter_start`), meter chart.
8. **Organizations (admin)**: view, create, edit, delete; backends; charger credentials; relay tuning; review-and-apply flow.
9. **Settings**: version, instance, MongoDB, link to the Swagger docs.

## History: what to build on the broker side

This is the part most likely to be underestimated.

- **Indexes.** Create them at startup (idempotently) for the collections the UI queries: `(org_name, charger_id, timestamp)` on statuses and meter values, `(org_name, charger_id, transaction_id)` on transactions, and so on. Without them every page is a collection scan.
- **Retention.** TTL indexes with configurable days per collection (`mongodb.retention.*`); defaults must be conservative and documented.
- **A real message log.** A new `ocpp_messages` collection recording both directions with action, ids and payload, replacing the nameless `call_results`/`call_errors` documents. Capture must be switchable and sampled/limited (heartbeats are the bulk of the volume) and sit behind a bounded write queue so a slow MongoDB never delays a charger reply. Fix the existing `call_results` noise first.
- **Command history.** One `commands` collection instead of one collection per action, holding request, outcome and time. The per-action collections stay for compatibility until removed deliberately.
- **MongoDB resilience** (existing gaps): retry the connection after a failed start, and make the health check ping.
- Without MongoDB the History pages show an empty state that explains why; the live pages keep working.

## Admin: the hard part

Editing organizations, backends and credentials from the UI means the broker must change configuration at runtime. Today it cannot. Decide this before building the admin pages:

| Option | Pros | Cons |
|--------|------|------|
| **A. Edit `config.yaml`** with atomic write + a reload endpoint | Keeps one familiar source of truth; works without MongoDB | Comments are lost unless we move to `ruamel.yaml`; the UI can overwrite hand edits; file permissions/containers; multi-instance drift |
| **B. Store organizations in MongoDB**, `config.yaml` seeds it | Natural for a UI, shared across instances, easy audit | Makes MongoDB mandatory for admin; two sources of truth to explain; migration path needed |

Recommendation: build a `ConfigStore` interface now, implement **A** first (validate with the existing `load_config` rules, write atomically, keep a timestamped backup, apply), and keep **B** as the follow-up if multi-instance matters. Either way:

- **Apply semantics must be explicit.** New settings apply to connections made after the change; existing sessions keep theirs until they reconnect, with an optional "drop connections of this org now" action. State this in the UI before the user applies.
- **Validate before applying**, reusing the loader's rules, and show the diff.
- **Secrets.** Credentials are set or rotated through a write-only call that hashes server-side; hashes and passwords are never returned to the browser.
- **Audit log** of every mutating call (time, action, target, source address, outcome). With one shared API key it cannot say *who*; see Auth.

## Auth and security

- v1 is a single shared API key: everyone with it is a full admin. The UI should say so on sign-in.
- Because the key lives in the browser, **XSS is the main risk.** Serve the SPA with a strict CSP (no inline scripts, `connect-src 'self'`), no third-party scripts or CDNs, `Cache-Control: no-store` on `index.html`, `X-Content-Type-Options`, `Referrer-Policy: no-referrer`. React escapes by default; ban `dangerouslySetInnerHTML` with a lint rule.
- **Throttle failed key attempts** per source address; today a key can be guessed at full speed.
- Never put the key in URLs, logs, or the event stream query string. Redact `X-API-Key` and `Authorization` in any request logging added for the UI.
- Cheap upgrade path to give the audit log an identity: allow **several labelled API keys** in configuration (label recorded in the audit log), before any user-account system. Real accounts/roles stay out of v1.
- Terminate TLS at the reverse proxy (already the documented setup); the UI and the charger port share a listener, so restrict `/ui`, `/api`, `/orgs`, `/docs` to your own network exactly as `docs/deployment.md` describes for the API.

## Testing

Follow the project's existing approach (real sockets, mutation-checked):

- **Backend:** unit and integration tests for every new endpoint using the existing real-uvicorn fixtures; event-stream tests that connect a scripted charger and assert the events; history tests over the in-memory fake MongoDB already used by the tag-sync tests, plus a CI job against a real MongoDB container for the index and TTL behaviour that the fake cannot show.
- **Frontend:** `tsc --noEmit`, ESLint, Vitest + React Testing Library for components, MSW for API mocks.
- **End to end:** Playwright against a real broker started by the test with a scripted charger (the `ScriptedCharger`/`FakeBackend` helpers already exist): sign in, see the charger appear live, send a `Reset`, see the reply, import tags with a bad record, fail over a leader and watch the backend view change.
- **Contract:** the OpenAPI drift check above.
- Keep `tests/test_docs.py` green: new endpoints and settings are documented in the same change.

## The product, and what it means for the UI

ocpp-broker connects **one charger to several OCPP backends**: one is the **leader** (the charger's only real conversation partner), the others are **followers** (observe-only copies of the charger's requests, and a standby that can take over). The broker can also be the backend itself. The UI is therefore a **topology and health console first**: for every charger, who is the leader, which followers are linked, whether links are up, what is buffered, and when failover happened. Tags, history and admin are supporting features.

## Broker as the leader (local backend)

Requirement (2026-10-04): when the broker itself is the charger's leader it must receive and answer the charger like a full OCPP backend.

**What exists** (read from `charge_point.py`, `session.py`, `api_server.py`):

| Area | State |
|------|-------|
| Charger to backend, standard OCPP 1.6 Core | **All 10 actions answered**: BootNotification, Authorize, Heartbeat, StatusNotification, MeterValues, StartTransaction, StopTransaction, DataTransfer, DiagnosticsStatusNotification, FirmwareStatusNotification. The `ocpp` library validates every payload. |
| Backend to charger | **All 19 standard commands** can be sent through REST and return the charger's real reply. Nothing is sent automatically. |
| Not implemented | The 12 OCPP 1.6 *Security* extension actions (SecurityEventNotification, SignCertificate, LogStatusNotification, SignedFirmwareStatusNotification, ExtendedTriggerMessage, and the backend-side CertificateSigned, DeleteCertificate, GetInstalledCertificateIds, GetLog, InstallCertificate, SignedUpdateFirmware). A charger sending one gets a `NotImplemented` CallError. |
| **Topology** | **Broker mode and relay mode are exclusive.** With `connect_to_backend: false` the broker answers locally and `backends` is ignored: it cannot be the leader *and* copy traffic to followers, and it cannot be a standby that takes over from a failed external leader. |

**Depth gaps** (the broker answers every message, but is a thin backend):

1. **No per-charger state.** Boot details, connector statuses, the open transaction and last-seen time are only logged, or written to MongoDB if present. Nothing can be read back, so neither a UI nor smarter replies are possible. Without MongoDB everything is forgotten on restart.
2. **BootNotification is always `Accepted`.** No `Pending`/`Rejected`, no list of known chargers; any id under an organization is accepted (credentials aside).
3. **StartTransaction is not idempotent.** A charger that retries after a lost reply gets a second transaction id and a second transaction. An id is also issued for an invalid tag, and there is no concurrent-transaction, busy-connector or reservation handling.
4. **StopTransaction is not validated**: it does not check the transaction exists, ignores duplicates poorly, and drops `transactionData`.
5. **No offline detection** (a charger that stops heartbeating is only noticed when the socket dies).
6. **No post-boot behaviour**: nothing configures the charger, triggers a status report or pushes the local authorization list.

**Work items** (workstream B; the UI shows each result):

| Id | Item |
|----|------|
| B1 | **Per-charger state model**, in memory for every session. In broker mode the handlers fill it; in relay mode it is filled by passively parsing the relayed frames (Boot, StatusNotification, Start/StopTransaction), so the console shows the same facts in both modes. This is also what the live console needs, so it is built once, in Phase 1. |
| B2 | **Local leader.** A backend entry that means "this broker" (proposal: `local: true` on one `backends` item, leader or not). The local backend answers the charger through `BrokerChargePoint`; the other backends are followers receiving copies of the charger's requests, exactly as today. The session needs a tee on the charger's receive path. |
| B3 | **Backend depth**: idempotent StartTransaction (a retry with the same charger, connector, tag, meter start and timestamp returns the first id), transaction validation on stop, `transactionData`, an optional boot policy (`Pending`/`Rejected` for unknown chargers), offline detection, and tag/reservation checks that a real backend performs. |
| B4 | **Failover with a local member** (optional, see open question 7): a local standby that takes over when the external leader fails, and the reverse. |
| B5 | **Conformance tests**: one table-driven test per standard action in both directions, run in broker mode, local-leader mode and relay mode, using the existing scripted charger and fake backend. |
| B6 | Docs: leader-follower, configuration, broker_as_backend. |

## Phases

Sizes are relative effort, not calendar promises: S ≈ days, M ≈ 1-2 weeks, L ≈ 2-4 weeks for one developer.

Reordered on 2026-10-04: the topology console comes first and tags move behind it, because live leader/follower state is what this product is for.

| Phase | Content | Size |
|-------|---------|------|
| **0. Foundations** | **Backend done:** `/ui` serving, CSP, `ui.enabled`, `GET /api/system/info`. **Remaining:** `ui/` scaffold (Vite, React, TypeScript), sign-in shell, packaging into the wheel, CI (Node build, lint, test, wheel with UI, OpenAPI drift). | M |
| **1. Live topology console** | B1 state model; `GET /api/orgs`, `GET /api/chargers`, `GET /api/chargers/{org}/{id}` (typed responses); event bus + SSE; command catalog; pages: overview, chargers, charger detail with topology (leader/followers/local, link state, buffered frames, failover events), command panel, backends. | L |
| **2. Local leader and backend depth** | B2 to B6. Backend work, shown in the console from Phase 1. Can overlap with Phase 3. | L |
| **3. Tags** | Tag pages on the existing API (add typed responses first): table, edit, bulk, import wizard, export, stats, sync. | S-M |
| **4. History** | Indexes, retention, `ocpp_messages` and `commands` collections, history endpoints; remove the forgeable `/api/mongodb` POST routes first; transactions and status timeline before the raw message log. | L |
| **5. Admin** | `ConfigStore`, validate/apply with backup, org/backend/credential CRUD, audit log, throttling, labelled keys. | L |
| **6. Hardening** | A11y pass, empty/error/loading states, large-list performance (virtualised tables), docs and screenshots, release process. | M |

Order: 0 → 1 → (2 and 3 in either order) → 4 → 5 → 6. Admin stays late because it depends on a decision (file or MongoDB) and changes how the broker behaves at runtime.

## Build log

| Piece | Commit | Notes |
|-------|--------|-------|
| `/ui` serving, CSP, `ui.enabled`, `GET /api/system/info` | `afa6aeb` | Per-request enable check (the app is built before the config is read). |
| Console shell: sign-in and overview, packaging, CI | Phase 0 frontend commit | Vite + React + TypeScript; TypeScript pinned to 5.9 (typescript-eslint and openapi-typescript do not support 7 yet). A wheel was built and its console served from an isolated install. |
| Per-charger state, `/api/orgs`, `/api/chargers`, charger detail | Phase 1 backend commit | State is learned by watching frames the same way in both modes. |
| Chargers list, charger page, topology, transactions, error boundary | Phase 1 frontend commit | 90 Vitest tests; every deliberate break of the pages fails a test. |

What building it changed in the plan:

1. **The state model needed no new protocol work.** Watching frames (in the relay loop and in the broker-mode adapter) gives the same boot details, connector statuses and counters in both modes, so B1 was small.
2. **Polling is good enough for now.** The event stream is not needed to make the pages useful; it can be added without changing them.
3. **The OpenAPI contract check compares the contract, not the text**, so a different FastAPI version in CI cannot fail it for wording.
4. **Typed responses are added per endpoint** as planned (finding 1); the older routes are still untyped, so the console only uses typed ones.
5. **An error boundary was added** after a mismatched test fixture showed that one render error blanked the whole console.

## Risks

| Risk | Mitigation |
|------|------------|
| Admin needs runtime reconfiguration, which the broker lacks | Decide A vs B up front; implement behind `ConfigStore`; apply to new connections only; explicit UI wording |
| History volume and cost (heartbeats, meter values) | Capture switches, sampling, TTL, bounded write queue, indexes before any history UI |
| Shared API key = full admin, key stored in the browser | CSP and no third-party code; throttling; audit; labelled keys; document the trade-off |
| Node toolchain added to a pure-Python project | Build only in CI and for release wheels; plain fallback page for source installs; keep `pip install ocpp-broker` free of Node |
| Event stream through proxies | `proxy_buffering off` documented; heartbeats on the stream; the UI falls back to polling |
| Multi-instance deployments see a partial picture | State the instance on the dashboard; shared state (event bus over MongoDB change streams or Redis) is out of scope for v1 |
| UI drifting from the API | Generated client + CI drift check; Playwright against a real broker |

## Open questions for you

1. **Is MongoDB acceptable as a requirement for the History pages** (they would be empty without it), or must history work without it?
2. **Admin config store:** start with editing `config.yaml` (option A) or move organizations into MongoDB (option B)?
3. **Visibility:** should some API keys be limited to certain organizations, or is one key seeing everything fine for v1?
4. **Retention defaults** for statuses, meter values and the message log, and whether the message log should be on by default.
5. **Where the UI is exposed:** on the same port as chargers (as planned) with `ui.enabled` on by default, or off unless you opt in? (Currently on by default; flip it in one line.)
6. **Local leader semantics (B2):** is "broker is the leader, external backends are observe-only followers" the topology you mean? And should a local backend be configured as an entry in `backends` (proposal) or by a separate switch on the organization?
7. **Failover with a local member (B4):** should the broker be able to take over as leader when the external leader fails (a built-in standby), and hand back afterwards? Today there is no automatic fail-back at all.
