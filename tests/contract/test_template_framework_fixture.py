"""M2.6 — hermetic Template Framework contract fixture (build plan §13 M2 worklist 5 + acceptance
"the Template Framework fixture consumes the bundle without importing OVT internals"; D16 "the only
shared artifact is VoiceContextBundle/v1; OVT has zero template awareness").

The consumer under test (`tests/fixtures/contracts/template_framework_consumer.py`) imports only the
stdlib. Here we (1) load it in isolation, (2) prove it extracts authoring guidance from the frozen
bundle, (3) prove its source references no OVT internals, and (4) prove OVT's own source has zero
template awareness — the dependency edge exists in exactly one direction, through the frozen wire
contract only.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "contracts"
CONSUMER_PATH = FIXTURE_DIR / "template_framework_consumer.py"
BUNDLE_PATH = FIXTURE_DIR / "voice-context-bundle.v1.json"


def _load_consumer():
    spec = importlib.util.spec_from_file_location("template_framework_consumer_isolated", CONSUMER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_consumer_extracts_authoring_context_from_frozen_bundle():
    consumer = _load_consumer()
    bundle = consumer.load_bundle(str(BUNDLE_PATH))
    ctx = consumer.build_authoring_context(bundle)
    assert ctx["consumer_id"] == "template-framework"
    assert ctx["tone"] == "warm-direct"
    assert ctx["sentence_length_target"] == 14.0
    assert "circle back" in ctx["banned_phrases"]
    assert ctx["generative_instruction"]  # non-empty compact instruction
    assert "context_relaxation:purpose" in ctx["warnings"]


def test_consumer_rejects_incompatible_major():
    consumer = _load_consumer()
    bundle = consumer.load_bundle(str(BUNDLE_PATH))
    bundle["schema"] = "voice-context-bundle/v2"
    with pytest.raises(ValueError):
        consumer.build_authoring_context(bundle)


def test_consumer_fails_closed_on_abstained_bundle():
    consumer = _load_consumer()
    bundle = consumer.load_bundle(str(BUNDLE_PATH))
    bundle["abstained"] = True
    with pytest.raises(ValueError):
        consumer.build_authoring_context(bundle)


def test_consumer_source_imports_no_ovt_internals():
    """The consumer needs only the frozen JSON contract — every import is stdlib, none is
    operator_voice. (The docstring may *name* operator_voice to explain the guarantee; what matters
    is that no import statement pulls it in.)"""
    source = CONSUMER_PATH.read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, flags=re.MULTILINE)
    assert imports  # sanity: we actually parsed some imports
    for mod in imports:
        top = mod.split(".", 1)[0]
        assert top != "operator_voice", "hermetic consumer must not import OVT internals"
        assert top in {"json", "typing", "__future__"}, f"non-stdlib import in hermetic consumer: {mod}"


def test_the_same_frozen_bundle_validates_under_the_ovt_contract():
    """The consumer parsed the bundle with pure JSON; here we independently confirm the SAME bytes
    satisfy OVT's own frozen v1 validator — the two sides agree on one artifact."""
    import json

    from operator_voice.domain.contracts import validate_voice_context_bundle

    payload = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    validate_voice_context_bundle(payload)  # no raise


def test_ovt_source_has_zero_template_framework_awareness():
    """D16 reverse edge: no OVT module imports template/quickhost code or reads template state."""
    src = REPO_ROOT / "src" / "operator_voice"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"^\s*(?:from|import)\s+(?:template|quickhost)\b", line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {line.strip()}")
    assert not offenders, f"OVT must have zero template awareness, found: {offenders}"
