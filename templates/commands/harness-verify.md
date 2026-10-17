---
description: Verify completed work against its requirements. Read-only report.
---

Verify the completed scope of: $ARGUMENTS

Source of truth, in order: an approved OpenSpec change (use the installed
OpenSpec verify workflow, `/opsx-verify <change-name>` or the
`openspec-verify-change` skill, if available), else the requirements doc or
spec the user named. If the workflow is unavailable, say so and review the
same artifacts by hand. Report ambiguity; never invent expected behavior.

For each requirement or acceptance scenario map it to:
- the implementation location (file:line),
- the test assertions or acceptance checks that cover it,
- the exact commands you ran, with exit codes and results.
Distinguish checks you executed from tests you only read.

Statuses:
- VERIFIED: current checks support the scenario.
- FAIL: implementation contradicts the requirement, or required behavior is absent.
- UNVERIFIED: evidence is missing, insufficient, stale, skipped or unavailable.
- BLOCKED BY AMBIGUITY: expected behavior cannot be established.

Rules:
- Syntax compilation alone is not behavioral verification.
- Do not install or upgrade dependencies, create virtualenvs, or call external
  services without explicit approval. Use the repo's existing environment.
- Report findings in chat and STOP. Do not edit implementation, tests,
  requirements or task checkboxes. Do not commit, archive or auto-repair.
- Do not claim completion while required behavior is FAIL, BLOCKED or UNVERIFIED.
