"""
Response models of the history endpoints (``/api/history``). Every list answers ``available: false`` with a
reason when MongoDB is not configured, not connected or not answering, so the web console can say so
instead of showing an error; ``items`` is then empty.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class HistoryInfo(BaseModel):
    available: bool
    reason: Optional[str] = Field(description="Why history is not available, when it is not")
    messages_enabled: bool = Field(description="Every OCPP frame is being recorded (mongodb.history.messages)")
    heartbeats_enabled: bool = Field(description="Heartbeats are recorded too, with the message log on")
    retention_days: Dict[str, Optional[int]] = Field(description="Days each kind of record is kept; null: for ever")
    counts: Dict[str, int] = Field(description="Roughly how many records each collection holds")


class _Page(BaseModel):
    available: bool
    reason: Optional[str]
    next_cursor: Optional[str] = Field(description="Pass it as `cursor` for the next (older) page; null on the last page")


class HistoryTransaction(BaseModel):
    org: str
    charger_id: str
    transaction_id: int
    connector_id: Optional[int]
    id_tag: Optional[str]
    started_at: Optional[datetime]
    stopped_at: Optional[datetime]
    meter_start: Optional[int] = Field(description="Wh")
    meter_stop: Optional[int] = Field(description="Wh")
    energy_wh: Optional[int] = Field(description="meter_stop - meter_start, when both are known")
    stop_reason: Optional[str]
    stop_id_tag: Optional[str]
    open: bool = Field(description="No stop has been recorded")


class TransactionPage(_Page):
    items: List[HistoryTransaction]


class MeterReading(BaseModel):
    timestamp: Optional[datetime] = Field(description="When the meter was read (the charger's time), as the charger sent it")
    connector_id: Optional[int]
    transaction_id: Optional[int]
    measurand: str
    phase: Optional[str]
    unit: str
    context: Optional[str]
    location: Optional[str]
    value: Optional[float] = Field(description="Null when the charger sent something that is not a number")
    raw_value: Optional[str]


class MeterReadingPage(_Page):
    items: List[MeterReading]


class TransactionDetail(BaseModel):
    available: bool
    reason: Optional[str]
    transaction: Optional[HistoryTransaction]
    readings: List[MeterReading] = Field(description="Oldest first")
    readings_truncated: bool = Field(description="There are more readings than were returned")


class StatusRecord(BaseModel):
    org: str
    charger_id: str
    connector_id: Optional[int]
    status: str
    error_code: Optional[str]
    info: Optional[str]
    vendor_id: Optional[str]
    vendor_error_code: Optional[str]
    timestamp: Optional[datetime]


class StatusPage(_Page):
    items: List[StatusRecord]


class CommandRecord(BaseModel):
    org: str
    charger_id: str
    message_id: str
    action: str
    payload: Any = Field(description="As sent, with secrets such as an AuthorizationKey replaced by ***")
    status: Literal["success", "error", "timeout", "cancelled", "pending"]
    response: Any
    error: Optional[str]
    sent_at: Optional[datetime]
    finished_at: Optional[datetime]
    duration_ms: Optional[int]


class CommandPage(_Page):
    items: List[CommandRecord]


class MessageError(BaseModel):
    code: Optional[str]
    description: Optional[str]


class MessageRecord(BaseModel):
    org: str
    charger_id: str
    direction: Literal["in", "out"] = Field(description="in: from the charger; out: to it")
    type: Literal["call", "result", "error"]
    action: Optional[str] = Field(description="Null for a reply whose request was not seen (for example after a restart)")
    message_id: str
    payload: Any = Field(description="Null when it was too large to keep (see `truncated`)")
    error: Optional[MessageError]
    truncated: bool
    size: Optional[int] = Field(description="Size in bytes of a payload that was not kept")
    timestamp: Optional[datetime]


class MessagePage(_Page):
    items: List[MessageRecord]
