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

One page of figures from `GET /api/system/info`, refreshed every 10 seconds: broker version, instance id, uptime, organizations, chargers connected to **this instance** (sessions are per process), whether the API needs a key, whether MongoDB answers right now, and whether the console is built into this installation. It warns when the API accepts requests without a key, or when MongoDB is configured but not answering. If a refresh fails it keeps the last figures on screen and says so.

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
