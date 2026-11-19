"""Coverage for docs generation, the resource registry, and the skill installer — build plan v3
§6 ("generated operation/schema docs, core guidance/status resources") + §6 ("thin conversational
veneer ... does not duplicate business logic or manipulate profile files")."""

from __future__ import annotations

from pathlib import Path

import pytest

from operator_voice.docs.generate import generate_all, render_index, render_intents
from operator_voice.docs.resources import ALL_RESOURCES, M1_RESOURCE_URIS, read_resource
from operator_voice.domain.operations import OPERATIONS
from operator_voice.interfaces.skill import install_skill, skill_markdown, uninstall_skill


def test_generate_all_is_deterministic(tmp_path: Path):
    out1, out2 = tmp_path / "a", tmp_path / "b"
    assert generate_all(out1) == generate_all(out2)


def test_generate_all_covers_every_operation(tmp_path: Path):
    files = generate_all(tmp_path)
    for op in OPERATIONS:
        assert f"cards/{op.cli_name}.md" in files
        assert op.name in files["INDEX.md"]


def test_render_index_never_hand_edited_marker_present():
    assert "GENERATED" in render_index()


def test_render_intents_lists_every_alias():
    from operator_voice.interfaces.intents import ALIASES

    text = render_intents()
    for name in ALIASES:
        assert name in text


def test_no_static_resource_body_contains_a_filesystem_path():
    # Every static guidance body (M1 + M2, now all active) must never leak an absolute path.
    for spec in ALL_RESOURCES:
        if not spec.dynamic:
            body = read_resource(spec.uri, service=None)
            assert "/Users/" not in body
            assert "/home/" not in body


def test_every_active_static_resource_is_readable():
    from operator_voice.docs.resources import ACTIVE_RESOURCE_URIS

    for spec in ALL_RESOURCES:
        if not spec.dynamic:
            assert spec.uri in ACTIVE_RESOURCE_URIS
            assert read_resource(spec.uri, service=None)  # no KeyError, non-empty


def test_read_resource_rejects_unknown_uri():
    with pytest.raises(KeyError):
        read_resource("ovt://not-a-real-resource", service=None)


def test_m1_resource_uris_are_all_registered():
    for uri in M1_RESOURCE_URIS:
        assert uri in {r.uri for r in ALL_RESOURCES}


def test_generated_docs_match_committed(tmp_path: Path):
    """Registry -> generated-docs parity (§13 M2 acceptance: 'generated MCP docs/resources/prompts
    match the registry'). Regenerate into a tmp dir and require byte-identical committed copies."""
    repo_root = Path(__file__).resolve().parents[2]
    committed = repo_root / "docs" / "generated"
    files = generate_all(tmp_path)
    for rel, content in files.items():
        path = committed / rel
        assert path.exists(), f"missing committed generated doc: {rel} (run `python -m operator_voice.docs.generate docs/generated`)"
        assert path.read_text(encoding="utf-8") == content, f"stale committed doc {rel}: regenerate docs/generated"


def test_prompts_registry_renders_and_enforces_required_args():
    from operator_voice.docs.prompts import PROMPTS, get_prompt, render_prompt_messages

    assert PROMPTS  # M2 populated the registry
    spec = get_prompt("voice-matched-draft")
    with pytest.raises(KeyError):
        spec.render({})  # missing required subject_id/consumer_id/task
    messages = render_prompt_messages("voice-matched-draft", {
        "subject_id": "subj_x", "consumer_id": "demo", "task": "write a note",
    })
    assert messages[0]["role"] == "user"
    assert "subj_x" in messages[0]["content"] and "demo" in messages[0]["content"]


def test_prompt_templates_never_embed_paths_or_secrets():
    from operator_voice.docs.prompts import PROMPTS

    for spec in PROMPTS:
        assert "/Users/" not in spec.template and "/home/" not in spec.template
        # unfilled optional placeholders render empty, never a path
        rendered = spec.template.format(**{a.name: "" for a in spec.arguments})
        assert "/Users/" not in rendered


def test_skill_markdown_has_frontmatter_and_no_operator_data():
    text = skill_markdown()
    assert text.startswith("---\n")
    assert "name: operator-voice" in text
    assert "/Users/" not in text


def test_skill_install_is_idempotent_and_uninstall_reverses_it(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    first = install_skill(home=home, agents=("claude",))
    assert first == {"claude": True}
    second = install_skill(home=home, agents=("claude",))
    assert second == {"claude": False}
    removed = uninstall_skill(home=home, agents=("claude",))
    assert removed == {"claude": True}
    assert not (home / ".claude" / "skills" / "operator-voice").exists()


def test_skill_install_skips_absent_agent_root(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    result = install_skill(home=home, agents=("codex",))
    assert result == {}


def test_shared_agent_copy_cannot_keep_superseded_policy(tmp_path: Path):
    shared = tmp_path / ".agents" / "skills" / "operator-voice" / "SKILL.md"
    shared.parent.mkdir(parents=True)
    shared.write_text("Synthetic superseded hourly maintenance instructions.")
    install_skill(home=tmp_path)
    assert shared.read_text() == skill_markdown()
    assert install_skill(home=tmp_path)["agents"] is False
    uninstall_skill(home=tmp_path)
    assert not shared.exists()


def test_shared_agent_root_is_not_created_when_absent(tmp_path: Path):
    install_skill(home=tmp_path)
    assert not (tmp_path / ".agents").exists()
