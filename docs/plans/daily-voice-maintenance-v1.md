# Daily voice maintenance v1

- **Version:** v1
- **Status:** APPROVED — FROZEN (operator's explicit 2026-10-30 instruction to implement and replace policy)
- **Date:** 2026-10-30
- **Supersedes:** scheduling and weekly-offer policy only in historical channel-type v2 and the original passive-curation design
- **Decision:** [ADR 0007](../adr/0007-daily-voice-maintenance.md)
- **Release:** 0.1.12

## Authority and scope

The operator requested implementation of the recommended daily cycle and replacement of existing
policy/infrastructure. This is the successor maintenance policy. Frozen plans, accepted ADRs,
contract tests and golden fixtures remain byte-identical. Remaining channel-type-voice milestones
are excluded. No raw operator writing belongs in tests, commits, review or reports.

## Delivery sequence

1. Prove semantic duplicate churn, empty-revision retirement and interrupted-materialization
   failures with synthetic tests; repair generation/publication decisions and durable imports.
2. Add encrypted durable daily control, per-source complete checkpoints, account pinning, exclusive
   ownership, catch-up, one 15-minute transient retry, Friday audit and episode notices. Preserve
   the off switch and paused/inactive sources. Publish only changed or pending portable content.
3. Replace weekly drafting offers with background freshness. Convert the local-jobs instance to
   on-demand through its supported CLI/planner; do not hand-edit a job or plist. Update skill,
   living policy, generated operation docs and release metadata.
4. Run isolated focused and broad tests, purity, frozen contracts and doc governance. Review the
   exact diff with the installed Bugbot skill's supported mechanism. Deliver a governed PR and
   verify its merge on freshly fetched main. Never directly arm native auto-merge.
5. Deploy from a clean merged checkout. Qualify all three current host connectors and complete
   paginated fetch paths. Enroll the existing selected scope and pin fresh identities. Activate
   one native host heartbeat, verify its schedule, convert/verify the legacy timer, and run one
   real daily cycle with all private output beneath `mktemp -d /tmp/ovt-m2.XXXXXX`, deleting it
   immediately. Keep reporting to aggregate counts. Auth/scope conflicts remain operator-owned.
6. Verify freshness and independent source outcomes, corrections, no repeated-evidence inflation,
   no-op repeat, pending-publication recovery and supported scheduling. Propagate the new payload
   through Skills → ecosystem-orchestrator → governed downstream vendoring. No hand vendoring.

## Readiness and authority

ADR 0007's Decision, Why and Consequences are complete and author-confirmed final, with no open
design questions. Its alternatives are grounded in the existing hourly local-only scan and native
host scheduling. Mutable indexes link the historical decisions to their successor without rewriting
accepted bytes. The operator explicitly instructed implementation and policy replacement; this
instruction supplies GO for this maintenance scope. Deployment and unattended execution remain
verification gates, not claims implied by policy approval.

## Acceptance

- Historical evidence remains cumulative; repeated text adds no artificial evidence/confidence.
- An identical profile produces no new generation; empty revisions retire obsolete inference.
- Files are atomic and cursor state advances only after durable materialization; replay is safe.
- Two hosts cannot own one cycle; expired owners cannot finish it. Off/paused sources remain off.
- DST, sleep gaps, partial failures, bounded retry and notification episode rules are tested.
- Complete empty fetches are fresh. Partial/unavailable sources never claim full coverage.
- Drafting does not fetch or offer refresh. The independent hourly timer is absent.
- The encrypted journal contains no writing or credentials; account ids are HMAC-pinned.
- Corrections, bounded context, provenance exclusion, schema v6 and frozen files remain intact.
- Measured stability is reported with new-evidence counts, never a fabricated confidence gate.

## Verification ledger

Execution evidence, policy-driven test replacements, connector limitations, release and propagation
links are recorded in a separate delivery report; this plan is byte-frozen with ADR acceptance.
