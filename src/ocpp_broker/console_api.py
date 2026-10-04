"""
Read-only endpoints for the web console: organizations, connected chargers, and one charger in detail
(who its leader and followers are, whether the links are up, what is buffered, which transactions are
running and how each backend numbers them).

Everything is read from this process's live sessions; nothing here changes broker state.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Query

from .backend_manager import BackendConnection
from .schemas.console import (
    BackendLink,
    BootInfo,
    ChargerDetail,
    ChargerList,
    ChargerSummary,
    ConnectorInfo,
    IdObject,
    OrgBackend,
    OrgSummary,
    TransactionRow,
)
from .session import ChargerSession, SessionMode
from .transaction_ids import backend_keys, table_for_org

BROKER_KEY = "broker"


def _version(subprotocol: str) -> str:
    """'ocpp1.6' -> '1.6'."""
    return subprotocol[4:] if subprotocol.lower().startswith("ocpp") else subprotocol


def _org_entries(broker: Any) -> List[Dict[str, Any]]:
    return list((getattr(broker, "config_data", None) or {}).get("organizations") or [])


def _mode(entry: Dict[str, Any]) -> str:
    return "relay" if entry.get("connect_to_backend", True) else "broker"


def _link(conn: BackendConnection, role: Literal["leader", "follower"]) -> BackendLink:
    down = conn.disconnected_since
    ready = conn.is_ready()
    return BackendLink(
        key=conn.key,
        url=conn.url,
        role=role,
        local=False,
        connected=ready,
        buffered_frames=conn.buffered_count if role == "leader" else 0,
        down_for_seconds=None if ready or down is None else round(max(0.0, time.monotonic() - down), 1),
    )


def _backends(session: ChargerSession) -> List[BackendLink]:
    """The leader first, then the followers. In broker mode the leader is the broker itself."""
    if session.mode is SessionMode.BROKER or session.backend_conn is None:
        return [
            BackendLink(
                key=BROKER_KEY, url=None, role="leader", local=True, connected=True, buffered_frames=0, down_for_seconds=None
            )
        ]
    return [_link(session.backend_conn, "leader"), *(_link(f, "follower") for f in session.follower_conns)]


def _summary(org: str, charger_id: str, session: ChargerSession) -> ChargerSummary:
    state = session.state
    links = _backends(session)
    leader, followers = links[0], links[1:]
    rows = session._ids.snapshot() if session._ids is not None else []
    running = [r for r in rows if r["state"] in ("open", "pending") and r["transaction_id"] is not None]
    boot = state.boot
    return ChargerSummary(
        org=org,
        charger_id=charger_id,
        mode=session.mode.value,
        ocpp_version=_version(session.org_entry.get("ocpp_subprotocol", "ocpp1.6")),
        connected_at=state.connected_at,
        last_seen=state.last_seen,
        remote_address=state.remote_address,
        vendor=boot.vendor if boot else None,
        model=boot.model if boot else None,
        firmware_version=boot.firmware_version if boot else None,
        connector_statuses={str(c.connector_id): c.status for c in state.ordered_connectors()},
        leader=leader,
        followers_total=len(followers),
        followers_connected=sum(1 for f in followers if f.connected),
        buffered_frames=leader.buffered_frames,
        open_transactions=len(running),
        degraded_transactions=sum(1 for r in running if r["degraded"]),
    )


def create_console_api(broker: Any) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["Console"])

    @router.get("/orgs", response_model=List[OrgSummary])
    async def list_orgs() -> List[OrgSummary]:
        connected: Dict[str, int] = {}
        for org, _ in list(broker.sessions):
            connected[org] = connected.get(org, 0) + 1
        result = []
        for entry in _org_entries(broker):
            backends = entry.get("backends") or []
            keys = backend_keys(backends)
            result.append(
                OrgSummary(
                    name=entry["name"],
                    mode=_mode(entry),  # type: ignore[arg-type]
                    ocpp_version=_version(entry.get("ocpp_subprotocol", "ocpp1.6")),
                    charger_auth_required=bool((entry.get("charger_auth") or {}).get("required")),
                    connected_chargers=connected.get(entry["name"], 0),
                    backends=[
                        OrgBackend(
                            key=key,
                            url=str(b.get("url", "")),
                            leader=bool(b.get("leader")),
                            ocpp_subprotocol=str(b.get("ocpp_subprotocol", entry.get("ocpp_subprotocol", "ocpp1.6"))),
                        )
                        for key, b in zip(keys, backends)
                    ]
                    if _mode(entry) == "relay"
                    else [],
                    transaction_id_mapping=_mode(entry) == "relay" and table_for_org(entry) is not None,
                )
            )
        return result

    @router.get("/chargers", response_model=ChargerList)
    async def list_chargers(
        org: Optional[str] = Query(None, description="Only this organization"),
        q: Optional[str] = Query(None, description="Only charger ids containing this text (case-insensitive)"),
    ) -> ChargerList:
        needle = q.lower() if q else None
        found = [
            _summary(o, cid, session)
            for (o, cid), session in sorted(list(broker.sessions.items()))
            if (org is None or o == org) and (needle is None or needle in cid.lower())
        ]
        return ChargerList(chargers=found, total=len(found))

    @router.get("/chargers/{org}/{charger_id}", response_model=ChargerDetail)
    async def charger_detail(
        org: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger id"),
    ) -> ChargerDetail:
        session = broker.sessions.get((org, charger_id))
        if session is None:
            raise HTTPException(status_code=404, detail=f"Charger {org}/{charger_id} is not connected to this instance")
        table = session._ids
        spaces = table.spaces_snapshot() if table is not None else {}
        state = session.state
        links = _backends(session)
        summary = _summary(org, charger_id, session)
        boot = state.boot
        return ChargerDetail(
            **summary.model_dump(),
            boot=BootInfo(**vars(boot)) if boot else None,
            last_heartbeat_at=state.last_heartbeat_at,
            connectors=[ConnectorInfo(**vars(c)) for c in state.ordered_connectors()],
            backends=links,
            transaction_id_mapping=table is not None,
            transactions=[TransactionRow(**row) for row in (table.snapshot() if table is not None else [])],
            reservations=[IdObject(**row) for row in spaces.get("reservation", [])],
            charging_profiles=[IdObject(**row) for row in spaces.get("profile", [])],
            frames_in=state.frames_in,
            frames_out=state.frames_out,
            id_table_stats=dict(table.stats) if table is not None else {},
        )

    return router
