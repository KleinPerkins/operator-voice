"""M2.1 context-taxonomy tests — build plan §13 worklist 1 acceptance
("multi-context synthesis, ranking, and bleed tests"; "Multi-context/language retrieval has no
unreported bleed").
"""

from __future__ import annotations

import pytest

from operator_voice.domain.models import ContextScope, FactState, ProfileFact
from operator_voice.kernel import taxonomy
from operator_voice.kernel.retrieval import (
    BundleRequest,
    build_context_bundle,
    rank_facts_with_relaxations,
)


# --------------------------------------------------------------------------------------
# Disambiguation
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "dimension,raw,expected",
    [
        ("channel", "IM", "slack"),
        ("channel", "e-mail", "email"),
        ("channel", "Gdoc", "docs"),
        ("channel", "PR", "code_review"),
        ("audience", "colleague", "peer"),
        ("audience", "execs", "exec"),
        ("audience", "client", "customer"),
        ("purpose", "status_update", "update"),
        ("purpose", "convince", "persuasion"),
        ("output_type", "msg", "message"),
        ("output_type", "tldr", "summary"),
        ("language", "english", "en"),
        ("language", "en_gb", "en-GB"),
        ("language", "spanish", "es"),
    ],
)
def test_disambiguate_maps_known_synonyms(dimension, raw, expected):
    assert taxonomy.disambiguate(dimension, raw) == expected


def test_disambiguate_is_case_insensitive_for_canonical_language_variants():
    assert taxonomy.disambiguate("language", "EN-gb") == "en-GB"
    assert taxonomy.disambiguate("language", "en-US") == "en-US"


def test_unknown_value_passes_through_lowercased_never_fuzzy_merged():
    # A genuinely unknown channel must NOT be merged into any known bucket (ContextScope contract).
    assert taxonomy.disambiguate("channel", "Carrier_Pigeon") == "carrier_pigeon"
    assert taxonomy.disambiguate("audience", "Regulators") == "regulators"


def test_normalize_scope_canonicalises_all_dimensions():
    raw = ContextScope(channel="IM", audience="execs", purpose="convince",
                       language="english", output_type="msg")
    norm = taxonomy.normalize_scope(raw)
    assert norm.key() == "slack|exec|persuasion|en|message"


# --------------------------------------------------------------------------------------
# Language families
# --------------------------------------------------------------------------------------

def test_base_language_strips_variant():
    assert taxonomy.base_language("en-GB") == "en"
    assert taxonomy.base_language("pt-BR") == "pt"
    assert taxonomy.base_language("es-419") == "es"
    assert taxonomy.base_language("en") == "en"


def test_language_compatible_only_within_base_family():
    assert taxonomy.language_compatible("en-GB", "en-US") is True
    assert taxonomy.language_compatible("en", "en-GB") is True
    assert taxonomy.language_compatible("es", "es-419") is True
    assert taxonomy.language_compatible("en", "es") is False
    assert taxonomy.language_compatible("pt-BR", "es") is False


# --------------------------------------------------------------------------------------
# Candidate scopes / ranking
# --------------------------------------------------------------------------------------

def test_candidate_scopes_exact_first_then_context_relaxations():
    req = ContextScope(channel="slack", audience="peer", purpose="update",
                       language="en", output_type="message")
    cands = taxonomy.candidate_scopes(req)
    assert cands[0].is_exact
    assert cands[0].scope.key() == req.key()
    # All later candidates carry at least one relaxation tag.
    assert all(c.relaxations for c in cands[1:])
    # The most-general candidate has every relaxable dimension at its general value.
    most_general = cands[-1].scope
    assert most_general.channel == "general"
    assert most_general.audience == "general"
    assert most_general.purpose == "general"
    assert most_general.output_type == "text"
    # Language never relaxed to a generic bucket — it stays the request language here.
    assert most_general.language == "en"


def test_candidate_scopes_language_variant_falls_back_to_base_but_never_across_base():
    req = ContextScope(channel="slack", language="en-GB")
    cands = taxonomy.candidate_scopes(req)
    langs = {c.scope.language for c in cands}
    assert "en-GB" in langs
    assert "en" in langs  # base fallback allowed and enumerated
    # No other base language ever appears.
    assert all(taxonomy.base_language(lang) == "en" for lang in langs)
    # The base-language fallback is reported.
    base_cand = next(c for c in cands if c.scope.language == "en" and c.scope.channel == "slack")
    assert any("language_variant_fallback:en" == r for r in base_cand.relaxations)


def test_ranking_prefers_exact_context_variant_fallback_over_relaxed_context():
    req = ContextScope(channel="slack", audience="peer", language="en-GB")
    cands = taxonomy.candidate_scopes(req)
    keys = [c.scope.key() for c in cands]
    exact = ContextScope(channel="slack", audience="peer", language="en-GB").key()
    base_same_ctx = ContextScope(channel="slack", audience="peer", language="en").key()
    # exact-context/base-language must rank before any context-relaxed candidate.
    first_relaxed_ctx = next(
        i for i, c in enumerate(cands) if any(r.startswith("context_fallback") for r in c.relaxations)
    )
    assert keys.index(exact) < keys.index(base_same_ctx) < first_relaxed_ctx


# --------------------------------------------------------------------------------------
# classify_fact_scope + bleed
# --------------------------------------------------------------------------------------

def test_classify_exact_match_has_no_relaxations():
    scope = ContextScope(channel="slack", audience="peer")
    usable, relax = taxonomy.classify_fact_scope(scope, scope)
    assert usable is True
    assert relax == ()


def test_classify_general_fact_is_usable_but_reported():
    fact_scope = ContextScope()  # fully general
    req = ContextScope(channel="slack", audience="peer", purpose="update", output_type="message")
    usable, relax = taxonomy.classify_fact_scope(fact_scope, req)
    assert usable is True
    assert any(r.startswith("context_fallback") for r in relax)


def test_legacy_adapter_channel_labels_normalize_to_general_and_serve_slack():
    """M3 adapters once stored facts under source-type channel names (cursor/artifact)."""
    legacy = ContextScope(channel="artifact")
    req = ContextScope(channel="slack")
    assert taxonomy.normalize_scope(legacy).channel == "general"
    usable, relax = taxonomy.classify_fact_scope(legacy, req)
    assert usable is True
    assert any(r.startswith("context_fallback:channel=") for r in relax)


def test_classify_cross_channel_is_bleed_excluded():
    fact_scope = ContextScope(channel="email")
    req = ContextScope(channel="slack")
    usable, relax = taxonomy.classify_fact_scope(fact_scope, req)
    assert usable is False
    assert relax and relax[0].startswith("bleed:")


def test_classify_cross_language_is_always_bleed():
    fact_scope = ContextScope(channel="slack", language="es")
    req = ContextScope(channel="slack", language="en")
    usable, relax = taxonomy.classify_fact_scope(fact_scope, req)
    assert usable is False
    assert relax[0].startswith("bleed:language=")


# --------------------------------------------------------------------------------------
# Multi-context synthesis + no-unreported-bleed at the retrieval boundary
# --------------------------------------------------------------------------------------

def _fact(fact_id: str, value: str, scope: ContextScope, *, facet: str = "tone") -> ProfileFact:
    return ProfileFact(
        fact_id=fact_id, subject_id="s", facet=facet, value=value, scope=scope,
        state=FactState.SUPPORTED, confidence=0.9, valid_from=1000.0,
    )


def test_multi_context_synthesis_ranks_exact_above_general_and_reports_relaxation():
    req_scope = ContextScope(channel="slack", audience="peer")
    facts = [
        _fact("f_general", "general_tone", ContextScope()),
        _fact("f_exact", "slack_peer_tone", req_scope),
        _fact("f_email", "email_tone", ContextScope(channel="email")),  # bleed
    ]
    ranked = rank_facts_with_relaxations(facts, req_scope)
    values = [f.value for f, _ in ranked]
    # exact first, general included (reported), email excluded entirely.
    assert values[0] == "slack_peer_tone"
    assert "general_tone" in values
    assert "email_tone" not in values
    # The general fact carries a reported relaxation; the exact one does not.
    relax_by_value = {f.value: relax for f, relax in ranked}
    assert relax_by_value["slack_peer_tone"] == ()
    assert any(r.startswith("context_fallback") for r in relax_by_value["general_tone"])


def test_bundle_reports_every_relaxation_and_excludes_cross_language_bleed():
    req_scope = ContextScope(channel="slack", audience="peer", language="en-GB")
    facts = [
        _fact("f_general_en", "general", ContextScope(language="en"), facet="general_trait"),
        _fact("f_exact", "exact", req_scope),
        _fact("f_spanish", "spanish", ContextScope(channel="slack", language="es"), facet="other_trait"),
    ]
    bundle = build_context_bundle(
        BundleRequest(subject_id="s", consumer_id="c", generation_id="g",
                      scope=req_scope, token_budget=1000),
        facts=facts,
    )
    values = [f.value for f in bundle.ranked_facts]
    assert "exact" in values
    assert "spanish" not in values  # cross-base-language never selected
    # Both the context relaxation and the language-variant fallback are reported as warnings.
    relaxation_warnings = [w for w in bundle.warnings if w.startswith("context_relaxation:")]
    assert any("context_fallback" in w for w in relaxation_warnings)
    assert any("language_variant_fallback:en" in w for w in relaxation_warnings)


def test_no_relaxation_warning_when_only_exact_facts_selected():
    req_scope = ContextScope(channel="slack")
    facts = [_fact("f_exact", "exact", req_scope)]
    bundle = build_context_bundle(
        BundleRequest(subject_id="s", consumer_id="c", generation_id="g",
                      scope=req_scope, token_budget=1000),
        facts=facts,
    )
    assert not [w for w in bundle.warnings if w.startswith("context_relaxation:")]


def test_sensitivity_floor_elevates_customer_and_apology_contexts():
    assert taxonomy.sensitivity_floor(ContextScope(audience="customer")) == "elevated"
    assert taxonomy.sensitivity_floor(ContextScope(purpose="apology")) == "elevated"
    assert taxonomy.sensitivity_floor(ContextScope(channel="slack", audience="peer")) == "standard"
