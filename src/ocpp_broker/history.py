"""
What the broker keeps of what happened, and how it is read back.

Written by the broker itself and never through the REST API, so the records can be trusted as a log:

* ``transactions``, ``charger_statuses``, ``meter_values``: written by the message handlers when the broker
  answers the charger (broker mode, local leader). In relay mode the backend answers, so only status changes
  are recorded (from watching the charger's frames).
* ``commands``: every command sent through the API or the console, with its (redacted) payload and outcome.
* ``ocpp_messages``: every frame to and from the charger, in every mode. Off unless
  ``mongodb.history.messages`` is true; it is large.

Configuration (``mongodb.history``)::

    messages: false          # record every OCPP frame in ocpp_messages
    heartbeats: false        # with messages on, also record Heartbeat requests and replies
    retention_days:          # a MongoDB TTL index per collection; omit or null to keep for ever
      messages: 30
      commands: 365
      statuses: null
      meter_values: null
      transactions: null

This module has no MongoDB dependency of its own: it builds documents, queries and cursors, and the
service (``mongodb_service.py``) runs them.
"""

from __future__ import annotations

import base64
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Retention setting name -> (collection, the field its TTL index is on)
RETENTION = {
    "messages": ("ocpp_messages", "timestamp"),
    "commands": ("commands", "sent_at"),
    "statuses": ("charger_statuses", "timestamp"),
    "meter_values": ("meter_values", "timestamp"),
    "transactions": ("transactions", "timestamp"),
}
DEFAULT_RETENTION_DAYS: Dict[str, Optional[int]] = {"messages": 30, "commands": 365}

# (collection, keys): what the history queries sort and filter on. Created when MongoDB connects.
INDEXES: List[Tuple[str, List[Tuple[str, int]]]] = [
    ("charger_statuses", [("org_name", 1), ("charger_id", 1), ("timestamp", -1)]),
    ("meter_values", [("org_name", 1), ("charger_id", 1), ("timestamp", -1)]),
    ("meter_values", [("org_name", 1), ("charger_id", 1), ("transaction_id", 1)]),
    ("transactions", [("org_name", 1), ("charger_id", 1), ("transaction_id", 1)]),
    ("transactions", [("org_name", 1), ("charger_id", 1), ("timestamp", -1)]),
    ("ocpp_messages", [("org_name", 1), ("charger_id", 1), ("timestamp", -1)]),
    ("commands", [("org_name", 1), ("charger_id", 1), ("sent_at", -1)]),
]

MAX_PAYLOAD_BYTES = 64 * 1024  # a larger payload is not stored, only its size
MAX_LABELS = 200  # unanswered calls remembered per charger so that their answers can be labelled
MAX_PAGE = 500
DEFAULT_PAGE = 100


@dataclass(frozen=True)
class HistoryConfig:
    messages: bool = False
    heartbeats: bool = False
    retention_days: Dict[str, Optional[int]] = field(default_factory=lambda: dict(DEFAULT_RETENTION_DAYS))


def validate_history_config(mongodb: Dict[str, Any]) -> None:
    """Raise ValueError for a ``mongodb.history`` block that cannot be used."""
    block = mongodb.get("history")
    if block is None:
        return
    if not isinstance(block, dict):
        raise ValueError("mongodb.history must be a mapping")
    for name in ("messages", "heartbeats"):
        if name in block and not isinstance(block[name], bool):
            raise ValueError(f"mongodb.history.{name} must be true or false")
    days = block.get("retention_days")
    if days is None:
        return
    if not isinstance(days, dict):
        raise ValueError("mongodb.history.retention_days must be a mapping")
    for name, value in days.items():
        if name not in RETENTION:
            raise ValueError(f"mongodb.history.retention_days.{name} is not known; use one of {', '.join(RETENTION)}")
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            raise ValueError(f"mongodb.history.retention_days.{name} must be a whole number of days, 1 or more (or null)")


def history_config(mongodb: Optional[Dict[str, Any]]) -> HistoryConfig:
    block = (mongodb or {}).get("history") or {}
    retention = dict(DEFAULT_RETENTION_DAYS)
    retention.update(block.get("retention_days") or {})
    return HistoryConfig(
        messages=bool(block.get("messages", False)),
        heartbeats=bool(block.get("heartbeats", False)),
        retention_days=retention,
    )


# ---------------------------------------------------------------------------
# Frames -> ocpp_messages documents
# ---------------------------------------------------------------------------
class MessageLabels:
    """
    Names the action a CALLRESULT or CALLERROR answers. A reply carries only the message id, so the id of
    every CALL seen is remembered, per direction, until it is answered (bounded, oldest forgotten first).
    """

    def __init__(self) -> None:
        self._calls: "OrderedDict[Tuple[str, str], str]" = OrderedDict()

    def remember(self, direction: str, message_id: str, action: str) -> None:
        self._calls[(direction, message_id)] = action
        while len(self._calls) > MAX_LABELS:
            self._calls.popitem(last=False)

    def answered(self, direction: str, message_id: str) -> Optional[str]:
        return self._calls.pop((direction, message_id), None)


def _payload_field(payload: Any) -> Dict[str, Any]:
    try:
        size = len(json.dumps(payload))
    except (TypeError, ValueError):
        return {"payload": None, "truncated": True}
    if size > MAX_PAYLOAD_BYTES:
        return {"payload": None, "truncated": True, "size": size}
    return {"payload": payload}


def message_document(
    org_name: str, charger_id: str, direction: str, raw: str, labels: MessageLabels, config: HistoryConfig
) -> Optional[Dict[str, Any]]:
    """
    The ``ocpp_messages`` document for one frame, or None when it is not to be recorded (not an OCPP frame,
    or a heartbeat with heartbeats off). ``direction`` is ``"in"`` for a frame from the charger and ``"out"``
    for one to it. Every frame goes through here, so a reply is always labelled and a call always remembered
    even when its document is not stored.
    """
    try:
        frame = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not (isinstance(frame, list) and len(frame) >= 3 and frame[0] in (2, 3, 4) and isinstance(frame[1], str)):
        return None
    kind, message_id = frame[0], frame[1]
    document: Dict[str, Any] = {
        "org_name": org_name,
        "charger_id": charger_id,
        "direction": direction,
        "message_id": message_id,
        "timestamp": datetime.now(timezone.utc),
    }
    if kind == 2:
        if len(frame) < 4 or not isinstance(frame[2], str):
            return None
        action: Optional[str] = frame[2]
        # The reply travels the other way; remember which side asked
        labels.remember(direction, message_id, frame[2])
        document.update(type="call", action=action, **_payload_field(frame[3]))
    else:
        asked = "out" if direction == "in" else "in"  # a reply from the charger answers a call we sent out
        action = labels.answered(asked, message_id)
        if kind == 3:
            document.update(type="result", action=action, **_payload_field(frame[2]))
        else:
            document.update(
                type="error",
                action=action,
                error={
                    "code": frame[2] if isinstance(frame[2], str) else None,
                    "description": frame[3] if len(frame) > 3 else None,
                },
                **_payload_field(frame[4] if len(frame) > 4 else {}),
            )
    if action == "Heartbeat" and not config.heartbeats:
        return None
    return document


# ---------------------------------------------------------------------------
# Reading: cursors, meter values
# ---------------------------------------------------------------------------
class BadCursor(ValueError):
    pass


def encode_cursor(when: datetime, row_id: Any) -> str:
    stamp = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return base64.urlsafe_b64encode(json.dumps([stamp.isoformat(), str(row_id)]).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> Tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        when, row_id = json.loads(base64.urlsafe_b64decode(padded.encode()))
        parsed = datetime.fromisoformat(when)
        if not isinstance(row_id, str):
            raise TypeError
    except (ValueError, TypeError, UnicodeError) as exc:
        raise BadCursor("The cursor is not valid") from exc
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)), row_id


def flatten_meter_values(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    One row per sampled value of a stored MeterValues document: the reading's time, the measurand, phase, unit
    and the value as a number (``None`` when the charger sent text, which OCPP allows for some formats).
    """
    rows: List[Dict[str, Any]] = []
    for reading in document.get("meter_value") or []:
        if not isinstance(reading, dict):
            continue
        taken = reading.get("timestamp") or document.get("timestamp")
        for sample in reading.get("sampled_value") or reading.get("sampledValue") or []:
            if not isinstance(sample, dict):
                continue
            raw = sample.get("value")
            try:
                value: Optional[float] = None if raw is None else float(raw)
            except (TypeError, ValueError):
                value = None
            rows.append(
                {
                    "timestamp": taken,
                    "connector_id": document.get("connector_id"),
                    "transaction_id": document.get("transaction_id"),
                    "measurand": sample.get("measurand") or "Energy.Active.Import.Register",
                    "phase": sample.get("phase"),
                    "unit": sample.get("unit") or "Wh",
                    "context": sample.get("context"),
                    "location": sample.get("location"),
                    "value": value,
                    "raw_value": None if raw is None else str(raw),
                }
            )
    return rows


def transaction_row(document: Dict[str, Any]) -> Dict[str, Any]:
    """A stored transaction as the API shows it; the energy is what the meter advanced during it."""
    start, stop = document.get("meter_start"), document.get("meter_stop")
    energy = stop - start if isinstance(start, (int, float)) and isinstance(stop, (int, float)) and stop >= start else None
    return {
        "org": document.get("org_name"),
        "charger_id": document.get("charger_id"),
        "transaction_id": document.get("transaction_id"),
        "connector_id": document.get("connector_id"),
        "id_tag": document.get("id_tag"),
        "started_at": document.get("timestamp"),
        "stopped_at": document.get("stop_timestamp"),
        "meter_start": start,
        "meter_stop": stop,
        "energy_wh": energy,
        "stop_reason": document.get("stop_reason"),
        "stop_id_tag": document.get("stop_id_tag"),
        "open": document.get("stop_timestamp") is None,
    }
