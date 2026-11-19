#!/usr/bin/env python3
"""Run the M2 calibration harness against the synthetic fixture corpora and write the
evidence-grounded, reproducible report to eval/calibration_report.json (build plan §13 M2.7 / D2).

The report's derived constants (function-word reference + calibrated band scales) are the evidence
that :mod:`operator_voice.kernel.calibration`'s frozen promotion thresholds and the runtime
calibrated bands are pinned to. Re-running this script on the same corpora is deterministic and
must produce a byte-identical report (asserted by tests/eval/test_calibration.py).

Usage:  python3 scripts/calibrate.py [--check]
  (no args)  regenerate eval/calibration_report.json
  --check    build the report in memory and verify it matches the committed file AND the
             preregistered manifest, exiting non-zero on any drift (no write).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from operator_voice.eval import calibrate  # noqa: E402
from operator_voice.eval import calibration_manifest as cm  # noqa: E402


def main() -> int:
    check = "--check" in sys.argv[1:]
    report = calibrate.build_report()

    manifest_check = cm.assert_report_meets_manifest(report)
    if not manifest_check.ok:
        print("calibration report FAILS preregistered manifest:")
        for f in manifest_check.failures:
            print(f"  - {f}")
        return 2

    report_path = calibrate.default_report_path()
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"

    if check:
        if not report_path.exists():
            print(f"missing {report_path} (run without --check to generate)")
            return 1
        existing = report_path.read_text(encoding="utf-8")
        if existing != serialized:
            print(f"{report_path} is stale; re-run scripts/calibrate.py")
            return 1
        print(f"calibration report OK (meets manifest, matches committed file): {report_path}")
        return 0

    calibrate.write_report(report, report_path)
    fb = report["feedback_effect"]
    print(f"wrote {report_path}")
    print(f"  promotion: {report['promotion']}")
    print(f"  calibrated bands: {sorted(report['calibrated_bands'])}")
    print(f"  function-word reference facets: {len(report['function_word_reference'])}")
    print(
        f"  feedback effect: pre={fb['pre_feedback_correction_rate']} "
        f"post={fb['post_feedback_correction_rate']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
