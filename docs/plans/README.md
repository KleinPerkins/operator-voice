# `docs/plans/` — planning-document convention

This directory holds every substantial execution/design plan for this repository (a milestone
build-out, a new adapter family, a corrective/remediation pass). The convention follows the
`doc-warden` pattern (`~/dev/_ops/doc-warden/POLICY.md`, the fleet-wide source of truth) practiced
across KleinPerkins repos (see `KleinPerkins/pr-automation-controller` for the reference
implementation). This file is the local rule.

## The convention

1. **Header.** Title as an H1, then a bare bullet list directly under it — no `## Metadata`
   heading:
   - `- **Version:** vN`
   - `- **Status:** DRAFT (for review) | APPROVED — FROZEN (immutable) | COMPLETE | ...`
   - `- **Date:** YYYY-MM-DD`
   - Optionally `**Supersedes:**` / `**Relates to:**` / `**Review provenance:**` bullets, as
     needed.
2. **Naming.** `<topic-slug>-vN.md` (e.g. `gmail-drive-channel-adapters-v1.md`). Milestone-scoped
   topics may use an `m<N>-` prefix when helpful (e.g. `m6-drive-adapter-v1.md`).
3. **Freeze-on-approval.** Once a version is owner-approved (or superseded by a newer version),
   it is **frozen immutable** — never edit it again, including for typos. A revision is always a
   **new** `-v<N+1>.md` file, preserving the prior version byte-identically. State the pointer
   explicitly in the new file's `**Supersedes:**` bullet and update the index file's row; the
   superseded file itself is never modified (byte-identical-forever — no status-line edits).
   Before owner GO, a `DRAFT (for review)` version may be revised in place; git history is its
   record.
   **Plan GO bundle & readiness precondition.** Explicit owner GO on a plan version accepts every
   ADR the plan cites, in the **same PR** as the plan freeze (per
   [`docs/adr/README.md`](../adr/README.md)). GO is offered **only** once every cited ADR is
   **ready-for-acceptance** (structurally complete + owner-confirmed — see the ADR readiness gate);
   a plan may sit at `DRAFT (for review)` while its cited ADRs are still `proposed`, and GO stays
   **blocked** until each one is ready. A plan GO must never accept a still-WIP ADR.
   **The GO / readiness ceremony lives in these READMEs (and `docs/adr/README.md`), never in a
   re-opened frozen plan body.** Once a plan is `APPROVED — FROZEN` its body is byte-frozen; author
   the GO gate completely before freeze and record any later acceptance/readiness detail here in the
   mutable index/convention, which future plans inherit — do not reach back and edit a frozen plan.
4. **Body shape** (follow the `gmail-drive-channel-adapters-v1.md` lineage for adapter/milestone
   plans): purpose and boundary → live-state verification (ground the plan in current `main` first)
   → architectural precedent (what this mirrors) → numbered milestones with acceptance tests →
   later/deferred milestones → non-goals → owner GO/NO-GO gate → cross-references (ADRs, adapter
   docs, generated cards). *(Corrected 2026-08-06: this point previously listed precedent before
   live-state verification, the reverse of what the named exemplar actually does — ground in
   verified reality first, then map that reality onto precedent.)*
5. **Discoverability.** Any agent or reviewer starting work in this repo should read this file
   before writing a new plan or acting on an old one.
6. **Relationship to the home-workspace build plan.** The immutable product design intent for OVT
   as a whole lives at `~/dev/_ops/plans/operator-voice-toolkit-build-plan-v4.md` — **not** in this
   directory. Earlier versions bumped one unversioned filename in place, which predates and differs
   from this repo-local doc-warden freeze-on-approval convention; plan
   `channel-type-voices-bounded-context-v2` §9 plans its successor as a separate `-v5.md` file.
   Treat the home-workspace plan as authoritative *product* scope; treat `docs/plans/` here as
   authoritative *execution* records for discrete deliverables within this repository.
   Do not edit the home-workspace plan from this repo.

## Doc-warden eligibility (verified 2026-10-23)

- **Policy path:** `~/dev/_ops/doc-warden/POLICY.md` — doc-warden policy **v2.0.0, ACTIVE**.
- **CLI:** `~/dev/_ops/bin/doc-warden.sh` — **installed**. `operator-voice` is **repaired**: two
  `repair-approve` receipts dated 2026-08-08 sit under `~/dev/_ops/doc-warden/state/operator-voice/`.
  Run the check with the path form, `~/dev/_ops/bin/doc-warden.sh check "$PWD"`; it reports
  CONFORMANT.
- **Enrollment:** `AGENTS.md` carries the `<!-- doc-warden:begin -->` block. The ADR **readiness
  gate** added alongside this file is doc-warden-compatible (adds no status token) and should be
  upstreamed to doc-warden.

## Index

Current maintenance policy is defined by [daily voice maintenance v1](daily-voice-maintenance-v1.md).
It supersedes only the weekly-offer/hourly-scheduling provisions of the historical plans below;
remaining channel-type-voice milestones are outside that maintenance release.

| File | Status | One-line description |
|---|---|---|
| [`daily-voice-maintenance-v1.md`](daily-voice-maintenance-v1.md) | APPROVED — FROZEN (immutable) | Authorized daily connected-host cycle, durable checkpoints, stable generations and conditional publication; retires weekly offers and the independent hourly timer. |
| [`gmail-drive-channel-adapters-v1.md`](gmail-drive-channel-adapters-v1.md) | Superseded by v2 (now historical) | M1 Drive + M2 Gmail agent-delegated channel adapters (owner-only Drive, Sent-only Gmail), mirroring the Slack three-stage pattern. |
| [`gmail-drive-channel-adapters-v2.md`](gmail-drive-channel-adapters-v2.md) | APPROVED — FROZEN (immutable) | v1 + `/swarm`-reviewed corrections: immutable timestamp-prefixed materialize layout, `file_id`-only Drive document identity, `drive_id`/`owners[]` ownership-schema precision, doc-parity and acceptance-test completeness. See [ADR 0002](../adr/0002-drive-gmail-materialize-identity-and-layout.md). |

## Related conventions

- `docs/adr/` (architecture decision records) has its own equivalent convention/index file —
  [`docs/adr/README.md`](../adr/README.md).
- Living product reference (not plans): [`docs/README.md`](../README.md),
  [`docs/slack-adapter.md`](../slack-adapter.md) (precedent for agent-delegated fetch).

## Successor source omissions

Private operational plans and ADRs are omitted from this source successor; their frozen originals remain privately preserved. See [source boundary](../source-successor.md) for the omitted paths and current implementation references. References from retained historical documents to omitted records identify private provenance, not missing public design approvals. The original repository registration and eligibility statements above are historical and do not register this successor.
