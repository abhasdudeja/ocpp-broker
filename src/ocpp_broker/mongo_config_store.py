"""
The organizations kept in MongoDB instead of in the configuration file (``admin.store: mongodb``).

Every broker instance that points at the same database sees the same organizations, and a change made through any of
them reaches the others (they look for a new revision every ``admin.poll_seconds``, see ``OcppBroker``). The first
instance to start with an empty database copies the organizations of its configuration file into it; after that the
database is the truth and the file's ``organizations`` are no longer read. Nothing else of the configuration moves:
ports, MongoDB, security and the rest stay in the file of each instance.

The organizations are one document, so a change is one atomic compare-and-set on its revision: if two people apply
a change at the same time, one wins and the other is told the configuration changed (``Conflict``), as with the file.
The previous versions are kept (the latest ``admin.keep_backups``). Unlike the file store nothing is lost on save:
there are no comments in a database.

Same interface as ``FileConfigStore``: ``snapshot``, ``plan``, ``apply`` and ``writable``.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import timezone
from typing import Any, Dict, List, Optional

from .config_store import (
    DEFAULT_KEEP_BACKUPS,
    Applied,
    Conflict,
    ConfigError,
    NotWritable,
    Plan,
    Snapshot,
    check,
)

logger = logging.getLogger("ocpp_broker.mongo_config_store")


def encode(organizations: List[Dict[str, Any]]) -> str:
    # A date a YAML file wrote without quotes comes back as text, which is what the tag model takes
    return json.dumps(organizations, ensure_ascii=False, default=str, sort_keys=False)


def new_revision() -> str:
    return uuid.uuid4().hex[:16]


class MongoConfigStore:
    def __init__(self, service: Any, keep_backups: int = DEFAULT_KEEP_BACKUPS, label: str = "MongoDB"):
        self.service = service
        self.keep_backups = keep_backups
        self.path = f"{label}: collection {service.ORGANIZATIONS} in database {service.database_name}"

    def writable(self) -> tuple[bool, Optional[str]]:
        if not self.service.is_connected():
            return False, "MongoDB is not connected, so the organizations cannot be read or changed"
        return True, None

    async def _document(self) -> Dict[str, Any]:
        try:
            document = await self.service.get_organizations()
        except Exception as exc:
            raise NotWritable(f"MongoDB did not answer: {exc}") from exc
        if document is None:
            raise ConfigError("No organizations are stored in MongoDB yet")
        return document

    @staticmethod
    def decode(document: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            organizations = json.loads(document["organizations_json"])
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"The organizations stored in MongoDB cannot be read: {exc}") from exc
        if not isinstance(organizations, list) or not all(isinstance(o, dict) for o in organizations):
            raise ConfigError("The organizations stored in MongoDB are not a list of mappings")
        return organizations

    async def snapshot(self) -> Snapshot:
        document = await self._document()
        modified = document["updated_at"]
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=timezone.utc)
        return Snapshot(self.path, str(document["revision"]), modified, copy.deepcopy(self.decode(document)))

    async def plan(self, revision: str, organizations: List[Dict[str, Any]]) -> Plan:
        document = await self._document()
        current = str(document["revision"])
        before = {"organizations": self.decode(document)}
        new_document = {"organizations": copy.deepcopy(organizations)}
        errors, warnings, _ = check(new_document)
        _, already, _ = check(before)
        warnings = [w for w in warnings if w not in already]
        plan = Plan(base_revision=revision, current_revision=current, document=new_document, errors=errors, warnings=warnings)
        if revision != current:
            plan.errors.insert(0, "The organizations have changed since this page loaded them. Reload, then make the change again.")
        return plan

    async def apply(self, plan: Plan, updated_by: str = "the admin API") -> Applied:
        if not plan.ok:
            raise ConfigError("; ".join(plan.errors))
        errors, _, runtime = check(plan.document)
        if errors or runtime is None:
            raise ConfigError("; ".join(errors) or "The organizations cannot be used")
        revision = new_revision()
        try:
            previous = await self.service.replace_organizations(
                plan.base_revision, encode(plan.document["organizations"]), revision, updated_by, self.keep_backups
            )
        except Exception as exc:
            raise NotWritable(f"Could not store the organizations in MongoDB (they were not changed): {exc}") from exc
        if previous is None:
            raise Conflict("The organizations have changed since this page loaded them. Reload, then make the change again.")
        return Applied(revision=revision, backup=f"history entry for revision {previous['revision']}", runtime=runtime)
