"""Continual-curation integration coverage — build plan v4 §13 M3 worklist 2/3 and M3 acceptance.
Exercises the single ``curate`` tick end-to-end against a real migrated SQLite DB + in-memory
Keychain crypto, with explicit adversarial coverage for the five properties the plan requires:
incremental correctness, catch-up after a simulated downtime gap, strict idempotency (on re-run AND
under crash-mid-run recovery), stale-operation recovery, retention, and per-source fault isolation.

Every fixture is synthetic, written for this suite. No live operator transcript, no real source,
no recurring job over real data, no network.
"""

from __future__ import annotations

from synthetic_sources import write_slack_notes

from dataclasses import replace
from pathlib import Path

import pytest

from operator_voice.application.service import OperatorVoiceService
from operator_voice.domain.models import CurationRun, CurationRunStatus, CurationTrigger, now_ts
from operator_voice.persistence import store
from operator_voice.persistence.sqlite import transaction

_counter = {"n": 0}

_SYNTHETIC_NOTES = [
    "I think we should ship this feature next week. It is ready, and the team agrees. Let's go.",
    "Honestly, I think this is the right call. We should ship it soon, and I trust the team here.",
    "We should definitely ship this next sprint. I believe the team is ready, and so am I, clearly.",
]


@pytest.fixture
def service(migrated_db, in_memory_crypto):
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


def _write_slack_notes(tmp_path: Path, texts: list[str]) -> Path:
    _counter["n"] += 1
    root = tmp_path / f"slack-drop-{_counter['n']}"
    root.mkdir()
    write_slack_notes(root, texts, conversations=len(texts))
    return root


def _add_slack_source(service, subject_id, root) -> str:
    return service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))["source_id"]


def _doc_count(service) -> int:
    return service.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


# --------------------------------------------------------------------------------------
# Inert / no-op / off
# --------------------------------------------------------------------------------------


def test_curate_is_inert_noop_with_no_active_source(service):
    """A tick with zero enrolled sources is a safe NOOP — this is what makes the registered
    local-jobs instance inert until the operator opts a source in (M3 testing constraint)."""
    service.setup()
    result = service.curate(trigger="scheduled")
    assert result["status"] == "NOOP"
    assert result["sources_total"] == 0
    assert result["documents_new"] == 0
    assert result["publish_status"] == "skipped_no_changes"
    # a durable content-free run row exists
    runs = service.curation_status()["runs"]
    assert runs and runs[0]["status"] == "NOOP"


def test_curate_while_off_is_skipped_not_an_error(service):
    service.setup()
    service.off(reason="testing")
    result = service.curate(trigger="scheduled")
    assert result["status"] == "skipped_off"
    # recorded as SKIPPED_OFF, never an error row
    assert service.curation_status()["runs"][0]["status"] == "SKIPPED_OFF"


def test_curate_trigger_defaults_from_env(service, monkeypatch):
    service.setup()
    monkeypatch.setenv("OVT_CURATE_TRIGGER", "scheduled")
    result = service.curate()  # no explicit trigger → env default
    assert result["trigger"] == "scheduled"


# --------------------------------------------------------------------------------------
# Ingest parity, incremental, idempotency
# --------------------------------------------------------------------------------------


def test_curate_ingests_active_sources_and_stamps_generation(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    _add_slack_source(service, subject_id, root)

    result = service.curate(trigger="manual")
    assert result["status"] == "COMPLETED"
    assert result["sources_total"] == 1 and result["sources_processed"] == 1
    assert result["documents_new"] == 3
    assert result["observations_new"] > 0
    assert result["generation_ids"]  # one generation stamped for the tick
    # LOCAL_ONLY default performs no network publication
    assert result["published"] is False
    assert result["publish_status"] == "skipped_local_only"

    facts = {f["facet"] for f in service.profile_inspect(subject_id=subject_id)["facts"]}
    assert "sentence_length" in facts


def test_curate_is_incremental_only_new_content(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    _add_slack_source(service, subject_id, root)

    first = service.curate()
    assert first["documents_new"] == 3

    # a new document appears in the SAME source root after the first tick
    write_slack_notes(root, ["A brand new operator note, added after the first curation tick, ready to ship."], conversations=100)
    second = service.curate()
    assert second["documents_new"] == 1  # only the new file, not the original three
    assert second["status"] == "COMPLETED"


def test_curate_is_idempotent_on_rerun(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    _add_slack_source(service, subject_id, root)

    service.curate()
    facts_before = service.profile_inspect(subject_id=subject_id)["facts"]
    docs_before = _doc_count(service)

    second = service.curate()
    assert second["documents_new"] == 0 and second["observations_new"] == 0
    assert second["publish_status"] == "skipped_no_changes"
    assert _doc_count(service) == docs_before  # no duplicate documents on re-run

    facts_after = service.profile_inspect(subject_id=subject_id)["facts"]
    # byte-identical facts (facet/value/state) — no duplicate or drifted facts from a re-run
    def _key(fs):
        return sorted((f["facet"], f["scope"], str(f["value"]), f["state"]) for f in fs)
    assert _key(facts_before) == _key(facts_after)


def test_curate_bounded_continuation_drains_backlog_across_ticks(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, [f"Operator note {i} with enough distinct prose to ingest cleanly." for i in range(5)])
    _add_slack_source(service, subject_id, root)

    t1 = service.curate(per_source_limit=2)
    assert t1["documents_new"] == 2 and t1["pending_continuation"] is True
    t2 = service.curate(per_source_limit=2)
    assert t2["documents_new"] == 2 and t2["pending_continuation"] is True
    t3 = service.curate(per_source_limit=2)
    assert t3["documents_new"] == 1 and t3["pending_continuation"] is False
    assert _doc_count(service) == 5  # exactly the five, none duplicated


# --------------------------------------------------------------------------------------
# Catch-up after downtime
# --------------------------------------------------------------------------------------


def test_curate_reports_catch_up_gap_after_simulated_downtime(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    _add_slack_source(service, subject_id, root)

    first = service.curate()
    first_run_id = first["run_id"]
    # Simulate the Mac having been asleep/off for an hour: backdate the completed run's finished_at.
    service.conn.execute(
        "UPDATE curation_runs SET finished_at = ? WHERE run_id = ?", (now_ts() - 3600, first_run_id)
    )
    service.conn.commit()

    # New content accumulated during the "downtime". The Slack v1 adapter's cursor is the last-seen
    # filename (lexicographic), so a backlog file must sort AFTER the watermark to be caught up —
    # true for append/timestamp-ordered sources (the realistic passive case). Named accordingly.
    write_slack_notes(root, ["A note I wrote while the machine was asleep, now caught up on next tick."], conversations=100)
    second = service.curate()
    assert second["catch_up_gap_seconds"] is not None
    assert second["catch_up_gap_seconds"] >= 3500  # ~1h gap surfaced
    assert second["documents_new"] == 1  # the backlog was processed on the catch-up tick


# --------------------------------------------------------------------------------------
# Stale-run recovery + crash-mid-run idempotency
# --------------------------------------------------------------------------------------


def test_curate_reclaims_stale_running_run(service, tmp_path):
    """A RUNNING row left by a crash/kill/long-sleep older than the stale threshold is reclaimed as
    RECOVERED on the next tick (§13 M3 worklist 3 stale-operation recovery)."""
    service.setup()
    stale = CurationRun(
        run_id="crun_stale", trigger=CurationTrigger.SCHEDULED, status=CurationRunStatus.RUNNING,
        started_at=now_ts() - (store.STALE_CURATION_RUN_SECONDS + 3600),
    )
    with transaction(service.conn):
        store.insert_curation_run(service.conn, stale)

    result = service.curate(trigger="scheduled")
    assert result["recovered_runs"] >= 1
    assert store.get_curation_run(service.conn, "crun_stale").status == CurationRunStatus.RECOVERED


def test_curate_crash_mid_run_leaves_committed_work_and_reruns_idempotently(service, tmp_path, monkeypatch):
    """Simulate a hard crash at the very end of a tick (after per-source work committed, before the
    run row is finalized): the committed documents survive and a fresh curate re-run adds nothing
    (strict idempotency under crash-mid-run recovery)."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    _add_slack_source(service, subject_id, root)

    real_update = store.update_curation_run

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated crash before run finalization")

    monkeypatch.setattr(store, "update_curation_run", _boom)
    with pytest.raises(RuntimeError):
        service.curate()
    monkeypatch.setattr(store, "update_curation_run", real_update)

    # The per-source ingest committed in its own transaction, so the documents are durable.
    docs_after_crash = _doc_count(service)
    assert docs_after_crash == 3
    # A RUNNING row from the crashed tick is still present (a trace to recover from).
    assert any(r.status == CurationRunStatus.RUNNING for r in store.list_curation_runs(service.conn, limit=10))

    # Re-running curate must NOT reprocess the already-ingested documents.
    result = service.curate()
    assert result["status"] == "COMPLETED"
    assert result["documents_new"] == 0
    assert _doc_count(service) == docs_after_crash


# --------------------------------------------------------------------------------------
# Fault isolation + retention
# --------------------------------------------------------------------------------------


def test_curate_isolates_a_failing_source_and_continues(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    good_root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    good_id = _add_slack_source(service, subject_id, good_root)

    # A source whose canonical_root does not exist → the adapter raises AdapterError at scan time.
    bad_id = service.source_add(
        subject_id=subject_id, adapter_type="slack_export", canonical_root=str(tmp_path / "does-not-exist"),
    )["source_id"]

    result = service.curate()
    assert result["sources_total"] == 2
    assert result["sources_processed"] == 1  # the good source succeeded
    assert result["sources_failed"] == 1     # the bad source failed but did NOT abort the tick
    assert result["status"] == "COMPLETED"
    assert result["documents_new"] == 3      # good source's work still landed

    states = {s["source_id"]: s for s in service.curation_status()["sources"]}
    assert states[good_id]["consecutive_failures"] == 0
    assert states[bad_id]["consecutive_failures"] >= 1
    assert states[bad_id]["last_error_class"]  # recorded, content-free


def test_curate_preserves_zero_day_retention(service, tmp_path):
    """Zero-day content retention (§5.1) must hold through the curate path exactly as it does for
    ingest: no document keeps raw content after the tick."""
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    _add_slack_source(service, subject_id, root)
    service.curate()

    rows = service.conn.execute("SELECT content_enc FROM documents WHERE subject_id = ?", (subject_id,)).fetchall()
    assert rows and all(row[0] is None for row in rows)


# --------------------------------------------------------------------------------------
# Learning / correction-uptake reporting
# --------------------------------------------------------------------------------------


def test_curation_status_surfaces_per_source_watermarks(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    source_id = _add_slack_source(service, subject_id, root)

    before = {s["source_id"]: s for s in service.curation_status()["sources"]}
    assert before[source_id]["adapter_type"] == "slack_export"
    assert before[source_id]["watermark"] is None

    service.curate()

    after = {s["source_id"]: s for s in service.curation_status()["sources"]}
    assert after[source_id]["watermark"] is not None
    assert after[source_id]["last_curated_at"] is not None
    assert after[source_id]["consecutive_failures"] == 0


def test_curation_and_learning_status_report_correction_uptake(service, tmp_path):
    setup = service.setup()
    subject_id = setup["subject_id"]
    root = _write_slack_notes(tmp_path, _SYNTHETIC_NOTES)
    _add_slack_source(service, subject_id, root)
    service.curate()

    # An operator correction is P0 and must show up in the uptake snapshot.
    service.fact_correct(subject_id=subject_id, facet="tone", new_value="direct and warm", reason="that's me")

    learning = service.learning_status()["learning"]
    assert learning["operator_corrections"] >= 1
    assert learning["corrected_facts"] >= 1

    curation = service.curation_status()
    assert curation["last_completed_run_id"]
    assert curation["learning"]["operator_corrections"] >= 1
    assert any(r["status"] == "COMPLETED" for r in curation["runs"])
