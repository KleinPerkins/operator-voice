"""M2.5 semantic/factual validation — build plan §13 M2 worklist 5. Kernel-pure half: deterministic
required/forbidden-fact checks (injected-fact block) and the semantic-verdict parser used by the
application upgrade path. Synthetic drafts only."""

from __future__ import annotations

from operator_voice.kernel import validation


def test_forbidden_fact_present_fails_closed():
    clean = validation.check_forbidden_facts("We will refund 100% within 3 days.", ["guarantee", "lifetime warranty"])
    assert clean.passed is True
    injected = validation.check_forbidden_facts("Our product is fda approved.", ["FDA approved"])
    assert injected.passed is False  # case-insensitive substring match
    assert "FDA approved" in injected.detail


def test_required_explicit_fact_missing_fails():
    check = validation.check_required_facts("A short draft.", source_brief="", required_facts=["Q3 launch"])
    assert check.passed is False
    present = validation.check_required_facts("We confirm the Q3 launch.", source_brief="", required_facts=["Q3 launch"])
    assert present.passed is True


def test_parse_semantic_verdict_reads_tokens_deterministically():
    assert validation.parse_semantic_verdict("...FIDELITY_OK...") is True
    assert validation.parse_semantic_verdict("verdict: MATERIAL_CHANGE") is False
    assert validation.parse_semantic_verdict("some unrelated prose") is None
    assert validation.parse_semantic_verdict(None) is None
    # material change wins if both somehow appear (fail-safe)
    assert validation.parse_semantic_verdict("FIDELITY_OK but also MATERIAL_CHANGE") is False


def test_build_semantic_signal_contains_only_draft_and_brief_never_raw_evidence():
    signal = validation.build_semantic_signal(
        draft_text="my draft line one\nline two", source_brief="the brief",
        required_facts=["Q3"], forbidden_facts=["FDA approved"],
    )
    assert "my draft line one" in signal
    assert "Q3" in signal and "FDA approved" in signal
    # newlines are escaped (single-line envelope segments), no raw multi-line injection
    assert "line one\\nline two" in signal


def test_validate_draft_blocks_injected_fact_as_unvalidated():
    from operator_voice.domain.models import ValidationOutcome

    run = validation.validate_draft(
        subject_id="s", draft_text="Our product is FDA approved and ships Monday.",
        source_brief="", facts=[], forbidden_facts=["FDA approved"],
    )
    assert run.outcome == ValidationOutcome.UNVALIDATED
    assert any(c["name"] == "forbidden_facts" and not c["passed"] for c in run.checks)
