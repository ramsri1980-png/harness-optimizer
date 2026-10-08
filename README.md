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
| get_repo_skeleton | Python symbol map with inclusive line ranges | *** |
| rip_file_lines | Read a precise line window | *** |
| apply_search_replace | Surgical block edit, no full rewrites | *** |
| find_dependent_references | Bounded Python text search (40 matches / 8,000 chars). Not semantic caller analysis. | ** |
| lint_file | py_compile check | -- |
| git_checkpoint | Stage + commit one file at a milestone | -- |
| rollback_show | Read-only impact report before any reset | -- |
| execute_and_capture | Run whitelisted command with clipped output | ** |
| inspect_database_schema | Read-only SQLite table listing | * |

**Plugins and rules:**
- harness.ts — OpenCode v2 plugin that parses [TOKEN METRIC] lines and
  accumulates a running total across sessions. **Note:** the `saved` value is
  an *estimated payload reduction* (`len(text)//4` of full-file baseline minus
  returned excerpt), not a measured end-to-end token saving.
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

## Security Model

`execute_and_capture` uses an **executable-name whitelist** (pytest, python3,
git, ls, cat, grep, rg, find) for convenience — **not for security**.
Whitelisted interpreters like `python3` and `find` are Turing-complete: they
can write files, spawn subprocesses, and access the network.

The tool applies a **best-effort pattern guard** that refuses the most obvious
bypasses (e.g., `python3 -c "open('x','w')..."`, `find -exec rm`, `git config
--global`). This guard is deliberately shallow. It stops accidental misuse and
signals intent — it is **not a sandbox**.

For real isolation when running autonomous agents:
- Run OpenCode inside a container or VM
- Use OpenCode's native per-command permission prompts
- Review every mutation before accepting it

## Model Compatibility

The harness tools are exposed over MCP with strict schemas. Some free-tier
routers and small models hallucinate parameter names (e.g., `path` instead of
`file_path`) and drop the `[TOKEN METRIC]` line from tool output. When this
happens, tool calls fail silently or the metric doesn't display.

**Recommended models:** DeepSeek V3/V4, Claude Sonnet/Opus, GPT-4/4o, or any

## Reducing Tool Overhead

Every enabled MCP tool occupies space in the model's tool list. In a
repository where a specific tool is not useful — for example, a non-git
repo doesn't need `git_checkpoint`, a Python-only repo may not need
`inspect_database_schema` — you can disable it:

**Option A — config UI**: run `/config`, uncheck the tools you don't
need, save. Restart OpenCode.

**Option B — env var**: in `~/.config/opencode/opencode.json`, set:

    "mcp": {
      "servers": {
        "harness-tools": {
          "environment": {
            "HARNESS_TOOLS": "rip_file_lines,apply_search_replace,get_repo_skeleton"
          }
        }
      }
    }

Use `HARNESS_TOOLS=none` to disable all tools, or a comma-separated list
of specific tool names. Disabled tools are not registered with the MCP
client — the model never sees them.

Restart OpenCode after changing this setting.

## License

MIT
