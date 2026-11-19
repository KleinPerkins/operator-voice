"""M2.7 calibration tests — build plan §13 M2 worklist 7 / D2 acceptance ("confidence calibration
and abstention meet the preregistered manifest"; "measure whether direct feedback lowers future
correction rate"). Determinism/reproducibility, manifest conformance, EXTERNAL exclusion.
"""

from __future__ import annotations

import json

from operator_voice.eval import calibrate
from operator_voice.eval import calibration_manifest as cm


def test_report_meets_preregistered_manifest():
    report = calibrate.build_report()
    check = cm.assert_report_meets_manifest(report)
    assert check.ok, check.failures


def test_report_is_deterministic():
    a = calibrate.build_report()
    b = calibrate.build_report()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_committed_report_matches_fresh_run():
    path = calibrate.default_report_path()
    assert path.exists(), "eval/calibration_report.json missing — run scripts/calibrate.py"
    committed = path.read_text(encoding="utf-8")
    fresh = json.dumps(calibrate.build_report(), indent=2, sort_keys=True) + "\n"
    assert committed == fresh, "calibration report is stale; re-run scripts/calibrate.py"


def test_stable_corpora_promote_and_noisy_abstains():
    report = calibrate.build_report()
    assert set(report["promotion"]["stable"].values()) == {"SUPPORTED"}
    assert set(report["promotion"]["noisy"].values()) == {"OBSERVED"}


def test_feedback_lowers_correction_rate_to_zero():
    fb = calibrate.measure_feedback_effect()
    assert fb["post_feedback_correction_rate"] < fb["pre_feedback_correction_rate"]
    assert fb["post_feedback_correction_rate"] == cm.FEEDBACK_EFFECT_METRIC.required_post_feedback_rate


def test_external_documents_never_contribute_to_calibration():
    report = calibrate.build_report()
    assert report["external_documents_contributing"] == 0
    assert report["external_documents_seen"] > 0  # the EXTERNAL corpus was present but excluded
    # No EXTERNAL corpus id appears in any promotion bucket.
    ids = set(report["promotion"]["stable"]) | set(report["promotion"]["noisy"])
    assert "external_reference_only" not in ids


def test_calibrated_bands_are_labeled_calibrated_and_loadable():
    reference, scales = calibrate.load_calibrated_artifacts()
    assert reference, "expected a derived function-word reference"
    assert scales, "expected at least one calibrated band scale"
    for facet, scale in scales.items():
        assert scale.provenance.value == "calibrated"
        # A calibrated scale classifies without raising and reports calibrated provenance.
        band = scale.classify(scale.thresholds[0][0])
        assert band.provenance.value == "calibrated"


def test_manifest_digest_is_stable():
    # The preregistered digest must be reproducible from the frozen manifest content.
    assert cm.compute_manifest_digest() == cm.CALIBRATION_MANIFEST_DIGEST
