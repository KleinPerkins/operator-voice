"""``operator_voice.gmail.materialize`` unit tests — OVT Gmail Adapter v1 (plan
``docs/plans/gmail-drive-channel-adapters-v2.md`` §4). Synthetic fixtures only; no real Gmail access.
"""

from __future__ import annotations

from pathlib import Path

from operator_voice.gmail import materialize

_AUTHOR = "operator@example.com"
_LONG = "This is a genuinely long enough operator-authored Sent message body for the import to keep it."


def _msg(
    *, message_id: str = "msg-1", thread_id: str = "thread-1",
    internal_date: str = "2026-07-30T14:02:00Z", from_addr: str = _AUTHOR,
    text: str = _LONG, subject: str = "A subject", **extra,
) -> dict:
    entry = {
        "message_id": message_id, "thread_id": thread_id, "internal_date": internal_date,
        "from": from_addr, "subject": subject, "text": text,
    }
    entry.update(extra)
    return entry


def test_materialize_writes_one_immutable_timestamp_prefixed_file(tmp_path: Path):
    result = materialize.materialize(tmp_path, author_email=_AUTHOR, messages=[_msg()])
    assert result.imported == 1
    assert result.messages == {"msg-1"}
    written = list(tmp_path.glob("*.md"))
    assert len(written) == 1
    assert written[0].name == "2026-07-30T14-02-00Z__msg-1.md"


def test_materialize_embeds_parseable_marker(tmp_path: Path):
    materialize.materialize(tmp_path, author_email=_AUTHOR, messages=[_msg()])
    text = next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
    assert "<!-- ovt:gmail id=msg-1 thread=thread-1 internal=2026-07-30T14:02:00Z -->" in text
    parsed = materialize.parse_export_file(text)
    assert len(parsed) == 1
    assert parsed[0]["message_id"] == "msg-1"
    assert parsed[0]["thread_id"] == "thread-1"
    assert parsed[0]["internal_date"] == "2026-07-30T14:02:00Z"
    assert parsed[0]["body"] == _LONG


def test_materialize_rejects_mismatched_author_email(tmp_path: Path):
    """G-M2-1. Impersonated From dropped (mirrors Slack's mismatched author_id test)."""
    result = materialize.materialize(
        tmp_path, author_email=_AUTHOR,
        messages=[
            _msg(message_id="mine"),
            _msg(message_id="theirs", from_addr="someone-else@example.com"),
        ],
    )
    assert result.imported == 1
    assert result.skipped_mismatched_author == 1
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_materialize_accepts_sender_email_field(tmp_path: Path):
    result = materialize.materialize(
        tmp_path, author_email=_AUTHOR,
        messages=[{"message_id": "m1", "thread_id": "t1", "internal_date": "2026-07-30T14:02:00Z",
                     "sender_email": _AUTHOR, "text": _LONG}],
    )
    assert result.imported == 1


def test_materialize_skips_duplicate_message_id_file(tmp_path: Path):
    materialize.materialize(tmp_path, author_email=_AUTHOR, messages=[_msg()])
    result = materialize.materialize(tmp_path, author_email=_AUTHOR, messages=[_msg()])
    assert result.imported == 0
    assert result.skipped_duplicate == 1


def test_materialize_skips_short_body(tmp_path: Path):
    result = materialize.materialize(tmp_path, author_email=_AUTHOR, messages=[_msg(text="short")])
    assert result.imported == 0
    assert result.skipped_short == 1


def test_import_state_round_trip(tmp_path: Path):
    materialize.write_import_state(tmp_path, {"sync_cursor": {"history_id": "999"}, "last_import_at": 1.0})
    loaded = materialize.read_import_state(tmp_path)
    assert loaded["sync_cursor"] == {"history_id": "999"}
    assert loaded["last_import_at"] == 1.0


def test_count_messages(tmp_path: Path):
    materialize.materialize(tmp_path, author_email=_AUTHOR, messages=[
        _msg(message_id="a", internal_date="2026-07-30T14:02:00Z"),
        _msg(message_id="b", internal_date="2026-07-31T10:00:00Z"),
    ])
    assert materialize.count_messages(tmp_path) == 2


def test_sent_metadata_and_own_text(tmp_path):
    messages = [
        _msg(message_id='missing-sent', labels=['INBOX']),
        _msg(message_id='draft', labels=['SENT', 'DRAFT']),
        _msg(message_id='chat', labels=['SENT', 'CHAT']),
        _msg(message_id='auto', auto_submitted='auto-replied'),
        _msg(message_id='delegate', sender='Other <other@example.com>'),
        _msg(message_id='empty', text='On Monday,\nSynthetic Sender wrote:\nA sufficiently long quoted message.'),
        _msg(message_id='reply', from_addr='Operator <OPERATOR@EXAMPLE.COM>', sender='Operator <OPERATOR@EXAMPLE.COM>',
             labels=['SENT'], auto_submitted=' NO ', text='My own reply.\nOn Monday,\nSynthetic Sender wrote:\nTheir words.'),
        _msg(message_id='legacy'),
    ]
    result = materialize.materialize(tmp_path, author_email=_AUTHOR, messages=messages)
    assert result.skipped_not_sent == 3
    assert result.skipped_auto_reply == result.skipped_delegate_sent == result.skipped_no_own_text == 1
    assert result.imported == 2
    reply = next(tmp_path.glob('*__reply.md')).read_text()
    assert '## Sent message' in reply
    assert materialize.parse_export_file(reply)[0]['body'] == 'My own reply.'


def test_trim_preserves_markers_and_empty_records_and_is_idempotent(tmp_path):
    marker = '<!-- ovt:gmail id=m thread=t internal=2026-07-30T14:02:00Z -->'
    path = tmp_path / 'stored.md'
    path.write_text(marker + '\n## Someone else subject\n\nMine.\nOn Monday,\nSynthetic Sender wrote:\nTheirs.\n')
    assert materialize.trim_export_file(path)
    first = path.read_bytes()
    assert not materialize.trim_export_file(path)
    assert path.read_bytes() == first and marker in first.decode()
    assert materialize.parse_export_file(first.decode())[0]['body'] == 'Mine.'
    path.write_text(marker + '\n## Old heading\n\nOn Monday,\nSynthetic Sender wrote:\nTheirs.\n')
    assert materialize.trim_export_file(path)
    assert materialize.parse_export_file(path.read_text())[0]['body'] == ''
    assert marker in path.read_text()
    path.write_text(marker + '\n## Old heading\n\nMine.\n')
    assert not materialize.trim_export_file(path)
    assert '## Sent message' in path.read_text()
