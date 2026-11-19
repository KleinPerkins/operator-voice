# Gmail voice capture (agent-delegated fetch, Sent-only, no OVT credentials)

**Audience:** both. **Policy:** the explicitly enrolled daily connected-host cycle (or an explicit refresh request) fetches own Sent mail, then OVT verifies and materializes locally. OVT holds no connector credentials and never fetches from its kernel or local curator. [ADR 0007](adr/0007-daily-voice-maintenance.md) replaces the former weekly-offer and independent hourly policies.

## Why this shape (policy, not just mechanism)

Every other OVT source is a **file the operator already has on disk** or an export an agent already
fetched. Sent mail lives in Gmail, so the naive approach would be "give OVT a Google OAuth client and
let it fetch on a schedule." That is **explicitly rejected**, identically to the
[Slack adapter](slack-adapter.md) and [Drive adapter](drive-adapter.md):

- **Zero OVT Google credentials.** No OAuth client, refresh/access token, service account, API key,
  or Google SDK/CLI dependency anywhere in this package. `operator_voice.gmail.materialize` and
  `operator_voice.adapters.gmail_export` import nothing from any Google library — they only read/write
  local Markdown. **Stage-0 fetch is delegated entirely to the calling agent's host Gmail tools.**
- **No calling any Gmail tool from the OVT CLI, from `local-jobs`/launchd, or from the passive
  `curate` path.** All Gmail access happens **agent-side**, per the workflow below.
- **No auto-harvest without an agent in the loop.** OVT can tell an agent that Gmail voice data is
  stale (`gmail-status`); it cannot go get fresh data itself.
- **Fresh identity with an enrollment pin.** Resolve the connected account on every refresh.
  Daily enrollment HMAC-pins it in the encrypted control journal; raw addresses are not stored there.

Mechanically, once messages are captured, Gmail voice data flows through the **exact same** pipeline
as every other source: a `gmail_export` adapter registered like any other, picked up by the same
daily cycle's `curate` pass, subject to the same authorship gating, synthesis, and review.

## The two operations

| Operation | Group / Posture | Does |
|---|---|---|
| `gmail.import` (CLI: `gmail-import`) | `MUTATION` / `REVERSIBLE` | Accepts a batch of the operator's own Sent messages (agent-fetched), verifies authorship **server-side**, deduplicates, and materializes each message as its own immutable Markdown file. Also persists the agent-supplied Gmail `historyId` cursor. |
| `gmail.status` (CLI: `gmail-status`) | `READ` / `SAFE` | Content-free freshness snapshot: message count, the persisted sync cursor to resume from, when `gmail.import` last ran, and a stale/fresh verdict. `--brief` prints one plain-text line instead of full JSON. |

Full argument/error/alias reference (auto-generated, never hand-edited):
[`generated/cards/gmail-import.md`](generated/cards/gmail-import.md),
[`generated/cards/gmail-status.md`](generated/cards/gmail-status.md).

### `gmail.import`'s request shape

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

Pass the envelope inline (`--author-email` + `--messages` + optional `--sync-cursor`), via
`--file <path>`, or over stdin if neither `--file` nor `--messages` is given.

### Server-side author verification — the hard invariant

`author_email` at the top level is the operator's own address, **self-asserted for this call**.
`gmail.import` re-verifies every message **before writing anything to disk**: each message's `from`
(or `sender_email`) must equal the top-level `author_email`, else the message is dropped under
`skipped_mismatched_author` — never silently accepted. This mirrors Slack's server-side author check.
See `tests/unit/test_gmail_materialize.py` and `tests/integration/test_gmail_import.py`.

### Known v1 limitations (issue #36)

Gmail v1 verifies each message's `from` equals the call's top-level `author_email`. Legitimate **Sent**
mail the operator actually authored from an **alias / send-as / delegated** address therefore fails
the check and is **silently dropped** — no error, just an absent record (`skipped_mismatched_author`).
Broad alias support is deferred to **M6** (plan §6); v1 names this limitation explicitly so an
operator is not surprised by missing Sent mail.

## Agent workflow (stage 0 — generic, host-agnostic)

There is no OVT-native Gmail fetch. When an agent decides it's time to refresh, it drives the
operator's **own host Gmail integration** (whatever read-only Gmail access the environment provides —
this doc names no specific tool or server on purpose):

1. `operator-voice gmail-status` — check current freshness and read back the last persisted
   `sync_cursor` (read-only).
2. Obtain the operator's own email address fresh from the host Gmail tools — this is `author_email`.
   Never hardcode it, never read it from OVT config.
3. If a refresh is needed, fetch **Sent** messages changed since the last cursor. The concrete
   generic recipe (issue #38):
   - Query the operator's **Sent** mailbox (trash excluded), incrementally from the persisted
     `sync_cursor.history_id` when present.
   - Request fields sufficient to build the envelope: `message_id`, `thread_id`, `internal_date`,
     `from` (or equivalent sender field), `subject`, a plain-text body, label ids (`labels`), and
     `Sender` (`sender`) and `Auto-Submitted` (`auto_submitted`) headers when present. OVT verifies
     authorship against the identity asserted for each call, then stores only its own-text segments.
     This does not pin identity across calls.
   - Keep only messages whose `from` equals the operator's own address (belt-and-braces — the
     server-side check is the real boundary).
   - Capture the response's next `historyId` to send back as `sync_cursor.history_id`.
4. Assemble the `{author_email, sync_cursor, messages}` envelope and call
   `operator-voice gmail-import` over stdin, or use a `mktemp` file with `--file` and delete it immediately.
5. Either call `operator-voice ingest --source-id <the gmail_export source>` immediately, or let the
   daily cycle's `cycle-finish` curate it.

### Full sync on cursor expiry (issue #37)

Gmail's `historyId` has roughly **one week** of retention; an expired `historyId` makes the host tool
error. When that happens the agent must **fall back to a FULL Sent resync**: discard the stored
`history_id`, enumerate the operator's Sent mailbox from scratch (trash excluded), then resume
incremental history-based fetch from the fresh `historyId` on subsequent refreshes. OVT stores
whatever `sync_cursor` the agent supplies verbatim and never interprets it — expiry detection and
the full-sync fallback are **agent-side**, exactly because OVT holds no Google credentials.

## When to refresh

When the host lacks History tools, exhaustively paginate Sent ids and fetch missing immutable bodies
using `capture-inventory`. This fallback does not attest every historical label change. Daily-enrolled
imports require actual `labels` on every message; retain actual Sender/Auto-Submitted headers when
present. Legacy unenrolled record compatibility is unchanged.

The enrolled host runs the daily fetch → durable import → curation → conditional publication
cycle at 18:00 in the configured timezone. A 15-minute due check handles a missed run and one
transient retry; an idle check does not fetch. An explicit refresh uses the same workflow.
Drafting never fetches or offers a weekly refresh.

A completed empty fetch is fresh. A partial or failed fetch does not advance the daily completion
checkpoint; 48 hours without complete success requires attention. Authentication or enrolled
account mismatch requires immediate attention. The old local-jobs timer is on-demand only.
See the [daily runbook](runbooks/daily-voice-maintenance.md) and the portable
[skill workflow](../src/operator_voice/skill/SKILL.md#daily-voice-maintenance).

## On-disk format — immutable, one file per message

`gmail.import` writes **one immutable Markdown file per message** under the fixed `gmail_export_dir`
(`RuntimePaths.state_dir / "gmail-export"`), named `<internal_date_iso>__<message_id>.md` (colons in
the timestamp replaced with `-`). Each file starts with:

```markdown
# Gmail voice capture

Operator Sent mail only. Server-verified authorship (see
`operator_voice.gmail.materialize`). Generated by `operator-voice gmail-import`.

<!-- ovt:gmail id=18c2… thread=18c2… internal=2026-07-30T14:02:00Z -->
## Re: launch timeline

plain text body
```

At scan time the `gmail_export` adapter tags every candidate with `channel=email` and
`output_type=email` (canonical `kernel/taxonomy.py` values).

### Identity is `message_id` alone

Unlike Drive — where one file evolves through revisions — each Sent message is a genuinely
independent utterance. Persisted document identity (`source_native_id`) is **`message_id` alone**.

## Adapter cursor

`gmail_export` reuses `slack_export`'s **exact, unmodified** lexicographic file-path cursor. Because
each record file is ISO-timestamp-prefixed and immutable, this has none of Slack's growable-file blind
spot. See `tests/unit/test_adapters.py::test_gmail_export_adapter_cursor_reuses_slack_algorithm`.

## Setup and conformance

- A fresh `operator-voice setup` idempotently registers a `gmail_export` source pointed at
  `gmail_export_dir` — see the `gmail_source` block in `setup`'s JSON result.
- `gmail_export` is held to the shared adapter contract via `adapters/conformance.py`.

## Cross-references

- Exact CLI/MCP arguments: [`generated/cards/gmail-import.md`](generated/cards/gmail-import.md),
  [`generated/cards/gmail-status.md`](generated/cards/gmail-status.md).
- Design decisions: [ADR 0001](adr/0001-google-channel-adapters-agent-delegated-fetch.md),
  [ADR 0002](adr/0002-drive-gmail-materialize-identity-and-layout.md), frozen plan
  [`plans/gmail-drive-channel-adapters-v2.md`](plans/gmail-drive-channel-adapters-v2.md) §4 (M2).
- Pipeline mechanics: [`system-mechanics.md`](system-mechanics.md).
- Day-to-day commands: [`operating-procedures.md`](operating-procedures.md).


### Own text and repeated evidence (0.1.10)

Sent labels exclude drafts and chats. When supplied, `Auto-Submitted` must be `no` and the
`Sender` address must match the call's asserted author. Display names are parsed away and addresses
are compared ignoring case. Missing optional metadata remains compatible with older envelopes.
Reply and forward tails are excluded, including wrapped reply headers up to three lines and 300
characters. The original body floor is retained, so a short reply above a long quote can count.
Stored headings use `Sent message`, never the reply's subject.

The adapter counts empty own text as `no_own_text` and long link digests as `digest` (at least 20
URL-bearing lines and 1,000 remaining prose words). Every repeated Gmail copy is excluded from
learning, matching own text ignoring case and spacing with a domain-separated keyed digest.
`reextract --dry-run` previews repeated records, file-body trims and remaining header-shaped blocks.
The real rebuild creates a verified database backup before trimming stored Gmail files. Filenames
and record markers remain compatible with 0.1.9; empty records retain their markers. Backups preserve
removed evidence and are disclosed, not cleared.
