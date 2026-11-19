"""M2.2 Tier-2 signal tests — build plan §13 worklist 2. Determinism, additivity (no key
collision with features/v1), descriptive-only micro-rhythm (D3), and reference-driven
function-word divergence (no hidden uncited constant).
"""

from __future__ import annotations

from operator_voice.domain.models import ContextScope
from operator_voice.kernel import features, signals
from operator_voice.kernel.normalize import split_sentences, tokenize_words

_SECRET = b"test-hmac-secret-not-real-0000000"

_STRUCTURED = (
    "We shipped the smaller build first because the risk was lower. Therefore we learned quickly. "
    "However, our next step is harder — I mean, the migration itself. On the other hand, the data "
    "model is stable. Specifically, our three services now share one schema."
)
_FLAT = "the thing is a thing and the thing is a thing and the thing is a thing okay okay okay."


def test_signals_are_deterministic():
    assert signals.extract_signals(_STRUCTURED) == signals.extract_signals(_STRUCTURED)


def test_lexical_entropy_bounds_and_ordering():
    high = signals.compute_lexical_entropy(tokenize_words(_STRUCTURED))
    low = signals.compute_lexical_entropy(tokenize_words(_FLAT))
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert high > low  # diverse prose is higher-entropy than a repetitive string


def test_lexical_entropy_single_token_is_zero():
    assert signals.compute_lexical_entropy(["same", "same", "same"]) == 0.0
    assert signals.compute_lexical_entropy([]) == 0.0


def test_micro_rhythm_is_descriptive_and_counts_interruptions():
    detail = signals.compute_micro_rhythm(
        _STRUCTURED, split_sentences(_STRUCTURED), tokenize_words(_STRUCTURED)
    )
    # Em-dash and a self-correction ("I mean") are present → positive rates.
    assert detail["emdash_rate"] > 0
    assert detail["self_correction_rate"] > 0
    assert detail["micro_rhythm_rate"] >= detail["emdash_rate"]


def test_specificity_ratio_prefers_possessive_and_concrete():
    specific = signals.compute_specificity_ratio(["we", "our", "3", "services", "my", "plan"])
    generic = signals.compute_specificity_ratio(
        ["someone", "should", "do", "something", "about", "things"]
    )
    assert specific > generic


def test_argument_flow_ratio_rewards_connectives():
    structured = signals.compute_argument_flow_ratio(
        _STRUCTURED, split_sentences(_STRUCTURED), tokenize_words(_STRUCTURED)
    )
    flat = signals.compute_argument_flow_ratio(_FLAT, split_sentences(_FLAT), tokenize_words(_FLAT))
    assert structured > flat


def test_function_word_divergence_zero_without_reference():
    # No reference distribution supplied -> 0.0 (never a hidden uncited constant).
    assert signals.compute_function_word_divergence(tokenize_words(_STRUCTURED)) == 0.0


def test_function_word_divergence_uses_reference_distribution():
    words = tokenize_words(_STRUCTURED)
    rates = features.compute_function_word_rates(words)
    reference = {facet: (0.0, 1.0) for facet in rates}  # far from observed → positive divergence
    assert signals.compute_function_word_divergence(words, reference) > 0.0


def test_signal_observation_key_is_additive_and_distinct_from_features_key():
    args = dict(subject_id="s", document_id="d", segment_id="seg", facet="burstiness_sentence_length")
    assert signals.signal_observation_key(_SECRET, **args) != features.observation_key(_SECRET, **args)


def test_signals_to_observations_emit_all_facets_with_signals_version():
    obs = signals.signals_to_observations(
        secret=_SECRET, subject_id="s", document_id="d", segment_id="seg",
        text=_STRUCTURED, context=ContextScope(channel="slack"),
    )
    assert {o.facet for o in obs} == set(signals.SIGNAL_FACETS)
    assert all(o.method_version == signals.SIGNALS_VERSION for o in obs)
    micro = next(o for o in obs if o.facet == "micro_rhythm_rate")
    assert micro.distribution and "emdash_rate" in micro.distribution


def test_signals_to_observations_are_idempotent_keys():
    kw = dict(secret=_SECRET, subject_id="s", document_id="d", segment_id="seg",
              text=_STRUCTURED, context=ContextScope())
    first = {o.facet: o.observation_key for o in signals.signals_to_observations(**kw)}
    second = {o.facet: o.observation_key for o in signals.signals_to_observations(**kw)}
    assert first == second
