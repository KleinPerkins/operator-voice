# Audit provenance — the in-repo copy of take/reject decisions

This is the file `NOTICE` refers to as "`docs/audit-provenance.md` in this repo for the in-repo copy of
the take/reject decisions." It is a short, navigable summary — the full forensic audit (scoring rubric,
exact source-line evidence, cross-repo component matrix) lives in
`operator-voice-toolkit-forensic-audit.md` in the `KleinPerkins/dev` workspace
(`~/dev/_ops/plans/operator-voice-toolkit-forensic-audit.md`) and is not duplicated here.

## Why this exists

Before writing any OVT code, a static forensic audit inspected three third-party repositories as prior
art for a voice-memory/application tool, scored each on a 100-point rubric, and made an explicit
take/adapt/reject decision per component. `NOTICE` records the legal/licensing obligations that follow
from that; this file records **which concept came from where**, so a reader can trace an OVT module back
to the audit finding that justified it.

**Provenance discipline (unchanged since audit): every module below is an independent re-implementation
of a described statistical/architectural concept, never a copy of source lines.** As of this review, no
code has been copied from any of the three repos — see `NOTICE` for the exact obligations that would
trigger if that ever changes.

## The three audited repos

| Repo | Score /100 | License | What it actually was |
|---|---|---|---|
| MYNAH | 23 | MIT | A prompt-only Claude skill — no executable inference code at all; its value was a well-organized *specification*, not an engine. |
| write-like-me | 41 (strongest of the three) | MIT | A Claude Code skill with real deterministic Python: role-based export parsing and a broad, cited-methodology stylometry extractor — but zero automated tests and single-shot, one-profile-at-a-time. |
| voice-analyser-mcp | 33 | Apache-2.0 | The only repo with a real running MCP server and the richest statistical feature set — undermined by a public-web-only collector (wrong source model for private operator text) and, in several analyzers, explicit AI-detection-evasion framing that OVT's build plan rejected outright. |

## What OVT took, adapted, and rejected

| Concept | Source | OVT's treatment | Where it lives today |
|---|---|---|---|
| Role-based export parsing (Claude/ChatGPT sender metadata) | write-like-me `extract_author_turns.py` | **Adapted historically**; agent-chat scanners are retired under ADR 0004. The kernel still quarantines ambiguous authorship for review. Stored evidence is never automatically erased. | `kernel/authorship.py`; retained Slack, Gmail and Drive materializers verify each record against the call's asserted identity |
| Tier-1 stylometry (sentence length, function words, punctuation, hedging, MATTR) | write-like-me `stylometry.py` | **Behaviorally reimplemented** under golden/property tests (the source had zero tests) | `kernel/features.py` |
| Stated-preference-outranks-measurement precedence | write-like-me `08-regeneration-and-diff.md` | **Elevated to a first-class kernel state machine**, not a manual diff-and-reread procedure | `kernel/synthesis.py` — `CORRECTED > CONFIRMED > SUPPORTED > OBSERVED` |
| Held-out verification protocol | write-like-me `07-verification.md` | **Automated** — the source only specified the procedure for a human/LLM to execute by counting | `kernel/validation.py`, `eval/` harness |
| Context taxonomy (10 communication contexts) | MYNAH `context-guide.md`/`style-dimensions.md` | **Re-authored as structured, validated data**, not enforced-nowhere prose | `kernel/taxonomy.py`, `domain/models.py`'s `ContextScope` |
| Profile-schema convention (required/optional fields) | MYNAH `profile-schema.md` | **Replaced with a machine-validated typed schema** (frozen `VoiceContextBundle/v1`) instead of an unenforced Markdown convention | `domain/contracts.py`, `docs/generated/schemas/*.json` |
| Burstiness / clustering / entropy statistics | voice-analyser-mcp `advanced-statistics.ts` | **Ported and tested** (Apache-2.0 concept reuse; source had zero tests) | `kernel/signals.py` |
| Micro-rhythm detection | voice-analyser-mcp `micro-rhythm.ts` | **Ported**, with all "detection risk"/evasion framing stripped — retain only operator-grounded descriptive signal | `kernel/signals.py` |
| Argument-flow confidence scoring | voice-analyser-mcp `argument-flow.ts` | **Adapted and generalized**: the pattern (match-strength ratio → confidence band) is applied system-wide via calibration, not left isolated to one analyzer | `kernel/signals.py`, `kernel/bands.py`, `kernel/calibration.py` |
| MCP server shape | voice-analyser-mcp `src/index.ts` | **Adapted** — stdio MCP, tool registration pattern | `interfaces/mcp.py` |

## What was explicitly rejected (not merely "hardened")

- **Public-sitemap web crawling as a source model** (voice-analyser-mcp) — structurally mismatched with
  "approved operator-authored" sources; OVT's adapters require an explicit, scoped, consented source
  registration and never crawl the public web by default.
- **AI-detection-evasion framing** (voice-analyser-mcp, several analyzers) — instructing output to
  deliberately mimic "non-AI" patterns (e.g. "AI rarely uses fragments; ADD deliberate fragments") is a
  different, more ethically fraught goal than authentic voice fidelity, and risks manufacturing quirks the
  operator doesn't actually have. The underlying descriptive statistics were kept; only this prescriptive
  framing was excluded (build plan Decision D3 — a deliberate, narrow exclusion, not a rejection of the
  repo's analytical value).
- **Whole-profile / whole-file context injection** (all three repos, in different forms) — elevated to a
  hard architectural rule (build plan D4) because it was the one failure mode every single audited repo
  shared. See [Product requirements](product-requirements.md#problems-solved-condensed).
- **Opaque external-memory delegation** (MYNAH's "save to PACK or Claude's built-in memory") — OVT owns
  its own encrypted local persistence rather than delegating storage to an unaudited external system.
- **Unenforced/uncited inference thresholds** (voice-analyser-mcp's per-analyzer qualitative bands) —
  replaced with calibrated, method-versioned bands (`kernel/calibration.py`) rather than asserted cutoffs.

## Licensing obligations (see `NOTICE` for the authoritative text)

No code has been copied from either MIT-licensed repo (write-like-me) or the Apache-2.0-licensed repo
(voice-analyser-mcp) — every module above is an independent re-implementation of the *concept*. If a
future change ever copies or directly adapts actual source lines rather than reimplementing the concept,
it must, before merge: (1) bundle the upstream LICENSE text for that dependency, and (2) add a per-file
"adapted from `<repo>` (`<license>`)" change notice. `NOTICE` exists precisely so this obligation is never
missed if that ever changes.

## Full detail

- Exact source-line evidence, the complete 100-point scoring rubric, and the full cross-repo component
  matrix: `~/dev/_ops/plans/operator-voice-toolkit-forensic-audit.md`.
- The architectural decisions this audit fed into: `~/dev/_ops/plans/operator-voice-toolkit-build-plan.md`
  §3 (audit decisions and assembly map) and §14 (decision log, D1–D22).
