<!-- HARNESS-OPTIMIZER:START -->

# Global Rules (all OpenCode sessions)

## Token Protocol
Use the smallest sufficient context — not the smallest possible excerpt.

- **Mapping**: use `get_repo_skeleton` when relevant code locations or
  relationships are unclear. Do not require a map before every read. If
  the relevant file is already known, read it directly.
- **Reading**: read the smallest sufficient context — normally a complete
  function, logical block, or file section plus necessary surrounding
  context. Full reads of small files are allowed. Expand context when
  dependencies or behavior are unclear.
- **Editing**: prefer `apply_search_replace` for surgical block edits.
  Use OpenCode's native `edit` tool when it provides equivalent safety
  with less work. Never rewrite a full file to make a small change.
- **Tool selection**: use harness tools or native OpenCode tools according
  to which is more effective and safer for the task. Do not force a
  harness tool merely to produce a savings metric.
- **Verification**: run language-appropriate checks and relevant tests
  after each coherent change. Preserve required regression tests and
  final acceptance checks. Reuse earlier evidence only while it remains
  applicable and current.
- **Test output**: prefer concise-output flags supported by the repo's
  existing test runner (e.g., `-q --tb=short` for pytest). Preserve the
  exit code, failure summary, warnings, and required acceptance checks.
  When the concise output is insufficient, rerun the specific failing
  test with full diagnostics. Do not add a summarization tool or hide
  errors to shorten output.
- **Milestones**: call `git_checkpoint` at meaningful milestones, not
  after every edit.
- **Lint scope**: `lint_file` performs Python syntax compilation
  (`py_compile`) only. It is not a linter, type checker, or test runner.
  For non-Python files it returns SKIPPED — use the repository's own
  configured checks instead. Never cite a successful `lint_file` as
  evidence that code works.

## Token Metric Reporting
Routine `[TOKEN METRIC]` lines are recorded by the token-metrics plugin in
`~/.config/opencode/.harness-token-metrics.log`. Do not echo them in normal
assistant replies — keep them out of the conversation flow. Display a metric
only when the user explicitly asks for it, and describe it as an estimated
payload reduction (not measured end-to-end savings). Never fabricate numbers.


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
Reuse prior evidence when it remains available and applicable. Reread files
or rerun checks when relevant state changed, evidence is missing, or
correctness is uncertain. Do not blindly redo everything, and do not blindly
trust stale evidence either.


## Requirement Verification
For work against explicit requirements — a requirements doc, an OpenSpec
change, or a user-provided spec — verify the completed scope before
claiming completion. Read the identified requirements and acceptance
scenarios. Report ambiguity rather than inventing expected behavior.

**OpenSpec handling.** When an approved OpenSpec change is active, use
the installed OpenSpec verification workflow — `/opsx-verify <change-name>`
in OpenCode, or the `openspec-verify-change` skill. Keep that change's
approved requirements as the source of truth. If the workflow is
unavailable, report that and perform an evidence-based review of the
same artifacts. Do not invent a CLI command or a second specification.

**Evidence for both paths.** For each requirement or acceptance scenario,
map it to its implementation location and the relevant test assertions
or acceptance checks. Report exact executed commands, exit codes, and
results. Distinguish executed checks from inspected tests. Use existing
permissions and isolated test data.

**Statuses.**
- **VERIFIED** — current checks support the stated scenario.
- **FAIL** — the implementation is present and contradicts the
  requirement, OR required behavior is entirely absent (no code path
  could satisfy the requirement).
- **UNVERIFIED** — evidence is missing, insufficient, stale, skipped,
  or unavailable, but an implementation might exist that satisfies the
  requirement.
- **BLOCKED BY AMBIGUITY** — expected behavior cannot be established.

Do not present syntax compilation alone as behavioral verification.

**Dependency discipline.** Do not install new dependencies, upgrade
existing ones, or access external services during verification without
explicit user approval. If a required test runner or tool is missing,
report it and ask how to proceed. Use the repository's existing
environment; do not create a new virtualenv, install globally, or run
`pip install` / `npm install` without approval.


**Review boundaries.** Report findings in chat and stop. During
verification, do not edit implementation, tests, requirements, or task
checkboxes; do not commit, archive, or automatically repair findings.
Do not claim completion while required behavior remains failed,
blocked, or unverified.

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
**If an approved OpenSpec change is active for this repository** (an
`openspec/` or `.openspec/` directory contains an approved change),
treat that OpenSpec change as the single planning authority. Do not
create a competing `.opencode/plan.md`. Follow the OpenSpec plan and
its tasks. `/harness-plan` is for work that has no OpenSpec plan.

When the user types `/harness-plan <task>`, produce a written implementation plan
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

<!-- HARNESS-OPTIMIZER:END -->


