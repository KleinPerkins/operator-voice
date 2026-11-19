"""Unit tests for ``google.status`` aggregation — deterministic, no network."""

from __future__ import annotations

from operator_voice.google.status import aggregate_google_status, build_google_brief

_STALE_AFTER = 24.0


def _drive(*, stale: bool = True, file_count: int = 0, record_count: int = 0, brief: str | None = None) -> dict:
    if brief is None:
        brief = (
            "drive-voice: stale — ask agent to refresh Drive voice"
            if stale
            else f"drive-voice: fresh ({file_count} files, {record_count} revisions, last import 2026-08-07 12:00 UTC)"
        )
    return {
        "last_import_at": None if stale else 1_000_000.0,
        "file_count": file_count,
        "record_count": record_count,
        "files": {},
        "sync_cursor": None,
        "stale": stale,
        "stale_after_hours": _STALE_AFTER,
        "brief": brief,
    }


def _gmail(*, stale: bool = True, message_count: int = 0, brief: str | None = None) -> dict:
    if brief is None:
        brief = (
            "gmail-voice: stale — ask agent to refresh Gmail voice"
            if stale
            else f"gmail-voice: fresh ({message_count} messages, last import 2026-08-07 12:00 UTC)"
        )
    return {
        "last_import_at": None if stale else 1_000_000.0,
        "message_count": message_count,
        "sync_cursor": None,
        "stale": stale,
        "stale_after_hours": _STALE_AFTER,
        "brief": brief,
    }


def test_brief_not_configured_when_both_channels_lack_runtime_paths():
    drive = _drive(brief="drive-voice: not configured (no runtime paths)")
    gmail = _gmail(brief="gmail-voice: not configured (no runtime paths)")
    assert build_google_brief(drive=drive, gmail=gmail) == (
        "google-voice: not configured (no runtime paths)"
    )


def test_brief_stale_lists_each_channel():
    drive = _drive(stale=True)
    gmail = _gmail(stale=False, message_count=4)
    brief = build_google_brief(drive=drive, gmail=gmail)
    assert brief.startswith("google-voice: stale — ask agent to refresh Google voice (")
    assert "drive stale" in brief
    assert "gmail fresh (4 messages)" in brief


def test_brief_fresh_summarizes_counts():
    drive = _drive(stale=False, file_count=2, record_count=5)
    gmail = _gmail(stale=False, message_count=9)
    assert build_google_brief(drive=drive, gmail=gmail) == (
        "google-voice: fresh (drive: 2 files/5 revisions, gmail: 9 messages)"
    )


def test_aggregate_marks_stale_when_either_channel_is_stale():
    result = aggregate_google_status(
        drive=_drive(stale=True),
        gmail=_gmail(stale=False, message_count=1),
        stale_after_hours=_STALE_AFTER,
    )
    assert result["stale"] is True
    assert result["drive"]["file_count"] == 0
    assert result["gmail"]["message_count"] == 1
    assert "google-voice:" in result["brief"]


def test_aggregate_fresh_only_when_both_channels_fresh():
    result = aggregate_google_status(
        drive=_drive(stale=False, file_count=1, record_count=1),
        gmail=_gmail(stale=False, message_count=3),
        stale_after_hours=_STALE_AFTER,
    )
    assert result["stale"] is False
    assert "fresh" in result["brief"]
