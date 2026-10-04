"""
Where the organizations of the broker live, and how they are changed while it runs.

``FileConfigStore`` edits the ``organizations`` of the configuration file the broker was started with, nothing
else in it: the file is read again for every change, the new organizations are checked with the same rules the
broker applies at startup (``config.build_config``), and only then is the file replaced, atomically, after a copy
of the old one has been kept. What is not an organization (ports, MongoDB, security) is written back as it was
read, so a value that came from an environment variable never ends up in the file.

Every change names the ``revision`` of the file it was made from. If the file has changed since (someone edited it
by hand, another instance of the admin API wrote it), the change is refused rather than overwriting that.

Writing the file with PyYAML **drops the comments** in it. That is said wherever a change is offered, and the
copy that is kept is the way back.

The interface is small (``snapshot``, ``plan``, ``apply``) so that a store backed by MongoDB could replace the
file later.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import yaml

from .config import build_config

logger = logging.getLogger("ocpp_broker.config_store")

BACKUP_INFIX = ".bak-"
DEFAULT_KEEP_BACKUPS = 10


class ConfigError(Exception):
    """A change that was not made, with the reason as it can be shown."""


class Conflict(ConfigError):
    """The file is not the one the change was made from."""


class NotWritable(ConfigError):
    """There is no file to change, or it cannot be written."""


@dataclass(frozen=True)
class Snapshot:
    path: str
    revision: str
    modified: datetime
    organizations: List[Dict[str, Any]]  # as written in the file, without the defaults the broker adds


@dataclass
class Plan:
    """What a change would do, found without touching anything."""

    base_revision: str  # the file the plan was made from
    current_revision: str  # the file now (differs when it changed since the caller read it)
    document: Dict[str, Any]  # the whole file as it would be written
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Applied:
    revision: str
    backup: Optional[str]  # the name of the copy kept of the file before it was replaced
    runtime: Dict[str, Any]  # the configuration the broker runs on, with the new organizations in it


def revision_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]


class _Warnings(logging.Handler):
    """Collects what the configuration code logs as a warning while a configuration is being checked."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def check(document: Dict[str, Any]) -> tuple[List[str], List[str], Optional[Dict[str, Any]]]:
    """``(errors, warnings, runtime configuration)`` of a configuration file's content, as the broker would read it."""
    handler = _Warnings()
    config_logger = logging.getLogger("ocpp_broker.config")
    config_logger.addHandler(handler)
    propagate, config_logger.propagate = config_logger.propagate, False  # checking is not news: the warnings are returned, not logged
    try:
        runtime = build_config(copy.deepcopy(document), apply_env=False)
    except ValueError as exc:
        return [str(exc)], handler.messages, None
    except Exception as exc:  # a shape the loader did not expect, such as organizations that are not a list
        return [f"The configuration cannot be read: {exc}"], handler.messages, None
    finally:
        config_logger.propagate = propagate
        config_logger.removeHandler(handler)
    return [], handler.messages, runtime


class FileConfigStore:
    def __init__(self, path: str, keep_backups: int = DEFAULT_KEEP_BACKUPS):
        self.path = os.path.abspath(path)
        self.keep_backups = keep_backups
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ reading
    def writable(self) -> tuple[bool, Optional[str]]:
        if not os.path.isfile(self.path):
            return False, "The broker was started without a configuration file, so there is nothing to change"
        if not os.access(self.path, os.W_OK) or not os.access(os.path.dirname(self.path), os.W_OK):
            return False, "The configuration file or its folder is read-only for the broker"
        return True, None

    def _read(self) -> tuple[Dict[str, Any], bytes]:
        try:
            with open(self.path, "rb") as handle:
                content = handle.read()
        except OSError as exc:
            raise NotWritable(f"Could not read the configuration file: {exc}") from exc
        try:
            document = yaml.safe_load(content) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"The configuration file is not valid YAML: {exc}") from exc
        if not isinstance(document, dict):
            raise ConfigError("The configuration file does not hold a mapping")
        return document, content

    def snapshot(self) -> Snapshot:
        document, content = self._read()
        organizations = document.get("organizations") or []
        if not isinstance(organizations, list) or not all(isinstance(o, dict) for o in organizations):
            raise ConfigError("The organizations in the configuration file are not a list of mappings")
        modified = datetime.fromtimestamp(os.path.getmtime(self.path), tz=timezone.utc)
        return Snapshot(self.path, revision_of(content), modified, copy.deepcopy(organizations))

    # ------------------------------------------------------------------ planning
    def plan(self, revision: str, organizations: List[Dict[str, Any]]) -> Plan:
        """
        Check ``organizations`` as the new organizations of the file. ``revision`` is the file they were made from;
        the plan says if that is not the file as it is now.
        """
        document, content = self._read()
        current = revision_of(content)
        new_document = {**document, "organizations": copy.deepcopy(organizations)}
        errors, warnings, _ = check(new_document)
        # What the loader says about the organizations nobody is touching is not news; show only what the change adds
        _, already, _ = check(document)
        warnings = [w for w in warnings if w not in already]
        plan = Plan(base_revision=revision, current_revision=current, document=new_document, errors=errors, warnings=warnings)
        if revision != current:
            plan.errors.insert(0, "The configuration file has changed since this page loaded it. Reload, then make the change again.")
        return plan

    # ------------------------------------------------------------------ applying
    async def apply(self, plan: Plan) -> Applied:
        """Write a plan that is ok. Raises ``Conflict`` if the file is no longer the one it was made from."""
        if not plan.ok:
            raise ConfigError("; ".join(plan.errors))
        async with self._lock:
            return await asyncio.to_thread(self._apply, plan)

    def _apply(self, plan: Plan) -> Applied:
        writable, reason = self.writable()
        if not writable:
            raise NotWritable(reason or "The configuration file cannot be written")
        _, content = self._read()
        if revision_of(content) != plan.base_revision:
            raise Conflict("The configuration file has changed since this page loaded it. Reload, then make the change again.")
        errors, _, runtime = check(plan.document)
        if errors or runtime is None:  # cannot happen for a plan that was ok, unless the code changed under it
            raise ConfigError("; ".join(errors) or "The configuration cannot be used")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")  # fixed width, so the names sort by time
        backup = f"{os.path.basename(self.path)}{BACKUP_INFIX}{stamp}"
        backup_path = os.path.join(os.path.dirname(self.path), backup)
        if os.path.exists(backup_path):  # the same microsecond twice: keep the later name later
            stamp += "z"
            backup = f"{os.path.basename(self.path)}{BACKUP_INFIX}{stamp}"
            backup_path = os.path.join(os.path.dirname(self.path), backup)
        header = (
            "# Written by the broker's admin API on "
            f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}. Comments in the earlier file were not kept;\n"
            f"# the file as it was is {backup}.\n"
        )
        text = header + yaml.safe_dump(plan.document, sort_keys=False, default_flow_style=False, allow_unicode=True)
        temporary = None
        try:
            shutil.copy2(self.path, backup_path)
            fd, temporary = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".config-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            shutil.copymode(self.path, temporary)  # a file made private by hand stays private
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            raise NotWritable(f"Could not write the configuration file (it was not changed): {exc}") from exc
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        self._prune_backups()
        return Applied(revision=revision_of(text.encode("utf-8")), backup=backup, runtime=runtime)

    def _prune_backups(self) -> None:
        folder = os.path.dirname(self.path)
        prefix = os.path.basename(self.path) + BACKUP_INFIX
        copies = sorted(name for name in os.listdir(folder) if name.startswith(prefix))
        for name in copies[: max(0, len(copies) - self.keep_backups)]:
            try:
                os.unlink(os.path.join(folder, name))
            except OSError as exc:
                logger.warning("Could not remove the old configuration backup %s: %s", name, exc)
