# Operating procedures (runbook)

**Audience:** both. Agents can run everything in this runbook marked safe/reversible on the operator's
explicit request; anything marked **operator-only** needs a human at the keyboard because it touches
real secrets, real recovery material, or a one-time trust decision. Every command below is real and
copy-pasteable; exact arguments/errors for any operation are in
[`docs/generated/cards/`](generated/cards/) — this page is the sequence, that's the reference.

If a term here is unfamiliar, [`docs/README.md`](README.md) has a plain-English glossary-by-example for
operators.

## Install

Two equivalent installers exist; both produce the same `{version, digest}` receipt
(see [Architecture → XDG runtime](architecture.md#xdg-runtime-and-the-atomic-install-contract)):

- **From the product repo** (`KleinPerkins/operator-voice`): `git clone` it, then run `./install.sh`.
- **From the Global Capability Binding** (`capabilities/skills/global/tools/operator-voice`, no access to
  the private product repo required): run its own `install.sh`, which installs directly from an embedded
  payload snapshot.
- **Via cpp**: a cpp-only machine gets OVT through cpp's own sync/bootstrap — no separate action needed;
  see [Topology → ecosystem integration](topology.md#ecosystem-integration).

Either installer:

1. creates a private venv with exactly two runtime dependencies (`cryptography`, `mcp`);
2. installs the `operator_voice` package as an immutable, content-digested runtime slot;
3. writes a `~/.local/bin/operator-voice` wrapper (put `~/.local/bin` on `PATH`);
4. idempotently installs the packaged `SKILL.md` into whichever of `.cursor`/`.claude`/`.codex` skills
   directories already exist.

```bash
./install.sh              # install (no-op if already current)
./install.sh --doctor     # read-only health check
./install.sh --check      # CI-safe static preflight, no writes, no network
```

Requires Python 3.12 on `PATH` (>=3.12,<3.13, matching `pyproject.toml` and the lockfile). Nothing here ever touches `~/dev` or any other repo — see the header
comment in `install.sh` for the full contract.

## First run

```bash
operator-voice setup                 # creates local encrypted state + Keychain KEK; starts LOCAL_ONLY
operator-voice status                # state + next step + active operator ids + consumer-id hint
operator-voice doctor                # deeper health check with the same ids + consumer-id hint
```

A fresh install is `LOCAL_ONLY` — no data leaves the machine unless and until you bind a remote (see
[Optional encrypted-Git backend](#optional-encrypted-git-backend-recovery--cross-machine) below). Every
core capability (ingest, learn, retrieve, correct, inspect, export, erase, turn off) works with zero
network access from this point.

## Day-to-day operation (agents can drive this)

**Before any `context-get` or voice-drafting call, an agent MUST resolve the binary and consumer
id. Resolve the subject when needed — never guess it. The canonical resolution protocol lives in
`src/operator_voice/skill/SKILL.md` Step 0; the short version:**

1. **Binary:** `command -v operator-voice` (or discover the host-provided wrapper name).
2. **Subject id:** `operator-voice subject-list` → read `subject_id`, or
   `operator-voice status` → read `operator_subject_ids`. Each value is an opaque `subj_*` token —
   **never** a name, handle, or email. `context-get` may omit it only when exactly one active
   operator subject exists; otherwise the tool refuses and lists the ids that require an explicit
   `--subject-id`.
3. **Consumer id:** always pass `--consumer-id <your-agent/tool-name>` — it is required.
4. **If subject resolution fails** (error, zero subjects, or ambiguous): tell the operator plainly
   and stop. Never present a generic draft as the operator's voice.

```bash
operator-voice subject-list                                     # Step 0b: resolve the opaque subj_* id
operator-voice source-add --subject-id subj_a1b2c3 --adapter-type manual --canonical-root <path>
operator-voice ingest --source-id <id>                          # one scan + kernel pipeline pass
operator-voice context-get --consumer-id cursor-agent --channel slack  # sole active operator defaults safely
operator-voice draft-validate --subject-id subj_a1b2c3 --draft-text "..."
operator-voice feedback-add --subject-id subj_a1b2c3 --utterance "I never say 'delve'"
operator-voice review-list                                      # contradictions/drift needing a decision
operator-voice learning-status                                  # plain-language "is this still learning me?"
```

Every mutating call that is destructive, irreversible, shared, or a sensitive export returns
`{"status": "confirmation_required", "instructions": "<exact phrase>"}` on the first call and performs
**no change** — relay that exact phrase back to the operator, then re-run with `--challenge-literal`
set to it verbatim. Never invent or guess the phrase; it's bound to that specific action and target.

## Slack voice (agent-driven, MCP-sourced, no token)

```bash
operator-voice slack-import --file /tmp/slack-envelope.json   # or --messages/stdin; see below
operator-voice slack-status                                    # full JSON: counts, last import, staleness
operator-voice slack-status --brief                            # one plain-text line (session-start hooks)
```

OVT never calls Slack itself — no Web API, no bot token, no OAuth, no Keychain-stored Slack
credential. The enrolled daily host cycle (or an explicit request) reads the operator's own messages via
the Slack MCP, then submits them as a `{author_id, messages}` JSON envelope to `slack-import`
(inline `--messages`, a `--file` path, or stdin). Authorship is verified **server-side**: every
message's own `author_id` is cross-checked against the call's top-level self-asserted `author_id`;
a mismatch is dropped and counted under `skipped_mismatched_author`, never silently accepted. A
fresh `setup` registers the `slack_export` source. The daily cycle fetches and durably imports,
then curates once. Explicit local curation is also available on demand. Full policy and format:
[`docs/slack-adapter.md`](slack-adapter.md).

### Migrating from an old manual Slack harvest

If you previously used the `manual` adapter against hand-copied Slack export files:

1. (optional) `operator-voice source-pause --source-id <old-manual-source-id>`
2. Copy the existing `.md` files into `~/.local/state/operator-voice/slack-export/` (the fixed
   `slack_export` directory — see `operator-voice status` / `doctor` for the exact path on your
   machine).
3. Re-run `operator-voice setup` (idempotent) — it registers the `slack_export` source pointed at
   that directory if not already registered.

There is no automatic migration in v1; this keeps `setup` simple and side-effect-free by default.

## Drive voice (agent-delegated, owner-only, no OVT credentials)

```bash
operator-voice drive-import --file /tmp/drive-envelope.json   # or --files/--sync-cursor/stdin; see below
operator-voice drive-status                                   # full JSON: file/revision counts, sync cursor, staleness
operator-voice drive-status --brief                           # one plain-text line (session-start hooks)
```

OVT never calls Google itself — no OAuth client, no token, no API key, no Google SDK/CLI dependency,
and no embedded credentials. The enrolled host cycle fetches the operator's own, **non-Shared-Drive**
files via the operator's own host Drive tools, then submits them as a `{owner_id, sync_cursor, files}`
JSON envelope to `drive-import` (inline `--files`, a `--file` path, or stdin). Ownership is verified
**server-side**, fail-closed: each file must be a My-Drive item (`drive_id` null) owned solely by the
operator (`owners == [owner_id]`, entry `owner_id` matching) — a Shared-Drive, multi-owner, or
mismatched file is dropped and counted (`skipped_shared_drive` / `skipped_shared_ownership` /
`skipped_mismatched_owner`), never silently accepted. Each revision is stored as its own immutable
file, but a file's **persisted identity is its `file_id` alone** — a newer revision supersedes the
stored one only when its `modified_time` is strictly newer (an older, out-of-order arrival is a
no-op). A fresh `setup` registers the `drive_export` source. The daily cycle imports eligible
Google Docs in selected folders/subfolders, then curates once. The host detects cursor expiry or
missing Changes support and falls back to exhaustive enumeration within the same selected scope
(OVT stores the cursor but makes no Google calls). Full policy, the generic fetch recipe, and the
on-disk format: [`docs/drive-adapter.md`](drive-adapter.md).

## Gmail voice (agent-delegated, Sent-only, no OVT credentials)

```bash
operator-voice gmail-import --file /tmp/gmail-envelope.json   # or --messages/--sync-cursor/stdin
operator-voice gmail-status                                   # full JSON: message count, sync cursor, staleness
operator-voice gmail-status --brief                           # one plain-text line (session-start hooks)
```

OVT never calls Google itself. An **agent** fetches the operator's **Sent** mail via host Gmail tools,
then submits a `{author_email, sync_cursor, messages}` JSON envelope to `gmail-import`. Authorship is
verified **server-side**: each message's `from` must equal `author_email` — a mismatch is dropped
(`skipped_mismatched_author`), never silently accepted. Alias/send-as/delegated Sent mail is a known v1
limitation (issue #36). A fresh `setup` registers the `gmail_export` source. The daily cycle
imports Sent mail and curates once. On `historyId` expiry or unavailable History tools, the host
exhaustively paginates Sent ids and retrieves missing records, without inventing a cursor. Full policy:
[`docs/gmail-adapter.md`](gmail-adapter.md).

## Unified Google status (Drive + Gmail)

```bash
operator-voice google-status                                  # full JSON: nested drive + gmail snapshots
operator-voice google-status --brief                          # one combined plain-text line (session-start hooks)
```

Aggregates the local `drive.status` and `gmail.status` snapshots into one stale/fresh verdict.
Still no OVT-side Google calls — reads only persisted import state already on disk. Full detail:
[`docs/google-status.md`](google-status.md).

## Maintain / repair

```bash
./install.sh --repair      # or: operator-voice repair
operator-voice doctor      # verify receipt/permissions/schema/backend/sinks; one plain next step
```

`repair` only re-verifies/replaces the runtime payload — it cannot touch config, local state, Keychain
items, the profile backend, or profile-repo content (see
[Architecture](architecture.md#xdg-runtime-and-the-atomic-install-contract)). If `doctor` reports schema
drift, `operator-voice` runs pending migrations automatically on next open; a downgrade to an
incompatible schema is refused rather than attempted.

## Backup, restore, and recovery

### Local-only (every operator, no setup required)

- `rebuild` recreates a specific `ProfileGeneration` from retained evidence using its pinned version
  bundle — it must reproduce the exact same digest, or it fails rather than silently drifting.
- `reanalyze` re-synthesizes existing observations with current synthesis rules. It does not read
  source text or replace extraction.
- `reextract --subject-id <id> --dry-run` fully scans retained active sources and reports reasons:
  new, kept, reextracted, now filtered, or file missing. Retired, paused and inactive sources remain
  untouched, with their stored document/observation counts disclosed. A missing root or a full scan
  with no candidates while documents remain stops before evidence changes.
- Applying `reextract` requires its exact challenge. A verified encrypted backup is created first;
  evidence replacement, current synthesis, retirement of unsupported inferred facts and generation
  stamping then occur in one database transaction. Corrections and confirmations remain authoritative.
  The backup retains removed evidence and is disclosed, not cleared, by later source/fact erasure.
- `backup-restore --dry-run` previews the newest verified local snapshot (or `--backup-path` selects
  an `operator-voice-*.sqlite3` file directly in the backup directory). Stop other OVT processes and
  pause curation first; a running process or database lock causes refusal. Apply only with the exact
  normal challenge and, if the snapshot predates an erase, the separate erased-evidence challenge.
  Restore changes the database only, not materialized Gmail files or Drive membership sidecars.
  Release rollback must follow the frozen v2 plan's ordered live-gate procedure.
- `persistence/backup.py` takes whole-file encrypted snapshots (already ciphertext at rest) automatically
  around risky operations (e.g. before a schema migration); the last 10 are retained.
- Status quo without any remote: `status`/`profile-backend_status` will say plainly that machine loss is
  not covered — this is intentional honesty, not a bug to fix by nagging.

### Optional encrypted-Git backend (recovery + cross-machine)

**Operator-only**, one-time trust decision — this binds a private Git remote:

```bash
operator-voice profile-remote_bind --remote-url git@github.com:<you>/<private-profile-repo>.git
# returns a confirmation phrase; nothing has changed yet
operator-voice profile-remote_bind --remote-url <same-url> --challenge-literal "<the exact phrase>"
```

Binding alone is **not** a proven backup. Before relying on it to survive machine loss, do one real
recovery drill in a disposable environment: `key.recover` to restore the wrapping key from your
operator-controlled recovery destination, then `profile.restore` to clone + rebuild + verify the
generation digest matches. Only after that passes should you trust the backend.

Ongoing use, once bound:

```bash
operator-voice profile-publish     # validate/encrypt/deliver the latest local generation (exact-parent CAS)
operator-voice profile-pull        # fetch remote head; refuses on divergent conflicting heads
operator-voice profile-sync_status # local/remote generation + publish status
operator-voice profile-reconcile   # merge disjoint typed changes; conflicting corrections queue for review, never auto-merged
```

When the profile remote is enrolled in the fleet merge-safety ruleset (PR required on
`main`), `profile-publish` automatically opens a governed PR on branch
`publish/<generation_id>`, waits for `pr-automation / verify` and the reviewer App squash-merge
(default wait: 600s; override with `OVT_PROFILE_PR_WAIT_SECONDS`), then realigns local sync state
to the merged `main` head. It never calls `gh pr merge` — merge stays the reviewer App's job.
Direct push still works when the remote allows it.

`profile-publish`/`pull`/`restore` never force-push and never silently resolve a real conflict — a
divergent head is surfaced, not overwritten.

### Cross-machine portability (no Git backend required)

```bash
operator-voice portability-export     # passphrase-encrypted (scrypt + AES-256-GCM) portable archive
operator-voice portability-import     # on the new machine; additive — never overwrites your corrections
```

The passphrase is never stored anywhere by OVT; a wrong passphrase or a tampered archive is refused, not
partially applied.

## Erasure

```bash
operator-voice erase --scope fact             # or: source | validation_set | model_call_payload
                                               #     | export_under_control | full_system
```

Each scope returns its own consequence descriptor and confirmation-literal gate first — no erase call
ever runs on the first attempt. `full_system` deletes every content table in dependency-safe order,
destroys local keys, unlinks plaintext export files it can find, and truncates/vacuums the database, then
verifies zero live/decryptable content references across every known sink. If you have a bound
`ENCRYPTED_GIT` backend, note that historical Git ciphertext is **not rewritten** — "erased" there means
the decryption keys are destroyed and verified, not that old bytes disappear from Git history. Copies
outside OVT's control (an unmanaged clone, an iCloud-synced Keychain item, a Time Machine backup) are
disclosed as unverifiable rather than falsely claimed as covered — `doctor`/`setup` will name these
common macOS channels so you can decide, not silently ignore them.

## Rollback

- **Runtime rollback**: reinstall the prior release (`install.sh` targeting an older payload) — the
  atomic slot design means the currently-broken slot is simply not the one made `current` again; nothing
  about install/repair ever mutates personal state, so this is safe on its own.
- **Profile rollback**: `rebuild` against an earlier retained `ProfileGeneration`, or `reanalyze` if you
  want a diff against the newer method instead of a straight revert.
- **`off` is a first-class operation, not "delete a folder."** It persists disabled mode, closes model
  egress, requests cancellation of active operations, waits to a bounded checkpoint, and reports anything
  that couldn't be stopped. `on` re-enables — it never restores previously erased data.

## Scheduling passive curation via `local-jobs`

```bash
operator-voice schedule-plan
```

This read-only operation prints supported local-jobs commands for one **on-demand** `curate`
instance. Setup/install/repair migrate its old timer to on-demand, preserving enabled state and
operator limits. Recurring wakes belong to the connected host's daily cycle. Enroll and verify
that host schedule before replacing live scheduling; never hand-edit a plist or job definition.
Follow the [daily runbook](runbooks/daily-voice-maintenance.md). Weekly drafting offers are retired.

## The render-only dashboard

```bash
python -m operator_voice.dashboard.generate --sample   # committed synthetic example
python -m operator_voice.dashboard.generate             # live redacted snapshot from local state
```

Writes a `.canvas.tsx` (default `dashboard/operator-voice-status.canvas.tsx`) for Cursor's Canvas
surface. It is **read-only by construction**: its only interactive element is a "Refresh" button that
re-runs the generator; it holds no mutable state and cannot dispatch any OVT operation. It shows runtime
posture, subject/fact/review counts, and sync/`EXTERNAL`-egress state — never raw text, fact values, or
an `EXTERNAL` subject's name.

## Uninstall

```bash
./install.sh --uninstall     # or: operator-voice uninstall
```

Removes the venv, runtime slot, wrapper, and installed skill files. **Profile data, config, and keys are
left untouched** — run `operator-voice erase --scope full_system` first if you actually want those gone.

## Cross-references

- Exact arguments/errors/aliases for every operation above: [`docs/generated/cards/`](generated/cards/)
  and the index at [`docs/generated/INDEX.md`](generated/INDEX.md).
- What each step is actually doing underneath: [`docs/system-mechanics.md`](system-mechanics.md).
- Why these boundaries exist: [`docs/product-requirements.md`](product-requirements.md).
