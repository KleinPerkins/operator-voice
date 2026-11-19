# Product requirements

**Audience:** anyone deciding whether a proposed change fits OVT, and anyone (agent or operator) who
needs to know what OVT promises and refuses to do. For plain-English "what does this do for me," see the
operator section of [`docs/README.md`](README.md#for-operators). For the exact CLI/MCP contract per
operation, see [`docs/generated/INDEX.md`](generated/INDEX.md).

**Authority:** `operator-voice-toolkit-build-plan.md` v4 (`~/dev/_ops/plans/`, in the `KleinPerkins/dev`
workspace) is the immutable source of vision/intent and is never edited after the fact. This document is
a living, in-repo summary of the requirements that document actually resolved to, kept in sync with what
shipped through M5. Where this page and the build plan differ, treat that as a documentation bug to
report, not a license to guess. The operator-authorized daily maintenance policy in
[ADR 0007](adr/0007-daily-voice-maintenance.md) is the explicit successor to the historical
unattended-fetch prohibition, local-jobs-only timer, and weekly refresh offers; other invariants remain.

## Daily maintenance policy

After explicit enrollment, the connected host refreshes own Slack, Sent Gmail and eligible Google
Docs daily at 18:00 in the configured timezone. A 15-minute host due check provides one transient
retry and catch-up after sleep; it does no connector or learning work when not due. Fetch each
source independently, import durably, curate once, and publish changed or pending encrypted content.
The former hourly local-jobs instance is on-demand only. Friday's cycle audits freshness and unfinished
work. Authentication/account changes need immediate attention; no complete refresh for 48 hours needs
attention. Quiet successful/no-op runs do not notify. Drafting never initiates a refresh or weekly offer.

The host owns authenticated read-only fetch and complete pagination. OVT pins enrolled account
identities by HMAC in an encrypted control journal and continues per-record authorship verification.
Incomplete or unavailable fetches are never fresh. Selected Drive folders remain the scope boundary.
Unchanged evidence creates no artificial confidence or profile version; stability is reported with
actual evidence counts, not a new usability threshold. See the
[daily runbook](runbooks/daily-voice-maintenance.md).

## What OVT is for

OVT is the operator's durable **voice memory and application layer**: a local, encrypted record of how
the operator actually writes, applied deliberately and narrowly rather than dumped wholesale into an
agent's context window.

- It observes only explicitly approved sources, and separates the operator's own writing from
  AI-authored, quoted, forwarded, or third-party text before any of it can become "learned voice."
- It accumulates evidence over time and improves from direct operator correction — continual learning is
  a **P0 product mechanic from M1**, not deferred polish (build plan D15).
- It hands an approved agent a small, task-specific `VoiceContextBundle` — never a monolithic profile
  dump (build plan D4; every one of the three audited prior-art tools shared this exact failure mode, see
  [`docs/audit-provenance.md`](audit-provenance.md)).
- It validates authored drafts against the operator's real patterns without inventing facts or quirks.
- It can analyze a separately enrolled third person's *communication patterns* for evidence-bounded
  insight, while refusing to ever generate or impersonate that person's voice.
- It runs completely on encrypted local state for every operator; an operator may additionally opt into an encrypted private Git replica for clean-machine recovery;
  this is never a default or a requirement.

### What OVT explicitly is not

An AI-detection-evasion tool, a covert surveillance system, a personality/mental-health profiler, an
autonomous publisher, or a mechanism for impersonating another person. The build plan rejected an entire
class of "detection risk" guidance found in one audited prior-art repo for exactly this reason (Decision
D3) — OVT's statistics describe the operator's own patterns; they are never framed as tricks to fool a
detector.

## Problems solved (condensed)

| Problem | OVT's answer |
|---|---|
| Voice memory is fragmented and disposable across transcripts/sessions | Durable encrypted local profile, survives context-window and machine turnover |
| Conversation exports mix operator, agent, and third-party text | Kernel-level authorship classification; only `SUBJECT`-authored segments ever become evidence |
| Prior tools are one-shot snapshots with no feedback loop | Explicit `FeedbackEvent` state machine with correction precedence that never silently reverts |
| One universal "voice" is false | Context-scoped facts (channel/audience/purpose/language), not one blob |
| Analysis ≠ application | Deterministic, token-budgeted retrieval with a quality floor and abstention |
| Untested thresholds look authoritative | Calibrated bands with method versions, not asserted heuristics |
| Durability conflicts with privacy | Local-only by default; encryption + explicit, narrow opt-in for remote backup |
| Multi-surface access can drift | One operation registry drives CLI/MCP/docs/help — no second implementation to drift |
| Analyzing another person is a different risk class | Separate `EXTERNAL` subject mode with deterministic refusals |

## Invariants and guarantees

These are the properties any future change to OVT **must preserve**. Each links to the mechanism that
enforces it (not just a stated policy) in [System mechanics](system-mechanics.md) or
[Architecture](architecture.md).

- **Privacy / retention-by-default.** Raw text, held-out samples, validation drafts, and model payloads
  default to **zero-day retention**. Derived signals remain sensitive and erasable even though they're
  pseudonymous. Local state is encrypted (Keychain-backed KEK, per-purpose DEKs — see
  [Architecture → Crypto](architecture.md#crypto)); directories/files are `0700`/`0600`.
- **Egress is closed by default, and split in two.** The model gateway is disabled unless explicitly
  configured (`application/gateway.py`, `enabled=False` by default). Even when configured, there are
  **two independent switches** — operator-subject egress and `EXTERNAL`-subject egress — and enabling one
  never enables the other. Raw and quarantined content can never be sent regardless of policy
  (`INELIGIBLE_DATA_CLASSES`, checked first). `ModelCallRecord` has no field capable of holding the
  payload sent — zero retention is structural, not a promise.
- **Authorship precedes learning.** No text becomes operator-voice evidence until the kernel has
  classified it as `SUBJECT`-authored. AI-authored, quoted, forwarded, or ambiguous text is quarantined or
  excluded, never silently absorbed. A correctly identified third party's text can never feed the
  operator's profile, and cannot be analyzed as *that person's* voice without a separate, explicit
  `EXTERNAL` subject enrollment — using an `OPERATOR` source can never be a backdoor around that.
- **Operator feedback is P0 and outranks inference.** Precedence is fixed:
  `CORRECTED > CONFIRMED > SUPPORTED > OBSERVED`; `MODEL_HYPOTHESIS` never outranks deterministic
  evidence without explicit operator confirmation. A new observation may *contradict* a correction (it
  gets queued for review) but can never silently overwrite it.
- **`LOCAL_ONLY` is a complete product, not a crippled default.** Every core verb — ingest, learn,
  retrieve, validate, give feedback, inspect, export, erase, turn off — works with zero network access
  and zero Git dependency. Remote publish/pull/restore verbs perform no network action in `LOCAL_ONLY` and
  say so plainly rather than silently no-oping.
- **Dual distributors converge on one digest, never a hierarchy.** A direct home install and the
  cpp-vendored payload are two independent distributors of the *same* immutable runtime. Both write an
  install receipt `{version, digest}`. Same digest → idempotent no-op. Same version + different digest →
  refuses (possible tamper), never silently overwrites. A healthy different version is never clobbered or
  downgraded automatically. See [Architecture → XDG runtime](architecture.md#xdg-runtime-and-the-atomic-install-contract).
- **Repair cannot touch personal state.** `repair` (runtime-payload verify/replace only) has no code path
  to config, local state, Keychain items, the profile backend, or profile-repo content — enforced by the
  function's own signature, not just a rule someone has to remember.
- **Consequential actions require a live, bound, single-use challenge.** Export (sensitive), erase, and
  any live-destructive correction require the kernel to compute a fresh consequence descriptor and return
  an exact plain-language literal; the caller must echo that literal back. Direct CLI/MCP/service calls
  cannot bypass this — it is enforced in the kernel, not in UI/Skill wording.
- **Erasure is honest about what "erased" can mean.** `erase` deletes content in one crash-safe operation
  and rotates/destroys the relevant encryption keys, verified across every known sink. Historical Git
  ciphertext (for `ENCRYPTED_GIT` users) is not rewritten — "erased" there means the decryption keys are
  destroyed and verified, not that old bytes vanish. Unknown/out-of-registry copies (e.g. Time Machine, a
  synced Keychain) are disclosed as unverifiable, never silently claimed as covered.

## Subject modes

| Mode | Purpose | Hard boundary |
|---|---|---|
| `OPERATOR` | Voice generation context, draft validation, analysis of the operator's *own* authored text | Still source/consumer-allowlisted; third-party segments inside an operator source never contribute evidence; no autonomous sending |
| `EXTERNAL` | Evidence-bounded historical communication-pattern analysis of a separately enrolled person | `context.get` and any generation-oriented request refuse at the kernel; forecasts of future decisions, explicit sensitive/protected-trait requests, and high-impact decision support all refuse; a reviewed facet denylist limits proxy leakage; requires explicit `SubjectRegistration` + stable source-native identifiers — a display name alone is never sufficient to merge identities |

## Success criteria

M5 defined a **preregistered, digest-pinned** scoring formula (`src/operator_voice/eval/manifest.py`,
human-readable rules in [`docs/eval-manifest.md`](eval-manifest.md)) across ten dimensions — efficacy,
accuracy, precision, consistency, resilience, performance, privacy/safety, operator friction, integration
cohesion, and overall build quality — each independently gated at **≥95/100**, with an explicit
`insufficient_evidence` abstention rather than a guessed or interpolated score for any unmeasured
dimension. Weights were frozen **before** any real evidence was collected against them (D21: "changing
weights after results is forbidden").

Historical release scores, generated evidence and live milestone ledgers remain in the retained private original. They do not qualify this successor. Re-run the relevant source tests and obtain independent release evidence for the successor identity; see [source boundary](source-successor.md).

## Non-requirements (explicitly out of scope)

- A runtime manager hierarchy across distributors (version+digest convergence is sufficient).
- Mandatory Git sync for every operator (`LOCAL_ONLY` is a first-class, complete product).
- A personal-use quality-bar carve-out (the ≥95×10 bar applies uniformly).
- M1 dependence on `intent-router` or `kp-mcp-bootstrap` runtimes that did not yet exist when OVT shipped
  (OVT ships its own deterministic aliases and explicit CLI/MCP/Skill fallback; a future router may
  *propose*, never authorize).
- Public-web crawling as a default source (one audited prior-art repo did this; it was explicitly
  rejected as a default because it doesn't match "approved operator-authored" sources — see
  [`docs/audit-provenance.md`](audit-provenance.md)).

## Cross-references

- Full vision, use cases (UC1–UC12), goals (G1–G10), and threat model: build plan §1–§4, §9
  (`~/dev/_ops/plans/operator-voice-toolkit-build-plan.md`).
- Forensic prior-art audit and scoring rubric that justified each design decision:
  `~/dev/_ops/plans/operator-voice-toolkit-forensic-audit.md`, summarized in
  [`docs/audit-provenance.md`](audit-provenance.md).
- How these invariants are mechanically implemented: [`docs/system-mechanics.md`](system-mechanics.md).
- Where each guarantee is enforced in the codebase: [`docs/architecture.md`](architecture.md).
