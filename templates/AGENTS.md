<!-- HARNESS-OPTIMIZER:START -->

# Harness Rules

## Output
Be concise. Answer first. Preserve commands, paths, errors, numbers, and critical qualifications. Explain safety risks and irreversible actions fully.

## Tools and edits
- Read the smallest sufficient context. Reuse current evidence; avoid redundant searches.
- Use `get_repo_skeleton` when code location is unknown; otherwise read the relevant file or lines.
- Prefer surgical edits with `apply_search_replace` and `expected_sha256`. Never bypass a refused edit using another tool; resolve the refusal or ask.
- Treat dependency matches as references, not confirmed callers.
- Native tools are allowed when equally safe and more efficient.
- Match existing code. No placeholders, silent scope expansion, or weakening tests.

## Verification
- Run relevant checks after changes. Syntax checks alone do not verify behavior.
- Report actual commands, failures, skipped checks, and uncertainty. Never claim unexecuted checks passed.
- Never install dependencies or access external services during verification without approval.
- Verification reports findings only; do not silently repair, commit, or alter requirements.
- After three unsuccessful attempts at the same fix, stop and report the blocker.

## Git safety
- Never auto-commit. `git_checkpoint` requires user approval.
- If rollback seems necessary, inspect with `rollback_show` and let the user decide.
- Never run destructive Git operations without explicit approval.

## User configuration
- Preserve `~/.config/opencode/opencode.json`, including unrelated providers, agents, plugins, and MCP servers.
- Read and merge only project-owned keys. Never reset the configuration or replace unrelated objects.
- Back up the configuration before changing it.
- `--force` does not authorize deleting unrelated settings.
- After edits, verify unrelated providers, agents, and plugins remain intact.

## Planning and context
- Approved OpenSpec changes are the planning authority; do not create competing plans.
- Otherwise `/harness-plan` writes a plan and stops until approval.
- `/harness-verify` checks requirements against implementation and tests.
- `/harness-checkpoint` preserves context when needed.
- If `server.py` changed, restart OpenCode before assuming the MCP server uses the new code.

<!-- HARNESS-OPTIMIZER:END -->
