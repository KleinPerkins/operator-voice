"""``operator_voice.slack.materialize`` unit tests — OVT Slack Adapter v1 (plan §5). Every fixture
here is synthetic text authored for this test suite; no real Slack export is read or written.

The single most important test in this file is ``test_materialize_rejects_mismatched_author_id``:
the plan's own success criteria requires an explicit test proving authorship is verified
SERVER-SIDE against the call's top-level self-asserted ``author_id``, never trusted from the
calling agent's own "these are all mine" filtering.
"""

from __future__ import annotations

from pathlib import Path

from operator_voice.slack import materialize


# ---------------------------------------------------------------------------------------------
# clean_text / slug_channel
# ---------------------------------------------------------------------------------------------


def test_clean_text_strips_mentions_channel_refs_links_and_bold():
    raw = "<@U123> check <#C456> and *this* out: <https://example.com/plain> or <https://example.com|labelled>"
    cleaned = materialize.clean_text(raw)
    assert "<@" not in cleaned and "<#" not in cleaned
    assert "*" not in cleaned
    assert "https://example.com/plain" in cleaned
    assert "labelled" in cleaned and "|" not in cleaned


def test_clean_text_strips_surrounding_whitespace():
    assert materialize.clean_text("   hello there   \n") == "hello there"


def test_slug_channel_hash_prefixed():
    assert materialize.slug_channel("#Card Platform") == "card-platform"


def test_slug_channel_dm():
    assert materialize.slug_channel("DM: Evan Eisman") == "dm-misc"


def test_slug_channel_group_dm():
    assert materialize.slug_channel("Group DM: Evan, Alex") == "group-dm-misc"


def test_slug_channel_fallback_normalizes_arbitrary_text():
    assert materialize.slug_channel("My Weird Channel!!") == "my-weird-channel"


def test_slug_channel_empty_falls_back_to_slack():
    assert materialize.slug_channel("") == "slack"
    assert materialize.slug_channel(None) == "slack"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# materialize() — happy path
# ---------------------------------------------------------------------------------------------


_OPERATOR = "U1"


def _msg(ts: str, text: str, author_id: str = _OPERATOR, channel: str = "#general", **extra):
    return {"channel": channel, "message_ts": ts, "time": "2026-01-01 09:00", "text": text, "author_id": author_id, **extra}


def test_materialize_writes_one_file_per_channel(tmp_path: Path):
    result = materialize.materialize(
        tmp_path,
        author_id=_OPERATOR,
        messages=[
            _msg("1700000000.000100", "This is a genuinely long enough operator message for import.", channel="#general"),
            _msg("1700000000.000200", "This is a second, separate, sufficiently long operator message.", channel="#eng"),
        ],
    )
    assert result.imported == 2
    assert result.channels == {"general", "eng"}
    assert (tmp_path / "general.md").exists()
    assert (tmp_path / "eng.md").exists()


def test_materialize_embeds_parseable_message_ts_marker(tmp_path: Path):
    materialize.materialize(
        tmp_path, author_id="U1",
        messages=[_msg("1700000000.000100", "A sufficiently long single operator message right here.")],
    )
    text = (tmp_path / "general.md").read_text(encoding="utf-8")
    assert "<!-- ovt:message ts=1700000000.000100 -->" in text
    parsed = materialize.parse_channel_file(text)
    assert parsed == [("1700000000.000100", "A sufficiently long single operator message right here.")]


# ---------------------------------------------------------------------------------------------
# materialize() — server-side author verification (THE critical invariant)
# ---------------------------------------------------------------------------------------------


def test_materialize_rejects_mismatched_author_id(tmp_path: Path):
    """The plan's own success criteria: 'slack-import rejects any message whose per-message
    author_id doesn't match the call's own top-level asserted author_id — server-verified, not
    agent-trusted.' A mismatch must be dropped, counted, and never written to disk."""
    result = materialize.materialize(
        tmp_path,
        author_id="U_OPERATOR",
        messages=[
            _msg("1700000000.000100", "This message genuinely belongs to the operator, long enough.", author_id="U_OPERATOR"),
            _msg("1700000000.000200", "This message claims someone else entirely wrote it, impersonation attempt.", author_id="U_IMPOSTER"),
        ],
    )
    assert result.imported == 1
    assert result.skipped_mismatched_author == 1

    text = (tmp_path / "general.md").read_text(encoding="utf-8")
    assert "operator, long enough" in text
    assert "impersonation attempt" not in text
    assert "1700000000.000200" not in text


def test_materialize_mismatched_author_never_written_even_alone(tmp_path: Path):
    """A batch containing ONLY a mismatched message must produce no channel file at all — never a
    silently-accepted fallback."""
    result = materialize.materialize(
        tmp_path, author_id="U_OPERATOR",
        messages=[_msg("1700000000.000100", "An entirely third-party authored message, not the operator's own.", author_id="U_OTHER")],
    )
    assert result.imported == 0
    assert result.skipped_mismatched_author == 1
    assert result.channels == set()
    assert not (tmp_path / "general.md").exists()


def test_materialize_case_sensitive_author_id_match(tmp_path: Path):
    """Author verification is a literal string match — no normalization that could widen who counts
    as 'the operator' beyond the exact self-asserted id."""
    result = materialize.materialize(
        tmp_path, author_id="U_OPERATOR",
        messages=[_msg("1700000000.000100", "Message text long enough to pass the short filter easily.", author_id="u_operator")],
    )
    assert result.imported == 0
    assert result.skipped_mismatched_author == 1


# ---------------------------------------------------------------------------------------------
# materialize() — retain short authored messages
# ---------------------------------------------------------------------------------------------


def test_materialize_keeps_short_single_line_message(tmp_path: Path):
    result = materialize.materialize(tmp_path, author_id="U1", messages=[_msg("1700000000.000100", "lol ok")])
    assert result.imported == 1
    assert result.skipped_short == 0


def test_materialize_keeps_short_multiline_message(tmp_path: Path):
    result = materialize.materialize(tmp_path, author_id="U1", messages=[_msg("1700000000.000100", "line one\nline two")])
    assert result.imported == 1
    assert result.skipped_short == 0


# ---------------------------------------------------------------------------------------------
# materialize() — invalid-entry handling (quarantine-not-abort at the batch level)
# ---------------------------------------------------------------------------------------------


def test_materialize_skips_non_dict_entries_without_aborting(tmp_path: Path):
    result = materialize.materialize(
        tmp_path, author_id="U1",
        messages=["not-a-dict", _msg("1700000000.000100", "A perfectly fine long enough operator message.")],
    )
    assert result.imported == 1
    assert result.skipped_invalid == 1


def test_materialize_skips_entries_missing_message_ts(tmp_path: Path):
    bad = _msg("1700000000.000100", "Some long enough operator text right here for the test.")
    del bad["message_ts"]
    result = materialize.materialize(tmp_path, author_id="U1", messages=[bad])
    assert result.imported == 0
    assert result.skipped_invalid == 1


def test_materialize_skips_entries_missing_author_id(tmp_path: Path):
    bad = _msg("1700000000.000100", "Some long enough operator text right here for the test.")
    del bad["author_id"]
    result = materialize.materialize(tmp_path, author_id="U1", messages=[bad])
    assert result.imported == 0
    assert result.skipped_invalid == 1


def test_materialize_skips_entries_with_non_string_text(tmp_path: Path):
    bad = _msg("1700000000.000100", "placeholder")
    bad["text"] = 12345
    result = materialize.materialize(tmp_path, author_id="U1", messages=[bad])
    assert result.imported == 0
    assert result.skipped_invalid == 1


# ---------------------------------------------------------------------------------------------
# materialize() — idempotent re-import (message-level dedup)
# ---------------------------------------------------------------------------------------------


def test_materialize_is_idempotent_across_two_calls(tmp_path: Path):
    msg = _msg("1700000000.000100", "A perfectly fine long enough operator message right here.")
    first = materialize.materialize(tmp_path, author_id="U1", messages=[msg])
    assert first.imported == 1

    second = materialize.materialize(tmp_path, author_id="U1", messages=[msg])
    assert second.imported == 0
    assert second.skipped_duplicate == 1

    text = (tmp_path / "general.md").read_text(encoding="utf-8")
    assert text.count("1700000000.000100") == 1  # never appended twice


def test_materialize_dedupes_within_the_same_batch(tmp_path: Path):
    msg = _msg("1700000000.000100", "A perfectly fine long enough operator message right here.")
    result = materialize.materialize(tmp_path, author_id="U1", messages=[msg, dict(msg)])
    assert result.imported == 1
    assert result.skipped_duplicate == 1


def test_materialize_second_call_appends_new_messages_to_existing_channel_file(tmp_path: Path):
    materialize.materialize(tmp_path, author_id="U1", messages=[_msg("1700000000.000100", "First operator message, long enough to keep.")])
    materialize.materialize(tmp_path, author_id="U1", messages=[_msg("1700000000.000200", "Second operator message, also long enough to keep.")])
    text = (tmp_path / "general.md").read_text(encoding="utf-8")
    parsed = materialize.parse_channel_file(text)
    assert [ts for ts, _ in parsed] == ["1700000000.000100", "1700000000.000200"]


# ---------------------------------------------------------------------------------------------
# parse_channel_file / count_messages_by_channel
# ---------------------------------------------------------------------------------------------


def test_parse_channel_file_round_trip_multiple_messages(tmp_path: Path):
    materialize.materialize(
        tmp_path, author_id="U1",
        messages=[
            _msg("1700000000.000100", "First long enough operator message for this test."),
            _msg("1700000000.000200", "Second long enough operator message for this test."),
        ],
    )
    text = (tmp_path / "general.md").read_text(encoding="utf-8")
    parsed = materialize.parse_channel_file(text)
    assert len(parsed) == 2
    assert parsed[0][0] == "1700000000.000100"
    assert parsed[1][0] == "1700000000.000200"


def test_count_messages_by_channel_missing_dir_returns_empty(tmp_path: Path):
    assert materialize.count_messages_by_channel(tmp_path / "does-not-exist") == {}


def test_count_messages_by_channel_counts_per_channel(tmp_path: Path):
    materialize.materialize(
        tmp_path, author_id="U1",
        messages=[
            _msg("1700000000.000100", "General channel message long enough to keep here.", channel="#general"),
            _msg("1700000000.000200", "Engineering channel message long enough to keep here.", channel="#eng"),
            _msg("1700000000.000300", "A second general channel message, also long enough.", channel="#general"),
        ],
    )
    counts = materialize.count_messages_by_channel(tmp_path)
    assert counts == {"general": 2, "eng": 1}


# ---------------------------------------------------------------------------------------------
# import-state sidecar
# ---------------------------------------------------------------------------------------------


def test_import_state_round_trip(tmp_path: Path):
    materialize.write_import_state(tmp_path, {"last_import_at": 1700000000.0})
    assert materialize.read_import_state(tmp_path) == {"last_import_at": 1700000000.0}


def test_read_import_state_missing_file_is_empty(tmp_path: Path):
    assert materialize.read_import_state(tmp_path) == {}


def test_read_import_state_corrupt_json_is_empty_not_an_error(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".import-state.json").write_text("{not valid json", encoding="utf-8")
    assert materialize.read_import_state(tmp_path) == {}


def test_read_import_state_non_dict_json_is_empty(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".import-state.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert materialize.read_import_state(tmp_path) == {}
