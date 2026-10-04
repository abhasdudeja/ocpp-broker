"""
Authentication: charger WebSocket upgrades (OCPP 1.6 security profile 1) and the REST API key.

Profile 1 is HTTP Basic auth on the WebSocket upgrade: the username is the
charge point identity (the charger id in the URL) and the password is its
authorization key. It does not encrypt anything; run the broker behind TLS
termination (profile 2) wherever the network is not trusted.
"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import os
import re
import secrets
import sys
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger("ocpp_broker.auth")

HASH_SCHEME = "pbkdf2_sha256"
# Authorization keys are random machine-generated secrets, not human passwords,
# so a moderate work factor is plenty and keeps reconnect storms affordable.
DEFAULT_ITERATIONS = 200_000
API_KEY_ENV = "OCPP_BROKER_API_KEY"
API_KEY_HEADER = "X-API-Key"


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Return ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` (base64) for ``password``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "$".join(
        [HASH_SCHEME, str(iterations), base64.b64encode(salt).decode(), base64.b64encode(digest).decode()]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored_hash.split("$")
        if scheme != HASH_SCHEME:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(actual, expected)


def hash_password_cli() -> None:
    """``ocpp-broker-hash-password [PASSWORD]``: print a hash for config.yaml."""
    password = sys.argv[1] if len(sys.argv) > 1 else None
    if password is None:
        import getpass

        password = getpass.getpass("Authorization key: ")
    print(hash_password(password))


# ---------------------------------------------------------------------------
# Charger WebSocket authentication (security profile 1)
# ---------------------------------------------------------------------------
def charger_auth_required(org_entry: Optional[Dict[str, Any]]) -> bool:
    """Whether chargers of this organization must authenticate."""
    if not org_entry:
        return False
    auth = org_entry.get("charger_auth") or {}
    return bool(auth.get("required", bool(auth.get("credentials"))))


def parse_basic_auth(header: Optional[str]) -> Optional[Tuple[str, str]]:
    """Decode an ``Authorization: Basic ...`` header into (username, password)."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic" or not token:
        return None
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode()
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    return (username, password) if sep else None


# Verified for unknown chargers so a miss costs the same as a wrong password.
_DUMMY_HASH = hash_password("not-a-real-credential", iterations=DEFAULT_ITERATIONS)


def _check_credentials(org_entry: Dict[str, Any], charger_id: str, authorization: Optional[str]) -> bool:
    parsed = parse_basic_auth(authorization)
    credentials = (org_entry.get("charger_auth") or {}).get("credentials") or {}
    entry = credentials.get(charger_id)

    if parsed is None or entry is None:
        verify_password("x", _DUMMY_HASH)
        return False
    username, password = parsed

    if "password_hash" in entry:
        password_ok = verify_password(password, entry["password_hash"])
    else:
        password_ok = hmac.compare_digest(password.encode(), str(entry.get("password", "")).encode())
        verify_password("x", _DUMMY_HASH)  # keep the cost uniform
    user_ok = hmac.compare_digest(username.encode(), charger_id.encode())
    return password_ok and user_ok


async def authenticate_charger(
    org_entry: Optional[Dict[str, Any]], charger_id: str, authorization: Optional[str]
) -> bool:
    """
    True if the upgrade request may proceed. Open organizations always pass;
    otherwise the Basic username must be the charger id and the password its
    configured key. Hashing runs in a worker thread so a burst of reconnecting
    chargers cannot stall the event loop.
    """
    if not charger_auth_required(org_entry):
        return True
    assert org_entry is not None
    return await asyncio.to_thread(_check_credentials, org_entry, charger_id, authorization)


# ---------------------------------------------------------------------------
# REST API key
# ---------------------------------------------------------------------------
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False, description="REST API key")


DEFAULT_KEY_LABEL = "api-key"
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,40}$")


def configured_api_key(config_data: Optional[Dict[str, Any]]) -> Optional[str]:
    """The API key: environment variable first, then ``security.api_key``."""
    key = os.environ.get(API_KEY_ENV)
    if not key and isinstance(config_data, dict):
        key = (config_data.get("security") or {}).get("api_key")
    return str(key) if key else None


def configured_api_keys(config_data: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """
    Every accepted API key as ``(label, key)``: the main key (labelled ``api-key``) and the entries of
    ``security.api_keys``. The label says which key a call used, so the audit log can tell people apart.
    """
    keys: List[Tuple[str, str]] = []
    main = configured_api_key(config_data)
    if main:
        keys.append((DEFAULT_KEY_LABEL, main))
    security = (config_data.get("security") or {}) if isinstance(config_data, dict) else {}
    for entry in security.get("api_keys") or []:
        if isinstance(entry, dict) and entry.get("key"):
            keys.append((str(entry.get("label", "")), str(entry["key"])))
    return keys


def match_api_key(provided: str, keys: List[Tuple[str, str]]) -> Optional[str]:
    """The label of the key ``provided`` equals. Every key is compared, so the time does not say which one matched."""
    found: Optional[str] = None
    for label, key in keys:
        if hmac.compare_digest(provided.encode(), key.encode()) and found is None:
            found = label
    return found


# ---------------------------------------------------------------------------
# Throttling failed key attempts
# ---------------------------------------------------------------------------
MAX_TRACKED_ADDRESSES = 10_000


class FailureThrottle:
    """
    Blocks a source address for ``lockout`` seconds after ``max_failures`` wrong keys within ``window``
    seconds, so a key cannot be guessed at full speed. While blocked every request from the address is
    refused, right keys included (otherwise a guess that works could not be told from one that is blocked).
    Addresses are as the broker sees them: behind a reverse proxy that is the proxy.
    """

    def __init__(self, max_failures: int, window: float, lockout: float, clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._clock = clock
        self._failures: Dict[str, Deque[float]] = {}
        self._locked_until: Dict[str, float] = {}

    def retry_after(self, address: str) -> int:
        """Seconds until ``address`` may try again; 0 if it may now."""
        until = self._locked_until.get(address)
        if until is None:
            return 0
        remaining = until - self._clock()
        if remaining <= 0:
            del self._locked_until[address]
            return 0
        return int(remaining) + 1

    def record_failure(self, address: str) -> None:
        now = self._clock()
        if address not in self._failures and len(self._failures) >= MAX_TRACKED_ADDRESSES:
            self._forget_stale(now)
        recent = self._failures.setdefault(address, deque())
        recent.append(now)
        while recent and now - recent[0] > self.window:
            recent.popleft()
        if len(recent) >= self.max_failures:
            self._locked_until[address] = now + self.lockout
            del self._failures[address]

    def record_success(self, address: str) -> None:
        self._failures.pop(address, None)

    def _forget_stale(self, now: float) -> None:
        for address in [a for a, recent in self._failures.items() if not recent or now - recent[-1] > self.window]:
            del self._failures[address]
        for address in [a for a, until in self._locked_until.items() if until <= now]:
            del self._locked_until[address]
        while len(self._failures) >= MAX_TRACKED_ADDRESSES:  # all of them recent: drop the oldest tracked
            del self._failures[next(iter(self._failures))]


THROTTLE_DEFAULTS = {"max_failures": 10, "window_seconds": 60, "lockout_seconds": 60}


def throttle_settings(security: Optional[Dict[str, Any]]) -> Tuple[int, float, float]:
    """``(max_failures, window, lockout)`` from ``security.api_key_throttle``; 0 failures turns it off."""
    block = (security or {}).get("api_key_throttle") or {}
    values = {**THROTTLE_DEFAULTS, **block}
    return int(values["max_failures"]), float(values["window_seconds"]), float(values["lockout_seconds"])


def make_api_key_dependency(broker):
    """
    FastAPI dependency enforcing the API key on every route it is attached to.

    The key is read per request, so it follows config reloads. With no key
    configured the API fails closed (503) unless
    ``security.allow_unauthenticated_api`` is explicitly true.
    """

    def throttle_for(security: Optional[Dict[str, Any]]) -> Optional[FailureThrottle]:
        """The broker's throttle, made again if its settings changed; None when it is switched off."""
        settings = throttle_settings(security)
        if settings[0] <= 0:
            return None
        current: Optional[FailureThrottle] = getattr(broker, "api_throttle", None)
        if current is None or (current.max_failures, current.window, current.lockout) != settings:
            current = FailureThrottle(*settings)
            broker.api_throttle = current
        return current

    async def require_api_key(request: Request, header_key: Optional[str] = Depends(_api_key_header)) -> None:
        provided = header_key
        if provided is None:
            bearer = request.headers.get("authorization", "")
            if bearer.lower().startswith("bearer "):
                provided = bearer[7:].strip()

        config_data = getattr(broker, "config_data", None)
        security = (config_data or {}).get("security") if isinstance(config_data, dict) else None
        keys = configured_api_keys(config_data)
        if not keys:
            if (security or {}).get("allow_unauthenticated_api"):
                request.state.api_key_label = "anonymous"
                return
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"REST API disabled: set security.api_key or {API_KEY_ENV}",
            )
        throttle = throttle_for(security)
        address = request.client.host if request.client else "unknown"
        if throttle is not None:
            wait = throttle.retry_after(address)
            if wait:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many wrong API keys from this address; try again in {wait} s",
                    headers={"Retry-After": str(wait)},
                )
        label = match_api_key(provided, keys) if provided is not None else None
        if label is None:
            if provided is not None and throttle is not None:
                throttle.record_failure(address)  # a missing key is not a guess
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if throttle is not None:
            throttle.record_success(address)
        request.state.api_key_label = label

    return require_api_key


if __name__ == "__main__":
    hash_password_cli()
