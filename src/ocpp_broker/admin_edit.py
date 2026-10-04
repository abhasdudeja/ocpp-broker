"""
Turning the admin API's changes into organizations of the configuration file, and saying what they change.

An organization in the file holds more than the console edits (``tags``, ``tag_management``, ``chargers``, any
setting written by hand). A change to an organization therefore starts from what the file has and replaces only
the settings the console knows; the rest is kept as it is. A setting left empty is removed, so the broker's
default applies again.

Passwords are write-only: one that is sent is hashed here (``auth.hash_password``) and only the hash is written;
a credential sent without a password keeps what the file has. Nothing built here for display (the change lines)
holds a password or a hash.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .auth import hash_password
from .schemas.admin import AdminBackend, AdminChange, AdminChangeLine, AdminCredentialInput, AdminOrg, AdminOrgInput

NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # an organization is a segment of the charger's address
CHARGER_ID_PATTERN = re.compile(r"^[^\s/\\]{1,64}$")
MIN_PASSWORD = 8
RECOMMENDED_PASSWORD = 16

DEFAULTS = {"backend_buffer_size": 200, "backend_outage_timeout": 30, "leader_failover_timeout": 15}
FAILBACK_DELAY = 60
BACKEND_KEYS = ("id", "url", "leader", "local", "ocpp_subprotocol")
TX_KEYS = ("mapping", "follower_wait", "dedupe_start", "retain_closed", "retain_open")


@dataclass
class EditResult:
    organizations: List[Dict[str, Any]]
    changes: List[AdminChangeLine] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading an organization of the file
# ---------------------------------------------------------------------------
def normalized(org: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The organization as the broker reads it (defaults, the chosen leader), or None if it cannot be read."""
    from .config_store import check

    errors, _, runtime = check({"organizations": [org]})
    return None if errors or runtime is None else runtime["organizations"][0]


def _credentials(org: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The charger credentials of an organization of the file, whichever way they were written."""
    found: Dict[str, Dict[str, Any]] = {}
    for charger_id, entry in ((org.get("charger_auth") or {}).get("credentials") or {}).items():
        found[str(charger_id)] = {"password": entry} if isinstance(entry, str) else dict(entry or {})
    return found


def view(org: Dict[str, Any], runtime: Optional[Dict[str, Any]] = None) -> AdminOrg:
    """
    An organization as the console shows it. ``runtime`` is the same organization as the broker normalizes it
    (defaults filled in, a leader chosen); without it the file's own values are used.
    """
    source = runtime or org
    backends = [
        AdminBackend(id=b.get("id"), url=b.get("url"), leader=bool(b.get("leader")), local=bool(b.get("local")), ocpp_subprotocol=b.get("ocpp_subprotocol"))
        for b in source.get("backends") or []
    ]
    auth = source.get("charger_auth") or {}
    credentials = _credentials(source)
    return AdminOrg(
        name=str(org.get("name")),
        connect_to_backend=bool(source.get("connect_to_backend", True)),
        ocpp_subprotocol=str(source.get("ocpp_subprotocol") or "ocpp1.6"),
        backends=backends,
        backend_buffer_size=int(source.get("backend_buffer_size", DEFAULTS["backend_buffer_size"])),
        backend_outage_timeout=int(source.get("backend_outage_timeout", DEFAULTS["backend_outage_timeout"])),
        leader_failover_timeout=int(source.get("leader_failover_timeout", DEFAULTS["leader_failover_timeout"])),
        leader_failback=bool(source.get("leader_failback", False)),
        leader_failback_delay=float(source.get("leader_failback_delay", FAILBACK_DELAY)),
        transaction_ids={k: (source.get("transaction_ids") or {}).get(k) for k in TX_KEYS},  # type: ignore[arg-type]
        charger_auth_required=bool(auth.get("required", bool(credentials))),
        credentials=[
            {"charger_id": cid, "storage": "hash" if "password_hash" in entry else "plaintext"}  # type: ignore[misc]
            for cid, entry in sorted(credentials.items())
        ],
        tags=len(org.get("tags") or []),
    )


# ---------------------------------------------------------------------------
# Building an organization of the file from an input
# ---------------------------------------------------------------------------
def _backend_entry(backend: AdminBackend, org_subprotocol: str, previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    entry: Dict[str, Any] = {k: v for k, v in (previous or {}).items() if k not in BACKEND_KEYS}  # whatever else was written by hand
    if backend.id:
        entry["id"] = backend.id
    if backend.local:
        entry["local"] = True
    elif backend.url:
        entry["url"] = backend.url
    # A leader is marked; a backend that is not is left unmarked, except a local one, where false means "standby"
    if backend.leader:
        entry["leader"] = True
    elif backend.local and backend.leader is False:
        entry["leader"] = False
    if backend.ocpp_subprotocol and backend.ocpp_subprotocol != org_subprotocol and not backend.local:
        entry["ocpp_subprotocol"] = backend.ocpp_subprotocol
    # Only the keys in BACKEND_KEYS order, extras last, so the file reads the same way each time
    ordered = {k: entry[k] for k in BACKEND_KEYS if k in entry}
    ordered.update({k: v for k, v in entry.items() if k not in ordered})
    return ordered


def _match_backend(previous: List[Dict[str, Any]], backend: AdminBackend) -> Optional[Dict[str, Any]]:
    for candidate in previous:
        if backend.id and candidate.get("id") == backend.id:
            return candidate
        if not backend.id and backend.url and candidate.get("url") == backend.url:
            return candidate
    return None


def _merge_credentials(
    name: str, existing: Dict[str, Dict[str, Any]], given: List[AdminCredentialInput], errors: List[str], warnings: List[str]
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for item in given:
        charger_id = item.charger_id
        if not CHARGER_ID_PATTERN.match(charger_id):
            errors.append(f"{name}: {charger_id!r} is not a usable charger id (1 to 64 characters, no spaces or slashes)")
            continue
        if charger_id in merged:
            errors.append(f"{name}: charger {charger_id} is listed twice")
            continue
        if item.password:
            if len(item.password) < MIN_PASSWORD:
                errors.append(f"{name}: the password for {charger_id} is too short (at least {MIN_PASSWORD} characters)")
                continue
            if len(item.password) < RECOMMENDED_PASSWORD:
                warnings.append(f"{name}: the password for {charger_id} is shorter than {RECOMMENDED_PASSWORD} characters")
            merged[charger_id] = {"password_hash": hash_password(item.password)}
        elif charger_id in existing:
            kept = existing[charger_id]
            merged[charger_id] = kept
            if "password_hash" not in kept:
                warnings.append(f"{name}: the password for {charger_id} is stored as plaintext; set it again to store a hash")
        else:
            errors.append(f"{name}: charger {charger_id} needs a password")
    return merged


def build_org(inp: AdminOrgInput, existing: Optional[Dict[str, Any]], errors: List[str], warnings: List[str]) -> Dict[str, Any]:
    """The organization as it is to be written: what the file has, with the settings of ``inp`` put over it."""
    org = copy.deepcopy(existing) if existing else {}
    name = inp.name
    org["name"] = name
    org["connect_to_backend"] = inp.connect_to_backend

    subprotocol = inp.ocpp_subprotocol or "ocpp1.6"
    if inp.ocpp_subprotocol:
        org["ocpp_subprotocol"] = inp.ocpp_subprotocol
    else:
        org.pop("ocpp_subprotocol", None)

    previous = [b for b in (existing or {}).get("backends") or [] if isinstance(b, dict)]
    org["backends"] = [_backend_entry(b, subprotocol, _match_backend(previous, b)) for b in inp.backends]
    if not org["backends"]:
        org.pop("backends")  # nothing to say: the broker's default is no backends

    for key in DEFAULTS:
        value = getattr(inp, key)
        if value is None:
            org.pop(key, None)
        else:
            org[key] = value

    for key in ("leader_failback", "leader_failback_delay"):
        value = getattr(inp, key)
        if value is None:
            org.pop(key, None)
        else:
            org[key] = value

    tx = {k: getattr(inp.transaction_ids, k) for k in TX_KEYS if getattr(inp.transaction_ids, k) is not None}
    kept_tx = {k: v for k, v in ((existing or {}).get("transaction_ids") or {}).items() if k not in TX_KEYS}  # unknown ones, kept
    tx.update(kept_tx)
    if tx:
        org["transaction_ids"] = tx
    else:
        org.pop("transaction_ids", None)

    auth = dict(org.get("charger_auth") or {})
    credentials = _merge_credentials(name, _credentials(org), inp.credentials, errors, warnings)
    if credentials:
        auth["credentials"] = credentials
    else:
        auth.pop("credentials", None)
    if inp.charger_auth_required is None:
        auth.pop("required", None)
    else:
        auth["required"] = inp.charger_auth_required
    if auth:
        org["charger_auth"] = auth
    else:
        org.pop("charger_auth", None)
    return org


# ---------------------------------------------------------------------------
# What a change does, in words
# ---------------------------------------------------------------------------
def _backend_label(backend: AdminBackend) -> str:
    return "this broker" if backend.local else (backend.id or backend.url or "backend")


def _backend_key(backend: AdminBackend) -> str:
    return backend.id or ("local" if backend.local else backend.url or "")


def _backend_line(backend: AdminBackend) -> str:
    detail = []
    if backend.url:
        detail.append(backend.url)
    if backend.leader:
        detail.append("leader")
    elif backend.local and backend.leader is False:
        detail.append("standby")
    text = _backend_label(backend)
    return f"{text} ({', '.join(detail)})" if detail else text


def describe(before: Optional[AdminOrg], after: Optional[AdminOrg], before_raw: Dict[str, Any], after_raw: Dict[str, Any]) -> List[str]:
    """What differs between two versions of an organization. Passwords and hashes are never part of it."""
    if before is None and after is not None:
        return [
            f"connect_to_backend: {str(after.connect_to_backend).lower()}",
            *[f"+ backend {_backend_line(b)}" for b in after.backends],
            *[f"+ credential for {c.charger_id}" for c in after.credentials],
        ]
    if before is None or after is None:
        return []
    lines: List[str] = []
    for name, old, new in (
        ("connect_to_backend", before.connect_to_backend, after.connect_to_backend),
        ("ocpp_subprotocol", before.ocpp_subprotocol, after.ocpp_subprotocol),
        ("backend_buffer_size", before.backend_buffer_size, after.backend_buffer_size),
        ("backend_outage_timeout", before.backend_outage_timeout, after.backend_outage_timeout),
        ("leader_failover_timeout", before.leader_failover_timeout, after.leader_failover_timeout),
        ("leader_failback", before.leader_failback, after.leader_failback),
        ("leader_failback_delay", before.leader_failback_delay, after.leader_failback_delay),
        ("charger_auth_required", before.charger_auth_required, after.charger_auth_required),
    ):
        if old != new:
            lines.append(f"{name}: {str(old).lower() if isinstance(old, bool) else old} → {str(new).lower() if isinstance(new, bool) else new}")
    for key in TX_KEYS:
        old, new = getattr(before.transaction_ids, key), getattr(after.transaction_ids, key)
        if old != new:
            lines.append(f"transaction_ids.{key}: {'default' if old is None else old} → {'default' if new is None else new}")

    old_backends = {_backend_key(b): b for b in before.backends}
    new_backends = {_backend_key(b): b for b in after.backends}
    for key, backend in new_backends.items():
        if key not in old_backends:
            lines.append(f"+ backend {_backend_line(backend)}")
    for key, backend in old_backends.items():
        if key not in new_backends:
            lines.append(f"- backend {_backend_line(backend)}")
    for key, backend in new_backends.items():
        was = old_backends.get(key)
        if was is not None and was != backend:
            lines.append(f"~ backend {_backend_label(backend)}: {_backend_line(was)} → {_backend_line(backend)}")

    old_credentials, new_credentials = _credentials(before_raw), _credentials(after_raw)
    for charger_id in sorted(new_credentials):
        if charger_id not in old_credentials:
            lines.append(f"+ credential for {charger_id}")
        elif new_credentials[charger_id] != old_credentials[charger_id]:
            lines.append(f"~ credential for {charger_id}: password replaced")
    for charger_id in sorted(old_credentials):
        if charger_id not in new_credentials:
            lines.append(f"- credential for {charger_id}")
    return lines


# ---------------------------------------------------------------------------
# Applying a list of changes
# ---------------------------------------------------------------------------
def apply_changes(organizations: List[Dict[str, Any]], changes: List[AdminChange], runtime_of: Any) -> EditResult:
    """
    The organizations after ``changes`` (the given list is not changed), what they change, and anything wrong
    with them. ``runtime_of(raw_org)`` returns the organization as the broker would normalize it (or None).
    """
    result = EditResult(organizations=copy.deepcopy(organizations))
    for change in changes:
        by_name = {o.get("name"): i for i, o in enumerate(result.organizations)}
        if change.op == "delete":
            name = change.name or ""
            if name not in by_name:
                result.errors.append(f"There is no organization named {name!r} to remove")
                continue
            removed = result.organizations.pop(by_name[name])
            result.changes.append(AdminChangeLine(org=name, kind="removed", lines=[f"organization removed with its {len(_credentials(removed))} credential(s) and {len(removed.get('tags') or [])} tag(s) in the file"]))
            continue
        if change.org is None:
            result.errors.append("An upsert needs an organization")
            continue
        inp = change.org
        index = by_name.get(inp.name)
        if index is None and not NAME_PATTERN.match(inp.name):
            result.errors.append(f"{inp.name!r} is not a usable organization name (1 to 64 letters, digits, dots, dashes or underscores): it is part of the address chargers connect to")
            continue
        existing = result.organizations[index] if index is not None else None
        new_raw = build_org(inp, existing, result.errors, result.warnings)
        before = view(existing, runtime_of(existing)) if existing is not None else None
        after = view(new_raw, runtime_of(new_raw))
        lines = describe(before, after, existing or {}, new_raw)
        if index is None:
            result.organizations.append(new_raw)
            result.changes.append(AdminChangeLine(org=inp.name, kind="added", lines=lines))
        else:
            result.organizations[index] = new_raw
            if lines:
                result.changes.append(AdminChangeLine(org=inp.name, kind="changed", lines=lines))
    return result
