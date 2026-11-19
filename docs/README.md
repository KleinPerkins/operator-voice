# Operator Voice Toolkit — documentation map

OVT is a local, encrypted memory of how one specific person writes, applied narrowly and on request —
never a monolithic profile dump. It is GA (milestones M1–M5 complete, `v0.1.5`), local-first by default,
and delivered both directly and through the governed KleinPerkins ecosystem. This page routes you to the
right doc; it doesn't restate any of them.

## For agents

Start here if you're an AI agent installing, operating, or extending OVT on the operator's behalf.

| Need | Go to |
|---|---|
| Daily fetch, curation, recovery, quiet notices and stability | [`runbooks/daily-voice-maintenance.md`](runbooks/daily-voice-maintenance.md) and [ADR 0007](adr/0007-daily-voice-maintenance.md) |
| CI tests, account-bound review and App-owned delivery | [`ci-delivery.md`](ci-delivery.md) |
| How is this built? (components, boundaries, kernel vs. gateway, crypto) | [`architecture.md`](architecture.md) |
| What must I never violate? (privacy, egress, authorship, feedback precedence) | [`product-requirements.md`](product-requirements.md) |
| Exact command to install / repair / back up / erase / schedule | [`operating-procedures.md`](operating-procedures.md) |
| How does a request actually flow end to end? | [`system-mechanics.md`](system-mechanics.md) |
| What connects to what outside this repo? (local-jobs, cpp, orchestrator, Template Framework) | [`topology.md`](topology.md) |
| Exact CLI/MCP arguments, error codes, plain-language aliases for one operation | [`generated/cards/`](generated/cards/) (start at [`generated/INDEX.md`](generated/INDEX.md)) |
| When and how to invoke OVT in a conversation | the packaged [`SKILL.md`](../src/operator_voice/skill/SKILL.md) |

If you're extending OVT rather than operating it: read `architecture.md`'s
[Known gaps](architecture.md#known-gaps) and `system-mechanics.md`'s
[extension points](system-mechanics.md#extension-points-for-future-work) first — several guardrails there
exist because an earlier version of this exact tool (audited as prior art) got them wrong.

## For operators

Start here if you're the person whose voice this is — no technical background assumed.

**What this actually does for you:** think of OVT as a notebook an assistant keeps about how *you*
write — which phrases you use, how formal you are in Slack versus email, what you always avoid — so that
when you ask an AI tool to "draft this in my voice," it actually sounds like you instead of generic AI
prose. It only writes down things you've actually written or explicitly told it; it never guesses or
makes things up, and it never sends your writing anywhere unless you specifically turn that on.

- **It stays on your computer by default.** Nothing leaves your machine unless you explicitly connect a
  private backup, and even then, only encrypted, curated signals go there — never your raw messages.
- **You're always in control.** Tell your agent "that's not how I'd say it" or "I never say X" and it
  remembers that correction *more* than anything it inferred on its own — a correction from you always
  wins.
- **You can inspect, pause, export, or delete everything.** Ask your agent to "show me what you've
  learned," "pause learning from Slack," "export my profile," or "erase everything" — each of those is a
  real, safe, one-command action; anything irreversible always double-checks with you in plain English
  first, using a phrase you have to say back, not just a "yes."
- **It only helps you write; it never impersonates anyone else.** You can ask it to *analyze* how a
  specific colleague tends to communicate (with their name attached and clear boundaries), but it will
  refuse to draft text pretending to be them.
- **Setup is one command, run once**, by you or by an agent helping you: see
  [Operating procedures → First run](operating-procedures.md#first-run). Day-to-day, you don't run
  commands at all — you just talk to your agent normally.

## The foundation (this doc set)

| Doc | Covers |
|---|---|
| [`architecture.md`](architecture.md) | Components, layering, the deterministic kernel vs. the injected default-off model gateway, XDG runtime/install contract, data model summary, crypto design. |
| [`product-requirements.md`](product-requirements.md) | What OVT is for, the invariants it must never break, subject modes, success criteria. |
| [`operating-procedures.md`](operating-procedures.md) | Install, first run, day-to-day, maintain/repair, backup/restore/recovery, erasure, rollback, scheduling, the dashboard, uninstall. |
| [`system-mechanics.md`](system-mechanics.md) | The full ingestion → authorship gating → synthesis → review → persistence → publish pipeline; encryption/CAS mechanics; `EXTERNAL` analysis and egress. |
| [`slack-adapter.md`](slack-adapter.md) | Agent-driven, MCP-sourced Slack voice capture: policy (no token/API/auto-harvest), the `slack.import`/`slack.status` operations, server-side author verification, and the on-disk format. Slack, Gmail and Drive are the retained integrations; agent chats and local notes are retired under ADR 0004. |
| [`drive-adapter.md`](drive-adapter.md) | Agent-delegated, owner-only Google Drive voice capture: policy (zero OVT Google credentials, no auto-harvest), the `drive.import`/`drive.status` operations, server-side owner + not-shared-drive verification, the immutable per-revision on-disk layout, and the `file_id`-alone identity + supersede-if-newer revision model (ADR 0002). |
| [`gmail-adapter.md`](gmail-adapter.md) | Agent-delegated, Sent-only Gmail voice capture: policy (zero OVT Google credentials, no auto-harvest), the `gmail.import`/`gmail.status` operations, server-side author verification, immutable per-message on-disk layout, and known v1 alias/send-as limitations (issue #36). |
| [`google-status.md`](google-status.md) | Combined Drive + Gmail freshness (`google.status`): one dashboard line aggregating local import state from both adapters; still no OVT-side Google calls. |
| [`topology.md`](topology.md) | Every surface (CLI/MCP/Skill/resources/prompts/schemas) and every external integration (local-jobs, ecosystem orchestrator, cpp, Template Framework, Keychain, profile-Git), with current live state. |
| [`audit-provenance.md`](audit-provenance.md) | The in-repo take/adapt/reject record `NOTICE` refers to — which OVT module traces back to which prior-art audit finding. |

## Plans and architecture decisions

| Doc | Covers |
|---|---|
| [`plans/README.md`](plans/README.md) | Convention for milestone build plans (`<topic>-vN.md`, freeze-on-approval). |
| [`adr/README.md`](adr/README.md) | Architecture decision records (Nygard-style, immutable once accepted). |

The immutable whole-product design intent remains at `~/dev/_ops/plans/operator-voice-toolkit-build-plan.md`
(home workspace, in-place versioning) — see [`plans/README.md`](plans/README.md) for how that relates to
repo-local plans.

## Generated reference (never hand-edit; regenerate via `python -m operator_voice.docs.generate docs/generated`)

| Doc | Covers |
|---|---|
| [`generated/INDEX.md`](generated/INDEX.md) | Every operation, grouped read/mutation/lifecycle, with CLI/MCP names and posture. |
| [`generated/INTENTS.md`](generated/INTENTS.md) | The colloquial alias table (router-off fallback). |
| [`generated/resources.md`](generated/resources.md), [`generated/prompts.md`](generated/prompts.md) | MCP resources and prompts. |
| [`generated/cards/*.md`](generated/cards/) | One page per operation: arguments, confirmation gating, examples, errors, aliases. |
| [`generated/schemas/*.json`](generated/schemas/) | Frozen `VoiceContextBundle/v1` and `SubjectInsightBundle/v1` contracts. |

## Evidence and history

Historical milestone ledgers, live maintenance results and the old generated evidence pack remain in the retained private original. They are excluded from this successor; see [source boundary](source-successor.md). The evaluation rules in [`eval-manifest.md`](eval-manifest.md) and dependency provenance in [`sbom.md`](sbom.md) remain available.

## Documentation backlog

Deeper docs deliberately **not** written yet, in priority order, so a future pass (or contributor) has a
concrete starting list instead of rediscovering these gaps from scratch:

1. **MCP host-wiring guide** — how to actually point Claude Desktop/Cursor/etc. at
   `python -m operator_voice.interfaces.mcp` (no installer currently writes this config; see
   [`architecture.md#known-gaps`](architecture.md#known-gaps)).
2. **Calibration/threshold reference** — a plain explanation of what each `kernel/calibration.py` band
   means and how the M1 benchmark/calibration harness (`eval/calibrate.py`,
   `docs/generated/m1-benchmark-baseline.json`) actually derived its numbers, for anyone tuning M2+
   signals.
3. **CONTRIBUTING.md** — repo has none; would cover local dev setup (`uv`), running the test suite by
   marker (`keychain`, `e2e`), and the PR/governance flow already described in
   [`topology.md#governed-delivery-for-this-repo`](topology.md#governed-delivery-for-this-repo).
4. **Threat-model doc, standalone** — today the full threat table lives only in the external build plan
   (§4.4); a repo-local summary (mirroring how `product-requirements.md` summarizes invariants) would let
   a security reviewer work from this repo alone.
5. **`operator-voice-profile` sibling-repo guide** — a short doc (likely living in that repo, cross-linked
   from here) walking through the encrypted-Git backend from the profile-repo side: what's actually in it,
   how to read the redacted manifest, what a teammate reviewing a profile-publish PR-less push should
   check.
6. **CHANGELOG.md** — none exists; historical version records remain in the retained private original.

## Cross-repo pointers (authoritative source stays where it is; don't duplicate)

- Full vision/use-cases/decision log: `operator-voice-toolkit-build-plan.md` (`~/dev/_ops/plans/`).
- Forensic prior-art audit (full rubric + line-level evidence): `operator-voice-toolkit-forensic-audit.md`
  (`~/dev/_ops/plans/`), summarized in [`audit-provenance.md`](audit-provenance.md).
- Generic ecosystem lifecycle rules (WF1/WF2, GCB, SSP paradigm): `OPERATING-MANUAL.md` §10/§10h/§10i
  (`~/dev/`).
- Ecosystem integration specifics for OVT: [`topology.md#ecosystem-integration`](topology.md#ecosystem-integration).
