#!/usr/bin/env python3
"""Dependency-free runnerless protection for operator-voice privacy invariants."""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from operator_voice.domain.models import AuthorshipState, ObservationCandidate
from operator_voice.kernel.authorship import classify_role, split_authored_segments
from operator_voice.kernel.external_analysis import classify_purpose, is_denied_facet
from operator_voice.kernel.normalize import normalize_text

KERNEL = ROOT / "src" / "operator_voice" / "kernel"
FORBIDDEN_INTERNAL = (
    "operator_voice.application",
    "operator_voice.adapters",
    "operator_voice.interfaces",
    "operator_voice.persistence",
    "operator_voice.runtime",
)
FORBIDDEN_NETWORK = {
    "aiohttp",
    "anthropic",
    "asyncio",
    "boto3",
    "ftplib",
    "grpc",
    "http",
    "http.client",
    "httpx",
    "openai",
    "requests",
    "smtplib",
    "socket",
    "ssl",
    "urllib",
    "urllib.request",
}


def imported_names(source: str, filename: str = "<fixture>") -> set[str]:
    tree = ast.parse(source, filename=filename)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imports.add(node.module)
                if node.module == "operator_voice":
                    imports.update(f"operator_voice.{alias.name}" for alias in node.names)
            else:
                base = "operator_voice.kernel" if node.level == 1 else "operator_voice"
                if node.module:
                    imports.add(f"{base}.{node.module}")
                else:
                    imports.update(f"{base}.{alias.name}" for alias in node.names)
    return imports


def forbidden_imports(imports: set[str]) -> list[str]:
    return sorted(
        name
        for name in imports
        if any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_NETWORK)
        or name.endswith(".gateway")
        or any(name == prefix or name.startswith(prefix + ".") for prefix in FORBIDDEN_INTERNAL)
    )


class RunnerlessPrivacyContract(unittest.TestCase):
    def test_kernel_is_dependency_and_network_free(self) -> None:
        modules = sorted(KERNEL.glob("*.py"))
        self.assertTrue(modules)
        offenders: dict[str, list[str]] = {}
        for path in modules:
            imports = imported_names(path.read_text(encoding="utf-8"), str(path))
            bad = forbidden_imports(imports)
            if bad:
                offenders[path.name] = bad
        self.assertEqual(offenders, {}, f"kernel purity violated: {offenders}")

    def test_kernel_purity_guard_bites_on_relative_and_dotted_imports(self) -> None:
        fixture = """
from .. import persistence
from ..application import service
from operator_voice import runtime
from urllib import parse
import http.server
"""
        self.assertEqual(
            forbidden_imports(imported_names(fixture)),
            [
                "http.server",
                "operator_voice.application",
                "operator_voice.persistence",
                "operator_voice.runtime",
                "urllib",
            ],
        )

    def test_quoted_and_forwarded_text_contributes_no_subject_segment(self) -> None:
        smuggled = "Eve wrote a very long third-party sentence that must never become operator evidence."
        for text in (
            f"Yes. Agreed.\n\n-----Original Message-----\n{smuggled}",
            f"Yes. Agreed.\n\nOn Jan 1, 2020, Eve wrote:\n{smuggled}",
            f"Yes. Agreed.\n\n> {smuggled}",
        ):
            segments = split_authored_segments(text, AuthorshipState.SUBJECT)
            subject_text = "\n".join(s.text for s in segments if s.state is AuthorshipState.SUBJECT)
            self.assertNotIn(smuggled, subject_text)
            self.assertIn("Yes. Agreed.", subject_text)

    def test_role_classification_fails_closed(self) -> None:
        def candidate(role: str) -> ObservationCandidate:
            return ObservationCandidate(
                candidate_id="c",
                claimed_subject_id="s",
                source_id="source",
                source_native_id="native",
                role=role,
                raw_text="text",
                digest="digest",
                locator="fixture",
            )

        self.assertIs(classify_role(candidate("assistant"), "user")[0], AuthorshipState.AI)
        self.assertIs(
            classify_role(candidate("mystery_impersonator"), "user")[0],
            AuthorshipState.AMBIGUOUS_QUARANTINED,
        )

    def test_external_subject_requests_fail_closed(self) -> None:
        denied = {
            "write as this person": "generation_or_impersonation",
            "predict their next move": "future_decision_forecast",
            "infer their mental health": "protected_or_sensitive_trait",
            "should we hire them": "high_impact_decision_support",
        }
        for purpose, category in denied.items():
            verdict = classify_purpose(purpose)
            self.assertFalse(verdict.allowed)
            self.assertEqual(verdict.category, category)
        self.assertTrue(classify_purpose("summarize historical communication patterns").allowed)

    def test_external_sensitive_facets_are_denied(self) -> None:
        for facet in ("political_lean", "mental_state", "sensitive:any", "trait:openness"):
            self.assertTrue(is_denied_facet(facet), facet)
        for facet in ("sentence_length", "greeting_style", "emoji_rate"):
            self.assertFalse(is_denied_facet(facet), facet)

    def test_normalization_is_deterministic_and_excludes_non_voice_content(self) -> None:
        raw = "Hello.  \n```python\nprint('not voice')\n```\n-- \nSignature"
        first = normalize_text(raw)
        self.assertEqual(first, normalize_text(raw))
        self.assertEqual(first.text, "Hello.")
        self.assertEqual(len(first.code_blocks), 1)


if __name__ == "__main__":
    result = unittest.main(exit=False, verbosity=2)
    if not result.result.wasSuccessful():
        raise SystemExit(1)
    print("PR_AUTOMATION_VERIFY_OK")
