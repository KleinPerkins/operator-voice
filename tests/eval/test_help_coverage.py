"""Locks the ``deterministic_help_coverage_rate`` and structural ``interface_registry_parity_rate``
measures at 100 by construction, and guards the per-operation help contract each depends on
(build plan v3 §6; eval-manifest measures of the same names). These tests fail loudly if a future
registry edit adds an operation without a worked example, a non-empty error taxonomy, or (for a
consequential operation) a challenge/consent description — so the M5 evidence pack's operator-
friction / efficacy inputs can never silently regress below the number the pack reports.
"""

from __future__ import annotations

from operator_voice.docs.generate import render_operation_card
from operator_voice.domain.help import (
    challenge_description,
    error_taxonomy,
    synthesized_example,
    worked_examples,
)
from operator_voice.domain.operations import OPERATIONS, Posture
from operator_voice.eval.coverage import (
    deterministic_help_coverage_rate,
    interface_registry_parity_rate,
    op_help_complete,
)


def test_deterministic_help_coverage_rate_is_100():
    assert deterministic_help_coverage_rate() == 100.0


def test_interface_registry_parity_rate_is_100():
    assert interface_registry_parity_rate() == 100.0


def test_every_operation_help_is_complete():
    incomplete = [op.name for op in OPERATIONS if not op_help_complete(op)]
    assert incomplete == [], f"operations with incomplete generated help: {incomplete}"


def test_every_operation_has_a_nonempty_pathfree_error_taxonomy():
    for op in OPERATIONS:
        taxonomy = error_taxonomy(op)
        assert taxonomy, f"{op.name} has an empty error taxonomy"
        codes = [e.code for e in taxonomy]
        assert len(codes) == len(set(codes)), f"{op.name} has duplicate error codes: {codes}"
        for e in taxonomy:
            assert e.code and e.meaning, f"{op.name} error {e!r} is missing a code or meaning"
            assert "/Users/" not in e.meaning and "/home/" not in e.meaning
            assert e.code.replace("_", "").isalnum(), f"non-token error code {e.code!r} on {op.name}"


def test_every_operation_has_at_least_one_worked_example_starting_with_the_cli():
    for op in OPERATIONS:
        examples = worked_examples(op)
        assert examples, f"{op.name} has no worked example"
        for ex in examples:
            assert ex.startswith(f"operator-voice {op.cli_name}"), f"{op.name}: bad example {ex!r}"


def test_synthesized_example_includes_required_flags_only():
    # ADR 0003 makes subject_id optional only for context.get; consumer_id remains required.
    ctx = next(op for op in OPERATIONS if op.name == "context.get")
    synthetic = synthesized_example(ctx)
    assert "--subject-id" not in synthetic and "--consumer-id" in synthetic
    assert "--channel" not in synthetic and "--token-budget" not in synthetic


def test_consequential_ops_have_a_challenge_description_and_others_do_not():
    for op in OPERATIONS:
        card = render_operation_card(op)
        if op.posture == Posture.CONSEQUENTIAL:
            assert challenge_description(op) is not None, f"{op.name} missing challenge description"
            assert "## Confirmation" in card, f"{op.name} card missing Confirmation section"
            assert "challenge_literal" in card, f"{op.name} confirmation must reference challenge_literal"
        else:
            assert challenge_description(op) is None, f"non-consequential {op.name} has a challenge description"
            assert "## Confirmation" not in card, f"non-consequential {op.name} card has a Confirmation section"


def test_consequential_op_set_matches_the_service_confirmation_gate():
    """The consequential operations (challenge-gated in the card) are exactly the ones the service
    routes through its confirmation gate — a drift check so the card can never advertise a gate the
    service does not enforce, or hide one it does."""
    consequential = {op.name for op in OPERATIONS if op.posture == Posture.CONSEQUENTIAL}
    assert consequential == {
        "export", "erase", "reextract", "backup.restore", "profile.remote_bind", "portability.export", "key.rotate", "uninstall",
    }
