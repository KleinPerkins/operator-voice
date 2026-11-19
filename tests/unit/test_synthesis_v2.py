"""M2.3 synthesis v2 — build plan §13 M2 worklist 3: calibrated confidence, source-diversity caps,
temporal validity (drift/supersede), and deterministic review reasons. Kernel-only (no I/O),
synthetic observations authored for this suite."""

from __future__ import annotations

from operator_voice.domain.models import ContextScope, FactState, ProfileFact, ReviewReason, SignalObservation
from operator_voice.kernel import synthesis
from operator_voice.kernel.calibration import DEFAULT_CALIBRATION


def _obs(doc: str, value: float) -> SignalObservation:
    return SignalObservation(
        observation_id=f"obs_{doc}_{value}", observation_key=f"key_{doc}_{value}", subject_id="subj_1",
        document_id=doc, segment_id=f"{doc}:0", facet="avg_sentence_length", value=value,
    )


def test_single_source_supported_fact_is_confidence_capped_and_flagged():
    """A fact promoted to SUPPORTED on 3 documents but only ONE source is a monoculture: its
    confidence must be capped and a LOW_SOURCE_DIVERSITY review reason attached (§13 worklist 3)."""
    observations = [_obs("d1", 10.0), _obs("d2", 10.0), _obs("d3", 10.0)]
    one_source = {"d1": "src_1", "d2": "src_1", "d3": "src_1"}
    result = synthesis.synthesize_fact(
        subject_id="subj_1", facet="avg_sentence_length", scope=ContextScope(),
        observations=observations, document_sources=one_source, calibration=DEFAULT_CALIBRATION,
    )
    assert result.fact.state == FactState.SUPPORTED
    assert result.fact.source_count == 1
    assert result.fact.confidence == DEFAULT_CALIBRATION.single_source_confidence_cap
    assert ReviewReason.LOW_SOURCE_DIVERSITY in result.review_reasons


def test_multi_source_supported_fact_is_not_capped_or_flagged():
    observations = [_obs("d1", 10.0), _obs("d2", 10.0), _obs("d3", 10.0)]
    three_sources = {"d1": "src_1", "d2": "src_2", "d3": "src_3"}
    result = synthesis.synthesize_fact(
        subject_id="subj_1", facet="avg_sentence_length", scope=ContextScope(),
        observations=observations, document_sources=three_sources, calibration=DEFAULT_CALIBRATION,
    )
    assert result.fact.state == FactState.SUPPORTED
    assert result.fact.source_count == 3
    assert result.fact.confidence > DEFAULT_CALIBRATION.single_source_confidence_cap
    assert ReviewReason.LOW_SOURCE_DIVERSITY not in result.review_reasons


def test_material_drift_supersedes_rather_than_overwrites_and_flags_drift():
    """Temporal validity: when a prior SUPPORTED numeric fact drifts materially, a NEW fact is
    minted (new id) and the old one is reported for supersede — history is preserved, not
    overwritten. DRIFT is attached for operator review."""
    existing = ProfileFact(
        fact_id="fact_old", subject_id="subj_1", facet="avg_sentence_length", value=10.0,
        scope=ContextScope(), state=FactState.SUPPORTED, confidence=0.9,
    )
    drifted = [_obs("d1", 20.0), _obs("d2", 20.0), _obs("d3", 20.0)]
    three_sources = {"d1": "src_1", "d2": "src_2", "d3": "src_3"}
    result = synthesis.synthesize_fact(
        subject_id="subj_1", facet="avg_sentence_length", scope=ContextScope(),
        observations=drifted, document_sources=three_sources, existing_fact=existing,
        calibration=DEFAULT_CALIBRATION,
    )
    assert result.superseded_fact_id == "fact_old"
    assert result.fact.fact_id != "fact_old"
    assert ReviewReason.DRIFT in result.review_reasons


def test_non_material_change_updates_in_place_without_drift():
    existing = ProfileFact(
        fact_id="fact_keep", subject_id="subj_1", facet="avg_sentence_length", value=10.0,
        scope=ContextScope(), state=FactState.SUPPORTED, confidence=0.9,
    )
    steady = [_obs("d1", 10.2), _obs("d2", 10.1), _obs("d3", 10.3)]
    three_sources = {"d1": "src_1", "d2": "src_2", "d3": "src_3"}
    result = synthesis.synthesize_fact(
        subject_id="subj_1", facet="avg_sentence_length", scope=ContextScope(),
        observations=steady, document_sources=three_sources, existing_fact=existing,
        calibration=DEFAULT_CALIBRATION,
    )
    assert result.superseded_fact_id is None
    assert result.fact.fact_id == "fact_keep"
    assert ReviewReason.DRIFT not in result.review_reasons


def test_corrected_fact_still_never_mutated_and_produces_no_review_reasons():
    corrected = ProfileFact(
        fact_id="fact_corr", subject_id="subj_1", facet="avg_sentence_length", value=12.0,
        scope=ContextScope(), state=FactState.CORRECTED, confidence=1.0,
    )
    contradicting = [_obs("d1", 40.0), _obs("d2", 40.0), _obs("d3", 40.0)]
    result = synthesis.synthesize_fact(
        subject_id="subj_1", facet="avg_sentence_length", scope=ContextScope(),
        observations=contradicting, document_sources={"d1": "s1", "d2": "s2", "d3": "s3"},
        existing_fact=corrected, calibration=DEFAULT_CALIBRATION,
    )
    assert result.fact is corrected
    assert result.contradiction is True
    assert result.review_reasons == ()  # a frozen corrected fact never spawns synthesis-driven reasons
