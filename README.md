# Operator Voice Toolkit (OVT)

A local, encrypted memory of how one operator writes — so an AI agent can draft in their real voice
without dumping their whole history into a prompt, and without their writing ever leaving their machine
unless they explicitly choose to back it up.

**Status:** GA, `v0.1.5` — milestones M1–M5 complete. Local-first by default (`LOCAL_ONLY`); an optional
encrypted Git backend and cpp delivery are both available through explicit opt-in / the standard
KleinPerkins ecosystem lifecycle, never as a requirement.

OVT learns from Slack, Gmail and Google Drive. Agent chats and local notes are retired; their stored
evidence stays encrypted until the operator explicitly erases it (ADR 0004).

## Quick start

```bash
git clone <this repo> && cd operator-voice
./install.sh              # installs CLI + stdio MCP + Skill; no data leaves your machine
operator-voice setup       # first-run: local encrypted state + Keychain key
operator-voice doctor      # verify everything is healthy
```

Full sequence, including backup/recovery and scheduling: [`docs/operating-procedures.md`](docs/operating-procedures.md).

## Documentation

Start at **[`docs/README.md`](docs/README.md)** — it routes agents and operators to the right doc instead
of repeating them here. In short:

- **Agents**: [`docs/architecture.md`](docs/architecture.md) ·
  [`docs/product-requirements.md`](docs/product-requirements.md) ·
  [`docs/operating-procedures.md`](docs/operating-procedures.md) ·
  [`docs/system-mechanics.md`](docs/system-mechanics.md) · [`docs/topology.md`](docs/topology.md)
- **Operators** (non-technical): [`docs/README.md#for-operators`](docs/README.md#for-operators) — plain
  English, no jargon.
- **Exact CLI/MCP reference**: [`docs/generated/INDEX.md`](docs/generated/INDEX.md).

## Governance

Proprietary KleinPerkins source; all rights reserved — see [`LICENSE`](LICENSE), [`NOTICE`](NOTICE) (third-party
provenance; see also [`docs/audit-provenance.md`](docs/audit-provenance.md)), and
[`SECURITY.md`](SECURITY.md).

<!-- G4 informational proof: the privacy kernel Check rejected a networking import and permits this restored head. -->
<!-- G4 requiredness proof: the named privacy job blocked a networking import and permits this restored head. -->
