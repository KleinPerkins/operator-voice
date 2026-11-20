# CI and source delivery

The CI candidate preserves the existing synthetic suite in six required shards: unit, integration,
contracts/property/security (plus the existing dependency-free privacy checks), evaluation, native,
and delivery. Native runs the real disposable Keychain round-trip and full installer cycle on a
standard hosted macOS runner. No private operator profile, real messages, credentials, capture service
or controller is a test dependency. All actions are commit-pinned, permissions read-only, jobs bounded,
and `ci-required` requires each shard's successful receipt for the same run, attempt and tested revision.

Install the locked test dependencies with `uv sync --locked --extra dev`. Run
`.venv/bin/python -I -B scripts/verify.py local` for the non-Keychain, non-installing local subset.
The verifier disables passive curation enrollment in every child suite so synthetic setup cannot alter
host scheduling. The output explicitly leaves the native shard unverified locally. Hosted CI requires it; it is never
optional there. The existing `--include-native` local option runs it only when that host test is intended.

The authoring agent coordinates one fresh independent review from the PR's originating Codex account,
then a separate account-bound PR-creation witness and the unedited review receipt. The shared GitHub
username does not identify the originating account. Use the qualified enrolled account route; a missing
route or failed review blocks delivery without a human technical reviewer or another-account substitute.

The existing Chaos Scheduler App4217367 owns exact-head approval and native squash. Initial source
admission remains an owner administrative operation. Keep its writer off until current-main CI,
read-only App readiness, actual current-PR receipt admission and owner-visible controls pass. Never
manually merge, arm auto-merge or weaken controls. Publication/history exposure clearance is separate.
The private profile repository, installed runtime and scheduled jobs are outside this source change.

## Bootstrap checkpoint (2026-11-20 UTC)

The initial WORK enrollment was admitted in [PR #1](https://github.com/KleinPerkins/operator-voice/pull/1)
as `79ad6a7d3d3f90c48f26cd128ed430de477f4328`. Its tree matches the reviewed enrollment
candidate. [Main CI run 44696766527, attempt 1](https://github.com/KleinPerkins/operator-voice/actions/runs/44696766527)
passed all six shards and `ci-required`, including the hosted native checks.

With the writer off, [read-only App qualification run 44697265318](https://github.com/KleinPerkins/operator-voice/actions/runs/44697265318)
qualified that same main revision using App4217367 (`chaos-scheduler-automerge`), scoped to this
repository. Both contributing rulesets reported `current_user_can_bypass=never` for the App;
the qualification reported `mutated=false`. The App token could not enumerate bypass actors,
so owner-visible ruleset inspection remains a separate activation check.

This checkpoint establishes bootstrap admission, main CI and read-only App readiness. Ordinary
delivery still requires a fresh same-WORK review, creation witness, unedited receipt and current
head/base CI, followed by read-only receipt qualification before owner-controlled writer activation.
An actual App-owned squash and passing merged-main CI are required to establish ordinary delivery.
