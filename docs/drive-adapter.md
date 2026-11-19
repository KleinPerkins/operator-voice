# Drive voice capture (agent-delegated fetch, owner-only, no OVT credentials)

**Audience:** both. **Policy:** the explicitly enrolled daily connected-host cycle (or an explicit refresh request) fetches solely owned Google Docs in registered folders and their subfolders, then OVT verifies and materializes locally. OVT holds no connector credentials and never fetches from its kernel or local curator. [ADR 0007](adr/0007-daily-voice-maintenance.md) replaces the former weekly-offer and independent hourly policies.

## Why this shape (policy, not just mechanism)

Slack, Gmail and Google Docs are the retained sources. Agents fetch through their existing host
tools; OVT reads local materialized files. OVT never receives credentials to fetch on a schedule.

- **Zero OVT Google credentials.** No OAuth client, refresh/access token, service account, API key,
  or Google SDK/CLI dependency anywhere in this package (`pyproject.toml` adds none).
  `operator_voice.drive.materialize` and `operator_voice.adapters.drive_export` import nothing from
  any Google library — they only read/write local Markdown. **Stage-0 fetch is delegated entirely to
  the calling agent's host Drive tools.**
- **No calling any Drive tool from the OVT CLI, from `local-jobs`/launchd, or from anywhere in the
  passive `curate` path.** That path stays network-free, exactly like every other adapter's
  scheduled re-scan. All Drive access happens **agent-side**, per the workflow below, never
  programmatically from this package.
- **No auto-harvest without an agent in the loop.** OVT can tell an agent that Drive voice data is
  stale (`drive-status`); it cannot go get fresh data itself.
- **Fresh identity with an enrollment pin.** Resolve the connected account on every refresh.
  Daily enrollment HMAC-pins it in the encrypted control journal; raw account ids are not stored there.

Mechanically, once files are captured, Drive voice data flows through the **exact same** pipeline as
every other source: a `drive_export` adapter registered like any other (see
[System mechanics](system-mechanics.md#2-adapter-scan--observationcandidate)), picked up by the same
daily cycle's `curate` pass, subject to the same authorship gating, synthesis, and review. Nothing about
retrieval, drafting, or correction is Drive-specific past the two operations below and the two
Drive-only wrinkles this doc calls out explicitly: **owner-only server-side verification** and the
**supersede-if-newer** revision model.

## Operations

| Operation | Group / Posture | Does |
|---|---|---|
| `drive.folder_add` / `drive.folder_remove` (CLI: `drive-folder_add` / `drive-folder_remove`) | `MUTATION` / `REVERSIBLE` | Register or remove an operator-selected folder. The sorted list lives in the fixed Drive source's `role_identity`; run `setup` first. |
| `drive.import` (CLI: `drive-import`) | `MUTATION` / `REVERSIBLE` | Accepts a batch of the operator's solely owned Google Docs in registered folders (agent-fetched), verifies ownership **server-side**, deduplicates, and materializes each revision as its own immutable Markdown file. Also persists the agent-supplied Drive sync cursor. |
| `drive.status` (CLI: `drive-status`) | `READ` / `SAFE` | Content-free freshness snapshot: per-`file_id` revision counts, the persisted sync cursor to resume from, when `drive.import` last ran, and a stale/fresh verdict. `--brief` prints one plain-text line instead of full JSON (for a session-start hook). |

Full argument/error/alias reference (auto-generated, never hand-edited):
[`generated/cards/drive-import.md`](generated/cards/drive-import.md),
[`generated/cards/drive-status.md`](generated/cards/drive-status.md).

### `drive.import`'s request shape

```json
{
  "owner_id": "operator@gmail.com",
  "sync_cursor": {
    "changes_page_token": "12345",
    "new_start_page_token": "67890"
  },
  "files": [
    {
      "file_id": "1abc…",
      "revision_id": "rev-7",
      "drive_id": null,
      "owners": ["operator@gmail.com"],
      "name": "Q3 decision memo",
      "mime_type": "application/vnd.google-apps.document",
      "modified_time": "2026-07-30T14:02:00Z",
      "owner_id": "operator@gmail.com",
      "folder_id": "selected-folder",
      "via_shortcut": false,
      "text": "plain text body exported agent-side"
    }
  ]
}
```

`drive_id` and `owners` use Google's own Drive API v3 field names (`driveId`/`owners`) verbatim, so
an agent maps host-tool output directly with no OVT-specific translation. Pass the envelope inline
(`--owner-id` + `--files` + optional `--sync-cursor`), via `--file <path>`, or over stdin if neither
`--file` nor `--files` is given (the CLI reads and parses the same envelope shape from stdin as a
convenience for agents that already have it in memory).

### Server-side owner + not-shared-drive verification — the hard invariant

`owner_id` at the top level is the operator's own Drive id, **self-asserted for this call**.
`drive.import` re-verifies every file **before writing anything to disk**, fail-closed — a missing
required key is rejected exactly like a present-but-disqualifying value, nothing is inferred:

- **Not a Shared Drive.** `drive_id` must be present-and-`null` (a My-Drive item). A non-null
  `drive_id` — **or a missing `drive_id` key** — is dropped under `skipped_shared_drive`. An agent's
  omission is never treated as proof the item is in My Drive.
- **Sole ownership.** `owners` must be exactly `[owner_id]`. `len(owners) != 1`, `owners[0] !=
  owner_id`, **or a missing `owners` key** → dropped under `skipped_shared_ownership`.
- **Owner match.** The entry's own `owner_id` must equal the top-level `owner_id`, else dropped under
  `skipped_mismatched_owner`.
- **Google Docs only.** Other MIME types, including Slides, PDFs and folder shortcuts, are counted
  as `skipped_not_google_doc`.
- **Registered folders only.** The entry's `folder_id` must be registered; otherwise it is counted
  as `skipped_unregistered_folder`. It names the registered ancestor under which the agent found
  the Doc or Doc shortcut, including within subfolders.

This holds even if the calling agent's own filtering was itself wrong (e.g. a bad `changes.list`
query pulled in a shared file) — the checks are server-side and cannot be bypassed by what the agent
believes it already filtered. Each record is verified against the identity asserted for this call;
this does not pin identity across calls (see the Slack adapter's
[server-side author verification](slack-adapter.md#server-side-author-verification--the-one-hard-invariant)).
See `tests/unit/test_drive_materialize.py` (reject-mismatched-owner, reject-shared-drive,
reject-multi-owner, fail-closed-on-missing-key) and `tests/integration/test_drive_import.py` for the
explicit rejection tests.

## Agent workflow (stage 0 — generic, host-agnostic)

There is no OVT-native Drive fetch. When an agent decides it's time to refresh (see
[When to refresh](#when-to-refresh) below), it drives the operator's **own host Drive integration**
(whatever read-only Drive access the operator's environment provides — this doc names no specific
tool or server on purpose; use only read-only, fail-open Drive access, never a mutating one):

1. `operator-voice drive-status` — check current freshness and read back the last persisted
   `sync_cursor` (read-only).
2. Obtain the operator's own Drive identity fresh from the host Drive tools (the account/profile id
   the host reports for the current user) — this is `owner_id`. Never hardcode it, never reuse a
   value from a previous session, never read it from OVT config.
3. Ask which folders hold the operator's own writing and register only their choices using
   `drive-folder_add --folder-id <id>`. Keep selected ids privately for this refresh; `drive-status` reports the registered count.
   Ask the operator for the folder selection when its ids are not available. Enumerate each
   selected, registered folder and its subfolders. Keep only Google Docs and shortcuts to
   Google Docs. Resolve a Doc shortcut to its target and apply sole-owner and My-Drive checks to
   the **target's** own metadata; send its target id, revision, MIME type and text with
   `via_shortcut: true`. Never follow folder shortcuts. Every entry supplies `folder_id` as the
   registered ancestor where the Doc or shortcut was found, even several levels down.
   A valid Changes cursor may narrow this refresh only within the registered-folder boundary;
   record the returned sync cursor for the next refresh.
4. Assemble the `{owner_id, sync_cursor, files}` envelope, populating `drive_id` and `owners` for
   **every** entry, and call `operator-voice drive-import --file …` (or `--files`/stdin).
5. Either call `operator-voice ingest --source-id <the drive_export source>` immediately, or let the
   daily cycle's `cycle-finish` curate it — both are safe (`file_id`-level dedup and supersede-if-newer
   make a re-scan a no-op for anything already seen at the same-or-older revision).

### Full sync on cursor expiry (issue #37)

Drive's `changes.list` page token can **expire** (Google returns an error asking the client to start
over). When that happens the agent must **fall back to a FULL sync**: discard the stored
`changes_page_token`, obtain a fresh `startPageToken`, and re-enumerate all registered folders and their subfolders
from scratch, keeping only eligible Google Docs and resolved Doc shortcuts, then resume incremental `changes.list` from
the new token on subsequent refreshes. OVT stores whatever `sync_cursor` the agent supplies verbatim
and never interprets it — the expiry detection and full-sync fallback are **agent-side**, exactly
because OVT holds no Google credentials and makes no Google calls. A full sync is always safe on the
OVT side: re-importing an unchanged file at the same revision is a `skipped_duplicate` on disk and a
no-op supersede in persistence; only a genuinely newer `modified_time` updates anything.

The agent never stores a Google token or credential, never scrapes Shared Drives or other people's
files, and never needs to decide "is this really mine and not shared" on its own — step 4's
server-side checks are the actual enforcement boundary, not agent judgment.

## When to refresh

When the host lacks Changes tools, exhaustively enumerate the same selected folder scope with
pagination and compare actual modified times against `capture-inventory`. Never fabricate a Changes
token; a capped or unfinished traversal is incomplete.

The enrolled host runs the daily fetch → durable import → curation → conditional publication
cycle at 18:00 in the configured timezone. A 15-minute due check handles a missed run and one
transient retry; an idle check does not fetch. An explicit refresh uses the same workflow.
Drafting never fetches or offers a weekly refresh.

A completed empty fetch is fresh. A partial or failed fetch does not advance the daily completion
checkpoint; 48 hours without complete success requires attention. Authentication or enrolled
account mismatch requires immediate attention. The old local-jobs timer is on-demand only.
See the [daily runbook](runbooks/daily-voice-maintenance.md) and the portable
[skill workflow](../src/operator_voice/skill/SKILL.md#daily-voice-maintenance).

## On-disk format — immutable, one file per revision

`drive.import` writes **one immutable Markdown file per revision** (never appended to after creation)
under the fixed `drive_export_dir` (`RuntimePaths.state_dir / "drive-export"`, created automatically
by `setup`/`paths.ensure()`), named `<modified_time_iso>__<file_id>__<revision_id>.md` (colons in the
timestamp replaced with `-` for filesystem portability). Each file starts with a machine-parseable
HTML comment carrying Drive's own opaque ids, the `modified_time`, and the agent-supplied `mime_type`
verbatim:

```markdown
# Drive voice capture

Operator-owned, non-Shared-Drive files only. Server-verified ownership (see
`operator_voice.drive.materialize`). Generated by `operator-voice drive-import`.

<!-- ovt:drive file=1abc… rev=rev-7 modified=2026-07-30T14:02:00Z mime=application/vnd.google-apps.document -->
## Q3 decision memo

plain text body exported agent-side
```

`operator_voice.drive.materialize.parse_export_file` is the **single** parser for this shape, shared
by the `drive_export` adapter (scan) and `drive.status` (counts) — there is exactly one place that
understands this format. Only `application/vnd.google-apps.document` records are learned, with `channel=docs` and
`output_type=doc`. Other types are filtered as `not_google_doc`, including old Slides records.

Folder membership lives separately in `.folder-membership.json`: each file id maps to exactly
`folder_id` and the boolean `via_shortcut`, with no text. The record marker and body remain compatible
with 0.1.9. An old Doc without membership is filtered as `unregistered_folder`; fetching the same
unchanged revision from a registered folder backfills membership even when `skipped_duplicate` is
reported, without rewriting its record. Removing a folder also excludes its records on future scans.

### Identity is `file_id` alone; revision is provenance (ADR 0002 Decision 2)

Unlike Slack/Gmail — where each message is a genuinely independent, immutable utterance — one Drive
file is **one evolving voice-evidence unit**, refreshed in place. So the **persisted** document
identity (`source_native_id`, the key `persistence/store.py::upsert_document` dedups on) is
**`file_id` alone**, never `file_id:revision_id`. `revision_id` and `modified_time` ride along as
**provenance** (`Provenance.native_revision`), not identity. This deliberately prevents three quick
re-saves of one memo from inflating `doc_count` and promoting a facet to retrieval-eligible on
edit-count alone — a trap `kernel/calibration.py`'s `min_documents_for_supported` would otherwise
fall into. See [ADR 0002](adr/0002-drive-gmail-materialize-identity-and-layout.md) for the full
rationale.

### Supersede-if-newer (the one persistence UPDATE path)

Because identity is `file_id` alone, a second revision of the same file collides on the dedup key.
`upsert_document` therefore gains **one narrow, additive UPDATE path**, reachable only from Drive's
own ingest (only Drive candidates carry a `native_revision`): when a stored document for that
`file_id` already exists, it compares the incoming `modified_time` against the stored one and updates
content + re-extracts stylometric signals **only if the incoming `modified_time` is strictly
greater** — deleting the superseded revision's observations so they never linger and inflate
confidence. An equal-or-older `modified_time` (e.g. an out-of-order agent submission) is a **no-op**,
never a downgrade. `revision_id` is an **opaque, non-ordinal** token (per Google's Drive API v3
`revisions` reference) and is **never** used to decide which revision is newer — only `modified_time`
is. The superseded revision's immutable on-disk file is preserved (never deleted) for the deferred M3
revision-diffing milestone to read directly. See
`tests/integration/test_drive_import.py::test_drive_import_supersedes_only_when_newer_end_to_end`
(the strictly-newer update) and `::test_drive_import_not_newer_revision_is_a_noop_end_to_end` (the
equal/older no-op).

## Adapter cursor: immutable records retain lexical ordering

Drive retains a lexical file-path cursor. Files are filtered after the cursor before the scan cap
is applied, so bounded continuation reaches the remaining immutable revision records. Slack uses a
separate change-ordered watermark for growing channel files.

**Residual, accepted risk (documented, not hidden):** if an agent submits records out of strict
timestamp order across two different `drive.import` calls (e.g. a retried or re-paginated fetch), a
record whose `modified_time` sorts *before* the current cursor is silently missed by the next
incremental `curate` tick until a full rescan (`ingest`/scan with no cursor). This is narrower than
Slack's blind spot (which triggers on *any* append to an old file) and is judged an acceptable v1
residual, exactly as Slack's own documented limitation. See
`tests/unit/test_adapters.py::test_drive_export_adapter_respects_cursor_across_multiple_files` and
`::test_drive_export_cursor_resumes_onto_a_newer_revision_no_slack_blind_spot`.

## Setup and conformance

- A fresh `operator-voice setup` idempotently registers a `drive_export` source pointed at
  `drive_export_dir`, alongside the on-demand curation registration and the `slack_export` source — see
  the `drive_source` block in `setup`'s own JSON result. Running `setup` again never duplicates the
  registration.
- `drive_export` is held to the exact same shared adapter contract as every other source
  (registration guard, canonical-path safety, resource ceilings, determinism, quarantine-not-abort,
  no persistence coupling) via `adapters/conformance.py` — see `tests/unit/test_adapter_conformance.py`
  and `tests/unit/test_adapters.py`.

## Cross-references

- Exact CLI/MCP arguments and errors: [`generated/cards/drive-import.md`](generated/cards/drive-import.md),
  [`generated/cards/drive-status.md`](generated/cards/drive-status.md).
- The design decisions behind identity, on-disk layout, and ownership schema:
  [ADR 0002](adr/0002-drive-gmail-materialize-identity-and-layout.md) and the frozen build plan
  [`plans/gmail-drive-channel-adapters-v2.md`](plans/gmail-drive-channel-adapters-v2.md) §3 (M1).
- How this fits the rest of the ingestion pipeline: [`system-mechanics.md`](system-mechanics.md).
- Day-to-day commands: [`operating-procedures.md`](operating-procedures.md).
- Everything this feature explicitly does **not** do: see [Why this shape](#why-this-shape-policy-not-just-mechanism)
  above (zero credentials, no auto-harvest) and owner-only v1 scope (shared/co-authored files are M3).
