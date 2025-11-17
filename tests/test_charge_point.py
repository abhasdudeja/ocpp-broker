import pytest

from ocpp.v16 import call_result

from ocpp_broker.charge_point import BrokerChargePoint


class DummyConnection:
    async def recv(self):
        raise RuntimeError("recv should not be used in unit tests")

    async def send(self, message: str):
        self.last_sent = message

    async def close(self, code: int = 1000, reason: str | None = None):
        self.closed_with = (code, reason)


class DummyRegistry:
    def __init__(self):
        self.calls = []

    async def update_from_backend(self, backend_id: str, charger_ids):
        self.calls.append((backend_id, list(charger_ids)))


class DummyTagManager:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    async def authorize_tag(self, org_name: str, id_tag: str):
        self.calls.append((org_name, id_tag))
        return self.responses.get(
            id_tag,
            {
                "status": "Accepted",
                "expiry_date": None,
                "parent_id_tag": None,
            },
        )


class DummyBroker:
    def __init__(self):
        self.config_data = {
            "ocpp": {
                "commands": {
                    "core": {
                        "heartbeat_interval": 123,
                    }
                }
            }
        }
        self.registry = DummyRegistry()
        self.tag_manager = DummyTagManager()
        self._next_transaction = 1000

    def get_registry(self, org_name: str):
        return self.registry

    def next_transaction_id(self):
        self._next_transaction += 1
        return self._next_transaction


@pytest.mark.asyncio
async def test_boot_notification_registers_charger():
    broker = DummyBroker()
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-1")

    result = await cp.on_boot_notification(
        charge_point_model="ModelX", charge_point_vendor="VendorY"
    )

    assert isinstance(result, call_result.BootNotification)
    assert result.interval == 123
    assert broker.registry.calls == [("org-1-local", ["CP_1"])]


@pytest.mark.asyncio
async def test_authorize_uses_tag_manager_response():
    broker = DummyBroker()
    broker.tag_manager = DummyTagManager(
        responses={
            "TEST123": {
                "status": "Blocked",
                "expiry_date": "2024-01-01T00:00:00Z",
                "parent_id_tag": "ADMIN",
            }
        }
    )
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-2")

    result = await cp.on_authorize(id_tag="TEST123")

    assert isinstance(result, call_result.Authorize)
    assert result.id_tag_info.status == "Blocked"
    assert result.id_tag_info.expiry_date == "2024-01-01T00:00:00Z"
    assert result.id_tag_info.parent_id_tag == "ADMIN"
    assert broker.tag_manager.calls == [("org-2", "TEST123")]


@pytest.mark.asyncio
async def test_start_transaction_returns_generated_id_and_tag_info():
    broker = DummyBroker()
    cp = BrokerChargePoint("CP_9", DummyConnection(), broker, "org-3")

    result = await cp.on_start_transaction(connector_id=1, id_tag="TAG42")

    assert isinstance(result, call_result.StartTransaction)
    assert result.transaction_id == 1001
    assert result.id_tag_info.status == "Accepted"


@pytest.mark.asyncio
async def test_authorize_without_tag_manager_returns_invalid():
    """Test that authorization fails when tag_manager is not available"""
    broker = DummyBroker()
    broker.tag_manager = None
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-1")

    result = await cp.on_authorize(id_tag="TEST123")

    assert isinstance(result, call_result.Authorize)
    assert result.id_tag_info.status == "Invalid"


@pytest.mark.asyncio
async def test_authorize_with_empty_id_tag_returns_invalid():
    """Test that authorization fails when id_tag is empty"""
    broker = DummyBroker()
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-1")

    result = await cp.on_authorize(id_tag="")

    assert isinstance(result, call_result.Authorize)
    assert result.id_tag_info.status == "Invalid"


@pytest.mark.asyncio
async def test_authorize_with_tag_not_found_returns_invalid():
    """Test that authorization returns Invalid when tag is not found"""
    broker = DummyBroker()
    broker.tag_manager = DummyTagManager(
        responses={
            "UNKNOWN": {
                "status": "Invalid",
                "expiry_date": None,
                "parent_id_tag": None,
            }
        }
    )
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-1")

    result = await cp.on_authorize(id_tag="UNKNOWN")

    assert isinstance(result, call_result.Authorize)
    assert result.id_tag_info.status == "Invalid"


@pytest.mark.asyncio
async def test_stop_transaction_without_id_tag_returns_invalid():
    """Test that StopTransaction returns Invalid when id_tag is missing"""
    broker = DummyBroker()
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-1")

    result = await cp.on_stop_transaction(transaction_id=123)

    assert isinstance(result, call_result.StopTransaction)
    assert result.id_tag_info.status == "Invalid"


@pytest.mark.asyncio
async def test_heartbeat_returns_current_time():
    """Test that Heartbeat returns current time"""
    broker = DummyBroker()
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-1")

    result = await cp.on_heartbeat()

    assert isinstance(result, call_result.Heartbeat)
    assert result.current_time is not None
    assert "T" in result.current_time  # ISO format check


@pytest.mark.asyncio
async def test_status_notification_returns_empty_payload():
    """Test that StatusNotification returns empty payload"""
    broker = DummyBroker()
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-1")

    result = await cp.on_status_notification(
        connector_id=1, status="Available"
    )

    assert isinstance(result, call_result.StatusNotification)


@pytest.mark.asyncio
async def test_meter_values_returns_empty_payload():
    """Test that MeterValues returns empty payload"""
    broker = DummyBroker()
    cp = BrokerChargePoint("CP_1", DummyConnection(), broker, "org-1")

    result = await cp.on_meter_values(
        connector_id=1, meter_value=[]
    )

    assert isinstance(result, call_result.MeterValues)

