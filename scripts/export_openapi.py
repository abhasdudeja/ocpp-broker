"""
Write the broker's OpenAPI schema to ui/openapi.json, the input of the web console's typed client,
and the command catalog to ui/src/test-fixtures/command-catalog.json, which the console's tests use.

    python scripts/export_openapi.py        # then, in ui/:  npm run api

tests/test_openapi_contract.py fails when the committed file no longer matches the API, so the
console cannot silently drift from the broker.
"""

import json
import pathlib

from ocpp_broker.commands import catalog
from ocpp_broker.server import app

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "ui" / "openapi.json"
CATALOG = ROOT / "ui" / "src" / "test-fixtures" / "command-catalog.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def render_catalog() -> str:
    return json.dumps(catalog(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    TARGET.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {TARGET}")
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(render_catalog(), encoding="utf-8", newline="\n")
    print(f"wrote {CATALOG}")
