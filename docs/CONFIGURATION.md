# Configuration

## Re-running the installer
The installer merges, it does not overwrite. Existing providers and agents
are preserved. Use `--force` to reset to defaults.

## Per-task model routing
OpenCode routes models per agent, not per MCP tool. Use multiple agents
with different models and delegate via @agent-name in AGENTS.md.

## Tool toggling
Set HARNESS_TOOLS in the MCP server block's environment to enable a subset.
Example: "HARNESS_TOOLS": "get_repo_skeleton,rip_file_lines,apply_search_replace"
