"""Benchmark-harness correctness — build plan §7 / §13 M1 worklist item 9. Uses tiny synthetic
sizes and low trial counts so this runs fast as part of the normal suite; the real M1 baseline
(small=10/medium=100/large=1000, §7's exact sizes) is produced once, out of band, by
scripts/run_m1_benchmarks.py and committed to docs/generated/m1-benchmark-baseline.json.
"""

from __future__ import annotations

import json

from operator_voice.eval.benchmarks import (
    pin_baseline,
    run_benchmark_suite,
    synthetic_corpus,
    write_baseline_file,
)


def test_synthetic_corpus_is_deterministic_and_size_correct():
    a = synthetic_corpus(5, seed=1)
    b = synthetic_corpus(5, seed=1)
    assert a == b
    assert len(a) == 5
    assert len(set(a)) == 5  # no accidental duplicate documents


def test_synthetic_corpus_different_seeds_or_sizes_are_disjoint_namespaces():
    a = synthetic_corpus(3, seed=1)
    b = synthetic_corpus(3, seed=2)
    assert set(a).isdisjoint(set(b))


def test_run_benchmark_suite_tiny(tmp_path):
    report = run_benchmark_suite(tmp_path, sizes=(2, 4), trials_by_size={2: 2, 4: 2})
    assert len(report.results) == 2
    for r in report.results:
        assert r.ingest.trials == 2
        assert r.ingest.p50_ms >= 0.0
        assert r.rebuild.p50_ms >= 0.0
        assert r.context_get.p50_ms >= 0.0
        assert r.db_bytes > 0
        assert r.peak_memory_bytes > 0


def test_pin_baseline_applies_margin(tmp_path):
    report = run_benchmark_suite(tmp_path, sizes=(2,), trials_by_size={2: 2})
    budget = pin_baseline(report, margin=1.0)
    observed = report.results[0].ingest.p50_ms
    assert budget["by_size"]["2"]["ingest_p50_ms"] == observed * 2.0


def test_write_and_load_baseline_file_round_trip(tmp_path):
    report = run_benchmark_suite(tmp_path / "run", sizes=(2,), trials_by_size={2: 2})
    out = tmp_path / "baseline.json"
    write_baseline_file(out, report)
    loaded = json.loads(out.read_text())
    assert loaded["observed"]["benchmark_version"] == report.benchmark_version
    assert "pinned_budget" in loaded
    assert loaded["pinned_budget"]["by_size"]["2"]["ingest_p50_ms"] > 0
