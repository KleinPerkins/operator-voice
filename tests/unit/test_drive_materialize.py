"""``operator_voice.drive.materialize`` unit tests — OVT Drive Adapter v1 (plan
``docs/plans/gmail-drive-channel-adapters-v2.md`` §3, ADR 0002). Every fixture here is synthetic
text authored for this test suite; no real Drive export is read or written, and this module never
touches Google.

The critical tests mirror the Slack adapter's ``test_materialize_rejects_mismatched_author_id``:
the plan's own acceptance criteria (D-M1-1, D-M1-2, D-M1-2b) require explicit tests proving ownership
is verified SERVER-SIDE, fail-closed, against the call's top-level self-asserted ``owner_id`` — never
trusted from the calling agent's own "these are all mine, all My-Drive" filtering.
"""

from __future__ import annotations

import json
from pathlib import Path

from operator_voice.drive import materialize

_OWNER = "operator@example.com"
_LONG = "This is a genuinely long enough operator-authored document body for the import to keep it."


def _file(
    *, file_id: str = "file-1", revision_id: str = "rev-1", modified_time: str = "2026-07-30T14:02:00Z",
    mime_type: str = "application/vnd.google-apps.document", text: str = _LONG,
    owner_id: str = _OWNER, owners=None, drive_id=None, name: str = "A memo", **extra,
) -> dict:
    """A fully-valid, owner-only My-Drive file entry; override any field to build a disqualifying
    fixture. ``owners`` defaults to exactly ``[_OWNER]`` and ``drive_id`` to present-and-null."""
    entry = {
        "file_id": file_id, "revision_id": revision_id, "modified_time": modified_time, "folder_id": "writing",
        "mime_type": mime_type, "text": text, "owner_id": owner_id,
        "owners": [_OWNER] if owners is None else owners, "drive_id": drive_id, "name": name,
    }
    entry.update(extra)
    return entry


# ---------------------------------------------------------------------------------------------
# materialize() — happy path + immutable, timestamp-prefixed, one-file-per-revision layout
# ---------------------------------------------------------------------------------------------


def test_materialize_writes_one_immutable_timestamp_prefixed_file(tmp_path: Path):
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[_file()], registered_folders=["writing"])
    assert result.imported == 1
    assert result.files == {"file-1"}
    written = list(tmp_path.glob("*.md"))
    assert len(written) == 1
    # Filename: <modified_time_iso>__<file_id>__<revision_id>.md, colons -> '-' so the timestamp
    # sorts first (ADR 0002 Decision 1).
    name = written[0].name
    assert name == "2026-07-30T14-02-00Z__file-1__rev-1.md"
    assert name.startswith("2026-07-30T14-02-00Z__")


def test_materialize_embeds_parseable_marker(tmp_path: Path):
    materialize.materialize(tmp_path, owner_id=_OWNER, files=[_file()], registered_folders=["writing"])
    text = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "<!-- ovt:drive file=file-1 rev=rev-1 modified=2026-07-30T14:02:00Z mime=application/vnd.google-apps.document -->" in text
    parsed = materialize.parse_export_file(text)
    assert len(parsed) == 1
    assert parsed[0]["file_id"] == "file-1"
    assert parsed[0]["revision_id"] == "rev-1"
    assert parsed[0]["modified_time"] == "2026-07-30T14:02:00Z"
    assert parsed[0]["mime_type"] == "application/vnd.google-apps.document"
    assert parsed[0]["body"] == _LONG


def test_materialize_two_revisions_of_same_file_are_two_immutable_files(tmp_path: Path):
    """Identity is file_id alone (ADR 0002 Decision 2), but the ON-DISK layout is one immutable file
    per REVISION (Decision 1) — a second revision of the same file_id is a distinct file, never an
    append/overwrite of the first."""
    materialize.materialize(tmp_path, owner_id=_OWNER, files=[_file(revision_id="rev-1", modified_time="2026-07-30T14:02:00Z")], registered_folders=["writing"])
    materialize.materialize(tmp_path, owner_id=_OWNER, files=[_file(revision_id="rev-2", modified_time="2026-08-01T09:00:00Z", text=_LONG + " Revised.")], registered_folders=["writing"])
    written = sorted(p.name for p in tmp_path.glob("*.md"))
    assert written == [
        "2026-07-30T14-02-00Z__file-1__rev-1.md",
        "2026-08-01T09-00-00Z__file-1__rev-2.md",
    ]


# ---------------------------------------------------------------------------------------------
# materialize() — server-side owner + not-shared-drive verification (THE critical invariants)
# ---------------------------------------------------------------------------------------------


def test_materialize_rejects_mismatched_owner_id(tmp_path: Path):
    """D-M1-1. A file whose entry ``owner_id`` does not match the call's top-level self-asserted
    ``owner_id`` is dropped, counted, and never written to disk — server-verified, not agent-trusted
    (mirrors Slack's ``test_materialize_rejects_mismatched_author_id``). The counter is the
    per-adapter ``skipped_mismatched_owner``, never Slack's ``skipped_mismatched_author``."""
    result = materialize.materialize(
        tmp_path, owner_id=_OWNER,
        files=[
            _file(file_id="mine", owner_id=_OWNER),
            _file(file_id="theirs", owner_id="someone-else@example.com", owners=[_OWNER]),
        ],
     registered_folders=["writing"])
    assert result.imported == 1
    assert result.skipped_mismatched_owner == 1
    written = sorted(p.name for p in tmp_path.glob("*.md"))
    assert all("mine" in n for n in written)
    assert not any("theirs" in n for n in written)


def test_materialize_rejects_shared_ownership_multi_owner(tmp_path: Path):
    """D-M1-2. A co-authored file (``owners`` length != 1, or ``owners[0]`` != top-level owner_id) is
    owner-only-excluded in v1 under ``skipped_shared_ownership`` (real list field, ADR 0002 Dec 3)."""
    result = materialize.materialize(
        tmp_path, owner_id=_OWNER,
        files=[_file(owners=[_OWNER, "coauthor@example.com"])],
     registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_shared_ownership == 1
    assert list(tmp_path.glob("*.md")) == []


def test_materialize_rejects_shared_ownership_wrong_sole_owner(tmp_path: Path):
    result = materialize.materialize(
        tmp_path, owner_id=_OWNER,
        files=[_file(owners=["someone-else@example.com"], owner_id="someone-else@example.com")],
     registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_shared_ownership == 1


def test_materialize_rejects_shared_drive_item_nonnull(tmp_path: Path):
    """D-M1-2b. A Shared-Drive item (non-null ``drive_id``) is dropped under ``skipped_shared_drive``."""
    result = materialize.materialize(
        tmp_path, owner_id=_OWNER,
        files=[_file(drive_id="0ABCsharedDriveId")],
     registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_shared_drive == 1
    assert list(tmp_path.glob("*.md")) == []


def test_materialize_rejects_missing_drive_id_key_fail_closed(tmp_path: Path):
    """D-M1-2b (fail-closed half): a MISSING ``drive_id`` key is rejected exactly like a
    present-but-disqualifying value — an agent's omission is never read as proof of My Drive."""
    entry = _file()
    del entry["drive_id"]
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[entry], registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_shared_drive == 1


def test_materialize_rejects_missing_owners_key_fail_closed(tmp_path: Path):
    entry = _file()
    del entry["owners"]
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[entry], registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_shared_ownership == 1


def test_materialize_case_sensitive_owner_match(tmp_path: Path):
    """Ownership is a literal string match — no normalization that could widen who counts as the
    operator beyond the exact self-asserted id."""
    result = materialize.materialize(
        tmp_path, owner_id=_OWNER,
        files=[_file(owners=["OPERATOR@EXAMPLE.COM"], owner_id="OPERATOR@EXAMPLE.COM")],
     registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_shared_ownership == 1


# ---------------------------------------------------------------------------------------------
# materialize() — fail-closed on any missing required structural key
# ---------------------------------------------------------------------------------------------


def test_materialize_fails_closed_on_missing_file_id(tmp_path: Path):
    entry = _file()
    del entry["file_id"]
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[entry], registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_invalid == 1
    assert list(tmp_path.glob("*.md")) == []


def test_materialize_fails_closed_on_missing_revision_id(tmp_path: Path):
    entry = _file()
    del entry["revision_id"]
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[entry], registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_invalid == 1


def test_materialize_fails_closed_on_missing_modified_time(tmp_path: Path):
    entry = _file()
    del entry["modified_time"]
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[entry], registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_invalid == 1


def test_materialize_fails_closed_on_non_string_text(tmp_path: Path):
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[_file(text=12345)], registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_invalid == 1


def test_materialize_skips_non_dict_entries_without_aborting(tmp_path: Path):
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=["not-a-dict", _file()], registered_folders=["writing"])
    assert result.imported == 1
    assert result.skipped_invalid == 1


# ---------------------------------------------------------------------------------------------
# materialize() — short-body filter + dedup (per-revision-filename)
# ---------------------------------------------------------------------------------------------


def test_materialize_skips_short_single_line_body(tmp_path: Path):
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[_file(text="too short")], registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_short == 1


def test_materialize_is_idempotent_across_two_calls(tmp_path: Path):
    entry = _file()
    first = materialize.materialize(tmp_path, owner_id=_OWNER, files=[entry], registered_folders=["writing"])
    assert first.imported == 1
    second = materialize.materialize(tmp_path, owner_id=_OWNER, files=[dict(entry)], registered_folders=["writing"])
    assert second.imported == 0
    assert second.skipped_duplicate == 1
    assert len(list(tmp_path.glob("*.md"))) == 1  # never rewritten


def test_materialize_dedupes_within_the_same_batch(tmp_path: Path):
    entry = _file()
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[entry, dict(entry)], registered_folders=["writing"])
    assert result.imported == 1
    assert result.skipped_duplicate == 1


# ---------------------------------------------------------------------------------------------
# parse_export_file / count_records_by_file / import-state sidecar
# ---------------------------------------------------------------------------------------------


def test_parse_export_file_tolerates_markerless_file(tmp_path: Path):
    # A hand-edited / markerless .md file yields no records rather than raising.
    assert materialize.parse_export_file("just some prose, no ovt:drive marker") == []


def test_count_records_by_file_counts_revisions_per_file(tmp_path: Path):
    materialize.materialize(tmp_path, owner_id=_OWNER, files=[
        _file(file_id="A", revision_id="rev-1", modified_time="2026-07-30T14:02:00Z"),
        _file(file_id="A", revision_id="rev-2", modified_time="2026-08-01T09:00:00Z", text=_LONG + " more"),
        _file(file_id="B", revision_id="rev-1", modified_time="2026-07-31T10:00:00Z"),
    ], registered_folders=["writing"])
    assert materialize.count_records_by_file(tmp_path) == {"A": 2, "B": 1}


def test_count_records_by_file_missing_dir_returns_empty(tmp_path: Path):
    assert materialize.count_records_by_file(tmp_path / "does-not-exist") == {}


def test_import_state_round_trip_with_sync_cursor(tmp_path: Path):
    state = {"last_import_at": 1700000000.0, "sync_cursor": {"changes_page_token": "12345", "new_start_page_token": "67890"}}
    materialize.write_import_state(tmp_path, state)
    assert materialize.read_import_state(tmp_path) == state


def test_read_import_state_corrupt_json_is_empty_not_an_error(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".import-state.json").write_text("{not valid json", encoding="utf-8")
    assert materialize.read_import_state(tmp_path) == {}


def test_materialize_rejects_slides_and_pdf(tmp_path):
    result = materialize.materialize(tmp_path, owner_id=_OWNER, files=[
        _file(file_id="slides", mime_type="application/vnd.google-apps.presentation", folder_id="writing"),
        _file(file_id="pdf", mime_type="application/pdf", folder_id="writing"),
    ], registered_folders=["writing"])
    assert result.imported == 0
    assert result.skipped_not_google_doc == 2
    assert not list(tmp_path.glob("*.md"))


def test_registered_docs_and_resolved_shortcuts_are_checked_and_deduplicated(tmp_path):
    result = materialize.materialize(tmp_path, owner_id=_OWNER, registered_folders=["writing"], files=[
        _file(file_id="own", folder_id="writing", via_shortcut=True),
        _file(file_id="own", folder_id="writing"),
        _file(file_id="outside", folder_id="other"),
        _file(file_id="shared-target", folder_id="writing", via_shortcut=True, owners=[_OWNER, "other"]),
        _file(file_id="shared-drive-target", folder_id="writing", via_shortcut=True, drive_id="shared"),
        _file(file_id="folder-target", mime_type="application/vnd.google-apps.folder", folder_id="writing", via_shortcut=True),
    ])
    assert result.imported == 1
    assert result.skipped_duplicate == 1
    assert result.skipped_unregistered_folder == 1
    assert result.skipped_shared_ownership == 1
    assert result.skipped_shared_drive == 1
    assert result.skipped_not_google_doc == 1
    sidecar = json.loads((tmp_path / ".folder-membership.json").read_text())
    assert set(sidecar) == {"own"}
    assert set(sidecar["own"]) == {"folder_id", "via_shortcut"}
    assert sidecar["own"]["folder_id"] == "writing"
    assert isinstance(sidecar["own"]["via_shortcut"], bool)
    assert _LONG not in (tmp_path / ".folder-membership.json").read_text()


def test_v2_record_still_matches_019_parser(tmp_path):
    import re
    # Literal 0.1.9 parser: rollback must keep reading these exact record bytes.
    old_parser = re.compile(
        r"<!-- ovt:drive file=(?P<file>\S+) rev=(?P<rev>\S+) modified=(?P<modified>\S+) mime=(?P<mime>\S+) -->"
        r"\n##[^\n]*\n\n(?P<body>.*?)(?=\n<!-- ovt:drive |\Z)", re.DOTALL)
    materialize.materialize(tmp_path, owner_id=_OWNER, registered_folders=["writing"],
                            files=[_file(folder_id="writing", via_shortcut=True)])
    record = next(tmp_path.glob("*.md")).read_text()
    match = old_parser.search(record)
    assert match is not None
    assert match.group("body").strip() == _LONG
    assert match.group("file") == "file-1"
