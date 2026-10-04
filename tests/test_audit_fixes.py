"""
Things that used to be wrong, from the audit of the first release: the configuration file the server reads,
loading it once, the log level, the failover watcher, error messages for bad commands, and MongoDB that is
down when the broker starts.
"""

import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest
import yaml

from ocpp_broker import broker as broker_module
from ocpp_broker import server
from ocpp_broker.broker import OcppBroker
from ocpp_broker.config import load_config
from ocpp_broker.data_transfer_handler import MAX_REMEMBERED_TRANSFERS, DataTransferHandler
from ocpp_broker.session import explain_validation_error

from .fakes import AUTH_HEADERS, ScriptedBackend, ScriptedCharger, fake_mongo_service


def write_config(path, **extra):
    path.write_text(yaml.safe_dump({"broker": {"host": "127.0.0.1", "port": 8123}, "organizations": [{"name": "O", "connect_to_backend": False}], **extra}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Which configuration file
# ---------------------------------------------------------------------------
def test_a_file_named_with_c_wins_over_the_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_BROKER_CONFIG", str(tmp_path / "from-env.yaml"))
    assert server.resolve_config_path(str(tmp_path / "from-flag.yaml")) == (tmp_path / "from-flag.yaml", True)


def test_the_environment_variable_names_the_file_when_there_is_no_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_BROKER_CONFIG", str(tmp_path / "from-env.yaml"))
    assert server.resolve_config_path(None) == (tmp_path / "from-env.yaml", True)


def test_without_either_the_current_folders_config_is_used_then_the_projects(tmp_path, monkeypatch):
    monkeypatch.delenv("OCPP_BROKER_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    path, asked = server.resolve_config_path(None)
    assert asked is False and path != tmp_path / "config.yaml", "not here: the project's own"
    write_config(tmp_path / "config.yaml")
    assert server.resolve_config_path(None) == (tmp_path / "config.yaml", False)


def test_the_environment_variable_is_really_used_to_load(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_BROKER_CONFIG", str(write_config(tmp_path / "mine.yaml")))
    cfg = server.load_broker_config(None)
    assert cfg["broker"]["port"] == 8123 and cfg["_path"].endswith("mine.yaml")


def test_a_file_asked_for_by_name_that_is_missing_is_an_error_not_a_silent_default(tmp_path, monkeypatch):
    monkeypatch.delenv("OCPP_BROKER_CONFIG", raising=False)
    with pytest.raises(FileNotFoundError, match="nope.yaml"):
        server.load_broker_config(str(tmp_path / "nope.yaml"))
    monkeypatch.setenv("OCPP_BROKER_CONFIG", str(tmp_path / "also-nope.yaml"))
    with pytest.raises(FileNotFoundError, match="also-nope.yaml"):
        server.load_broker_config(None)


def test_the_command_line_exits_with_a_clear_message_when_the_named_file_is_missing(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="ocpp_broker.server"), pytest.raises(SystemExit) as stopped:
        server.run_broker_server(str(tmp_path / "nope.yaml"))
    assert stopped.value.code == 2 and "Configuration file not found" in caplog.text and "nope.yaml" in caplog.text


def test_environment_overrides_apply_when_there_is_no_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_PORT", "9911")
    monkeypatch.setattr(server.Path, "exists", lambda self: False)  # no config.yaml anywhere
    monkeypatch.delenv("OCPP_BROKER_CONFIG", raising=False)
    assert server.load_broker_config(None)["broker"]["port"] == 9911


# ---------------------------------------------------------------------------
# One load
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_configuration_the_server_loaded_is_used_without_reading_the_file_again(monkeypatch):
    loads = []
    monkeypatch.setattr(broker_module, "load_config", lambda path: loads.append(path) or {"organizations": []})
    broker = OcppBroker()
    await broker.load_config({"organizations": [{"name": "Given"}], "mongodb": {"enabled": False}})
    assert loads == [] and broker.config_data["organizations"][0]["name"] == "Given"
    await broker.load_config()
    assert loads == ["config.yaml"], "without one it still reads the file"


@pytest.mark.asyncio
async def test_main_async_hands_the_loaded_configuration_to_the_broker(monkeypatch):
    seen = []

    async def record(config=None):
        seen.append(config)

    class NoServer:
        def __init__(self, config):
            pass

        async def serve(self):
            return None

    monkeypatch.setattr(server.broker, "load_config", record)
    monkeypatch.setattr(server, "BrokerServer", NoServer)
    monkeypatch.setattr(server, "apply_cors", lambda *a, **k: None)
    cfg = {"broker": {"host": "127.0.0.1", "port": 1}, "logging": {"level": "INFO"}, "organizations": [], "_path": "x.yaml"}
    root = logging.getLogger().level
    try:
        await server.main_async(cfg)
    finally:
        logging.getLogger().setLevel(root)
    assert seen == [cfg], "the configuration that was loaded, not a second read of the file"


# ---------------------------------------------------------------------------
# The log level
# ---------------------------------------------------------------------------
@pytest.fixture
def root_level():
    root = logging.getLogger()
    before = root.level
    yield root
    root.setLevel(before)


@pytest.mark.parametrize("name, expected", [("DEBUG", logging.DEBUG), ("warning", logging.WARNING), ("ERROR", logging.ERROR), ("INFO", logging.INFO)])
def test_the_configured_log_level_is_applied(root_level, name, expected):
    server.apply_log_level({"logging": {"level": name}})
    assert root_level.level == expected


def test_a_config_without_a_level_means_info(root_level):
    root_level.setLevel(logging.ERROR)
    server.apply_log_level({})
    assert root_level.level == logging.INFO


def test_the_environment_variable_sets_the_level_in_the_loaded_config(tmp_path, monkeypatch):
    path = write_config(tmp_path / "c.yaml", logging={"level": "WARNING"})
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert load_config(str(path))["logging"]["level"] == "DEBUG"
    monkeypatch.setenv("LOG_LEVEL", "loud")
    assert load_config(str(path))["logging"]["level"] == "WARNING", "an unknown name in the environment is ignored"


def test_an_unknown_level_in_the_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    with pytest.raises(ValueError, match="logging.level must be"):
        load_config(str(write_config(tmp_path / "c.yaml", logging={"level": "LOUD"})))


# ---------------------------------------------------------------------------
# Small things
# ---------------------------------------------------------------------------
def test_tags_not_being_configured_is_not_announced_as_tag_management_being_disabled(caplog):
    broker = OcppBroker()
    with caplog.at_level(logging.INFO, logger="ocpp_broker.broker"):
        broker._ensure_tag_manager()
    assert "Tag management disabled" not in caplog.text


@pytest.mark.asyncio
async def test_the_remembered_data_transfers_are_bounded_and_the_latest_still_come_back():
    handler = DataTransferHandler(SimpleNamespace(config_data={}))
    for n in range(MAX_REMEMBERED_TRANSFERS + 50):
        handler.received_transfers.append({"charger_id": "CP1" if n % 2 else "CP2", "vendor_id": "V", "message_id": str(n)})
    assert len(handler.received_transfers) == MAX_REMEMBERED_TRANSFERS
    latest = handler.get_transfer_history(limit=3)
    assert [t["message_id"] for t in latest] == [str(MAX_REMEMBERED_TRANSFERS + 47 + i) for i in range(3)]
    mine = handler.get_transfer_history(charger_id="CP1", limit=2)
    assert all(t["charger_id"] == "CP1" for t in mine) and len(mine) == 2


# ---------------------------------------------------------------------------
# Messages for a command that is wrong
# ---------------------------------------------------------------------------
class Violation(Exception):
    def __init__(self, description, cause=None):
        super().__init__(description)
        self.description = description
        self.details = {"cause": cause} if cause else {}


def test_the_reason_is_the_first_line_without_the_schema_dump():
    cause = "Payload '{'type': 'Medium'}' for action 'Reset' is not valid: 'Medium' is not one of ['Hard', 'Soft']\n\nFailed validating 'enum' in schema['properties']['type']:\n    {...}"
    assert explain_validation_error(Violation("syntactically incorrect", cause)) == "'Medium' is not one of ['Hard', 'Soft']"


def test_a_cause_without_the_usual_prefix_is_given_as_it_is():
    assert explain_validation_error(Violation("incomplete", "'type' is a required property")) == "'type' is a required property"


def test_without_a_cause_the_description_is_used():
    assert explain_validation_error(Violation("Something is wrong")) == "Something is wrong"
    assert explain_validation_error(ValueError("plain")) == "plain"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"type": "Medium"}, "'Medium' is not one of ['Hard', 'Soft']"),
        ({}, "missing 1 required positional argument: 'type'"),
    ],
)
async def test_the_rest_error_for_a_bad_command_is_short_and_readable(run_server, payload, reason):
    port = await run_server({"organizations": [{"name": "Home", "connect_to_backend": False}]})
    charger = await ScriptedCharger.connect(port, "Home", "CP1")
    try:
        await asyncio.sleep(0.2)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS) as client:
            response = await client.post("/api/ocpp/organizations/Home/chargers/CP1/commands", json={"action": "Reset", "payload": payload})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail.startswith("Invalid payload for Reset: ") and reason in detail
        assert "<Call" not in detail and "Failed validating" not in detail and len(detail) < 160, detail
    finally:
        await charger.close()


# ---------------------------------------------------------------------------
# Failover when no follower is healthy at first
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_follower_that_becomes_healthy_after_the_timeout_is_promoted_within_a_second_not_a_whole_timeout_later(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    follower_port = follower.port
    await follower.stop()  # the only follower is down when the leader fails
    org = {"name": "Fleet", "connect_to_backend": True, "leader_failover_timeout": 3, "backends": [{"id": "lead", "url": leader.url, "leader": True}, {"id": "follow", "url": f"ws://127.0.0.1:{follower_port}/ocpp"}]}
    port = await run_server({"organizations": [org]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        await asyncio.sleep(0.5)
        loop = asyncio.get_running_loop()
        await leader.stop()
        await asyncio.sleep(3.6)  # past the timeout: nobody to promote
        assert server.broker.sessions[("Fleet", "CP1")].backend_conn.url == leader.url.rstrip("/")
        healthy_at = loop.time()
        await follower.start(follower_port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS) as client:
            for _ in range(80):
                if (await client.get("/api/chargers/Fleet/CP1")).json()["leader"]["key"] == "follow":
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("never promoted")
        assert loop.time() - healthy_at < 2.2, "within about a second of the follower being healthy (plus its reconnect), not after another full timeout"
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


@pytest.mark.asyncio
async def test_a_leader_that_comes_back_within_the_timeout_keeps_its_place(run_server):
    leader, follower = await ScriptedBackend().start(), await ScriptedBackend().start()
    leader_port = leader.port
    org = {"name": "Fleet", "connect_to_backend": True, "leader_failover_timeout": 3, "backends": [{"id": "lead", "url": leader.url, "leader": True}, {"id": "follow", "url": follower.url}]}
    port = await run_server({"organizations": [org]})
    charger = await ScriptedCharger.connect(port, "Fleet", "CP1")
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS) as client:
            await asyncio.sleep(0.6)
            await leader.stop()
            await asyncio.sleep(1.2)  # well inside the timeout, and the follower is healthy: still no promotion
            await leader.start(leader_port)
            await asyncio.sleep(4.0)  # long past the original deadline
            detail = (await client.get("/api/chargers/Fleet/CP1")).json()
        assert detail["leader"]["key"] == "lead" and detail["leader"]["connected"] is True
    finally:
        await charger.close()
        await leader.stop()
        await follower.stop()


# ---------------------------------------------------------------------------
# MongoDB that is down when the broker starts
# ---------------------------------------------------------------------------
@pytest.fixture
def quick_retries(monkeypatch):
    monkeypatch.setattr(broker_module, "MONGODB_RETRY_FIRST", 0.02)
    monkeypatch.setattr(broker_module, "MONGODB_RETRY_MAX", 0.05)


@pytest.fixture
def flaky_mongodb(monkeypatch):
    """MongoDBService whose connect() fails until ``state['up']`` is set."""
    from pymongo.errors import ServerSelectionTimeoutError

    from ocpp_broker import mongodb_service

    state = {"up": False, "attempts": 0}

    async def connect(self):
        state["attempts"] += 1
        if not state["up"]:
            raise ServerSelectionTimeoutError("no server")
        self._connected = True
        self.db = fake_mongo_service().db

    monkeypatch.setattr(mongodb_service.MongoDBService, "connect", connect)
    return state


@pytest.mark.asyncio
async def test_a_mongodb_that_is_down_at_startup_is_retried_and_switched_on_when_it_answers(quick_retries, flaky_mongodb, caplog):
    broker = OcppBroker()
    broker.config_data = {"mongodb": {"enabled": True, "connection_string": "mongodb://nowhere", "database_name": "d"}, "organizations": [{"name": "Home", "tags": [{"id_tag": "T1", "status": "Accepted"}]}]}
    broker._ensure_tag_manager()
    with caplog.at_level(logging.INFO, logger="ocpp_broker.broker"):
        await broker._initialize_mongodb()
        assert broker.mongodb_service is None and broker._mongo_retry is not None
        await asyncio.sleep(0.3)
        assert flaky_mongodb["attempts"] >= 3, "it keeps trying"
        assert broker.mongodb_service is None
        flaky_mongodb["up"] = True
        await asyncio.wait_for(broker._mongo_retry, 2)
    assert broker.mongodb_service is not None and broker.mongodb_service.is_connected()
    assert broker._presence_indexed is False
    assert broker.tag_manager.mongodb_service is broker.mongodb_service and broker.tag_manager._use_mongodb is True
    assert "MongoDB is available now" in caplog.text
    stored = broker.mongodb_service.db["tags"].docs
    assert [d["id_tag"] for d in stored] == ["T1"], "the tags loaded from the configuration were pushed into MongoDB"


@pytest.mark.asyncio
async def test_only_the_first_failure_is_logged_loudly(quick_retries, flaky_mongodb, caplog):
    broker = OcppBroker()
    broker.config_data = {"mongodb": {"enabled": True}}
    with caplog.at_level(logging.DEBUG, logger="ocpp_broker.broker"):
        await broker._initialize_mongodb()
        await asyncio.sleep(0.3)
        broker.stop_mongodb_retry()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "Failed to initialize MongoDB" in errors[0].getMessage()
    assert flaky_mongodb["attempts"] >= 4, "and it kept trying"


@pytest.mark.asyncio
async def test_no_retry_is_started_when_mongodb_is_not_enabled_or_when_it_connects_at_once(quick_retries, flaky_mongodb, monkeypatch):
    monkeypatch.delenv("MONGODB_ENABLED", raising=False)
    broker = OcppBroker()
    broker.config_data = {"mongodb": {"enabled": False}}
    await broker._initialize_mongodb()
    assert broker._mongo_retry is None and flaky_mongodb["attempts"] == 0

    flaky_mongodb["up"] = True
    broker.config_data = {"mongodb": {"enabled": True}}
    await broker._initialize_mongodb()
    assert broker._mongo_retry is None and broker.mongodb_service is not None


@pytest.mark.asyncio
async def test_the_retry_can_be_stopped(quick_retries, flaky_mongodb):
    broker = OcppBroker()
    broker.config_data = {"mongodb": {"enabled": True}}
    await broker._initialize_mongodb()
    task = broker._mongo_retry
    broker.stop_mongodb_retry()
    await asyncio.sleep(0.05)
    assert task.cancelled() and broker._mongo_retry is None
    attempts = flaky_mongodb["attempts"]
    await asyncio.sleep(0.2)
    assert flaky_mongodb["attempts"] == attempts, "no more attempts"
    broker.stop_mongodb_retry()  # harmless when there is nothing to stop


@pytest.mark.asyncio
async def test_the_server_stops_the_retry_when_it_shuts_down(quick_retries, flaky_mongodb, monkeypatch):
    import socket

    monkeypatch.setattr(server.broker, "config_data", {"organizations": [], "mongodb": {"enabled": True}})
    await server.broker._initialize_mongodb()
    task = server.broker._mongo_retry
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    uv = server.BrokerServer(server.build_uvicorn_config(server.app, {"broker": {"host": "127.0.0.1", "port": sock.getsockname()[1]}}))
    uv.config.log_level = "warning"
    serving = asyncio.create_task(uv.serve(sockets=[sock]))
    while not uv.started:
        await asyncio.sleep(0.01)
    uv.should_exit = True
    await asyncio.wait_for(serving, 10)
    assert task.cancelled() or task.done()
    server.broker._mongo_retry = None


@pytest.mark.asyncio
async def test_the_mongodb_health_route_asks_the_server_now(run_server, monkeypatch):
    class Mongo:
        database_name = "d"

        def __init__(self, answers):
            self.answers = answers

        def is_connected(self):
            return True

        async def ping(self):
            return self.answers

    port = await run_server({"organizations": []})
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH_HEADERS) as client:
        monkeypatch.setattr(server.broker, "mongodb_service", Mongo(True), raising=False)
        assert (await client.get("/api/mongodb/health")).json() == {"status": "connected", "connected": True, "database": "d"}
        monkeypatch.setattr(server.broker, "mongodb_service", Mongo(False), raising=False)
        assert (await client.get("/api/mongodb/health")).json() == {"status": "disconnected", "connected": False, "database": "d"}
        monkeypatch.setattr(server.broker, "mongodb_service", None, raising=False)
        assert (await client.get("/api/mongodb/health")).json() == {"status": "not_configured", "connected": False}
