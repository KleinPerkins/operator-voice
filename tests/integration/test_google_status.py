"""``google.status`` integration coverage — M8 unified Google freshness (plan §6 M8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operator_voice.application.service import OperatorVoiceService
from operator_voice.runtime.paths import RuntimePaths

_AUTHOR = "operator@example.com"
_OWNER = "owner-123"
_LONG = "A perfectly ordinary, sufficiently long operator-authored body for this test."
_DOC_MIME = "application/vnd.google-apps.document"


@pytest.fixture
def service(migrated_db, in_memory_crypto, tmp_path: Path) -> OperatorVoiceService:
    conn, _db_path = migrated_db
    paths = RuntimePaths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    paths.ensure()
    svc = OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto, paths=paths)
    svc.setup()
    svc.drive_folder_add(folder_id="writing")
    return svc


@pytest.fixture
def paths_only_service(migrated_db, in_memory_crypto) -> OperatorVoiceService:
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


def _drive_file(**overrides) -> dict:
    base = {
        "file_id": "file-1", "revision_id": "rev-1", "drive_id": None,
        "owners": [_OWNER], "mime_type": _DOC_MIME, "modified_time": "2026-07-30T14:02:00Z",
        "owner_id": _OWNER, "text": _LONG, "folder_id": "writing",
    }
    base.update(overrides)
    return base


def _gmail_msg(**overrides) -> dict:
    base = {
        "message_id": "msg-1", "thread_id": "thread-1", "internal_date": "2026-07-30T14:02:00Z",
        "from": _AUTHOR, "subject": "Hello", "text": _LONG,
    }
    base.update(overrides)
    return base


def test_google_status_stale_when_neither_channel_imported(service):
    status = service.google_status()
    assert status["stale"] is True
    assert "stale" in status["brief"].lower()
    assert "drive" in status
    assert "gmail" in status


def test_google_status_fresh_when_both_channels_imported(service):
    service.drive_import(owner_id=_OWNER, files=[_drive_file()])
    service.gmail_import(author_email=_AUTHOR, messages=[_gmail_msg()])
    status = service.google_status()
    assert status["stale"] is False
    assert "fresh" in status["brief"].lower()
    assert status["drive"]["file_count"] == 1
    assert status["gmail"]["message_count"] == 1


def test_google_status_stale_when_only_one_channel_imported(service):
    service.drive_import(owner_id=_OWNER, files=[_drive_file()])
    status = service.google_status()
    assert status["stale"] is True
    assert "drive fresh" in status["brief"]
    assert "gmail stale" in status["brief"]


def test_google_status_without_runtime_paths_is_not_configured(paths_only_service):
    status = paths_only_service.google_status()
    assert status["stale"] is True
    assert "not configured" in status["brief"].lower()


def test_google_status_never_surfaces_raw_text(service):
    drive_secret = "A very specific operator sentence that must never leak into google-status drive output."
    gmail_secret = "A very specific operator sentence that must never leak into google-status gmail output."
    service.drive_import(owner_id=_OWNER, files=[_drive_file(text=drive_secret)])
    service.gmail_import(author_email=_AUTHOR, messages=[_gmail_msg(text=gmail_secret)])
    status = service.google_status()
    dumped = json.dumps(status)
    assert drive_secret not in dumped
    assert gmail_secret not in dumped
