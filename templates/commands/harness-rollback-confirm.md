---
description: Show a fresh rollback preview. Does not execute rollback.
---

The user has invoked /harness-rollback-confirm. This command NO LONGER executes a
destructive rollback. It produces a fresh preview so the user can decide
what to do next.

## What to do

1. Call the `rollback_show` tool for the current working repository.
2. Display the returned report verbatim.
3. End your reply with this message:

   "No files were changed. Automatic rollback is disabled. Review the
   changes above and perform any intended rollback yourself in your
   terminal. If files change after this preview, obtain a new preview
   before proceeding."

## What NOT to do

- Do NOT run `git reset --hard`, `git clean`, `git checkout --`, or any
  other destructive Git command.
- Do NOT invoke another tool to accomplish the same effect.
- Do NOT use an earlier preview or earlier approval as authorization to
  execute anything. Always produce a fresh preview at the moment the user
  asks.
- Do NOT modify any files in the repository.

Invoking /harness-rollback-confirm is a request for a current preview — not
authorization to discard work.
