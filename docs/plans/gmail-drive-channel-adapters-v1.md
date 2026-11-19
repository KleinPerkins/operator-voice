# Gmail and Drive channel adapters — build plan (v1)

- **Version:** v1
- **Status:** DRAFT (for review)
- **Date:** 2026-08-05
- **Relates to:** [`docs/slack-adapter.md`](../slack-adapter.md) (precedent), ADR
  [`0001-google-channel-adapters-agent-delegated-fetch.md`](../adr/0001-google-channel-adapters-agent-delegated-fetch.md),
  home-workspace `~/dev/_ops/plans/operator-voice-toolkit-build-plan.md` v4 (product scope — do not
  edit from this repo)
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

---

## 1. Live-state verification (2026-08-05)

Verified against `origin/main` at `1daf63a`:

| Artifact | Current state |
|---|---|
| Slack precedent | Shipped: `slack/materialize.py`, `adapters/slack_export.py`, `slack.import` / `slack.status` in `operations.py` (~L348–375), `AdapterType.SLACK_EXPORT`, `RuntimePaths.slack_export_dir`, `_ensure_slack_source()` in `setup()`, docs at `docs/slack-adapter.md`. |
| Adapter registry | `adapters/registry.py` maps `AdapterType` → `scan`; conformance profiles in `adapters/conformance.py`. |
| Taxonomy | `kernel/taxonomy.py` already includes `channel` values `email`, `docs`, `slides` and synonyms `gmail`→`email`, `gdoc`/`gdocs`→`docs`, `deck`/`presentation`→`slides`. |
| Doc-warden | `docs/plans/` and `docs/adr/` did not exist; CLI `~/dev/_ops/bin/doc-warden.sh` and policy `~/dev/_ops/doc-warden/POLICY.md` absent on this machine — README indices hand-authored. |
| Home-workspace plan | `~/dev/_ops/plans/operator-voice-toolkit-build-plan.md` v4 uses **in-place** version bumps on one filename — differs from this repo's freeze-on-approval `docs/plans/<topic>-vN.md` convention (see [`docs/plans/README.md`](README.md)). |

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

**One deliberate improvement over Slack (authorized by ADR 0001):** adopt **native API sync
cursors** for agent-side incremental fetch (Gmail `historyId`, Drive Changes API
`newStartPageToken`) persisted in each export dir's `.import-state.json`, and use **per-record
composite adapter cursors** (`<file_id>:<revision_id>` / `<message_id>`) instead of Slack's
lexicographic file-path cursor that misses late-appended content in an already-scanned file.

---

## 3. Milestone M1 — minimum viable end-to-end Drive adapter

**Goal:** Operator can ask an agent to refresh Drive voice; OVT materializes **owner-only** files
the operator owns, tags Google Docs/Slides (and other owned MIME types) with correct taxonomy
labels, and the hourly `curate` job ingests them like any other source.

### 3.1 v1 scope (in)

- Files where the operator is the **sole owner** (`owners.me == true` and no other human owners in
  the agent-supplied metadata — exact field names come from the host MCP payload, not from OVT
  calling Drive).
- Google Docs and Google Slides: set retrieval-facing `channel` to `docs` or `slides` and
  `output_type` to `doc` or `slide` respectively (via adapter, using MIME/type hints in the
  materialized envelope).
- Other owned native/text-exportable types (plain text, PDF text extraction performed **agent-side**
  before submit): materialize with `channel=docs`, `output_type=doc` unless taxonomy ADR adds a
  dedicated bucket (see §3.6).
- Operations `drive.import` and `drive.status` mirroring `slack.import` / `slack.status` posture
  (`MUTATION`/`REVERSIBLE` and `READ`/`SAFE` respectively).

### 3.2 v1 scope (out — name as later milestones)

Deferred to **M3+** (see §6): shared/co-owned Drive files, Drive shortcuts, folders as sources,
Comments/ suggesting mode, Sheets, native binary-only types without agent-side text extraction,
automatic revision diffing, Docs live API export from OVT.

### 3.3 New / modified files

**New modules**

- `src/operator_voice/drive/__init__.py`
- `src/operator_voice/drive/materialize.py` — server-side owner verification, dedup by
  `file_id:revision_id`, Markdown wire format with `<!-- ovt:drive file=… rev=… -->` markers (parallel
  to Slack's `<!-- ovt:message ts=… -->`).
- `src/operator_voice/adapters/drive_export.py` — file-tree scan; per-record cursor; taxonomy tagging.

**Modified modules (M1 PR)**

- `src/operator_voice/domain/models.py` — add `AdapterType.DRIVE_EXPORT = "drive_export"`.
- `src/operator_voice/domain/operations.py` — register `drive.import`, `drive.status`.
- `src/operator_voice/adapters/registry.py` — wire `drive_export.scan`.
- `src/operator_voice/adapters/conformance.py` — `_Profile` for `DRIVE_EXPORT`.
- `src/operator_voice/runtime/paths.py` — `drive_export_dir` property + `ensure()`.
- `src/operator_voice/application/service.py` — `drive_import`, `drive_status`, `_ensure_drive_source`,
  extend `setup()` return payload with `drive_source`.
- `src/operator_voice/interfaces/intents.py` — colloquial aliases ("refresh drive voice", …).
- `src/operator_voice/interfaces/cli.py` — stdin/`--brief` parity with slack handlers if needed.
- `src/operator_voice/domain/help.py` — help ranking strings.

**Docs (M1 PR)**

- `docs/drive-adapter.md` — mirror `docs/slack-adapter.md` structure; **no** hardcoded MCP server name;
  describe agent workflow generically ("host Google workspace MCP", read-only file/list/export tools).
- Regenerate `docs/generated/**` after `operations.py` change.

**Tests (M1 PR)**

- `tests/unit/test_drive_materialize.py` — **must include** `test_materialize_rejects_mismatched_owner_id`
  (mirror `test_slack_materialize.py::test_materialize_rejects_mismatched_author_id`).
- `tests/unit/test_adapters.py` — Drive adapter cursor, taxonomy channel tags, monotonic cursor behavior.
- `tests/integration/test_drive_import.py` — round trip import → setup registration → adapter scan.
- `tests/unit/test_adapter_conformance.py` — parametrized or dedicated
  `test_drive_export_adapter_conformance_report_passes_every_dimension` (mirror Slack test at
  ~L47–58).
- Extend `test_kernel_purity.py` — confirm new packages import nothing forbidden (AST scan is
  path-based; no new kernel files).

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
      "name": "Q3 decision memo",
      "mime_type": "application/vnd.google-apps.document",
      "modified_time": "2026-07-30T14:02:00Z",
      "owner_id": "operator@gmail.com",
      "text": "plain text body exported agent-side"
    }
  ]
}
```

**Server-side owner verification (hard invariant):** top-level `owner_id` is self-asserted for
this call (never persisted). Each entry's `owner_id` must match before write. Mismatch →
`skipped_mismatched_owner` (never `skipped_mismatched_author` — keep per-adapter vocabulary in
responses/docs). Multi-owner entries → `skipped_shared_ownership` in v1.

### 3.5 Agent workflow (stage 0 — generic)

Document in `docs/drive-adapter.md` only; no OVT code calls Google.

1. `operator-voice drive-status` (or `drive.status` via MCP) — freshness snapshot.
2. Agent obtains operator identity fresh from host Google tools (profile/account id — never
   hardcoded, never read from OVT config).
3. If refresh needed: list **owned** files modified since last `sync_cursor.changes_page_token`
   (agent uses host Drive Changes API or equivalent MCP wrapper), export text agent-side for Docs/
   Slides/other allowed types, assemble envelope.
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

**Evaluate for M1:** add `SYNONYMS["channel"]["google_drive"] = "docs"` only if needed for
forward-compatible agent labels — avoid new canonical `channel` values unless a second consumer
needs them (lean scope). Non-Docs/Slides owned files do **not** require a new canonical channel in
v1; they ride the existing `docs` bucket.

### 3.7 M1 acceptance tests

| ID | Test | Pass criterion |
|---|---|---|
| D-M1-1 | `test_materialize_rejects_mismatched_owner_id` | Mismatch dropped, never on disk |
| D-M1-2 | `test_materialize_rejects_shared_ownership` | Multi-owner entry skipped in v1 |
| D-M1-3 | `test_drive_export_tags_docs_and_slides_channels` | Candidates carry `docs` / `slides` |
| D-M1-4 | `test_drive_export_adapter_composite_cursor` | New revision after cursor still picked up |
| D-M1-5 | `test_drive_import_setup_round_trip` | `setup()` registers `drive_export` once |
| D-M1-6 | `test_adapter_conformance` drive profile | All conformance dimensions green |
| D-M1-7 | `test_kernel_purity` | No new forbidden imports under `kernel/` or materialize |

---

## 4. Milestone M2 — Gmail adapter (Sent mail only)

**Goal:** Same three-stage pattern for **Sent** messages only — the operator's outbound email voice.

### 4.1 v1 scope (in)

- Messages from the operator's **Sent** mailbox/trash-excluded, agent-fetched.
- Server-side verification: top-level `author_email` must match each message's `from`/`sender_email`
  (exact field documented in adapter doc from host MCP shape).
- Taxonomy: `channel=email`, `output_type=email`.
- Operations `gmail.import`, `gmail.status`.
- Native **`historyId`** cursor stored in `.import-state.json` for incremental agent fetch (ADR 0001).

### 4.2 v1 scope (out — later milestones)

See §6: Drafts, Inbox, label-filtered harvest, thread reply extraction (inbound quotes stripped
agent-side), attachments, delegated/send-as aliases not owned by the operator.

### 4.3 New / modified files

**New:** `gmail/materialize.py`, `adapters/gmail_export.py`, tests mirroring M1 list,
`docs/gmail-adapter.md`.

**Modified:** same registry/service/paths/setup/intents/help pattern as M1 with `GMAIL_EXPORT`,
`gmail_export_dir`.

**Wire format:** `<!-- ovt:gmail id=… thread=… -->` blocks; dedup on `message_id`.

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
matches Slack's author_id pattern).

### 4.5 M2 acceptance tests

| ID | Test | Pass criterion |
|---|---|---|
| G-M2-1 | `test_materialize_rejects_mismatched_author_email` | Impersonated From dropped |
| G-M2-2 | `test_gmail_export_sent_only_role` | Adapter never emits non-user roles |
| G-M2-3 | `test_gmail_history_id_persisted` | Import writes cursor; status reads it |
| G-M2-4 | `test_gmail_adapter_composite_cursor` | Per-message cursor resumes correctly |
| G-M2-5 | Conformance + integration round trip | Same bar as Slack/Drive |

---

## 5. Shared implementation notes (M1 + M2)

### 5.1 Materialize module boundary

- Lives under `src/operator_voice/drive/` and `…/gmail/` — **not** under `kernel/`.
- Imports: stdlib + pathlib only (same purity bar as `slack/materialize.py`).
- Shared patterns: `.import-state.json` sidecar, `MaterializeResult.as_dict()`, import-state
  timestamp for 24h stale heuristic (reuse `_SLACK_STALE_AFTER_HOURS` constant or rename to neutral
  `_CHANNEL_STALE_AFTER_HOURS`).

### 5.2 Adapter scan cursor (improvement over Slack)

Slack v1 uses lexicographic **file path** cursors (`adapters/slack_export.py` L57–60), which skips
late-appended messages in an already-scanned file
(`test_slack_export_adapter_cursor_does_not_reprocess_a_file_that_gains_new_messages`).

Drive/Gmail adapters **must** use a composite cursor of the last processed
`source_native_id` (`<file_id>:<revision_id>` / `<message_id>`) with deterministic ordering by
`(timestamp, source_native_id)`. Incremental scans revisit files when a new revision/message id
sorts after the cursor. Document the semantics in each adapter doc; add explicit regression tests
(**this is the genuine improvement over Slack precedent**).

Agent-side fetch cursors (`historyId`, Changes API tokens) are separate — stored in
`.import-state.json`, surfaced via `*.status`, never used directly by `curate`.

### 5.3 `setup()` registration

Idempotent `_ensure_drive_source` / `_ensure_gmail_source` matching `_ensure_slack_source()` (
`service.py` ~L417–442): register only when `self.paths` present; never duplicate `(adapter_type,
canonical_root)` pair.

### 5.4 Conformance parametrization

Extend `tests/unit/test_adapter_conformance.py`:

```python
@pytest.mark.parametrize("adapter_type", [
    AdapterType.SLACK_EXPORT,
    AdapterType.DRIVE_EXPORT,  # M1
    AdapterType.GMAIL_EXPORT,  # M2
])
def test_channel_export_adapter_conformance(tmp_path, adapter_type):
    ...
```

Keep dedicated Slack/Drive/Gmail tests for adapter-specific role/taxonomy assertions.

### 5.5 Version bump

Ship M1 and M2 each with a semver patch bump in `pyproject.toml` (lesson from M5b evidence ledger:
adapter operations shipped under an unchanged version breaks install digest receipts).

---

## 6. Later milestones (explicitly deferred)

Name these in docs/issues; do not scope into M1/M2.

| Milestone | Scope |
|---|---|
| **M3 — Drive shared/co-authored** | Multi-owner files with explicit operator-segment extraction; shared Drives |
| **M4 — Gmail drafts** | Draft corpus with stronger "not sent" labeling; separate materialization subdir |
| **M5 — Gmail inbound reply extraction** | Inbox/thread harvest with quote stripping + authorship segmentation (`kernel/authorship.py` forward-header paths) |
| **M6 — Gmail labels & filters** | Label-scoped harvest beyond Sent |
| **M7 — Drive Sheets/Slides notes** | Additional MIME families, agent-side export recipes |
| **M8 — Unified Google status** | Optional combined freshness dashboard line (still no OVT-side Google calls) |

---

## 7. Non-goals (all milestones)

- OVT holding Google credentials or calling Google APIs directly.
- Auto-harvest on a timer without an agent in the loop.
- Persisting operator Google identity in OVT config (re-derived fresh each refresh).
- Impersonation or harvesting mail/files the operator does not own.
- Kernel or persistence changes beyond new adapter candidates and taxonomy synonyms.
- Regenerating `docs/generated/` in this plan-only PR.

---

## 8. Owner GO / NO-GO gate

| Gate | Requirement |
|---|---|
| Plan review | Owner reads this v1 DRAFT + ADR 0001 |
| Approval | Explicit GO → flip Status to `APPROVED — FROZEN`, open M1 implementation PR |
| M1 done | All §3.7 tests green; `docs/drive-adapter.md` merged; verifier App green |
| M2 done | All §4.5 tests green; `docs/gmail-adapter.md` merged; semver bumped |

---

## 9. Cross-references

- ADR: [`docs/adr/0001-google-channel-adapters-agent-delegated-fetch.md`](../adr/0001-google-channel-adapters-agent-delegated-fetch.md)
- Slack precedent: [`docs/slack-adapter.md`](../slack-adapter.md)
- Adapter contract: [`docs/system-mechanics.md`](../system-mechanics.md)
- Product invariants: [`docs/product-requirements.md`](../product-requirements.md)
- Doc conventions: [`docs/plans/README.md`](README.md), [`docs/adr/README.md`](../adr/README.md)
