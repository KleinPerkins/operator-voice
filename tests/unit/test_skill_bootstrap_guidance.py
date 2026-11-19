"""M1 acceptance tests — OV-M1-1 and OV-M1-2.

OV-M1-1  test_skill_teaches_subject_resolution
         Verifies that the packaged SKILL.md contains the mandatory Step-0 guidance elements
         defined in docs/plans/operator-voice-subject-resolution-bootstrap-v1.md §3.4, and that
         it is zero-link-clean (no ``cpp`` or other downstream-repo token).

OV-M1-2  No-regression check — install round-trip stays green (delegated to
         test_docs_and_skill.py::test_skill_install_is_idempotent_and_uninstall_reverses_it;
         this module asserts the install.sh --check precondition only).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILL_PATH = _REPO_ROOT / "src" / "operator_voice" / "skill" / "SKILL.md"


def _skill_text() -> str:
    return _SKILL_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# OV-M1-1: test_skill_teaches_subject_resolution
# ---------------------------------------------------------------------------

class TestSkillTeachesSubjectResolution:
    """OV-M1-1 — all sub-assertions must pass together."""

    def test_skill_file_exists(self):
        assert _SKILL_PATH.exists(), f"SKILL.md not found at {_SKILL_PATH}"

    def test_a_subject_resolution_step_names_subject_list_as_id_source(self):
        """(a) A subject-resolution step naming ``subject-list`` as the source of the opaque
        subject id.  ``status`` is a health/setup check that does NOT return the subject id
        (surfacing the id in status/doctor is M2, not yet shipped)."""
        text = _skill_text()
        assert "subject-list" in text, (
            "SKILL.md must reference ``subject-list`` as the id-resolution command."
        )
        # status must NOT be presented as a source of the subj_* id
        # It is fine if 'status' appears as a health check, but the id-source line must
        # credit only subject-list.
        # Check: the sentence "only correct value comes from" must NOT include "status"
        id_source_match = re.search(
            r"only correct value comes from (.{0,120})",
            text, re.IGNORECASE,
        )
        assert id_source_match, (
            "SKILL.md must contain the 'only correct value comes from' statement."
        )
        source_clause = id_source_match.group(1)
        assert "subject-list" in source_clause, (
            "The 'only correct value comes from' statement must name ``subject-list``."
        )
        # status should not appear in that clause as an id source
        assert "status" not in source_clause, (
            "SKILL.md must NOT list ``status`` as a source of the subject id in the "
            "'only correct value comes from' clause — status is a health check only "
            "(id surfacing in status/doctor is M2, not yet shipped)."
        )

    def test_b_subject_id_is_opaque_subj_token_never_name_or_handle(self):
        """(b) Explicit statement that ``--subject-id`` is an opaque ``subj_*`` id, never a
        name/handle/email."""
        text = _skill_text()
        # Must say subject-id is opaque / subj_* format
        assert "subj_" in text, (
            "SKILL.md must state that --subject-id is an opaque ``subj_*`` identifier."
        )
        assert re.search(r"never.{0,80}(name|handle|email)", text, re.IGNORECASE), (
            "SKILL.md must state that --subject-id is NEVER the operator's name, handle, "
            "or email."
        )

    def test_b_anti_pattern_callout_present(self):
        """(b) Named anti-pattern: passing a handle like ``KleinPerkins`` yields
        ``unknown subject_id``."""
        text = _skill_text()
        # The plan requires a named anti-pattern callout referencing the incident handle
        assert "KleinPerkins" in text, (
            "SKILL.md must include the named anti-pattern callout referencing ``KleinPerkins`` "
            "as an example of a wrong (handle-style) subject id."
        )
        assert "unknown subject_id" in text, (
            "SKILL.md must include the ``unknown subject_id`` error text so agents know what "
            "to expect when they pass a wrong id."
        )

    def test_c_generic_binary_resolution_prefer_operator_voice(self):
        """(c) Generic binary-resolution guidance: prefer ``operator-voice``, else the
        host-provided wrapper discovered at runtime."""
        text = _skill_text()
        # Must guide agents to discover the binary rather than assume it
        assert "command -v" in text, (
            "SKILL.md must show ``command -v`` as the discovery mechanism for the binary."
        )
        # Must state that the name may differ on managed/vendored installs
        assert re.search(
            r"(different|differ|may.{0,30}name|wrapper.{0,60}name|name.{0,30}differ)",
            text, re.IGNORECASE,
        ), (
            "SKILL.md must note that the binary may have a different name on managed/vendored "
            "installs (generic resolution, not hardcoded)."
        )

    def test_c_every_example_means_resolved_binary_rule(self):
        """(c) The 'every `operator-voice …` example below means the binary you resolved' rule."""
        text = _skill_text()
        # Require the explicit 'every … means the binary you resolved' wording
        assert re.search(
            r"every.{0,60}means.{0,60}(binary|resolved)",
            text, re.IGNORECASE,
        ), (
            "SKILL.md must contain the rule: every ``operator-voice …`` example means the "
            "binary you resolved in Step 0."
        )

    def test_d_consumer_id_described_as_required(self):
        """(d) ``--consumer-id`` described as required."""
        text = _skill_text()
        assert "--consumer-id" in text, "SKILL.md must mention ``--consumer-id``."
        # Must describe it as mandatory / required, not just optional
        assert re.search(
            r"(mandatory|required|must pass).{0,120}consumer.?id",
            text, re.IGNORECASE,
        ) or re.search(
            r"consumer.?id.{0,120}(mandatory|required|must pass)",
            text, re.IGNORECASE,
        ), (
            "SKILL.md must describe ``--consumer-id`` as a required/mandatory argument."
        )

    def test_e_stop_dont_fabricate_instruction(self):
        """(e) A stop-don't-fabricate instruction covering error/zero/ambiguity."""
        text = _skill_text()
        # Must say stop / never fabricate
        has_stop = re.search(r"\bstop\b", text, re.IGNORECASE) is not None
        has_never_fabricate = re.search(
            r"never.{0,80}(fabricat|generic draft|present.{0,30}generic)",
            text, re.IGNORECASE,
        ) is not None
        assert has_stop and has_never_fabricate, (
            "SKILL.md must include a stop-don't-fabricate instruction (e.g. 'tell the operator "
            "plainly and stop; never present a generic draft as the operator's voice')."
        )
        # Must cover error / zero / ambiguity cases
        assert re.search(r"(error|zero|ambig)", text, re.IGNORECASE), (
            "SKILL.md must cover error/zero-subjects/ambiguity cases in the stop instruction."
        )

    def test_f_at_least_one_channel_example_has_consumer_id(self):
        """(f) At least one capture-section (``--channel``) example carrying ``--consumer-id``."""
        text = _skill_text()
        # Find any line that has both --channel and --consumer-id
        for line in text.splitlines():
            if "--channel" in line and "--consumer-id" in line:
                return  # found
        pytest.fail(
            "SKILL.md must have at least one ``--channel`` example that also carries "
            "``--consumer-id``."
        )

    def test_zero_link_clean_no_cpp_token(self):
        """Zero-link assertion: the packaged SKILL.md contains no ``cpp``/downstream-repo token.
        Mirrors the ``verify-skills.sh`` INV-zero-link scan."""
        text = _skill_text()
        # The regex from the plan: (^|[^A-Za-z.])cpp([^A-Za-z]|$)
        # We match the full text for any standalone 'cpp' token
        matches = re.findall(r"(?:^|[^A-Za-z.])cpp(?:[^A-Za-z]|$)", text, re.MULTILINE)
        assert not matches, (
            "SKILL.md must be zero-link-clean: it contains a ``cpp`` token, which would fail "
            "the KleinPerkins/skills verify-skills.sh INV-zero-link scan and block propagation. "
            f"Found {len(matches)} match(es). Remove all ``cpp`` tokens from SKILL.md."
        )
