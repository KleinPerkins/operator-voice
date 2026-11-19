"""``slack.import`` / ``slack.status`` integration coverage — OVT Slack Adapter v1 (plan §5:
"slack-import round-trip ... disposable HOME, synthetic JSON"). Exercises the real
:class:`OperatorVoiceService` against a migrated SQLite DB, in-memory-Keychain crypto, and a
temp-dir :class:`RuntimePaths` — never the operator's real Keychain, database, or Slack export.

Every message fixture here is synthetic text authored for this test suite; nothing is read from
or written to a real Slack workspace, and the CLI/service never call Slack itself (no network, no
SDK, no token — see ``operator_voice.slack.materialize``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.domain.models import AdapterType
from operator_voice.persistence import store
from operator_voice.runtime.paths import RuntimePaths
from operator_voice.slack import materialize as slack_materialize


@pytest.fixture
def service(migrated_db, in_memory_crypto, tmp_path: Path) -> OperatorVoiceService:
    """A real service WITH runtime paths set (a temp-dir XDG layout), since ``slack.import`` /
    ``slack.status`` are refused without ``self.paths`` (§ OVT Slack Adapter 1b/1d)."""
    conn, _db_path = migrated_db
    paths = RuntimePaths(config_dir=tmp_path / "config", data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    paths.ensure()
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto, paths=paths)


@pytest.fixture
def paths_only_service(migrated_db, in_memory_crypto) -> OperatorVoiceService:
    """A service with NO runtime paths — the pre-existing default shape most of this suite's other
    fixtures use — to prove ``slack.import``/``slack.status`` degrade explicitly rather than
    crashing when the caller has no export directory configured."""
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


def _msg(ts: str, text: str, author_id: str = "U1", channel: str = "#general"):
    return {"channel": channel, "message_ts": ts, "time": "2026-01-01 09:00", "text": text, "author_id": author_id}


# ---------------------------------------------------------------------------------------------
# slack.import — argument validation
# ---------------------------------------------------------------------------------------------


def test_slack_import_requires_runtime_paths(paths_only_service):
    with pytest.raises(ServiceError):
        paths_only_service.slack_import(author_id="U1", messages=[_msg("1700000000.000100", "A long enough operator message right here.")])


def test_slack_import_requires_author_id(service):
    with pytest.raises(ServiceError):
        service.slack_import(author_id=None, messages=[_msg("1700000000.000100", "A long enough operator message right here.")])


def test_slack_import_accepts_completed_empty_fetch_but_requires_list(service):
    assert service.slack_import(author_id="U1", messages=[])["imported"] == 0
    assert service.slack_status()["stale"] is False
    with pytest.raises(ServiceError):
        service.slack_import(author_id="U1", messages=None)


# ---------------------------------------------------------------------------------------------
# slack.import — happy path + on-disk effects
# ---------------------------------------------------------------------------------------------


def test_slack_import_writes_materialized_files_and_updates_state(service):
    result = service.slack_import(author_id="U1", messages=[
        _msg("1700000000.000100", "A perfectly ordinary, sufficiently long operator message."),
    ])
    assert result["status"] == "ok"
    assert result["imported"] == 1

    export_dir = service.paths.slack_export_dir
    assert (export_dir / "general.md").exists()
    state = slack_materialize.read_import_state(export_dir)
    assert state.get("last_import_at") is not None


def test_slack_import_via_file_argument(service, tmp_path):
    envelope = {"author_id": "U1", "messages": [_msg("1700000000.000100", "A file-sourced operator message, long enough to keep.")]}
    envelope_path = tmp_path / "envelope.json"
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")

    result = service.slack_import(file=str(envelope_path))
    assert result["status"] == "ok"
    assert result["imported"] == 1


def test_slack_import_file_not_found(service, tmp_path):
    with pytest.raises(ServiceError):
        service.slack_import(file=str(tmp_path / "does-not-exist.json"))


def test_slack_import_file_invalid_json(service, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ServiceError):
        service.slack_import(file=str(bad))


def test_slack_import_file_must_be_a_json_object(service, tmp_path):
    not_object = tmp_path / "list.json"
    not_object.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ServiceError):
        service.slack_import(file=str(not_object))


# ---------------------------------------------------------------------------------------------
# slack.import — server-side author verification, end to end (plan's own success criteria: "have
# an explicit test for this")
# ---------------------------------------------------------------------------------------------


def test_slack_import_rejects_mismatched_author_id_end_to_end(service):
    """The plan's headline invariant, exercised through the real service (not just the pure
    ``materialize`` module): a top-level self-asserted ``author_id`` is cross-checked against each
    message's own ``author_id`` — a mismatch is dropped and counted, never silently accepted, and
    its text never reaches disk."""
    result = service.slack_import(author_id="U_REAL_OPERATOR", messages=[
        _msg("1700000000.000100", "This message is genuinely from the operator, long enough to keep.", author_id="U_REAL_OPERATOR"),
        _msg("1700000000.000200", "This message impersonates the operator but is NOT actually theirs.", author_id="U_ATTACKER"),
    ])
    assert result["status"] == "ok"
    assert result["imported"] == 1
    assert result["skipped_mismatched_author"] == 1

    export_dir = service.paths.slack_export_dir
    text = (export_dir / "general.md").read_text(encoding="utf-8")
    assert "genuinely from the operator" in text
    assert "impersonates the operator" not in text
    assert "1700000000.000200" not in text


def test_slack_import_never_trusts_agent_side_filtering_alone(service):
    """Even if EVERY message in the batch carries the SAME (wrong) author_id — i.e. the calling
    agent's own 'these are all mine' filtering was itself wrong — the server-side check still
    catches it: nothing is accepted unless it matches the call's own top-level author_id."""
    result = service.slack_import(author_id="U_REAL_OPERATOR", messages=[
        _msg("1700000000.000100", "A message wrongly attributed to the operator by the agent.", author_id="U_SOMEONE_ELSE"),
        _msg("1700000000.000200", "Another message wrongly attributed to the operator by the agent.", author_id="U_SOMEONE_ELSE"),
    ])
    assert result["imported"] == 0
    assert result["skipped_mismatched_author"] == 2
    export_dir = service.paths.slack_export_dir
    assert not (export_dir / "general.md").exists()


# ---------------------------------------------------------------------------------------------
# slack.import -> slack_export adapter round trip
# ---------------------------------------------------------------------------------------------


def test_slack_import_then_setup_registered_adapter_scans_the_materialized_messages(service):
    """Full round trip: ``slack.import`` materializes files, ``setup()`` idempotently registers the
    ``slack_export`` source pointed at the same directory, and the registered adapter can scan the
    materialized messages back out — proving the two halves of the feature actually connect."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    assert setup["slack_source"]["status"] in {"added", "already_registered"}

    service.slack_import(author_id="U1", messages=[
        _msg("1700000000.000100", "A round-trip operator message, long enough to survive filtering."),
    ])

    sources = store.list_sources(service.conn, service.crypto, subject_id)
    slack_sources = [s for s in sources if s.adapter_type == AdapterType.SLACK_EXPORT]
    assert len(slack_sources) == 1

    result = service.ingest(source_id=slack_sources[0].source_id)
    assert result["documents_new"] >= 1


# ---------------------------------------------------------------------------------------------
# slack.status
# ---------------------------------------------------------------------------------------------


def test_slack_status_stale_when_never_imported(service):
    status = service.slack_status()
    assert status["stale"] is True
    assert status["last_import_at"] is None
    assert status["message_count"] == 0
    assert "stale" in status["brief"].lower()


def test_slack_status_fresh_immediately_after_import(service):
    service.slack_import(author_id="U1", messages=[
        _msg("1700000000.000100", "A perfectly ordinary, sufficiently long operator message."),
    ])
    status = service.slack_status()
    assert status["stale"] is False
    assert status["message_count"] == 1
    assert "fresh" in status["brief"].lower()


def test_slack_status_stale_after_threshold_elapsed(service):
    service.slack_import(author_id="U1", messages=[
        _msg("1700000000.000100", "A perfectly ordinary, sufficiently long operator message."),
    ])
    export_dir = service.paths.slack_export_dir
    state = slack_materialize.read_import_state(export_dir)
    # Push the watermark well past the staleness threshold without waiting real wall-clock time.
    state["last_import_at"] = state["last_import_at"] - (192 * 3600)
    slack_materialize.write_import_state(export_dir, state)

    status = service.slack_status()
    assert status["stale"] is True
    assert "stale" in status["brief"].lower()


def test_slack_status_without_runtime_paths_is_not_configured_not_a_crash(paths_only_service):
    status = paths_only_service.slack_status()
    assert status["stale"] is True
    assert "not configured" in status["brief"].lower()


def test_slack_status_never_surfaces_raw_message_text(service):
    """Content-free by construction: ``slack.status`` returns counts and a verdict, never text."""
    secret_text = "A very specific operator sentence that must never leak into status output."
    service.slack_import(author_id="U1", messages=[_msg("1700000000.000100", secret_text)])
    status = service.slack_status()
    assert secret_text not in json.dumps(status)


def test_source_add_accepts_slack_export_adapter_type(service):
    """``slack_export`` is a first-class ``source.add`` choice AND round-trips through the real service."""
    from operator_voice.domain.operations import get_operation

    choices = next(a.choices for a in get_operation("source.add").args if a.name == "adapter_type")
    assert "slack_export" in choices

    setup = service.setup()
    added = service.source_add(
        subject_id=setup["subject_id"], adapter_type="slack_export",
        canonical_root=str(service.paths.slack_export_dir / "alt"),
    )
    assert added["status"] == "added"
    assert added["source_id"]


def test_slack_coverage_counts_eligible_and_learned_messages(service):
    sid = service.setup()['subject_id']
    service.slack_import(author_id='U1', messages=[
        _msg('1.0', 'The first synthetic message is ready to share.'),
        _msg('2.0', 'This second synthetic message has a distinct plan.'),
        _msg('3.0', 'Thanks!'),
    ])
    source = next(s for s in store.list_sources(service.conn, service.crypto, sid) if s.adapter_type == AdapterType.SLACK_EXPORT)
    before = service.slack_status()
    assert before['ingested_count'] == 0 and before['eligible_count'] == 2
    assert '0 of 2 messages learned from' in before['brief']
    service.ingest(source_id=source.source_id)
    after = service.slack_status()
    assert after['ingested_count'] == after['eligible_count'] == 2
    assert '2 of 2 messages learned from' in after['brief']
