"""
Response models of the console endpoints (``/api/orgs``, ``/api/chargers``).

They are declared so the OpenAPI schema describes every field and the web console's types are
generated from it. Names are version-neutral on purpose: ``ocpp_version`` is data, and a
connector can later gain an EVSE id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class OrgBackend(BaseModel):
    key: str = Field(description="The backend's id from the configuration, or its URL if it has none")
    url: str
    leader: bool = Field(description="Marked leader in the configuration (a failover can change who leads a charger)")
    ocpp_subprotocol: str


class OrgSummary(BaseModel):
    name: str
    mode: Literal["broker", "relay"] = Field(description="broker: the broker answers chargers itself; relay: it forwards to backends")
    ocpp_version: str
    charger_auth_required: bool
    connected_chargers: int = Field(description="Chargers of this organization connected to this instance")
    backends: List[OrgBackend] = Field(description="Empty in broker mode")
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
