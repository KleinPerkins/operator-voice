"""ADR 0005: additive v1 fields. The frozen contract stays untouched."""
import json
from pathlib import Path

from operator_voice.domain.contracts import voice_context_bundle_from_dict, voice_context_bundle_to_dict

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "voice-context-bundle.v1.json"


def test_document_count_is_optional_and_round_trips():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    plain = voice_context_bundle_to_dict(voice_context_bundle_from_dict(payload))
    assert all("document_count" not in fact for fact in plain["ranked_facts"])
    payload["ranked_facts"][0]["document_count"] = 72
    again = voice_context_bundle_to_dict(voice_context_bundle_from_dict(payload))
    assert again["ranked_facts"][0]["document_count"] == 72
    assert again["schema"] == "voice-context-bundle/v1"
