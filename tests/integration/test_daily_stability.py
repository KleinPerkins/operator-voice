"""Daily learning regressions; all writing and identities are synthetic."""
import pytest

from operator_voice.application.service import OperatorVoiceService
from operator_voice.persistence import store
from operator_voice.runtime.paths import RuntimePaths


@pytest.fixture
def service(migrated_db, in_memory_crypto, tmp_path):
    paths = RuntimePaths(tmp_path / "config", tmp_path / "data", tmp_path / "state")
    paths.ensure()
    svc = OperatorVoiceService(migrated_db[0], in_memory_crypto,
                               profile_pack_crypto=in_memory_crypto, paths=paths)
    svc.setup()
    svc.drive_folder_add(folder_id="synthetic-writing")
    return svc


def doc(service, *, revision=1, file_id="synthetic-doc", text=None):
    service.drive_import(owner_id="writer@example.test", files=[{
        "file_id": file_id, "revision_id": str(revision),
        "modified_time": f"2026-10-{revision:02d}T18:00:00Z",
        "folder_id": "synthetic-writing", "mime_type": "application/vnd.google-apps.document",
        "owner_id": "writer@example.test", "owners": ["writer@example.test"], "drive_id": None,
        "text": text if text is not None else "Please review the synthetic release plan. We should ship the tested feature tomorrow.",
    }])


def test_identical_newer_revision_does_not_version_or_publish(service):
    doc(service)
    assert service.curate()["generation_ids"]
    doc(service, revision=2)
    result = service.curate()
    assert result["documents_new"] == 1
    assert result["generation_ids"] == []
    assert result["publish_status"] == "skipped_no_changes"


def test_later_identical_document_does_not_version_or_publish(service):
    doc(service)
    service.curate()
    doc(service, revision=2, file_id="synthetic-copy")
    result = service.curate()
    assert result["generation_ids"] == []
    assert result["publish_status"] == "skipped_no_changes"


def test_revision_removing_prose_versions_retirement(service):
    doc(service)
    first = service.curate()
    doc(service, revision=2, text="```python\nprint('synthetic non-prose only')\n```\n")
    result = service.curate()
    assert result["observations_new"] == 0
    assert result["generation_ids"] and result["generation_ids"] != first["generation_ids"]
    subject = store.list_subjects(service.conn)[0]
    assert not store.list_facts(service.conn, service.crypto, subject.subject_id)


@pytest.mark.parametrize("integration,identity,payload", [
    ("slack", "author_id", "messages"), ("gmail", "author_email", "messages"),
    ("drive", "owner_id", "files"),
])
def test_completed_empty_fetch_is_recordable(service, integration, identity, payload):
    result = getattr(service, integration + "_import")(**{identity: "writer@example.test", payload: []})
    assert result["imported"] == 0
    assert getattr(service, integration + "_status")()["last_import_at"] is not None


def test_interrupted_materialization_cannot_leave_a_partial_record(service, monkeypatch):
    from operator_voice.persistence import atomic
    real_replace = atomic.os.replace
    def fail_record(source, target):
        if target.suffix == ".md":
            raise OSError("synthetic replace failure")
        return real_replace(source, target)
    monkeypatch.setattr(atomic.os, "replace", fail_record)
    with pytest.raises(OSError, match="synthetic"):
        doc(service)
    assert list(service.paths.drive_export_dir.glob("*.md")) == []
    assert service.drive_status()["last_import_at"] is None
    monkeypatch.setattr(atomic.os, "replace", real_replace)
    doc(service)
    assert service.curate()["documents_new"] == 1


def test_empty_revision_retires_previously_learned_document(service):
    doc(service)
    assert service.curate()["generation_ids"]
    doc(service, revision=2, text="")
    result = service.curate()
    assert result["generation_ids"]
    subject = store.list_subjects(service.conn)[0]
    assert store.list_facts(service.conn, service.crypto, subject.subject_id) == []
    status = service.drive_status()
    assert status["eligible_count"] == status["ingested_count"] == 0


def test_cycle_preserves_off_and_rejects_wrong_account_before_materializing(service):
    from operator_voice.application.service import ServiceError
    service.cycle_configure(identities={"slack": "SYNTHETIC", "gmail": "writer@example.test", "drive": "writer@example.test"})
    with pytest.raises(ServiceError, match="identity"):
        service.gmail_import(author_email="different@example.test", messages=[])
    assert service.gmail_status()["last_import_at"] is None
    service.off(reason="synthetic test")
    assert service.cycle_begin(force=True)["status"] == "off"
    assert not service.cycle_status()["due"]


def test_daily_finish_is_single_use_and_curates_durable_imports(service):
    from operator_voice.application.service import ServiceError
    service.cycle_configure(identities={"slack": "SYNTHETIC", "gmail": "writer@example.test", "drive": "writer@example.test"})
    run = service.cycle_begin(force=True)
    doc(service)
    for integration in run["integrations"]:
        service.cycle_record(run_id=run["run_id"], integration=integration, outcome="success")
    outcome = service.cycle_finish(run_id=run["run_id"])
    assert outcome["last_cycle"]["documents_new"] == 1
    assert outcome["last_cycle"]["profile_changed"]
    with pytest.raises(ServiceError, match="lease"):
        service.cycle_finish(run_id=run["run_id"])


def test_slack_newer_edit_replaces_signals_and_cannot_regress(service):
    def message(text, edited=None):
        value = {"channel": "synthetic", "message_ts": "1700000000.000100",
                 "author_id": "SYNTHETIC", "text": text}
        if edited is not None:
            value["edited_ts"] = edited
        service.slack_import(author_id="SYNTHETIC", messages=[value])
    message("Please review the synthetic release plan. We should publish the tested feature tomorrow.")
    assert service.curate()["generation_ids"]
    message("OK", "1700000100.000100")
    changed = service.curate()
    assert changed["generation_ids"] and changed["observations_new"] == 0
    subject = store.list_subjects(service.conn)[0]
    assert not store.list_facts(service.conn, service.crypto, subject.subject_id)
    status = service.slack_status()
    assert status["eligible_count"] == status["ingested_count"] == 0
    message("An older synthetic long version must never restore the superseded observations.", "1700000050.000100")
    assert service.curate()["generation_ids"] == []


def test_late_arriving_google_record_is_not_lost_behind_lexical_cursor(service):
    doc(service, revision=3)
    assert service.curate()["documents_new"] == 1
    doc(service, revision=1, file_id="late-doc", text="This different synthetic memo arrived late. Please read the longer explanation carefully.")
    assert service.curate()["documents_new"] == 1
    assert service.curate()["documents_new"] == 0


def test_curation_lock_refuses_overlapping_writer_before_any_learning(service):
    from operator_voice.application.service import OperationRefused
    from operator_voice.persistence.atomic import exclusive_file
    doc(service)
    with exclusive_file(service.paths.state_dir / ".curation.lock"):
        with pytest.raises(OperationRefused, match="already running"):
            service.curate()
    assert service.curate()["documents_new"] == 1


def test_import_state_failure_replays_durable_record_without_inflation(service, monkeypatch):
    from operator_voice.drive import materialize
    write_state = materialize.write_import_state
    def fail_state(*args, **kwargs):
        raise OSError("synthetic checkpoint failure")
    monkeypatch.setattr(materialize, "write_import_state", fail_state)
    with pytest.raises(OSError, match="checkpoint"):
        doc(service)
    assert service.drive_status()["last_import_at"] is None
    monkeypatch.setattr(materialize, "write_import_state", write_state)
    doc(service)
    assert service.curate()["documents_new"] == 1
    assert service.curate()["generation_ids"] == []


def test_enrolled_gmail_refuses_missing_sent_labels(service):
    from operator_voice.application.service import ServiceError
    service.cycle_configure(identities={"slack": "SYNTHETIC", "gmail": "writer@example.test", "drive": "writer@example.test"})
    with pytest.raises(ServiceError, match="label"):
        service.gmail_import(author_email="writer@example.test", messages=[{
            "message_id": "synthetic-message", "from": "writer@example.test",
            "internal_date": "2026-10-30T18:00:00Z", "text": "Please read this synthetic memo before tomorrow."}])
    assert not list(service.paths.gmail_export_dir.glob("*.md"))


def test_profile_digest_is_independent_of_fact_row_order(service, monkeypatch):
    from dataclasses import replace
    doc(service)
    service.curate()
    subject = store.list_subjects(service.conn)[0]
    sample = store.list_facts(service.conn, service.crypto, subject.subject_id)[0]
    facts = [replace(sample, value={"phrase": phrase}) for phrase in ("synthetic alpha", "synthetic beta")]
    monkeypatch.setattr(store, "list_facts", lambda *args: facts)
    first = service._profile_digest(service.conn, subject.subject_id)
    facts.reverse()
    assert service._profile_digest(service.conn, subject.subject_id) == first
