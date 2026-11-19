"""M1 / ADR 0005: the token budget bounds the whole emitted bundle. Synthetic facts only."""
import json

import pytest

from operator_voice.domain.contracts import voice_context_bundle_to_dict
from operator_voice.domain.models import ContextScope, FactState, ProfileFact
from operator_voice.kernel import retrieval
from operator_voice.kernel.retrieval import (
    EMITTED_JSON_SEPARATORS, MIN_TOKEN_BUDGET, BundleRequest, build_context_bundle, estimate_tokens,
)

FIXED = {"state": FactState.CORRECTED, "confidence": 1.0}


def _fact(facet, value, *, state=FactState.SUPPORTED, channel="general", confidence=0.8, ids=2000, fact_id=None):
    return ProfileFact(fact_id=fact_id or f"fact_{facet}_{channel}", subject_id="subj_test", facet=facet,
                       value=value, scope=ContextScope(channel=channel), state=state, confidence=confidence,
                       document_count=ids, provenance_ids=[f"obs_{i:032d}" for i in range(ids)], valid_from=1000.0)


def _profile():
    facets = [f"facet_{i:02d}" for i in range(40)]
    return ([_fact(f, 0.5 + i / 100) for i, f in enumerate(facets)]
            + [_fact(f, 9.0, channel="slack") for f in facets[:10]] + [_fact("facet_legacy", 1.0, channel="cursor")]
            + [_fact("punct_semicolon_rate", 0.0, **FIXED), _fact("punct_colon_rate", 0.0, **FIXED)])


def _build(budget, facts=None, scope=None):
    request = BundleRequest(subject_id="subj_" + "0" * 32, consumer_id="cursor-agent", generation_id="gen_" + "0" * 32,
                            scope=scope or ContextScope(channel="slack"), token_budget=budget)
    return build_context_bundle(request, _profile() if facts is None else facts)


def _emit(bundle):
    return json.dumps(voice_context_bundle_to_dict(bundle), separators=EMITTED_JSON_SEPARATORS, default=str)


def _selected(bundle):
    return [(f.facet, f.value) for f in bundle.ranked_facts]


@pytest.mark.parametrize("budget", [200, 400, 800, 1600, 4000])
def test_emitted_bundle_never_exceeds_budget(budget):
    bundle = _build(budget)
    assert estimate_tokens(_emit(bundle)) <= bundle.actual_tokens <= budget


def test_corrections_first_one_value_per_facet_no_ids_no_legacy_labels():
    bundle = _build(4000)
    facets = [f.facet for f in bundle.ranked_facts]
    assert {f.state for f in bundle.ranked_facts[:2]} == {FactState.CORRECTED}
    assert len(facets) == len(set(facets)) and "facet_legacy" not in facets
    assert {f.facet: f for f in bundle.ranked_facts}["facet_00"].scope.channel == "slack"
    assert not set(bundle.instructions["corrective"]) & set(bundle.instructions["generative"])
    assert bundle.provenance_refs == [] and "obs_" not in _emit(bundle)


def test_set_valued_facets_keep_each_distinct_value_once():
    phrases = ["synthetic phrase one", "synthetic phrase two", "synthetic phrase two"]
    facts = [_fact("banned_phrase", p, ids=0, fact_id=f"fact_ban_{i}", **FIXED) for i, p in enumerate(phrases)]
    assert sorted(v for _, v in _selected(_build(800, facts, ContextScope()))) == sorted(set(phrases))


def test_cut_ignores_the_clock_and_larger_budgets_only_add_facts(monkeypatch):
    cuts = []
    for clock in (1.0, 1761190000.1234567):
        monkeypatch.setattr(retrieval, "now_ts", lambda clock=clock: clock)
        cuts.append([_selected(_build(b)) for b in range(300, 4001, 100)])
    assert cuts[0] == cuts[1]
    assert all(small == large[:len(small)] for small, large in zip(cuts[0], cuts[0][1:]))


def test_abstains_within_the_minimum_with_a_retry_hint_that_works():
    facts = [_fact(f"rule_{i}", 0.0, **FIXED) for i in range(6)]
    small = _build(MIN_TOKEN_BUDGET, facts, ContextScope())
    assert small.abstained and small.ranked_facts == [] and small.actual_tokens <= MIN_TOKEN_BUDGET
    hint = next(int(w.split(":", 1)[1]) for w in small.warnings if w.startswith("minimum_budget_tokens:"))
    assert len(_build(hint, facts, ContextScope()).ranked_facts) == 6


def test_repeated_builds_differ_only_in_envelope_identity():
    a, b = (voice_context_bundle_to_dict(_build(800)) for _ in range(2))
    for bundle in (a, b):
        bundle.pop("bundle_id"), bundle.pop("created_at")
    assert a == b


def test_correction_beats_exact_scope_confirmation_for_the_same_facet():
    facts = [
        _fact("tone", "confirmed", state=FactState.CONFIRMED, channel="slack"),
        _fact("tone", "corrected", **FIXED),
    ]
    bundle = _build(800, facts)
    assert _selected(bundle) == [("tone", "corrected")]
    assert bundle.ranked_facts[0].state == FactState.CORRECTED


def test_distinct_inferred_set_value_survives_an_authoritative_set_value():
    facts = [
        _fact("banned_phrase", "synthetic first", **FIXED),
        _fact("banned_phrase", "synthetic second"),
    ]
    assert _selected(_build(800, facts)) == [
        ("banned_phrase", "synthetic first"), ("banned_phrase", "synthetic second"),
    ]


def test_oversized_request_metadata_refuses_instead_of_emitting_over_budget():
    request = BundleRequest(
        subject_id="s", consumer_id="c" * 4000, generation_id="g",
        scope=ContextScope(), token_budget=200,
    )
    with pytest.raises(ValueError, match="minimum_budget_tokens:"):
        build_context_bundle(request, [])
