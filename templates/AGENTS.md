# Global Rules (all OpenCode sessions)

## Token Protocol
- ALWAYS call `get_repo_skeleton` before reading any source file
- Use `rip_file_lines` for targeted reads — NEVER read full files
- Use `apply_search_replace` for edits — NEVER rewrite full files
- Run `lint_file` after every code change
- Only call `git_checkpoint` at meaningful milestones

## Token Metric Reporting
When a harness-tools call returns text containing a `[TOKEN METRIC]` line,
include that line verbatim in your reply when it is easy to do so:

    💰 <tool_name>: saved <N> tokens (<baseline> baseline → <actual> actual)

The `<N>` value is an **estimated payload reduction** — the difference between
the full-file representation and the returned excerpt, computed as
`len(text)//4`. It is **not** a measured end-to-end token saving.

Do not summarize, inflate, or fabricate the numbers. The token-metrics plugin
also records every metric to `~/.config/opencode/.harness-token-metrics.log`,
so skipping the line in chat is acceptable — never invent a replacement.

## Context Pressure Protocol
When context-watch warns you, STOP and produce a Context Checkpoint Report:
1. COMPLETED — everything fully finished and verified
2. IN PROGRESS — state and next step
3. NOT STARTED — scope never touched
4. SKIPPED DUE TO CONTEXT — be honest; this is the most important section
5. UNCERTAIN / POSSIBLY MISSED — assumptions and unverified requirements
6. FILES TOUCHED — every file read (with line ranges) and modified
7. RECOMMENDATION — CONTINUE, RESUME in new session, or ABANDON

Do NOT continue after the report unless the user says so.

## Model Fallback
If fallback switches models mid-task, you still have full history.
Do not re-read files or re-run tests. Continue where the previous model left off.


## No Shell Bypass of Refused Edits
If a harness-tools edit is refused (ambiguous search, empty search, missing
target, safety guard), do NOT try to accomplish the same edit via
`execute_and_capture` with `python3 -c`, `sed -i`, `perl -i`, or similar.
The refusal is intentional.

Instead:
- Add more unique context to `apply_search_replace`
- Use OpenCode's native `edit` tool
- Ask the user how to proceed

## Plan Mode
When the user types `/plan <task>`, produce a written implementation plan
and write it to `.opencode/plan.md`, then STOP. Do not begin implementation.

Once the user approves the plan (any affirmative reply), immediately read
`.opencode/plan.md` back into context and treat it as your implementation
brief. Reference it while editing.

## Rollback Protocol
If you believe the repository is in a broken state, call `rollback_show`
first. Present the impact report. Do NOT run destructive git commands
yourself. Let the user decide whether to roll back and how.

## Restart Awareness
If you are unsure whether a recently edited MCP tool file (`server.py`)
is loaded, tell the user to restart OpenCode. You cannot reload the MCP
server mid-session.


