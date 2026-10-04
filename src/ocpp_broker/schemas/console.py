"""
Response models of the console endpoints (``/api/orgs``, ``/api/chargers``).

They are declared so the OpenAPI schema describes every field and the web console's types are
generated from it. Names are version-neutral on purpose: ``ocpp_version`` is data, and a
connector can later gain an EVSE id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class OrgBackend(BaseModel):
    key: str = Field(description="The backend's id from the configuration, or its URL if it has none (\"broker\" for the local one)")
    url: Optional[str] = Field(description="Null for the local backend, which is this broker itself")
    local: bool = Field(description="This broker itself, answering the charger as its leader; the other backends only observe")
    leader: bool = Field(description="Marked leader in the configuration (a failover can change who leads a charger)")
    ocpp_subprotocol: str


class OrgSummary(BaseModel):
    name: str
    mode: Literal["broker", "relay"] = Field(
        description="broker: the broker answers chargers itself (with a local backend, other backends receive copies); relay: it forwards to backends"
    )
    ocpp_version: str
    charger_auth_required: bool
    connected_chargers: int = Field(description="Chargers of this organization connected to this instance")
    backends: List[OrgBackend] = Field(description="Empty in broker mode without followers")
    transaction_id_mapping: bool = Field(description="Transaction ids are translated per backend (relay mode with several backends)")


class BackendLink(BaseModel):
    """One backend as one charger's session sees it."""

    key: str
    url: Optional[str] = Field(description="Null for the broker itself")
    role: Literal["leader", "follower"]
    local: bool = Field(description="The broker itself is the backend (broker mode)")
    connected: bool
    buffered_frames: int = Field(description="Charger frames waiting for this backend (leader only)")
    down_for_seconds: Optional[float] = Field(description="How long the link has been down, if it is")


class ConnectorInfo(BaseModel):
    connector_id: int = Field(description="0 is the charger as a whole")
    status: str
    error_code: Optional[str]
    info: Optional[str]
    updated_at: datetime


class BootInfo(BaseModel):
    vendor: Optional[str]
    model: Optional[str]
    serial_number: Optional[str]
    firmware_version: Optional[str]
    iccid: Optional[str]
    imsi: Optional[str]
    meter_type: Optional[str]
    meter_serial_number: Optional[str]
    received_at: datetime


class TransactionRow(BaseModel):
    transaction_id: Optional[int] = Field(description="The id the charger holds; null until the leader has answered the start")
    state: Literal["pending", "open", "closed"]
    backend_ids: Dict[str, int] = Field(description="Each backend's own id for it, by backend key")
    degraded: List[str] = Field(description="Backends that never learned an id for it and are skipped")
    awaiting: List[str] = Field(description="Followers that have not yet said which id they issued")


class IdObject(BaseModel):
    """A reservation or charging profile the charger holds."""

    id: int = Field(description="The id the charger holds")
    backend_ids: Dict[str, int] = Field(description="Each backend's own id (empty if made through the REST API)")
    expires: Optional[float] = Field(description="Wall-clock seconds; reservations end at their expiry date")


class ChargerSummary(BaseModel):
    org: str
    charger_id: str
    online: bool = True
    mode: Literal["broker", "relay"]
    ocpp_version: str
    connected_at: datetime
    last_seen: Optional[datetime]
    remote_address: Optional[str]
    vendor: Optional[str]
    model: Optional[str]
    firmware_version: Optional[str]
    connector_statuses: Dict[str, str] = Field(description="Connector id (as text) to its last reported status")
    leader: BackendLink
    followers_total: int
    followers_connected: int
    buffered_frames: int
    open_transactions: int
    degraded_transactions: int = Field(description="Running transactions in which some backend is skipped")


class ChargerList(BaseModel):
    chargers: List[ChargerSummary]
    total: int


class ChargerDetail(ChargerSummary):
    boot: Optional[BootInfo]
    last_heartbeat_at: Optional[datetime]
    connectors: List[ConnectorInfo]
    backends: List[BackendLink] = Field(description="The leader first, then the followers")
    transaction_id_mapping: bool
    transactions: List[TransactionRow]
    reservations: List[IdObject]
    charging_profiles: List[IdObject]
    frames_in: int
    frames_out: int
    id_table_stats: Dict[str, int] = Field(description="Counters of what the transaction id table did: rewritten, remapped, skipped, ...")


class ConsoleEvent(BaseModel):
    """One event of ``GET /api/events``. ``data`` depends on ``type``; see the web console and events documentation."""

    id: int = Field(description="Increases by one per event within one run of the broker")
    type: Literal[
        "charger.connected",
        "charger.disconnected",
        "charger.replaced",
        "charger.boot",
        "charger.status",
        "backend.link",
        "backend.failover",
        "transaction.started",
        "transaction.stopped",
        "command.result",
    ]
    time: datetime
    org: Optional[str]
    charger_id: Optional[str]
    data: Dict[str, Any]


class CommandSpecInfo(BaseModel):
    action: str
    summary: str
    risk: Literal["read", "change", "disruptive"] = Field(
        description="read: only asks; change: alters settings or data; disruptive: can interrupt charging or restart the charger"
    )
    json_schema: Dict[str, Any] = Field(description="JSON Schema (draft 4) of the command's payload, as the ocpp library validates it")
    route: str = Field(description="The typed route for this command; the generic commands route accepts any of them too")


class CommandCatalog(BaseModel):
    ocpp_version: str
    commands: List[CommandSpecInfo]


class CommandLogEntry(BaseModel):
    message_id: str
    action: str
    status: Literal["pending", "success", "error", "timeout", "cancelled"]
    payload: Any = Field(description="What was sent; secret values (an AuthorizationKey) are replaced by ***")
    response: Any = Field(description="The charger's answer, if it gave one")
    error: Optional[str] = Field(description="A CallError, a timeout or a local failure, as text")
    sent_at: datetime
    finished_at: Optional[datetime]
    duration_ms: Optional[int]


class CommandHistory(BaseModel):
    commands: List[CommandLogEntry] = Field(description="Newest first; the last 50 commands sent to this charger while it stayed connected")


class OfflineCharger(BaseModel):
    """A charger this broker has seen before (remembered in MongoDB) that is not connected to this instance now."""

    org: str
    charger_id: str
    mode: Optional[str]
    vendor: Optional[str]
    model: Optional[str]
    firmware_version: Optional[str]
    remote_address: Optional[str]
    last_connected_at: Optional[datetime]
    last_disconnected_at: Optional[datetime]
    last_seen_at: Optional[datetime] = Field(description="Its last message before the connection ended (or when it connected)")
    last_boot_at: Optional[datetime]


class OfflineChargerList(BaseModel):
    available: bool = Field(description="False when chargers are not remembered: MongoDB is not configured or not answering")
    reason: Optional[str] = Field(description="Why it is not available")
    chargers: List[OfflineCharger] = Field(description="Most recently seen first")
    total: int


class BackendStat(BaseModel):
    """One configured backend, summed over the chargers connected to this instance."""

    key: str
    url: Optional[str] = Field(description="Null for the local backend (this broker itself)")
    local: bool
    configured_leader: bool
    leading: int = Field(description="Chargers for which it is the leader now (a failover can change this)")
    following: int = Field(description="Chargers for which it is a follower now")
    links_up: int
    links_down: int
    buffered_frames: int = Field(description="Charger messages held for it as a leader that is unreachable")
    down_chargers: List[str] = Field(description="Chargers whose link to it is down (at most 20)")


class OrgBackends(BaseModel):
    org: str
    mode: Literal["broker", "relay"]
    chargers: int = Field(description="Chargers of this organization connected to this instance")
    backends: List[BackendStat]
