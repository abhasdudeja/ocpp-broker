"""
The audit log of the admin API: one line for every change that was made, refused or failed.

It holds when, from where (the address the broker sees) and with which API key label, which organizations were
touched and what changed in words. It never holds a password, a hash or an API key; the lines that describe a
change come from ``admin_edit.describe``, which does not see them.

The log is kept in memory (the latest few hundred, what the console lists) and appended to a file as JSON lines
so that it survives a restart. A file that cannot be written is logged once and the log carries on in memory.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, List, Optional

from .schemas.admin import AuditEntry

logger = logging.getLogger("ocpp_broker.audit")

KEEP = 500
TAIL_BYTES = 512 * 1024  # how much of the end of the file is read back at startup


class AuditLog:
    def __init__(self, path: Optional[str] = None, keep: int = KEEP):
        self.path = path
        self._entries: Deque[AuditEntry] = deque(maxlen=keep)
        self._warned = False
        # Called with every entry as it is recorded (the admin API sets it to copy entries into MongoDB)
        self.mirror: Optional[Callable[[AuditEntry], None]] = None
        if path:
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        try:
            with open(self.path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - TAIL_BYTES))
                tail = handle.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("Could not read the audit log %s: %s", self.path, exc)
            return
        for line in tail.splitlines():
            try:
                self._entries.append(AuditEntry.model_validate(json.loads(line)))
            except ValueError:
                continue  # a line that is not ours, or was cut in half (by a crash, or by reading only the end), is skipped

    def record(
        self,
        action: str,
        outcome: str,
        source: Optional[str],
        key_label: Optional[str],
        organizations: List[str],
        summary: List[str],
        detail: Optional[str] = None,
        revision: Optional[str] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            time=datetime.now(timezone.utc),
            action=action,
            outcome=outcome,  # type: ignore[arg-type]
            source=source,
            key_label=key_label,
            organizations=organizations,
            summary=summary,
            detail=detail,
            revision=revision,
        )
        self._entries.append(entry)
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) + "\n")
            except OSError as exc:
                if not self._warned:
                    self._warned = True
                    logger.warning("Could not write the audit log %s (kept in memory only): %s", self.path, exc)
        if self.mirror is not None:
            try:
                self.mirror(entry)
            except Exception as exc:  # the copy is a convenience; the entry is already kept
                logger.warning("Could not copy an audit entry to MongoDB: %s", exc)
        logger.info(
            "admin %s %s by %s from %s: %s",
            action, outcome, key_label or "unknown key", source or "unknown address", "; ".join(summary) or detail or "",
        )
        return entry

    def recent(self, limit: int = 100) -> List[AuditEntry]:
        """Newest first."""
        return list(self._entries)[::-1][:limit]

    def __len__(self) -> int:
        return len(self._entries)


def describe_source(request: Any) -> Optional[str]:
    return request.client.host if getattr(request, "client", None) else None
