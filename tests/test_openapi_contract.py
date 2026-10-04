"""
The web console is typed from ui/openapi.json. This fails when the broker's API changes
without that file (and so the console's generated types) being regenerated.

It compares the *contract* (routes, parameters, request and response models and their fields)
rather than the raw text, so a different FastAPI or Pydantic version that words a title or a
description differently does not fail it.
"""

import json
import pathlib

from ocpp_broker.server import app

SNAPSHOT = pathlib.Path(__file__).resolve().parents[1] / "ui" / "openapi.json"
HOW_TO_FIX = "Run `python scripts/export_openapi.py`, then `npm run api` in ui/, and commit both files."


def _strip(node):
    """The schema without wording: titles and descriptions come and go between library versions."""
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in ("title", "description", "examples", "example")}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def contract(doc: dict) -> dict:
    paths = {}
    for path, operations in doc["paths"].items():
        for method, op in operations.items():
            paths[f"{method.upper()} {path}"] = {
                "parameters": sorted(
                    json.dumps(_strip({"in": p["in"], "name": p["name"], "required": bool(p.get("required")), "schema": p.get("schema")}), sort_keys=True)
                    for p in op.get("parameters", [])
                ),
                "request": _strip(op.get("requestBody", {}).get("content")),
                "responses": {
                    code: _strip(response.get("content"))
                    for code, response in op.get("responses", {}).items()
                    if code.startswith("2")
                },
            }
    schemas = {name: _strip(schema) for name, schema in doc.get("components", {}).get("schemas", {}).items()}
    return {"paths": paths, "schemas": schemas}


def test_the_committed_schema_matches_the_api():
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    live, saved = contract(app.openapi()), contract(committed)
    added = sorted(set(live["paths"]) - set(saved["paths"]))
    removed = sorted(set(saved["paths"]) - set(live["paths"]))
    changed = sorted(p for p in set(live["paths"]) & set(saved["paths"]) if live["paths"][p] != saved["paths"][p])
    schemas = sorted(n for n in set(live["schemas"]) | set(saved["schemas"]) if live["schemas"].get(n) != saved["schemas"].get(n))
    assert not (added or removed or changed or schemas), (
        f"ui/openapi.json is out of date. {HOW_TO_FIX}\n"
        f"routes added: {added}\nroutes removed: {removed}\nroutes changed: {changed}\nschemas changed: {schemas}"
    )


def test_the_contract_check_notices_real_changes():
    """A guard for the guard: the comparison must not be blind to a new route, field or type."""
    base = json.loads(json.dumps(app.openapi()))
    assert contract(base) == contract(json.loads(json.dumps(base)))

    wording = json.loads(json.dumps(base))
    wording["components"]["schemas"]["SystemInfo"]["title"] = "Something else"
    assert contract(wording) == contract(base), "titles are not part of the contract"

    renamed = json.loads(json.dumps(base))
    renamed["paths"]["/api/system/infos"] = renamed["paths"].pop("/api/system/info")
    assert contract(renamed) != contract(base)

    retyped = json.loads(json.dumps(base))
    retyped["components"]["schemas"]["SystemInfo"]["properties"]["version"]["type"] = "integer"
    assert contract(retyped) != contract(base)

    dropped = json.loads(json.dumps(base))
    del dropped["components"]["schemas"]["SystemInfo"]["properties"]["uptime_seconds"]
    assert contract(dropped) != contract(base)
