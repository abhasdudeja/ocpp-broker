"""
Models of the admin API (``/api/admin``): organizations, their backends and charger credentials.

Passwords only ever go in (``AdminCredentialInput.password``); nothing the broker returns holds a password or a
hash. A credential is listed by charger id and how it is stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AdminBackend(BaseModel):
    id: Optional[str] = Field(None, description="Names the backend in the transaction id table; set it when there are several")
    url: Optional[str] = Field(None, description="The backend's WebSocket address; none for the local backend (this broker)")
    leader: Optional[bool] = Field(None, description="Marked as the leader. With a local backend, false makes it a standby that takes over")
    local: bool = Field(False, description="This broker itself acts as the backend")
    ocpp_subprotocol: Optional[str] = Field(None, description="Defaults to the organization's")


class AdminTransactionIds(BaseModel):
    mapping: Optional[bool] = None
    follower_wait: Optional[float] = Field(default=None, gt=0, description="Seconds")
    dedupe_start: Optional[bool] = None
    retain_closed: Optional[float] = Field(default=None, gt=0, description="Seconds")
    retain_open: Optional[float] = Field(default=None, gt=0, description="Seconds")


class AdminCredential(BaseModel):
    charger_id: str
    storage: Literal["hash", "plaintext"] = Field(description="How the broker holds the password; plaintext should be replaced")


class AdminOrg(BaseModel):
    """An organization as the broker reads it (defaults filled in). Settings the console does not edit, such as tags, are kept as they are."""

    name: str
    connect_to_backend: bool
    ocpp_subprotocol: str
    backends: List[AdminBackend]
    backend_buffer_size: int
    backend_outage_timeout: int
    leader_failover_timeout: int
    leader_failback: bool = Field(description="Give the charger back to the configured leader after a failover")
    leader_failback_delay: float = Field(description="Seconds the configured leader must stay connected before that")
    transaction_ids: AdminTransactionIds
    charger_auth_required: bool
    credentials: List[AdminCredential]
    tags: int = Field(description="Tags in the file for this organization (edited on the Tags page, not here)")


class AdminConfig(BaseModel):
    store: Literal["file", "mongodb"] = Field(description="Where the organizations live: the configuration file, or MongoDB (admin.store)")
    path: str
    revision: str = Field(description="Names the file as it is; changes must say which revision they were made from")
    modified: datetime
    writable: bool
    writable_reason: Optional[str]
    keeps_backups: int
    organizations: List[AdminOrg]


class AdminCredentialInput(BaseModel):
    charger_id: str
    password: Optional[str] = Field(None, repr=False, description="Set or replace the password; leave out to keep the one the broker has")


class AdminOrgInput(BaseModel):
    name: str
    connect_to_backend: bool = True
    ocpp_subprotocol: Optional[str] = None
    backends: List[AdminBackend] = Field(default_factory=list)
    backend_buffer_size: Optional[int] = Field(default=None, ge=0)
    backend_outage_timeout: Optional[int] = Field(default=None, ge=0)
    leader_failover_timeout: Optional[int] = Field(default=None, ge=0)
    leader_failback: Optional[bool] = None
    leader_failback_delay: Optional[float] = Field(default=None, gt=0, description="Seconds")
    transaction_ids: AdminTransactionIds = Field(default_factory=lambda: AdminTransactionIds(mapping=None, follower_wait=None, dedupe_start=None, retain_closed=None, retain_open=None))
    charger_auth_required: Optional[bool] = Field(None, description="Chargers must send credentials; by default they must when any are listed")
    credentials: List[AdminCredentialInput] = Field(default_factory=list, description="Every charger that may connect. One left out is removed.")


class AdminChange(BaseModel):
    op: Literal["upsert", "delete"]
    org: Optional[AdminOrgInput] = Field(None, description="For upsert: the organization to add, or the new settings of the one with that name")
    name: Optional[str] = Field(None, description="For delete: the organization to remove")


class AdminRequest(BaseModel):
    revision: str = Field(description="The revision of the configuration these changes were made from")
    changes: List[AdminChange] = Field(min_length=1, max_length=50)


class AdminApplyRequest(AdminRequest):
    drop_connections: List[str] = Field(
        default_factory=list,
        description="Organizations whose connected chargers are disconnected now (they reconnect and get the new settings)",
    )


class AdminChangeLine(BaseModel):
    org: str
    kind: Literal["added", "removed", "changed"]
    lines: List[str] = Field(description="What changes, never a password or a hash")


class AdminPlan(BaseModel):
    ok: bool
    errors: List[str]
    warnings: List[str]
    changes: List[AdminChangeLine]
    conflict: bool = Field(description="The configuration file is not the revision the changes were made from")
    current_revision: str
    connected_chargers: Dict[str, int] = Field(description="Chargers connected now, for each organization the changes touch")


class AdminApplied(BaseModel):
    revision: str
    backup: Optional[str] = Field(description="The file as it was before, kept next to it")
    changes: List[AdminChangeLine]
    warnings: List[str]
    dropped_connections: int
    applied_at: datetime


class AuditEntry(BaseModel):
    time: datetime
    action: str
    outcome: Literal["applied", "refused", "failed"]
    source: Optional[str] = Field(description="The address the call came from, as the broker sees it")
    key_label: Optional[str] = Field(description="The label of the API key the call used")
    organizations: List[str]
    summary: List[str]
    detail: Optional[str] = Field(description="Why a change was refused or failed")
    revision: Optional[str] = Field(description="The revision of the file after the change")


class AuditLogPage(BaseModel):
    entries: List[AuditEntry] = Field(description="Newest first")
    persisted_to: Optional[str] = Field(description="The file the log is kept in")
