"""
HTTP Basic auth on the charger WebSocket upgrade (OCPP security profile 1),
plus the credential helpers and config normalisation behind it.
"""

import base64
import logging

import pytest
import websockets
from websockets.exceptions import InvalidStatus

from ocpp_broker import server
from ocpp_broker.auth import (
    authenticate_charger,
    charger_auth_required,
    hash_password,
    parse_basic_auth,
    verify_password,
)
from ocpp_broker.config import load_config

from .fakes import wait_for

HASHED = hash_password("hashed-key-0123456789", iterations=1000)

SECURED = {
    "name": "Secured",
    "connect_to_backend": False,
    "charger_auth": {
        "credentials": {
            "CP_PLAIN": {"password": "plain-key-0123456789"},
            "CP_HASHED": {"password_hash": HASHED},
        }
    },
}
OPEN = {"name": "Open", "connect_to_backend": False}
CONFIG = {"organizations": [SECURED, OPEN]}


def basic(user: str, password: str) -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def _connect(port, org, charger_id, headers=None):
    return websockets.connect(
        f"ws://127.0.0.1:{port}/{org}/{charger_id}",
        subprotocols=["ocpp1.6"],
        additional_headers=headers,
        ping_interval=None,
    )


# ---------------------------------------------------------------------------
# Live upgrade handshake
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "charger_id,password",
    [("CP_PLAIN", "plain-key-0123456789"), ("CP_HASHED", "hashed-key-0123456789")],
)
async def test_correct_credentials_connect(run_server, charger_id, password):
    port = await run_server(CONFIG)

    async with await _connect(port, "Secured", charger_id, basic(charger_id, password)) as ws:
        await wait_for(lambda: ("Secured", charger_id) in server.broker.sessions)
        assert ws.close_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        None,  # no Authorization header
        basic("CP_PLAIN", "wrong-key"),  # wrong password
        basic("SOMEONE_ELSE", "plain-key-0123456789"),  # right key, wrong username
        {"Authorization": "Bearer abc"},  # wrong scheme
        {"Authorization": "Basic !!!not-base64!!!"},  # garbage
        basic("CP_PLAIN", ""),  # empty password
    ],
    ids=["missing", "wrong-password", "username-mismatch", "bearer", "garbage", "empty-password"],
)
async def test_bad_credentials_get_http_401_on_the_upgrade(run_server, headers):
    port = await run_server(CONFIG)

    with pytest.raises(InvalidStatus) as rejected:
        async with await _connect(port, "Secured", "CP_PLAIN", headers):
            pass

    response = rejected.value.response
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")
    assert ("Secured", "CP_PLAIN") not in server.broker.sessions


@pytest.mark.asyncio
async def test_a_charger_not_in_the_credentials_list_is_rejected(run_server):
    port = await run_server(CONFIG)

    with pytest.raises(InvalidStatus) as rejected:
        async with await _connect(port, "Secured", "CP_UNKNOWN", basic("CP_UNKNOWN", "plain-key-0123456789")):
            pass

    assert rejected.value.response.status_code == 401


@pytest.mark.asyncio
async def test_one_chargers_key_does_not_open_another_charger(run_server):
    port = await run_server(CONFIG)

    with pytest.raises(InvalidStatus) as rejected:
        async with await _connect(port, "Secured", "CP_HASHED", basic("CP_HASHED", "plain-key-0123456789")):
            pass

    assert rejected.value.response.status_code == 401


@pytest.mark.asyncio
async def test_orgs_without_credentials_stay_open(run_server):
    port = await run_server(CONFIG)

    async with await _connect(port, "Open", "ANY_CHARGER") as ws:
        await wait_for(lambda: ("Open", "ANY_CHARGER") in server.broker.sessions)
        assert ws.close_code is None


@pytest.mark.asyncio
async def test_required_without_credentials_rejects_everyone(run_server):
    port = await run_server(
        {"organizations": [{"name": "Locked", "connect_to_backend": False, "charger_auth": {"required": True}}]}
    )

    with pytest.raises(InvalidStatus) as rejected:
        async with await _connect(port, "Locked", "CP1", basic("CP1", "x")):
            pass

    assert rejected.value.response.status_code == 401


@pytest.mark.asyncio
async def test_credentials_never_appear_in_logs(run_server, caplog):
    port = await run_server(CONFIG)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(InvalidStatus):
            async with await _connect(port, "Secured", "CP_PLAIN", basic("CP_PLAIN", "hunter2-secret")):
                pass

    # Only the broker's and the server's own logs; the test client logs its request headers.
    server_side = [r for r in caplog.records if r.name.startswith(("ocpp_broker", "uvicorn"))]
    assert server_side, "expected the broker to log the rejection"
    text = " ".join(r.getMessage() for r in server_side)
    assert "hunter2-secret" not in text
    assert base64.b64encode(b"CP_PLAIN:hunter2-secret").decode() not in text
    assert "authentication failed" in text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def test_password_hash_roundtrip_and_format():
    stored = hash_password("s3cret", iterations=1000)

    assert stored.startswith("pbkdf2_sha256$1000$")
    assert verify_password("s3cret", stored)
    assert not verify_password("S3cret", stored)
    assert stored != hash_password("s3cret", iterations=1000), "salted"


@pytest.mark.parametrize("stored", ["", "garbage", "md5$1$a$b", "pbkdf2_sha256$x$y$z", "pbkdf2_sha256$1$!!$!!"])
def test_verify_password_rejects_malformed_hashes_without_raising(stored):
    assert verify_password("anything", stored) is False


@pytest.mark.parametrize(
    "header,expected",
    [
        (None, None),
        ("", None),
        ("Basic", None),
        ("Bearer abc", None),
        ("Basic !!!", None),
        (f"Basic {base64.b64encode(b'no-colon').decode()}", None),
        (f"Basic {base64.b64encode(b'user:pa:ss').decode()}", ("user", "pa:ss")),  # only the first colon splits
        (f"basic {base64.b64encode(b'user:').decode()}", ("user", "")),
    ],
)
def test_parse_basic_auth(header, expected):
    assert parse_basic_auth(header) == expected


@pytest.mark.parametrize(
    "org,expected",
    [
        (None, False),
        ({}, False),
        ({"charger_auth": {}}, False),
        ({"charger_auth": {"credentials": {"A": {"password": "x"}}}}, True),
        ({"charger_auth": {"required": True}}, True),
        ({"charger_auth": {"required": False, "credentials": {"A": {"password": "x"}}}}, False),
    ],
)
def test_charger_auth_required(org, expected):
    assert charger_auth_required(org) is expected


@pytest.mark.asyncio
async def test_open_orgs_skip_verification_entirely():
    assert await authenticate_charger(OPEN, "CP", None) is True
    assert await authenticate_charger(None, "CP", None) is True


# ---------------------------------------------------------------------------
# Config normalisation
# ---------------------------------------------------------------------------
def _load(tmp_path, org):
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"organizations": [org]}))
    return load_config(str(path))["organizations"][0]


def test_config_normalises_credentials_and_enables_auth(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.config"):
        org = _load(
            tmp_path,
            {
                "name": "A",
                "charger_auth": {"credentials": {"CP1": "bare-string-key", 1002: {"password_hash": HASHED}}},
            },
        )

    assert org["charger_auth"]["required"] is True
    assert org["charger_auth"]["credentials"]["CP1"] == {"password": "bare-string-key"}
    assert "1002" in org["charger_auth"]["credentials"], "numeric YAML keys become charger id strings"
    assert any("plaintext" in r.getMessage() for r in caplog.records)


def test_config_warns_loudly_about_open_orgs(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.config"):
        org = _load(tmp_path, {"name": "A"})

    assert org["charger_auth"]["required"] is False
    assert any("UNAUTHENTICATED" in r.getMessage() for r in caplog.records)


def test_config_rejects_credentials_without_a_password(tmp_path):
    with pytest.raises(ValueError, match="CP1"):
        _load(tmp_path, {"name": "A", "charger_auth": {"credentials": {"CP1": {}}}})


def test_config_warns_when_auth_is_required_but_nothing_is_configured(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="ocpp_broker.config"):
        _load(tmp_path, {"name": "A", "charger_auth": {"required": True}})

    assert any("every charger will be rejected" in r.getMessage() for r in caplog.records)
