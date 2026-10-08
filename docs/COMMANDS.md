# Commands

- `/plan <task>` — produce an implementation plan, stop, wait for approval
- `/config` — open the web UI at localhost:1080 for Harness-Optimizer settings
- `/rollback-confirm` — show a fresh rollback preview. Does not execute rollback.
- `/help-harness` — show this documentation index
**Requirement verification**: for OpenSpec-driven work, use the installed
OpenSpec verification workflow (`/opsx-verify <change-name>` in OpenCode,
or the `openspec-verify-change` skill). For plain requirements docs, ask
the model to verify each requirement with code evidence, test evidence,
and status — see the Requirement Verification rule in your global AGENTS.md.
