"""M5-A review-swarm fix (Data/Privacy P1): the generated per-operation error taxonomy must reflect
the failure modes the service ACTUALLY produces — never advertise an error the handler cannot raise,
and never mislabel a raw DB crash as a friendly one. These tests probe the real service against a
fresh DB and lock:

  * the operations whose taxonomy advertises ``unknown_subject`` are EXACTLY the ones that raise it;
  * the three mutation handlers that used to raw-crash on an unknown subject (FOREIGN KEY) now raise
    a clean ``ServiceError`` instead;
  * ``fact.deprecate`` / ``subject.pause`` / ``source.pause`` now refuse an unknown id rather than
    running a 0-row UPDATE and reporting a false success;
  * the pure-read handlers that do NOT look their subject up (and therefore must NOT advertise
    ``unknown_subject``) still no-op cleanly on an unknown id.

If a future edit re-broadens the taxonomy or drops a guard, one of these bites.
"""

from __future__ import annotations

from synthetic_sources import write_slack_notes

import re

import pytest

from operator_voice.application.service import OperatorVoiceService, ServiceError
from operator_voice.domain.help import error_taxonomy
from operator_voice.domain.operations import OPERATIONS


@pytest.fixture
def service(migrated_db, in_memory_crypto):
    conn, _db_path = migrated_db
    return OperatorVoiceService(conn, in_memory_crypto, profile_pack_crypto=in_memory_crypto)


# The operations whose card advertises `unknown_subject` — must equal the set that truly raises it.
_ADVERTISES_UNKNOWN_SUBJECT = {
    "context.get", "subject.insights", "subject.alias_add",
    "source.add", "subject.pause", "feedback.add", "fact.correct", "reanalyze", "portability.export",
}


def _codes(op_name: str) -> set[str]:
    op = next(o for o in OPERATIONS if o.name == op_name)
    return {e.code for e in error_taxonomy(op)}


def test_unknown_subject_is_advertised_by_exactly_the_ops_that_raise_it():
    advertised = {op.name for op in OPERATIONS if "unknown_subject" in {e.code for e in error_taxonomy(op)}}
    assert advertised == _ADVERTISES_UNKNOWN_SUBJECT


@pytest.mark.parametrize("op_name", sorted(_ADVERTISES_UNKNOWN_SUBJECT - {"portability.export"}))
def test_advertised_unknown_subject_actually_raises(service, op_name):
    # portability.export reaches its subject check only past the confirmation gate; it is covered by
    # a dedicated two-step probe below.
    bad = "subj_DOES_NOT_EXIST"
    calls = {
        "context.get": lambda: service.context_get(subject_id=bad, consumer_id="demo-cli"),
        "subject.insights": lambda: service.subject_insights(subject_id=bad, purpose="communication_pattern_analysis"),
        "subject.alias_add": lambda: service.subject_alias_add(subject_id=bad, source_type="slack_export", source_native_id="x"),
        "source.add": lambda: service.source_add(subject_id=bad, adapter_type="slack_export", canonical_root="./x"),
        "feedback.add": lambda: service.feedback_add(subject_id=bad, utterance="that is not how I would say it"),
        "fact.correct": lambda: service.fact_correct(subject_id=bad, facet="tone", new_value="warm"),
        "subject.pause": lambda: service.subject_pause(subject_id=bad),
        "reanalyze": lambda: service.reanalyze(subject_id=bad),
    }
    with pytest.raises(ServiceError):
        calls[op_name]()


def test_feedback_add_unknown_subject_is_clean_not_a_raw_integrity_error(service):
    # Regression: previously raised sqlite3.IntegrityError (FOREIGN KEY) — a raw DB crash mislabeled
    # in the card as a friendly unknown_subject. Must now be a ServiceError.
    with pytest.raises(ServiceError):
        service.feedback_add(subject_id="subj_NOPE", utterance="reads a little stiff")


def test_fact_deprecate_refuses_unknown_fact(service):
    assert "unknown_fact" in _codes("fact.deprecate")
    with pytest.raises(ServiceError):
        service.fact_deprecate(fact_id="fact_NOPE")


def test_portability_export_unknown_subject_raises_after_confirmation(service):
    service.setup(display_label="Op")
    # First call returns the challenge (no change); second call with the literal reaches the subject
    # check and must raise for an unknown subject.
    first = service.portability_export(destination="/tmp/ovt-x.ovtpack", passphrase="correct-horse-1", subject_id="subj_NOPE")
    assert first["status"] == "confirmation_required"
    m = re.search(r"challenge_literal='([^']+)'", first["instructions"])
    assert m, f"no quoted challenge_literal in {first['instructions']!r}"
    literal = m.group(1)
    with pytest.raises(ServiceError):
        service.portability_export(
            destination="/tmp/ovt-x.ovtpack", passphrase="correct-horse-1",
            subject_id="subj_NOPE", challenge_literal=literal,
        )


@pytest.mark.parametrize("op_name", ["profile.inspect", "profile.search", "draft.validate"])
def test_ops_that_do_not_look_up_the_subject_neither_raise_nor_advertise(service, op_name):
    # These declare a subject_id but never look it up (pure reads), so they must NOT advertise
    # unknown_subject and must not raise on an unknown id (they return an empty-but-valid result).
    assert "unknown_subject" not in _codes(op_name)
    bad = "subj_NOPE"
    calls = {
        "profile.inspect": lambda: service.profile_inspect(subject_id=bad),
        "profile.search": lambda: service.profile_search(subject_id=bad),
        "draft.validate": lambda: service.draft_validate(subject_id=bad, draft_text="hello there team"),
    }
    result = calls[op_name]()  # must not raise
    assert isinstance(result, dict)


def test_pause_ops_refuse_unknown_ids_instead_of_false_success(service):
    # Regression (review-swarm operator-integrity P1, same class as fact.deprecate): pausing an
    # unknown subject/source used to run a 0-row UPDATE and still report {"status": "paused"} — a
    # mutation lying about success. Both must now refuse and advertise the accurate error.
    assert "unknown_subject" in _codes("subject.pause")
    assert "unknown_source" in _codes("source.pause")
    with pytest.raises(ServiceError):
        service.subject_pause(subject_id="subj_NOPE")
    with pytest.raises(ServiceError):
        service.source_pause(source_id="src_NOPE")


def test_source_and_generation_and_review_id_errors_are_accurate(service):
    assert "unknown_source" in _codes("ingest")
    assert "unknown_generation" in _codes("rebuild")
    assert "no_open_review_item" in _codes("review.resolve")
    with pytest.raises(ServiceError):
        service.ingest(source_id="src_NOPE")
    with pytest.raises(ServiceError):
        service.rebuild(generation_id="gen_NOPE")
    with pytest.raises(ServiceError):
        service.review_resolve(review_id="rev_NOPE")
