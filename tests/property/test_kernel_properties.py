"""Golden/property tests for the deterministic kernel — build plan §13 M1 worklist item 6:
"behaviorally port only audit-approved statistics with golden/property tests." Uses Hypothesis to
prove the properties the kernel's own docstrings promise (determinism, idempotency-key stability,
never-raises) hold over a wide input space, not just the handful of literal fixtures in
tests/unit/test_kernel_pipeline.py.
"""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given, settings

from operator_voice.domain.models import ContextScope
from operator_voice.kernel.features import extract_features, features_to_observations, observation_key
from operator_voice.kernel.normalize import normalize_text, split_sentences, tokenize_words

_TEXT = st.text(min_size=0, max_size=2000)
_SETTINGS = settings(max_examples=200, deadline=None)


@given(_TEXT)
@_SETTINGS
def test_normalize_text_never_raises(raw: str):
    normalize_text(raw)


@given(_TEXT)
@_SETTINGS
def test_normalize_text_is_deterministic(raw: str):
    a = normalize_text(raw)
    b = normalize_text(raw)
    assert a.text == b.text
    assert a.code_blocks == b.code_blocks


@given(_TEXT)
@_SETTINGS
def test_normalize_text_is_idempotent_on_its_own_output(raw: str):
    # Signature/mobile-footer stripping only ever removes trailing content once; renormalizing an
    # already-normalized text (which by construction has no trailing whitespace, no ">3 blank
    # lines, and no dangling signature marker) must be a true no-op.
    once = normalize_text(raw).text
    twice = normalize_text(once).text
    assert once == twice


@given(_TEXT)
@_SETTINGS
def test_tokenize_words_output_is_always_lowercase_alpha_or_apostrophe(raw: str):
    for w in tokenize_words(raw):
        assert w == w.lower()
        assert w != ""
        assert all(c.isalpha() or c == "'" for c in w)


@given(_TEXT)
@_SETTINGS
def test_split_sentences_never_raises_and_never_returns_blank_entries(raw: str):
    for s in split_sentences(raw):
        assert s.strip() != ""


@given(_TEXT)
@_SETTINGS
def test_extract_features_never_raises_and_is_deterministic(raw: str):
    a = extract_features(raw)
    b = extract_features(raw)
    assert a == b
    assert a["word_count"] >= 0.0
    assert a["sentence_count"] >= 0.0
    assert -1.0 <= a["burstiness_sentence_length"] <= 1.0


@given(
    secret=st.binary(min_size=16, max_size=32),
    subject_id=st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=("Cs",))),
    document_id=st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=("Cs",))),
    segment_id=st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=("Cs",))),
    facet=st.text(min_size=1, max_size=20, alphabet=st.characters(blacklist_categories=("Cs",))),
)
@_SETTINGS
def test_observation_key_is_a_pure_deterministic_function_of_its_inputs(
    secret: bytes, subject_id: str, document_id: str, segment_id: str, facet: str
):
    # This IS the exactly-once-ingestion idempotency guarantee (build plan §10.3): re-deriving the
    # same key for the same (secret, subject, document, segment, facet) tuple is what turns a
    # duplicate ingest into a pure UPSERT rather than a duplicate row.
    a = observation_key(secret, subject_id, document_id, segment_id, facet)
    b = observation_key(secret, subject_id, document_id, segment_id, facet)
    assert a == b
    assert len(a) == 64  # hex sha256
    # Changing any single input must (with overwhelming probability) change the key — a collision
    # here would silently merge two distinct facets' observations.
    different_facet = observation_key(secret, subject_id, document_id, segment_id, facet + "\x00")
    assert different_facet != a


@given(_TEXT)
@_SETTINGS
def test_features_to_observations_never_raises_and_keys_are_unique_per_facet(raw: str):
    observations = features_to_observations(
        secret=b"0" * 32, subject_id="subj-1", document_id="doc-1", segment_id="seg-1",
        text=raw, context=ContextScope(),
    )
    keys = [o.observation_key for o in observations]
    assert len(keys) == len(set(keys))  # one distinct key per facet, no accidental collision
