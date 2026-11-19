# Daily voice maintenance

Policy: [ADR 0007](../adr/0007-daily-voice-maintenance.md). The portable agent procedure is
[SKILL.md → Daily voice maintenance](../../src/operator_voice/skill/SKILL.md#daily-voice-maintenance).

## Enrollment and replacement

1. Verify merged source, installed version, existing replica checkpoint and corrections. Keep all
   live output under a directory created with `mktemp -d /tmp/ovt-m2.XXXXXX`; print aggregates and
   remove files immediately. Preserve source and backup contents.
2. Read the installed host/scheduler guidance. Verify fresh Slack/Gmail/Drive identities and actual
   fetch capability. Gmail must expose Sent labels and full From/Sender/Auto-Submitted headers.
   Drive must expose target ownership/My-Drive metadata and complete folder traversal. A tool listing
   alone is not an authentication or complete-pagination proof.
3. Read existing selected Drive roots privately through `capture-inventory`; never register a new
   folder without its operator selection. Configure the daily controller from a private identities
   file. It pins HMAC identities, not credentials. Defaults: 18:00 America/Los_Angeles.
4. Through the host's supported automation tool, prepare one continuing-chat wake every 15 minutes.
   Its first action is `cycle-status`; no due work means no connector reads, curation or publication.
   This schedule provides bounded retry and next-awake catch-up without a custom daemon. Preserve the
   host's notification policy: quiet no-op/success, actionable failures only.
5. Through supported local-jobs commands, preview then convert `operator-voice-curate` to
   `execution_mode=on_demand`. Setup/install perform the same conversion. Verify the host entry is
   active and the old timer is absent/not running; run local-jobs doctor. Do not hand-edit configs.
6. Exercise one explicit `cycle-begin --force` through source records and `cycle-finish`. Verify a
   second unchanged curation creates no generation/publication. Separately observe an actual native
   scheduled wake before claiming unattended operation. Record omissions honestly.

## Per-wake contract

Use the portable skill's numbered workflow. Preserve its run id and opaque continuation privately.
Each source has its own successful checkpoint; unfinished work cannot advance another source's
cursor. Use native change streams when available; otherwise complete paginated enumeration. Imported
records/revisions provide safe deduplication. A bounded/capped result is incomplete, not success.

The available host may omit Slack edit metadata or old edits outside the overlap; report that
coverage limitation. Pass only actual provider `edited.ts` values. Gmail's Sent-id enumeration is
not a History stream and cannot attest every historical label change. Do not imply otherwise.

On Friday, inspect completed-fetch age, pending curation, source failures, publication status and
correction uptake. Report new actionable issues once per episode. Acknowledgement means the notice
was delivered, not that the underlying problem is fixed. Do not notify unchanged success.

## Recovery and stability

The encrypted `daily-cycle.enc` control journal is atomic and protected by a run lease. A crash
before finish resumes after lease expiry; committed imports replay idempotently. Never bypass a live
owner or edit the journal manually. A bad key/journal stops. The database-only backup does not restore
this journal; pause the host and inspect control state before any explicitly authorized restore.

The daily path never invokes reextract, erase, restore, account rebinding or folder expansion.
Those retain their existing authorization and exact human challenge boundaries. A replica conflict
requires supported reconciliation; a post-publication defect requires a governed forward repair.

From 0.1.14, `profile-pull` recognizes the exact pending generation after its governed publication
merges, using the existing publication acknowledgement path without publishing or waiting. A remote
advance that does not match that pending publication remains a conflict. Never manually clear sync
state or accept an unrelated checkpoint to make a wake proceed.

Profile changes are measured from live facts and portable content, preserving corrections. Report
new documents/observations alongside unchanged cycles. No new evidence means no new convergence
evidence. This release introduces no channel-voice usability threshold or fallback rule.
