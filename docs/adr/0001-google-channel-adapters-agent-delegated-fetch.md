# ADR 0001: Google channel adapters — agent-delegated fetch and sync cursors

Status: accepted — 2026-08-06 (owner GO alongside plan v2)

## Context

OVT already ships an agent-delegated Slack voice path (`docs/slack-adapter.md`): the host agent
fetches via its own MCP connection; OVT only materializes, verifies authorship server-side, and
curates from disk. Google Workspace (Drive + Gmail) has the same constraint — no operator-local
"export this folder" artifact — but differs in incremental-sync ergonomics: Google APIs expose
native change tokens (`historyId` for Gmail, Changes API page tokens for Drive) whereas Slack v1
relies on search queries and a lexicographic **file-path** adapter cursor with a documented
monotonic-append limitation.

This ADR authorizes two new adapters (Drive before Gmail) and records cursor strategy before M1
implementation begins.

## Decision

### 1. Agent-delegated Google fetch — zero OVT credentials

OVT will **never** hold Google OAuth tokens, service-account keys, or API client libraries.

- **Stage 0 (fetch)** is exclusively agent-side: the host agent uses whatever Google workspace MCP
  or capability the operator's environment already provides (same policy wording as
  `docs/slack-adapter.md` — no specific server name in OVT docs or code).
- **Stage 1 (materialize)** is `drive.import` / `gmail.import`: JSON envelope in, server-side
  owner/author verification, local Markdown under fixed XDG export dirs.
- **Stage 2 (curate)** is `drive_export` / `gmail_export` adapters: passive file-tree scan only.

This matches the Slack rejection rationale: scheduled unattended harvest, embedded credentials, and
passive-path network I/O remain forbidden (`docs/product-requirements.md` privacy/egress invariants).

### 2. v1 scope boundaries

> **Update — 2026-10-23:** amended by [ADR 0004](0004-single-voice-profile.md) and
> [ADR 0006](0006-gmail-own-sent-text.md).

**Drive v1 — owner-only files**

- Materialize only entries where the operator is the sole human owner (agent supplies ownership
  metadata from host tools; OVT re-verifies `owner_id` per entry against the call's top-level
  `owner_id` before any write).
- Google Docs → taxonomy `channel=docs`, `output_type=doc`.
- Google Slides → `channel=slides`, `output_type=slide`.
- Other solely-owned text-exportable files → `channel=docs`, `output_type=doc` for v1 (no new
  canonical channel value required).
- **Out of v1:** shared drives, multi-owner files, comments-only, Sheets, OVT-side export API calls.

> **Update — 2026-08-06:** the concrete envelope fields that make the shared-drive/multi-owner
> exclusions above mechanically enforceable (not just narratively asserted) are specified in
> [ADR 0002](0002-drive-gmail-materialize-identity-and-layout.md) Decision 3.

**Gmail v1 — Sent mail only**

- Materialize only Sent messages; agent strips quoted reply bodies before submit when possible.
- Server-side verification: each message's sender must match top-level `author_email`.
- Taxonomy: `channel=email`, `output_type=email`.
- **Out of v1:** Drafts, Inbox, arbitrary labels, attachments binary ingest, send-as aliases not
  provably the operator.

Deferred scope is named as later milestones in the build plan (M3+), not silent roadmap drops.

### 3. Cursor strategy — adopt native API tokens; fix adapter cursor weakness

**Agent-side incremental fetch (adopt):**

| Source | Native cursor | Storage |
|---|---|---|
| Gmail | `historyId` from Gmail API (or host MCP equivalent) | `.import-state.json` in `gmail_export_dir`, read/written by `gmail.import`, exposed read-only in `gmail.status` |
| Drive | Changes API `newStartPageToken` / `changesPageToken` chain | Same pattern in `drive_export_dir` |

The agent passes the last known cursor in the import envelope; OVT persists the updated cursor
returned after successful materialization. OVT never calls Google to advance the cursor — it only
stores what the agent supplies, exactly like it stores `last_import_at` for Slack freshness.

**Adapter-side scan cursor (adopt — improvement over Slack v1):**

Slack's `slack_export` adapter uses a lexicographic file-path cursor. If a channel file gains new
messages after its path was passed, incremental `curate` misses them until a full re-scan
(`docs/slack-adapter.md` § "Adapter cursor: monotonic-append assumption").

Drive and Gmail adapters **will not** copy that limitation. They will cursor on the last processed
**record identity** (`source_native_id`: `<file_id>:<revision_id>` or `<message_id>`), ordered by
`(timestamp, source_native_id)`. New revisions/messages in an already-seen file sort after the
cursor and are picked up on the next incremental ingest.

> **Update — 2026-08-06:** the concrete on-disk mechanism for this — an ISO-8601-timestamp-prefixed
> immutable filename per record, reusing Slack's own unmodified sort/cursor algorithm — and a
> correction to persisted **document identity** (Drive dedups on `file_id` alone, not
> `file_id:revision_id` — the latter let ordinary editing inflate confidence via document-count
> alone) are specified in
> [ADR 0002](0002-drive-gmail-materialize-identity-and-layout.md) Decisions 1–2. Gmail's
> `message_id`-only identity above is unaffected and carries forward unchanged.

**Slack backlog (optional, not blocking M1/M2):** a future ADR may retrofit Slack to composite
record cursors; this ADR does not require Slack changes for Google delivery.

**Evaluation record — historyId / Changes API vs Slack file-position heuristic:**

| Approach | Verdict |
|---|---|
| Slack v1 file-path adapter cursor | **Declined for Google** — wrong tool; misses late appends within a file. |
| Gmail `historyId` for agent fetch | **Adopt** — purpose-built incremental mailbox sync; agent already uses host MCP. |
| Drive Changes API tokens for agent fetch | **Adopt** — purpose-built file change stream; avoids full-drive list on every refresh. |
| Persist Google cursors in OVT SQLite | **Declined** — keep cursors in content-free `.import-state.json` sidecars co-located with exports (Slack precedent); no new persistence tables. |
| OVT advances cursors by calling Google | **Declined** — violates agent-delegated fetch decision §1. |

## Why

- **Parity with Slack trust model:** Server-side verification remains the enforcement boundary; the
  agent proposes, OVT verifies, the kernel never trusts caller filtering.
- **Product fit:** Drive/Gmail are first-class MYNAH channels (`email`, `docs`, `slides`) already
  in `kernel/taxonomy.py`; shipping adapters unlocks cross-context voice without new kernel concepts.
- **Incremental sync:** Google native tokens reduce agent work and operator latency versus
  re-listing entire Drive/Sent mailbox each refresh; fixing adapter cursors prevents the known Slack
  v1 blind spot for multi-import files.
- **Lean v1:** Owner-only / Sent-only cuts authorship ambiguity that would otherwise require
  `kernel/authorship.py` quote-splitting and shared-ownership policies before value is proven.

## Consequences

- **Positive:** Two new sources follow one proven three-stage pattern; genuine cursor improvement
  over Slack; no credential surface in OVT; doc-warden plan/ADR trail for implementers.
- **Negative:** Agents must implement Google fetch recipes (documented generically in adapter
  docs); operators with heavy shared Drive or non-Sent mail must wait for M3+ milestones.
- **Neutral:** `docs/generated/` and semver bump happen in implementation PRs, not this plan PR.
- **Follow-on:** Accepting this ADR + approving plan v1 unblocks M1 (`drive.*`) implementation PR;
  M2 (`gmail.*`) follows M1 merge. Supersession of any decision here requires ADR 0002+, not plan
  edits alone.

**Supersedes:** none (first ADR in this repository).

**Relates to:** `docs/plans/gmail-drive-channel-adapters-v1.md`, `docs/slack-adapter.md`.
