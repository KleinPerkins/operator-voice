"""M2.3 operator review queue persistence — build plan §13 M2 worklist 3. Exercises encrypted
review-item round-trip, the (subject, facet, scope, reason) de-dup guard, and resolve/dismiss
against a real migrated SQLite DB with in-memory-Keychain crypto. Synthetic data only."""

from __future__ import annotations

from operator_voice.domain.models import (
    ContextScope,
    ReviewItem,
    ReviewReason,
    ReviewStatus,
    SubjectRegistration,
    new_id,
)
from operator_voice.persistence import store


def _subject(conn) -> str:
    subject = SubjectRegistration.new_operator("Test Operator")
    store.insert_subject(conn, subject)
    return subject.subject_id


def _item(subject_id: str, reason: ReviewReason, facet: str = "avg_sentence_length") -> ReviewItem:
    return ReviewItem(
        review_id=new_id("rev"), subject_id=subject_id, reason=reason,
        detail=f"synthetic review detail for {facet}", facet=facet,
        scope=ContextScope(channel="slack"), related_fact_id="fact_x", related_ids=["obs_1", "obs_2"],
    )


def test_review_item_round_trip_encrypts_detail_and_reads_back(migrated_db, in_memory_crypto):
    conn, _ = migrated_db
    subject_id = _subject(conn)
    item = _item(subject_id, ReviewReason.LOW_SOURCE_DIVERSITY)
    store.insert_review_item(conn, in_memory_crypto, item)

    # detail is stored encrypted (never plaintext on disk)
    row = conn.execute("SELECT detail_enc FROM review_items WHERE review_id = ?", (item.review_id,)).fetchone()
    assert row["detail_enc"] is not None
    assert b"synthetic review detail" not in bytes(row["detail_enc"])

    fetched = store.get_review_item(conn, in_memory_crypto, item.review_id)
    assert fetched is not None
    assert fetched.detail == item.detail
    assert fetched.reason == ReviewReason.LOW_SOURCE_DIVERSITY
    assert fetched.scope.key() == ContextScope(channel="slack").key()
    assert fetched.status == ReviewStatus.OPEN


def test_has_open_review_dedup_guard(migrated_db, in_memory_crypto):
    conn, _ = migrated_db
    subject_id = _subject(conn)
    scope_key = ContextScope(channel="slack").key()
    assert store.has_open_review(conn, subject_id, "avg_sentence_length", scope_key, "LOW_SOURCE_DIVERSITY") is False

    store.insert_review_item(conn, in_memory_crypto, _item(subject_id, ReviewReason.LOW_SOURCE_DIVERSITY))
    assert store.has_open_review(conn, subject_id, "avg_sentence_length", scope_key, "LOW_SOURCE_DIVERSITY") is True
    # a different reason for the same fact is NOT deduped
    assert store.has_open_review(conn, subject_id, "avg_sentence_length", scope_key, "DRIFT") is False


def test_resolve_review_item_flips_status_and_clears_open_guard(migrated_db, in_memory_crypto):
    conn, _ = migrated_db
    subject_id = _subject(conn)
    item = _item(subject_id, ReviewReason.DRIFT)
    store.insert_review_item(conn, in_memory_crypto, item)
    assert store.count_open_review_items(conn, subject_id) == 1

    changed = store.resolve_review_item(conn, item.review_id, "RESOLVED", "operator confirmed the drift")
    assert changed is True
    assert store.count_open_review_items(conn, subject_id) == 0
    # resolving an already-resolved item is a no-op (rowcount 0), never an error
    assert store.resolve_review_item(conn, item.review_id, "DISMISSED", "again") is False

    fetched = store.get_review_item(conn, in_memory_crypto, item.review_id)
    assert fetched.status == ReviewStatus.RESOLVED
    assert fetched.resolution == "operator confirmed the drift"


def test_list_review_items_filters_by_subject_and_status(migrated_db, in_memory_crypto):
    conn, _ = migrated_db
    subject_id = _subject(conn)
    store.insert_review_item(conn, in_memory_crypto, _item(subject_id, ReviewReason.DRIFT, facet="a"))
    store.insert_review_item(conn, in_memory_crypto, _item(subject_id, ReviewReason.LOW_CONFIDENCE, facet="b"))
    open_items = store.list_review_items(conn, in_memory_crypto, subject_id=subject_id, status="OPEN")
    assert len(open_items) == 2
    resolved = store.list_review_items(conn, in_memory_crypto, subject_id=subject_id, status="RESOLVED")
    assert resolved == []
