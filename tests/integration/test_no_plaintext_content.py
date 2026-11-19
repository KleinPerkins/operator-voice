"""Regression coverage for security review findings P1-1 and P1-3: three columns that were left
in cleartext, contradicting the store's own design invariant (``persistence/migrations.py``
module docstring: "every column that could carry raw text ... is encrypted").

- P1-1: ``feedback_events.reason`` held the raw operator utterance / correction rationale.
- P1-3: ``sources.canonical_root`` and ``documents.locator`` held real filesystem paths.

Each test asserts the FULL RAW SQLite ROW — not just the typed domain object the store layer
returns — never contains the real content in the clear, then proves the encrypted round trip
still returns the original value through the normal read path.
"""

from __future__ import annotations

from synthetic_sources import write_slack_notes

from pathlib import Path

import pytest

from operator_voice.application.service import OperatorVoiceService
from operator_voice.persistence import migrations, store
from operator_voice.persistence.crypto import CryptoContext, InMemoryKeyStore
from operator_voice.persistence.sqlite import connect


@pytest.fixture
def service(tmp_path: Path) -> OperatorVoiceService:
    conn = connect(tmp_path / "operator-voice.sqlite3")
    migrations.migrate(conn)
    crypto = CryptoContext(InMemoryKeyStore())
    svc = OperatorVoiceService(conn, crypto, profile_pack_crypto=crypto)
    svc.setup(display_label="operator")
    return svc


def _raw_row(conn, table: str, id_col: str, id_val: str) -> dict:
    row = conn.execute(f"SELECT * FROM {table} WHERE {id_col} = ?", (id_val,)).fetchone()
    assert row is not None
    return {k: row[k] for k in row.keys()}


def test_feedback_add_reason_column_never_contains_the_raw_utterance(service: OperatorVoiceService):
    subject_id = service.subject_list()["subjects"][0]["subject_id"]
    secret_utterance = "never say the word delve, it always sounds fake to me"
    result = service.feedback_add(subject_id=subject_id, utterance=secret_utterance)
    assert result["status"] == "recorded"

    raw = _raw_row(service.conn, "feedback_events", "subject_id", subject_id)
    assert raw["reason"] != secret_utterance
    assert secret_utterance not in (raw["reason"] or "")
    assert raw["reason_enc"] is not None, "reason must be encrypted, not left as an empty placeholder only"

    # The encrypted round trip still returns the real value through the normal read path.
    events = store.list_feedback_events(service.conn, service.crypto, subject_id)
    assert any(e.reason == secret_utterance for e in events)


def test_fact_correct_reason_column_never_contains_the_raw_reason_text(service: OperatorVoiceService):
    subject_id = service.subject_list()["subjects"][0]["subject_id"]
    secret_reason = "corrected because my manager's real name was misattributed here"
    result = service.fact_correct(subject_id=subject_id, facet="tone.formality", new_value="casual", reason=secret_reason)
    assert result["status"] == "corrected"

    raw = _raw_row(service.conn, "feedback_events", "subject_id", subject_id)
    assert raw["reason"] != secret_reason
    assert secret_reason not in (raw["reason"] or "")

    events = store.list_feedback_events(service.conn, service.crypto, subject_id)
    assert any(e.reason == secret_reason for e in events)


def test_source_canonical_root_column_never_contains_the_real_path(service: OperatorVoiceService, tmp_path: Path):
    subject_id = service.subject_list()["subjects"][0]["subject_id"]
    secret_root = tmp_path / "Users" / "realname" / "Documents" / "private-notes"
    secret_root.mkdir(parents=True)
    result = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(secret_root))
    source_id = result["source_id"]

    raw = _raw_row(service.conn, "sources", "source_id", source_id)
    assert raw["canonical_root"] != str(secret_root)
    assert str(secret_root) not in (raw["canonical_root"] or "")
    assert raw["canonical_root_enc"] is not None

    fetched = store.get_source(service.conn, service.crypto, source_id)
    assert fetched.canonical_root == str(secret_root)


def test_document_locator_column_never_contains_the_real_path(service: OperatorVoiceService, tmp_path: Path):
    subject_id = service.subject_list()["subjects"][0]["subject_id"]
    root = tmp_path / "drop"
    root.mkdir()
    write_slack_notes(root, ["I always feel calm after these sessions."])
    secret_note = root / "note-with-a-revealing-filename-about-my-therapist.md"
    (root / "notes-0000.md").rename(secret_note)
    expected_locator = f"slack:{secret_note.stem}:1700000000.000100"
    source = service.source_add(subject_id=subject_id, adapter_type="slack_export", canonical_root=str(root))
    service.ingest(source_id=source["source_id"])

    row = service.conn.execute("SELECT document_id, locator, locator_enc FROM documents WHERE source_id = ?", (source["source_id"],)).fetchone()
    assert row is not None
    assert row["locator"] != expected_locator
    assert expected_locator not in (row["locator"] or "")
    assert row["locator_enc"] is not None

    doc = store.get_document(service.conn, service.crypto, row["document_id"])
    assert doc.locator == expected_locator
