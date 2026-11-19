"""Adapter unit tests — build plan v3 §13 M1 worklist item 5. Every fixture here is synthetic
text authored for this test suite; no adapter ever reads a real export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operator_voice.adapters import drive_export, gmail_export, slack_export
from operator_voice.adapters.base import AdapterError, resolve_within_root
from operator_voice.domain.models import AdapterType, SourceRegistration
from operator_voice.drive.materialize import materialize as drive_materialize
from operator_voice.gmail.materialize import materialize as gmail_materialize
from operator_voice.slack.materialize import materialize as slack_materialize


def _source(tmp_path: Path, adapter_type: AdapterType, subdir: str = "root", **kwargs) -> SourceRegistration:
    root = tmp_path / subdir
    root.mkdir(exist_ok=True)
    return SourceRegistration(
        source_id="src_1", subject_id="subj_1", adapter_type=adapter_type, canonical_root=str(root),
        opted_in_at=0.0, approved_purpose="voice_generation", active=True,
        role_identity=kwargs.pop("role_identity", {"subject_role": "user", "registered_folders": ["writing"]}),
    )


def test_resolve_within_root_rejects_missing_path(tmp_path):
    with pytest.raises(AdapterError):
        resolve_within_root(str(tmp_path / "does-not-exist"))


def test_quarantine_warning_reason_never_duplicates_the_locator_path(tmp_path, monkeypatch):
    """M1 P2-4 (closed M5-B): a size-cap quarantine's ``reason`` used to embed the SAME absolute
    filesystem path already carried by ``locator`` (e.g. reaching a consumer/log twice, including
    the operator's home-directory username each time). ``reason`` must describe only WHY, never
    WHERE — ``locator`` remains the sole carrier of the path. Exercised through Slack via the
    shared ``read_capped`` helper used by all retained adapters."""
    source = _source(tmp_path, AdapterType.SLACK_EXPORT)
    root = Path(source.canonical_root)
    big = root / "oversized-note.md"
    big.write_text("x" * 10)
    monkeypatch.setattr("operator_voice.adapters.base.MAX_BYTES_PER_FILE", 1)

    result = slack_export.scan(source, cursor=None)

    assert result.candidates == []
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.locator == str(big)
    assert str(big) not in warning.reason
    assert root.name not in warning.reason
    assert "exceeds MAX_BYTES_PER_FILE" in warning.reason


# ---------------------------------------------------------------------------------------------
# slack_export — OVT Slack Adapter v1. Scans the per-channel Markdown files ``slack.import``
# materializes; every message has already passed server-side author verification at import time
# (see ``test_slack_materialize.py``), so this adapter is structurally single-role after import verification.
# ---------------------------------------------------------------------------------------------


def test_slack_export_adapter_reads_materialized_messages_as_user_role_with_fixed_channel(tmp_path):
    source = _source(tmp_path, AdapterType.SLACK_EXPORT)
    root = Path(source.canonical_root)
    slack_materialize(root, author_id="U1", messages=[
        {"channel": "#general", "message_ts": "1700000000.000100", "time": "09:00",
         "text": "A sufficiently long operator message for the adapter to pick up.", "author_id": "U1"},
    ])

    result = slack_export.scan(source, cursor=None)
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.role == "user"
    assert c.channel == "slack"  # the communication MEDIUM, never the per-channel slug
    assert c.source_native_id == "general:1700000000.000100"


def test_slack_export_adapter_refuses_inactive_source(tmp_path):
    source = _source(tmp_path, AdapterType.SLACK_EXPORT)
    source.active = False
    with pytest.raises(Exception):
        slack_export.scan(source, cursor=None)


def test_slack_export_adapter_respects_cursor_across_multiple_channel_files(tmp_path):
    source = _source(tmp_path, AdapterType.SLACK_EXPORT)
    root = Path(source.canonical_root)
    slack_materialize(root, author_id="U1", messages=[
        {"channel": "#eng", "message_ts": "1700000000.000100", "text": "An engineering channel message, long enough to keep.", "author_id": "U1"},
        {"channel": "#general", "message_ts": "1700000000.000200", "text": "A general channel message, also long enough to keep.", "author_id": "U1"},
    ])

    first = slack_export.scan(source, cursor=None)
    assert len(first.candidates) == 2

    second = slack_export.scan(source, cursor=first.next_cursor)
    assert len(second.candidates) == 0




def test_slack_export_adapter_tolerates_hand_edited_file_without_a_message_marker(tmp_path):
    """A channel file with no ``slack.import``-authored marker (e.g. hand-edited by the operator)
    yields zero messages rather than crashing the whole scan — quarantine-not-abort."""
    source = _source(tmp_path, AdapterType.SLACK_EXPORT)
    root = Path(source.canonical_root)
    slack_materialize(root, author_id="U1", messages=[
        {"channel": "#general", "message_ts": "1700000000.000100", "text": "A genuine operator message, long enough to keep here.", "author_id": "U1"},
    ])
    (root / "freeform-notes.md").write_text("# Just some plain notes\n\nNo message markers here at all.\n")

    result = slack_export.scan(source, cursor=None)
    assert len(result.candidates) == 1  # the genuine materialized message is still found
    assert result.candidates[0].source_native_id == "general:1700000000.000100"


def test_slack_export_adapter_determinism(tmp_path):
    source = _source(tmp_path, AdapterType.SLACK_EXPORT)
    root = Path(source.canonical_root)
    slack_materialize(root, author_id="U1", messages=[
        {"channel": "#general", "message_ts": "1700000000.000100", "text": "A genuinely long enough operator message, for determinism.", "author_id": "U1"},
    ])
    first = slack_export.scan(source, cursor=None)
    second = slack_export.scan(source, cursor=None)
    assert [(c.role, c.locator, c.digest) for c in first.candidates] == [(c.role, c.locator, c.digest) for c in second.candidates]


# ---------------------------------------------------------------------------------------------
# drive_export — OVT Drive Adapter v1. Scans the immutable, timestamp-prefixed per-revision files
# ``drive.import`` materializes; every file has already passed server-side owner + not-shared-drive
# verification at import time (see ``test_drive_materialize.py``), so this adapter is structurally
# single-role like ``slack_export``. Unlike Slack's one fixed channel, a file's channel /
# output_type come from its mime (Docs->docs/doc, Slides->slides/slide; ADR 0002 §3.6).
# ---------------------------------------------------------------------------------------------

_DRIVE_OWNER = "operator@example.com"
_DOC_MIME = "application/vnd.google-apps.document"
_SLIDES_MIME = "application/vnd.google-apps.presentation"


def _dfile(*, file_id, revision_id="rev-1", modified_time="2026-07-30T14:02:00Z", mime_type=_DOC_MIME,
           text="A sufficiently long operator-authored document body for the adapter to pick up.") -> dict:
    return {
        "file_id": file_id, "revision_id": revision_id, "modified_time": modified_time,
        "mime_type": mime_type, "text": text, "owner_id": _DRIVE_OWNER, "owners": [_DRIVE_OWNER],
        "drive_id": None, "name": "A memo", "folder_id": "writing",
    }


def test_drive_export_reads_only_docs_with_doc_channel(tmp_path):
    """A registered Google Doc -> channel/output_type ``docs``/``doc``; Slides are excluded; identity (``source_native_id``) is the bare ``file_id`` ALONE (ADR 0002 Decision 2),
    never ``file_id:revision_id``. Every candidate is single-role ``user`` by construction."""
    source = _source(tmp_path, AdapterType.DRIVE_EXPORT)
    root = Path(source.canonical_root)
    drive_materialize(root, owner_id=_DRIVE_OWNER, files=[
        _dfile(file_id="doc-1", mime_type=_DOC_MIME, modified_time="2026-07-30T14:02:00Z"),
        _dfile(file_id="slides-1", mime_type=_SLIDES_MIME, modified_time="2026-07-31T10:00:00Z"),
    ], registered_folders=["writing"])

    result = drive_export.scan(source, cursor=None)
    by_channel = {c.channel: c for c in result.candidates}
    assert set(by_channel) == {"docs"}
    assert all(c.role == "user" for c in result.candidates)
    assert by_channel["docs"].output_type == "doc"
    assert by_channel["docs"].source_native_id == "doc-1"  # file_id ALONE


def test_drive_export_carries_modified_time_and_revision_as_context_provenance(tmp_path):
    """The candidate's ``context`` carries ``modified_time`` (the authoritative supersede-if-newer
    comparator) and ``revision_id`` (opaque provenance) — the ONLY entry point to the store's
    Drive-only supersede path (ADR 0002 Decision 2)."""
    source = _source(tmp_path, AdapterType.DRIVE_EXPORT)
    root = Path(source.canonical_root)
    drive_materialize(root, owner_id=_DRIVE_OWNER, files=[_dfile(file_id="doc-1", revision_id="rev-7", modified_time="2026-07-30T14:02:00Z")], registered_folders=["writing"])
    c = drive_export.scan(source, cursor=None).candidates[0]
    assert c.context["modified_time"] == "2026-07-30T14:02:00Z"
    assert c.context["revision_id"] == "rev-7"


def test_drive_export_adapter_refuses_inactive_source(tmp_path):
    source = _source(tmp_path, AdapterType.DRIVE_EXPORT)
    source.active = False
    with pytest.raises(Exception):
        drive_export.scan(source, cursor=None)


def test_drive_export_adapter_respects_cursor_across_multiple_files(tmp_path):
    source = _source(tmp_path, AdapterType.DRIVE_EXPORT)
    root = Path(source.canonical_root)
    drive_materialize(root, owner_id=_DRIVE_OWNER, files=[
        _dfile(file_id="a", modified_time="2026-07-30T14:02:00Z"),
        _dfile(file_id="b", modified_time="2026-07-31T10:00:00Z"),
    ], registered_folders=["writing"])
    first = drive_export.scan(source, cursor=None)
    assert len(first.candidates) == 2
    second = drive_export.scan(source, cursor=first.next_cursor)
    assert len(second.candidates) == 0


def test_drive_export_cursor_resumes_onto_a_newer_revision_no_slack_blind_spot(tmp_path):
    """Drive retains lexical ordering: because each
    revision is its OWN immutable file named ``<modified_time>__<file_id>__<revision_id>.md``, a
    later revision is a NEW file whose name sorts AFTER the cursor — so a plain lexical resume DOES
    pick it up without a change-time watermark (§ drive_export.py docstring)."""
    source = _source(tmp_path, AdapterType.DRIVE_EXPORT)
    root = Path(source.canonical_root)
    drive_materialize(root, owner_id=_DRIVE_OWNER, files=[_dfile(file_id="doc-A", revision_id="rev-1", modified_time="2026-07-30T14:02:00Z")], registered_folders=["writing"])
    first = drive_export.scan(source, cursor=None)
    assert len(first.candidates) == 1

    # A later import writes a NEWER revision of the SAME file as a distinct, later-sorting file.
    drive_materialize(root, owner_id=_DRIVE_OWNER, files=[_dfile(file_id="doc-A", revision_id="rev-2", modified_time="2026-08-01T09:00:00Z", text="Revision two, rewritten and still long enough to keep.")], registered_folders=["writing"])
    resumed = drive_export.scan(source, cursor=first.next_cursor)
    assert len(resumed.candidates) == 1  # newer revision picked up on resume — NO blind spot
    assert resumed.candidates[0].source_native_id == "doc-A"
    assert resumed.candidates[0].context["revision_id"] == "rev-2"


def test_drive_export_adapter_tolerates_hand_edited_markerless_file(tmp_path):
    source = _source(tmp_path, AdapterType.DRIVE_EXPORT)
    root = Path(source.canonical_root)
    drive_materialize(root, owner_id=_DRIVE_OWNER, files=[_dfile(file_id="doc-1")], registered_folders=["writing"])
    (root / "freeform-notes.md").write_text("# Just some plain notes\n\nNo ovt:drive markers here at all.\n")
    result = drive_export.scan(source, cursor=None)
    assert len(result.candidates) == 1
    assert result.candidates[0].source_native_id == "doc-1"


def test_drive_export_adapter_determinism(tmp_path):
    source = _source(tmp_path, AdapterType.DRIVE_EXPORT)
    root = Path(source.canonical_root)
    drive_materialize(root, owner_id=_DRIVE_OWNER, files=[_dfile(file_id="doc-1")], registered_folders=["writing"])
    first = drive_export.scan(source, cursor=None)
    second = drive_export.scan(source, cursor=None)
    assert [(c.role, c.locator, c.digest) for c in first.candidates] == [(c.role, c.locator, c.digest) for c in second.candidates]


# ---------------------------------------------------------------------------------------------
# gmail_export — OVT Gmail Adapter v1 (Sent-only, immutable per-message files)
# ---------------------------------------------------------------------------------------------

_GMAIL_AUTHOR = "operator@example.com"


def _gmsg(*, message_id, thread_id="thread-1", internal_date="2026-07-30T14:02:00Z",
          text="A sufficiently long operator-authored Sent message body for the adapter to pick up.") -> dict:
    return {
        "message_id": message_id, "thread_id": thread_id, "internal_date": internal_date,
        "from": _GMAIL_AUTHOR, "subject": "Subject", "text": text,
    }


def test_gmail_export_reads_materialized_files_with_email_channel_and_output_type(tmp_path):
    """G-M2-0 adapter half + G-M2-2: channel=email, output_type=email, role=user only."""
    source = _source(tmp_path, AdapterType.GMAIL_EXPORT)
    root = Path(source.canonical_root)
    gmail_materialize(root, author_email=_GMAIL_AUTHOR, messages=[_gmsg(message_id="msg-1")])

    result = gmail_export.scan(source, cursor=None)
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.channel == "email"
    assert c.output_type == "email"
    assert c.role == "user"
    assert c.source_native_id == "msg-1"


def test_gmail_export_sent_only_role(tmp_path):
    """G-M2-2: adapter never emits non-user roles."""
    source = _source(tmp_path, AdapterType.GMAIL_EXPORT)
    root = Path(source.canonical_root)
    gmail_materialize(root, author_email=_GMAIL_AUTHOR, messages=[_gmsg(message_id="msg-1")])
    assert all(c.role == "user" for c in gmail_export.scan(source, cursor=None).candidates)


def test_gmail_export_adapter_preserves_lexical_cursor(tmp_path):
    """G-M2-4: per-message immutable-file cursor resumes correctly."""
    source = _source(tmp_path, AdapterType.GMAIL_EXPORT)
    root = Path(source.canonical_root)
    gmail_materialize(root, author_email=_GMAIL_AUTHOR, messages=[
        _gmsg(message_id="a", internal_date="2026-07-30T14:02:00Z"),
        _gmsg(message_id="b", internal_date="2026-07-31T10:00:00Z"),
    ])
    first = gmail_export.scan(source, cursor=None)
    assert len(first.candidates) == 2
    second = gmail_export.scan(source, cursor=first.next_cursor)
    assert len(second.candidates) == 0

    gmail_materialize(root, author_email=_GMAIL_AUTHOR, messages=[
        _gmsg(message_id="c", internal_date="2026-08-01T09:00:00Z"),
    ])
    resumed = gmail_export.scan(source, cursor=first.next_cursor)
    assert len(resumed.candidates) == 1
    assert resumed.candidates[0].source_native_id == "c"


def test_gmail_export_adapter_refuses_inactive_source(tmp_path):
    source = _source(tmp_path, AdapterType.GMAIL_EXPORT)
    source.active = False
    with pytest.raises(Exception):
        gmail_export.scan(source, cursor=None)


def test_gmail_export_adapter_tolerates_hand_edited_markerless_file(tmp_path):
    source = _source(tmp_path, AdapterType.GMAIL_EXPORT)
    root = Path(source.canonical_root)
    gmail_materialize(root, author_email=_GMAIL_AUTHOR, messages=[_gmsg(message_id="msg-1")])
    (root / "freeform-notes.md").write_text("# Just some plain notes\n\nNo ovt:gmail markers here at all.\n")
    result = gmail_export.scan(source, cursor=None)
    assert len(result.candidates) == 1
    assert result.candidates[0].source_native_id == "msg-1"


def test_gmail_export_adapter_determinism(tmp_path):
    source = _source(tmp_path, AdapterType.GMAIL_EXPORT)
    root = Path(source.canonical_root)
    gmail_materialize(root, author_email=_GMAIL_AUTHOR, messages=[_gmsg(message_id="msg-1")])
    first = gmail_export.scan(source, cursor=None)
    second = gmail_export.scan(source, cursor=None)
    assert [(c.role, c.locator, c.digest) for c in first.candidates] == [(c.role, c.locator, c.digest) for c in second.candidates]
