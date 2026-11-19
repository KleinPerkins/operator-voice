# AGENTS.md — operator-voice agent entrypoint

You are working inside **`operator-voice`** (`KleinPerkins/operator-voice`), the Operator Voice Toolkit
(OVT). This file is the entrypoint `src/operator_voice/__init__.py` refers to as "governance" — read it
first, then follow the docs it points to. GA, `v0.1.5`, milestones M1–M5 complete.

## Sources of truth (in order)

1. **[`docs/README.md`](docs/README.md)** — the documentation map: architecture, product requirements
   and invariants, operating procedures, system mechanics, topology, all routed for agents vs. operators.
2. **[`docs/product-requirements.md`](docs/product-requirements.md)** — the invariants you must never
   violate (privacy, egress, authorship-precedes-learning, operator-feedback precedence, `LOCAL_ONLY`
   completeness, dual-distributor convergence, honest erasure). Read this before writing any code that
   touches the kernel, persistence, or a new adapter.
3. **`~/dev/_ops/plans/operator-voice-toolkit-build-plan-v4.md`** — the immutable, authoritative design
   intent for this whole product. Do not edit it from this repo; it lives in the `KleinPerkins/dev`
   workspace one level up.

## What this repo is

A local-first, encrypted memory of how one operator writes, so a host agent can draft in their voice on
request. A **deterministic kernel** (`src/operator_voice/kernel/` — no network/model/I-O imports,
mechanically enforced by `tests/unit/test_kernel_purity.py`) does all fact-extraction and synthesis; an
**optional, default-off model gateway** exists only for qualitative comparison. See
[`docs/architecture.md`](docs/architecture.md) for the full component map.

## Working here

- **Git identity**: this is a personal/KleinPerkins repo — use the **local** noreply identity
  (`user.name = KleinPerkins`, `user.email = 49759187+KleinPerkins@users.noreply.github.com`), never
  `--global`. See `~/dev/AGENTS.md` if you need the home-workspace-wide rationale.
- **Delivery path is governed** — the authorized successor uses the six meaningful GitHub Actions
  shards and the existing Chaos Scheduler App4217367. Source presence is not publication or activation.
  The authoring agent automatically obtains independent review from the PR-creation Codex account,
  preferring a qualified enrolled same-account cloud route and otherwise its enrolled local adapter.
  Work and personal origins remain distinct despite their shared GitHub user. Use `scripts/local_review.py`,
  then `scripts/create_review_pr.py`, then publish the unedited receipt prefixed
  `<!-- g1-review-receipt:v1 -->`. Repeat after head, current base or trusted policy changes.
  Missing origin or unavailable review blocks that PR, without cross-account or human technical-review fallback.
  The trusted-main App alone approves and requests native squash after matching origin, review and exact CI.
  Initial admission remains owner-controlled. Never manually merge, manipulate auto-merge or bypass checks.
  See [`docs/ci-delivery.md`](docs/ci-delivery.md); historical `.github/pr-*` records are not the new gate.
- **One writer per working tree** — if a second parallel agent session needs to touch this repo, give it
  its own git worktree rather than sharing this one.
- **Dev loop**: `uv sync` to install (including `dev` extras), `uv run pytest` for the full suite,
  `uv run pytest -m "not e2e and not keychain"` for the fast subset (the `keychain` marker touches the
  real macOS Keychain via a disposable throwaway file — never the operator's login keychain; `e2e` is
  slow and network-touching). Regenerate reference docs after changing `domain/operations.py`,
  `docs/resources.py`, or `docs/prompts.py`: `python -m operator_voice.docs.generate docs/generated`
  (output is byte-stable — never hand-edit anything under `docs/generated/`).
- **Structural invariants are test-enforced, not just documented** — `test_kernel_purity.py` (AST-based
  import scan) and the contract-freeze tests under `tests/contract/` will fail CI if you violate the
  kernel-purity or frozen-schema guarantees. Treat a failure there as a design signal, not an obstacle to
  route around.

## Documentation-only changes

If you're here only to fix or extend documentation: everything under `docs/`, this file, and the root
`README.md` are fair game. Never hand-edit `docs/generated/**` (regenerate it instead), and never edit the
two `~/dev/_ops/plans/*.md` build/audit documents from this repo — they're immutable ground truth owned by
the home workspace.

Plan/ADR governance — freeze-on-approval plus the ADR **readiness gate** (a plan GO may accept an ADR only
from `ready-for-acceptance`, never a WIP one) — lives in [`docs/plans/README.md`](docs/plans/README.md) and
[`docs/adr/README.md`](docs/adr/README.md); the cross-consistency invariant is test-enforced by
`tests/unit/test_doc_governance.py`.

## Where things live (quick map)

| Area | Path |
|---|---|
| Deterministic kernel | `src/operator_voice/kernel/` |
| Mutation boundary (the only place kernel facts get written) | `src/operator_voice/application/service.py` |
| Operation registry (single source of truth for CLI/MCP/docs) | `src/operator_voice/domain/operations.py` |
| CLI / MCP / Skill entrypoints | `src/operator_voice/interfaces/` |
| Encrypted local persistence | `src/operator_voice/persistence/` |
| Install / XDG runtime / portable payload | `install.sh`, `src/operator_voice/runtime/` |
| Full documentation map | [`docs/README.md`](docs/README.md) |

<!-- doc-warden:begin -->
## Documentation governance

This repo's documentation structure is governed by `doc-warden`.

| Artifact type | Location |
|---|---|
| Plans | `docs/plans/` — index at `docs/plans/README.md` |
| ADRs | `docs/adr/` — index at `docs/adr/README.md` |
| Runbooks | `docs/runbooks/` |
| Glossary | `docs/GLOSSARY.md` (created when genuine terminology ambiguity arises) |

**Policy:** `~/dev/_ops/doc-warden/POLICY.md`
**Engine:** `~/dev/_ops/bin/doc-warden.sh check operator-voice` (read-only health check)
<!-- doc-warden:end -->
