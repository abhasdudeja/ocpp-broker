"""
The admin API (``/api/admin``): change an organization's settings, its backends and its chargers' credentials
while the broker runs. Off unless ``admin.enabled`` is true, because it lets whoever holds an API key rewrite the
configuration file and decide which chargers may connect.

A change is made in three steps the console shows: read the configuration (``GET /config``, which names its
revision), check what a set of changes would do (``POST /config/validate``: errors, warnings and what changes,
in words, with nothing written), then apply it (``POST /config/apply``). Applying writes the configuration file
atomically after keeping a copy of it, and the broker uses the new organizations from then on. A charger that is
already connected keeps the settings it connected with until it reconnects, unless its organization is named in
``drop_connections``.

See config_store.py (the file), admin_edit.py (what a change does) and audit.py (the log of changes).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse

from .admin_edit import EditResult, apply_changes, normalized, view
from .audit import AuditLog, describe_source
from .config_store import DEFAULT_KEEP_BACKUPS, Applied, Conflict, ConfigError, FileConfigStore, NotWritable, Plan
from .schemas.admin import (
    AdminApplied,
    AdminApplyRequest,
    AdminChangeLine,
    AdminConfig,
    AdminPlan,
    AdminRequest,
    AuditLogPage,
)

logger = logging.getLogger("ocpp_broker.admin_api")

DISABLED = "The admin API is disabled: set admin.enabled: true in the configuration to use it"
ACTION = "config.apply"


async def validation_error_without_input(request: Request, exc: Exception):
    """
    FastAPI's answer to a request it cannot read repeats the offending value, and for a missing field that value is
    the whole object around it, which here can hold a password. For the admin API say where and what, not the input.
    """
    assert isinstance(exc, RequestValidationError)
    if not request.url.path.startswith("/api/admin"):
        return await request_validation_exception_handler(request, exc)
    errors = [{key: value for key, value in error.items() if key not in ("input", "ctx", "url")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


def _flatten(changes: List[AdminChangeLine]) -> List[str]:
    return [f"{change.org}: {line}" for change in changes for line in change.lines]


def create_admin_api(broker: Any) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["Admin"])

    def settings() -> Dict[str, Any]:
        return (getattr(broker, "config_data", None) or {}).get("admin") or {}

    def guard() -> None:
        if not settings().get("enabled"):
            raise HTTPException(status_code=403, detail=DISABLED)

    def store() -> FileConfigStore:
        path = getattr(broker, "_cfg_path", None) or "config.yaml"
        keep = int(settings().get("keep_backups", DEFAULT_KEEP_BACKUPS))
        current: Optional[FileConfigStore] = getattr(broker, "admin_store", None)
        if current is None or current.path != os.path.abspath(path) or current.keep_backups != keep:
            current = FileConfigStore(path, keep)
            broker.admin_store = current
        return current

    def audit() -> AuditLog:
        path = settings().get("audit_log") or f"{store().path}.audit.jsonl"
        current: Optional[AuditLog] = getattr(broker, "audit", None)
        if current is None or current.path != path:
            current = AuditLog(path)
            broker.audit = current
        return current

    def connected(names: List[str]) -> Dict[str, int]:
        counts = {name: 0 for name in names}
        for (org_name, _), _session in list(broker.sessions.items()):
            if org_name in counts:
                counts[org_name] += 1
        return counts

    async def prepare(request: AdminRequest) -> tuple[EditResult, Plan]:
        """Work out what the changes would do, from the file as it is now. Nothing is written."""
        snapshot = await asyncio.to_thread(store().snapshot)
        # Hashing a password takes a moment: do it away from the event loop
        result = await asyncio.to_thread(apply_changes, snapshot.organizations, request.changes, normalized)
        plan = store().plan(request.revision, result.organizations)
        plan.errors = list(dict.fromkeys([*result.errors, *plan.errors]))
        plan.warnings = list(dict.fromkeys([*result.warnings, *plan.warnings]))
        return result, plan

    @router.get("/config", response_model=AdminConfig)
    async def read_config() -> AdminConfig:
        """The organizations in the configuration file, as the broker reads them, and the revision to base changes on."""
        guard()
        files = store()
        writable, reason = files.writable()
        try:
            snapshot = await asyncio.to_thread(files.snapshot)
        except NotWritable as exc:
            return AdminConfig(path=files.path, revision="", modified=datetime.now(timezone.utc), writable=False, writable_reason=reason or str(exc), keeps_backups=files.keep_backups, organizations=[])
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return AdminConfig(
            path=snapshot.path,
            revision=snapshot.revision,
            modified=snapshot.modified,
            writable=writable,
            writable_reason=reason,
            keeps_backups=files.keep_backups,
            organizations=[view(org, normalized(org)) for org in snapshot.organizations],
        )

    def plan_answer(result: EditResult, plan: Plan) -> AdminPlan:
        conflict = plan.base_revision != plan.current_revision
        return AdminPlan(
            ok=plan.ok,
            errors=plan.errors,
            warnings=plan.warnings,
            changes=result.changes,
            conflict=conflict,
            current_revision=plan.current_revision,
            connected_chargers=connected([change.org for change in result.changes]),
        )

    @router.post("/config/validate", response_model=AdminPlan)
    async def validate(request: AdminRequest) -> AdminPlan:
        """Say what the changes would do, and whether they could be applied, without writing anything."""
        guard()
        try:
            result, plan = await prepare(request)
        except NotWritable as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ConfigError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return plan_answer(result, plan)

    @router.post("/config/apply", response_model=AdminApplied)
    async def apply(body: AdminApplyRequest, request: Request) -> AdminApplied:
        """
        Write the changes to the configuration file (keeping a copy of it) and use the new organizations from now
        on. Refused with `409` if the file is not the revision the changes were made from, and `422` if they would
        not make a configuration the broker can use.
        """
        guard()
        log = audit()
        source = describe_source(request)
        label = getattr(request.state, "api_key_label", None)
        touched = [change.name or (change.org.name if change.org else "") for change in body.changes]

        def refuse(status: int, detail: str, summary: Optional[List[str]] = None) -> HTTPException:
            log.record(ACTION, "refused", source, label, touched, summary or [], detail=detail)
            return HTTPException(status_code=status, detail=detail)

        try:
            result, plan = await prepare(body)
        except ConfigError as exc:
            raise refuse(409, str(exc))
        unknown = sorted(set(body.drop_connections) - {change.org for change in result.changes})
        if unknown:
            raise refuse(422, f"drop_connections names organizations these changes do not touch: {', '.join(unknown)}")
        if plan.base_revision != plan.current_revision:
            raise refuse(409, "The configuration file has changed since this page loaded it. Reload, then make the change again.")
        if not plan.ok:
            raise refuse(422, "; ".join(plan.errors))

        try:
            applied: Applied = await store().apply(plan)
        except Conflict as exc:
            raise refuse(409, str(exc))
        except (NotWritable, ConfigError) as exc:
            log.record(ACTION, "failed", source, label, touched, _flatten(result.changes), detail=str(exc))
            raise HTTPException(status_code=409, detail=str(exc))

        broker.use_organizations(applied.runtime["organizations"])
        dropped = await broker.drop_connections(set(body.drop_connections)) if body.drop_connections else 0
        summary = _flatten(result.changes)
        if dropped:
            summary.append(f"disconnected {dropped} charger(s) of {', '.join(sorted(body.drop_connections))}")
        log.record(ACTION, "applied", source, label, [change.org for change in result.changes], summary, revision=applied.revision)
        return AdminApplied(
            revision=applied.revision,
            backup=applied.backup,
            changes=result.changes,
            warnings=plan.warnings,
            dropped_connections=dropped,
            applied_at=datetime.now(timezone.utc),
        )

    @router.get("/audit", response_model=AuditLogPage)
    async def read_audit(limit: int = Query(100, ge=1, le=500)) -> AuditLogPage:
        """What was changed through the admin API, newest first: when, from where, with which key label, and what."""
        guard()
        log = audit()
        return AuditLogPage(entries=log.recent(limit), persisted_to=log.path)

    return router
