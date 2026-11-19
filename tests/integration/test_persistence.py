"""Persistence-layer integration coverage: crypto round trip (real disposable Keychain + fast
in-memory keystore), migrations, retention sweep, encrypted backup/restore with downgrade
refusal, and full sink-registry erasure. Synthetic fixture data only.
"""

from __future__ import annotations

import time

import pytest

from operator_voice.persistence import audit, backup, erase, migrations, retention
from operator_voice.persistence.crypto import CryptoContext, aead_decrypt, aead_encrypt


def test_envelope_encryption_round_trip_in_memory(in_memory_crypto: CryptoContext):
    dek, wrapped = in_memory_crypto.wrap_new_dek()
    unwrapped = in_memory_crypto.unwrap_dek(wrapped)
    assert unwrapped == dek

    ciphertext = aead_encrypt(dek, b"synthetic operator fact value")
    assert aead_decrypt(dek, ciphertext) == b"synthetic operator fact value"


def test_destroy_local_keys_makes_wrapped_dek_permanently_unwrappable(in_memory_crypto: CryptoContext):
    dek, wrapped = in_memory_crypto.wrap_new_dek()
    in_memory_crypto.destroy_local_keys()
    with pytest.raises(Exception):
        in_memory_crypto.unwrap_dek(wrapped)


@pytest.mark.keychain
def test_real_disposable_keychain_round_trip(disposable_keychain_crypto: CryptoContext):
    dek, wrapped = disposable_keychain_crypto.wrap_new_dek()
    unwrapped = disposable_keychain_crypto.unwrap_dek(wrapped)
    assert unwrapped == dek


def test_migrate_is_idempotent_and_schema_compatible(migrated_db):
    conn, _ = migrated_db
    assert migrations.current_version(conn) == migrations.CURRENT_SCHEMA_VERSION
    # Re-running migrate on an already-migrated DB is a no-op, not an error.
    assert migrations.migrate(conn) == migrations.CURRENT_SCHEMA_VERSION
    ok, detail = migrations.check_compatible(conn)
    assert ok, detail


def test_migrate_refuses_downgrade(migrated_db):
    conn, _ = migrated_db
    with pytest.raises(migrations.SchemaError):
        migrations.migrate(conn, target_version=0)


def test_audit_journal_is_content_free_by_construction(migrated_db):
    conn, _ = migrated_db
    event = audit.record(
        conn, operation_id="op_1", operation_type="erase", actor="operator",
        consumer=None, result="ok", versions={"schema_version": "operator-voice-schema/v1"},
        deletion_counts={"facts": 3},
    )
    # The function signature has no free-text field to leak content into — this assertion
    # documents that invariant rather than merely inspecting output.
    assert "detail" not in audit.record.__code__.co_varnames
    rows = audit.read(conn, limit=10)
    assert rows[0].event_id == event.event_id
    assert rows[0].deletion_counts == {"facts": 3}


def test_retention_sweep_clears_expired_document_content(migrated_db):
    conn, _ = migrated_db
    now = time.time()
    conn.execute(
        "INSERT INTO subjects(subject_id, kind, display_label, approved_purposes_json, "
        "consent_basis, generation_permission, retention_policy, active, created_at) "
        "VALUES ('subj_1','OPERATOR','test','[]','operator-attested',1,'default',1,?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO sources(source_id, subject_id, adapter_type, canonical_root, opted_in_at, "
        "approved_purpose, active, retention_policy, limits_json, role_identity_json) "
        "VALUES ('src_1','subj_1','manual','/tmp/synthetic',?,'voice_generation',1,'default','{}','{}')",
        (now,),
    )
    conn.execute(
        "INSERT INTO documents(document_id, subject_id, source_id, source_native_id, digest, "
        "locator, content_enc, key_generation_id, created_at, context_json, sensitivity, "
        "authorship_state, expires_at) "
        "VALUES ('doc_1','subj_1','src_1','n1','d1','l1',X'00112233','keygen_1',?, '{}', "
        "'standard','SUBJECT',?)",
        (now, now - 1),
    )
    counts = retention.sweep_expired(conn)
    assert counts["documents_content_cleared"] == 1
    row = conn.execute("SELECT content_enc FROM documents WHERE document_id='doc_1'").fetchone()
    assert row["content_enc"] is None


def test_backup_verify_restore_round_trip(migrated_db, tmp_path):
    conn, db_path = migrated_db
    conn.execute(
        "INSERT INTO subjects(subject_id, kind, display_label, approved_purposes_json, "
        "consent_basis, generation_permission, retention_policy, active, created_at) "
        "VALUES ('subj_1','OPERATOR','test','[]','operator-attested',1,'default',1,?)",
        (time.time(),),
    )
    conn.commit() if hasattr(conn, "commit") else None

    backups_dir = tmp_path / "backups"
    snapshot = backup.create_backup(db_path, backups_dir)
    ok, detail = backup.verify_backup(snapshot)
    assert ok, detail

    restored_path = tmp_path / "restored.sqlite3"
    backup.restore_backup(snapshot, restored_path)
    from operator_voice.persistence.sqlite import connect

    restored_conn = connect(restored_path)
    row = restored_conn.execute("SELECT subject_id FROM subjects").fetchone()
    assert row["subject_id"] == "subj_1"
    restored_conn.close()


def test_backup_restore_refuses_schema_downgrade(migrated_db, tmp_path):
    conn, db_path = migrated_db
    backups_dir = tmp_path / "backups"
    snapshot = backup.create_backup(db_path, backups_dir)

    # Simulate a live DB that has since been upgraded past the backup's schema version.
    conn.execute(
        "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
        (str(migrations.CURRENT_SCHEMA_VERSION + 1),),
    )
    with pytest.raises(RuntimeError, match="downgrade"):
        backup.restore_backup(snapshot, db_path)


def test_erase_full_system_clears_content_but_keeps_audit(migrated_db, in_memory_crypto):
    conn, _ = migrated_db
    now = time.time()
    conn.execute(
        "INSERT INTO subjects(subject_id, kind, display_label, approved_purposes_json, "
        "consent_basis, generation_permission, retention_policy, active, created_at) "
        "VALUES ('subj_1','OPERATOR','test','[]','operator-attested',1,'default',1,?)",
        (now,),
    )
    audit.record(conn, operation_id="op_1", operation_type="ingest", actor="operator",
                 consumer=None, result="ok")

    report = erase.erase_full_system(conn, in_memory_crypto)
    assert conn.execute("SELECT COUNT(*) AS c FROM subjects").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()["c"] == 1
    assert "local_database" in report.sinks_cleared
    assert "keychain_kek" in report.sinks_cleared
    assert "local_wal" in report.sinks_cleared
    assert report.historical_ciphertext_note is not None

    # P2-1: checkpoint_and_vacuum must run cleanly with no open transaction (its own hard
    # precondition) and must not raise even though the DB was just fully emptied.
    erase.checkpoint_and_vacuum(conn)


def test_erase_full_system_deletes_export_destination_files(migrated_db, in_memory_crypto, tmp_path):
    """Security review P1-2: `export` writes human-readable fact JSON OUTSIDE the database;
    `full_system` erase must unlink every such file, not just its `export_records` row —
    otherwise "irreversibly erase everything" silently leaves plaintext exports on disk."""
    conn, _ = migrated_db
    now = time.time()
    dest = tmp_path / "sensitive-export.json"
    dest.write_text('{"facts": ["synthetic export content"]}')
    assert dest.exists()
    conn.execute(
        "INSERT INTO export_records(export_id, subject_id, scope, destination, created_at) "
        "VALUES ('exp_1', 'subj_1', 'facts', ?, ?)",
        (str(dest), now),
    )

    report = erase.erase_full_system(conn, in_memory_crypto)

    assert not dest.exists(), "full_system erase left a plaintext export file on disk"
    assert report.deletion_counts["export_files"] == 1
    assert "export_destination_file" in report.sinks_cleared
    assert conn.execute("SELECT COUNT(*) AS c FROM export_records").fetchone()["c"] == 0


def test_erase_full_system_removes_profile_pack_clone_dir(migrated_db, in_memory_crypto, tmp_path):
    """Security review P2-1: an ENCRYPTED_GIT local clone directory is disclosed as a sink and
    must actually be removed by `full_system`, not silently left behind."""
    conn, _ = migrated_db
    clone_dir = tmp_path / "profile-pack-clone"
    clone_dir.mkdir()
    (clone_dir / "some-object").write_bytes(b"opaque ciphertext bytes")

    report = erase.erase_full_system(conn, in_memory_crypto, profile_pack_clone_dir=clone_dir)

    assert not clone_dir.exists()
    assert "profile_pack_clone" in report.sinks_cleared


def test_erase_fact_and_source_scopes_are_targeted(migrated_db):
    conn, _ = migrated_db
    now = time.time()
    conn.execute(
        "INSERT INTO subjects(subject_id, kind, display_label, approved_purposes_json, "
        "consent_basis, generation_permission, retention_policy, active, created_at) "
        "VALUES ('subj_1','OPERATOR','test','[]','operator-attested',1,'default',1,?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO facts(fact_id, subject_id, facet, value_enc, key_generation_id, scope_json, "
        "scope_key, state, confidence, document_count, source_count, provenance_ids_json, "
        "synthesis_version, valid_from) "
        "VALUES ('fact_1','subj_1','tone',X'00','keygen_1','{}','general|general|general|en|text',"
        "'SUPPORTED',0.8,1,1,'[]','v1',?)",
        (now,),
    )
    report = erase.erase_fact(conn, "fact_1")
    assert report.deletion_counts["facts"] == 1
    assert conn.execute("SELECT COUNT(*) AS c FROM facts").fetchone()["c"] == 0
