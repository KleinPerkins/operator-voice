# Gmail and Drive channel adapters — build plan (v2)

- **Version:** v2
- **Status:** APPROVED — FROZEN (immutable)
- **Date:** 2026-08-06
- **Supersedes:** [`gmail-drive-channel-adapters-v1.md`](gmail-drive-channel-adapters-v1.md) (frozen historical; byte-identical, never edited)
- **Review provenance:** `/swarm` peer review, Deep band, 4 reviewers (`eng-systems`, `dependency-assassin`,
  `doc-sharpener`, `first-principles`) against `origin/main` at `db9677c`. See §10.
- **Relates to:** [`docs/slack-adapter.md`](../slack-adapter.md) (precedent), ADR
  [`0001-google-channel-adapters-agent-delegated-fetch.md`](../adr/0001-google-channel-adapters-agent-delegated-fetch.md),
  ADR [`0002-drive-gmail-materialize-identity-and-layout.md`](../adr/0002-drive-gmail-materialize-identity-and-layout.md)
  (new — amends 0001 §2/§3), home-workspace `~/dev/_ops/plans/operator-voice-toolkit-build-plan.md` v4
  (product scope — do not edit from this repo)
- **Repository:** `KleinPerkins/operator-voice`

---

## 0. Purpose and boundary

Add **two** new agent-delegated channel adapters — **Google Drive first**, **Gmail second** —
using the same three-stage pattern already proven by the Slack adapter:

| Stage | Who | What |
|---|---|---|
| **0 — Fetch** | Host agent | Calls Google workspace tools via whatever MCP/capability the operator's environment already provides. OVT never holds Google tokens, OAuth clients, or API SDKs. |
| **1 — Materialize + verify** | OVT (`*.materialize` + `*.import` service handler) | Accepts an agent-submitted JSON envelope, verifies authorship/ownership **server-side**, deduplicates, writes local Markdown (or structured sidecars) under fixed XDG export dirs. |
| **2 — Curate** | OVT (`*_export` adapter → `ObservationCandidate` → kernel) | Passive hourly `curate` re-scans on-disk materialization only — no network, no Google calls. |

This plan is **documentation and sequencing only**. It authorizes no live mutation by itself. No
`docs/generated/` regeneration is required until `domain/operations.py` entries land in a
follow-on implementation PR.

**Hard constraints carried forward from Slack and product invariants:**

- No Google API SDK, OAuth flow, or credential storage anywhere under `src/operator_voice/`.
- No calling Google MCP/tools from OVT CLI, `local-jobs`, or the passive `curate` path.
- No materialize logic under `kernel/`; kernel purity (`tests/unit/test_kernel_purity.py`) unchanged.
- No hardcoded MCP server names in OVT docs — describe fetch generically (as
  [`docs/slack-adapter.md`](../slack-adapter.md) does for Slack).
- Authorship precedes learning: mismatched owner/author ids are **dropped**, never partially written.

**v2 carries forward every v1 decision unchanged except where explicitly marked "v2:" below.** Every
mark traces to a specific, code-verified swarm finding (§10) — v2 is a precision pass, not a rewrite.

**M1 blocking prerequisite (v2 — new, must land before or within the M1 PR):** `ObservationCandidate`
(`domain/models.py`) has no `output_type` field today, and `_ingest_source_within`
(`application/service.py` ~L1129) constructs each document's `ContextScope` from `channel` alone —
`output_type` silently defaults to `"text"` regardless of what an adapter intends. §3.6/§4.1's
`doc`/`slide`/`email` `output_type` tagging is **inert without this fix** — traced mechanically, not
theoretical. This is a small, generic fix (thread one more field through one existing code path) that
belongs in M1 because M1 is the first adapter that needs `output_type` to mean anything; see §3.3 and
D-M1-0.

---

## 1. Live-state verification (2026-08-06)

Verified against `origin/main` at `db9677c` (v1's plan + ADR-scaffolding PR, merged):

| Artifact | Current state |
|---|---|
| Slack precedent | Shipped: `slack/materialize.py`, `adapters/slack_export.py`, `slack.import` / `slack.status` in `operations.py`, `AdapterType.SLACK_EXPORT`, `RuntimePaths.slack_export_dir`, `_ensure_slack_source()` in `setup()`, docs at `docs/slack-adapter.md`. |
| Adapter registry | `adapters/registry.py` maps `AdapterType` → `scan`; conformance profiles + a per-adapter-type suffix map in `adapters/conformance.py` (v2: this suffix map is itself a new-adapter touchpoint — see §3.3, §5.4). |
| Taxonomy | `kernel/taxonomy.py` already includes `channel` values `email`, `docs`, `slides` and synonyms `gmail`→`email`, `gdoc`/`gdocs`→`docs`, `deck`/`presentation`→`slides`. |
| **`output_type` threading (v2 — new finding)** | `ObservationCandidate` has no `output_type` field; `_ingest_source_within` (`service.py` ~L1129) builds `ContextScope(channel=...)` only, so `output_type` always defaults to `"text"` today regardless of adapter intent. See §0 blocking prerequisite. |
| **`source.add` adapter-type choices (v2 — new finding)** | `operations.py` `source.add`'s `adapter_type` arg is a hardcoded `choices=(...)` tuple (~L333–336) that does **not** yet include `drive_export`/`gmail_export` — must be extended in each milestone's own PR or the CLI/MCP path rejects an otherwise-valid registration even after the adapter code ships. |
| **`upsert_document` is insert-only (v2 — new finding)** | `persistence/store.py::upsert_document` dedups exactly-once on `(source_id, source_native_id)` and returns early with no update on a repeat key — there is no update/supersede path today. Relevant to Drive's revision model; see §3.4, §5.2, ADR 0002 Decision 2. |
| **Calibration thresholds (v2 — new finding)** | `kernel/calibration.py::DEFAULT_CALIBRATION`: `min_documents_for_supported=3`, `single_source_confidence_cap=0.8`. Confirms — does not merely suggest — that three same-file revisions treated as three independent documents can promote a facet to `SUPPORTED` on edit-count alone. |
| **`test_kernel_purity.py` scope (v2 — new finding)** | The AST import-scan (`tests/unit/test_kernel_purity.py`) walks `kernel/*.py` **non-recursively only** — it does not and cannot verify `drive/materialize.py` or `gmail/materialize.py` (§3.3/§4.3's purity rows, D-M1-7 and its Gmail equivalent, must name the *actual* guarantee: these modules aren't under `kernel/` at all, not that a kernel-purity test covers them). |
| **Version string duplication (v2 — new finding)** | The project version is defined in **both** `pyproject.toml` and `src/operator_voice/__init__.py::__version__` — v1 §5.5 named only the former; both must move together or the two go stale relative to each other. |
| Doc-warden | `docs/plans/` and `docs/adr/` exist now (v1 shipped them); CLI `~/dev/_ops/bin/doc-warden.sh` and policy `~/dev/_ops/doc-warden/POLICY.md` remain absent on this machine — README indices stay hand-authored. `docs/plans/README.md` point 4 had its own body-shape ordering corrected 2026-08-06 to match what the named exemplar (this lineage) actually does (live-state before precedent, not after). |
| Home-workspace plan | `~/dev/_ops/plans/operator-voice-toolkit-build-plan.md` v4 uses **in-place** version bumps on one filename — differs from this repo's freeze-on-approval `docs/plans/<topic>-vN.md` convention (see [`docs/plans/README.md`](README.md)). Unchanged from v1; noted again per instruction. |

---

## 2. Architectural precedent (Slack → Google)

Mirror these Slack files/patterns mechanically; deviations require an ADR amendment.

| Slack (shipped) | Drive (M1) | Gmail (M2) |
|---|---|---|
| `slack/materialize.py` | `drive/materialize.py` | `gmail/materialize.py` |
| `adapters/slack_export.py` | `adapters/drive_export.py` | `adapters/gmail_export.py` |
| `slack.import` / `slack.status` | `drive.import` / `drive.status` | `gmail.import` / `gmail.status` |
| `AdapterType.SLACK_EXPORT` | `AdapterType.DRIVE_EXPORT` | `AdapterType.GMAIL_EXPORT` |
| `RuntimePaths.slack_export_dir` | `RuntimePaths.drive_export_dir` | `RuntimePaths.gmail_export_dir` |
| `_ensure_slack_source()` | `_ensure_drive_source()` | `_ensure_gmail_source()` |
| `docs/slack-adapter.md` | `docs/drive-adapter.md` | `docs/gmail-adapter.md` |
| `tests/unit/test_slack_materialize.py` | `tests/unit/test_drive_materialize.py` | `tests/unit/test_gmail_materialize.py` |
| `tests/integration/test_slack_import.py` | `tests/integration/test_drive_import.py` | `tests/integration/test_gmail_import.py` |
| *(v2 — new row)* `source.add` `choices=(...)` tuple, `operations.py` | add `"drive_export"` | add `"gmail_export"` |
| *(v2 — new row)* `conformance.py` suffix map | add `AdapterType.DRIVE_EXPORT: ".md"` | add `AdapterType.GMAIL_EXPORT: ".md"` |

**One deliberate improvement over Slack (authorized by ADR 0001, mechanism specified in ADR 0002):**
adopt **native API sync cursors** for agent-side incremental fetch (Gmail `historyId`, Drive Changes
API `newStartPageToken`) persisted in each export dir's `.import-state.json`; and materialize each
record to its **own immutable, ISO-8601-timestamp-prefixed file** so Slack's exact, unmodified
sort/cursor algorithm (`adapters/slack_export.py`) can be reused verbatim while still closing Slack's
documented monotonic-append blind spot (`docs/slack-adapter.md` § "Adapter cursor"). **v2 correction:**
v1 described this as a "composite adapter cursor" without naming an on-disk mechanism; ADR 0002
Decision 1 supplies the concrete, cheaper mechanism (see §5.2).

---

## 3. Milestone M1 — minimum viable end-to-end Drive adapter

**Goal:** Operator can ask an agent to refresh Drive voice; OVT materializes **owner-only** files
the operator owns, tags Google Docs/Slides (and other owned MIME types) with correct taxonomy
labels, and the hourly `curate` job ingests them like any other source.

### 3.1 v1 scope (in)

- Files where the operator is the **sole owner**, **and** the file is confirmed not to reside in a
  Shared Drive. **v2 — precision (ADR 0002 Decision 3):** enforced via two required envelope fields,
  not a single ambiguous `owner_id` string — see §3.4.
- Google Docs and Google Slides: set retrieval-facing `channel` to `docs` or `slides` and
  `output_type` to `doc` or `slide` respectively (via adapter, using MIME/type hints in the
  materialized envelope). **Requires the §0 `output_type`-threading fix to actually reach persisted
  `ContextScope` — this is now an explicit D-M1-0 acceptance test, not an implicit assumption.**
- Other owned native/text-exportable types (plain text, PDF text extraction performed **agent-side**
  before submit): materialize with `channel=docs`, `output_type=doc` — no dedicated taxonomy bucket
  needed (§3.6 resolves this definitively; v1 had left it as an open evaluation).
- Operations `drive.import` and `drive.status` mirroring `slack.import` / `slack.status` posture
  (`MUTATION`/`REVERSIBLE` and `READ`/`SAFE` respectively).

### 3.2 v1 scope (out — name as later milestones)

Deferred to **M3+** (see §6): shared/co-owned Drive files, Drive shortcuts, folders as sources,
Comments/suggesting mode, Sheets, native binary-only types without agent-side text extraction,
automatic revision diffing (Drive's on-disk immutable-per-revision layout, §5.2, is designed so this
milestone can read historical revisions directly from disk without a materialize-format change).

**v2 — clarity fix:** "Docs live API export from OVT" is a **permanent non-goal** (§7), not a
deferred milestone — v1 listed it in both places ambiguously. It is removed from this deferred list;
§7 is its only home.

### 3.3 New / modified files

**New modules**

- `src/operator_voice/drive/__init__.py`
- `src/operator_voice/drive/materialize.py` — server-side owner + not-shared-drive verification,
  dedup on `file_id` alone (**v2 — corrected from `file_id:revision_id`**; see §3.4, ADR 0002
  Decision 2), one immutable Markdown file per revision with `<!-- ovt:drive file=… rev=… -->`
  markers (parallel to Slack's `<!-- ovt:message ts=… -->`), and the new supersede-if-newer write
  path (v2 — new; see §5.2).
- `src/operator_voice/adapters/drive_export.py` — file-tree scan reusing Slack's unmodified
  lexicographic-filename cursor algorithm (**v2 — corrected from a described-but-unspecified
  "composite cursor"**; see §5.2); taxonomy tagging.

**Modified modules (M1 PR)**

- `src/operator_voice/domain/models.py` — add `AdapterType.DRIVE_EXPORT = "drive_export"`.
  **v2 — new:** thread `output_type` from `ObservationCandidate` through to `ContextScope` in
  `application/service.py::_ingest_source_within` (the §0 blocking prerequisite; a generic fix,
  exercised first by Drive but not Drive-specific — Slack candidates keep defaulting to `"text"`
  unless a future PR sets one).
- `src/operator_voice/domain/operations.py` — register `drive.import`, `drive.status`; **v2 — new:**
  extend `source.add`'s `adapter_type` `choices` tuple (~L333–336) to include `"drive_export"`.
- `src/operator_voice/adapters/registry.py` — wire `drive_export.scan`.
- `src/operator_voice/adapters/conformance.py` — `_Profile` for `DRIVE_EXPORT`; **v2 — new:** add
  `AdapterType.DRIVE_EXPORT: ".md"` to the safety-test suffix map (~L341–347) — an adapter type
  missing from this map silently falls back to `.txt`, which would let a Drive-specific
  oversized/symlink safety case pass for the wrong reason (matching `.txt`'s filter, not `.md`'s).
- `src/operator_voice/persistence/store.py` — **v2 — new, narrow:** add a supersede-if-newer path
  reachable only from Drive's own ingest call (ADR 0002 Decision 2); `upsert_document`'s existing
  insert-only contract for every other adapter is unchanged.
- `src/operator_voice/runtime/paths.py` — `drive_export_dir` property + `ensure()`.
- `src/operator_voice/application/service.py` — `drive_import`, `drive_status`, `_ensure_drive_source`,
  extend `setup()` return payload with `drive_source`. **v2 — sequencing note (see §5.3):**
  `_ensure_drive_source` follows the exact `_ensure_slack_source` pattern — called after `setup()`'s
  own `staged_operation` commits, requires `self.paths` to be non-`None`, and calls
  `self.paths.ensure()` before computing `canonical_root`.
- `src/operator_voice/interfaces/intents.py` — colloquial aliases ("refresh drive voice", …).
- `src/operator_voice/interfaces/cli.py` — stdin/`--brief` parity with slack handlers if needed.
- `src/operator_voice/domain/help.py` — help ranking strings.

**Docs (M1 PR)**

- `docs/drive-adapter.md` — mirror `docs/slack-adapter.md` structure; **no** hardcoded MCP server name;
  describe agent workflow generically ("host Google workspace MCP", read-only file/list/export tools).
  **v2 — new, explicit:** document the immutable-per-revision-file layout and the
  supersede-if-newer/provenance-vs-identity distinction (ADR 0002) so a reader doesn't have to infer
  it from code.
- `docs/README.md` — **v2 — new:** add Drive's doc row(s) to the documentation map/foundation table
  (same table Slack already appears in) so the map stays accurate the moment M1 merges.
- `docs/architecture.md` — **v2 — new:** correct any hardcoded adapter/scanner counts once Drive
  ships (this doc currently describes the pre-Drive/Gmail adapter count and asserts no email/Docs
  adapter exists — both become false at M1 merge).
- `docs/operating-procedures.md` — **v2 — new:** add a "Drive voice" operating-procedure section
  parallel to the existing "Slack voice" section (same rationale — this is where an operator-facing
  day-2 refresh procedure belongs, and the file already has the Slack precedent to mirror).
- Regenerate `docs/generated/**` after `operations.py` change.

**Tests (M1 PR)**

- `tests/unit/test_drive_materialize.py` — **must include** `test_materialize_rejects_mismatched_owner_id`
  (mirror `test_slack_materialize.py::test_materialize_rejects_mismatched_author_id`), **v2 — new:**
  `test_materialize_rejects_shared_drive_item` and `test_materialize_supersedes_only_when_newer`
  (out-of-order-arrival guard — ADR 0002 Decision 2's residual-risk note).
- `tests/unit/test_adapters.py` — Drive adapter cursor, taxonomy channel tags, monotonic cursor behavior.
- `tests/integration/test_drive_import.py` — round trip import → setup registration → adapter scan.
- `tests/unit/test_adapter_conformance.py` — parametrized or dedicated
  `test_drive_export_adapter_conformance_report_passes_every_dimension` (mirror Slack test); **v2 —
  new:** assert the conformance suffix map includes an explicit `DRIVE_EXPORT` entry rather than
  relying on the `.txt` fallback (see D-M1-8).
- Extend `test_kernel_purity.py` — **v2 — corrected claim:** this AST scan walks `kernel/*.py`
  non-recursively and does **not** cover `drive/materialize.py`; the actual guarantee it provides for
  M1 is "no new files were added under `kernel/`," which is what D-M1-7 now says precisely.

### 3.4 `drive.import` request envelope (sketch)

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
      "text": "plain text body exported agent-side"
    }
  ]
}
```

**v2 — new required fields (ADR 0002 Decision 3):** `drive_id` and `owners` (field names match
Google's own Drive API v3 `driveId`/`owners` verbatim, so an agent maps host-tool output directly —
no OVT-specific translation). Both are **required keys**; a missing key is rejected exactly like a
present-but-disqualifying value (fail-closed, never inferred).

**Server-side owner + not-shared-drive verification (hard invariant):** top-level `owner_id` is
self-asserted for this call (never persisted). Per entry:

- `drive_id` non-null/non-empty, **or the key is missing** → `skipped_shared_drive` (**v2 — new
  outcome**; a missing key is never treated as proof of My Drive).
- `owners` length ≠ 1, or `owners[0]` ≠ top-level `owner_id`, **or the key is missing** →
  `skipped_shared_ownership` (**v2 — precision:** now checked against a real list field, not inferred
  from a single ambiguous string).
- Entry `owner_id` mismatched against top-level `owner_id` → `skipped_mismatched_owner` (never
  `skipped_mismatched_author` — keep per-adapter vocabulary in responses/docs; unchanged from v1).

**v2 — document identity correction (ADR 0002 Decision 2):** persisted document identity
(`ObservationCandidate.source_native_id`, the key `upsert_document` dedups on) is **`file_id` alone**,
not `file_id:revision_id` as v1 stated. `revision_id` is still captured — as **provenance**, via the
existing `domain/models.py::Provenance.native_revision` field (already defined for exactly this case;
no new field). This matters because `upsert_document` is insert-only (§1) and
`kernel/calibration.py`'s `min_documents_for_supported=3` (§1) means treating each revision as a
distinct document lets three quick saves of one memo promote a facet to `SUPPORTED` on edit-count
alone — a materially different accumulation semantic than every other adapter's genuinely-independent-
record model. Fixing identity at the source (this decision) is simpler than adding file-awareness to
kernel synthesis, and keeps `kernel/` completely untouched.

### 3.5 Agent workflow (stage 0 — generic)

Document in `docs/drive-adapter.md` only; no OVT code calls Google.

1. `operator-voice drive-status` (or `drive.status` via MCP) — freshness snapshot.
2. Agent obtains operator identity fresh from host Google tools (profile/account id — never
   hardcoded, never read from OVT config).
3. If refresh needed: list **owned, non-Shared-Drive** files modified since last
   `sync_cursor.changes_page_token` (agent uses host Drive Changes API or equivalent MCP wrapper),
   export text agent-side for Docs/Slides/other allowed types, assemble envelope **including
   `drive_id` and `owners` for every entry** (§3.4).
4. `operator-voice drive-import --file …` (or inline args / stdin).
5. Optional immediate `ingest`, or wait for hourly `curate`.

Persist returned `new_start_page_token` / next changes token in `.import-state.json` via
`drive.import` (agent reads it back on next refresh from `drive.status` JSON).

### 3.6 Taxonomy (`kernel/taxonomy.py`)

M1 adapter sets candidate `channel` / `output_type` at scan time (not in materialize — keep kernel
data-only):

| Materialized `mime_type` (agent-supplied) | `channel` | `output_type` |
|---|---|---|
| Google Docs | `docs` | `doc` |
| Google Slides | `slides` | `slide` |
| Other owned text (plain, markdown export, PDF text) | `docs` | `doc` |

**v2 — resolved (was "evaluate for M1" in v1):** **no** new `SYNONYMS["channel"]` entry is needed.
The adapter hardcodes the canonical literal (`"docs"` / `"slides"`) directly from its own MIME-type
switch, exactly matching how `slack_export.py` hardcodes `channel="slack"` today — synonyms exist for
*operator/agent free-text input* normalization (e.g. a `context.set` call), not for adapter-internal
tagging, so there is no second consumer to justify one (lean-scope principle). Non-Docs/Slides owned
files ride the existing `docs` bucket, unchanged from v1.

### 3.7 M1 acceptance tests

| ID | Test | Pass criterion |
|---|---|---|
| **D-M1-0** *(v2 — new)* | `test_ingest_threads_output_type_from_candidate_into_context_scope` | The §0 blocking prerequisite: a candidate's `output_type` (e.g. `"doc"`) is present on the persisted `ContextScope`, not defaulted to `"text"` |
| D-M1-1 | `test_materialize_rejects_mismatched_owner_id` | Mismatch dropped, never on disk |
| D-M1-2 | `test_materialize_rejects_shared_ownership` | Multi-owner entry (`owners` length ≠ 1 or mismatched) skipped in v1 |
| **D-M1-2b** *(v2 — new)* | `test_materialize_rejects_shared_drive_item` | Non-null `drive_id`, or a missing `drive_id` key, skipped as `skipped_shared_drive` |
| D-M1-3 | `test_drive_export_tags_docs_and_slides_channels` | Candidates carry `docs` / `slides` |
| D-M1-4 | `test_drive_export_adapter_cursor_reuses_slack_algorithm` | New immutable revision file after cursor still picked up, via Slack's unmodified sort/filter cursor (**v2 — renamed/reframed from "composite cursor"**) |
| **D-M1-4b** *(v2 — new)* | `test_materialize_supersedes_only_when_newer` | A second revision of the same `file_id` updates persisted content only if strictly newer than the stored revision; an out-of-order older revision is a no-op |
| D-M1-5 | `test_drive_import_setup_round_trip` | `setup()` registers `drive_export` once |
| D-M1-6 | `test_adapter_conformance` drive profile | All conformance dimensions green |
| **D-M1-7** *(v2 — corrected)* | `test_kernel_purity` | No new files added under `kernel/` (this AST scan does not, and cannot, inspect `drive/materialize.py` — see §1) |
| **D-M1-8** *(v2 — new)* | `test_source_add_accepts_drive_export_adapter_type` | `source.add`'s CLI/MCP `choices` tuple includes `"drive_export"` |
| **D-M1-9** *(v2 — new)* | `test_conformance_suffix_map_has_explicit_drive_entry` | `conformance.py`'s suffix map has a `DRIVE_EXPORT: ".md"` entry, not the `.txt` fallback |

---

## 4. Milestone M2 — Gmail adapter (Sent mail only)

**Goal:** Same three-stage pattern for **Sent** messages only — the operator's outbound email voice.

### 4.1 v1 scope (in)

- Messages from the operator's **Sent** mailbox/trash-excluded, agent-fetched.
- Server-side verification: top-level `author_email` must match each message's `from`/`sender_email`
  (exact field documented in adapter doc from host MCP shape).
- Taxonomy: `channel=email`, `output_type=email` — **v2 note:** also depends on the §0 `output_type`-
  threading fix landing in M1; M2 does not re-fix it, just relies on it (see D-M2-0).
- Operations `gmail.import`, `gmail.status`.
- Native **`historyId`** cursor stored in `.import-state.json` for incremental agent fetch (ADR 0001).

### 4.2 v1 scope (out — later milestones)

See §6: Drafts, Inbox, label-filtered harvest, thread reply extraction (inbound quotes stripped
agent-side), attachments, delegated/send-as aliases not owned by the operator.

### 4.3 New / modified files

**New:** `gmail/materialize.py`, `adapters/gmail_export.py`, tests mirroring M1 list,
`docs/gmail-adapter.md`.

**Modified:** same registry/service/paths/setup/intents/help pattern as M1 with `GMAIL_EXPORT`,
`gmail_export_dir`. **v2 — new (mirrors M1's D-M1-8/D-M1-9):** extend `source.add`'s `choices` tuple
with `"gmail_export"`; add `AdapterType.GMAIL_EXPORT: ".md"` to `conformance.py`'s suffix map.
**v2 — new (mirrors M1's docs additions):** `docs/README.md`, `docs/architecture.md`,
`docs/operating-procedures.md` each get their Gmail-parallel update in the M2 PR (do not defer
docs-map accuracy to some later cleanup pass — each milestone keeps the map honest for what has
actually shipped by that point).

**Wire format:** one immutable Markdown file per message (**v2 — explicit, was implicit in v1**:
`<!-- ovt:gmail id=… thread=… -->` blocks, filename prefixed with `internal_date` in ISO-8601 per ADR
0002 Decision 1 — identical layout principle as Drive, but Gmail's dedup key stays `message_id` alone
since each message genuinely is an independent, immutable utterance, unlike a Drive file's revisions).

### 4.4 `gmail.import` envelope (sketch)

```json
{
  "author_email": "operator@gmail.com",
  "sync_cursor": { "history_id": "987654" },
  "messages": [
    {
      "message_id": "18c2…",
      "thread_id": "18c2…",
      "internal_date": "2026-07-30T14:02:00Z",
      "from": "operator@gmail.com",
      "subject": "Re: launch timeline",
      "text": "plain text body (agent strips quoted reply blocks before submit)"
    }
  ]
}
```

Mismatch on `from` vs top-level `author_email` → `skipped_mismatched_author` (email vocabulary
matches Slack's author_id pattern). Unchanged from v1 — Gmail's envelope needed no ownership-schema
correction (ADR 0002 Decision 3 is Drive-only; a Sent message has exactly one sender by construction).

### 4.5 M2 acceptance tests

| ID | Test | Pass criterion |
|---|---|---|
| **G-M2-0** *(v2 — new)* | `test_gmail_candidates_carry_email_output_type` | Relies on M1's D-M1-0 fix; confirms Gmail candidates also reach persisted `ContextScope` with `output_type="email"` |
| G-M2-1 | `test_materialize_rejects_mismatched_author_email` | Impersonated From dropped |
| G-M2-2 | `test_gmail_export_sent_only_role` | Adapter never emits non-user roles |
| G-M2-3 | `test_gmail_history_id_persisted` | Import writes cursor; status reads it |
| **G-M2-4** *(v2 — corrected)* | `test_gmail_export_adapter_cursor_reuses_slack_algorithm` | Per-message immutable-file cursor (Slack's unmodified algorithm, per-message filenames) resumes correctly — renamed from "composite cursor" to match §5.2's actual mechanism |
| G-M2-5 | Conformance + integration round trip | Same bar as Slack/Drive |
| **G-M2-6** *(v2 — new, mirrors D-M1-8/D-M1-9)* | `test_source_add_accepts_gmail_export_adapter_type` + suffix-map test | Same CLI-choices and conformance-suffix-map coverage as Drive |
| **G-M2-7** *(v2 — new, mirrors D-M1-7)* | `test_kernel_purity` | No new files added under `kernel/` (same corrected guarantee as D-M1-7 — the scan does not, and cannot, inspect `gmail/materialize.py`) |

---

## 5. Shared implementation notes (M1 + M2)

### 5.1 Materialize module boundary

- Lives under `src/operator_voice/drive/` and `…/gmail/` — **not** under `kernel/`.
- Imports: stdlib + pathlib only (same purity bar as `slack/materialize.py`).
- Shared patterns: `.import-state.json` sidecar, `MaterializeResult.as_dict()`, import-state
  timestamp for 24h stale heuristic. **v2 — resolved and corrected (v1's "reuse or rename" question
  was open, and misplaced the constant under materialize):** the stale-after-hours constant
  (`_SLACK_STALE_AFTER_HOURS`) actually lives in `application/service.py` at module scope, consumed
  by `slack_status()` — not inside `slack/materialize.py`. M1 renames it to the neutral
  `_CHANNEL_STALE_AFTER_HOURS` and updates its call sites in `slack_status()`, so the new
  `drive_status()` (M1) and `gmail_status()` (M2) reference the same shared constant rather than each
  forking their own copy or importing a Slack-branded name.

### 5.2 Adapter scan cursor and on-disk layout (improvement over Slack — see ADR 0002)

Slack v1 uses lexicographic **file path** cursors (`adapters/slack_export.py`), which skips
late-appended messages in an already-scanned file
(`test_slack_export_adapter_cursor_does_not_reprocess_a_file_that_gains_new_messages`).

**v2 — the mechanism, fully specified (this is what ADR 0002 Decision 1 adds over v1's unspecified
"composite cursor" language):** Drive and Gmail materialize **one immutable file per record**, never
appended to after creation, named `<timestamp_iso>__<file_id>__<revision_id>.md` (Drive) or
`<timestamp_iso>__<message_id>.md` (Gmail) — colons replaced with `-` for filesystem portability.
Because the leading path segment is a zero-padded ISO-8601 timestamp, **plain lexicographic filename
order is chronological order**, so `drive_export.py`/`gmail_export.py` reuse **Slack's existing,
unmodified** sort/cursor algorithm verbatim (sorted `iter_files_within` → filter `> cursor` →
`next_cursor = files[-1]`). No new comparator, no parse-every-file-every-tick restructuring. A file is
either fully new (picked up once) or already-seen and permanently behind the cursor — correct,
because an immutable file can never change again. This closes Slack's blind spot without inheriting
Slack's cost profile or inventing new adapter-contract machinery.

**Accepted residual risk (documented, mirroring how Slack's own limitation is documented, not
hidden):** an agent submitting records out of strict timestamp order across two different import calls
could have a record silently missed by the next incremental tick until a full rescan — narrower than
Slack's blind spot (any append vs. only out-of-order submission) and judged an acceptable v1 tradeoff.

**Document identity is separate from on-disk layout (ADR 0002 Decision 2):** Drive's *persisted*
document identity is `file_id` alone, with `revision_id` carried as provenance
(`Provenance.native_revision`) rather than as part of the dedup key — see §3.4. This requires one
narrow, additive **supersede-if-newer** path in `persistence/store.py`, reachable only from Drive's
ingest call: when a candidate's `file_id` already has a stored document, update content only if the
new candidate's revision is strictly newer than the stored one (never unconditional overwrite — this
is what protects against the out-of-order-arrival risk above corrupting already-good state). Gmail is
unaffected — its dedup key stays `message_id` alone, unchanged from v1, because each message is a
genuinely independent, immutable utterance.

Agent-side fetch cursors (`historyId`, Changes API tokens) are separate — stored in
`.import-state.json`, surfaced via `*.status`, never used directly by `curate`.

### 5.3 `setup()` registration

Idempotent `_ensure_drive_source` / `_ensure_gmail_source` matching `_ensure_slack_source()`
(`service.py`): register only when `self.paths` is not `None`; call `self.paths.ensure()` before
computing `canonical_root`; never duplicate `(adapter_type, canonical_root)` pair. **v2 — precision
(traced against the live `setup()`/`_ensure_slack_source()` code, not just its name):** this call
happens **after** `setup()`'s own `staged_operation` transaction has already committed — like
`local_jobs_enroll.ensure_curation_schedule()`, it never nests a second `staged_operation` inside
`setup()`'s, and it must never raise (a caller with no `paths`, e.g. a minimal test double, still
completes `setup()` — return `{"status": "skipped", "reason": "no_runtime_paths"}` in that case,
exactly like the Slack precedent).

### 5.4 Conformance coverage

`tests/unit/test_adapter_conformance.py` already runs its generic conformance suite against every
registered `AdapterType` (no per-adapter-type `@pytest.mark.parametrize` block needs hand-editing for
a new type to be covered — **v2 correction:** v1's sketch implied a parametrize list needing a manual
new entry per milestone; the real gap is `conformance.py`'s own `_Profile` registration and its
suffix map, §3.3/D-M1-9, not the test file's parametrization). Keep dedicated Slack/Drive/Gmail tests
for adapter-specific role/taxonomy assertions.

### 5.5 Version bump

Ship M1 and M2 each with a semver patch bump. **v2 — corrected (v1 named only `pyproject.toml`):**
bump **both** `pyproject.toml`'s `version` **and** `src/operator_voice/__init__.py`'s `__version__` in
the same commit — they are two independent strings today and drifting them apart is itself a bug, not
just an omission. Lesson from the M5b evidence ledger: adapter operations shipped under an unchanged
version breaks install digest receipts.

**v2 — new subsection: ecosystem propagation is a separate, later concern, named so it isn't
silently forgotten.** This repo's own semver bump is necessary but not sufficient for the full
KleinPerkins ecosystem to notice new capability — Global Capability Binding `meta.yml`, WF1 registry
enrollment, and WF2 cpp-sync vendoring are each separate, independently-timed steps documented in
`docs/topology.md`. **This plan explicitly scopes ecosystem propagation OUT of the M1/M2 implementation
PRs** — it is the same class of follow-on as the existing M8 "unified Google status" milestone: named
here so it is a deliberate, tracked deferral rather than a silent gap, not something either milestone's
acceptance tests block on.

---

## 6. Later milestones (explicitly deferred)

Name these in docs/issues; do not scope into M1/M2.

| Milestone | Scope |
|---|---|
| **M3 — Drive shared/co-authored + revision diffing** | Multi-owner files with explicit operator-segment extraction; shared Drives; automatic revision diffing (reads the immutable per-revision on-disk files §5.2 already preserves — no materialize-format change needed to unlock this later) |
| **M4 — Gmail drafts** | Draft corpus with stronger "not sent" labeling; separate materialization subdir |
| **M5 — Gmail inbound reply extraction** | Inbox/thread harvest with quote stripping + authorship segmentation (`kernel/authorship.py` forward-header paths) |
| **M6 — Gmail labels, filters & attachments** | Label-scoped harvest beyond Sent; delegated/send-as aliases not provably the operator; attachment binary ingest *(v2 — folded v1's two previously-unnamed "later" items — attachments and send-as aliases — into this existing milestone rather than leaving them as bare non-goal bullets with no milestone home)* |
| **M7 — Drive Sheets/Slides notes** | Additional MIME families, agent-side export recipes |
| **M8 — Unified Google status** | Optional combined freshness dashboard line (still no OVT-side Google calls) |
| **M9 — Ecosystem propagation** *(v2 — new)* | GCB `meta.yml` update, WF1 registry enrollment, WF2 cpp-sync vendoring for the new Drive/Gmail capability surface (§5.5) — deliberately named, not silently dropped |

---

## 7. Non-goals (all milestones)

- OVT holding Google credentials or calling Google APIs directly.
- Auto-harvest on a timer without an agent in the loop.
- Persisting operator Google identity in OVT config (re-derived fresh each refresh).
- Impersonation or harvesting mail/files the operator does not own.
- **Docs live API export from OVT** (v2: consolidated here from v1's ambiguous double-listing — this
  is the single, permanent home for this non-goal; it is not also a deferred milestone in §3.2/§6).
- Kernel or persistence changes beyond new adapter candidates and taxonomy synonyms — **v2 note:**
  the one addition to this boundary is `persistence/store.py`'s narrow supersede-if-newer path
  (§3.4/§5.2/ADR 0002 Decision 2), which is a persistence change, explicitly and deliberately, not a
  kernel change; `kernel/` itself remains untouched.
- Regenerating `docs/generated/` in this plan-only PR.

---

## 8. Owner GO / NO-GO gate

| Gate | Requirement |
|---|---|
| Plan review | Owner reads this v2 DRAFT + ADR 0001 + ADR 0002 |
| ADR acceptance | On GO: accept ADR 0001 + ADR 0002 (`Status: accepted — YYYY-MM-DD (owner GO alongside plan v2)`) and update `docs/adr/README.md` index — same PR as plan freeze |
| Approval | Explicit GO → accept cited ADRs **and** flip plan Status to `APPROVED — FROZEN` (one PR); then open M1 implementation PR |
| M1 done | All §3.7 tests green (D-M1-0 through D-M1-9); `docs/drive-adapter.md` + `docs/README.md`/`docs/architecture.md`/`docs/operating-procedures.md` updates merged; both version strings bumped (§5.5); verifier App green |
| M2 done | All §4.5 tests green (G-M2-0 through G-M2-7); `docs/gmail-adapter.md` + doc-map updates merged; both version strings bumped; `docs/generated/**` regenerated for the `gmail.*`/`drive.*` `operations.py` entries |

---

## 9. Cross-references

- ADRs: [`docs/adr/0001-google-channel-adapters-agent-delegated-fetch.md`](../adr/0001-google-channel-adapters-agent-delegated-fetch.md),
  [`docs/adr/0002-drive-gmail-materialize-identity-and-layout.md`](../adr/0002-drive-gmail-materialize-identity-and-layout.md)
- Slack precedent: [`docs/slack-adapter.md`](../slack-adapter.md)
- Adapter contract: [`docs/system-mechanics.md`](../system-mechanics.md)
- Product invariants: [`docs/product-requirements.md`](../product-requirements.md)
- Doc conventions: [`docs/plans/README.md`](README.md), [`docs/adr/README.md`](../adr/README.md)
- Prior version: [`gmail-drive-channel-adapters-v1.md`](gmail-drive-channel-adapters-v1.md) (frozen historical)

---

## 10. Swarm review provenance

A `/swarm` Deep-band review (4 reviewers, no verifier — this is a plan document, not live code) ran
against v1 + ADR 0001 + the live codebase at `origin/main` `db9677c`. Each reviewer's claims were
spot-verified against the actual source (file + line citations) before being folded in; low-confidence
or unverifiable claims were dropped rather than included on trust. Summary by reviewer focus:

| Reviewer | Focus | Highest-value findings folded into v2 |
|---|---|---|
| `eng-systems` | Kernel purity, adapter pattern fidelity | `output_type` threading gap (§0); `test_kernel_purity.py`'s real (non-recursive) scope (§1, D-M1-7); `source.add` choices tuple (§1, D-M1-8); conformance suffix-map fallback (§3.3, D-M1-9); dual version-string location (§1, §5.5) |
| `dependency-assassin` | Sequencing, cross-module deps | `setup()`/`_ensure_*_source()` exact commit-then-best-effort sequencing (§5.3); ecosystem-propagation steps beyond local semver (§5.5, new M9); hand-authored docs (`docs/README.md`, `docs/architecture.md`, `docs/operating-procedures.md`) staleness at each milestone (§3.3, §4.3) |
| `doc-sharpener` | Plan clarity, milestone acceptance | "Docs live API export" double-listed as both deferred and non-goal (§3.2/§7); §6's two unnamed later-milestone items with no home (M6, §6); `docs/plans/README.md` point 4's body-shape order not matching its own named exemplar (§1, fixed in `README.md` directly) |
| `first-principles` | Cursor design, v1 scope holes | Composite cursor described but no on-disk mechanism specified (§2, §5.2, ADR 0002 Decision 1); revision-keyed dedup inflates confidence via edit-count (§3.4, ADR 0002 Decision 2); Shared Drive ownership unrepresentable in the v1 envelope schema (§3.4, ADR 0002 Decision 3) |

**Key deltas, v1 → v2 (non-exhaustive; see inline `v2 —` markers throughout for the complete list):**

1. New blocking M1 prerequisite: thread `output_type` from candidate into persisted `ContextScope`.
2. Cursor mechanism fully specified: immutable ISO-8601-prefixed per-record files reusing Slack's
   unmodified cursor algorithm (new ADR 0002 Decision 1) — not a new comparator or restructured scan.
3. Drive document identity corrected to `file_id` alone, with revision as provenance not dedup key,
   plus a new narrow supersede-if-newer persistence path (ADR 0002 Decision 2).
4. `drive.import` envelope gains required `drive_id`/`owners[]` fields so the already-decided
   shared-drive/multi-owner exclusion is mechanically enforceable, fail-closed (ADR 0002 Decision 3).
5. Four previously-invisible integration touchpoints now named explicitly: `source.add`'s adapter-type
   choices tuple, `conformance.py`'s per-type suffix map, dual version-string locations, and
   `test_kernel_purity.py`'s real non-recursive scope.
6. Documentation-parity debt named per-milestone (`docs/README.md`, `docs/architecture.md`,
   `docs/operating-procedures.md`) instead of left implicit.
7. Deferred-milestone completeness: consolidated the two ambiguously-placed items (Docs live export,
   attachments/send-as) into one unambiguous home each; added M9 (ecosystem propagation).
8. `docs/plans/README.md` point 4 corrected to match the section order its own named exemplar uses.
