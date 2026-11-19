# ADR 0002: Drive and Gmail materialize — record identity, scan-cursor layout, and ownership schema

Status: accepted — 2026-08-06 (owner GO alongside plan v2)

**Amends:** [`0001-google-channel-adapters-agent-delegated-fetch.md`](0001-google-channel-adapters-agent-delegated-fetch.md)
§2 (Drive ownership-exclusion mechanism) and §3 (Drive record-identity specifics only — Gmail's
`historyId` agent-fetch cursor and the overall zero-credential, agent-delegated-fetch decision are
**unchanged** and fully carried forward). ADR 0001 is not rewritten; two short `> **Update**`
pointers at its amended points cross-link here per `docs/adr/README.md` point 3.

**Relates to:** `docs/plans/gmail-drive-channel-adapters-v2.md`, `docs/slack-adapter.md`.

## Context

A `/swarm` peer review (Deep band, 4 reviewers) of build-plan v1 mechanically traced v1's one claimed
improvement over Slack — a "composite adapter cursor... ordered by `(timestamp, source_native_id)`"
(v1 §5.2) — against the live adapter contract (`adapters/base.py`, `adapters/slack_export.py`) and
found it was **stated but not specified**: no on-disk file-grouping granularity was named, so the
claim silently presupposed either (a) an unstated, materially more expensive parse-every-file-then-
sort scan algorithm, or (b) per-record immutable files, which alone still leaves opaque Google IDs
(`revision_id`, `message_id`) sorting in no reliable relationship to chronological order. Independently,
the same review found that v1's `file_id:revision_id` materialize-dedup key — mechanically traced
through `persistence/store.py::upsert_document` (exactly-once, insert-only, no update path) and
`kernel/synthesis.py`/`kernel/calibration.py` (`min_documents_for_supported=3`, no revision-collapsing)
— lets ordinary editing of one Drive file alone promote a facet to retrieval-eligible `SUPPORTED`
purely through edit-count, not independent evidence. A third, independently-confirmed finding: the
`drive.import` envelope (v1 §3.4) carries only a flat `owner_id` string, which cannot express either
"not a Shared Drive item" or "no other human owners" — the two conditions v1 §3.1 and ADR 0001 §2
already promise to enforce. All three findings trace to the same root: **v1 correctly decided *that*
Drive materialize should differ from Slack's, but under-specified *how*.** This ADR closes that gap.

## Decision

### 1. On-disk layout: one immutable file per record, chronologically-sortable filename

> **Update — 2026-10-23:** amended by [ADR 0006](0006-gmail-own-sent-text.md).

Drive and Gmail materialize **one file per record, never appended to after creation** — a sharp
departure from Slack's growable per-channel file:

| Source | Filename pattern | Example |
|---|---|---|
| Drive | `<modified_time_iso>__<file_id>__<revision_id>.md` | `2026-07-30T14-02-00Z__1abc...__rev-7.md` |
| Gmail | `<internal_date_iso>__<message_id>.md` | `2026-07-30T14-02-00Z__18c2....md` |

(Colons in the ISO-8601 timestamp are replaced with `-` for filesystem portability; the timestamp
always sorts first.)

**Why this, not a new scan algorithm:** because the leading path component is a zero-padded ISO-8601
timestamp, plain lexicographic filename order **is** chronological order. `adapters/drive_export.py`/
`gmail_export.py` therefore reuse **Slack's exact, unmodified cursor algorithm**
(`adapters/slack_export.py`'s sorted-`iter_files_within` → `[f for f in files if str(f) > cursor]` →
`next_cursor = str(files[-1])`) — no parse-everything-every-scan restructuring, no new comparator. A
file is either fully new (picked up once, immutable thereafter) or already-seen (permanently behind
the cursor, correctly, because it can never change again). This is **strictly better** than Slack's
own growable-file cursor, which has a real monotonic-append blind spot (`docs/slack-adapter.md` §
"Adapter cursor") — the immutable-file design has none, because there is nothing left to append to.

**Residual, accepted risk (documented, not hidden — same honesty bar as Slack's own limitation note):**
if an agent submits records out of strict timestamp order across two different `drive.import`/
`gmail.import` calls (e.g. a retried or re-paginated fetch), a record whose timestamp sorts *before*
the current cursor will be silently missed by the next incremental `curate` tick until a full rescan.
This is narrower than Slack's blind spot (which triggers on *any* append to an old file, not just
out-of-order agent submission) and is judged an acceptable v1 residual risk, exactly as Slack's own
documented limitation was.

### 2. Document identity: `file_id` alone; revision is provenance, not identity

Drive's **persisted** document identity (`ObservationCandidate.source_native_id`, the key
`persistence/store.py::upsert_document` dedups on) is **`file_id` alone** — not `file_id:revision_id`
as v1 stated. One Drive file is one evolving voice-evidence unit, refreshed in place; it is not a
stream of independent utterances the way Slack messages or Gmail messages genuinely are.

- `revision_id` is still captured, but as **provenance, not identity** — via the existing
  `domain/models.py::Provenance.native_revision` field (already defined, already documented for
  exactly this case: *"other sources pin native revision/ID plus digest"* — unused by any adapter
  today). No new dataclass field is invented.
- `persistence/store.py` gains one narrow, additive capability: a **supersede-if-newer** path,
  reachable only from `drive_import`'s own materialize/ingest call — when a candidate's `file_id`
  already has a stored document, compare the new candidate's `modified_time`/`revision_id` ordinal
  against the stored one and **update content + re-extract signals only if strictly newer** (never
  unconditionally overwrite — this is what keeps the out-of-order-arrival residual risk in §1 from
  silently corrupting already-good state). Superseded revisions remain readable on-disk (§1's
  immutable per-revision files are never deleted) for the deferred M3 "automatic revision diffing"
  milestone to read directly — this decision does not foreclose M3, it just stops revision churn
  from being *persisted, evidence-counted document rows* today.
- Gmail is **unaffected** by this decision: each Gmail message is a genuine, immutable, independent
  utterance (matching Slack's own model), so Gmail's dedup key stays `message_id` alone, exactly as
  v1 already specified.

> **Update — 2026-08-06:** The supersede-if-newer comparator is **`modified_time` only** — it is the
> authoritative, reliably time-ordinal signal. Drive's `revision_id` is an **opaque** token with **no
> guaranteed lexical or numeric ordering** (per Google's Drive API v3 `revisions` reference), so it
> must **not** be used to decide "which revision is newer"; it is retained purely as provenance
> (`Provenance.native_revision`). Concretely, on a `file_id` collision the ingest path updates content
> + re-extracts signals **only if** the candidate's `modified_time` is **strictly greater** than the
> stored document's, and treats an equal-or-older `modified_time` as a no-op. This refines the
> "`modified_time`/`revision_id` ordinal" wording in the bullet above (and the parallel "revision is
> strictly newer" phrasing in frozen plan `gmail-drive-channel-adapters-v2.md` §5.2/§3.4); the
> decision itself — `file_id`-only identity, revision-as-provenance — is unchanged. Inline correction
> per [`docs/adr/README.md`](README.md) point 3; Status is unchanged (`accepted`).

**Why not just accept revision-accumulation:** `kernel/calibration.py`'s `min_documents_for_supported`
is `3` and `single_source_confidence_cap` (`0.8`, applied only when `min_sources_for_full_confidence`
isn't met) *dampens* but does not *prevent* three quick re-saves of one memo alone promoting a facet
into `RETRIEVAL_ELIGIBLE_STATES` — a materially different accumulation semantic than every other
adapter's genuinely-independent-utterance model, discovered by mechanically tracing `doc_count`
through `kernel/synthesis.py::synthesize_fact`. Fixing identity at the source is simpler and more
durable than adding file_id-awareness to kernel synthesis (which would either require parsing the
opaque `source_native_id` string in the kernel — breaking kernel purity's opaque-id treatment — or a
schema change to `SignalObservation`/`SourceDocument` themselves; both are larger than this fix).

### 3. Ownership-schema precision: `drive_id` and `owners[]`, fail-closed

> **Update — 2026-10-23:** amended by [ADR 0004](0004-single-voice-profile.md).

`drive.import`'s per-file envelope (v1 §3.4) gains two required fields, named after Google's own Drive
API v3 field names so an agent maps them directly with no translation judgment call:

- **`drive_id`** (nullable) — mirrors Google's `driveId` (*"Output only. ID of the shared drive the
  file resides in. Only populated for items in shared drives"* — externally verified against
  `developers.google.com/workspace/drive/api/reference/rest/v3/files`). Non-null/non-empty →
  reject as a new **`skipped_shared_drive`** outcome. A **missing** `drive_id` key is *also*
  rejected, fail-closed — an agent's omission is never treated as proof the item is in My Drive.
- **`owners`** (a list, e.g. `["operator@gmail.com"]`) — replaces the single flat `owner_id` for the
  ownership check itself (`owner_id` stays as the top-level self-asserted identity, matching Slack's
  `author_id` pattern unchanged). `skipped_shared_ownership` triggers whenever `len(owners) != 1` or
  `owners[0] != <top-level owner_id>`. A missing `owners` key is rejected fail-closed, same rule.

This is the concrete enforcement mechanism for a policy ADR 0001 §2 already decided ("Out of v1:
shared drives, multi-owner files") but could not actually implement — the old single-`owner_id`-string
schema had no field capable of expressing either exclusion.

## Why

- **Simplicity over cleverness:** §1's fix is a *filename convention*, not a new algorithm — it makes
  Drive/Gmail's scan cheaper and simpler than the "parse everything, sort, filter" alternative the
  review identified as the literal-reading consequence of v1's own words, while still delivering the
  genuine improvement over Slack's blind spot that ADR 0001 intended.
- **Fix identity at the source, not confidence math at the sink:** §2 keeps `kernel/synthesis.py` and
  `kernel/calibration.py` completely untouched (no kernel changes, preserving kernel purity and v1's
  own non-goal of "no kernel or persistence changes beyond new adapter candidates" in spirit — the one
  persistence addition is a narrow, additive supersede path, not a schema rework), eliminating the
  confidence-inflation risk at the row that would otherwise inflate `doc_count`, rather than adding
  compensating logic downstream.
- **Field names an agent can map without guessing:** §3 borrows Google's own REST API field names
  verbatim rather than inventing OVT-specific ones, minimizing agent-side translation ambiguity.

## Consequences

- **Positive:** The composite-cursor claim in ADR 0001 §3 is now genuinely implementable at Slack's
  own cost profile, not a hidden O(total-records)-per-tick liability; Drive's confidence math can no
  longer be gamed by ordinary editing; the shared-drive/multi-owner exclusions ADR 0001 §2 promised
  are now mechanically enforceable, not just narratively asserted.
- **Negative:** Drive materialize writes more on-disk files than a growable-per-container layout would
  (one per revision, never cleaned up in v1) — an accepted disk-usage/inode-count tradeoff for a
  single-operator local tool, and an asset for the deferred M3 revision-diffing milestone rather than
  waste. `persistence/store.py` gains one new narrow write path (supersede-if-newer) that v1's original
  design didn't need — the smallest capability that resolves Decision 2, not a general update mechanism.
- **Neutral:** Gmail's materialize/identity model is unaffected by Decisions 2–3 (both are Drive-only);
  Gmail still benefits from Decision 1's filename-layout fix.
- **Follow-on:** `docs/plans/gmail-drive-channel-adapters-v2.md` §3.3/§3.4/§5.2 specify these decisions
  as concrete M1 file-list and envelope changes. Superseding any decision here requires ADR 0003+, not
  plan edits alone.

**Supersedes:** none. **Amends:** ADR 0001 §2, §3 (Drive-specific points only, cross-linked there).

**Relates to:** `docs/plans/gmail-drive-channel-adapters-v2.md`, `docs/slack-adapter.md`,
`docs/adr/0001-google-channel-adapters-agent-delegated-fetch.md`.
