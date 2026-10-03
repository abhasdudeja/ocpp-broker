"""
The documentation must describe the code that exists.

Checks, over every Markdown file under docs/ and the root README:
  * every REST route mentioned is a real route, and curl examples use a real method
  * every YAML block uses only settings the broker reads, and complete configs load
  * every relative link (and #anchor) resolves
  * names that were once documented but never existed do not come back

A block or line can opt out with an HTML comment on the line before it:
    <!-- docs-test: skip -->
"""

import re
from pathlib import Path

import pytest
import yaml

from ocpp_broker import server
from ocpp_broker.config import load_config

ROOT = Path(__file__).resolve().parents[1]
DOC_FILES = sorted([*ROOT.joinpath("docs").rglob("*.md"), ROOT / "README.md"])
SKIP = "<!-- docs-test: skip -->"


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _fenced_blocks(text: str):
    """Yield (language, body, start_line, skipped) for each fenced code block."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^\s*```(\S*)", lines[i])
        if match:
            lang, start = match.group(1), i
            skipped = i > 0 and lines[i - 1].strip() == SKIP
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            yield lang, "\n".join(body), start + 1, skipped
        i += 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
_OPENAPI = server.app.openapi()["paths"]
_HEALTH = {"/health": {"get", "head"}, "/ocpp-check": {"get"}}


def _templates() -> dict[str, set[str]]:
    table = {path: set(methods) for path, methods in _OPENAPI.items()}
    for path, methods in _HEALTH.items():
        table[path] = methods
    return table


def _to_regex(template: str) -> re.Pattern:
    return re.compile("^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(template)) + "$")


_PATTERNS = {tmpl: _to_regex(tmpl) for tmpl in _templates()}


def _normalise(path: str) -> str:
    path = path.split("?")[0].rstrip(".,;:)`'\"")
    # placeholders: {org}, $BROKER-free shell vars, <id>
    path = re.sub(r"\$\{?\w+\}?", "X", path)
    path = re.sub(r"<[^>]*>", "X", path)
    return path.rstrip("/") or "/"


def _match_route(path: str):
    path = _normalise(path)
    if "/.../" in path:  # "/api/ocpp/.../commands": "..." stands for several segments
        head, _, tail = path.partition("/.../")
        return [
            tmpl for tmpl in _PATTERNS
            if re.sub(r"\{[^}]*\}", "X", tmpl).startswith(head + "/") and tmpl.endswith("/" + tail)
        ]
    return [tmpl for tmpl, pattern in _PATTERNS.items() if pattern.match(path)]


def _is_prefix_of_real_route(path: str) -> bool:
    """`/api/mongodb/` style mentions of a whole family of routes."""
    path = _normalise(path)
    return any(tmpl == path or tmpl.startswith(path + "/") for tmpl in _templates())


# Preceded by anything (a host, $BROKER, a quote); a bare "/api" or "/orgs" is ignored later.
ROUTE_RE = re.compile(r"(/(?:api|orgs)(?:/[A-Za-z0-9_{}<>$.\-]*)*)")


def _route_mentions(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if number > 1 and lines[number - 2].strip() == SKIP:
            continue
        if "ws://" in line or "wss://" in line:
            continue
        for match in ROUTE_RE.finditer(line):
            yield number, match.group(1)


@pytest.mark.parametrize("doc", DOC_FILES, ids=_rel)
def test_every_route_mentioned_exists(doc):
    bad = []
    for number, route in _route_mentions(doc):
        if _normalise(route) in ("/api", "/orgs"):
            continue
        if not _match_route(route) and not _is_prefix_of_real_route(route):
            bad.append(f"{_rel(doc)}:{number}: {route}")
    assert not bad, "routes that do not exist:\n" + "\n".join(bad)


_CURL_METHOD = re.compile(r"-X\s+([A-Z]+)")
_CURL_PATH = re.compile(r"""["']?(?:\$\{?\w+\}?|https?://[^/\s"']+)?(/(?:api|orgs)[^\s"']*)""")


@pytest.mark.parametrize("doc", DOC_FILES, ids=_rel)
def test_curl_examples_use_the_right_method(doc):
    bad = []
    for lang, body, start, skipped in _fenced_blocks(doc.read_text(encoding="utf-8")):
        if skipped or lang not in ("bash", "sh", "shell", "console", ""):
            continue
        commands = re.sub(r"\\\r?\n", " ", body).splitlines()
        for offset, command in enumerate(commands):
            if "curl" not in command or "/api" not in command and "/orgs" not in command:
                continue
            path_match = _CURL_PATH.search(command)
            if not path_match:
                continue
            templates = _match_route(path_match.group(1))
            if not templates:
                continue  # unknown routes are reported by the route test
            method = (_CURL_METHOD.search(command) or [None, None])[1]
            if method is None:
                method = "POST" if re.search(r"\s(-d|--data\S*)\s", command) else "GET"
            allowed = set().union(*(_templates()[t] for t in templates))
            if method.lower() not in allowed:
                bad.append(f"{_rel(doc)}:{start + offset + 1}: {method} {path_match.group(1)} (allowed: {sorted(allowed)})")
    assert not bad, "curl examples with a method the route does not accept:\n" + "\n".join(bad)


# ---------------------------------------------------------------------------
# YAML configuration
# ---------------------------------------------------------------------------
BACKEND = {"id": None, "url": None, "leader": None, "ocpp_subprotocol": None}
ORG = {
    "name": None,
    "connect_to_backend": None,
    "ocpp_subprotocol": None,
    "tags": None,
    "charger_auth": {"required": None, "credentials": None},
    "backends": [BACKEND],
    "backend_buffer_size": None,
    "backend_outage_timeout": None,
    "leader_failover_timeout": None,
    "transaction_ids": {"mapping": None, "follower_wait": None, "dedupe_start": None, "retain_closed": None},
}
TOP = {
    "broker": {"host": None, "port": None},
    "mongodb": {"enabled": None, "connection_string": None, "database_name": None},
    "ocpp": {"commands": {"core": {"heartbeat_interval": None}}},
    "security": {
        "api_key": None,
        "allow_unauthenticated_api": None,
        "cors": {"allow_origins": None, "allow_credentials": None},
        "websocket": {"ping_interval": None, "ping_timeout": None},
    },
    "ui": {"enabled": None},
    "data_transfer": {
        "enabled": None, "known_vendors": None, "known_message_ids": None,
        "validate_vendors": None, "validate_message_ids": None, "vendors": None, "vendor_messages": None,
    },
    "organizations": [ORG],
}


def _unknown_keys(node, schema, where="") -> list[str]:
    """Keys in ``node`` that the broker does not read."""
    if schema is None:
        return []
    if isinstance(schema, list):
        if not isinstance(node, list):
            return []
        return [k for item in node for k in _unknown_keys(item, schema[0], where)]
    if not isinstance(node, dict):
        return []
    problems = []
    for key, value in node.items():
        if key not in schema:
            problems.append(f"{where}{key}")
        else:
            problems += _unknown_keys(value, schema[key], f"{where}{key}.")
    return problems


def _classify(parsed: dict):
    keys = set(parsed)
    if keys <= set(TOP):
        return TOP
    if keys <= set(ORG):
        return ORG
    if keys <= set(BACKEND):
        return BACKEND
    if keys <= {"required", "credentials"}:
        return {"required": None, "credentials": None}
    return None


def _yaml_blocks(doc: Path):
    for lang, body, start, skipped in _fenced_blocks(doc.read_text(encoding="utf-8")):
        if lang in ("yaml", "yml") and not skipped:
            yield start, body


@pytest.mark.parametrize("doc", DOC_FILES, ids=_rel)
def test_yaml_examples_only_use_settings_the_broker_reads(doc):
    problems = []
    for start, body in _yaml_blocks(doc):
        try:
            parsed = yaml.safe_load(body)
        except yaml.YAMLError as exc:
            problems.append(f"{_rel(doc)}:{start}: not valid YAML ({str(exc).splitlines()[0]})")
            continue
        if not isinstance(parsed, dict):
            continue  # a list or scalar fragment
        schema = _classify(parsed)
        if schema is None:
            problems.append(f"{_rel(doc)}:{start}: unrecognised top-level keys {sorted(parsed)}")
            continue
        for key in _unknown_keys(parsed, schema):
            problems.append(f"{_rel(doc)}:{start}: '{key}' is not read by the broker")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("doc", DOC_FILES, ids=_rel)
def test_complete_yaml_configs_load(doc, tmp_path):
    for start, body in _yaml_blocks(doc):
        try:
            parsed = yaml.safe_load(body)
        except yaml.YAMLError:
            continue  # reported by the test above
        if not isinstance(parsed, dict) or "organizations" not in parsed:
            continue
        path = tmp_path / f"config_{start}.yaml"
        path.write_text(body, encoding="utf-8")
        try:
            load_config(str(path))
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{_rel(doc)}:{start}: config does not load: {exc}")


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------
def _slug(heading: str) -> str:
    text = re.sub(r"`", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    anchors, seen = set(), {}
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        match = re.match(r"^#{1,6}\s+(.*)", line)
        if match:
            slug = _slug(match.group(1))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")


@pytest.mark.parametrize("doc", DOC_FILES, ids=_rel)
def test_relative_links_and_anchors_resolve(doc):
    bad = []
    in_fence = False
    for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        for target in LINK_RE.findall(line):
            if re.match(r"^[a-z]+:", target):
                continue  # http:, https:, mailto:
            file_part, _, anchor = target.partition("#")
            dest = (doc.parent / file_part).resolve() if file_part else doc
            if not dest.exists():
                bad.append(f"{_rel(doc)}:{number}: {target} (no such file)")
            elif anchor and dest.suffix == ".md" and anchor not in _anchors(dest):
                bad.append(f"{_rel(doc)}:{number}: {target} (no such heading)")
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------------------
# Things that were documented but never existed
# ---------------------------------------------------------------------------
FICTION = [
    "OCPP_BROKER_LOG_LEVEL", "OCPP_BROKER_HOST", "OCPP_BROKER_PORT", "--dry-run", "--verbose",
    "message_validator", "validate_messages_when", "command_router", "ocpp_router", "enable_ocpp_router",
    "DynamicLeaderManager", "add_backend_dynamic", "promote_leader", "reload_from_config",
    "/api/metrics", "/api/connections", "/api/organizations", "/api/chargers", "/api/backends",
    "/api/config", "/api/logs", "/api/ocpp/validate", "requirements-dev.txt", "FormationViolation",
    "docker pull your-org", "your-org/ocpp-broker", "support@your",
]


#: A line that says the thing does NOT exist is allowed to name it.
NEGATION = re.compile(r"\b(no|not|never|removed|ignored|used to|no longer|nothing)\b", re.IGNORECASE)


@pytest.mark.parametrize("doc", DOC_FILES, ids=_rel)
def test_invented_names_stay_out(doc):
    lines = doc.read_text(encoding="utf-8").splitlines()
    bad = []
    for number, line in enumerate(lines, start=1):
        if number > 1 and lines[number - 2].strip() == SKIP:
            continue
        if NEGATION.search(line):
            continue
        for name in FICTION:
            if name in line:
                bad.append(f"{_rel(doc)}:{number}: {name}")
    assert not bad, "\n".join(bad)


def test_no_doc_links_to_a_page_that_does_not_exist_in_the_index():
    """The index pages list real files only (guards the old README/SUMMARY, which listed ~15 missing pages)."""
    for index in (ROOT / "docs" / "README.md", ROOT / "docs" / "SUMMARY.md"):
        for target in LINK_RE.findall(index.read_text(encoding="utf-8")):
            if not re.match(r"^[a-z]+:", target):
                assert (index.parent / target.split("#")[0]).exists(), f"{_rel(index)} links to missing {target}"


def test_every_doc_page_is_reachable_from_the_index():
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    linked = {Path(t.split("#")[0]).as_posix() for t in LINK_RE.findall(index)}
    pages = {p.relative_to(ROOT / "docs").as_posix() for p in (ROOT / "docs").rglob("*.md")}
    missing = sorted(pages - linked - {"README.md"})
    # SUMMARY.md and index.md are navigation pages in their own right
    missing = [m for m in missing if m not in ("SUMMARY.md", "index.md")]
    assert not missing, f"pages not linked from docs/README.md: {missing}"
