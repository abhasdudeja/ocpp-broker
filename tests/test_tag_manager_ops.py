"""
TagManager: statistics, validation, bulk, import and export (real manager, no mocks).
"""

import json

import pytest

from ocpp_broker.schemas.tags import OCPPTag, TagSearchRequest
from ocpp_broker.tag_manager import TagManager

ORG = "Org1"


def tag(id_tag, status="Accepted", **kw):
    return OCPPTag(id_tag=id_tag, status=status, **kw)


@pytest.fixture
def manager():
    return TagManager({})


async def _stored(manager, id_tag):
    return await manager.get_tag(ORG, id_tag)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validate_accepts_a_clean_new_tag(manager):
    result = await manager.validate_tag(ORG, tag("NEW1"))
    assert result.is_valid and not result.errors and not result.warnings


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["café", " lead", "trail ", "tab\t"])
async def test_validate_rejects_non_printable_ascii_ids(manager, bad):
    result = await manager.validate_tag(ORG, tag(bad))
    assert not result.is_valid and "printable ASCII" in result.errors[0]


@pytest.mark.asyncio
async def test_validate_flags_self_parent_and_missing_parent(manager):
    await manager.add_tag(ORG, tag("ADMIN"))

    assert (await manager.validate_tag(ORG, tag("T1", parent_id_tag="ADMIN"))).is_valid
    self_parent = await manager.validate_tag(ORG, tag("T2", parent_id_tag="T2"))
    missing = await manager.validate_tag(ORG, tag("T3", parent_id_tag="GHOST"))

    assert "itself" in self_parent.errors[0]
    assert "GHOST" in missing.errors[0]


@pytest.mark.asyncio
async def test_validate_expiry_past_is_a_warning_garbage_is_an_error(manager):
    past = await manager.validate_tag(ORG, tag("T1", expiry_date="2000-01-01T00:00:00Z"))
    junk = await manager.validate_tag(ORG, tag("T2", expiry_date="soon"))
    naive_future = await manager.validate_tag(ORG, tag("T3", expiry_date="2999-01-01T00:00:00"))

    assert past.is_valid and "past" in past.warnings[0]
    assert not junk.is_valid
    assert naive_future.is_valid and not naive_future.warnings


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bulk_items_succeed_or_fail_independently(manager):
    await manager.add_tag(ORG, tag("EXISTS"))

    added = await manager.bulk_operation(ORG, "add", [tag("A"), tag("EXISTS"), tag("B")])
    assert (added.succeeded, added.failed) == (2, 1)
    assert [r.error for r in added.results] == [None, "already exists", None]

    updated = await manager.bulk_operation(ORG, "update", [tag("A", "Blocked"), tag("MISSING")])
    assert [r.success for r in updated.results] == [True, False]
    assert (await _stored(manager, "A")).status.value == "Blocked"

    deleted = await manager.bulk_operation(ORG, "delete", [tag("A"), tag("A")])
    assert [r.error for r in deleted.results] == [None, "not found"]


@pytest.mark.asyncio
async def test_bulk_rejects_an_unknown_operation(manager):
    with pytest.raises(ValueError):
        await manager.bulk_operation(ORG, "explode", [tag("A")])


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_import_accepts_an_object_or_a_bare_list(manager):
    a = await manager.import_tags(ORG, "json", json.dumps({"tags": [{"id_tag": "A", "status": "Accepted"}]}))
    b = await manager.import_tags(ORG, "json", json.dumps([{"id_tag": "B", "status": "Accepted"}]))
    assert (a.imported, b.imported) == (1, 1)


@pytest.mark.asyncio
async def test_import_skips_existing_unless_overwrite_is_set(manager):
    await manager.add_tag(ORG, tag("A", "Accepted"))
    data = json.dumps([{"id_tag": "A", "status": "Blocked"}, {"id_tag": "B", "status": "Accepted"}])

    kept = await manager.import_tags(ORG, "json", data)
    assert (kept.imported, kept.updated, kept.skipped) == (1, 0, 1)
    assert (await _stored(manager, "A")).status.value == "Accepted"

    overwritten = await manager.import_tags(ORG, "json", data, overwrite_existing=True)
    assert (overwritten.imported, overwritten.updated, overwritten.skipped) == (0, 2, 0)
    assert (await _stored(manager, "A")).status.value == "Blocked"


@pytest.mark.asyncio
async def test_import_reports_bad_records_and_still_imports_the_good_ones(manager):
    data = json.dumps(
        [
            {"id_tag": "OK1", "status": "Accepted"},
            {"id_tag": "BADSTATUS", "status": "Maybe"},
            {"id_tag": "X" * 30, "status": "Accepted"},
            {"id_tag": "BADDATE", "status": "Accepted", "expiry_date": "never"},
            {"id_tag": "OK1", "status": "Accepted"},
            "not even an object",
            {"id_tag": "OK2", "status": "Accepted"},
        ]
    )

    result = await manager.import_tags(ORG, "json", data)

    assert result.total == 7 and result.imported == 2
    by_record = {e.record: e for e in result.errors}
    assert set(by_record) == {2, 3, 4, 5, 6}
    assert "status" in by_record[2].error
    assert "id_tag" in by_record[3].error
    assert "ISO 8601" in by_record[4].error
    assert "duplicate" in by_record[5].error
    assert await _stored(manager, "OK2") and not await _stored(manager, "BADSTATUS")


@pytest.mark.asyncio
async def test_import_allows_a_parent_defined_in_the_same_batch(manager):
    data = json.dumps(
        [
            {"id_tag": "CHILD", "status": "Accepted", "parent_id_tag": "BOSS"},
            {"id_tag": "BOSS", "status": "Accepted"},
            {"id_tag": "ORPHAN", "status": "Accepted", "parent_id_tag": "GHOST"},
        ]
    )

    result = await manager.import_tags(ORG, "json", data)

    assert result.imported == 2
    assert [e.id_tag for e in result.errors] == ["ORPHAN"]


@pytest.mark.asyncio
async def test_validate_only_changes_nothing_but_reports_the_outcome(manager):
    await manager.add_tag(ORG, tag("A"))
    data = json.dumps([{"id_tag": "A", "status": "Blocked"}, {"id_tag": "NEW", "status": "Accepted"},
                       {"id_tag": "BAD", "status": "Nope"}])

    result = await manager.import_tags(ORG, "json", data, overwrite_existing=True, validate_only=True)

    assert (result.imported, result.updated, len(result.errors)) == (1, 1, 1)
    assert result.validate_only is True
    assert await _stored(manager, "NEW") is None
    assert (await _stored(manager, "A")).status.value == "Accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("source,data", [("json", "{nope"), ("json", '"a string"'), ("json", '{"tags": 5}'),
                                         ("csv", 'id_tag,status,metadata\nA,Accepted,{bad')])
async def test_import_raises_valueerror_for_unparseable_input(manager, source, data):
    with pytest.raises(ValueError):
        await manager.import_tags(ORG, source, data)


@pytest.mark.asyncio
async def test_import_csv_with_empty_cells(manager):
    csv_text = "id_tag,status,tag_type,expiry_date,parent_id_tag,description\nA,Accepted,NFC,,,Front desk\n"

    result = await manager.import_tags(ORG, "csv", csv_text)

    assert result.imported == 1
    stored = await _stored(manager, "A")
    assert stored.tag_type.value == "NFC" and stored.expiry_date is None and stored.description == "Front desk"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
async def _seed(manager):
    await manager.add_tag(ORG, tag("BOSS", description="Admin, \"the boss\""))
    await manager.add_tag(
        ORG,
        tag("U1", "Blocked", tag_type="NFC", parent_id_tag="BOSS", expiry_date="2999-12-31T23:59:59Z",
            metadata={"dept": "IT", "n": 3}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["json", "csv"])
async def test_export_then_import_round_trips(manager, fmt):
    await _seed(manager)
    exported = await manager.export_tags(ORG, fmt)

    fresh = TagManager({})
    result = await fresh.import_tags(ORG, fmt, exported)

    assert result.imported == 2 and not result.errors, result.errors
    for id_tag in ("BOSS", "U1"):
        original = (await manager.get_tag(ORG, id_tag)).model_dump()
        copy = (await fresh.get_tag(ORG, id_tag)).model_dump()
        for field in ("status", "tag_type", "expiry_date", "parent_id_tag", "description", "metadata"):
            assert copy[field] == original[field], (id_tag, field)


@pytest.mark.asyncio
async def test_export_without_metadata_omits_the_extra_fields(manager):
    await _seed(manager)

    as_json = json.loads(await manager.export_tags(ORG, "json", include_metadata=False))
    as_csv = (await manager.export_tags(ORG, "csv", include_metadata=False)).splitlines()[0]

    assert all({"created_at", "updated_at", "metadata"}.isdisjoint(t) for t in as_json["tags"])
    assert as_csv == "id_tag,status,tag_type,expiry_date,parent_id_tag,description"
    with_meta = (await manager.export_tags(ORG, "csv")).splitlines()[0]
    assert with_meta.endswith("created_at,updated_at,metadata")


@pytest.mark.asyncio
async def test_export_rejects_unknown_formats_and_only_includes_the_requested_org(manager):
    await manager.add_tag("Other", tag("THEIRS"))
    await manager.add_tag(ORG, tag("MINE"))

    with pytest.raises(ValueError):
        await manager.export_tags(ORG, "xml")
    assert json.loads(await manager.export_tags(ORG, "json"))["count"] == 1
    assert json.loads(await manager.export_tags("Empty", "json"))["tags"] == []


# ---------------------------------------------------------------------------
# search_tags still works after sharing the loader
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_search_still_filters_and_paginates(manager):
    await manager.bulk_operation(ORG, "add", [tag(f"T{i}", "Accepted" if i % 2 else "Blocked") for i in range(6)])

    page = await manager.search_tags(ORG, TagSearchRequest(status="Accepted", limit=2, offset=1))

    assert page.total == 3 and len(page.tags) == 2
