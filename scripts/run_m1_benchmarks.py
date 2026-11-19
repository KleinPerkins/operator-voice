#!/usr/bin/env python3
"""Run the real M1 performance benchmark (build plan §7's exact small=10/medium=100/large=1000
synthetic corpora) once, on the target Mac, and write the observed-baseline-plus-margin
regression budget to docs/generated/m1-benchmark-baseline.json.

Usage:  python3 scripts/run_m1_benchmarks.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from operator_voice.eval.benchmarks import run_benchmark_suite, write_baseline_file  # noqa: E402


def main() -> None:
    out_path = REPO_ROOT / "docs" / "generated" / "m1-benchmark-baseline.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ovt-m1-benchmark-") as tmp:
        print("running M1 benchmark suite (small=10, medium=100, large=1000)...")
        report = run_benchmark_suite(Path(tmp), sizes=(10, 100, 1000))
        for r in report.results:
            print(
                f"  size={r.size:5d}  ingest p50/p95={r.ingest.p50_ms:.1f}/{r.ingest.p95_ms:.1f}ms "
                f"rebuild p50/p95={r.rebuild.p50_ms:.1f}/{r.rebuild.p95_ms:.1f}ms "
                f"context_get p50/p95={r.context_get.p50_ms:.1f}/{r.context_get.p95_ms:.1f}ms "
                f"peak_mem={r.peak_memory_bytes / 1e6:.2f}MB db={r.db_bytes / 1e3:.1f}KB tokens={r.context_tokens}"
            )
        write_baseline_file(out_path, report, margin=0.5)
        print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
