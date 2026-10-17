# Lean mode

Cuts per-request and per-call token cost. Opt-in; with `HARNESS_LEAN` unset
every tool behaves exactly as before.

## Turn it on / off

    python3 scripts/set-lean.py                  # on, 4-tool profile
    python3 scripts/set-lean.py --tools minimal  # 2 tools
    python3 scripts/set-lean.py --off            # off
    python3 scripts/set-lean.py --dry-run        # preview, change nothing

It edits only `mcp.servers["harness-tools"].environment` in
`~/.config/opencode/opencode.json` (backup first). Restart OpenCode.
The CTX plugin runs in OpenCode's process, so also export `HARNESS_LEAN=1`
(and optionally `HARNESS_CTX_MAP_TOKENS=800`) in the shell that starts OpenCode.

## What changes

| Area | Default | `HARNESS_LEAN=1` |
| --- | --- | --- |
| `[TOKEN METRIC]` line in every result | inline | not returned; appended to a JSONL log |
| sha256 fingerprint in reads/edits | 64 hex | 16 hex (edit accepts a prefix of 12+) |
| `apply_search_replace` result | multi-line + diff | `OK L2-2 sha256:... (was ...)`; diff only if `HARNESS_DIFF_LINES>0` |
| `get_repo_skeleton` outline | uncapped, 500 files | capped by `max_tokens`, 200 files |
| `find_dependent_references` | 40 matches / 8000 chars | 20 / 3000 |
| identical repeat read or map | full output again | `UNCHANGED: ...` within `HARNESS_DEDUP_WINDOW` calls (default 8) |
| CTX repo-map packet | rebuilt every repo question | reused while fresh (`HARNESS_CTX_MAP_TTL_S`, default 600) |

## Environment variables

- `HARNESS_LEAN` 1/true/yes/on enables lean mode (MCP server and CTX plugin).
- `HARNESS_DIFF_LINES` lines of diff to echo from edits (default 0).
- `HARNESS_DEDUP_WINDOW` how many dedup-capable calls count as "still in context" (default 8; 0 disables).
- `HARNESS_METRICS_LOG` JSONL path, or `off` (default in lean: `~/.local/state/harness-optimizer/metrics.jsonl`).
- `HARNESS_CTX_MAP_TOKENS`, `HARNESS_CTX_MAP_TTL_S` CTX map size / freshness.

## Measure it

    ~/developer/harness-optimizer/venv/bin/python scripts/measure-overhead.py
    python3 scripts/server-metrics.py --since 24h

`measure-overhead.py` counts the schemas the model actually receives plus
AGENTS.md. `server-metrics.py` summarizes the lean log; its numbers are
estimates, not provider-reported usage. `scripts/poc-measure.sh` gives real
provider numbers.

## Notes

- With inline metrics off, the OpenCode plugin's running totals and
  `scripts/tool-calls.sh` stop growing; use `server-metrics.py` instead.
- `UNCHANGED` assumes the earlier output is still in the model's context. After
  a context compaction, ask for the read again with a slightly different range,
  or set `HARNESS_DEDUP_WINDOW=0`.
