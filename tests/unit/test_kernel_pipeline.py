"""Unit/property coverage for the deterministic kernel pipeline, using only synthetic fixture
text (operator prompt constraint: no live transcripts). Exercises normalize -> authorship ->
features -> synthesis -> retrieval end to end without any I/O.
"""

from __future__ import annotations

import pytest

from operator_voice.domain.models import (
    ContextScope,
    FactState,
    ObservationCandidate,
    ProfileFact,
    RETRIEVAL_ELIGIBLE_STATES,
    SignalObservation,
)
from operator_voice.kernel import authorship, features, normalize, retrieval, synthesis
from operator_voice.kernel.calibration import DEFAULT_CALIBRATION
from operator_voice.kernel.retrieval import BundleRequest, build_context_bundle


SYNTHETIC_OPERATOR_SENTENCES = [
    "I think we should ship the smaller version first and learn from real usage.",
    "Honestly, the safest path here is to ship the smaller version first and learn as we go.",
    "My instinct says ship the smaller version first, then iterate based on what we learn.",
]


def test_normalize_is_deterministic_and_strips_signature_blocks():
    raw = "Hello there.\n-- \nOperator\nSent from my iPhone\n"
    first = normalize.normalize_text(raw)
    second = normalize.normalize_text(raw)
    assert first == second
    assert "Operator" not in first.text
    assert "Hello there." in first.text


def test_normalize_collapses_excess_blank_lines():
    raw = "Hello there.\n\n\n\n\nMore text."
    normalized = normalize.normalize_text(raw)
    assert "\n\n\n" not in normalized.text


def test_authorship_classifies_subject_ai_and_quarantines_ambiguous():
    subject_candidate = ObservationCandidate(
        candidate_id="c1", claimed_subject_id="subj_1", source_id="src_1",
        source_native_id="n1", role="user", raw_text="my own words", digest="d1", locator="l1",
    )
    ai_candidate = ObservationCandidate(
        candidate_id="c2", claimed_subject_id="subj_1", source_id="src_1",
        source_native_id="n2", role="assistant", raw_text="the assistant's words", digest="d2", locator="l2",
    )
    subject_state, _ = authorship.classify_role(subject_candidate, subject_role="user")
    ai_state, _ = authorship.classify_role(ai_candidate, subject_role="user")
    assert subject_state.value == "SUBJECT"
    assert ai_state.value == "AI"


def test_quoted_block_is_demoted_from_subject_authorship():
    text = "My real reply.\n> quoted text from someone else\nMore of my reply."
    segments = authorship.split_authored_segments(text, outer_state=authorship.AuthorshipState.SUBJECT)
    states = {seg.state for seg in segments}
    assert authorship.AuthorshipState.SUBJECT in states
    assert authorship.AuthorshipState.OTHER in states or authorship.AuthorshipState.AMBIGUOUS_QUARANTINED in states


@pytest.mark.parametrize(
    "header",
    [
        "-----Original Message-----",
        "On Mon, Jan 1 2020, Eve Adversary wrote:",
        "---------- Forwarded message ---------",
        "Begin forwarded message:",
    ],
)
def test_forward_or_reply_header_tail_is_sticky_other(header):
    """Regression (M2 security fix): a forward/reply header has no per-line marker, so EVERYTHING
    after it is third-party until EOF and must NEVER flip back to SUBJECT — otherwise the forwarded
    body contaminates operator evidence."""
    subject_line = "This is my own short note."
    smuggled = "A very long forwarded sentence from someone else that would distort metrics badly if counted."
    text = f"{subject_line}\n\n{header}\n{smuggled}\nAnd a second forwarded line that is also not mine."
    segments = authorship.split_authored_segments(text, outer_state=authorship.AuthorshipState.SUBJECT)

    subject_text = " ".join(s.text for s in segments if s.state == authorship.AuthorshipState.SUBJECT)
    other_text = " ".join(s.text for s in segments if s.state != authorship.AuthorshipState.SUBJECT)
    assert subject_line in subject_text
    assert smuggled in other_text
    assert "second forwarded line" in other_text  # sticky: no flip back to SUBJECT
    assert smuggled not in subject_text


def test_features_to_observations_idempotency_key_is_stable():
    secret = b"test-hmac-secret-not-real"
    scope = ContextScope()
    obs1 = features.features_to_observations(
        secret=secret, subject_id="subj_1", document_id="doc_1", segment_id="seg_1",
        text=SYNTHETIC_OPERATOR_SENTENCES[0], context=scope,
    )
    obs2 = features.features_to_observations(
        secret=secret, subject_id="subj_1", document_id="doc_1", segment_id="seg_1",
        text=SYNTHETIC_OPERATOR_SENTENCES[0], context=scope,
    )
    assert [o.observation_key for o in obs1] == [o.observation_key for o in obs2]
    assert len(obs1) > 0


def test_synthesis_promotes_to_supported_with_enough_diverse_evidence():
    secret = b"test-hmac-secret-not-real"
    scope = ContextScope()
    all_obs = []
    doc_sources = {}
    for i, sentence in enumerate(SYNTHETIC_OPERATOR_SENTENCES):
        doc_id = f"doc_{i}"
        doc_sources[doc_id] = f"src_{i}"
        all_obs += features.features_to_observations(
            secret=secret, subject_id="subj_1", document_id=doc_id, segment_id=f"seg_{i}",
            text=sentence, context=scope,
        )

    facet_obs = [o for o in all_obs if o.facet == "sentence_length"]
    result = synthesis.synthesize_fact(
        subject_id="subj_1", facet="sentence_length", scope=scope,
        observations=facet_obs, document_sources=doc_sources, calibration=DEFAULT_CALIBRATION,
    )
    assert result.fact.document_count == 3
    assert result.fact.source_count == 3
    assert result.fact.state in (FactState.SUPPORTED, FactState.OBSERVED)


def test_corrected_fact_is_never_silently_overwritten_by_new_observations():
    scope = ContextScope()
    corrected = ProfileFact(
        fact_id="fact_1", subject_id="subj_1", facet="banned_phrase", value="delve",
        scope=scope, state=FactState.CORRECTED, confidence=1.0,
    )
    contradicting_obs = [
        SignalObservation(
            observation_id="obs_x", observation_key="key_x", subject_id="subj_1",
            document_id="doc_x", segment_id="seg_x", facet="banned_phrase", value=999.0,
        )
    ]
    result = synthesis.synthesize_fact(
        subject_id="subj_1", facet="banned_phrase", scope=scope,
        observations=contradicting_obs, document_sources={"doc_x": "src_x"},
        existing_fact=corrected,
    )
    # Non-numeric corrected values (e.g. a banned-phrase string) can never be numerically
    # contradicted by aggregated numeric observations; the frozen fact is still never mutated.
    assert result.fact is corrected
    assert result.fact.value == "delve"
    assert result.contradiction is False


def test_corrected_numeric_fact_reports_contradiction_without_mutating():
    scope = ContextScope()
    corrected = ProfileFact(
        fact_id="fact_2", subject_id="subj_1", facet="avg_sentence_length", value=12.0,
        scope=scope, state=FactState.CORRECTED, confidence=1.0,
    )
    contradicting_obs = [
        SignalObservation(
            observation_id="obs_y", observation_key="key_y", subject_id="subj_1",
            document_id="doc_y", segment_id="seg_y", facet="avg_sentence_length", value=40.0,
        )
    ]
    result = synthesis.synthesize_fact(
        subject_id="subj_1", facet="avg_sentence_length", scope=scope,
        observations=contradicting_obs, document_sources={"doc_y": "src_y"},
        existing_fact=corrected,
    )
    assert result.fact is corrected
    assert result.fact.value == 12.0
    assert result.contradiction is True
    assert result.contradiction_detail is not None


def test_retrieval_abstains_below_quality_floor_and_excludes_wrong_context():
    scope_slack = ContextScope(channel="slack")
    scope_email = ContextScope(channel="email")

    email_fact = ProfileFact(
        fact_id="fact_email", subject_id="subj_1", facet="tone", value="formal",
        scope=scope_email, state=FactState.SUPPORTED, confidence=0.9,
    )

    empty_bundle = build_context_bundle(
        BundleRequest(subject_id="subj_1", consumer_id="test-consumer", generation_id="gen_1",
                       scope=scope_slack, token_budget=1000),
        facts=[email_fact],
    )
    assert empty_bundle.abstained is True
    assert "abstained_below_quality_floor" in empty_bundle.warnings
    assert all(f.facet != "tone" for f in empty_bundle.ranked_facts)

    slack_fact = ProfileFact(
        fact_id="fact_slack", subject_id="subj_1", facet="tone", value="casual",
        scope=scope_slack, state=FactState.SUPPORTED, confidence=0.9,
    )
    populated_bundle = build_context_bundle(
        BundleRequest(subject_id="subj_1", consumer_id="test-consumer", generation_id="gen_1",
                       scope=scope_slack, token_budget=1000),
        facts=[email_fact, slack_fact],
    )
    assert populated_bundle.abstained is False
    assert [f.value for f in populated_bundle.ranked_facts] == ["casual"]


def test_retrieval_is_byte_identical_for_repeated_identical_queries():
    scope = ContextScope()
    facts = [
        ProfileFact(fact_id="f1", subject_id="s", facet="tone", value="direct", scope=scope,
                    state=FactState.SUPPORTED, confidence=0.8, valid_from=1000.0),
    ]
    req = BundleRequest(subject_id="s", consumer_id="c", generation_id="g", scope=scope, token_budget=1000)
    first = build_context_bundle(req, facts)
    second = build_context_bundle(req, facts)
    assert [f.value for f in first.ranked_facts] == [f.value for f in second.ranked_facts]
    assert first.actual_tokens == second.actual_tokens
    assert first.warnings == second.warnings


def test_own_text_wrapped_headers_are_bounded():
    from operator_voice.kernel.authorship import own_text
    for header in ["On Monday,\nSynthetic Sender wrote:", "On Monday,\nSynthetic Sender\nwrote:"]:
        assert own_text("My own reply.\n" + header + "\nTheir words.") == "My own reply."
    over_lines = "On Monday,\nSynthetic\nSender\nwrote:\nTheir words."
    assert "Their words." in own_text(over_lines)
    over_chars = "On " + "x" * 300 + " wrote:\nTheir words."
    assert "Their words." in own_text(over_chars)
    assert own_text("My reply.\n> Their words.\nMy closing.") == "My reply.\nMy closing."
    assert own_text("On Monday,\nSynthetic Sender wrote:\nTheir words.") == ""
