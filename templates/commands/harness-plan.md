---
description: Produce a written implementation plan, then stop.
---

Read the current task: $ARGUMENTS

If an approved OpenSpec change (`openspec/` or `.openspec/`) covers this task,
do NOT write a competing plan: follow that change and its tasks instead.

Otherwise produce a written implementation plan. Do NOT begin implementation.

The plan must include:
1. Files to change (full paths)
2. Order of changes
3. The verification step for each change
4. Any dependencies or risks

Write the plan to `.opencode/plan.md` using the write tool. Then stop and
wait for my approval before editing any source code.

Once I approve (any affirmative reply), read `.opencode/plan.md` back into
context and treat it as your implementation brief while editing.
