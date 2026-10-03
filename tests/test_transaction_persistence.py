"""
StartTransaction / StopTransaction persistence.

Uses the real MongoDBService against an in-memory fake database, so these
tests see exactly which collections get written and what ends up stored.
"""

from datetime import datetime, timezone

import pytest

from ocpp_broker.charge_point import BrokerChargePoint
from ocpp_broker.mongodb_service import MongoDBService


class FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.writes = 0

    async def insert_one(self, doc):
        self.writes += 1
        self.docs.append(dict(doc))

    async def update_one(self, flt, update, upsert=False):
        self.writes += 1
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in flt.items()):
                doc.update(update.get("$set", {}))
                return
        if upsert:
            self.docs.append({**flt, **update.get("$set", {})})


class FakeDB(dict):
    def __missing__(self, name):
        self[name] = FakeCollection()
        return self[name]


@pytest.fixture
def mongo():
    service = MongoDBService("mongodb://unused")
    service._connected = True
    service.db = FakeDB()
    return service


class _Tags:
    async def authorize_tag(self, org_name, id_tag):
        return {"status": "Accepted"}


class _Broker:
    def __init__(self, mongodb_service):
        self.config_data = {}
        self.tag_manager = _Tags()
        self.mongodb_service = mongodb_service
        self._tx = 0

    def next_transaction_id(self):
        self._tx += 1
        return self._tx


class _Conn:
    async def send(self, message):
        pass


def _charge_point(mongo):
    return BrokerChargePoint("CP_1", _Conn(), _Broker(mongo), "org-1")


@pytest.mark.asyncio
async def test_stop_records_meter_stop_without_clobbering_start_fields(mongo):
    cp = _charge_point(mongo)
    started = await cp.on_start_transaction(
        connector_id=2, id_tag="TAG1", meter_start=100, timestamp="2026-01-01T10:00:00Z"
    )

    await cp.on_stop_transaction(
        transaction_id=started.transaction_id,
        id_tag="TAG1",
        meter_stop=250,
        timestamp="2026-01-01T11:30:00Z",
        reason="EVDisconnected",
    )

    (doc,) = mongo.db["transactions"].docs
    assert doc["meter_start"] == 100, "stop must not overwrite meter_start"
    assert doc["meter_stop"] == 250
    assert doc["connector_id"] == 2, "stop must not reset connector_id"
    assert doc["id_tag"] == "TAG1"
    assert doc["transaction_type"] == "stop"
    assert doc["stop_reason"] == "EVDisconnected"
    assert doc["stop_timestamp"] == datetime(2026, 1, 1, 11, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_stop_without_recorded_start_is_stored_as_partial_document(mongo):
    cp = _charge_point(mongo)

    await cp.on_stop_transaction(transaction_id=77, meter_stop=500, timestamp="2026-01-01T11:30:00Z")

    (doc,) = mongo.db["transactions"].docs
    assert doc["transaction_id"] == 77
    assert doc["meter_stop"] == 500
    assert "meter_start" not in doc


@pytest.mark.asyncio
async def test_each_message_is_written_to_exactly_one_collection(mongo):
    """Previously every call was also logged raw into start_transactions/etc."""
    cp = _charge_point(mongo)

    await cp.on_boot_notification(charge_point_model="M", charge_point_vendor="V")
    await cp.on_authorize(id_tag="TAG1")
    await cp.on_status_notification(connector_id=1, status="Available")
    await cp.on_meter_values(connector_id=1, meter_value=[{"sampled_value": []}])
    started = await cp.on_start_transaction(connector_id=1, id_tag="TAG1", meter_start=0)
    await cp.on_stop_transaction(transaction_id=started.transaction_id, meter_stop=5)

    written = {name: c.writes for name, c in mongo.db.items() if c.writes}
    assert set(written) == {
        "charger_configurations",
        "authorizations",
        "charger_statuses",
        "charger_statuses_latest",
        "meter_values",
        "transactions",
    }, written
    assert written["transactions"] == 2  # one insert (start) + one update (stop)


@pytest.mark.asyncio
async def test_status_notifications_without_a_dedicated_store_are_still_persisted(mongo):
    cp = _charge_point(mongo)

    await cp.on_diagnostics_status_notification(status="Uploaded")
    await cp.on_firmware_status_notification(status="Installed")

    assert mongo.db["diagnostics_status_notifications"].docs[0]["payload"] == {"status": "Uploaded"}
    assert mongo.db["firmware_status_notifications"].docs[0]["payload"] == {"status": "Installed"}


def test_helper_for_double_writes_is_gone():
    assert not hasattr(BrokerChargePoint, "_save_to_mongodb_if_enabled")
