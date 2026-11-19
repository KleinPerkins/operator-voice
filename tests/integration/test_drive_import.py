"""``drive.import`` / ``drive.status`` integration coverage — OVT Drive Adapter v1
(docs/plans/gmail-drive-channel-adapters-v2.md §3, ADR 0002). Exercises the real
:class:`OperatorVoiceService` against a migrated SQLite DB, in-memory-Keychain crypto, and a
temp-dir :class:`RuntimePaths` — never the operator's real Keychain, database, or Google Drive.

Every file fixture here is synthetic text authored for this test suite; nothing is read from or
written to real Google Drive, and the service never calls Google itself (no network, no SDK, no
token, no OAuth — see ``operator_voice.drive.materialize``). The headline invariants proven here:
server-side owner + not-shared-drive enforcement end-to-end (D-M1-1/2/2b), the ``file_id``-alone
identity + ``modified_time`` supersede-if-newer persistence path AND its not-newer no-op (D-M1-4b),
and ``output_type`` reaching the persisted ``ContextScope`` (D-M1-3, building on merged D-M1-0).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.domain.models import AdapterType
from operator_voice.drive import materialize as drive_materialize
from operator_voice.persistence import store
from operator_voice.runtime.paths import RuntimePaths

_OWNER = "operator@example.com"
_DOC_MIME = "application/vnd.google-apps.document"
_SLIDES_MIME = "application/vnd.google-apps.presentation"
_LONG = "A perfectly ordinary, sufficiently long operator-authored document body for this test."


@pytest.fixture
def unconfigured_service(migrated_db, in_memory_crypto, tmp_path: Path) -> OperatorVoiceService:
    """A real service WITH runtime paths (temp-dir XDG layout), since ``drive.import`` /
    ``drive.status`` are refused / not-configured without ``self.paths`` (mirrors the Slack fixture)."""
    conn, _db_path = migrated_db
    paths = RuntimePaths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    paths.ensure()
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto, paths=paths)


@pytest.fixture
def service(unconfigured_service):
    unconfigured_service.setup()
    unconfigured_service.drive_folder_add(folder_id="writing")
    return unconfigured_service


@pytest.fixture
def paths_only_service(migrated_db, in_memory_crypto) -> OperatorVoiceService:
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


def _file(
    *, file_id: str = "file-1", revision_id: str = "rev-1", modified_time: str = "2026-07-30T14:02:00Z",
    mime_type: str = _DOC_MIME, text: str = _LONG, owner_id: str = _OWNER, owners=None, drive_id=None,
    name: str = "A memo",
) -> dict:
    return {
        "file_id": file_id, "revision_id": revision_id, "modified_time": modified_time, "folder_id": "writing",
        "mime_type": mime_type, "text": text, "owner_id": owner_id,
        "owners": [_OWNER] if owners is None else owners, "drive_id": drive_id, "name": name,
    }


def _drive_source(service: OperatorVoiceService, subject_id: str):
    sources = store.list_sources(service.conn, service.crypto, subject_id)
    drive = [s for s in sources if s.adapter_type == AdapterType.DRIVE_EXPORT]
    assert len(drive) == 1, "setup() must register exactly one drive_export source"
    return drive[0]


def _doc_rows(service: OperatorVoiceService, source_id: str):
    return service.conn.execute(
        "SELECT document_id, native_revision, context_json FROM documents WHERE source_id = ?",
        (source_id,),
    ).fetchall()


# ---------------------------------------------------------------------------------------------
# drive.import — argument validation (mirrors slack.import)
# ---------------------------------------------------------------------------------------------


def test_drive_import_requires_runtime_paths(paths_only_service):
    with pytest.raises(ServiceError):
        paths_only_service.drive_import(owner_id=_OWNER, files=[_file()])


def test_drive_import_requires_owner_id(service):
    with pytest.raises(ServiceError):
        service.drive_import(owner_id=None, files=[_file()])


def test_drive_import_accepts_completed_empty_fetch_but_requires_list(service):
    assert service.drive_import(owner_id=_OWNER, files=[])["imported"] == 0
    assert service.drive_status()["stale"] is False
    with pytest.raises(ServiceError):
        service.drive_import(owner_id=_OWNER, files=None)


# ---------------------------------------------------------------------------------------------
# drive.import — happy path + on-disk effects + sync-cursor persistence
# ---------------------------------------------------------------------------------------------


def test_drive_import_writes_materialized_files_and_persists_sync_cursor(service):
    cursor = {"changes_page_token": "tok-123", "new_start_page_token": "tok-999"}
    result = service.drive_import(owner_id=_OWNER, files=[_file()], sync_cursor=cursor)
    assert result["status"] == "ok"
    assert result["imported"] == 1

    export_dir = service.paths.drive_export_dir
    assert list(export_dir.glob("*.md"))
    state = drive_materialize.read_import_state(export_dir)
    assert state.get("last_import_at") is not None
    assert state.get("sync_cursor") == cursor  # persisted verbatim for the agent's next resume (#37)


def test_drive_import_via_file_argument(service, tmp_path):
    envelope = {"owner_id": _OWNER, "sync_cursor": {"new_start_page_token": "abc"}, "files": [_file()]}
    envelope_path = tmp_path / "drive-envelope.json"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")

    result = service.drive_import(file=str(envelope_path))
    assert result["status"] == "ok"
    assert result["imported"] == 1
    assert drive_materialize.read_import_state(service.paths.drive_export_dir)["sync_cursor"] == {"new_start_page_token": "abc"}


def test_drive_import_file_not_found(service, tmp_path):
    with pytest.raises(ServiceError):
        service.drive_import(file=str(tmp_path / "nope.json"))


def test_drive_import_file_invalid_json(service, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ServiceError):
        service.drive_import(file=str(bad))


def test_drive_import_file_must_be_a_json_object(service, tmp_path):
    not_object = tmp_path / "list.json"
    not_object.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ServiceError):
        service.drive_import(file=str(not_object))


# ---------------------------------------------------------------------------------------------
# drive.import — server-side owner + not-shared-drive enforcement, end to end
# ---------------------------------------------------------------------------------------------


def test_drive_import_rejects_shared_multiowner_and_mismatched_end_to_end(service):
    """The headline invariant through the real service: a Shared-Drive, multi-owner, or mismatched
    file is dropped and counted server-side, never silently accepted, and its text never hits disk —
    even though the calling agent asserted the whole batch was 'mine' (D-M1-1/2/2b)."""
    result = service.drive_import(owner_id=_OWNER, files=[
        _file(file_id="ok"),                                                     # kept
        _file(file_id="shared-drive", drive_id="0ABCsharedDrive"),               # skipped_shared_drive
        _file(file_id="coauthored", owners=[_OWNER, "other@example.com"]),       # skipped_shared_ownership
        _file(file_id="not-mine", owner_id="other@example.com", owners=[_OWNER]),# skipped_mismatched_owner
    ])
    assert result["imported"] == 1
    assert result["skipped_shared_drive"] == 1
    assert result["skipped_shared_ownership"] == 1
    assert result["skipped_mismatched_owner"] == 1

    written = "\n".join(p.name for p in service.paths.drive_export_dir.glob("*.md"))
    assert "ok" in written
    for rejected in ("shared-drive", "coauthored", "not-mine"):
        assert rejected not in written


# ---------------------------------------------------------------------------------------------
# drive.import -> setup-registered drive_export adapter round trip + output_type -> ContextScope
# ---------------------------------------------------------------------------------------------


def test_drive_import_then_setup_registered_adapter_scans_with_output_types(service):
    """Full round trip: ``setup()`` idempotently registers the ``drive_export`` source at
    ``drive_export_dir``, ``drive.import`` materializes owner-only files there, and ``ingest`` scans
    them back out — proving only registered Docs reach the persisted ``docs``/``doc`` scope.
    Slides are excluded under ADR 0004."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    assert setup["drive_source"]["status"] in {"added", "already_registered"}

    service.drive_import(owner_id=_OWNER, files=[
        _file(file_id="doc-1", mime_type=_DOC_MIME, text="A Google Doc body, plainly the operator's own long-enough prose."),
        _file(file_id="slides-1", mime_type=_SLIDES_MIME, text="A Slides deck's speaker notes, also the operator's own words here."),
    ])

    source = _drive_source(service, subject_id)
    result = service.ingest(source_id=source.source_id)
    assert result["documents_new"] == 1

    rows = _doc_rows(service, source.source_id)
    scopes = {(json.loads(r["context_json"])["channel"], json.loads(r["context_json"])["output_type"]) for r in rows}
    assert scopes == {("docs", "doc")}


# ---------------------------------------------------------------------------------------------
# drive.import — supersede-if-newer (modified_time) update path + not-newer no-op (issue #39)
# ---------------------------------------------------------------------------------------------


def test_drive_import_supersedes_only_when_newer_end_to_end(service):
    """ADR 0002 Decision 2 + issue #39: a Drive file's persisted identity is its ``file_id`` ALONE.
    Two revisions of the same file_id collapse to ONE document row, and the row records the STRICTLY-
    NEWER ``modified_time`` — the store's first-ever UPDATE path, reached only via a Drive candidate's
    ``native_revision``."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    service.drive_import(owner_id=_OWNER, files=[
        _file(file_id="doc-A", revision_id="r1", modified_time="2026-07-01T00:00:00Z", text="Revision one of the operator's own long-enough document body."),
        _file(file_id="doc-A", revision_id="r2", modified_time="2026-08-01T00:00:00Z", text="Revision two, meaningfully rewritten, still the operator's own words."),
    ])
    source = _drive_source(service, subject_id)
    service.ingest(source_id=source.source_id)

    rows = _doc_rows(service, source.source_id)
    assert len(rows) == 1  # file_id-alone identity: two revisions -> one row
    assert rows[0]["native_revision"] == "2026-08-01T00:00:00Z"  # strictly-newer revision won


def test_drive_import_supersede_uses_chronological_not_lexical_modified_time(service):
    """Regression: unpadded ISO hours must not make lexical order disagree with chronology."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    service.drive_import(owner_id=_OWNER, files=[
        _file(file_id="doc-A", revision_id="r1", modified_time="2026-08-01T9:00:00Z", text="Revision one of the operator's own long-enough document body."),
        _file(file_id="doc-A", revision_id="r2", modified_time="2026-08-01T10:00:00Z", text="Revision two, meaningfully rewritten, still the operator's own words."),
    ])
    source = _drive_source(service, subject_id)
    service.ingest(source_id=source.source_id)
    rows = _doc_rows(service, source.source_id)
    assert len(rows) == 1
    assert rows[0]["native_revision"] == "2026-08-01T10:00:00Z"


def test_drive_import_not_newer_revision_is_a_noop_end_to_end(service):
    """The fail-closed half of the supersede path: once the newest revision is persisted, re-scanning
    an older/equal revision (here forced via a full re-scan after a cursor reset — the documented
    cursor-expiry -> full-sync path, issue #37) adds ZERO new documents and never downgrades the
    stored row. 'Strictly-greater-else-no-op' (issue #39 heightened-review boundary)."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    service.drive_import(owner_id=_OWNER, files=[
        _file(file_id="doc-A", revision_id="r1", modified_time="2026-07-01T00:00:00Z", text="Revision one of the operator's own long-enough document body."),
        _file(file_id="doc-A", revision_id="r2", modified_time="2026-08-01T00:00:00Z", text="Revision two, meaningfully rewritten, still the operator's own words."),
    ])
    source = _drive_source(service, subject_id)
    service.ingest(source_id=source.source_id)
    before = _doc_rows(service, source.source_id)
    assert len(before) == 1 and before[0]["native_revision"] == "2026-08-01T00:00:00Z"

    # Force a full re-scan (cursor reset == the agent's cursor-expiry full-sync fallback). Both the
    # older r1 and the equal r2 are re-encountered; every one is not-strictly-newer -> a no-op.
    store.set_source_cursor(service.conn, source.source_id, None)
    service.conn.commit()
    result = service.ingest(source_id=source.source_id)
    assert result["documents_new"] == 0

    after = _doc_rows(service, source.source_id)
    assert len(after) == 1
    assert after[0]["document_id"] == before[0]["document_id"]
    assert after[0]["native_revision"] == "2026-08-01T00:00:00Z"  # never downgraded


# ---------------------------------------------------------------------------------------------
# drive.status
# ---------------------------------------------------------------------------------------------


def test_drive_status_stale_when_never_imported(service):
    status = service.drive_status()
    assert status["stale"] is True
    assert status["last_import_at"] is None
    assert status["record_count"] == 0
    assert status["sync_cursor"] is None
    assert "stale" in status["brief"].lower()


def test_drive_status_fresh_after_import_surfaces_sync_cursor(service):
    cursor = {"new_start_page_token": "tok-42"}
    service.drive_import(owner_id=_OWNER, files=[_file()], sync_cursor=cursor)
    status = service.drive_status()
    assert status["stale"] is False
    assert status["file_count"] == 1
    assert status["record_count"] == 1
    assert status["sync_cursor"] == cursor
    assert "fresh" in status["brief"].lower()


def test_drive_status_stale_after_threshold_elapsed(service):
    service.drive_import(owner_id=_OWNER, files=[_file()])
    export_dir = service.paths.drive_export_dir
    state = drive_materialize.read_import_state(export_dir)
    state["last_import_at"] = state["last_import_at"] - (192 * 3600)
    drive_materialize.write_import_state(export_dir, state)

    status = service.drive_status()
    assert status["stale"] is True
    assert "stale" in status["brief"].lower()


def test_drive_status_without_runtime_paths_is_not_configured_not_a_crash(paths_only_service):
    status = paths_only_service.drive_status()
    assert status["stale"] is True
    assert "not configured" in status["brief"].lower()


def test_drive_status_never_surfaces_raw_text(service):
    secret = "A very specific operator sentence that must never leak into drive-status output ever."
    service.drive_import(owner_id=_OWNER, files=[_file(text=secret)])
    status = service.drive_status()
    assert secret not in json.dumps(status)


# ---------------------------------------------------------------------------------------------
# source.add adapter-type choices (D-M1-8)
# ---------------------------------------------------------------------------------------------


def test_source_add_accepts_drive_export_adapter_type(service):
    """D-M1-8: ``drive_export`` is a first-class ``source.add`` choice AND round-trips through the
    real service (not just present in the choices tuple)."""
    from operator_voice.domain.operations import get_operation

    choices = next(a.choices for a in get_operation("source.add").args if a.name == "adapter_type")
    assert "drive_export" in choices

    setup = service.setup()
    added = service.source_add(
        subject_id=setup["subject_id"], adapter_type="drive_export",
        canonical_root=str(service.paths.drive_export_dir / "alt"),
    )
    assert added["status"] == "added"
    assert added["source_id"]


def test_drive_folder_registration_is_sorted_idempotent_and_removable(service):
    service.drive_folder_add(folder_id="z")
    service.drive_folder_add(folder_id="a")
    service.drive_folder_add(folder_id="z")
    sid = service.setup()["subject_id"]
    source = _drive_source(service, sid)
    assert source.role_identity["registered_folders"] == ["a", "writing", "z"]
    assert source.role_identity["subject_role"] == "user"
    assert service.drive_status()["registered_folder_count"] == 3
    for folder in ["a", "writing", "z"]:
        service.drive_folder_remove(folder_id=folder)
    assert service.drive_status()["registered_folder_count"] == 0
    assert "no registered folders" in service.drive_status()["brief"]
    result = service.drive_import(owner_id=_OWNER, files=[_file()])
    assert result["imported"] == 0
    assert result["skipped_unregistered_folder"] == 1


def test_drive_folders_require_setup(unconfigured_service):
    for operation in [unconfigured_service.drive_folder_add, unconfigured_service.drive_folder_remove]:
        with pytest.raises(ServiceError, match="run `operator-voice setup`"):
            operation(folder_id="writing")


def test_legacy_doc_membership_backfill_preserves_record_bytes(service):
    from operator_voice.adapters.drive_export import scan
    sid = service.setup()["subject_id"]
    result = service.drive_import(owner_id=_OWNER, files=[_file()])
    assert result["imported"] == 1
    root = service.paths.drive_export_dir
    record = next(root.glob("*.md"))
    before = record.read_bytes()
    (root / ".folder-membership.json").unlink()  # A pre-v2 export has no membership sidecar.
    source = _drive_source(service, sid)
    missing = scan(source, None)
    assert not missing.candidates
    assert missing.filter_counts == {"not_google_doc": 0, "unregistered_folder": 1}
    result = service.drive_import(owner_id=_OWNER, files=[_file()])
    assert result["imported"] == 0 and result["skipped_duplicate"] == 1
    assert record.read_bytes() == before
    assert len(scan(_drive_source(service, sid), None).candidates) == 1
    ingested = service.ingest(source_id=source.source_id)
    assert ingested["documents_new"] == 1
    service.drive_folder_remove(folder_id="writing")
    excluded = scan(_drive_source(service, sid), None)
    assert not excluded.candidates
    assert excluded.filter_counts["unregistered_folder"] == 1


def test_old_slides_record_is_filtered_at_scan(service):
    from operator_voice.adapters.drive_export import scan
    sid = service.setup()["subject_id"]
    root = service.paths.drive_export_dir
    root.mkdir(parents=True, exist_ok=True)
    (root / "legacy.md").write_text(
        "<!-- ovt:drive file=old-slides rev=r modified=2026-01-01T00:00:00Z "
        "mime=application/vnd.google-apps.presentation -->\n## Legacy\n\nSynthetic old slide text.")
    result = scan(_drive_source(service, sid), None)
    assert not result.candidates
    assert result.filter_counts["not_google_doc"] == 1
