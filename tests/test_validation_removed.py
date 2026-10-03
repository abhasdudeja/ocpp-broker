"""
The custom message validator is gone: the ocpp library validates in broker
mode and relay mode forwards frames untouched.
"""

import asyncio
import importlib.util
import logging

import pytest
import yaml

from ocpp_broker.broker import OcppBroker
from ocpp_broker.config import load_config
from ocpp_broker.middleware import process_charger_to_backend

from .fakes import FakeCharger


def test_message_validator_module_is_gone():
    assert importlib.util.find_spec("ocpp_broker.message_validator") is None


@pytest.mark.asyncio
async def test_relay_forwards_frames_unchanged_even_if_not_valid_ocpp():
    for frame in ('[2, "1", "NotAnAction", {"junk": true}]', "definitely not json"):
        forwarded, _ = await process_charger_to_backend("CP1", frame)
        assert forwarded == frame


@pytest.mark.asyncio
async def test_broker_mode_still_rejects_schema_violations_via_the_ocpp_library():
    broker = OcppBroker()
    broker.config_data = {"organizations": [{"name": "OrgA", "connect_to_backend": False}]}
    charger = FakeCharger()
    task = asyncio.create_task(broker.handle_charger(charger, "/OrgA/CP1"))
    for _ in range(100):
        if ("OrgA", "CP1") in broker.sessions:
            break
        await asyncio.sleep(0.01)

    # BootNotification requires chargePointVendor and chargePointModel.
    charger.deliver([2, "bad-1", "BootNotification", {}])
    reply = await charger.next_reply()

    assert reply[0] == 4 and reply[1] == "bad-1", reply

    await charger.close()
    await task


def test_removed_org_settings_produce_a_warning_not_a_silent_noop(tmp_path, caplog):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "organizations": [
                    {
                        "name": "orgA",
                        "connect_to_backend": False,
                        "validate_messages_when_backend_leader": True,
                        "validation": {"strict_mode": True},
                    }
                ]
            }
        )
    )

    with caplog.at_level(logging.WARNING, logger="ocpp_broker.config"):
        cfg = load_config(str(cfg_path))

    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "validate_messages_when_backend_leader" in warned
    assert "'validation'" in warned
    assert "validate_messages_when_broker_backend" not in warned  # not present, not reported

    org = cfg["organizations"][0]
    assert "validate_messages_when_broker_backend" not in org, "no default is injected any more"


def test_shipped_config_does_not_use_removed_settings():
    from pathlib import Path

    shipped = Path(__file__).resolve().parents[1] / "config.yaml"
    text = shipped.read_text(encoding="utf-8")
    assert "validate_messages" not in text
    assert "strict_mode" not in yaml.safe_load(text)["organizations"][0].get("validation", {})
