# Web Console

The broker serves a web console at `/ui` on its own port, next to the charger WebSocket and the REST API. It is an early version. The pages that exist are listed below; more (commands, tags, history) are planned, see `plans/ui-plan.md` in the repository.

## Opening it

```
http://localhost:8765/ui/
```

Sign in with the broker's API key (`security.api_key` or `OCPP_BROKER_API_KEY`, see [Configuration](configuration.md#security-section)). There are no user accounts: everyone who has the key can do everything the API allows, and the sign-in page says so.

What the sign-in does:

- It checks the key with `GET /api/system/info` ([API Reference](api-reference.md#system-information)). A wrong key says so; a broker with no key configured says that instead (the API answers `503` until one is set); an unreachable broker says that.
- The key is kept in the browser tab's `sessionStorage` only, so it is gone when the tab closes. It is sent in the `X-API-Key` header and never in a URL. If the broker later stops accepting it (the key was changed), the console signs out and says why.

## The overview

Figures from `GET /api/system/info`, refreshed every 10 seconds: broker version, instance id, uptime, organizations, chargers connected to **this instance** (sessions are per process), whether the API needs a key, whether MongoDB answers right now, and whether the console is built into this installation. It warns when the API accepts requests without a key, or when MongoDB is configured but not answering. If a refresh fails it keeps the last figures on screen and says so.

Below them, a table of the **organizations** from `GET /api/orgs`: mode (broker or relay) and OCPP version, how many chargers are connected (a link to the chargers page filtered to that organization), the backends with the configured leader marked, whether transaction ids are translated for them, and whether chargers must authenticate.

## Chargers

The chargers connected to this broker instance (`GET /api/chargers`, refreshed every 5 seconds). One row per charger: its id (a link to the charger's page), vendor and model once it has booted, organization, mode, the **leader** it currently talks to with a dot for the link state (in broker mode the leader is "this broker"), how many **followers** are connected, the status of each connector, how many transactions are running, and how long ago it last sent a message.

Things that need attention are marked: a leader that is down, charger messages **waiting** for it, followers that are not all connected, a **Faulted** connector, and running transactions in which a backend is being **skipped** (the broker never learned that backend's transaction id).

Search by charger id and filter by organization; both are kept in the address, so a filtered list can be bookmarked or shared. A charger that is not connected to this instance is not listed.

## A charger

`/ui/chargers/{org}/{charger id}` (`GET /api/chargers/{org}/{charger_id}`, refreshed every 3 seconds):

- **Connections:** a diagram of charger, broker and the backends for this charger, the leader first. Each backend shows its role, address, whether the link is up and for how long it has been down, and how many charger messages are waiting for it. After a failover the new leader is the first box. A text description is there for screen readers.
- **Connection** and **Identity:** the address the broker sees, when it connected, last message and last heartbeat, message counts, and what the charger said in its `BootNotification` (vendor, model, serial number, firmware, ICCID, IMSI, meter).
- **Connectors:** each connector's status, error code and text, and how long ago it changed.
- **Transactions:** one row per transaction with the id the **charger holds**, its state, and **each backend's own id** in its own column, so a backend's dashboard can be matched with the charger's log. A backend marked *skipped* never learned its id and is not sent that transaction's messages; *waiting for its id* means a follower has not yet answered. When transaction ids are not translated for this charger the page says so.
- **Reservations** and **charging profiles** the charger holds, with each backend's own number, and a collapsed list of what the transaction id table has done (ids rewritten, remapped, skipped).

If the charger is not connected to this instance the page says so and keeps checking; if it disconnects while open, the page keeps what it last showed and says that it is out of date. If a page fails to render something it was sent, the console shows a message and the navigation still works.

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

The console's types come from the broker's OpenAPI schema. After changing the API, run `python scripts/export_openapi.py` (writes `ui/openapi.json`) and then `npm run api` in `ui/` (writes `schema.d.ts` in the console's `api` source folder), and commit both. A test (`tests/test_openapi_contract.py`) fails when `ui/openapi.json` no longer matches the API.

## Related documentation

- [API Reference](api-reference.md)
- [Configuration](configuration.md)
- [Deployment](deployment.md)
