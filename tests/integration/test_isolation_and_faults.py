"""Multi-operator distribution/isolation and failure-injection coverage — build plan §13 M1
worklist item 9: "failure injection, multi-operator distribution/isolation." Two angles:

1. Distribution isolation: two independent "operators" (each its own disposable DB + its own
   in-memory keystore, standing in for two separate machines/installs — the real-world unit of
   isolation this toolkit cares about; OVT is a single-user-per-install local tool, never a
   shared multi-tenant DB) never share state, and one's ciphertext is meaningless to the other's
   key.
2. Failure injection: an exception raised mid-operation leaves zero partial mutation (the staged
   transaction rolls back completely) and is recorded as a content-free audit error, matching
   build plan §8/§11's staged-operation guarantee.
"""

from __future__ import annotations

from synthetic_sources import write_slack_notes

from pathlib import Path

import pytest

from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.persistence import migrations, store
from operator_voice.persistence.crypto import CryptoContext, InMemoryKeyStore, aead_decrypt, aead_encrypt
from operator_voice.persistence.sqlite import connect, transaction


def _new_operator(tmp_path: Path, name: str) -> tuple[OperatorVoiceService, CryptoContext]:
    db_path = tmp_path / f"{name}.sqlite3"
    conn = connect(db_path)
    migrations.migrate(conn)
    crypto = CryptoContext(InMemoryKeyStore())
    service = OperatorVoiceService(conn, crypto, profile_pack_crypto=crypto)
    service.setup(display_label=name)
    return service, crypto


def _write_notes(tmp_path: Path, name: str, texts: list[str]) -> Path:
    root = tmp_path / f"{name}-drop"
    root.mkdir()
    write_slack_notes(root, texts, conversations=len(texts))
    return root


def test_two_operators_never_share_subjects_or_facts(tmp_path):
    op_a, _crypto_a = _new_operator(tmp_path, "operator-a")
    op_b, _crypto_b = _new_operator(tmp_path, "operator-b")

    subjects_a = op_a.subject_list()["subjects"]
    subjects_b = op_b.subject_list()["subjects"]
    assert len(subjects_a) == 1 and len(subjects_b) == 1
    assert subjects_a[0]["subject_id"] != subjects_b[0]["subject_id"]
    assert subjects_a[0]["display_label"] == "operator-a"
    assert subjects_b[0]["display_label"] == "operator-b"

    root_a = _write_notes(tmp_path, "operator-a", [
        "I always ship on Fridays and never regret it.",
        "Shipping on Fridays works for me every single time.",
        "I ship on Fridays; it has never once caused a problem.",
    ])
    source_a = op_a.source_add(subject_id=subjects_a[0]["subject_id"], adapter_type="slack_export", canonical_root=str(root_a))
    op_a.ingest(source_id=source_a["source_id"])

    # Operator B's subject list, facts, and DB file are completely untouched by A's ingest.
    assert op_b.subject_list()["subjects"] == subjects_b
    facts_b = store.list_facts(op_b.conn, op_b.crypto, subjects_b[0]["subject_id"])
    assert facts_b == []


def test_operator_b_ciphertext_is_meaningless_under_operator_a_key(tmp_path):
    _op_a, crypto_a = _new_operator(tmp_path, "operator-a")
    _op_b, crypto_b = _new_operator(tmp_path, "operator-b")

    dek_a, _wrapped_a = crypto_a.wrap_new_dek()
    dek_b, _wrapped_b = crypto_b.wrap_new_dek()
    assert dek_a != dek_b  # independent DEKs per operator, never derived/shared

    plaintext_b = b"operator-b's secret voice profile content"
    sealed = aead_encrypt(dek_b, plaintext_b, aad=b"test")

    # Decrypting operator B's ciphertext with operator A's independent DEK must fail loudly, never
    # silently return wrong/garbage plaintext that looks valid.
    with pytest.raises(Exception):
        aead_decrypt(dek_a, sealed, aad=b"test")

    # Sanity: B's own key still round-trips correctly (proves the failure above is real isolation,
    # not a broken encrypt/decrypt implementation).
    assert aead_decrypt(dek_b, sealed, aad=b"test") == plaintext_b


def test_ingest_failure_mid_operation_leaves_zero_partial_state(tmp_path, monkeypatch):
    service, _crypto = _new_operator(tmp_path, "operator-fault")
    subject_id = service.subject_list()["subjects"][0]["subject_id"]
    root = _write_notes(tmp_path, "operator-fault", [
        "This note should never be visible if ingest fails partway through.",
        "Neither should this second note.",
    ])
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))

    import operator_voice.persistence.store as store_module

    real_upsert = store_module.upsert_document
    call_count = {"n": 0}

    def _flaky_upsert(conn, crypto, doc, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated mid-ingest failure (disk full / crypto error / etc.)")
        return real_upsert(conn, crypto, doc, **kwargs)

    monkeypatch.setattr(store_module, "upsert_document", _flaky_upsert)

    with pytest.raises(RuntimeError, match="simulated mid-ingest failure"):
        service.ingest(source_id=source["source_id"])

    # The staged transaction must have rolled back completely: the first document's upsert (which
    # succeeded before the injected failure) must NOT be visible — all-or-nothing, not partial.
    with transaction(service.conn):
        cur = service.conn.execute("SELECT COUNT(*) FROM documents WHERE subject_id = ?", (subject_id,))
        assert cur.fetchone()[0] == 0

    # A content-free audit error event was recorded for the attempt (staged_operation's contract) —
    # queried via the raw table since there is no public list_audit_events() accessor yet.
    cur = service.conn.execute(
        "SELECT result FROM audit_events WHERE operation_type = 'ingest' ORDER BY created_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    assert row is not None and row[0].startswith("error:"), row


def test_source_pause_mid_flight_does_not_corrupt_other_sources(tmp_path):
    service, _crypto = _new_operator(tmp_path, "operator-multi-source")
    subject_id = service.subject_list()["subjects"][0]["subject_id"]
    root1 = _write_notes(tmp_path, "src1", ["First source note, always the same content here."])
    root2 = _write_notes(tmp_path, "src2", ["Second source note, completely different content."])
    source1 = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root1))
    source2 = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root2))

    service.ingest(source_id=source1["source_id"])
    service.source_pause(source_id=source1["source_id"])

    # Pausing source1 must not block ingesting source2 (independent sources, independent state).
    result2 = service.ingest(source_id=source2["source_id"])
    assert result2["status"] == "ingested"

    with pytest.raises(ServiceError):
        service.ingest(source_id=source1["source_id"])  # paused source refuses to ingest


def test_named_third_party_analysis_without_external_enrollment_refuses(tmp_path):
    """Build plan §13 M1 acceptance evidence: "named-third-party analysis without EXTERNAL
    enrollment refuses." subject.insights requires a real, previously-enrolled subject_id; a
    named third party who was never enrolled via subject.add(kind="EXTERNAL") has no subject_id
    to present, so any attempt to analyze them refuses outright — never silently proceeds against
    a guessed/free-text name."""
    service, _crypto = _new_operator(tmp_path, "operator-external-refusal")

    # No subject_id at all for this unenrolled third party -> refuses (unknown subject_id).
    with pytest.raises(ServiceError, match="unknown subject_id"):
        service.subject_insights(subject_id="subj_never_enrolled", purpose="general")

    # The OPERATOR subject itself is never a valid target for third-party insights either,
    # even though it IS a real, enrolled subject_id.
    operator_subject_id = service.subject_list()["subjects"][0]["subject_id"]
    with pytest.raises(ServiceError, match="refuses OPERATOR subjects"):
        service.subject_insights(subject_id=operator_subject_id, purpose="general")

    # Only after explicit EXTERNAL enrollment does subject.insights proceed (structurally, per
    # §4.3 — M1 ships no EXTERNAL product surface, so the bundle is correctly empty with a
    # warning, but the call itself is no longer refused).
    external = service.subject_add(kind="EXTERNAL", display_label="Third Party", purposes=["analysis"])
    bundle = service.subject_insights(subject_id=external["subject_id"], purpose="general")
    assert bundle["subject_id"] == external["subject_id"]
