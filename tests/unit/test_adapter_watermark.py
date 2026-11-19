"""M2: change-ordered watermark (plan single-voice-bounded-context-v1). Synthetic fixtures only."""
import os
from pathlib import Path

from operator_voice.adapters import base, slack_export
from operator_voice.adapters.base import FileWatermark
from operator_voice.domain.models import AdapterType, SourceRegistration, now_ts
from operator_voice.slack.materialize import materialize as slack_materialize


def _reg(root, adapter):
    return SourceRegistration(source_id="src_test", subject_id="subj_test", adapter_type=adapter,
                              canonical_root=str(root), opted_in_at=now_ts(), approved_purpose="voice_generation",
                              active=True, role_identity={"subject_role": "user"})


def _bump(path, seconds):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


def _msg(channel, ts, text=None):
    return {"channel": f"#{channel}", "message_ts": ts, "author_id": "U1",
            "text": text or f"A synthetic message long enough to keep, number {ts}."}


def _slack(root, channel, ts, bump=0):
    slack_materialize(root, author_id="U1", messages=[_msg(channel, ts)])
    if bump:
        _bump(root / f"{channel}.md", bump)


def test_new_file_sorting_before_the_watermark_is_scanned(tmp_path):
    _slack(tmp_path, "zeta", "1700000000.000100")
    first = slack_export.scan(_reg(tmp_path, AdapterType.SLACK_EXPORT), None)
    _slack(tmp_path, "alpha", "1700000100.000100", bump=5)
    second = slack_export.scan(_reg(tmp_path, AdapterType.SLACK_EXPORT), first.next_cursor)
    assert [c.source_native_id for c in second.candidates] == ["alpha:1700000100.000100"]


def test_file_that_gains_messages_is_rescanned(tmp_path):
    _slack(tmp_path, "c", "1700000000.000100")
    first = slack_export.scan(_reg(tmp_path, AdapterType.SLACK_EXPORT), None)
    _slack(tmp_path, "c", "1700000200.000100", bump=5)
    second = slack_export.scan(_reg(tmp_path, AdapterType.SLACK_EXPORT), first.next_cursor)
    assert "c:1700000200.000100" in {c.source_native_id for c in second.candidates}


def test_cap_applies_after_the_watermark_filter(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "MAX_FILES_PER_SCAN", 3)
    for i in range(5):
        _slack(tmp_path, f"n{i}", f"170000000{i}.000100", bump=i + 1)
    first = slack_export.scan(_reg(tmp_path, AdapterType.SLACK_EXPORT), None)
    assert len(first.candidates) == 3 and first.has_more
    second = slack_export.scan(_reg(tmp_path, AdapterType.SLACK_EXPORT), first.next_cursor)
    assert len(second.candidates) == 2 and not second.has_more


def test_legacy_path_cursor_triggers_one_full_rescan(tmp_path):
    for name in ("a", "b"):
        _slack(tmp_path, name, "1700000000.000100")
    rescanned = slack_export.scan(_reg(tmp_path, AdapterType.SLACK_EXPORT), str(tmp_path / "b.md"))
    assert {c.source_native_id for c in rescanned.candidates} == {
        "a:1700000000.000100", "b:1700000000.000100"}
    assert FileWatermark.decode(rescanned.next_cursor) is not None


def test_short_messages_are_kept_on_disk_but_not_analyzed_under_features_v1(tmp_path):
    from operator_voice.slack.materialize import parse_channel_file
    slack_materialize(tmp_path, author_id="U1", messages=[_msg("c", "1700000000.000100", "ok"),
                                                          _msg("c", "1700000000.000200")])
    assert len(parse_channel_file((tmp_path / "c.md").read_text(encoding="utf-8"))) == 2
    result = slack_export.scan(_reg(tmp_path, AdapterType.SLACK_EXPORT), None)
    assert [c.source_native_id for c in result.candidates] == ["c:1700000000.000200"]
