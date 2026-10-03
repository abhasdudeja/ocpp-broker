"""
Backend entries in the configuration: `id` is only a label, and the leader is chosen predictably.
"""

import yaml

from ocpp_broker.config import load_config


def _load(tmp_path, backends):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"organizations": [{"name": "A", "connect_to_backend": True, "backends": backends}]}))
    return load_config(str(path))["organizations"][0]["backends"]


def test_backend_without_id_or_leader_flag_loads_and_becomes_leader(tmp_path):
    backends = _load(tmp_path, [{"url": "ws://a.example.com/ocpp"}, {"url": "ws://b.example.com/ocpp"}])

    assert [b.get("leader") for b in backends] == [True, None]


def test_first_backend_is_leader_when_none_is_flagged(tmp_path):
    backends = _load(tmp_path, [{"id": "a", "url": "ws://a/x"}, {"id": "b", "url": "ws://b/x"}])

    assert [b.get("leader") for b in backends] == [True, None]


def test_only_the_first_flagged_leader_stays_leader_even_if_entries_are_identical(tmp_path):
    same = {"id": "dup", "url": "ws://same/x", "leader": True}
    backends = _load(tmp_path, [dict(same), dict(same), {"id": "c", "url": "ws://c/x"}])

    assert [b["leader"] for b in backends[:2]] == [True, False]
