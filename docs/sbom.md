# M1 software bill of materials (SBOM) and licensing

**Authority:** build plan §13 M1 acceptance evidence category "licensing/SBOM." OVT itself is
`license = "Proprietary"` (`pyproject.toml`; see `LICENSE`/`NOTICE`) — a proprietary KleinPerkins source repository,
never intended for redistribution outside the KleinPerkins ecosystem lifecycle described in
`README.md`.

## Runtime dependencies (declared in `pyproject.toml`'s `[project.dependencies]`)

| Package | Pinned range | License | Role |
|---|---|---|---|
| `cryptography` | `>=48.0.1,<49.0` | Apache-2.0 OR BSD-3-Clause | AES-256-GCM AEAD, Keychain-wrapped key material (`persistence/crypto.py`) |
| `mcp` | `>=1.2.0,<2.0.0` | MIT | stdio MCP server surface (`interfaces/mcp.py`) |

Pinned ranges above are read directly from `pyproject.toml` (single source of truth via
`runtime/requirements.py`). This table intentionally omits a point-in-time "observed version" column —
regenerate that locally (see below) rather than trust a number that drifts the moment a range is bumped;
the `cryptography` range above was itself bumped once already during M5-B hardening without this table
being updated at the time, which is the drift this note exists to prevent recurring.

Both licenses are permissive and compatible with this proprietary source repository:
neither imposes copyleft, network-service disclosure, or attribution-in-UI obligations beyond
standard notice preservation, which `NOTICE` already satisfies for third-party dependencies.

## Portable payload dependency scope

The GCB-embedded portable payload (`operator_voice.runtime.payload.build_portable_payload`)
contains ONLY this repository's own `.py`/`.md`/`.json` source files — it embeds no third-party
dependency source. A downstream installer resolves `cryptography`/`mcp` itself, from its own
package index, at install time (`install.sh`'s venv-creation step) — exactly like the SSP's own
`install.sh`. This means the payload's own SBOM is empty by construction; the dependency SBOM
above is the complete picture for any environment that installs OVT via any distributor.

## Dev-only dependencies (never shipped in the runtime payload or venv)

| Package | Pinned range | License |
|---|---|---|
| `pytest` | `>=9.0.3,<10.0` | MIT |
| `pytest-cov` | `>=5.0,<6.0` | MIT |
| `hypothesis` | `>=6.100,<7.0` | Mozilla Public License 2.0 |

`payload.build_portable_payload`'s allowlist (`.py`/`.md`/`.json`/`.toml`, never `tests/`) already
guarantees these never reach any installed runtime — this table exists purely for completeness of
the SBOM, not because any distribution path could leak them.

## Regenerating this table

Run `pip show <package>` inside the repo's dev venv (`pip install -e ".[dev]"`) for each entry in
`pyproject.toml`'s `dependencies`/`optional-dependencies.dev` lists, or use `pip-licenses` if a
future milestone wants a machine-generated SBOM artifact rather than this hand-maintained table
(M1 has exactly 2 runtime + 3 dev dependencies, all leaf packages with no dependency-of-concern
transitive tree — a generator is not yet justified by the scale).
