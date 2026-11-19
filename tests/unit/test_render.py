"""Shared rendering and backend copy for M1 / ADR 0005."""
from operator_voice.interfaces.render import render_json
from operator_voice.application.service import backend_health_copy
from operator_voice.domain.models import BackendMode, ProfileBackendConfig


def test_context_get_renders_compact_and_other_operations_stay_readable():
    payload = {"schema": "voice-context-bundle/v1", "ranked_facts": [{"facet": "x"}]}
    assert render_json("context.get", payload) == '{"schema":"voice-context-bundle/v1","ranked_facts":[{"facet":"x"}]}'
    assert "\n" in render_json("status", payload)


def test_status_copy_mentions_the_encrypted_copy_only_when_one_exists():
    assert backend_health_copy(ProfileBackendConfig()) == "This profile stays on this computer"
    assert "encrypted copy syncs" in backend_health_copy(ProfileBackendConfig(mode=BackendMode.ENCRYPTED_GIT))
