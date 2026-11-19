# ADR 0007: Daily voice maintenance through the connected host

Status: accepted — 2026-10-30 (author-confirmed ready-for-acceptance; owner instruction to implement and replace policy, alongside daily voice maintenance v1)

## Decision

The operator authorized replacing weekly refresh offers and independent hourly curation with
one daily fetch → durable import → deterministic curation → conditional encrypted publication
cycle. The default enrolled schedule is 18:00 America/Los_Angeles. A host wake every 15 minutes
checks a durable due gate; it fetches only for a due daily slot, explicit refresh, unfinished
work, or one transient retry. Missed daily slots coalesce into one catch-up from each source's
last complete checkpoint. The host must be running and connected; this is not an always-on service.

This supersedes only [ADR 0001](0001-google-channel-adapters-agent-delegated-fetch.md)'s prohibition on operator-authorized unattended host fetch,
the old local-jobs-only scheduling decision, and [v2 M2](../plans/channel-type-voices-bounded-context-v2.md)'s weekly-offer policy. Frozen historical
plans and accepted ADRs remain byte-identical; the mutable ADR/plan indexes carry successor links.
The operator's 2026-10-30 instruction is the authority for this policy change. This is a new
maintenance release, not implementation of the remaining channel-type-voice milestones.

OVT holds no connector credentials, SDK, model or network client for fetching. Its kernel remains
pure. The host uses its authenticated read-only tools and each importer still verifies every
record. Enrollment additionally HMAC-pins the three fresh connected identities. A mismatch
requires operator review and cannot silently rebind. Registration of new Drive folders still
requires the operator's selection. Only eligible Google Docs and resolved Doc shortcuts within
those folders/subfolders qualify; folder shortcuts, Slides, shared drives and co-owned files do not.

Each source is independent. A complete empty fetch is success; an unavailable connector, capped
search, remaining page, or failed import is not. Native change cursors are preferred. Where the
host lacks them, exhaustive paginated enumeration with durable replay/deduplication is permitted;
the host must disclose that fallback and never fabricate a cursor. Completeness remains a host
attestation, not something the deterministic kernel can infer from an import batch.

The local daily journal is encrypted, atomically replaced and protected by an exclusive lock and
an expiring run lease. It contains only control metadata, opaque continuation and aggregates.
It is not source evidence, is not published, and is not part of the database-only backup. Existing
schema v6 is unchanged. A damaged journal or missing key fails closed. Record and sidecar writes
are atomic; checkpoints follow durable materialization. Crashes replay safely.

After imports, curation processes durable backlog and computes a canonical semantic profile
digest. Identical evidence does not create a generation. Removing the last learnable prose does
create a generation when it retires inferred facts. Corrections and confirmations keep precedence.
Publication is conditional on changed publishable content or unfinished publication. A conflict
halts publication for reconciliation; no overwrite, force push or automatic restore is allowed.

Transient failures receive one retry after 15 minutes. Authentication/identity failures require
attention immediately; a source with no complete successful refresh for 48 hours requires
attention. Notices are deduplicated by episode and acknowledged only after delivery. Friday's
daily cycle audits freshness, backlog, corrections and publication. Quiet successful/no-op runs
do not notify. Drafting uses existing bounded context and never triggers an implicit fetch.

The legacy local-jobs curation registration becomes on-demand. Installation/setup/repair must
not recreate an hourly timer. A supported host automation owns recurring wakeups; no OVT daemon,
custom scheduler or new service is added. Activation verifies replacement scheduling before
retiring the old timer and verifies both afterwards.

## Why

Hourly local scans cannot learn writing that has not been fetched. Weekly prompts interrupt
drafting and make freshness depend on remembering to answer. One connected daily cycle matches
the rate at which a writing profile needs new evidence while keeping recovery bounded.

The baseline implementation already aggregates historical observations and filters repeated
documents. Replacing its synthesis algorithm would be unnecessary. The concrete defects are
generation triggers tied to touched facets, non-atomic materialization, and disconnected fetch
and curation. Synthetic regressions cover these defects directly.

A daily-only host timer does not establish a 15-minute retry or next-awake catch-up. The small
15-minute due check provides those guarantees while avoiding connector calls and learning on
idle wakes. Actual host wake latency is observable and must not be described as real-time.

## Consequences

This removes weekly offers and the hourly timer, but retains explicit refresh, offline local
curation, existing source formats and lexical Gmail/Drive cursors. Freshness tracks completed
fetches separately from imported file age. No new corpus, destructive rebuild, re-erasure or
folder expansion is authorized by maintenance enrollment.

Stability reporting uses measured profile changes and evidence counts. An unchanged cycle with
no new evidence does not prove convergence or establish a usability/confidence threshold. Channel
voices, fallback, shared traits and correction-scope migration remain outside this work.

Connector pagination, account identity, schedule enrollment, lock behavior and one real cycle
must be verified before claiming activation. Local synthetic tests do not prove unattended host
access. Rollback of control/configuration preserves local evidence; post-publication data repair
uses a governed forward release, not an unreviewed database or code rollback.
