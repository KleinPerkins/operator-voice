"""M2.2 band tests — build plan §13 worklist 2 ("replace uncited labels with calibrated/explicitly
heuristic bands") and audit take-table (uncited vendor thresholds must be labeled heuristic, not
settled fact).
"""

from __future__ import annotations

import pytest

from operator_voice.kernel import bands


def test_default_bands_are_explicitly_heuristic():
    # Every default scale MUST declare HEURISTIC provenance (nothing presented as settled fact).
    for facet, scale in bands.HEURISTIC_SCALES.items():
        assert scale.provenance is bands.BandProvenance.HEURISTIC
        band = scale.classify(scale.thresholds[0][0])
        assert band.provenance is bands.BandProvenance.HEURISTIC
        assert "heuristic" in band.basis.lower()


def test_specificity_ladder_matches_audited_shape_but_is_heuristic():
    # >0.4/>0.25/>0.15 shape retained, but demoted to heuristic (audit specificity-patterns row).
    assert bands.classify("specificity_ratio", 0.5).label == "high"
    assert bands.classify("specificity_ratio", 0.30).label == "medium"
    assert bands.classify("specificity_ratio", 0.18).label == "low"
    assert bands.classify("specificity_ratio", 0.05).label == "generic"
    assert bands.classify("specificity_ratio", 0.5).provenance is bands.BandProvenance.HEURISTIC


def test_classify_prefers_calibrated_scale_when_supplied():
    calibrated = {
        "lexical_entropy": bands.BandScale(
            facet="lexical_entropy",
            provenance=bands.BandProvenance.CALIBRATED,
            thresholds=((0.9, "high"), (0.6, "medium")),
            basis="calibration-manifest/v2 rev abc123",
        )
    }
    band = bands.classify("lexical_entropy", 0.95, calibrated)
    assert band.provenance is bands.BandProvenance.CALIBRATED
    assert band.label == "high"
    assert "calibration-manifest" in band.basis


def test_unknown_facet_raises_never_silently_misbands():
    with pytest.raises(KeyError):
        bands.classify("not_a_signal", 1.0)


def test_bandscale_rejects_non_descending_thresholds():
    with pytest.raises(ValueError):
        bands.BandScale(
            facet="x", provenance=bands.BandProvenance.HEURISTIC,
            thresholds=((0.1, "low"), (0.5, "high")), basis="bad",
        )


def test_classify_floor_label_when_below_all_thresholds():
    assert bands.classify("argument_flow_ratio", 0.0).label == "unstructured"
