# System mechanics

**Audience:** agents/engineers who need to know *how a request actually flows through OVT* — not just
which module owns which concern (see [Architecture](architecture.md) for that), but the sequence of
decisions, encryption, and persistence that happen between "an adapter reads a document" and "an agent
gets a bundle back."

## The canonical lifecycle

```
enrollment → adapter scan → kernel authorship decision → normalization/dedup → signal extraction
  → synthesis (ProfileFact) → review queue (if contradiction/drift) → encrypted local generation
  → optional encrypted-Git publish → context/insight request → bounded bundle → host agent drafts
  → deterministic (+ optional semantic) validation → feedback → superseding generation
```

Every stage below names the exact module that implements it. This is intentionally an *implementation*
walkthrough — for the invariants each stage must never violate, see
[Product requirements](product-requirements.md#invariants-and-guarantees).

### 1. Enrollment (explicit, no source ships enabled)

`subject.add` registers a `SubjectRegistration` (`OPERATOR` or, with an approved purpose, `EXTERNAL`).
`source.add` registers a `SourceRegistration` scoped to one subject, one adapter type, and a canonical
root — this call **is** the opt-in; nothing is ever collected by default. Both are ordinary `reversible`
mutations through `OperatorVoiceService`.

### 2. Adapter scan → `ObservationCandidate`

`adapters/registry.py` dispatches to three scanners (`slack_export`, `drive_export`,
`gmail_export`) implementing a shared
`scan(registration, cursor=None, limit=...) -> AdapterScanResult` contract (`adapters/base.py`). Adapters:

- resolve paths within an allowlisted root and reject symlink escapes (`resolve_within_root`);
- enforce size/record caps (`read_capped`);
- check the source is active/not paused (`registration_guard`);
- read records materialized after per-call author/owner verification; text never proves identity;
- return `ObservationCandidate`s plus a `next cursor` for incremental (M3 continual-curation) re-scans.

Adapters never touch the database directly — `adapters/conformance.py` structurally denies any
`persistence.store`/`crypto`/`sqlite3` import in an adapter module.

Agent-chat and local-note adapters are retired (ADR 0004). Their enum values remain readable for
existing databases, but new registrations and ingest are refused. Curation skips them without
scanning or recording a failure, and synthesis excludes their observations. Stored evidence remains
until the operator erases each source; `status --brief` displays the notice while documents remain.

### 3. Kernel authorship decision (identity/authorship gating)

`kernel/identity.py` and `kernel/authorship.py` classify each candidate segment as `SUBJECT`, `AI`,
`OTHER` (correctly identified third party), or `AMBIGUOUS` (quarantined for review). This is the
single most safety-critical step in the pipeline:

- Only `SUBJECT`-classified segments ever reach feature extraction — proven end-to-end by
  `tests/integration/test_service.py::test_ai_authored_and_quoted_text_contribute_zero_operator_evidence`.
- `OTHER` (third-party) segments are retained per policy but **never** contribute to the operator's
  profile, and cannot be analyzed as that third party's voice without a separate `EXTERNAL`
  `SubjectRegistration` for them specifically.
- `AMBIGUOUS` segments are quarantined (inspectable, not evidence) rather than guessed either way.
- Hard-coded "AI tell" phrase matches (a pattern inherited conceptually from one audited prior-art repo)
  may raise a review signal but **never delete evidence outright** — the audited repo's silent
  auto-drop behavior was explicitly rejected (see [`docs/audit-provenance.md`](audit-provenance.md)).

### 4. Normalization and deduplication

`kernel/normalize.py` — NFC/whitespace normalization, sentence/word tokenization. Documents dedup on
`(source_id, source_native_id)`; observations dedup on a stable keyed-HMAC `observation_key`, so re-running
`ingest`/`curate` over the same content is a safe, idempotent no-op by construction (`INSERT OR IGNORE`),
not by a client-supplied idempotency token.

### 5. Deterministic signal extraction

`kernel/features.py` (Tier-1: sentence length, function words, punctuation, hedging, pronouns, MATTR,
paragraph structure — behaviorally ported from the strongest audited prior-art methodology) and
`kernel/signals.py` (Tier-2: burstiness, entropy, micro-rhythm, function-word divergence, argument-flow
confidence) produce `SignalObservation`s over `SUBJECT`-authored segments only. Every observation carries
a method version — no optional dependency may silently change what a fact's method version claims to be.

### 6. Synthesis → `ProfileFact`

`kernel/synthesis.py` aggregates observations into `ProfileFact`s and drives the state machine:

```
OBSERVED → SUPPORTED → CONFIRMED / CORRECTED → DEPRECATED
```

- `OBSERVED`: at least one accepted record; hidden from normal retrieval, inspection-only.
- `SUPPORTED`: cleared a calibrated per-facet threshold (`kernel/calibration.py`,
  `kernel/bands.py`) — not a universal "N samples" rule.
- `CONFIRMED` / `CORRECTED`: operator-affirmed or operator-replaced; `CORRECTED` is highest precedence
  and is never silently superseded by a later measurement (a new contradicting observation is queued for
  review instead of auto-applied).
- `DEPRECATED`: reversibly superseded; evidence remains, excluded from normal retrieval.

`MODEL_HYPOTHESIS` (an optional-gateway qualitative proposal) can exist alongside this but never outranks
deterministic evidence without explicit operator confirmation.

### 7. Review queue

Contradictions, drift, single-source facts, and low-confidence facts land in a plain-language
`review.list` queue rather than silently auto-applying or silently dropping. `review.resolve` records the
operator's decision but does not itself mutate a fact — an operator/agent still calls `fact.correct` or
`fact.deprecate` to act on it. This keeps "what did the reviewer decide" and "what changed the profile"
as two separately auditable events.

### 8. Encrypted local persistence

Every accepted change lands in an encrypted local `ProfileGeneration` — see
[Architecture → Crypto](architecture.md#crypto) for the KEK/DEK mechanics and
[Encryption, CAS, and scan mechanics](#encryption-cas-and-scan-mechanics) below for how that's verified
clean. A generation pins its full version bundle (schema, normalization, extraction, synthesis, taxonomy,
policy, tokenizer, feature-registry versions) plus a content digest, so `rebuild` can later reproduce it
exactly and `reanalyze` can honestly diff against a newer method.

### 9. Optional profile-pack publish (`ENCRYPTED_GIT` only)

`LOCAL_ONLY` is the universal default and stops here. If an operator has explicitly bound a private
encrypted Git backend (`profile.remote_bind`), `persistence/profile_pack.py`:

- filters to publishable state only (`OBSERVED` facts never leave the machine);
- serializes approved derived signals/facts/feedback/taxonomy/calibration/provenance digests/generation
  metadata — never raw text, never keys;
- encrypts each object (deterministic AEAD, content-addressed: `profile/objects/<sha256[:2]>/<sha256>.enc`)
  so republishing unchanged content is a no-op, not a new commit;
- delivers via a plain, non-force `git push` — Git's own fast-forward check *is* the compare-and-swap;
  a divergent remote head is refused, never merged or overwritten by this path (`profile.reconcile`
  handles deterministic disjoint-change merging separately; conflicting corrections always queue for
  review).

### 10. Retrieval → bounded bundle

`context.get` (an `OPERATOR`-subject-only, safe read) calls `kernel/retrieval.py`, which:

- ranks facts deterministically (scope match, operator precedence, confidence, recency, fact ID as a
  stable tie-breaker) — repeating the same query against the same generation returns byte-identical
  output;
- respects the caller's token budget (unknown tokenizers get a conservative estimator, never a silent
  overrun);
- evaluates a quality floor **before** truncating — if the budget can't fit the minimum required facts,
  the result abstains rather than returning a misleadingly thin profile;
- never injects the whole profile or raw exemplars by default — the one failure mode every audited
  prior-art repo shared (build plan D4).

`subject.insights` is the `EXTERNAL`-only analogue: it returns a `SubjectInsightBundle` (evidence-bounded
observations + uncertainty + warnings, **no generative instructions**) and refuses outright for an
`OPERATOR` subject or a generation-oriented purpose.

### 11. Host-agent drafting and validation

OVT never generates prose itself (build plan D8 — "host agent generates"). The calling agent drafts using
the bundle, then calls `draft.validate`, which always runs deterministic checks (`kernel/validation.py`:
banned phrases, required/forbidden facts, style fit) and, only when policy permits and the draft is
explicitly flagged high-stakes, an optional semantic/factual comparison through the gateway. The outcome
is one of `validated` / `deterministic_only` / `unvalidated` — the absence of semantic review is surfaced
honestly, never silently treated as a pass.

### 12. Feedback and correction memory

`feedback.add` accepts a plain-language utterance and a target (fact/draft/context). `kernel/feedback.py`
classifies it into confirm/correct/reject/ban/scope/identity/source, records an immutable `FeedbackEvent`
(verbatim utterance, prior/new value, actor, resulting generation), and — because `CORRECTED` is highest
precedence — the very next generation reflects it. This is what "continual learning is P0" means
mechanically, not just as a stated goal.

## Encryption, CAS, and scan mechanics

- **At rest**: field-level AES-256-GCM under a Keychain-backed KEK/DEK hierarchy (full detail:
  [Architecture → Crypto](architecture.md#crypto)). Three at-rest plaintext gaps found by an independent
  M1 security review were fixed and are now regression-tested by
  `tests/integration/test_no_plaintext_content.py`, which reads the **raw SQLite row** (not the decrypted
  API result) and asserts the real value never appears outside its `*_enc` column.
- **Content-addressing**: profile-pack objects are keyed by the sha256 of their encrypted content, so
  republishing identical content is a byte-for-byte no-op rather than a redundant Git object.
- **Portable-payload denylist**: `runtime/payload.py` doesn't just exclude sensitive file types from the
  snapshot it builds — it **raises** if a `.sqlite3`/`.env`/`.pem`/`.key` file exists anywhere in the
  source tree being packaged, even one that wouldn't have been copied. A packaging bug can't silently ship
  a stray secret.
- **Zero-link discipline**: OVT's own source must never reference the specific name of a downstream
  governed repository it's vendored into. A real violation of this (source comments referencing the
  downstream repo by name) was found and fixed during M1 delivery — see `docs/m1-implementation-ledger.md`
  and [Topology → ecosystem integration](topology.md#ecosystem-integration).

## `EXTERNAL` subject enrollment and analysis

`EXTERNAL` subjects are a deliberately separate, more restrictive code path, not a flag on `OPERATOR`:

- Enrollment (`subject.add` with an approved purpose, `subject.alias_add`) requires a stable
  source-native identifier (account/email/document-author ID) with confidence and a collision-review
  record — a display name alone can never merge identities.
- `kernel/external_analysis.py` gates purpose and applies a reviewed **facet denylist** so
  incidentally-proxied sensitive traits (a real risk of any communication-feature analysis) are excluded,
  not merely downweighted; the resulting bundle still carries a proxy-risk warning because the denylist
  reduces but cannot eliminate that risk.
- The kernel refuses, structurally: any `context.get`-shaped or generation-oriented request for an
  `EXTERNAL` subject; explicit protected/sensitive-trait requests; forecasts of future decisions/actions;
  high-impact decision support. These are kernel-level refusals, not something a prompt or Skill wording
  could talk it out of.
- `SubjectInsightBundle/v1` is structurally incapable of carrying generative instructions — confirmed by
  its own frozen schema (`docs/generated/schemas/subject-insight-bundle.v1.schema.json`), which has no
  `instructions`/`ranked_facts` fields at all (unlike `VoiceContextBundle/v1`, which has both).

## Separate, default-off `EXTERNAL` egress

Beyond the kernel-level refusals above, if an operator has explicitly enabled model-gateway egress *for
their own (`OPERATOR`) subject*, that alone changes nothing for `EXTERNAL` data — `external_egress_enabled`
is a second, independently-defaulted-off switch (`application/gateway.py:196-200`) checked separately from
`operator_egress_enabled`. This is tested directly: `tests/security/` includes a fixture proving `EXTERNAL`
egress stays denied when only operator egress is enabled.

## Daily maintenance and local curation

`curate` runs one tick over every active source: incremental per-source cursor advance, catch-up after
downtime, the same strict idempotency as manual `ingest`, stale-run recovery, then a conditional
`ENCRYPTED_GIT` publish (a no-op under `LOCAL_ONLY`). Generations use a canonical semantic profile
digest; duplicate input cannot create a version, and retiring the last inferred fact is a real change.
A second digest of portable objects avoids unnecessary publication. Pending publication still retries.
One exclusive curation lock prevents overlapping manual/host runs.

Under [ADR 0007](adr/0007-daily-voice-maintenance.md), the host fetches daily and invokes this path
after durable imports. An encrypted, atomic control journal records per-source completion, opaque
paging, a run lease, bounded retry and aggregate notices. The journal is separate from the database
backup and never contains source prose. Schema v6 is unchanged. `runtime/scheduler.py` plans one
on-demand local-jobs entry; installation migrates the old hourly timer. The host owns recurring wakes.
See the [daily runbook](runbooks/daily-voice-maintenance.md).

## Extension points for future work

See [Topology → future expansion](topology.md#future-expansion) for the full guardrail list. In summary,
mechanically:

- **New adapters** implement `adapters.base`'s `scan()` contract and register in `adapters/registry.py`;
  `adapters/conformance.py`'s structural checks apply automatically to any new adapter type. Per D-series
  precedent (build plan §13 M4), a new adapter should be demand-proven, not spec-sped — public/bulk
  sources remain non-default.
- **New signals** are added to `kernel/features.py`/`signals.py` with a method version and calibration
  entry (`kernel/calibration.py`) — never silently changing what an existing method version means.
- **New operations** are added once to `domain/operations.py` (+ the matching `OperatorVoiceService`
  method); CLI, MCP, and generated docs derive automatically. This is the concrete mechanism behind
  "surfaces derive from shared primitives" — there is structurally no place to add a CLI-only or
  MCP-only capability without going through the one registry.
- **New consumers of `VoiceContextBundle/v1`** (e.g. a future Template Framework adoption) integrate via
  `context.get` only; the contract-freeze tests in `tests/contract/` protect the bundle shape and the
  zero-import boundary in both directions.
