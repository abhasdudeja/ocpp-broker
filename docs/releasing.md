# Releasing

The version has one source: `version` in `pyproject.toml`. `ocpp_broker.__version__` and the REST API's version both read the installed package metadata.

## Checklist

1. **Everything green.** On the branch to release, CI (`.github/workflows/pypi.yml`) passes: ruff and mypy, the console (ESLint, type check, Vitest, build), the Python tests on 3.10, 3.11 and 3.12 with the console inside the wheel, and the MongoDB tests against a real MongoDB (`mongo-tests`).
2. **Run it once for real.** Start a broker with a configuration of your own and a charger, open `/ui`, sign in, and look at the pages you changed. Tests do not see layout.
3. **Changelog.** Move the entries under *Unreleased* in `CHANGELOG.md` to a heading with the new version and the date; say what was added, changed and removed, and anything a user must do when upgrading (a setting that changed meaning, a route that went away).
4. **Version.** Raise `version` in `pyproject.toml` (and nowhere else). The tests fail if another module hard-codes a version.
5. **Docs.** `pytest tests/test_docs.py` passes: documented routes, settings and links exist. Change the docs in the same commit as the behaviour.
6. **Tag and publish.** Commit, tag `v<version>`, push, then publish a GitHub release for the tag. The `publish` job builds the console, builds the package and uploads it to PyPI, but only after every other job has passed.
7. **Check the package.** In a clean environment `pip install ocpp-broker==<version>`, start `ocpp-broker-server`, open `/ui`.

## What the build does

- The console is built from `ui/` into `src/ocpp_broker/ui_dist` (not committed) and shipped inside the wheel; CI fails if the wheel lacks `ui_dist/index.html`.
- `ui/openapi.json` and the typed client under `ui/src/api/` are generated from the broker's API (`python scripts/export_openapi.py`, then `npm run api` in `ui/`). A test fails if they are out of date, so run both after changing an API route or model and commit the result.

## Upgrading notes worth repeating in the release

- Early versions had `POST /api/mongodb/...` routes that wrote records; they are gone.
- The admin API is off until `admin.enabled: true`.
- Saving from the admin API rewrites `config.yaml` without comments.
