"""EXTERNAL-subject analysis kernel — build plan v4 §4.3 subject-kind table, §13 M4 worklist 2, D17.

Pure/deterministic coverage of the refusal gate, the high-risk facet denylist, and evidence-bounded
observation building (uncertainty + counter-evidence + proxy-risk warning). All fixtures are
SYNTHETIC; no real person's data appears anywhere.
"""

from __future__ import annotations

import pytest

from operator_voice.domain.models import ContextScope, FactState, ProfileFact
from operator_voice.kernel import external_analysis as ea


def _fact(facet: str, value, state: FactState, *, confidence: float = 0.9,
          source_count: int = 2, document_count: int = 5, ci=None, scope: ContextScope | None = None) -> ProfileFact:
    return ProfileFact(
        fact_id=f"fact_{facet}", subject_id="subj_ext", facet=facet, value=value,
        scope=scope or ContextScope(), state=state, confidence=confidence,
        source_count=source_count, document_count=document_count, confidence_interval=ci,
    )


# --------------------------------------------------------------------------------------
# classify_purpose — fail-closed refusal set
# --------------------------------------------------------------------------------------

def test_analysis_purpose_is_allowed():
    v = ea.classify_purpose("understand their historical communication patterns")
    assert v.allowed is True
    assert v.category == "analysis"


@pytest.mark.parametrize("purpose", [
    "write as them in their voice",
    "help me impersonate this person",
    "draft as this person",
    "ghostwrite a reply as them",
])
def test_generation_or_impersonation_refused(purpose):
    v = ea.classify_purpose(purpose)
    assert v.allowed is False
    assert v.category == "generation_or_impersonation"


@pytest.mark.parametrize("purpose", [
    "predict what they will decide next quarter",
    "forecast their future behavior",
    "what will they do about the deal",
])
def test_future_decision_forecast_refused(purpose):
    v = ea.classify_purpose(purpose)
    assert v.allowed is False
    assert v.category == "future_decision_forecast"


@pytest.mark.parametrize("purpose", [
    "infer their political affiliation",
    "assess their mental health",
    "what is their religion",
    "diagnose their personality type",
])
def test_protected_or_sensitive_trait_refused(purpose):
    v = ea.classify_purpose(purpose)
    assert v.allowed is False
    assert v.category == "protected_or_sensitive_trait"


@pytest.mark.parametrize("purpose", [
    "should we hire this candidate",
    "decide whether to fire them",
    "assess their creditworthiness",
    "should i trust them for a loan",
])
def test_high_impact_decision_refused(purpose):
    v = ea.classify_purpose(purpose)
    assert v.allowed is False
    assert v.category == "high_impact_decision_support"


def test_empty_purpose_refused():
    assert ea.classify_purpose("").allowed is False
    assert ea.classify_purpose("   ").allowed is False


def test_refusal_category_is_deterministic_for_multi_marker_purpose():
    # A purpose that trips BOTH generation and forecast markers resolves to a stable category
    # (generation is checked first) — never nondeterministic.
    p = "write as them and predict what they will decide"
    assert ea.classify_purpose(p).category == "generation_or_impersonation"
    assert ea.classify_purpose(p).category == "generation_or_impersonation"


# --------------------------------------------------------------------------------------
# is_denied_facet — reviewed high-risk denylist + namespace guard
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("facet", ["political_lean", "religion", "health_status", "mental_state", "personality_type"])
def test_denylisted_facets_are_denied(facet):
    assert ea.is_denied_facet(facet) is True


@pytest.mark.parametrize("facet", ["sensitive:foo", "protected:bar", "trait:openness", "identity:email"])
def test_denied_namespaces_are_denied(facet):
    assert ea.is_denied_facet(facet) is True


@pytest.mark.parametrize("facet", ["sentence_length", "tone", "greeting_style", "emoji_rate"])
def test_neutral_communication_facets_are_allowed(facet):
    assert ea.is_denied_facet(facet) is False


# --------------------------------------------------------------------------------------
# build_insight_observations — evidence-bounded, uncertainty + counter-evidence
# --------------------------------------------------------------------------------------

def test_only_calibrated_states_become_observations():
    facts = [
        _fact("sentence_length", "short", FactState.SUPPORTED),
        _fact("tone", "warm", FactState.CONFIRMED),
        _fact("register", "formal", FactState.CORRECTED),
        _fact("raw_signal", 0.1, FactState.OBSERVED),        # too raw — excluded
        _fact("old_thing", "x", FactState.DEPRECATED),        # excluded from all retrieval
    ]
    obs, warnings = ea.build_insight_observations(facts)
    facets = {o.facet for o in obs}
    assert facets == {"sentence_length", "tone", "register"}
    assert all(o.uncertainty for o in obs)                    # every observation carries uncertainty
    assert all(o.counter_evidence for o in obs)               # and counter-evidence


def test_denied_facet_is_excluded_and_counted_in_warning():
    facts = [
        _fact("sentence_length", "short", FactState.SUPPORTED),
        _fact("political_lean", "left", FactState.SUPPORTED),  # denied
    ]
    obs, warnings = ea.build_insight_observations(facts)
    assert {o.facet for o in obs} == {"sentence_length"}
    assert any("high-risk facet" in w for w in warnings)      # exclusion is visible, never silent


def test_single_source_fact_flags_low_confidence_counter_evidence():
    facts = [_fact("tone", "warm", FactState.SUPPORTED, source_count=1, document_count=1, confidence=0.4)]
    obs, _ = ea.build_insight_observations(facts)
    joined = " ".join(obs[0].counter_evidence).lower()
    assert "single source" in joined or "one source" in joined
    assert "not a stable trait" in joined                     # standing caveat always present


def test_observations_are_descriptive_not_prescriptive():
    facts = [_fact("tone", "warm", FactState.CONFIRMED)]
    obs, _ = ea.build_insight_observations(facts)
    text = obs[0].observation.lower()
    # historical/descriptive framing, never an authoring instruction
    assert text.startswith("has ")
    for imperative in ("write ", "you should", "compose", "draft "):
        assert imperative not in text


def test_deterministic_ordering_by_facet_then_scope():
    facts = [
        _fact("tone", "b", FactState.SUPPORTED, scope=ContextScope(channel="chat")),
        _fact("tone", "a", FactState.SUPPORTED, scope=ContextScope()),
        _fact("audience_fit", "x", FactState.SUPPORTED),
    ]
    obs1, _ = ea.build_insight_observations(facts)
    obs2, _ = ea.build_insight_observations(list(reversed(facts)))
    assert [o.facet for o in obs1] == [o.facet for o in obs2]  # order independent of input order
    assert [o.facet for o in obs1][0] == "audience_fit"        # sorted by facet


def test_empty_evidence_yields_explicit_warning_not_silence():
    obs, warnings = ea.build_insight_observations([])
    assert obs == []
    assert any("No calibrated" in w for w in warnings)
