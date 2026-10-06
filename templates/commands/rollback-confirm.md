---
description: Execute a git rollback after reviewing rollback_show.
---

The user has reviewed the rollback impact report and confirmed. Run:

    git reset --hard HEAD && git clean -fd

Confirm what was restored and report the new HEAD commit hash.
