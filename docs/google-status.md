# Unified Google status (Drive + Gmail)

M8 deliverable: one combined freshness line for the two Google channel adapters. OVT still
**never** calls Google — this operation only reads the same local import-state sidecars and
content-free counts that `drive.status` and `gmail.status` already expose.

## Operation

| Registry key | CLI | MCP | Posture |
|---|---|---|---|
| `google.status` | `google-status` | `google_status` | `READ` / `SAFE` |

## What it returns

Full JSON includes nested `drive` and `gmail` snapshots (the same shapes as the individual
status operations), plus:

- `stale` — `true` when **either** channel is stale (48 hours since its last complete daily fetch)
- `stale_after_hours` — shared constant (48)
- `brief` — one plain-language dashboard line for session-start hooks

## Session-start hook

```bash
operator-voice google-status --brief
```

Example lines:

- `google-voice: fresh (drive: 3 files/5 revisions, gmail: 12 messages)`
- `google-voice: stale — ask agent to refresh Google voice (drive stale; gmail fresh (12 messages))`
- `google-voice: not configured (no runtime paths)`

Use this when a PM or agent wants a single glance at both Google channels instead of running
`drive-status --brief` and `gmail-status --brief` separately. Per-channel detail remains available
via the individual status operations or the nested JSON fields.

An optional separately governed consumer may display this read-only status. This source successor does not modify any consumer repository or host job.

## Related docs

- [`drive-adapter.md`](drive-adapter.md) — Drive harvest policy and `drive.status`
- [`gmail-adapter.md`](gmail-adapter.md) — Gmail harvest policy and `gmail.status`
- [`operating-procedures.md`](operating-procedures.md) — day-to-day commands


Stale means at least 48 hours since a complete daily fetch (or import for unenrolled manual capture).
It does not trigger fetching or block drafting. The enrolled daily due gate or an explicit refresh
request owns fetching; weekly offers are retired. See [daily maintenance](runbooks/daily-voice-maintenance.md).
