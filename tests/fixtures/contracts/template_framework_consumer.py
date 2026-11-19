"""Hermetic stand-in for the Quickhost Template Framework's authoring step (build plan §6.3, D16).

This module is DELIBERATELY standalone: it imports ONLY the Python standard library and consumes a
``voice-context-bundle/v1`` payload as plain JSON. It never imports ``operator_voice`` internals,
never writes OVT state, and never asks OVT to enter template routing/storage/rendering — the frozen
bundle is the ONLY shared artifact. The contract test asserts this file's source contains no
``operator_voice`` reference, proving the consumer needs nothing but the frozen wire contract.
"""

from __future__ import annotations

import json
from typing import Any

ACCEPTED_MAJORS = ("v1",)


def _major(schema: str) -> str:
    return schema.rsplit("/", 1)[1].split(".", 1)[0]


def load_bundle(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_authoring_context(bundle: dict[str, Any]) -> dict[str, Any]:
    """Extract exactly what a template author needs from the bundle, using only the public contract.
    Raises ValueError on an unacceptable major version or an abstained/empty bundle — the consumer
    fails closed rather than authoring from nothing."""
    schema = bundle.get("schema", "")
    if not schema.startswith("voice-context-bundle/"):
        raise ValueError(f"not a voice-context-bundle: {schema!r}")
    if _major(schema) not in ACCEPTED_MAJORS:
        raise ValueError(f"consumer accepts {ACCEPTED_MAJORS}, got {schema!r}")
    if bundle.get("abstained"):
        raise ValueError("bundle abstained; author should ask for more input, not guess")

    tone = None
    sentence_target = None
    banned: list[str] = []
    for fact in bundle.get("ranked_facts", []):
        if fact["facet"] == "register_tone":
            tone = fact["value"]
        elif fact["facet"] == "sentence_length":
            sentence_target = fact["value"]
        elif fact["facet"] == "banned_phrase" and fact["state"] == "CORRECTED":
            banned.append(str(fact["value"]))

    instructions = bundle.get("instructions", {})
    return {
        "consumer_id": bundle["consumer_id"],
        "tone": tone,
        "sentence_length_target": sentence_target,
        "banned_phrases": banned,
        "generative_instruction": instructions.get("generative", ""),
        "corrective_instruction": instructions.get("corrective", ""),
        "warnings": list(bundle.get("warnings", [])),
        "confidence": bundle.get("confidence"),
    }
