"""Pure evidence-measure collector arithmetic — no filesystem, no test-suite introspection."""

from __future__ import annotations

import pytest

from operator_voice.eval.metrics import (
    BudgetAdherence,
    benchmark_budget_adherence_rate,
    build_evidence_report,
    clean_rate,
    rate,
)


def test_rate_basic():
    assert rate(7, 10) == pytest.approx(70.0)
    assert rate(0, 5) == pytest.approx(0.0)
    assert rate(5, 5) == pytest.approx(100.0)


def test_rate_rejects_empty_total_rather_than_returning_zero_or_hundred():
    with pytest.raises(ZeroDivisionError):
        rate(0, 0)


def test_rate_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        rate(-1, 5)
    with pytest.raises(ValueError):
        rate(6, 5)


def test_clean_rate_empty_findings_is_a_clean_hundred():
    assert clean_rate(0, 0) == pytest.approx(100.0)


def test_clean_rate_basic():
    assert clean_rate(1, 4) == pytest.approx(75.0)
    assert clean_rate(0, 4) == pytest.approx(100.0)
    assert clean_rate(4, 4) == pytest.approx(0.0)


def test_budget_adherence_lower_is_better():
    assert BudgetAdherence(observed=90.0, budget=100.0).met() is True
    assert BudgetAdherence(observed=110.0, budget=100.0).met() is False


def test_budget_adherence_higher_is_better():
    assert BudgetAdherence(observed=110.0, budget=100.0, lower_is_better=False).met() is True
    assert BudgetAdherence(observed=90.0, budget=100.0, lower_is_better=False).met() is False


def test_benchmark_budget_adherence_rate():
    adherences = [
        BudgetAdherence(observed=10, budget=20),  # met
        BudgetAdherence(observed=30, budget=20),  # missed
        BudgetAdherence(observed=5, budget=20),   # met
        BudgetAdherence(observed=5, budget=20),   # met
    ]
    assert benchmark_budget_adherence_rate(adherences) == pytest.approx(75.0)


def test_benchmark_budget_adherence_rate_rejects_empty_input():
    with pytest.raises(ValueError):
        benchmark_budget_adherence_rate([])


def test_build_evidence_report_rejects_unknown_measure_id():
    with pytest.raises(ValueError):
        build_evidence_report(not_a_real_measure=100.0)


def test_build_evidence_report_rejects_out_of_range_value():
    with pytest.raises(ValueError):
        build_evidence_report(authorship_isolation_rate=101.0)
    with pytest.raises(ValueError):
        build_evidence_report(authorship_isolation_rate=-1.0)


def test_build_evidence_report_accepts_known_measures():
    report = build_evidence_report(authorship_isolation_rate=95.0)
    assert report.get("authorship_isolation_rate") == pytest.approx(95.0)
    assert report.get("nonexistent") is None
