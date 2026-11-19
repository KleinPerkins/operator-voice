"""Synthetic backup, replacement and curation-erasure safety boundaries."""
import re
from pathlib import Path

import pytest

from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.persistence import backup, store
from synthetic_sources import write_slack_notes


def _literal(result, field="instructions"):
    return re.search(r"(?:challenge_literal|erased_evidence_literal)='([^']+)'", result[field]).group(1)


@pytest.fixture
def svc(migrated_db, in_memory_crypto, tmp_path, monkeypatch):
    service = OperatorVoiceService(migrated_db[0], in_memory_crypto, profile_pack_crypto=in_memory_crypto)
    # Never let unrelated real OVT processes influence synthetic restore tests.
    monkeypatch.setattr(service, "_other_ovt_process_running", lambda: False, raising=False)
    sid = service.setup()["subject_id"]
    root = write_slack_notes(tmp_path / "notes", [
        "The first synthetic note has enough words. We will ship it soon.",
        "The second synthetic note is also ready. We agree with this approach.",
    ], conversations=2)
    source = service.source_add(subject_id=sid, adapter_type="slack_export", canonical_root=str(root))
    service.ingest(source_id=source["source_id"])
    return service, sid, source["source_id"], root, migrated_db[1]


def _apply(s, sid):
    return s.reextract(subject_id=sid, challenge_literal=_literal(s.reextract(subject_id=sid)))


def test_reextract_replaces_digest_classification_and_is_idempotent(svc):
    s, sid, source, root, _ = svc
    corrected = s.fact_correct(subject_id=sid, facet="tone", new_value="plain", reason="synthetic correction")
    path = sorted(root.glob("*.md"))[0]
    path.write_text(path.read_text().replace("The first synthetic note", "The changed synthetic note"))
    preview = s.reextract(subject_id=sid, dry_run=True)
    assert preview["sources"][0]["reextracted"] >= 1
    _apply(s, sid)
    after = s.reextract(subject_id=sid, dry_run=True)["sources"][0]
    assert after["kept"] == 2 and after["reextracted"] == 0
    facts = store.list_facts(s.conn, s.crypto, sid)
    assert any(f.fact_id == corrected["fact_id"] and f.value == "plain" and f.state.value == "CORRECTED" for f in facts)


def test_backup_failure_precedes_any_evidence_changes(svc, monkeypatch):
    s, sid, _, root, _ = svc
    sorted(root.glob("*.md"))[0].unlink()
    before = list(s.conn.execute("SELECT document_id,digest FROM documents"))
    def fail(*args):
        raise RuntimeError("synthetic backup failure")
    monkeypatch.setattr(backup, "create_backup", fail)
    challenge = s.reextract(subject_id=sid)
    with pytest.raises(RuntimeError, match="backup failure"):
        s.reextract(subject_id=sid, challenge_literal=_literal(challenge))
    assert list(s.conn.execute("SELECT document_id,digest FROM documents")) == before


def test_reextract_paused_and_inactive_sources_are_untouched(svc):
    s, sid, source, root, _ = svc
    store.set_source_paused(s.conn, source, True)
    for path in root.iterdir():
        path.unlink()
    preview = s.reextract(subject_id=sid, dry_run=True)["sources"][0]
    assert preview["paused"] and preview["documents"] == 2 and preview["observations"] > 0
    _apply(s, sid)
    assert len(store.list_document_ids_for_source(s.conn, source)) == 2
    s.conn.execute("UPDATE sources SET paused=0, active=0 WHERE source_id=?", (source,))
    assert s.reextract(subject_id=sid, dry_run=True)["sources"][0]["active"] is False


def test_backup_restore_newest_path_restriction_and_erased_evidence_challenge(svc):
    s, sid, source, _, db = svc
    done = _apply(s, sid)
    snapshot = Path(done["backup_path"])
    assert s.backup_restore(dry_run=True)["backup_path"] == str(snapshot)
    with pytest.raises(ServiceError):
        s.backup_restore(backup_path=str(db), dry_run=True)
    challenge = s.erase(scope="source", target=source)
    erased = s.erase(scope="source", target=source, challenge_literal=_literal(challenge))
    assert "local_backups" in erased["sinks_disclosed_unverifiable"]
    assert "local_backups" not in erased["sinks_cleared"]
    assert s.backup_restore(dry_run=True)["erased_evidence_literal_required"]
    normal = s.backup_restore()
    assert normal["status"] == "confirmation_required"
    second = s.backup_restore(challenge_literal=_literal(normal))
    assert second["status"] == "confirmation_required"
    assert "erased evidence" in second["instructions"]
    assert not store.list_document_ids_for_source(s.conn, source)
    restored = s.backup_restore(challenge_literal=_literal(normal), erased_evidence_literal=_literal(second))
    assert restored["status"] == "restored"
    assert len(store.list_document_ids_for_source(s.conn, source)) == 2


def test_backup_restore_refuses_process_or_database_lock(svc, monkeypatch):
    import sqlite3
    s, sid, _, _, db = svc
    _apply(s, sid)
    monkeypatch.setattr(s, "_other_ovt_process_running", lambda: True)
    with pytest.raises(ServiceError, match="process"):
        s.backup_restore(dry_run=True)
    monkeypatch.setattr(s, "_other_ovt_process_running", lambda: False)
    other = sqlite3.connect(db)
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ServiceError, match="locked"):
            s.backup_restore(dry_run=True)
    finally:
        other.rollback()
        other.close()


@pytest.mark.parametrize("command", [
    "/synthetic/bin/operator-voice status",
    "/synthetic/bin/example-tool-operator-voice status",
    "/synthetic/python -m operator_voice.interfaces.cli status",
])
def test_restore_detects_other_runtime_and_distributor_processes(monkeypatch, command):
    import os
    import subprocess

    monkeypatch.setattr(os, "getpid", lambda: 100)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, stdout=f"101 {command}\n",
    ))
    assert OperatorVoiceService._other_ovt_process_running()


def test_restore_ignores_own_and_unrelated_processes(monkeypatch):
    import os
    import subprocess

    monkeypatch.setattr(os, "getpid", lambda: 100)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, stdout=(
            "100 /synthetic/bin/example-tool-operator-voice backup-restore\n"
            "101 /synthetic/bin/another-tool status\n"
        ),
    ))
    assert not OperatorVoiceService._other_ovt_process_running()


def test_verified_backups_never_overwrite_a_previous_snapshot(svc, monkeypatch):
    s, sid, _, _, db = svc
    monkeypatch.setattr(backup.time, "strftime", lambda *args: "20260101T000000Z")
    first = backup.create_backup(db, db.parent / "backups")
    original = first.read_bytes()
    s.fact_correct(subject_id=sid, facet="tone", new_value="direct", reason="synthetic")
    second = backup.create_backup(db, db.parent / "backups")
    assert second != first
    assert first.read_bytes() == original


def test_reextract_reclassifies_from_fresh_candidate_and_rejects_ai_evidence(svc, monkeypatch):
    from operator_voice.adapters import registry
    from operator_voice.domain.models import AdapterType, AuthorshipState
    s, sid, source, _, _ = svc
    original = registry._REGISTRY[AdapterType.SLACK_EXPORT]
    def changed_identity(*args, **kwargs):
        result = original(*args, **kwargs)
        for candidate in result.candidates:
            candidate.role = "assistant"
        return result
    monkeypatch.setitem(registry._REGISTRY, AdapterType.SLACK_EXPORT, changed_identity)
    done = _apply(s, sid)
    assert done["sources"][0]["reextracted"] == 2
    assert s.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert all(store.get_document(s.conn, s.crypto, doc).authorship_state != AuthorshipState.SUBJECT
               for doc in store.list_document_ids_for_source(s.conn, source))
    assert store.list_facts(s.conn, s.crypto, sid) == []


def test_reextract_is_idempotent_for_documents_with_no_subject_text(svc, monkeypatch):
    from operator_voice.adapters import registry
    from operator_voice.domain.models import AdapterType
    s, sid, _, _, _ = svc
    original = registry._REGISTRY[AdapterType.SLACK_EXPORT]
    def assistant(*args, **kwargs):
        result = original(*args, **kwargs)
        for candidate in result.candidates:
            candidate.role = 'assistant'
        return result
    monkeypatch.setitem(registry._REGISTRY, AdapterType.SLACK_EXPORT, assistant)
    _apply(s, sid)
    second = _apply(s, sid)
    assert second['sources'][0]['reextracted'] == 0
    assert second['sources'][0]['kept'] == 2
    assert s.conn.execute('SELECT COUNT(*) FROM observations').fetchone()[0] == 0


def test_reextract_closes_legacy_duplicate_facts_and_preserves_older_correction(svc):
    from dataclasses import replace
    from operator_voice.domain.models import FactState
    s, sid, _, _, _ = svc
    original = store.list_facts(s.conn, s.crypto, sid)[0]
    store.upsert_fact(s.conn, s.crypto, replace(original, fact_id='legacy-duplicate', valid_from=original.valid_from + 1))
    _apply(s, sid)
    same = [f for f in store.list_facts(s.conn, s.crypto, sid) if f.facet == original.facet and f.scope == original.scope]
    assert len(same) == 1
    correction = replace(same[0], fact_id='synthetic-correction', state=FactState.CORRECTED, value=777,
                         valid_from=same[0].valid_from + 1)
    store.upsert_fact(s.conn, s.crypto, correction)
    store.upsert_fact(s.conn, s.crypto, replace(same[0], fact_id='later-inferred-duplicate', valid_from=correction.valid_from + 1))
    _apply(s, sid)
    same = [f for f in store.list_facts(s.conn, s.crypto, sid) if f.facet == original.facet and f.scope == original.scope]
    assert len(same) == 1 and same[0].fact_id == correction.fact_id
    assert same[0].state == FactState.CORRECTED and same[0].value == 777
