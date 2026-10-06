# harness-optimizer

One-command installer for a token-optimized MCP tool suite that supercharges
OpenCode with surgical code reading/editing, safe git checkpoints, and live
token-savings metrics.

## Install

    npx harness-optimizer

Then restart OpenCode. Ask: `List your available tools.`

## Commands

    npx harness-optimizer              # install or update
    npx harness-optimizer --doctor     # health check (15 checks)
    npx harness-optimizer --uninstall  # clean removal
    npx harness-optimizer --force      # reset to defaults
    npx harness-optimizer --help       # full usage

## What It Installs

**9 MCP tools** exposed to OpenCode over stdio:

| Tool | Purpose | Token saving |
|---|---|---|
| get_repo_skeleton | AST map of Python repo (classes + signatures only) | *** |
| rip_file_lines | Read a precise line window | *** |
| apply_search_replace | Surgical block edit, no full rewrites | *** |
| find_dependent_references | Find every caller of a symbol | ** |
| lint_file | py_compile check | -- |
| git_checkpoint | Stage + commit one file at a milestone | -- |
| rollback_show | Read-only impact report before any reset | -- |
| execute_and_capture | Run whitelisted command with clipped output | ** |
| inspect_database_schema | Read-only SQLite table listing | * |

**Plugins and rules:**
- harness.ts — OpenCode v2 plugin that parses [TOKEN METRIC] lines and
  accumulates a running total across sessions
- Global AGENTS.md — token protocol, context pressure protocol, plan mode,
  rollback protocol, metric echo rule
- Custom commands: /plan, /config, /rollback-confirm, /help-harness

## Requirements

- Linux, macOS, or WSL (native Windows not supported)
- Node.js 18+
- Python 3.11+
- Git
- OpenCode 2.x

## Documentation

After install, see ~/developer/harness-optimizer/docs/:

- README.md
- ARCHITECTURE.md
- COMMANDS.md
- TOOLS.md
- CONFIGURATION.md
- TROUBLESHOOTING.md

## License

MIT
