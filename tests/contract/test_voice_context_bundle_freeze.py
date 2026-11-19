"""M2.6 — freeze VoiceContextBundle/v1 + consumer/version negotiation (build plan §5, §6.3, §13 M2
worklist 5). These tests PIN the public contract: the required-field set is frozen, additive-only
within v1; version negotiation accepts v1.x, tolerates extra fields, and rejects incompatible
majors and missing required fields. The golden fixture round-trips byte-stably."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operator_voice.domain.contracts import (
    VOICE_CONTEXT_BUNDLE_REQUIRED_V1,
    VOICE_CONTEXT_BUNDLE_VERSION,
    SchemaError,
    negotiate_bundle_version,
    validate_voice_context_bundle,
    voice_context_bundle_from_dict,
    voice_context_bundle_to_dict,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "voice-context-bundle.v1.json"

# The FROZEN v1 public contract. Changing this literal is a deliberate contract change and must be a
# documented v1.x addendum — never an accidental edit. (§5 frozen/additive within v1.)
_FROZEN_REQUIRED_V1 = {
    "schema", "bundle_id", "subject_id", "consumer_id", "generation_id", "request_scope",
    "ranked_facts", "instructions", "warnings", "provenance_refs", "budget_tokens",
    "actual_tokens", "confidence", "abstained",
}


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_required_field_set_is_frozen():
    assert set(VOICE_CONTEXT_BUNDLE_REQUIRED_V1) == _FROZEN_REQUIRED_V1


def test_golden_fixture_is_a_valid_v1_bundle():
    payload = _load()
    validate_voice_context_bundle(payload)  # no raise
    assert payload["schema"] == VOICE_CONTEXT_BUNDLE_VERSION


def test_golden_fixture_round_trips_stably():
    payload = _load()
    bundle = voice_context_bundle_from_dict(payload)
    reserialized = voice_context_bundle_to_dict(bundle)
    # every frozen required field survives the round trip with equal value
    for key in _FROZEN_REQUIRED_V1:
        assert reserialized[key] == payload[key], key


def test_missing_required_field_is_hard_reject():
    payload = _load()
    del payload["confidence"]
    with pytest.raises(SchemaError):
        validate_voice_context_bundle(payload)


def test_unknown_extra_field_is_tolerated_additive():
    payload = _load()
    payload["some_future_v1_3_field"] = {"anything": True}
    validate_voice_context_bundle(payload)  # no raise — additive/forward-compatible


def test_minor_version_v1_x_is_accepted():
    payload = _load()
    payload["schema"] = "voice-context-bundle/v1.3"
    validate_voice_context_bundle(payload)  # v1.x is compatible


def test_incompatible_major_v2_is_rejected():
    payload = _load()
    payload["schema"] = "voice-context-bundle/v2"
    with pytest.raises(SchemaError):
        validate_voice_context_bundle(payload)


@pytest.mark.parametrize("accepted", [None, [], ["v1"], ["1"], ["voice-context-bundle/v1"], ["v1.4"], ["v2", "v1"]])
def test_negotiation_accepts_v1_consumers(accepted):
    assert negotiate_bundle_version(accepted) == VOICE_CONTEXT_BUNDLE_VERSION


@pytest.mark.parametrize("accepted", [["v2"], ["v0"], ["v2", "v3"], ["subject-insight-bundle/v2"]])
def test_negotiation_rejects_incompatible_only_consumers(accepted):
    with pytest.raises(SchemaError):
        negotiate_bundle_version(accepted)
