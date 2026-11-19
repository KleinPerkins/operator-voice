# Topology — surfaces, integrations, and repo map

**Audience:** agents/engineers who need to know *what OVT connects to* outside its own process, and where
the authoritative source for each connection actually lives. Everything here is cross-referenced to a
real file at a real path — this page routes, it doesn't restate.

## Repository boundary

This source successor contains the product, installer and reference documentation. An optional encrypted profile backend remains a separate operator-selected private repository. Global capability and downstream distribution continue to be owned by the ecosystem orchestrator; this preparation changes no consumer. Historical deployment details remain in the private original.

## Agent-facing surfaces

Every surface below derives from the **one** operation registry (`domain/operations.py`) —
see [Architecture → the one operation registry](architecture.md#the-one-operation-registry).

| Surface | Where | Notes |
|---|---|---|
| CLI | `interfaces/cli.py`, wrapper at `~/.local/bin/operator-voice` | Always available once installed; every operation including the 9 CLI-only `lifecycle` ones. |
| stdio MCP | `interfaces/mcp.py` | **Read-only by default** — only the 20 `read` operations are exposed unless the host sets `OPERATOR_VOICE_MCP_ENABLE_MUTATIONS=1`; the 9 `lifecycle` ops are never MCP-exposed at all. No host wiring (e.g. `claude_desktop_config.json`) is installed automatically — see [Architecture → known gaps](architecture.md#known-gaps). |
| Skill | `interfaces/skill.py`, packaged `skill/SKILL.md` | Installed idempotently into whichever of `.cursor`/`.claude`/`.codex` skills dirs exist; thin conversational veneer, never duplicates business logic. |
| `help` | `domain/help.py` | Deterministic ranking over the full plain-English request; never invokes an operation itself. |
| Colloquial aliases | `interfaces/intents.py` | Router-off fallback table (~24 operations × plain-English phrases); works with zero `intent-router` dependency. |
| MCP resources | `docs/resources.py` | 13 resources: 7 static guidance (`ovt://guidance/*`), 6 dynamic redacted status (`ovt://status/*`); all content-free/redacted by construction. |
| MCP prompts | `docs/prompts.py` | 3 typed authoring scaffolds (`voice-matched-draft`, `correct-my-voice`, `external-subject-brief`); non-authoritative, no mutation path. |
| Schemas | `docs/generated/schemas/*.json` | Frozen `voice-context-bundle.v1` and `subject-insight-bundle.v1` required-field contracts. |
| Generated docs | `docs/generated/{INDEX,INTENTS,resources,prompts}.md`, `docs/generated/cards/*.md` | Rebuilt from the registries by `python -m operator_voice.docs.generate`; byte-stable, never hand-edited. |

## Ecosystem integration

The product builds a content-addressed portable payload, and distributors converge on the same version/digest receipt. The ecosystem orchestrator owns any governed downstream vendoring. A source successor does not replace existing consumer pins, prove propagation or alter installed runtime/profile state. The optional encrypted Git backend remains operator-configured and is never an install prerequisite.

## Governed delivery for this repo

The authorized source-delivery successor uses meaningful sharded GitHub Actions, independent review
from the PR-creation Codex account, and Chaos Scheduler App4217367-owned native squash. Source admission,
publication, actual CI/App readiness and activation remain distinct; see [CI delivery](ci-delivery.md).
Agents never manually merge, manipulate auto-merge or bypass checks. Historical `.github/pr-*` records
retain prior provenance and are not the successor gate.

`operator-voice-profile` is separately retained private with no CI. This source conversion does not
publish profiles, alter the encrypted archive protocol or install/schedule any host job.

## Future expansion

See [System mechanics → extension points](system-mechanics.md#extension-points-for-future-work) for the
mechanical "where do I plug in" list. The guardrails any future change to this topology must preserve:

- **The demand-proven-adapter rule**: a new source adapter (email/Slack/Docs/etc.) ships only when a real
  need is demonstrated, behind the same consent/registration contract as every existing adapter — public
  or bulk collection never becomes a default source.
- **Surfaces derive from shared primitives, never fork**: CLI/MCP/docs/help must never grow a
  surface-specific capability that bypasses the one operation registry — that would silently reintroduce
  the exact multi-surface drift the registry exists to prevent.
- **Zero-link stays zero**: OVT's own source and payload must never reference a specific downstream
  governed repository by name (a real violation of this was found and fixed during M1 — see
  the retained private original).
- **No new distributor without receipt convergence**: any future third distribution path must converge on
  the same `{version, digest}` receipt contract — never a second, competing install mechanism.
- **The Template Framework boundary stays one-directional**: `VoiceContextBundle/v1` is the only shared
  artifact; OVT must never gain template awareness, and a future consumer must never store or mutate OVT
  state.
