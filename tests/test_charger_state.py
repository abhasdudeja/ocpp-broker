"""
ChargerState: what the console knows about a connected charger, learned by watching its frames.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ocpp_broker.charger_state import ChargerState, remote_address


def call(action, payload, mid="m1"):
    return json.dumps([2, mid, action, payload])


BOOT = {
    "chargePointVendor": "Acme",
    "chargePointModel": "Wallbox 7",
    "chargePointSerialNumber": "SN-7",
    "firmwareVersion": "2.1.0",
    "iccid": "8931",
    "imsi": "2040",
    "meterType": "MT",
    "meterSerialNumber": "MSN",
}


def test_every_frame_is_counted_and_marks_the_charger_as_seen():
    state = ChargerState()
    assert state.last_seen is None and state.frames_in == 0
    state.observe(call("Authorize", {"idTag": "X"}))
    state.observe("not json at all")
    state.observe(json.dumps({"a": 1}))
    assert state.frames_in == 3 and state.last_seen is not None


def test_a_boot_notification_gives_the_identity():
    state = ChargerState()
    state.observe(call("BootNotification", BOOT))
    boot = state.boot
    assert (boot.vendor, boot.model, boot.serial_number, boot.firmware_version) == ("Acme", "Wallbox 7", "SN-7", "2.1.0")
    assert (boot.iccid, boot.imsi, boot.meter_type, boot.meter_serial_number) == ("8931", "2040", "MT", "MSN")
    assert boot.received_at.tzinfo is not None


def test_a_later_boot_replaces_the_earlier_one_and_missing_fields_are_none():
    state = ChargerState()
    state.observe(call("BootNotification", BOOT))
    state.observe(call("BootNotification", {"chargePointVendor": "Acme", "chargePointModel": "Wallbox 8"}))
    assert state.boot.model == "Wallbox 8" and state.boot.firmware_version is None and state.boot.iccid is None


def test_status_notifications_build_the_connector_list_in_order():
    state = ChargerState()
    state.observe(call("StatusNotification", {"connectorId": 2, "status": "Charging", "errorCode": "NoError"}))
    state.observe(call("StatusNotification", {"connectorId": 0, "status": "Available", "errorCode": "NoError"}))
    state.observe(call("StatusNotification", {"connectorId": 1, "status": "Faulted", "errorCode": "GroundFailure", "info": "RCD"}))
    state.observe(call("StatusNotification", {"connectorId": 2, "status": "Finishing", "errorCode": "NoError"}))
    connectors = state.ordered_connectors()
    assert [(c.connector_id, c.status) for c in connectors] == [(0, "Available"), (1, "Faulted"), (2, "Finishing")]
    assert (connectors[1].error_code, connectors[1].info) == ("GroundFailure", "RCD")


def test_the_chargers_own_timestamp_is_used_when_it_is_readable():
    state = ChargerState()
    state.observe(call("StatusNotification", {"connectorId": 1, "status": "Available", "timestamp": "2026-10-04T10:15:00Z"}))
    assert state.connectors[1].updated_at == datetime(2026, 10, 4, 10, 15, tzinfo=timezone.utc)
    state.observe(call("StatusNotification", {"connectorId": 2, "status": "Available", "timestamp": "2026-10-04T10:15:00"}))
    assert state.connectors[2].updated_at.tzinfo is not None, "a timestamp without a zone counts as UTC"


@pytest.mark.parametrize("timestamp", ["yesterday", 5, None, "2026-13-45T00:00:00Z"])
def test_an_unreadable_timestamp_falls_back_to_when_it_arrived(timestamp):
    state = ChargerState()
    before = datetime.now(timezone.utc)
    state.observe(call("StatusNotification", {"connectorId": 1, "status": "Available", "timestamp": timestamp}))
    assert state.connectors[1].updated_at >= before


@pytest.mark.parametrize(
    "payload",
    [
        {"connectorId": "1", "status": "Available"},
        {"connectorId": True, "status": "Available"},
        {"connectorId": 1.5, "status": "Available"},
        {"connectorId": 1},
        {"connectorId": 1, "status": 7},
        {},
    ],
)
def test_a_status_notification_that_does_not_make_sense_is_ignored(payload):
    state = ChargerState()
    state.observe(call("StatusNotification", payload))
    assert state.connectors == {} and state.frames_in == 1


def test_a_heartbeat_is_remembered():
    state = ChargerState()
    assert state.last_heartbeat_at is None
    state.observe(call("Heartbeat", {}))
    assert state.last_heartbeat_at is not None


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps([3, "m1", {"status": "Accepted"}]),
        json.dumps([4, "m1", "NotImplemented", "", {}]),
        json.dumps([2, "m1", "BootNotification"]),
        json.dumps([2, "m1", "BootNotification", "not a dict"]),
        json.dumps([2, "m1", "StatusNotification", None]),
        json.dumps([]),
        json.dumps("text"),
        "",
        None,
    ],
)
def test_frames_that_are_not_charger_requests_change_nothing_but_the_count(raw):
    state = ChargerState()
    state.observe(raw)
    assert state.boot is None and state.connectors == {} and state.last_heartbeat_at is None
    assert state.frames_in == 1


def test_frames_sent_to_the_charger_are_counted():
    state = ChargerState()
    state.note_sent()
    state.note_sent()
    assert state.frames_out == 2


def test_the_connection_time_is_when_the_state_was_created():
    before = datetime.now(timezone.utc)
    assert ChargerState().connected_at >= before


@pytest.mark.parametrize(
    "websocket,expected",
    [
        (SimpleNamespace(client=SimpleNamespace(host="10.0.0.5", port=51234)), "10.0.0.5:51234"),
        (SimpleNamespace(client=SimpleNamespace(host="10.0.0.5", port=None)), "10.0.0.5"),
        (SimpleNamespace(client=None), None),
        (SimpleNamespace(), None),
    ],
)
def test_the_remote_address_is_host_and_port_when_known(websocket, expected):
    assert remote_address(websocket) == expected
