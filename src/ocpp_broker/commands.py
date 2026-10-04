"""
The commands an operator can send to a charger: the catalog the console builds its forms from, and the
short per-charger log of what was sent and how it ended.

The catalog lists the OCPP 1.6 messages a central system sends (the ones the typed routes under
``/api/ocpp/organizations/{org}/chargers/{id}/commands/`` cover), each with the JSON Schema the ``ocpp``
library itself validates against. The schemas are read from the installed library, so they cannot
drift from what broker mode accepts.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

Risk = Literal["read", "change", "disruptive"]

OCPP_VERSION = "1.6"
LOG_SIZE = 50  # commands remembered per connected charger

REDACTED = "***"
# ChangeConfiguration keys whose value is a secret (OCPP 1.6 security whitepaper: the basic-auth password)
SECRET_KEYS = frozenset({"authorizationkey"})


@dataclass(frozen=True)
class CommandSpec:
    action: str
    summary: str
    risk: Risk


# read: only asks; change: alters settings or data on the charger; disruptive: can interrupt a charging
# session, take a connector out of service or restart the charger (the console asks for confirmation).
COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec("GetConfiguration", "Read configuration keys", "read"),
    CommandSpec("GetCompositeSchedule", "Read the charging schedule in force", "read"),
    CommandSpec("GetLocalListVersion", "Read the version of the local authorization list", "read"),
    CommandSpec("TriggerMessage", "Ask the charger to send a message now", "read"),
    CommandSpec("ChangeConfiguration", "Set a configuration key", "change"),
    CommandSpec("ClearCache", "Clear the authorization cache", "change"),
    CommandSpec("DataTransfer", "Send vendor-specific data", "change"),
    CommandSpec("GetDiagnostics", "Ask the charger to upload its diagnostics", "change"),
    CommandSpec("SendLocalList", "Send or update the local authorization list", "change"),
    CommandSpec("SetChargingProfile", "Set a charging profile", "change"),
    CommandSpec("ClearChargingProfile", "Remove charging profiles", "change"),
    CommandSpec("ReserveNow", "Reserve a connector for an id tag", "change"),
    CommandSpec("CancelReservation", "Cancel a reservation", "change"),
    CommandSpec("RemoteStartTransaction", "Start a charging session", "disruptive"),
    CommandSpec("RemoteStopTransaction", "Stop a charging session", "disruptive"),
    CommandSpec("ChangeAvailability", "Make a connector or the charger operative or inoperative", "disruptive"),
    CommandSpec("UnlockConnector", "Unlock a connector", "disruptive"),
    CommandSpec("Reset", "Restart the charger", "disruptive"),
    CommandSpec("UpdateFirmware", "Tell the charger to download and install firmware", "disruptive"),
)


def route_for(action: str) -> str:
    return f"/api/ocpp/organizations/{{org_name}}/chargers/{{charger_id}}/commands/{action}"


def _schema_dir() -> Path:
    import ocpp.v16

    return Path(ocpp.v16.__file__).parent / "schemas"


@lru_cache(maxsize=1)
def _schemas() -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for spec in COMMANDS:
        # The library names the request schema after the action ("Reset.json", not "ResetRequest.json")
        with open(_schema_dir() / f"{spec.action}.json", encoding="utf-8") as handle:
            schema = json.load(handle)
        schema.pop("$schema", None)
        found[spec.action] = schema
    return found


def schema_for(action: str) -> Optional[Dict[str, Any]]:
    """A copy of the request schema of one catalog command, or None for any other action."""
    schema = _schemas().get(action)
    return copy.deepcopy(schema) if schema is not None else None


def catalog() -> Dict[str, Any]:
    """The command catalog as served by ``GET /api/ocpp/commands/catalog``."""
    return {
        "ocpp_version": OCPP_VERSION,
        "commands": [
            {
                "action": spec.action,
                "summary": spec.summary,
                "risk": spec.risk,
                "json_schema": schema_for(spec.action) or {},
                "route": route_for(spec.action),
            }
            for spec in COMMANDS
        ],
    }


# ---------------------------------------------------------------------------
# The per-charger log
# ---------------------------------------------------------------------------
def redact_payload(action: str, payload: Any) -> Any:
    """A copy of a command payload that is safe to keep in the log and show again: secret values removed."""
    if action == "ChangeConfiguration" and isinstance(payload, dict):
        key = payload.get("key")
        if isinstance(key, str) and key.lower() in SECRET_KEYS and "value" in payload:
            return {**payload, "value": REDACTED}
    return payload


def redact_response(action: str, response: Any) -> Any:
    if action == "GetConfiguration" and isinstance(response, dict) and isinstance(response.get("configurationKey"), list):
        cleaned: List[Any] = []
        for item in response["configurationKey"]:
            if isinstance(item, dict) and isinstance(item.get("key"), str) and item["key"].lower() in SECRET_KEYS and "value" in item:
                item = {**item, "value": REDACTED}
            cleaned.append(item)
        return {**response, "configurationKey": cleaned}
    return response


@dataclass
class CommandEntry:
    message_id: str
    action: str
    payload: Any
    status: str  # pending | success | error | timeout | cancelled
    sent_at: datetime
    response: Any = None
    error: Optional[str] = None
    finished_at: Optional[datetime] = None

    def finish(self, status: str, response: Any = None, error: Optional[str] = None) -> None:
        self.status = status
        self.response = redact_response(self.action, response)
        self.error = error
        self.finished_at = datetime.now(timezone.utc)

    @property
    def duration_ms(self) -> Optional[int]:
        if self.finished_at is None:
            return None
        return max(0, round((self.finished_at - self.sent_at).total_seconds() * 1000))


def new_entry(message_id: str, action: str, payload: Any) -> CommandEntry:
    return CommandEntry(message_id, action, redact_payload(action, payload), "pending", datetime.now(timezone.utc))
