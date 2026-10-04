"""
Write the broker's OpenAPI schema to ui/openapi.json, the input of the web console's typed client.

    python scripts/export_openapi.py        # then, in ui/:  npm run api

tests/test_openapi_contract.py fails when the committed file no longer matches the API, so the
console cannot silently drift from the broker.
"""

import json
import pathlib

from ocpp_broker.server import app

TARGET = pathlib.Path(__file__).resolve().parents[1] / "ui" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    TARGET.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {TARGET}")
