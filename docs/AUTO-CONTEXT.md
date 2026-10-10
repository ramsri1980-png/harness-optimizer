# Auto-Context (CTX)

## What it is

Auto-Context is an OpenCode plugin that injects a labeled reference packet —
the content of one file, or a ranked map of the repository — into the outgoing
model request, without a tool call.

## When it fires

The plugin runs a four-way gate on every user turn and only injects when the
gate says it is useful.

**NONE** — No injection. Greeting ("hi, how are you today?"), title and
compaction requests, a turn with no user text, a question with no file-path
token and no repo-orientation phrasing. Also the result of the kill switch.

**REUSE** — A harness-labeled packet for the requested file is already present
in the conversation and its sha256 still matches the file on disk. The plugin
leaves the existing packet alone instead of injecting a duplicate.

**FOCUSED** — The user named a file and the packet is missing or stale. The
plugin reads that file and injects its content, replacing any stale
harness-owned packet for the same source. Paths that are absolute, start with
`/` or `~`, or contain `..` are rejected.

**MAP** — The user asked how the repository is structured, where something is
implemented, or similar. The plugin spawns a lazy Python worker that produces
a ranked repo map and injects that instead of a single file.

Decisions are workspace-scoped: only files inside the approved workspace root
are considered. Sensitive files are excluded — see below.

## Enable it

```
npx harness-optimizer --with-context
```

Then restart OpenCode. The flag is opt-in; without it the installer behaves
exactly as before and installs nothing from this feature.

## Disable it

```
npx harness-optimizer --uninstall --with-context
```

or set `HARNESS_CTX=off` before launching OpenCode. With the kill switch set,
the plugin loads but returns without injecting anything.

## What gets installed

| File | Location |
| --- | --- |
| `auto-context.ts` | `~/.config/opencode/plugins/auto-context.ts` |
| `context_worker.py` | `~/.config/opencode/plugins/context_worker.py` |
| `harness_core/` | `~/.config/opencode/harness_core/` |

The worker lives beside the plugin so its `sys.path` insert of
`dirname(__file__) + "/.."` resolves to `~/.config/opencode/`, where
`harness_core` lives.

## Privacy and boundaries

- Reads only files inside the approved workspace root.
- Excludes `.env`, `*.pem`, `*.key`, `id_rsa`, `id_ed25519`, `credentials.json`,
  `.git/config`.
- Never spawns a shell, runs tests, or edits files.
- Worker exits on plugin dispose or stdin EOF.
- Kill switch: `HARNESS_CTX=off`.

## Troubleshooting

- Plugin doesn't load: restart OpenCode after install.
- No packet injected: check `~/.config/opencode/plugins/` for
  `auto-context.ts` and `context_worker.py`.
- Worker errors: see `~/.local/share/opencode/log/opencode.log` for
  `[harness.auto-context worker stderr]` lines.
- Verify the install: `npx harness-optimizer --doctor --with-context`.
