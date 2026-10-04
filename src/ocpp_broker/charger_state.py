"""
What the broker knows about one connected charger, for the web console and the API.

It is filled by *watching* the charger's frames (``observe``), the same way in both modes: in
broker mode the frames are the ones the ``ocpp`` library is about to handle, in relay mode the
ones being forwarded. Nothing here changes a frame or answers anything, and a frame it cannot
read is simply counted. Nothing is persisted: it describes the current connection.

Names are version-neutral (``connector_id`` today, an ``evse_id`` can be added for OCPP 2.x)
so the console does not need to change when other protocol versions are supported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _when(value: Any, default: datetime) -> datetime:
    """A charger's own timestamp if it is a readable ISO 8601 string, else ``default``."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return default
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return default


@dataclass
class BootInfo:
    vendor: Optional[str]
    model: Optional[str]
    serial_number: Optional[str]
    firmware_version: Optional[str]
    iccid: Optional[str]
    imsi: Optional[str]
    meter_type: Optional[str]
    meter_serial_number: Optional[str]
    received_at: datetime


@dataclass
class ConnectorState:
    connector_id: int  # 0 is the charger as a whole
    status: str
    error_code: Optional[str]
    info: Optional[str]
    updated_at: datetime


@dataclass
class ChargerState:
    remote_address: Optional[str] = None
    connected_at: datetime = field(default_factory=_now)
    last_seen: Optional[datetime] = None
    last_heartbeat_at: Optional[datetime] = None
    frames_in: int = 0
    frames_out: int = 0
    boot: Optional[BootInfo] = None
    connectors: Dict[int, ConnectorState] = field(default_factory=dict)

    def note_sent(self) -> None:
        self.frames_out += 1

    def observe(self, raw: str) -> None:
        """Take note of one frame received from the charger."""
        now = _now()
        self.frames_in += 1
        self.last_seen = now
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not (isinstance(frame, list) and len(frame) >= 4 and frame[0] == 2 and isinstance(frame[3], dict)):
            return  # an answer to something the broker or a backend asked, or something odd
        action, payload = frame[2], frame[3]
        if action == "BootNotification":
            self.boot = BootInfo(
                vendor=_text(payload.get("chargePointVendor")),
                model=_text(payload.get("chargePointModel")),
                serial_number=_text(payload.get("chargePointSerialNumber")),
                firmware_version=_text(payload.get("firmwareVersion")),
                iccid=_text(payload.get("iccid")),
                imsi=_text(payload.get("imsi")),
                meter_type=_text(payload.get("meterType")),
                meter_serial_number=_text(payload.get("meterSerialNumber")),
                received_at=now,
            )
        elif action == "Heartbeat":
            self.last_heartbeat_at = now
        elif action == "StatusNotification":
            connector = payload.get("connectorId")
            status = payload.get("status")
            if isinstance(connector, int) and not isinstance(connector, bool) and isinstance(status, str):
                self.connectors[connector] = ConnectorState(
                    connector_id=connector,
                    status=status,
                    error_code=_text(payload.get("errorCode")),
                    info=_text(payload.get("info")),
                    updated_at=_when(payload.get("timestamp"), now),
                )

    def ordered_connectors(self) -> list[ConnectorState]:
        return [self.connectors[k] for k in sorted(self.connectors)]


def remote_address(websocket: Any) -> Optional[str]:
    """``host:port`` of the peer as the server sees it (a reverse proxy's address if there is one)."""
    client = getattr(websocket, "client", None)
    host = getattr(client, "host", None)
    if host is None:
        return None
    port = getattr(client, "port", None)
    return f"{host}:{port}" if port else str(host)
