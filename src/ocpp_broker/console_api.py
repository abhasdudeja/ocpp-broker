"""
Read-only endpoints for the web console: organizations, connected chargers, and one charger in detail
(who its leader and followers are, whether the links are up, what is buffered, which transactions are
running and how each backend numbers them).

Everything is read from this process's live sessions; nothing here changes broker state.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Query, Request

from .schemas.console import (
    BackendLink,
    BootInfo,
    ChargerDetail,
    ChargerList,
    ChargerSummary,
    CommandHistory,
    CommandLogEntry,
    ConnectorInfo,
    IdObject,
    LeaderChanged,
    LeaderRequest,
    BackendStat,
    OfflineCharger,
    OfflineChargerList,
    OrgBackend,
    OrgBackends,
    OrgSummary,
    TransactionRow,
)
from .session import ChargerSession, SessionMode
from .local_backend import LocalBackend
from .transaction_ids import backend_keys, leader_index, local_index, table_for_org

logger = logging.getLogger("ocpp_broker.console_api")

BROKER_KEY = "broker"
OFFLINE_LIMIT = 500


def _version(subprotocol: str) -> str:
    """'ocpp1.6' -> '1.6'."""
    return subprotocol[4:] if subprotocol.lower().startswith("ocpp") else subprotocol


def _org_entries(broker: Any) -> List[Dict[str, Any]]:
    return list((getattr(broker, "config_data", None) or {}).get("organizations") or [])


def _local(entry: Dict[str, Any]) -> bool:
    """The organization has a backend that is this broker itself."""
    return bool(entry.get("connect_to_backend", True)) and local_index(entry.get("backends") or []) is not None


def _when(value: Any) -> Optional[datetime]:
    """A time read back from MongoDB, which hands out naive UTC datetimes: say it is UTC."""
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _offline(doc: Dict[str, Any]) -> OfflineCharger:
    return OfflineCharger(
        org=str(doc.get("org_name", "")),
        charger_id=str(doc.get("charger_id", "")),
        mode=doc.get("mode"),
        vendor=doc.get("vendor"),
        model=doc.get("model"),
        firmware_version=doc.get("firmware_version"),
        remote_address=doc.get("remote_address"),
        last_connected_at=_when(doc.get("last_connected_at")),
        last_disconnected_at=_when(doc.get("last_disconnected_at")),
        last_seen_at=_when(doc.get("last_seen_at")),
        last_boot_at=_when(doc.get("last_boot_at")),
    )


def _mode(entry: Dict[str, Any]) -> str:
    """broker: the broker answers the chargers (a local leader); relay: a backend does (a local standby may take over)."""
    backends = entry.get("backends") or []
    local = local_index(backends) if entry.get("connect_to_backend", True) else None
    local_leads = local is not None and leader_index(backends) == local
    return "relay" if entry.get("connect_to_backend", True) and not local_leads else "broker"


def _link(conn: Any, role: Literal["leader", "follower"], configured: bool) -> BackendLink:
    if isinstance(conn, LocalBackend):  # this broker itself
        return BackendLink(
            key=conn.key, url=None, role=role, local=True, connected=conn.is_ready(), buffered_frames=0, down_for_seconds=None,
            configured_leader=configured,
        )
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
        configured_leader=configured,
    )


def _backends(session: ChargerSession) -> List[BackendLink]:
    """The leader first, then the followers. In plain broker mode the leader is the broker itself."""
    if session.backend_conn is None:
        local = BackendLink(
            key=session.local_key or BROKER_KEY, url=None, role="leader", local=True, connected=True, buffered_frames=0, down_for_seconds=None,
            configured_leader=True,
        )
        return [local, *(_link(f, "follower", False) for f in session.follower_conns)]
    preferred = session.preferred
    return [_link(session.backend_conn, "leader", session.backend_conn is preferred), *(_link(f, "follower", f is preferred) for f in session.follower_conns)]


def _transaction_rows(broker: Any, org: str, charger_id: str, session: ChargerSession) -> List[Dict[str, Any]]:
    """
    The charger's transactions: from the id table when there is one (it knows each backend's own id),
    otherwise, when the broker itself answers, from what it numbered.
    """
    if session._ids is not None:
        return session._ids.snapshot()
    local = getattr(broker, "local_transactions", {}).get((org, charger_id))
    if local is None or session.mode is not SessionMode.BROKER:
        return []
    key = session.local_key or BROKER_KEY
    return [
        {"transaction_id": row["transaction_id"], "state": row["state"], "backend_ids": {key: row["transaction_id"]}, "degraded": [], "awaiting": []}
        for row in local.snapshot()
    ]


def _summary(broker: Any, org: str, charger_id: str, session: ChargerSession) -> ChargerSummary:
    state = session.state
    links = _backends(session)
    leader, followers = links[0], links[1:]
    rows = _transaction_rows(broker, org, charger_id, session)
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
                            url=None if b.get("local") else str(b.get("url", "")),
                            local=bool(b.get("local")),
                            leader=bool(b.get("leader")),
                            ocpp_subprotocol=str(b.get("ocpp_subprotocol", entry.get("ocpp_subprotocol", "ocpp1.6"))),
                        )
                        for key, b in zip(keys, backends)
                    ]
                    if _mode(entry) == "relay" or _local(entry)
                    else [],
                    transaction_id_mapping=(_mode(entry) == "relay" or _local(entry)) and table_for_org(entry) is not None,
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
            _summary(broker, o, cid, session)
            for (o, cid), session in sorted(list(broker.sessions.items()))
            if (org is None or o == org) and (needle is None or needle in cid.lower())
        ]
        return ChargerList(chargers=found, total=len(found))

    @router.get("/chargers/offline", response_model=OfflineChargerList)
    async def offline_chargers(
        org: Optional[str] = Query(None, description="Only this organization"),
        q: Optional[str] = Query(None, description="Only charger ids containing this text (case-insensitive)"),
    ) -> OfflineChargerList:
        """
        Chargers this broker has seen (kept in MongoDB, written when a charger connects, boots and
        disconnects) that are not connected to this instance now. Without MongoDB nothing is remembered:
        the answer is then `available: false` with the reason.
        """
        mongodb = getattr(broker, "mongodb_service", None)
        if mongodb is None:
            return OfflineChargerList(available=False, reason="MongoDB is not configured, so chargers that are not connected are not remembered", chargers=[], total=0)
        if not mongodb.is_connected():
            return OfflineChargerList(available=False, reason="MongoDB is not connected", chargers=[], total=0)
        try:
            documents = await mongodb.list_presence(org)
        except Exception as exc:
            return OfflineChargerList(available=False, reason=f"MongoDB did not answer: {exc}", chargers=[], total=0)
        needle = q.lower() if q else None
        online = set(broker.sessions)
        found = [
            _offline(doc)
            for doc in documents
            if (doc.get("org_name"), doc.get("charger_id")) not in online and (needle is None or needle in str(doc.get("charger_id", "")).lower())
        ]
        found.sort(key=lambda c: c.last_seen_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return OfflineChargerList(available=True, reason=None, chargers=found[:OFFLINE_LIMIT], total=len(found))

    @router.get("/backends", response_model=List[OrgBackends])
    async def backends(org: Optional[str] = Query(None, description="Only this organization")) -> List[OrgBackends]:
        """Each backend of each organization where the broker has backends, with how its links stand across the connected chargers."""
        result: List[OrgBackends] = []
        for entry in _org_entries(broker):
            name = entry["name"]
            configured = entry.get("backends") or []
            if (org is not None and name != org) or not configured or not (_mode(entry) == "relay" or _local(entry)):
                continue
            keys = backend_keys(configured)
            stats = {
                key: BackendStat(
                    key=key,
                    url=None if b.get("local") else str(b.get("url", "")),
                    local=bool(b.get("local")),
                    configured_leader=bool(b.get("leader")),
                    leading=0,
                    following=0,
                    links_up=0,
                    links_down=0,
                    buffered_frames=0,
                    down_chargers=[],
                )
                for key, b in zip(keys, configured)
            }
            sessions = [(cid, s) for (o, cid), s in sorted(list(broker.sessions.items())) if o == name]
            for cid, session in sessions:
                for link in _backends(session):
                    stat = stats.get(link.key)
                    if stat is None:
                        continue
                    if link.role == "leader":
                        stat.leading += 1
                    else:
                        stat.following += 1
                    if link.connected:
                        stat.links_up += 1
                    else:
                        stat.links_down += 1
                        if len(stat.down_chargers) < 20:
                            stat.down_chargers.append(cid)
                    stat.buffered_frames += link.buffered_frames
            result.append(OrgBackends(org=name, mode=_mode(entry), chargers=len(sessions), backends=list(stats.values())))  # type: ignore[arg-type]
        return result

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
        summary = _summary(broker, org, charger_id, session)
        boot = state.boot
        return ChargerDetail(
            **summary.model_dump(),
            boot=BootInfo(**vars(boot)) if boot else None,
            last_heartbeat_at=state.last_heartbeat_at,
            connectors=[ConnectorInfo(**vars(c)) for c in state.ordered_connectors()],
            backends=links,
            transaction_id_mapping=table is not None,
            transactions=[TransactionRow(**row) for row in _transaction_rows(broker, org, charger_id, session)],
            reservations=[IdObject(**row) for row in spaces.get("reservation", [])],
            charging_profiles=[IdObject(**row) for row in spaces.get("profile", [])],
            frames_in=state.frames_in,
            frames_out=state.frames_out,
            id_table_stats=dict(table.stats) if table is not None else {},
        )

    @router.post("/chargers/{org}/{charger_id}/leader", response_model=LeaderChanged)
    async def change_leader(
        request: Request,
        body: LeaderRequest,
        org: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger id"),
    ) -> LeaderChanged:
        """
        Make a connected follower this charger's leader now. Messages the old leader has not answered yet may time
        out at the charger, which then retries them with the new leader. Nothing is written to the configuration:
        the charger goes back to the configured leader when it reconnects (or by `leader_failback`, only after a failover).
        """
        session = broker.sessions.get((org, charger_id))
        if session is None or session.backend_conn is None:
            raise HTTPException(status_code=404, detail=f"Charger {org}/{charger_id} is not connected to this instance with backends")
        old = session.backend_conn.key
        try:
            session.promote_to(body.backend)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        logger.warning("Leader of %s/%s changed from %s to %s by %s", org, charger_id, old, body.backend, getattr(request.state, "api_key_label", None))
        return LeaderChanged(old_leader=old, new_leader=body.backend)

    @router.get("/chargers/{org}/{charger_id}/commands", response_model=CommandHistory)
    async def charger_commands(
        org: str = Path(..., description="Organization name"),
        charger_id: str = Path(..., description="Charger id"),
    ) -> CommandHistory:
        """The commands sent to this charger through the API or the console, newest first (kept while it stays connected)."""
        session = broker.sessions.get((org, charger_id))
        if session is None:
            raise HTTPException(status_code=404, detail=f"Charger {org}/{charger_id} is not connected to this instance")
        return CommandHistory(
            commands=[
                CommandLogEntry(
                    message_id=e.message_id,
                    action=e.action,
                    status=e.status,
                    payload=e.payload,
                    response=e.response,
                    error=e.error,
                    sent_at=e.sent_at,
                    finished_at=e.finished_at,
                    duration_ms=e.duration_ms,
                )
                for e in reversed(list(session.command_log))
            ]
        )

    return router
