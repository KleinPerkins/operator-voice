"""Synthetic clock, concurrency and recovery checks for the daily controller."""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import pytest

from operator_voice.runtime.daily_cycle import DailyCycle, CycleError


def ts(value):
    return datetime.fromisoformat(value).timestamp()


@pytest.fixture
def cycle(tmp_path, in_memory_crypto):
    return DailyCycle(tmp_path, in_memory_crypto)


def configured(cycle):
    cycle.configure(timezone="America/Los_Angeles", hour=18,
                    identities={"slack": "SYNTHETIC", "gmail": "writer@example.test", "drive": "writer@example.test"},
                    now=ts("2026-11-01T12:00:00-08:00"))


def complete(cycle, run, now):
    for source in run["integrations"]:
        cycle.record(run["run_id"], source, "success", now=now, counts={"fetched": 0})
    cycle.finish(run["run_id"], now=now, curation={"sources_failed": 0, "pending_continuation": False,
                 "publish_status": "skipped_no_changes", "profile_changed": False})


def test_daily_slot_dst_catchup_and_no_second_fetch(cycle):
    configured(cycle)
    before = ts("2026-11-01T17:59:00-08:00")
    # First activation waits until the next configured slot.
    assert cycle.status(now=before)["due"] is False
    at = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=at)
    assert run["window_end"] == at
    complete(cycle, run, at + 60)
    assert cycle.begin(now=at + 900)["status"] == "not_due"
    catchup = cycle.begin(now=ts("2026-11-04T09:00:00-08:00"))
    assert catchup["status"] == "started"
    assert catchup["integrations"] == ["slack", "gmail", "drive"]


def test_two_controllers_cannot_own_one_cycle(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: cycle.begin(now=now)["status"], range(2)))
    assert sorted(outcomes) == ["busy", "started"]


def test_retry_once_at_fifteen_minutes_with_source_isolation(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=now)
    cycle.record(run["run_id"], "slack", "transient_error", now=now)
    for source in ("gmail", "drive"):
        cycle.record(run["run_id"], source, "success", now=now)
    cycle.finish(run["run_id"], now=now, curation={})
    assert cycle.begin(now=now + 899)["status"] == "not_due"
    retry = cycle.begin(now=now + 900)
    assert retry["integrations"] == ["slack"]
    cycle.record(retry["run_id"], "slack", "transient_error", now=now + 900)
    cycle.finish(retry["run_id"], now=now + 900, curation={})
    assert cycle.begin(now=now + 1800)["status"] == "not_due"
    assert cycle.status(now=now + 1800)["sources"]["gmail"]["last_success_at"] == now


def test_crash_recovery_does_not_claim_fetch_success(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=now)
    cycle.record(run["run_id"], "gmail", "success", now=now)
    resumed = cycle.begin(now=now + 7201)
    assert resumed["status"] == "started"
    assert "gmail" not in resumed["integrations"]
    with pytest.raises(CycleError):
        cycle.record(run["run_id"], "slack", "success", now=now + 7201)


def test_identity_pinning_and_private_state(cycle, tmp_path):
    configured(cycle)
    cycle.verify_identity("gmail", "WRITER@example.test")
    with pytest.raises(CycleError, match="identity"):
        cycle.verify_identity("gmail", "different@example.test")
    assert all(b"writer@example.test" not in p.read_bytes() for p in tmp_path.iterdir())


def test_auth_and_48_hour_alerts_are_episode_deduplicated(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=now)
    cycle.record(run["run_id"], "gmail", "auth_error", now=now)
    cycle.record(run["run_id"], "slack", "transient_error", now=now)
    cycle.record(run["run_id"], "drive", "success", now=now)
    cycle.finish(run["run_id"], now=now, curation={})
    assert cycle.status(now=now)["attention"] == ["gmail:auth_error"]
    cycle.acknowledge(["gmail:auth_error"], now=now)
    assert cycle.status(now=now)["attention"] == []
    assert "slack:stale" in cycle.status(now=now + 48 * 3600)["attention"]


def test_incomplete_pages_never_advance_success(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=now)
    cycle.record(run["run_id"], "gmail", "incomplete", now=now, checkpoint={"page_token": "synthetic-token"})
    assert cycle.status(now=now)["sources"]["gmail"]["last_success_at"] is None


def test_invalid_free_text_outcomes_are_refused(cycle):
    configured(cycle)
    run = cycle.begin(force=True)
    with pytest.raises(CycleError):
        cycle.record(run["run_id"], "gmail", "raw operator writing")


def test_unenrolled_identity_is_not_verified(cycle):
    assert cycle.verify_identity("gmail", "writer@example.test") is False
    configured(cycle)
    assert cycle.verify_identity("gmail", "writer@example.test") is True


def test_backlog_is_not_misreported_as_publication_failure(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=now)
    for source in run["integrations"]:
        cycle.record(run["run_id"], source, "success", now=now)
    outcome = cycle.finish(run["run_id"], now=now, curation={
        "pending_continuation": True, "publish_status": "skipped_no_changes"})
    assert outcome["last_cycle"]["curation_pending"] is True
    assert outcome["last_cycle"]["publication_pending"] is False
    assert outcome["last_cycle"]["unchanged_cycles"] == 0
    assert "curation:unfinished" in cycle.status(now=now + 48 * 3600)["attention"]
    assert "publication:unfinished" not in cycle.status(now=now + 48 * 3600)["attention"]


def test_existing_replica_conflict_is_immediate_and_acknowledgeable(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    assert "publication:conflict" in cycle.status(now=now, conflict=True)["attention"]
    cycle.acknowledge(["publication:conflict"], now=now, conflict=True)
    assert "publication:conflict" not in cycle.status(now=now, conflict=True)["attention"]


def test_pending_publication_retries_without_refetching_and_is_bounded(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=now)
    for source in run["integrations"]:
        cycle.record(run["run_id"], source, "success", now=now)
    cycle.finish(run["run_id"], now=now, curation={"publish_status": "PENDING_PUBLICATION"})
    retry = cycle.begin(now=now + 900)
    assert retry["integrations"] == []
    cycle.finish(retry["run_id"], now=now + 900, curation={"publish_status": "PENDING_PUBLICATION"})
    assert cycle.begin(now=now + 1800)["status"] == "not_due"
    assert cycle.status(now=now + 1800)["last_cycle"]["publication_pending"]


def test_corrupt_control_state_fails_closed(cycle):
    configured(cycle)
    cycle.path.write_bytes(b"synthetic corrupt ciphertext")
    with pytest.raises(CycleError, match="cannot be verified"):
        cycle.begin(force=True)


def test_midnight_catchup_finishes_old_window_before_advancing(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=now)
    cycle.record(run["run_id"], "gmail", "incomplete", now=now,
                 checkpoint={"page_token": "synthetic-old-page"})
    for source in ("slack", "drive"):
        cycle.record(run["run_id"], source, "success", now=now)
    cycle.finish(run["run_id"], now=now, curation={})
    later = now + 86400
    continuation = cycle.begin(now=later)
    assert continuation["integrations"] == ["gmail"]
    assert continuation["window_end"] == now
    assert continuation["resume"]["gmail"]["checkpoint"]["page_token"] == "synthetic-old-page"
    complete(cycle, continuation, later + 60)
    next_day = cycle.begin(now=later + 900)
    assert next_day["integrations"] == ["slack", "gmail", "drive"]
    assert next_day["window_end"] == later + 900


def test_continuation_does_not_hide_earlier_profile_change(cycle):
    configured(cycle)
    now = ts("2026-11-01T18:00:00-08:00")
    run = cycle.begin(now=now)
    for source in run["integrations"]:
        cycle.record(run["run_id"], source, "success", now=now)
    cycle.finish(run["run_id"], now=now, curation={"pending_continuation": True,
                 "generation_ids": ["synthetic-generation"], "documents_new": 2})
    retry = cycle.begin(now=now + 900)
    result = cycle.finish(retry["run_id"], now=now + 900, curation={})
    assert result["last_cycle"]["profile_changed"] is True
    assert result["last_cycle"]["documents_new"] == 2
    assert result["last_cycle"]["unchanged_cycles"] == 0
