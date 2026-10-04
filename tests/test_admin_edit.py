"""
What an admin change does to the organizations of the configuration file (admin_edit.py), the file store that
writes them (config_store.py) and the audit log (audit.py).
"""

import json
import os
import stat
import sys

import pytest
import yaml

from ocpp_broker.admin_edit import apply_changes, build_org, describe, normalized, view
from ocpp_broker.audit import KEEP, AuditLog
from ocpp_broker.auth import hash_password, verify_password
from ocpp_broker.config_store import Conflict, ConfigError, FileConfigStore, NotWritable, check
from ocpp_broker.schemas.admin import AdminChange, AdminOrgInput

HASH = hash_password("existing-key-0123456789", iterations=1000)


def org_input(name="Fleet", **fields):
    return AdminOrgInput(name=name, **fields)


def upsert(**fields):
    return AdminChange(op="upsert", org=org_input(**fields))


def built(inp, existing=None):
    errors, warnings = [], []
    org = build_org(inp, existing, errors, warnings)
    return org, errors, warnings


EXISTING = {
    "name": "Fleet",
    "connect_to_backend": True,
    "ocpp_subprotocol": "ocpp1.6",
    "tags": [{"id_tag": "T1", "status": "Accepted"}],
    "tag_management": {"enabled": True},
    "chargers": ["CP1"],
    "color": "blue",  # something written by hand that the console does not know
    "backends": [
        {"id": "primary", "url": "ws://a.example/ocpp", "leader": True, "headers": {"x": "1"}},
        {"id": "standby", "url": "ws://b.example/ocpp"},
    ],
    "charger_auth": {"credentials": {"CP1": {"password_hash": HASH}, "CP2": {"password": "plain-key-0123456789"}}},
}


def same_backends():
    return [
        {"id": "primary", "url": "ws://a.example/ocpp", "leader": True},
        {"id": "standby", "url": "ws://b.example/ocpp"},
    ]


# --------------------------------------------------------------------------- building an organization
def test_a_new_organization_holds_only_what_was_asked():
    org, errors, _ = built(org_input("Newco", connect_to_backend=False))
    assert org == {"name": "Newco", "connect_to_backend": False}
    assert errors == []


def test_changing_an_organization_keeps_what_the_console_does_not_edit():
    inp = org_input(connect_to_backend=True, backends=[dict(b, leader=b.get("leader")) for b in same_backends()], credentials=[{"charger_id": "CP1"}, {"charger_id": "CP2"}])
    org, errors, _ = built(inp, EXISTING)
    assert errors == []
    assert org["tags"] == EXISTING["tags"] and org["tag_management"] == {"enabled": True}
    assert org["chargers"] == ["CP1"] and org["color"] == "blue"


def test_a_setting_left_empty_is_removed_so_the_default_applies_again():
    existing = {**EXISTING, "backend_buffer_size": 50, "leader_failover_timeout": 3, "ocpp_subprotocol": "ocpp2.0.1", "transaction_ids": {"mapping": True, "unknown_future": 5}}
    org, _, _ = built(org_input(), existing)
    assert "backend_buffer_size" not in org and "leader_failover_timeout" not in org and "ocpp_subprotocol" not in org
    assert org["transaction_ids"] == {"unknown_future": 5}, "an unknown setting stays; the known one that was emptied goes"


def test_settings_given_are_written_as_numbers():
    org, _, _ = built(org_input(backend_buffer_size=0, backend_outage_timeout=45, leader_failover_timeout=0, ocpp_subprotocol="ocpp1.6"))
    assert (org["backend_buffer_size"], org["backend_outage_timeout"], org["leader_failover_timeout"]) == (0, 45, 0)
    assert org["ocpp_subprotocol"] == "ocpp1.6"


def test_transaction_id_settings_are_written_when_given():
    org, _, _ = built(org_input(transaction_ids={"mapping": False, "retain_open": 600}))
    assert org["transaction_ids"] == {"mapping": False, "retain_open": 600}


def test_backends_are_written_the_short_way():
    org, _, _ = built(
        org_input(
            backends=[
                {"id": "mine", "local": True, "leader": False},
                {"id": "main", "url": "ws://x.example/o", "leader": True, "ocpp_subprotocol": "ocpp1.6"},
                {"url": "ws://y.example/o", "leader": False, "ocpp_subprotocol": "ocpp2.0.1"},
            ]
        )
    )
    assert org["backends"] == [
        {"id": "mine", "local": True, "leader": False},
        {"id": "main", "url": "ws://x.example/o", "leader": True},
        {"url": "ws://y.example/o", "ocpp_subprotocol": "ocpp2.0.1"},
    ], "a leader is marked, a follower is not, a local standby says false, the organization's own subprotocol is not repeated"


def test_a_backend_keeps_the_settings_written_by_hand_that_the_console_does_not_know():
    org, _, _ = built(org_input(backends=[{"id": "primary", "url": "ws://changed.example/ocpp", "leader": True}]), EXISTING)
    assert org["backends"] == [{"id": "primary", "url": "ws://changed.example/ocpp", "leader": True, "headers": {"x": "1"}}]


def test_a_new_password_is_stored_as_a_hash_and_never_as_text():
    org, errors, warnings = built(org_input(credentials=[{"charger_id": "CP9", "password": "a-long-key-0123456789"}]))
    entry = org["charger_auth"]["credentials"]["CP9"]
    assert errors == [] and warnings == []
    assert set(entry) == {"password_hash"} and verify_password("a-long-key-0123456789", entry["password_hash"])
    assert "a-long-key-0123456789" not in json.dumps(org)


def test_a_credential_without_a_password_keeps_what_the_file_has():
    org, errors, _ = built(org_input(credentials=[{"charger_id": "CP1"}]), EXISTING)
    assert errors == [] and org["charger_auth"]["credentials"] == {"CP1": {"password_hash": HASH}}


def test_a_replaced_password_gets_a_new_hash():
    org, _, _ = built(org_input(credentials=[{"charger_id": "CP1", "password": "another-key-0123456789"}]), EXISTING)
    new = org["charger_auth"]["credentials"]["CP1"]["password_hash"]
    assert new != HASH and verify_password("another-key-0123456789", new) and not verify_password("existing-key-0123456789", new)


def test_a_credential_left_out_is_removed():
    org, _, _ = built(org_input(credentials=[{"charger_id": "CP1"}]), EXISTING)
    assert "CP2" not in org["charger_auth"]["credentials"]
    org, _, _ = built(org_input(), EXISTING)
    assert "charger_auth" not in org, "no credentials and no required flag: nothing left to say"


def test_a_plaintext_password_that_is_kept_is_flagged():
    _, _, warnings = built(org_input(credentials=[{"charger_id": "CP2"}]), EXISTING)
    assert any("CP2" in w and "plaintext" in w for w in warnings)


def test_a_credential_for_a_charger_with_no_password_on_record_is_refused():
    _, errors, _ = built(org_input(credentials=[{"charger_id": "NEW"}]))
    assert errors == ["Fleet: charger NEW needs a password"]


@pytest.mark.parametrize("bad", ["", "a b", "x/y", "x" * 65, "back\\slash"])
def test_a_charger_id_that_cannot_be_in_an_address_is_refused(bad):
    _, errors, _ = built(org_input(credentials=[{"charger_id": bad, "password": "a-long-key-0123456789"}]))
    assert errors and "not a usable charger id" in errors[0]


def test_a_charger_listed_twice_is_refused():
    _, errors, _ = built(org_input(credentials=[{"charger_id": "A", "password": "a-long-key-0123456789"}, {"charger_id": "A", "password": "b-long-key-0123456789"}]))
    assert any("listed twice" in e for e in errors)


def test_a_short_password_is_refused_and_a_shortish_one_is_a_warning():
    org, errors, _ = built(org_input(credentials=[{"charger_id": "A", "password": "short"}]))
    assert any("too short" in e for e in errors) and "charger_auth" not in org
    org, errors, warnings = built(org_input(credentials=[{"charger_id": "A", "password": "eight-ch"}]))
    assert errors == [] and any("shorter than 16" in w for w in warnings) and "A" in org["charger_auth"]["credentials"]


def test_the_required_flag_is_written_when_given_and_left_out_otherwise():
    org, _, _ = built(org_input(charger_auth_required=False))
    assert org["charger_auth"] == {"required": False}
    org, _, _ = built(org_input(charger_auth_required=None), {"name": "Fleet", "charger_auth": {"required": True}})
    assert "charger_auth" not in org


# --------------------------------------------------------------------------- showing an organization
def test_an_organization_is_shown_with_its_defaults_and_without_its_secrets():
    shown = view(EXISTING, normalized(EXISTING))
    assert shown.name == "Fleet" and shown.tags == 1
    assert (shown.backend_buffer_size, shown.backend_outage_timeout, shown.leader_failover_timeout) == (200, 30, 15)
    assert [(b.id, b.url, b.leader) for b in shown.backends] == [("primary", "ws://a.example/ocpp", True), ("standby", "ws://b.example/ocpp", False)]
    assert [(c.charger_id, c.storage) for c in shown.credentials] == [("CP1", "hash"), ("CP2", "plaintext")]
    assert shown.charger_auth_required is True
    text = shown.model_dump_json()
    assert HASH not in text and "plain-key-0123456789" not in text and "password" not in text


def test_a_string_credential_is_read_as_a_plaintext_password():
    shown = view({"name": "A", "charger_auth": {"credentials": {"X": "secret-text-0123456789"}}})
    assert [(c.charger_id, c.storage) for c in shown.credentials] == [("X", "plaintext")]
    assert "secret-text" not in shown.model_dump_json()


def test_an_organization_that_cannot_be_read_has_no_normalized_form():
    assert normalized({"name": "Bad", "backends": [{"local": True}, {"local": True}]}) is None
    assert normalized({"name": "Good", "connect_to_backend": False}) is not None


# --------------------------------------------------------------------------- saying what changes
def lines_of(before, after):
    return describe(view(before, normalized(before)), view(after, normalized(after)), before, after)


def test_a_change_of_setting_is_described_with_old_and_new():
    before = {"name": "A", "connect_to_backend": False}
    after = {"name": "A", "connect_to_backend": True, "backend_buffer_size": 50, "backends": [{"url": "ws://x/o"}]}
    lines = lines_of(before, after)
    assert "connect_to_backend: false → true" in lines
    assert "backend_buffer_size: 200 → 50" in lines
    assert any(line.startswith("+ backend ws://x/o") for line in lines)


def test_backends_added_removed_and_changed_are_told_apart_by_their_id():
    before = {"name": "A", "backends": [{"id": "one", "url": "ws://1/o", "leader": True}, {"id": "two", "url": "ws://2/o"}]}
    after = {"name": "A", "backends": [{"id": "one", "url": "ws://1new/o", "leader": True}, {"id": "three", "url": "ws://3/o"}]}
    lines = lines_of(before, after)
    assert "+ backend three (ws://3/o)" in lines
    assert "- backend two (ws://2/o)" in lines
    assert "~ backend one: one (ws://1/o, leader) → one (ws://1new/o, leader)" in lines


def test_credentials_are_described_without_a_password_or_a_hash():
    before = {"name": "A", "charger_auth": {"credentials": {"C1": {"password_hash": HASH}, "C2": {"password_hash": HASH}, "C3": {"password_hash": HASH}}}}
    other = hash_password("rotated-key-0123456789", iterations=1000)
    after = {"name": "A", "charger_auth": {"credentials": {"C1": {"password_hash": HASH}, "C2": {"password_hash": other}, "C4": {"password_hash": other}}}}
    lines = lines_of(before, after)
    assert lines == ["+ credential for C4", "~ credential for C2: password replaced", "- credential for C3"] or sorted(lines) == sorted(["+ credential for C4", "~ credential for C2: password replaced", "- credential for C3"])
    text = json.dumps(lines)
    assert HASH not in text and other not in text and "pbkdf2" not in text


def test_a_change_that_changes_nothing_has_no_lines():
    org = {"name": "A", "connect_to_backend": False}
    assert lines_of(org, dict(org)) == []


def test_a_new_organization_is_described_with_its_backends_and_credentials():
    inp = org_input("Newco", backends=[{"url": "ws://x/o", "leader": True}], credentials=[{"charger_id": "CP1", "password": "a-long-key-0123456789"}])
    result = apply_changes([], [AdminChange(op="upsert", org=inp)], normalized)
    [change] = result.changes
    assert change.kind == "added" and change.org == "Newco"
    assert "+ backend ws://x/o (ws://x/o, leader)" in change.lines and "+ credential for CP1" in change.lines
    assert "a-long-key-0123456789" not in json.dumps([c.model_dump() for c in result.changes])


# --------------------------------------------------------------------------- applying changes
def test_changes_are_applied_in_order_to_a_copy_of_the_list():
    original = [{"name": "A", "connect_to_backend": False}, {"name": "B", "connect_to_backend": False}]
    changes = [
        AdminChange(op="delete", name="A"),
        upsert(name="C", connect_to_backend=False),
        upsert(name="B", connect_to_backend=True, backends=[{"url": "ws://x/o"}]),
    ]
    result = apply_changes(original, changes, normalized)
    assert [o["name"] for o in result.organizations] == ["B", "C"]
    assert [(c.org, c.kind) for c in result.changes] == [("A", "removed"), ("C", "added"), ("B", "changed")]
    assert [o["name"] for o in original] == ["A", "B"] and "backends" not in original[1], "the list given is not changed"


def test_removing_an_organization_that_is_not_there_is_an_error():
    result = apply_changes([], [AdminChange(op="delete", name="Ghost")], normalized)
    assert result.errors == ["There is no organization named 'Ghost' to remove"]


def test_an_upsert_without_an_organization_is_an_error():
    assert apply_changes([], [AdminChange(op="upsert")], normalized).errors == ["An upsert needs an organization"]


@pytest.mark.parametrize("name", ["has space", "a/b", "", "x" * 65, "é"])
def test_a_new_organization_needs_a_name_that_can_be_part_of_an_address(name):
    result = apply_changes([], [upsert(name=name)], normalized)
    assert result.errors and "not a usable organization name" in result.errors[0] and result.organizations == []


def test_an_existing_organization_with_an_unusual_name_can_still_be_edited():
    result = apply_changes([{"name": "Old Name"}], [upsert(name="Old Name", connect_to_backend=False)], normalized)
    assert result.errors == [] and result.organizations[0]["connect_to_backend"] is False


def test_a_change_that_changes_nothing_is_not_reported():
    original = [{"name": "A", "connect_to_backend": False}]
    result = apply_changes(original, [upsert(name="A", connect_to_backend=False)], normalized)
    assert result.changes == [] and result.errors == []


# --------------------------------------------------------------------------- the file store
CONFIG = """# my notes
broker:
  host: 0.0.0.0
  port: 8765
mongodb:
  enabled: false
organizations:
  - name: Fleet
    connect_to_backend: false
"""


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def orgs(store):
    return store.snapshot().organizations


def test_a_snapshot_names_the_file_by_its_content(config_file):
    store = FileConfigStore(str(config_file))
    first = store.snapshot()
    assert first.organizations == [{"name": "Fleet", "connect_to_backend": False}]
    assert store.snapshot().revision == first.revision
    config_file.write_text(CONFIG + "# more\n", encoding="utf-8")
    assert store.snapshot().revision != first.revision


def test_a_snapshot_is_a_copy_the_caller_may_change(config_file):
    store = FileConfigStore(str(config_file))
    store.snapshot().organizations[0]["name"] = "Changed"
    assert orgs(store)[0]["name"] == "Fleet"


def test_a_file_that_is_not_yaml_or_not_a_mapping_is_reported(config_file):
    store = FileConfigStore(str(config_file))
    config_file.write_text("a: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        store.snapshot()
    config_file.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="does not hold a mapping"):
        store.snapshot()
    config_file.write_text("organizations: [1, 2]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a list of mappings"):
        store.snapshot()


def test_a_missing_file_is_not_writable(tmp_path):
    store = FileConfigStore(str(tmp_path / "nothing.yaml"))
    writable, reason = store.writable()
    assert writable is False and "without a configuration file" in reason
    with pytest.raises(NotWritable):
        store.snapshot()


def test_a_plan_says_when_the_file_is_not_the_one_the_change_was_made_from(config_file):
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    assert store.plan(snap.revision, snap.organizations).ok
    plan = store.plan("0123456789abcdef", snap.organizations)
    assert not plan.ok and plan.base_revision != plan.current_revision and "has changed" in plan.errors[0]


def test_a_plan_has_the_errors_the_loader_finds(config_file):
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    bad = [{"name": "Fleet", "backends": [{"local": True}, {"local": True}]}]
    plan = store.plan(snap.revision, bad)
    assert not plan.ok and "only one backend can be local" in plan.errors[0]


def test_a_plan_has_the_warnings_the_loader_gives_and_logs_none(config_file, caplog):
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    with caplog.at_level("WARNING", logger="ocpp_broker.config"):
        plan = store.plan(snap.revision, [{"name": "Fleet", "connect_to_backend": False}])
    assert any("UNAUTHENTICATED" in w for w in plan.warnings)
    assert "UNAUTHENTICATED" not in caplog.text, "checking a change is not news"


def test_check_reports_a_shape_the_loader_did_not_expect():
    errors, _, runtime = check({"organizations": "not a list"})
    assert errors and runtime is None


@pytest.mark.asyncio
async def test_applying_writes_the_organizations_and_nothing_else_changes_in_the_file(config_file, monkeypatch):
    monkeypatch.setenv("MONGODB_CONNECTION_STRING", "mongodb://user:from-the-environment@host/")
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    new = snap.organizations + [{"name": "Newco", "connect_to_backend": False}]
    applied = await store.apply(store.plan(snap.revision, new))

    written = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert [o["name"] for o in written["organizations"]] == ["Fleet", "Newco"]
    assert written["broker"] == {"host": "0.0.0.0", "port": 8765} and written["mongodb"] == {"enabled": False}
    assert list(written) == ["broker", "mongodb", "organizations"], "the sections keep their order"
    assert "from-the-environment" not in config_file.read_text(encoding="utf-8"), "a value from the environment is not written to the file"
    assert applied.revision == store.snapshot().revision
    assert [o["name"] for o in applied.runtime["organizations"]] == ["Fleet", "Newco"]
    assert applied.runtime["organizations"][1]["backend_buffer_size"] == 200, "the runtime configuration has the defaults the file does not"
    assert "backend_buffer_size" not in written["organizations"][1]


@pytest.mark.asyncio
async def test_applying_keeps_a_copy_of_the_file_as_it_was(config_file):
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    applied = await store.apply(store.plan(snap.revision, [{"name": "Other", "connect_to_backend": False}]))
    copy = config_file.parent / applied.backup
    assert copy.read_text(encoding="utf-8") == CONFIG, "the copy is the file as it was, comments included"
    assert "my notes" not in config_file.read_text(encoding="utf-8").split("\n", 2)[2], "the comments of the old file are gone from the new one"
    assert applied.backup in config_file.read_text(encoding="utf-8").splitlines()[1], "the new file says where the old one is"


@pytest.mark.asyncio
async def test_two_changes_in_one_second_keep_two_copies(config_file):
    store = FileConfigStore(str(config_file))
    names = set()
    for number in range(3):
        snap = store.snapshot()
        applied = await store.apply(store.plan(snap.revision, [{"name": f"O{number}", "connect_to_backend": False}]))
        names.add(applied.backup)
    assert len(names) == 3 and all((config_file.parent / name).exists() for name in names)


@pytest.mark.asyncio
async def test_only_the_latest_copies_are_kept(config_file):
    store = FileConfigStore(str(config_file), keep_backups=2)
    for number in range(5):
        snap = store.snapshot()
        await store.apply(store.plan(snap.revision, [{"name": f"O{number}", "connect_to_backend": False}]))
    copies = sorted(p.name for p in config_file.parent.iterdir() if ".bak-" in p.name)
    assert len(copies) == 2
    kept = [(config_file.parent / name).read_text(encoding="utf-8") for name in copies]
    assert any("O3" in text for text in kept) and not any("Fleet" in text for text in kept), "the oldest went"


@pytest.mark.asyncio
async def test_a_file_changed_by_hand_in_between_is_not_overwritten(config_file):
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    plan = store.plan(snap.revision, [{"name": "Mine", "connect_to_backend": False}])
    config_file.write_text(CONFIG + "# edited by hand\n", encoding="utf-8")
    with pytest.raises(Conflict):
        await store.apply(plan)
    assert config_file.read_text(encoding="utf-8").endswith("# edited by hand\n")
    assert not [p for p in config_file.parent.iterdir() if ".bak-" in p.name], "nothing was done, so no copy"


@pytest.mark.asyncio
async def test_a_plan_with_errors_is_not_applied(config_file):
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    plan = store.plan(snap.revision, [{"name": "X", "backends": [{"local": True}, {"local": True}]}])
    with pytest.raises(ConfigError, match="only one backend can be local"):
        await store.apply(plan)
    assert config_file.read_text(encoding="utf-8") == CONFIG


@pytest.mark.asyncio
async def test_a_failed_write_leaves_the_file_as_it_was_and_no_stray_files(config_file, monkeypatch):
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    plan = store.plan(snap.revision, [{"name": "X", "connect_to_backend": False}])

    def refuse(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(NotWritable, match="it was not changed"):
        await store.apply(plan)
    monkeypatch.undo()
    assert config_file.read_text(encoding="utf-8") == CONFIG
    assert not [p.name for p in config_file.parent.iterdir() if p.name.endswith(".tmp")], "the half-written file is removed"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no permission bits to keep")
async def test_the_file_keeps_its_permissions(config_file):
    os.chmod(config_file, 0o640)  # not what a temporary file is created with (0o600)
    before = stat.S_IMODE(os.stat(config_file).st_mode)
    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    await store.apply(store.plan(snap.revision, [{"name": "X", "connect_to_backend": False}]))
    assert stat.S_IMODE(os.stat(config_file).st_mode) == before


@pytest.mark.asyncio
async def test_changes_made_at_the_same_time_are_taken_one_after_the_other(config_file):
    import asyncio

    store = FileConfigStore(str(config_file))
    snap = store.snapshot()
    plans = [store.plan(snap.revision, [{"name": f"O{n}", "connect_to_backend": False}]) for n in range(3)]
    outcomes = await asyncio.gather(*(store.apply(p) for p in plans), return_exceptions=True)
    assert sum(1 for o in outcomes if not isinstance(o, Exception)) == 1, "one wins; the others find the file changed"
    assert all(isinstance(o, Conflict) for o in outcomes if isinstance(o, Exception))


# --------------------------------------------------------------------------- the audit log
def record(log, n=1, **kw):
    return log.record("config.apply", kw.pop("outcome", "applied"), kw.pop("source", "10.0.0.1"), kw.pop("key_label", "alice"), ["Fleet"], [f"change {n}"], **kw)


def test_the_log_lists_newest_first_and_up_to_a_limit(tmp_path):
    log = AuditLog(str(tmp_path / "audit.jsonl"))
    for n in range(5):
        record(log, n)
    assert [e.summary for e in log.recent(3)] == [["change 4"], ["change 3"], ["change 2"]]
    assert len(log) == 5


def test_the_log_survives_a_restart(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    log = AuditLog(path)
    record(log, 1, outcome="refused", detail="conflict")
    record(log, 2, revision="abc")
    again = AuditLog(path)
    entries = again.recent()
    assert [e.summary for e in entries] == [["change 2"], ["change 1"]]
    assert entries[0].revision == "abc" and entries[1].outcome == "refused" and entries[1].detail == "conflict"
    assert entries[0].key_label == "alice" and entries[0].source == "10.0.0.1"


def test_a_torn_or_foreign_line_does_not_stop_the_log_loading(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(str(path))
    record(log, 1)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"time": "garbage"\n')
        handle.write("not json at all\n")
        handle.write('{"unrelated": true}\n')
    record(log, 2)
    assert [e.summary for e in AuditLog(str(path)).recent()] == [["change 2"], ["change 1"]]


def test_only_the_latest_entries_are_kept_in_memory(tmp_path):
    log = AuditLog(str(tmp_path / "audit.jsonl"), keep=3)
    for n in range(10):
        record(log, n)
    assert [e.summary[0] for e in log.recent(10)] == ["change 9", "change 8", "change 7"]
    assert KEEP >= 100


def test_a_log_file_that_cannot_be_written_does_not_stop_the_log(tmp_path, caplog):
    log = AuditLog(str(tmp_path / "missing-folder" / "audit.jsonl"))
    with caplog.at_level("WARNING", logger="ocpp_broker.audit"):
        record(log, 1)
        record(log, 2)
    assert [e.summary for e in log.recent()] == [["change 2"], ["change 1"]]
    assert caplog.text.count("kept in memory only") == 1, "said once, not for every entry"


def test_a_log_without_a_file_is_only_in_memory():
    log = AuditLog(None)
    record(log)
    assert len(log) == 1


def test_a_big_file_is_read_from_its_end(tmp_path):
    from ocpp_broker import audit

    path = tmp_path / "audit.jsonl"
    log = AuditLog(str(path), keep=10_000)
    for n in range(40):
        record(log, n)
    original = audit.TAIL_BYTES
    audit.TAIL_BYTES = 2000
    try:
        again = AuditLog(str(path), keep=10_000)
    finally:
        audit.TAIL_BYTES = original
    entries = again.recent(100)
    assert 0 < len(entries) < 40 and entries[0].summary == ["change 39"], "the newest are there; the first line read is not a torn one"
