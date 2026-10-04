"""
Labelled API keys and the throttle on wrong keys.
"""

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from ocpp_broker import server
from ocpp_broker.auth import (
    API_KEY_ENV,
    MAX_TRACKED_ADDRESSES,
    FailureThrottle,
    configured_api_keys,
    make_api_key_dependency,
    match_api_key,
    throttle_settings,
)
from ocpp_broker.config import _validate_config

from .fakes import API_KEY


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# --------------------------------------------------------------------------- the throttle itself
def make(max_failures=3, window=60, lockout=120):
    clock = Clock()
    return FailureThrottle(max_failures, window, lockout, clock), clock


def test_an_address_is_blocked_after_the_allowed_number_of_wrong_keys():
    throttle, _ = make(max_failures=3)
    for _ in range(2):
        throttle.record_failure("1.2.3.4")
        assert throttle.retry_after("1.2.3.4") == 0
    throttle.record_failure("1.2.3.4")
    assert throttle.retry_after("1.2.3.4") == 121, "the lockout, rounded up to whole seconds"


def test_the_block_ends_when_the_lockout_has_passed_and_the_count_starts_again():
    throttle, clock = make(max_failures=2, lockout=30)
    throttle.record_failure("a")
    throttle.record_failure("a")
    clock.advance(29)
    assert throttle.retry_after("a") == 2
    clock.advance(1.5)
    assert throttle.retry_after("a") == 0
    throttle.record_failure("a")
    assert throttle.retry_after("a") == 0, "one failure after a lockout is one failure, not two"


def test_failures_older_than_the_window_do_not_count():
    throttle, clock = make(max_failures=3, window=60)
    throttle.record_failure("a")
    throttle.record_failure("a")
    clock.advance(61)
    throttle.record_failure("a")
    assert throttle.retry_after("a") == 0
    throttle.record_failure("a")
    throttle.record_failure("a")
    assert throttle.retry_after("a") > 0


def test_a_right_key_clears_the_failures_so_far():
    throttle, _ = make(max_failures=3)
    throttle.record_failure("a")
    throttle.record_failure("a")
    throttle.record_success("a")
    throttle.record_failure("a")
    throttle.record_failure("a")
    assert throttle.retry_after("a") == 0


def test_addresses_are_counted_separately():
    throttle, _ = make(max_failures=2)
    throttle.record_failure("a")
    throttle.record_failure("a")
    throttle.record_failure("b")
    assert throttle.retry_after("a") > 0
    assert throttle.retry_after("b") == 0


def test_the_number_of_addresses_remembered_is_bounded():
    throttle, clock = make(max_failures=5)
    for number in range(MAX_TRACKED_ADDRESSES + 50):
        throttle.record_failure(f"10.{number // 65536}.{(number // 256) % 256}.{number % 256}")
    assert len(throttle._failures) <= MAX_TRACKED_ADDRESSES
    clock.advance(1000)
    throttle.record_failure("fresh")
    assert len(throttle._failures) == 1, "stale ones are forgotten first"


def test_a_locked_address_is_not_forgotten_when_the_table_is_cleaned():
    throttle, clock = make(max_failures=1, lockout=500)
    throttle.record_failure("locked")
    clock.advance(100)
    for number in range(MAX_TRACKED_ADDRESSES):
        throttle.record_failure(f"x{number}")  # each of these is locked too (one failure is enough)
    assert throttle.retry_after("locked") > 0


def test_settings_default_to_ten_wrong_keys_a_minute_and_a_minutes_lockout():
    assert throttle_settings(None) == (10, 60.0, 60.0)
    assert throttle_settings({}) == (10, 60.0, 60.0)
    assert throttle_settings({"api_key_throttle": {"max_failures": 3, "lockout_seconds": 5}}) == (3, 60.0, 5.0)


# --------------------------------------------------------------------------- labelled keys
def test_the_main_key_and_the_listed_keys_are_all_accepted_with_their_labels(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "main")
    keys = configured_api_keys({"security": {"api_keys": [{"label": "alice", "key": "a"}, {"label": "bob", "key": "b"}]}})
    assert keys == [("api-key", "main"), ("alice", "a"), ("bob", "b")]


def test_the_listed_keys_work_without_a_main_key(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV)
    assert configured_api_keys({"security": {"api_keys": [{"label": "alice", "key": "a"}]}}) == [("alice", "a")]
    assert configured_api_keys({"security": {}}) == []
    assert configured_api_keys(None) == []


def test_matching_names_the_key_and_refuses_a_near_miss():
    keys = [("alice", "secret-a"), ("bob", "secret-b")]
    assert match_api_key("secret-b", keys) == "bob"
    assert match_api_key("secret-a", keys) == "alice"
    assert match_api_key("secret-", keys) is None
    assert match_api_key("", keys) is None
    assert match_api_key("secret-a", []) is None
    assert match_api_key("same", [("first", "same"), ("second", "same")]) == "first", "two labels on one key: the first is the one named"


def labelled_app(config):
    class Stub:
        config_data = config
        api_throttle = None

    app = FastAPI()
    broker = Stub()

    @app.get("/who", dependencies=[Depends(make_api_key_dependency(broker))])
    async def who(request: Request):
        return {"label": request.state.api_key_label}

    return app


def test_a_call_knows_which_key_it_used(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, "main")
    client = TestClient(labelled_app({"security": {"api_keys": [{"label": "alice", "key": "a"}]}}))
    assert client.get("/who", headers={"X-API-Key": "a"}).json() == {"label": "alice"}
    assert client.get("/who", headers={"X-API-Key": "main"}).json() == {"label": "api-key"}
    assert client.get("/who", headers={"Authorization": "Bearer a"}).json() == {"label": "alice"}
    assert client.get("/who", headers={"X-API-Key": "c"}).status_code == 401


def test_an_open_api_labels_its_callers_anonymous(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV)
    client = TestClient(labelled_app({"security": {"allow_unauthenticated_api": True}}))
    assert client.get("/who").json() == {"label": "anonymous"}


# --------------------------------------------------------------------------- the whole way through the API
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": []})
    return TestClient(server.app, client=("203.0.113.9", 40000))


PATH = "/api/mongodb/health"


def test_ten_wrong_keys_lock_the_address_out_even_for_the_right_key(client):
    for _ in range(10):
        assert client.get(PATH, headers={"X-API-Key": "wrong"}).status_code == 401
    refused = client.get(PATH, headers={"X-API-Key": API_KEY})
    assert refused.status_code == 429
    assert 1 <= int(refused.headers["retry-after"]) <= 61
    assert "try again in" in refused.json()["detail"]
    assert client.get(PATH).status_code == 429, "a request with no key at all is refused too"


def test_another_address_is_not_affected(client):
    for _ in range(10):
        client.get(PATH, headers={"X-API-Key": "wrong"})
    other = TestClient(server.app, client=("198.51.100.7", 40000))
    assert other.get(PATH, headers={"X-API-Key": API_KEY}).status_code == 200


def test_requests_with_no_key_are_not_guesses_and_are_not_counted(client):
    for _ in range(30):
        assert client.get(PATH).status_code == 401
    assert client.get(PATH, headers={"X-API-Key": API_KEY}).status_code == 200


def test_a_right_key_between_mistakes_keeps_the_count_from_building_up(client):
    for _ in range(3):
        for _ in range(9):
            assert client.get(PATH, headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get(PATH, headers={"X-API-Key": API_KEY}).status_code == 200


def test_the_limits_come_from_the_configuration(monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": [], "security": {"api_key_throttle": {"max_failures": 2}}})
    client = TestClient(server.app, client=("203.0.113.10", 40000))
    assert client.get(PATH, headers={"X-API-Key": "x"}).status_code == 401
    assert client.get(PATH, headers={"X-API-Key": "x"}).status_code == 401
    assert client.get(PATH, headers={"X-API-Key": "x"}).status_code == 429


def test_the_throttle_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": [], "security": {"api_key_throttle": {"max_failures": 0}}})
    client = TestClient(server.app, client=("203.0.113.11", 40000))
    for _ in range(40):
        assert client.get(PATH, headers={"X-API-Key": "x"}).status_code == 401
    assert client.get(PATH, headers={"X-API-Key": API_KEY}).status_code == 200


def test_changed_limits_start_a_new_count(monkeypatch):
    config = {"organizations": [], "security": {"api_key_throttle": {"max_failures": 2}}}
    monkeypatch.setattr(server.broker, "config_data", config)
    client = TestClient(server.app, client=("203.0.113.12", 40000))
    client.get(PATH, headers={"X-API-Key": "x"})
    client.get(PATH, headers={"X-API-Key": "x"})
    assert client.get(PATH, headers={"X-API-Key": API_KEY}).status_code == 429
    config["security"]["api_key_throttle"]["max_failures"] = 5
    assert client.get(PATH, headers={"X-API-Key": API_KEY}).status_code == 200


def test_a_listed_key_opens_the_whole_api(monkeypatch):
    monkeypatch.setattr(server.broker, "config_data", {"organizations": [], "security": {"api_keys": [{"label": "ops", "key": "ops-key"}]}})
    client = TestClient(server.app, client=("203.0.113.13", 40000))
    assert client.get(PATH, headers={"X-API-Key": "ops-key"}).status_code == 200
    assert client.get("/api/system/info", headers={"Authorization": "Bearer ops-key"}).json()["api_auth"] == "api_key"


def test_a_listed_key_alone_counts_as_a_protected_api(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV)
    monkeypatch.setattr(server.broker, "config_data", {"organizations": [], "security": {"api_keys": [{"label": "ops", "key": "ops-key"}]}})
    client = TestClient(server.app, client=("203.0.113.14", 40000))
    assert client.get(PATH).status_code == 401, "not 503: there is a key"
    assert client.get(PATH, headers={"X-API-Key": "ops-key"}).status_code == 200


# --------------------------------------------------------------------------- configuration
def config(security):
    return {"broker": {"port": 8000}, "organizations": [], "security": security}


@pytest.mark.parametrize(
    "security, message",
    [
        ({"api_keys": "abc"}, "must be a list"),
        ({"api_keys": ["abc"]}, "needs a key"),
        ({"api_keys": [{"label": "a"}]}, "needs a key"),
        ({"api_keys": [{"label": "a", "key": 5}]}, "needs a key"),
        ({"api_keys": [{"key": "k"}]}, "needs a label"),
        ({"api_keys": [{"label": "has space", "key": "k"}]}, "needs a label"),
        ({"api_keys": [{"label": "x" * 41, "key": "k"}]}, "needs a label"),
        ({"api_keys": [{"label": "a", "key": "k"}, {"label": "a", "key": "j"}]}, "used twice"),
        ({"api_keys": [{"label": "api-key", "key": "k"}]}, "used twice"),
        ({"api_key_throttle": 5}, "must be a mapping"),
        ({"api_key_throttle": {"nothing": 1}}, "is not known"),
        ({"api_key_throttle": {"max_failures": -1}}, "0 or more"),
        ({"api_key_throttle": {"window_seconds": 0}}, "1 or more"),
        ({"api_key_throttle": {"lockout_seconds": "60"}}, "must be a number"),
        ({"api_key_throttle": {"max_failures": True}}, "must be a number"),
    ],
)
def test_keys_and_throttle_settings_that_cannot_be_used_stop_the_broker_at_startup(security, message):
    with pytest.raises(ValueError, match=message):
        _validate_config(config(security))


def test_good_settings_are_accepted():
    _validate_config(config({"api_keys": [{"label": "alice", "key": "k1"}, {"label": "ops.team-2", "key": "k2"}], "api_key_throttle": {"max_failures": 0, "window_seconds": 30, "lockout_seconds": 15}}))
