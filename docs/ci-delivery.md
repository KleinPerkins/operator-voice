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
