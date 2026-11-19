"""Structural guard — the ADR readiness gate and plan/ADR acceptance cross-consistency.

Pure *structural* test (parses the docs, executes no product code), in the same idiom as
``test_kernel_purity.py`` and ``tests/contract/`` — the governance invariant holds mechanically,
not just by convention.

Policy source of truth: ``docs/adr/README.md`` + ``docs/plans/README.md`` (the mutable convention
READMEs). The rule those encode:

  A plan GO accepts every ADR the plan cites, in the same PR that freezes the plan. An ADR may be
  accepted only from a *ready-for-acceptance* state; a plan GO may NEVER accept an ADR still in a
  pre-acceptance state (``proposed``/``draft`` — i.e. WIP/incomplete). Readiness itself is a
  checklist GATE on the existing ``proposed`` state, not a new status token.

Readiness is a transient pre-GO state and is governed by the checklist in ``docs/adr/README.md``;
it is not separately checkable from final-state docs, by design. What *is* durably checkable — and
what this test enforces — is the end state on ``main``: no approved/frozen plan may cite an ADR that
never crossed the acceptance gate. The status-vocabulary check additionally blocks a forked/bespoke
ADR status token (e.g. ``ready-for-acceptance``), keeping the repo uniform with ``doc-warden``.
"""

from __future__ import annotations

import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_ADR_DIR = _REPO_ROOT / "docs" / "adr"
_PLANS_DIR = _REPO_ROOT / "docs" / "plans"

# Valid ADR status tokens = this repo's local vocabulary PLUS doc-warden's canonical ``draft`` (its
# name for ``proposed``). Accepting both means this guard survives the pending doc-warden ``repair``
# (``proposed`` -> ``draft``) without an edit, while still failing on a rogue/forked token such as a
# bespoke ``ready-for-acceptance`` — readiness is a GATE on ``proposed``, never its own token.
_VALID_ADR_STATUSES = {"proposed", "draft", "accepted", "superseded", "deprecated"}

# Pre-acceptance ADR states: the ADR has NOT crossed the readiness/acceptance gate and so may not be
# laundered to ``accepted`` by a plan GO. (``accepted``/``superseded``/``deprecated`` all mean the
# ADR *was* bound at some point, so they are exempt.)
_PRE_ACCEPTANCE_ADR_STATUSES = {"proposed", "draft"}

# A plan whose header Status contains any of these is owner-approved/bound: its GO has happened, so
# every ADR it cites must already be accepted. ``FROZEN`` matches OVT's ``APPROVED — FROZEN``;
# ``ACCEPTED`` matches doc-warden's canonical plan term; ``COMPLETE`` covers a shipped plan. A
# ``DRAFT``/``SUPERSEDED``/``PROPOSED`` plan contains none of these and is (correctly) not gated.
_BOUND_PLAN_MARKERS = ("FROZEN", "ACCEPTED", "COMPLETE")


def _md_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in directory.glob("*.md") if p.name != "README.md")


def _adr_status_token(text: str) -> str | None:
    # Nygard header: ``Status: <token> — <date> (optional context)`` on its own line near the top.
    match = re.search(r"^Status:\s*([A-Za-z-]+)", text, re.MULTILINE)
    return match.group(1).lower() if match else None


def _adr_number(path: pathlib.Path) -> str | None:
    match = re.match(r"(\d{4})-", path.name)
    return match.group(1) if match else None


def _plan_status_line(text: str) -> str | None:
    # Plan header bullet: ``- **Status:** <...>``.
    match = re.search(r"^-\s*\*\*Status:\*\*\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _cited_adr_numbers(text: str) -> set[str]:
    # ADRs are cited as relative links (``../adr/NNNN-slug.md``) and/or ``ADR NNNN`` mentions.
    nums = set(re.findall(r"adr/(\d{4})", text))
    nums |= set(re.findall(r"\bADR[ -]?(\d{4})\b", text))
    return nums


def test_doc_governance_dirs_scan() -> None:
    assert _md_files(_ADR_DIR), "expected ADR files to scan under docs/adr/"
    assert _md_files(_PLANS_DIR), "expected plan files to scan under docs/plans/"


def test_every_adr_has_a_valid_status_token() -> None:
    """Each ADR carries a parseable Status token from the agreed vocabulary — never a forked one."""
    offenders: dict[str, str | None] = {}
    for path in _md_files(_ADR_DIR):
        token = _adr_status_token(path.read_text(encoding="utf-8"))
        if token not in _VALID_ADR_STATUSES:
            offenders[path.name] = token
    assert not offenders, (
        f"ADR(s) with a missing or forked Status token: {offenders}. Valid tokens: "
        f"{sorted(_VALID_ADR_STATUSES)}. Do NOT introduce a bespoke token (e.g. "
        "'ready-for-acceptance') — readiness is a pre-acceptance GATE on the existing "
        "proposed/draft state (see docs/adr/README.md), not a new status. This keeps the repo "
        "uniform with doc-warden."
    )


def test_no_approved_plan_cites_a_pre_acceptance_adr() -> None:
    """The core invariant: a plan GO must never accept/launder a still-WIP ADR.

    Restated over the durable end state: no ``APPROVED — FROZEN`` (or otherwise bound) plan may cite
    an ADR still in a pre-acceptance state (``proposed``/``draft``).
    """
    adr_status: dict[str, str | None] = {}
    for path in _md_files(_ADR_DIR):
        number = _adr_number(path)
        if number:
            adr_status[number] = _adr_status_token(path.read_text(encoding="utf-8"))

    violations: list[str] = []
    for plan in _md_files(_PLANS_DIR):
        text = plan.read_text(encoding="utf-8")
        status = _plan_status_line(text) or ""
        if not any(marker in status.upper() for marker in _BOUND_PLAN_MARKERS):
            continue  # DRAFT / superseded / proposed plan — GO has not bound its ADRs yet.
        for number in sorted(_cited_adr_numbers(text)):
            token = adr_status.get(number)
            if token in _PRE_ACCEPTANCE_ADR_STATUSES:
                violations.append(
                    f"{plan.name} (Status: {status!r}) cites ADR {number}, "
                    f"still in pre-acceptance state {token!r}"
                )

    assert not violations, (
        "A plan GO must never accept an ADR that was not ready-for-acceptance. "
        "Approved/frozen plan(s) cite pre-acceptance ADR(s):\n  - "
        + "\n  - ".join(violations)
        + "\nFix one of: (a) the ADR was truly accepted at GO — set its Status to "
        "'accepted — <date> (owner GO alongside plan vN)' and update docs/adr/README.md; or "
        "(b) the plan is not actually approved — keep it DRAFT until every cited ADR clears the "
        "readiness gate in docs/adr/README.md."
    )
