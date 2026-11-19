#!/usr/bin/env python3
"""M5-A evidence collector — build plan v4 §13 M5 step 3 ("produce the versioned evidence pack
using the EXACT preregistered scoring manifest/formula/weights from M1").

This script is the ONLY place the frozen ``operator_voice.eval.manifest`` formula is fed real,
observed measure values. It never edits the manifest, never invents a weight, and never scores a
dimension itself — it collects each of the manifest's fourteen evidence measures from a concrete,
cited source and hands them to the manifest's own ``score_all``:

  * Fixture measures (a pass rate) are collected by running the exact cited test node-set in an
    isolated pytest subprocess and computing 100 * passed / (passed + failed + errors). A single
    failure in any cited set lowers that measure — the number is observed, never asserted.
  * Computed measures (help coverage, interface parity, benchmark budget adherence) are computed by
    pure, separately-unit-tested functions in ``operator_voice.eval.coverage`` /
    ``operator_voice.eval.metrics`` / ``.benchmarks``.
  * ``security_review_clean_rate`` is NOT self-assessed: it is supplied on the command line from
    the independent security-review disposition (``--security-open`` / ``--security-total``). Absent
    it, the measure is omitted and every dimension that depends on it correctly ABSTAINS.
  * ``ecosystem_lifecycle_conformance_rate`` is likewise NOT self-scored in M5-A: two of its three
    checks (live GCB ``verify-skills.sh`` + lock parity, live WF1 registry state) are the governed
    M5-B delivery and out of hermetic scope, so the measure is OMITTED by default and
    ``integration_cohesion`` (and thus ``overall_build_quality``) correctly ABSTAINS. Its hermetic
    sub-checks (payload zero-link + no bespoke vendoring/parallel installer in the diff) ARE verified
    and recorded. A verifier supplies the measure only AFTER live M5-B re-verification, via
    ``--attest-ecosystem-rate`` / ``--attest-ecosystem-note`` — the same out-of-band-disposition
    pattern as the security review.

Hermetic: no network, no live cpp/orchestrator/skills repo, no real Keychain, synthetic corpora
only. The network-touching e2e install test (``-m e2e``) is deliberately excluded; the hermetic
runtime-install + M5 clean-room suites carry the distributor-convergence / local-only evidence.

Usage:
    uv run python scripts/collect_m5_evidence.py \
        [--security-open N --security-total M] [--out docs/generated/m5-evidence-pack.json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import operator_voice  # noqa: E402
from operator_voice.eval import coverage  # noqa: E402
from operator_voice.eval import benchmarks  # noqa: E402
from operator_voice.eval.manifest import (  # noqa: E402
    LEAF_DIMENSIONS,
    MANIFEST_DIGEST,
    MANIFEST_VERSION,
    DIMENSIONS,
    score_all,
)
from operator_voice.eval.metrics import (  # noqa: E402
    BudgetAdherence,
    benchmark_budget_adherence_rate,
    build_evidence_report,
    clean_rate,
    rate,
)
from operator_voice.runtime.payload import build_portable_payload, compute_digest  # noqa: E402

# --------------------------------------------------------------------------------------------
# Fixture measures: measure id -> (cited pytest node-set, human citation). Every path is a real
# committed test module. Where the frozen manifest names a network-touching e2e module, the
# hermetic equivalent is substituted and the substitution is disclosed in the pack.
# --------------------------------------------------------------------------------------------
FIXTURE_MEASURES: dict[str, dict[str, object]] = {
    "authorship_isolation_rate": {
        "nodes": ["tests/unit/test_adapters.py", "tests/integration/test_service.py",
                  "tests/security/test_authorship_egress_adversarial.py"],
        "note": "Adapter authorship/quarantine + service authorship cases + adversarial authorship/egress suite.",
    },
    "exactly_once_recovery_rate": {
        "nodes": ["tests/integration/test_service.py", "tests/integration/test_persistence.py",
                  "tests/integration/test_curation.py"],
        "note": "Duplicate/idempotent ingest + migration/transaction atomicity + curation catch-up/stale-run recovery.",
    },
    "local_only_zero_egress_rate": {
        "nodes": ["tests/integration/test_service.py", "tests/integration/test_profile_pack.py",
                  "tests/integration/test_cleanroom_m5.py"],
        "note": "LOCAL_ONLY remote-verb no-op cases + the M5 cpp-only full LOCAL_ONLY lifecycle (hermetic substitute for the network e2e install test).",
    },
    "distributor_convergence_rate": {
        "nodes": ["tests/integration/test_runtime_install.py", "tests/integration/test_cleanroom_m5.py"],
        "note": "M1 dual-distributor fixtures + M5 cpp-only / home-first / cpp-first convergence + version-drift fail-closed + repair boundary. Live GCB-embedded + ~/dev bootstrap convergence is the governed M5-B step.",
    },
    "storage_encryption_scan_clean_rate": {
        "nodes": ["tests/integration/test_persistence.py", "tests/integration/test_profile_pack.py",
                  "tests/integration/test_runtime_install.py", "tests/integration/test_no_plaintext_content.py"],
        "note": "Crypto round-trip + backup ciphertext scans + commit-content scans + payload denylist + repo-wide no-plaintext-content scan.",
    },
    "retrieval_determinism_rate": {
        "nodes": ["tests/unit/test_kernel_pipeline.py", "tests/property/test_kernel_properties.py"],
        "note": "Retrieval determinism/abstention cases + property-based kernel invariants.",
    },
    "correction_precedence_durability_rate": {
        "nodes": ["tests/unit/test_kernel_pipeline.py", "tests/integration/test_service.py"],
        "note": "Correction-precedence cases + rebuild/feedback precedence + publish/restore durability.",
    },
    "profile_backend_integrity_rate": {
        "nodes": ["tests/integration/test_profile_pack.py", "tests/unit/test_reconcile.py"],
        "note": "Divergent-writer / clone+recover+rebuild digest-parity / safe-refusal + reconcile conflict-queueing.",
    },
    "lifecycle_completeness_rate": {
        "nodes": ["tests/integration/test_persistence.py", "tests/integration/test_service.py",
                  "tests/integration/test_key_rotate.py"],
        "note": "Erase-granularity/backup/retention/migration + off/cancel + key rotation/destruction (crypto-shred) scenarios.",
    },
}

# Measures computed directly (not a test pass rate). ``ecosystem_lifecycle_conformance_rate`` is
# NOT here: in M5-A it is deferred (abstained) rather than computed — see _ecosystem_hermetic_evidence.
COMPUTED_IDS = {
    "interface_registry_parity_rate", "benchmark_budget_adherence_rate",
    "deterministic_help_coverage_rate",
}

_SUMMARY_TOKENS = ("passed", "failed", "error", "errors", "skipped", "deselected", "xfailed", "xpassed")


def _run_pytest(nodes: list[str]) -> dict[str, int]:
    """Run the given pytest node-set hermetically (``-m "not e2e"``) in its own subprocess and
    parse the terminal summary into counts. Raises if pytest could not even collect/run."""
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider",
           "-m", "not e2e", *nodes]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    text = proc.stdout + "\n" + proc.stderr
    counts = {t: 0 for t in ("passed", "failed", "errors", "skipped", "deselected")}
    # The summary line is the last non-empty line, e.g. "416 passed, 2 deselected in 9.41s".
    summary_line = ""
    for line in reversed(text.strip().splitlines()):
        if any(tok in line for tok in _SUMMARY_TOKENS) and re.search(r"\d", line):
            summary_line = line
            break
    for n, tok in re.findall(r"(\d+)\s+(passed|failed|error|errors|skipped|deselected|xfailed|xpassed)", summary_line):
        key = "errors" if tok in ("error", "errors") else tok
        if key in counts:
            counts[key] += int(n)
    counts["_selected_total"] = counts["passed"] + counts["failed"] + counts["errors"]
    if counts["_selected_total"] == 0:
        raise RuntimeError(f"pytest collected nothing for {nodes!r}; output tail:\n{text[-2000:]}")
    return counts


def _fixture_rate(nodes: list[str]) -> tuple[float, dict[str, int]]:
    counts = _run_pytest(nodes)
    passed = counts["passed"]
    total = counts["_selected_total"]
    return rate(passed, total), counts


def _benchmark_adherence() -> tuple[float, list[dict[str, object]]]:
    """Run the synthetic small/medium/large benchmark suite and compare each metric to the pinned
    M1 budget. Returns (rate, per-budget detail)."""
    baseline = benchmarks.load_baseline_file(REPO_ROOT / "docs" / "generated" / "m1-benchmark-baseline.json")
    budgets_by_size = baseline["pinned_budget"]["by_size"]
    with tempfile.TemporaryDirectory(prefix="ovt-m5-bench-") as tmp:
        report = benchmarks.run_benchmark_suite(Path(tmp))
    adherences: list[BudgetAdherence] = []
    detail: list[dict[str, object]] = []
    for r in report.results:
        b = budgets_by_size[str(r.size)]
        pairs = {
            "ingest_p50_ms": r.ingest.p50_ms, "ingest_p95_ms": r.ingest.p95_ms,
            "rebuild_p50_ms": r.rebuild.p50_ms, "rebuild_p95_ms": r.rebuild.p95_ms,
            "context_get_p50_ms": r.context_get.p50_ms, "context_get_p95_ms": r.context_get.p95_ms,
            "peak_memory_bytes": float(r.peak_memory_bytes), "db_bytes": float(r.db_bytes),
        }
        for metric, observed in pairs.items():
            budget = float(b[metric])
            a = BudgetAdherence(observed=observed, budget=budget, lower_is_better=True)
            adherences.append(a)
            detail.append({"size": r.size, "metric": metric, "observed": round(observed, 3),
                           "budget": round(budget, 3), "met": a.met()})
    return benchmark_budget_adherence_rate(adherences), detail


# Actionable downstream-linkage patterns (a real code dependency or remote), NOT bare prose
# mentions. The frozen eval manifest documents where a measure's evidence comes from
# ("personal/ecosystem-orchestrator config/registry.yml") in a docstring — that provenance prose is
# not a link and must not be flagged. A genuine link is an import of a downstream module or a
# hardcoded git remote/PR-merge command targeting a downstream repo.
_IMPORT_LINK = re.compile(r"^\s*(?:from|import)\s+[\w.]*(?:card_platform|ecosystem_orchestrator|cpp_sync)\b", re.M)
_URL_LINK = re.compile(r"github\.com[:/](?:Affirm/card-platform-product|KleinPerkins/ecosystem-orchestrator)")
# Actionable mechanism tokens for the diff review (downstream write / parallel installer / merge-arming).
_MECHANISM_TOKENS = ("cpp-sync", "gh pr merge --auto", "--admin", "affirm/src/", "quiver/")


def _changed_functional_files() -> list[Path]:
    """Files this branch changes on the FUNCTIONAL surface where a bespoke installer / downstream
    write could actually live — src/, tests/, scripts/, install.sh, and build files. Excludes only
    docs/ (documentation, incl. the ledger and the generated evidence pack, which cannot vendor) and
    THIS collector file itself (measurement tooling that legitimately names the governance/mechanism
    tokens as its own denylist). Scanning the REST of scripts/ closes the "a parallel installer
    dropped in scripts/ would evade check 3" gap. Commit-state-independent (tracked + untracked)."""
    tracked = subprocess.run(["git", "diff", "--name-only", "origin/main"], cwd=REPO_ROOT,
                             capture_output=True, text=True).stdout.split()
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO_ROOT,
                               capture_output=True, text=True).stdout.split()
    collector_rel = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
    out: list[Path] = []
    for rel in sorted(set(tracked) | set(untracked)):
        if rel.startswith("docs/") or rel == collector_rel:
            continue
        if rel.startswith(("src/", "tests/", "scripts/")) or rel in ("install.sh", "pyproject.toml", "Makefile"):
            p = REPO_ROOT / rel
            if p.is_file():
                out.append(p)
    return out


def _ecosystem_hermetic_evidence() -> tuple[bool, list[dict[str, object]]]:
    """Collect the parts of the frozen ``ecosystem_lifecycle_conformance_rate`` measure that are
    honestly verifiable INSIDE M5-A's hermetic scope, and mark the rest as deferred — never scored
    as PASS from an M1 record. The measure's three checks:

      1. live GCB ``verify-skills.sh`` (manifest contract + lock parity) — DEFERRED to M5-B (reading
         the live ``KleinPerkins/skills`` shelf is out of M5-A scope, and the CURRENT release's shelf
         refresh has not happened yet). The SSP-side ZERO-LINK half (the freshly built payload has no
         downstream import or git remote) IS verified here, hermetically, as supporting evidence.
      2. live WF1 registry ``scope=global`` + explicit state — DEFERRED to M5-B (reading the
         ``ecosystem-orchestrator`` repo is out of M5-A scope).
      3. no bespoke vendoring / direct downstream write / parallel installer anywhere in the diff —
         VERIFIED HERMETICALLY here (full-content scan of every changed functional file).

    Returns ``(hermetic_ok, checks)`` where ``hermetic_ok`` is True iff every check that CAN be
    evaluated hermetically (the payload zero-link scan + the diff scan) is clean. It deliberately
    does NOT return a measure rate: because two of the three checks cannot be evaluated in M5-A, the
    measure as a whole is ``insufficient_evidence`` and is OMITTED from the report (so
    ``integration_cohesion`` abstains) unless a verifier supplies a live M5-B attestation."""
    checks: list[dict[str, object]] = []

    # Hermetic supporting evidence for check 1: the built portable payload carries no actionable
    # downstream linkage (import or git remote).
    with tempfile.TemporaryDirectory(prefix="ovt-m5-zl-") as tmp:
        pkg_root = Path(operator_voice.__file__).parent
        payload_dir = Path(tmp) / "payload"
        build_portable_payload(pkg_root, payload_dir, version=operator_voice.__version__)
        hits: list[str] = []
        for p in sorted(payload_dir.rglob("*")):
            if p.is_file():
                blob = p.read_text(encoding="utf-8", errors="ignore")
                if _IMPORT_LINK.search(blob):
                    hits.append(f"{p.relative_to(payload_dir).as_posix()}::import-linkage")
                if _URL_LINK.search(blob):
                    hits.append(f"{p.relative_to(payload_dir).as_posix()}::downstream-remote")
    payload_zero_link_clean = not hits
    checks.append({
        "check": "gcb_manifest_zero_link_lock_parity",
        "status": "deferred_to_m5b",
        "hermetic_supporting_evidence": {
            "payload_zero_link_clean": payload_zero_link_clean,
            "actionable_linkage_hits": hits,
        },
        "provenance": "Live GCB verify-skills.sh (manifest contract + lock parity) against the CURRENT release shelf is the governed M5-B step and out of M5-A scope. Verified hermetically here only: the freshly built payload has no downstream import or git remote (zero-link).",
    })

    # Check 2 — live WF1 registry state. Deferred; not read here.
    checks.append({
        "check": "wf1_registry_scope_global_explicit_state",
        "status": "deferred_to_m5b",
        "provenance": "Reading the ecosystem-orchestrator WF1 registry is out of M5-A scope; scope=global + explicit state is re-confirmed live in the governed M5-B delivery. M5-A touches no WF1/WF2.",
    })

    # Check 3 — no bespoke vendoring / direct downstream write / parallel installer, scanning the
    # FULL content of every changed functional file. Hermetic and verifiable now.
    diff_hits: list[str] = []
    for p in _changed_functional_files():
        blob = p.read_text(encoding="utf-8", errors="ignore")
        rel = p.relative_to(REPO_ROOT).as_posix()
        if _IMPORT_LINK.search(blob):
            diff_hits.append(f"{rel}::import-linkage")
        if _URL_LINK.search(blob):
            diff_hits.append(f"{rel}::downstream-remote")
        for tok in _MECHANISM_TOKENS:
            if tok in blob:
                diff_hits.append(f"{rel}::{tok}")
    diff_clean = not diff_hits
    checks.append({
        "check": "no_bespoke_vendoring_or_parallel_installer_in_diff",
        "status": "verified_hermetic",
        "passed": diff_clean,
        "provenance": "Full-content scan of every changed functional file (src/, tests/, scripts/ except this collector, install.sh, build) for downstream import/remote/mechanism tokens; docs/ excluded (cannot vendor). Full diff also gets independent manual verifier review.",
        "detail": {"scan_hits": sorted(set(diff_hits))},
    })

    hermetic_ok = payload_zero_link_clean and diff_clean
    return hermetic_ok, checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Collect M5-A evidence and score the frozen manifest.")
    ap.add_argument("--security-open", type=int, default=None, help="Open P0/P1 findings from the independent security review")
    ap.add_argument("--security-total", type=int, default=None, help="Total findings raised in the independent security review")
    ap.add_argument("--attest-ecosystem-rate", type=float, default=None,
                    help="M5-B ONLY: the ecosystem_lifecycle_conformance_rate [0,100] a verifier supplies AFTER live GCB/WF1 re-verification. Omit in M5-A so integration_cohesion honestly abstains.")
    ap.add_argument("--attest-ecosystem-note", type=str, default=None,
                    help="Required justification recorded alongside --attest-ecosystem-rate (who verified, against which live shelf/registry).")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "generated" / "m5-evidence-pack.json")
    args = ap.parse_args(argv)
    if (args.attest_ecosystem_rate is None) != (args.attest_ecosystem_note is None):
        ap.error("--attest-ecosystem-rate and --attest-ecosystem-note must be supplied together")

    version = operator_voice.__version__
    release_digest, release_files = compute_digest(Path(operator_voice.__file__).parent)

    measures: dict[str, float] = {}
    provenance: dict[str, dict[str, object]] = {}

    print("== Fixture measures (cited pytest node-sets) ==")
    for mid, spec in FIXTURE_MEASURES.items():
        nodes = list(spec["nodes"])  # type: ignore[arg-type]
        value, counts = _fixture_rate(nodes)
        measures[mid] = value
        provenance[mid] = {"kind": "fixture_pass_rate", "nodes": nodes, "counts": counts, "note": spec["note"]}
        print(f"  {mid:42s} {value:6.2f}  ({counts['passed']}/{counts['_selected_total']} passed) {nodes}")

    print("== Computed measures ==")
    help_rate = coverage.deterministic_help_coverage_rate()
    measures["deterministic_help_coverage_rate"] = help_rate
    provenance["deterministic_help_coverage_rate"] = {
        "kind": "computed", "source": "operator_voice.eval.coverage.deterministic_help_coverage_rate()",
        "note": "Fraction of OPERATIONS whose generated card has a worked example, a non-empty error taxonomy, and (if consequential) a challenge/consent description. Locked by tests/eval/test_help_coverage.py.",
    }
    print(f"  deterministic_help_coverage_rate           {help_rate:6.2f}")

    parity_rate = coverage.interface_registry_parity_rate()
    measures["interface_registry_parity_rate"] = parity_rate
    provenance["interface_registry_parity_rate"] = {
        "kind": "computed", "source": "operator_voice.eval.coverage.interface_registry_parity_rate() + tests/integration/test_interfaces.py, tests/unit/test_docs_and_skill.py (green)",
        "note": "Per-op CLI subcommand + correct MCP exposure + generated card. Colloquial-intent and direct-service-policy-enforcement halves proven by the cited interface test suite.",
    }
    print(f"  interface_registry_parity_rate             {parity_rate:6.2f}")

    bench_rate, bench_detail = _benchmark_adherence()
    measures["benchmark_budget_adherence_rate"] = bench_rate
    provenance["benchmark_budget_adherence_rate"] = {
        "kind": "computed", "source": "operator_voice.eval.benchmarks.run_benchmark_suite() vs docs/generated/m1-benchmark-baseline.json",
        "detail": bench_detail,
    }
    misses = [d for d in bench_detail if not d["met"]]
    print(f"  benchmark_budget_adherence_rate            {bench_rate:6.2f}  ({len(bench_detail)-len(misses)}/{len(bench_detail)} budgets met)")
    for d in misses:
        print(f"      MISS size={d['size']} {d['metric']}: observed={d['observed']} > budget={d['budget']}")

    eco_hermetic_ok, eco_checks = _ecosystem_hermetic_evidence()
    if args.attest_ecosystem_rate is not None:
        if not (0.0 <= args.attest_ecosystem_rate <= 100.0):
            ap.error("--attest-ecosystem-rate must be within [0, 100]")
        measures["ecosystem_lifecycle_conformance_rate"] = args.attest_ecosystem_rate
        provenance["ecosystem_lifecycle_conformance_rate"] = {
            "kind": "verifier_attested_m5b", "rate": args.attest_ecosystem_rate,
            "attestation_note": args.attest_ecosystem_note,
            "hermetic_ok": eco_hermetic_ok, "checks": eco_checks,
        }
        print(f"  ecosystem_lifecycle_conformance_rate       {args.attest_ecosystem_rate:6.2f}  (M5-B verifier-attested)")
    else:
        # M5-A: two of the three checks (live GCB verify-skills.sh, live WF1 registry) cannot be
        # evaluated hermetically, so the measure is OMITTED — integration_cohesion (and thus overall)
        # correctly ABSTAINS, exactly like the abstention discipline the manifest applies elsewhere.
        provenance["ecosystem_lifecycle_conformance_rate"] = {
            "kind": "deferred_m5b", "hermetic_ok": eco_hermetic_ok, "checks": eco_checks,
            "note": ("Omitted in M5-A (insufficient evidence): the live GCB verify-skills.sh + WF1 "
                     "registry checks are the governed M5-B delivery and out of hermetic scope. The "
                     "hermetic sub-checks (payload zero-link + diff has no bespoke vendoring/parallel "
                     "installer) are clean; integration_cohesion abstains pending M5-B live verification."),
        }
        print(f"  ecosystem_lifecycle_conformance_rate       (DEFERRED — hermetic sub-checks {'clean' if eco_hermetic_ok else 'NOT clean'}; integration_cohesion will ABSTAIN in M5-A)")

    if args.security_open is not None and args.security_total is not None:
        sec = clean_rate(args.security_open, args.security_total)
        measures["security_review_clean_rate"] = sec
        provenance["security_review_clean_rate"] = {
            "kind": "independent_review", "open_p0_p1": args.security_open, "total_findings": args.security_total,
            "note": "Supplied from the M5 independent security-review disposition; never self-assessed.",
        }
        print(f"  security_review_clean_rate                 {sec:6.2f}  (open P0/P1={args.security_open}, total={args.security_total})")
    else:
        print("  security_review_clean_rate                 (PENDING — independent review not yet supplied; dependent dimensions will ABSTAIN)")

    report = build_evidence_report(**measures)
    scores = score_all(report)

    print("\n== Dimension scores (frozen manifest) ==")
    all_ge_95 = True
    for dim in DIMENSIONS:
        s = scores[dim]
        if isinstance(s, str):
            print(f"  {dim:24s} {s}")
            all_ge_95 = False
        else:
            flag = "" if s >= 95.0 else "   <-- BELOW 95"
            if s < 95.0:
                all_ge_95 = False
            print(f"  {dim:24s} {s:6.2f}{flag}")

    pack = {
        "schema": "m5-evidence-pack/v1",
        "release_candidate": {"name": "operator-voice", "version": version,
                              "package_digest": release_digest, "package_file_count": len(release_files)},
        "manifest": {"version": MANIFEST_VERSION, "digest": MANIFEST_DIGEST},
        "measures": measures,
        "measure_provenance": provenance,
        "dimension_scores": {d: scores[d] for d in DIMENSIONS},
        "all_dimensions_ge_95": all_ge_95,
        "leaf_dimensions": list(LEAF_DIMENSIONS),
        "hermetic": True,
        "notes": [
            "No network, no live cpp/orchestrator/skills repo, no real Keychain; synthetic corpora only.",
            "e2e (PyPI network) install test excluded; hermetic runtime-install + M5 clean-room carry convergence/local-only evidence.",
            "Live GCB payload refresh + WF1/WF2 cpp-sync are the governed M5-B steps, not scored here.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pack, indent=2, sort_keys=True) + "\n")
    try:
        shown = args.out.resolve().relative_to(REPO_ROOT)
    except ValueError:
        shown = args.out.resolve()  # --out outside the repo (e.g. a /tmp dry run)
    print(f"\nWrote {shown} (all_dimensions_ge_95={all_ge_95})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
