"""``gmail.import`` / ``gmail.status`` integration coverage — OVT Gmail Adapter v1
(docs/plans/gmail-drive-channel-adapters-v2.md §4). Synthetic fixtures only; no real Gmail access.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.domain.models import AdapterType
from operator_voice.gmail import materialize as gmail_materialize
from operator_voice.persistence import store
from operator_voice.runtime.paths import RuntimePaths

_AUTHOR = "operator@example.com"
_LONG = "A perfectly ordinary, sufficiently long operator-authored Sent message body for this test."


@pytest.fixture
def service(migrated_db, in_memory_crypto, tmp_path: Path) -> OperatorVoiceService:
    conn, _db_path = migrated_db
    paths = RuntimePaths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    paths.ensure()
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto, paths=paths)


@pytest.fixture
def paths_only_service(migrated_db, in_memory_crypto) -> OperatorVoiceService:
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


def _msg(
    *, message_id: str = "msg-1", thread_id: str = "thread-1",
    internal_date: str = "2026-07-30T14:02:00Z", from_addr: str = _AUTHOR,
    text: str = _LONG, subject: str = "Hello",
) -> dict:
    return {
        "message_id": message_id, "thread_id": thread_id, "internal_date": internal_date,
        "from": from_addr, "subject": subject, "text": text,
    }


def _gmail_source(service: OperatorVoiceService, subject_id: str):
    sources = store.list_sources(service.conn, service.crypto, subject_id)
    gmail = [s for s in sources if s.adapter_type == AdapterType.GMAIL_EXPORT]
    assert len(gmail) == 1, "setup() must register exactly one gmail_export source"
    return gmail[0]


def test_gmail_import_requires_runtime_paths(paths_only_service):
    with pytest.raises(ServiceError):
        paths_only_service.gmail_import(author_email=_AUTHOR, messages=[_msg()])


def test_gmail_import_requires_author_email(service):
    with pytest.raises(ServiceError):
        service.gmail_import(author_email=None, messages=[_msg()])


def test_gmail_import_accepts_completed_empty_fetch_but_requires_list(service):
    assert service.gmail_import(author_email=_AUTHOR, messages=[])["imported"] == 0
    assert service.gmail_status()["stale"] is False
    with pytest.raises(ServiceError):
        service.gmail_import(author_email=_AUTHOR, messages=None)


def test_gmail_import_writes_materialized_files_and_persists_sync_cursor(service):
    cursor = {"history_id": "987654"}
    result = service.gmail_import(author_email=_AUTHOR, messages=[_msg()], sync_cursor=cursor)
    assert result["status"] == "ok"
    assert result["imported"] == 1

    export_dir = service.paths.gmail_export_dir
    assert list(export_dir.glob("*.md"))
    state = gmail_materialize.read_import_state(export_dir)
    assert state.get("last_import_at") is not None
    assert state.get("sync_cursor") == cursor


def test_gmail_import_via_file_argument(service, tmp_path):
    envelope = {"author_email": _AUTHOR, "sync_cursor": {"history_id": "abc"}, "messages": [_msg()]}
    envelope_path = tmp_path / "gmail-envelope.json"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")

    result = service.gmail_import(file=str(envelope_path))
    assert result["imported"] == 1


def test_gmail_import_rejects_mismatched_author_email_end_to_end(service):
    result = service.gmail_import(author_email=_AUTHOR, messages=[
        _msg(message_id="mine"),
        _msg(message_id="theirs", from_addr="impersonator@example.com"),
    ])
    assert result["imported"] == 1
    assert result["skipped_mismatched_author"] == 1
    assert len(list(service.paths.gmail_export_dir.glob("*.md"))) == 1


def test_gmail_import_then_setup_registered_adapter_scans_with_email_output_type(service):
    """G-M2-0: Gmail candidates reach persisted ContextScope with channel=email, output_type=email."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    service.gmail_import(author_email=_AUTHOR, messages=[_msg(message_id="sent-1")])
    source = _gmail_source(service, subject_id)
    service.ingest(source_id=source.source_id)

    rows = service.conn.execute(
        "SELECT context_json FROM documents WHERE source_id = ?",
        (source.source_id,),
    ).fetchall()
    assert len(rows) == 1
    ctx = json.loads(rows[0]["context_json"])
    assert ctx["channel"] == "email"
    assert ctx["output_type"] == "email"


def test_gmail_history_id_persisted(service):
    """G-M2-3: Import writes cursor; status reads it."""
    cursor = {"history_id": "hist-42"}
    service.gmail_import(author_email=_AUTHOR, messages=[_msg()], sync_cursor=cursor)
    status = service.gmail_status()
    assert status["sync_cursor"] == cursor


def test_gmail_status_stale_when_never_imported(service):
    status = service.gmail_status()
    assert status["stale"] is True
    assert status["last_import_at"] is None
    assert status["message_count"] == 0
    assert "stale" in status["brief"].lower()


def test_gmail_status_fresh_after_import(service):
    service.gmail_import(author_email=_AUTHOR, messages=[_msg()])
    status = service.gmail_status()
    assert status["stale"] is False
    assert status["message_count"] == 1
    assert "fresh" in status["brief"].lower()


def test_gmail_status_stale_after_threshold_elapsed(service):
    service.gmail_import(author_email=_AUTHOR, messages=[_msg()])
    export_dir = service.paths.gmail_export_dir
    state = gmail_materialize.read_import_state(export_dir)
    state["last_import_at"] = state["last_import_at"] - (192 * 3600)
    gmail_materialize.write_import_state(export_dir, state)

    status = service.gmail_status()
    assert status["stale"] is True
    assert "stale" in status["brief"].lower()


def test_gmail_status_without_runtime_paths_is_not_configured_not_a_crash(paths_only_service):
    status = paths_only_service.gmail_status()
    assert status["stale"] is True
    assert "not configured" in status["brief"].lower()


def test_gmail_status_never_surfaces_raw_message_text(service):
    secret = "SUPER_SECRET_GMAIL_BODY_TEXT_NEVER_LEAK"
    service.gmail_import(author_email=_AUTHOR, messages=[_msg(text=secret + " " + _LONG)])
    status = service.gmail_status()
    assert secret not in json.dumps(status)


def test_source_add_accepts_gmail_export_adapter_type(service):
    """G-M2-6: ``gmail_export`` is a first-class ``source.add`` choice."""
    from operator_voice.domain.operations import get_operation

    choices = next(a.choices for a in get_operation("source.add").args if a.name == "adapter_type")
    assert "gmail_export" in choices

    setup = service.setup()
    added = service.source_add(
        subject_id=setup["subject_id"], adapter_type="gmail_export",
        canonical_root=str(service.paths.gmail_export_dir / "alt"),
    )
    assert added["status"] == "added"


def test_digest_is_filtered_but_nine_word_reply_is_learned(service):
    sid = service.setup()['subject_id']
    digest = '\n'.join(['https://example.com'] * 20) + '\n' + 'word ' * 1000
    reply = 'I agree with this plan and will follow up.'
    service.gmail_import(author_email=_AUTHOR, messages=[
        _msg(message_id='digest', text=digest),
        _msg(message_id='reply', text=reply + '\nOn Monday,\nSynthetic Sender wrote:\nTheir original message.'),
    ])
    source = _gmail_source(service, sid)
    from operator_voice.adapters.gmail_export import scan
    result = scan(source, None)
    assert result.filter_counts['digest'] == 1
    assert [c.raw_text for c in result.candidates] == [reply]
    service.ingest(source_id=source.source_id)
    rows = service.conn.execute('SELECT source_native_id,context_json FROM documents').fetchall()
    assert len(rows) == 1 and rows[0]['source_native_id'] == 'reply'
    assert json.loads(rows[0]['context_json'])['words'] == 9
