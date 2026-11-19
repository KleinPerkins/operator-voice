# Slack voice capture (agent-driven, MCP-sourced, no token)

**Audience:** both. **Policy:** the explicitly enrolled daily connected-host cycle (or an explicit refresh request) fetches own Slack messages, then OVT verifies and materializes locally. OVT holds no connector credentials and never fetches from its kernel or local curator. [ADR 0007](adr/0007-daily-voice-maintenance.md) replaces the former weekly-offer and independent hourly policies.

## Why this shape (policy, not just mechanism)

Slack, Gmail and Google Drive are the retained integrations. Agents fetch writing through their
existing connected tools and materialize local files for OVT. Agent chats and local notes are
retired (ADR 0004). Giving OVT credentials to fetch on a schedule is **explicitly rejected**:

- **No Slack Web API, bot token, OAuth, or Keychain-stored Slack credential** anywhere in this
  package. `operator_voice.slack.materialize` and `operator_voice.adapters.slack_export` both
  import nothing from any Slack SDK — they only read/write local Markdown.
- **No calling the Slack MCP (or any Slack tool) from the OVT CLI, from `local-jobs`/launchd, or
  from anywhere in the passive `curate` path.** That path stays network-free, exactly like every
  other adapter's scheduled re-scan. All Slack MCP use happens **agent-side**, per the workflow
  below, never programmatically from this package.
- **No auto-harvest without an agent in the loop.** OVT can tell an agent that Slack voice data is
  stale (`slack-status`); it cannot go get fresh data itself.
- **Fresh identity on every call.** Daily enrollment additionally pins a domain-separated HMAC
  of the connected identity in the encrypted control journal. Raw account ids are not stored there.
  Per-record authorship checks remain mandatory; a mismatch cannot silently rebind the account.

Mechanically, once messages are captured, Slack voice data flows through the **exact same** pipeline
as every other source: a `slack_export` adapter registered like any other (see
[System mechanics](system-mechanics.md#2-adapter-scan--observationcandidate)), picked up by the same
daily cycle's `curate` pass, subject to the same authorship gating, synthesis, and review. Nothing about
retrieval, drafting, or correction is Slack-specific past the two operations below.

## The two operations

| Operation | Group / Posture | Does |
|---|---|---|
| `slack.import` (CLI: `slack-import`) | `MUTATION` / `REVERSIBLE` | Accepts a batch of the operator's own Slack messages (agent-captured), verifies authorship **server-side**, deduplicates, and materializes them as per-channel Markdown. |
| `slack.status` (CLI: `slack-status`) | `READ` / `SAFE` | Content-free freshness snapshot: per-channel message counts, when `slack.import` last ran, and a stale/fresh verdict. `--brief` prints one plain-text line instead of full JSON (for a session-start hook). |

Full argument/error/alias reference (auto-generated, never hand-edited):
[`generated/cards/slack-import.md`](generated/cards/slack-import.md),
[`generated/cards/slack-status.md`](generated/cards/slack-status.md).

### `slack.import`'s request shape

```json
{
  "author_id": "U0358NMNN93",
  "messages": [
    {
      "channel": "example-team",
      "channel_id": "C0123",
      "message_ts": "1700000000.000100",
      "time": "2026-07-30 14:02",
      "text": "the raw Slack markdown text of the message",
      "author_id": "U0358NMNN93"
    }
  ]
}
```

Pass it inline (`--author-id` + `--messages`), via `--file <path>`, or over stdin if neither flag is
given (the CLI reads and parses the same envelope shape from stdin as a convenience for agents that
already have it in memory).

### Server-side author verification — the one hard invariant

`author_id` at the top level is the operator's own Slack id, **self-asserted for this call**. Every
entry in `messages` carries its **own** `author_id`, taken by the calling agent directly from the
Slack MCP output's own sender field — never inferred from the message text. `slack.import`
re-verifies each message's `author_id` against the call's top-level `author_id` **before writing
anything to disk**:

- A match is materialized normally.
- A mismatch is **dropped** and counted under the response's `skipped_mismatched_author` — never
  silently accepted, and never partially written.

This holds even if the calling agent's own "these are all mine" filtering was itself wrong (e.g. a
bad search-query assumption pulled in someone else's message) — the check is server-side and cannot
be bypassed by what the agent believes it already filtered. Records are checked against the
identity asserted for this call. Daily enrollment adds a separate HMAC account pin; the
materializer's per-call authorship check remains unchanged. See
`tests/unit/test_slack_materialize.py` and `tests/integration/test_slack_import.py` for the explicit
mismatch-rejection tests.

## Agent workflow

There is no OVT-native Slack fetch. When an agent decides it's time to refresh (see
[When to refresh](#when-to-refresh) below), it drives the Slack MCP itself, using only **read-only,
fail-open** Slack MCP tools (per whatever Slack-MCP-safety guardrails the operator's own
environment enforces) — never a mutating Slack tool:

1. `operator-voice slack-status` — check current freshness (read-only).
2. `slack_read_user_profile` with no `user_id` argument (defaults to the current/self user) — this
   is how the agent obtains the operator's own Slack id fresh, for `author_id`. Never hardcode it,
   never reuse a value from a previous session.
3. Search using the freshly verified explicit author id, never ambiguous `from:me`. Follow the
   daily skill's fixed fetch window, 48-hour overlap and complete pagination. Extract stable channel
   id, `message_ts`, `time`, `text`, and each message's actual sender as `author_id`. Only pass an
   `edited_ts` supplied by Slack; missing edit metadata and older edits outside the overlap are
   coverage limitations. A per-run cap is incomplete and retains its real continuation.
4. Assemble the `{author_id, messages}` envelope and call `operator-voice slack-import --file …`
   (or `--messages`).
5. Either call `operator-voice ingest --source-id <the slack_export source>` immediately, or let the
   daily cycle's `cycle-finish` curate it — both are safe (message-level dedup makes a re-scan a
   no-op for anything already seen).

The agent never stores a Slack token or credential, never scrapes the whole workspace, and never
needs to decide "is this really mine" on its own — step 4's server-side check is the actual
enforcement boundary, not agent judgment.

## When to refresh

The enrolled host runs the daily fetch → durable import → curation → conditional publication
cycle at 18:00 in the configured timezone. A 15-minute due check handles a missed run and one
transient retry; an idle check does not fetch. An explicit refresh uses the same workflow.
Drafting never fetches or offers a weekly refresh.

A completed empty fetch is fresh. A partial or failed fetch does not advance the daily completion
checkpoint; 48 hours without complete success requires attention. Authentication or enrolled
account mismatch requires immediate attention. The old local-jobs timer is on-demand only.
See the [daily runbook](runbooks/daily-voice-maintenance.md) and the portable
[skill workflow](../src/operator_voice/skill/SKILL.md#daily-voice-maintenance).

## On-disk format

`slack.import` writes one Markdown file per channel slug under the fixed
`slack_export_dir` (`RuntimePaths.state_dir / "slack-export"`, created automatically by
`setup`/`paths.ensure()`). Each message becomes a block starting with a machine-parseable HTML
comment carrying Slack's own `message_ts` verbatim:

```markdown
# Slack voice harvest — example-team

Operator-authored messages only. Server-verified authorship (see `operator_voice.slack.materialize`).
Generated by `operator-voice slack-import`.

<!-- ovt:message ts=1700000000.000100 -->
## 2026-07-30 14:02

the raw Slack markdown text of the message
```

`operator_voice.slack.materialize.parse_channel_file` is the **single** parser for this shape,
shared by the `slack_export` adapter (re-scan) and `slack.status` (message counts) — there is
exactly one place that understands this format. A channel name is slugged (`#general` → `general`,
a DM → `dm-misc`, a group DM → `group-dm-misc`, anything else → a lowercase-hyphenated slug) purely
for file grouping; it is **not** the candidate's retrieval-facing `channel`, which the adapter always
sets to the fixed literal `"slack"` (the communication *medium*, matching the closed retrieval
taxonomy — see [System mechanics](system-mechanics.md#4-normalization-and-deduplication)).

Re-importing the same message (same channel slug + `message_ts`) is detected by parsing the file
that already exists on disk and is never appended twice (`skipped_duplicate`); the same dedup
applies within a single batch. Every non-empty authored message is kept on disk; the compatible
`skipped_short` response field stays zero. The adapter keeps the existing analytic floor (25
characters, or 12 for multiline prose), so short messages remain available for future analysis.

## Adapter cursor: changed files are revisited

Slack uses a change-ordered watermark: the later of each file's modification and change times,
then its path. New messages appended to an existing channel and new files sorting before an older
path are scanned. The scan cap applies after filtering by the watermark, so continuation reaches
all remaining files. Legacy path cursors trigger one full rescan, with stable message identities
preventing double-counting. Drive and Gmail retain their lexical cursors for immutable records.

## Setup and conformance

- A fresh `operator-voice setup` idempotently registers a `slack_export` source pointed at
  `slack_export_dir`, alongside the on-demand curation registration — see the `slack_source` block in
  `setup`'s own JSON result. Running `setup` again never duplicates the registration.
- `slack_export` is held to the exact same shared adapter contract as every other source
  (registration guard, canonical-path safety, resource ceilings, determinism, quarantine-not-abort,
  no persistence coupling) via `adapters/conformance.py` — see
  `tests/unit/test_adapter_conformance.py` and `tests/unit/test_adapters.py`.

## Migrating from an old manual Slack harvest

See [Operating procedures → migrating from an old manual Slack harvest](operating-procedures.md#migrating-from-an-old-manual-slack-harvest)
for the exact steps. In short: copy old export Markdown into `slack_export_dir`, then re-run `setup`
(idempotent) — there is no automatic migration in v1.

## Cross-references

- Exact CLI/MCP arguments and errors: [`generated/cards/slack-import.md`](generated/cards/slack-import.md),
  [`generated/cards/slack-status.md`](generated/cards/slack-status.md).
- How this fits the rest of the ingestion pipeline: [`system-mechanics.md`](system-mechanics.md).
- Day-to-day commands and the migration runbook: [`operating-procedures.md`](operating-procedures.md).
- Everything this feature explicitly does **not** do: see [Why this shape](#why-this-shape-policy-not-just-mechanism)
  above — those bullets are the authoritative non-goal list for this feature.
