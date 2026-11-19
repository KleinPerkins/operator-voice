# `docs/adr/` — architecture decision records

This directory holds durable architecture decisions — the "why," not the "how" (that's
`docs/plans/`). It follows a lightweight Nygard-style format consistent with
[`KleinPerkins/pr-automation-controller`](https://github.com/KleinPerkins/pr-automation-controller)
and the `doc-warden` convention (`~/dev/_ops/doc-warden/POLICY.md`, the fleet-wide source of truth).
This file is the local rule.

## The convention

1. **Header.** `# ADR NNNN: <title>` (4-digit, zero-padded, sequential — never reused, even if a
   later decision reverses an earlier one), then `Status: <status> — YYYY-MM-DD (optional context)`
   on the next line. Valid statuses: `proposed`, `accepted`, `superseded`, `deprecated`.
2. **Body.** An optional short intro paragraph, then `## Decision` (may have numbered/named
   subsections for a multi-part decision), then `## Why`, then `## Consequences`. Add `## Context`
   or `## Live-state verification record` when the decision depends on point-in-time ground truth.
3. **Immutability.** An accepted ADR is never rewritten to reflect new information — write a new,
   higher-numbered ADR that supersedes it (cross-link both directions) or, for a small in-scope
   correction, an inline `> **Update — ...**` blockquote at point of use. A `proposed` ADR that was
   never accepted may be rewritten in place before acceptance; git history is its record.
4. **ADRs vs. `docs/plans/`.** An ADR records a durable decision and its rationale (rarely
   changes once accepted). A plan document records the execution detail for *carrying out* work,
   including scope, sequencing, acceptance tests, and an owner GO/NO-GO gate. A plan may cite the
   ADR that authorizes its architectural shape; an ADR should not carry milestone-level file lists
   or test ledgers that belong in a plan.
5. **Readiness gate before acceptance (milestone/adapter work).** An ADR reaches `accepted` only
   through an explicit **ready-for-acceptance** gate. A plan GO may **never** auto-accept an ADR
   that is still incomplete/WIP — that would launder a half-formed decision straight to `accepted`.
   The three points on the path:
   - **`proposed` — not yet ready (WIP).** Sections are still being authored; `<!-- TODO -->`
     markers or open questions remain. A plan GO may **not** accept it. (doc-warden classifies such
     a skeleton `NEEDS_AUTHORING`.)
   - **`proposed` — ready-for-acceptance.** Every Nygard section (`## Decision`, `## Why`,
     `## Consequences`) is authored and final, no `TODO`/open-question markers remain, and the
     owner/author confirms the Decision/Why/Consequences are final enough to bind. This is the
     **only** state a plan GO may accept from. (doc-warden classifies such a complete draft
     `CONFORMANT`.) Optionally record the milestone in the Status parenthetical, e.g.
     `proposed — <date> (ready-for-acceptance <date>: sections final, owner-confirmed; awaiting plan <slug> GO)`.
   - **`accepted` — bound (terminal until superseded).** Acceptance is **co-requisite** with plan
     approval: the same PR that freezes the plan (`APPROVED — FROZEN`) flips every cited ADR's
     `Status:` to `accepted — YYYY-MM-DD (owner GO alongside plan vN)` and updates the index rows
     below. A plan may be complete and in review while its cited ADRs are still `proposed`; GO is
     **blocked** until every cited ADR is ready-for-acceptance.

   Ready-for-acceptance is a **checklist gate, not a new status token** (see the checklist below) —
   the Status line stays `proposed`, so this adds nothing to the `proposed`/`accepted`/`superseded`/
   `deprecated` vocabulary and stays uniform with `doc-warden` (whose canonical name for `proposed`
   is `draft`; the gate is identical under either name). ADR 0001/0002 were accepted at plan v2's GO
   and had cleared this bar (all sections final, owner-confirmed) at that time.
6. **Cursor-local plan files are not this.** Ephemeral `~/.cursor/plans/*.plan.md` files are not
   committed to this repository and are not a substitute for either an ADR or a `docs/plans/`
   document.
7. **Discoverability.** Read this file before adding a new architecture decision or trying to find
   out why an existing one was made.

## Readiness checklist (`proposed` → ready-for-acceptance)

Run this before a plan GO is allowed to accept a cited ADR. The ADR is ready **only** when ALL hold:

- [ ] `## Decision`, `## Why`, and `## Consequences` are all present and authored (no `<!-- TODO -->`).
- [ ] No unresolved open questions remain in the ADR body.
- [ ] Alternatives in `## Why` are each traced against real evidence, not left hypothetical.
- [ ] The decision is final enough to bind — the owner/author confirms it will not change before GO.
- [ ] Every ADR this one amends or supersedes is cross-linked in both directions.

An ADR that fails any item stays `proposed` (not ready) and a plan GO must not accept it. The gate
is enforced structurally by `tests/unit/test_doc_governance.py` at the end state: no
`APPROVED — FROZEN` plan may cite an ADR still in `proposed`/`draft`.

## Doc-warden eligibility (verified 2026-10-23)

`~/dev/_ops/doc-warden/POLICY.md` is doc-warden policy **v2.0.0, ACTIVE**, and
`~/dev/_ops/bin/doc-warden.sh` is installed. `operator-voice` is **repaired**: two `repair-approve`
receipts dated 2026-08-08 sit under `~/dev/_ops/doc-warden/state/operator-voice/`, and `AGENTS.md`
carries the `<!-- doc-warden:begin -->` block. Run the check with the path form,
`~/dev/_ops/bin/doc-warden.sh check "$PWD"`; it reports CONFORMANT. This repo keeps its local
vocabulary (`proposed`/`accepted`/`superseded`/`deprecated`); doc-warden's canonical name for
`proposed` is `draft`. The readiness gate above is a doc-warden-**compatible** local extension
(doc-warden is silent on a readiness/GO-acceptance gate today); it adds **no new status token**, and
**should be upstreamed into `doc-warden`** so the whole fleet inherits it.

## Index

The daily-maintenance successor [ADR 0007](0007-daily-voice-maintenance.md) replaces the
scheduling/weekly-offer portions of ADR 0001 and the channel-type v2 plan. Historical accepted
files remain byte-identical; this index supplies the reverse successor pointer.

| ADR | Status | One-line decision |
|---|---|---|
| [0007](0007-daily-voice-maintenance.md) | Accepted — 2026-10-30 | Operator-authorized daily host fetch, durable per-source completion, identity pins, on-demand local curation and measured profile stability. |
| [0001](0001-google-channel-adapters-agent-delegated-fetch.md) | Accepted — 2026-08-06 (owner GO alongside plan v2) | Agent-delegated Google fetch (zero OVT credentials); v1 Drive owner-only + Gmail Sent-only; adopt native API sync cursors over Slack's file-path heuristic. |
| [0002](0002-drive-gmail-materialize-identity-and-layout.md) | Accepted — 2026-08-06 (owner GO alongside plan v2) | Amends 0001 §2/§3: Drive/Gmail materialize one immutable timestamp-prefixed file per record (reuses Slack's cursor algorithm unchanged); Drive persists identity as `file_id` alone with revision as provenance, not a document-count-inflating dedup key; `drive.import` envelope gains `drive_id`/`owners[]` to make the shared-drive/multi-owner exclusion enforceable. |

## Related conventions

- Execution plans: [`docs/plans/README.md`](../plans/README.md).
- Slack precedent (agent-delegated fetch, no hardcoded MCP server names in OVT docs):
  [`docs/slack-adapter.md`](../slack-adapter.md).

## Successor source omissions

Private operational plans and ADRs are omitted from this source successor; their frozen originals remain privately preserved. See [source boundary](../source-successor.md) for the omitted paths and current implementation references. References from retained historical documents to omitted records identify private provenance, not missing public design approvals. The original repository registration and eligibility statements above are historical and do not register this successor.
