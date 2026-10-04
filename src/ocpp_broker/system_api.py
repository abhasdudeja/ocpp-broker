"""
``GET /api/system/info``: what this broker process is and whether its parts are healthy.

It is the call the web console makes to check an API key, and the first thing
its dashboard shows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ._version import __version__
from .auth import configured_api_keys
from .ui import ui_built, ui_enabled
from .write_behind import WriteBehind


class MongoInfo(BaseModel):
    configured: bool = Field(description="MongoDB is enabled and a service object exists")
    connected: bool = Field(description="The connection made at startup succeeded")
    reachable: bool = Field(description="The server answered a ping just now")
    database: Optional[str] = None
    pending_writes: int = Field(description="Records waiting to be written to MongoDB (they are written after the charger has been answered)")
    written: int = Field(description="Records written since the broker started")
    failed_writes: int = Field(description="Write attempts that failed or timed out (a timed-out write is tried again)")
    dropped_writes: int = Field(description="Records thrown away because too many were waiting; not stored")
    writes_degraded: bool = Field(description="Recent writes keep failing: MongoDB looks to be down or unreachable")


class SystemInfo(BaseModel):
    version: str
    instance_id: str = Field(description="Random per process; differs between instances and restarts")
    started_at: datetime
    uptime_seconds: float
    api_auth: Literal["api_key", "none"] = Field(
        description="'none' only when security.allow_unauthenticated_api is true"
    )
    ui_enabled: bool
    ui_built: bool = Field(description="The console files are present in this installation")
    admin_enabled: bool = Field(description="The admin API (admin.enabled) may change organizations and credentials")
    organizations: int = Field(description="Organizations in the configuration")
    connected_chargers: int = Field(description="Chargers connected to this instance (sessions are per process)")
    mongodb: MongoInfo


def create_system_api(broker) -> APIRouter:
    router = APIRouter(prefix="/api/system", tags=["System"])

    @router.get("/info", response_model=SystemInfo)
    async def system_info() -> SystemInfo:
        config = getattr(broker, "config_data", None) or {}
        mongodb = getattr(broker, "mongodb_service", None)
        now = datetime.now(timezone.utc)
        writes = getattr(broker, "writes", None)
        counts = writes.stats() if isinstance(writes, WriteBehind) else {}
        return SystemInfo(
            version=__version__,
            instance_id=broker.instance_id,
            started_at=broker.started_at,
            uptime_seconds=round((now - broker.started_at).total_seconds(), 1),
            api_auth="api_key" if configured_api_keys(config) else "none",
            ui_enabled=ui_enabled(broker),
            ui_built=ui_built(),
            admin_enabled=bool((config.get("admin") or {}).get("enabled")),
            organizations=len(config.get("organizations", [])),
            connected_chargers=len(broker.sessions),
            mongodb=MongoInfo(
                configured=mongodb is not None,
                connected=bool(mongodb and mongodb.is_connected()),
                reachable=bool(mongodb and await mongodb.ping()),
                database=mongodb.database_name if mongodb else None,
                pending_writes=counts.get("pending", 0),
                written=counts.get("written", 0),
                failed_writes=counts.get("failed", 0),
                dropped_writes=counts.get("dropped", 0),
                writes_degraded=bool(counts.get("degraded", False)),
            ),
        )

    return router
