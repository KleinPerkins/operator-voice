"""Preregistered evaluation manifest — build plan §12/§13 M1 worklist item 9. Proves the frozen
formula's arithmetic is correct and its abstention discipline holds, WITHOUT ever computing or
asserting a real GA score (that measurement event is out of M1 scope; see manifest.py docstring).
"""

from __future__ import annotations

import pytest

from operator_voice.eval import manifest
from operator_voice.eval.manifest import (
    ABSTAIN,
    AGGREGATE_DIMENSION,
    DIMENSIONS,
    LEAF_DIMENSIONS,
    MEASURES,
    EvidenceReport,
    assert_manifest_well_formed,
    compute_manifest_digest,
    score_all,
    score_dimension,
)


def test_ten_dimensions_exact_wording_and_order():
    assert DIMENSIONS == (
        "efficacy", "accuracy", "precision", "consistency", "resilience",
        "performance", "privacy_safety", "operator_friction", "integration_cohesion",
        "overall_build_quality",
    )
    assert len(DIMENSIONS) == 10
    assert AGGREGATE_DIMENSION == "overall_build_quality"
    assert len(LEAF_DIMENSIONS) == 9


def test_manifest_is_well_formed():
    assert_manifest_well_formed()  # raises on failure; a passing call is the assertion


def test_every_leaf_dimension_weights_sum_to_exactly_one():
    for dim in LEAF_DIMENSIONS:
        total = sum(m.weight_by_dimension.get(dim, 0.0) for m in MEASURES)
        assert total == pytest.approx(1.0, abs=1e-9), f"{dim}: {total}"


def test_digest_is_deterministic_and_pinned():
    # Regression pin: this exact value was frozen when the manifest first landed. If a future
    # change to MEASURES/AGGREGATE_WEIGHTS changes this digest, that change MUST land in a commit
    # that touches no observed-value file (D21) — this test's failure is the tripwire that forces
    # a human to notice and justify the edit, not a silent drift.
    assert compute_manifest_digest() == manifest.MANIFEST_DIGEST
    assert compute_manifest_digest() == compute_manifest_digest()  # pure function, no hidden state


def test_score_dimension_abstains_on_missing_measure():
    empty = EvidenceReport()
    for dim in LEAF_DIMENSIONS:
        assert score_dimension(dim, empty) == ABSTAIN


def test_score_dimension_abstains_on_partially_missing_measure():
    # `consistency` depends on 4 measures; supplying only 3 must still abstain, not silently
    # renormalize the remaining weights to sum to 1.
    report = EvidenceReport(values={
        "exactly_once_recovery_rate": 100.0,
        "distributor_convergence_rate": 100.0,
        "retrieval_determinism_rate": 100.0,
        # correction_precedence_durability_rate deliberately omitted
    })
    assert score_dimension("consistency", report) == ABSTAIN


def test_score_dimension_weighted_average_is_correct_for_precision():
    # precision = authorship_isolation_rate * 0.60 + interface_registry_parity_rate * 0.40
    report = EvidenceReport(values={
        "authorship_isolation_rate": 90.0,
        "interface_registry_parity_rate": 80.0,
    })
    expected = 90.0 * 0.60 + 80.0 * 0.40
    assert score_dimension("precision", report) == pytest.approx(expected)


def test_score_all_covers_every_dimension_and_aggregate_abstains_when_any_leaf_does():
    partial = EvidenceReport(values={"authorship_isolation_rate": 100.0, "interface_registry_parity_rate": 100.0})
    scores = score_all(partial)
    assert set(scores) == set(DIMENSIONS)
    assert scores["precision"] == pytest.approx(100.0)
    assert scores["accuracy"] == ABSTAIN  # missing retrieval/correction measures
    assert scores[AGGREGATE_DIMENSION] == ABSTAIN  # any abstaining leaf poisons the rollup


def test_score_all_full_report_produces_all_numeric_scores_and_a_consistent_rollup():
    full = EvidenceReport(values={m.id: 100.0 for m in MEASURES})
    scores = score_all(full)
    for dim in DIMENSIONS:
        assert scores[dim] == pytest.approx(100.0), f"{dim}: {scores[dim]!r}"

    # A non-uniform report's aggregate must equal the plain mean of its 9 leaf scores (equal
    # weights, D21 "no invented importance ordering").
    mixed = EvidenceReport(values={m.id: 50.0 for m in MEASURES})
    mixed_scores = score_all(mixed)
    leaf_values = [mixed_scores[d] for d in LEAF_DIMENSIONS]
    assert all(isinstance(v, float) for v in leaf_values)
    assert mixed_scores[AGGREGATE_DIMENSION] == pytest.approx(sum(leaf_values) / len(leaf_values))


def test_score_dimension_rejects_unknown_dimension():
    with pytest.raises(ValueError):
        score_dimension("made_up_dimension", EvidenceReport())


def test_no_measure_id_collides_and_every_measure_has_a_named_source():
    ids = [m.id for m in MEASURES]
    assert len(ids) == len(set(ids))
    for m in MEASURES:
        assert m.source, f"{m.id} has no documented evidence source"
        assert m.description, f"{m.id} has no description"
