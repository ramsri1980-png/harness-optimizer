# Harness-Optimizer — Build & Integration Runbook (FINAL)

**Status:** ✅ COMPLETE — v0.1.1 published to npm

**Machine:** sriram-Latitude-6430U (Ubuntu, user: sriram)
**OpenCode:** v2.0.23 · **Node:** v20.20.2 · **npm:** 10.8.2 · **Python:** 3.12.3 · **FastMCP:** 4.0.11
**npm package:** https://www.npmjs.com/package/harness-optimizer
**Publisher:** ramsri1980

---

## One-Command Install (any Linux/macOS/WSL)

    npx harness-optimizer

Health check:

    npx harness-optimizer --doctor

Uninstall:

    npx harness-optimizer --uninstall

Force-reset to defaults:

    npx harness-optimizer --force

---

## What Gets Installed

**MCP server** at `~/developer/harness-optimizer/`:
- 9 tools via FastMCP over stdio
- Runtime venv with FastMCP 4.x
- `HARNESS_TOOLS` env var filters which tools register

**OpenCode config** at `~/.config/opencode/`:
- `opencode.json` — providers (V2 plural), plugins, mcp block
- `AGENTS.md` — global rules + metric echo rule
- `plugins/harness.ts` — token metrics plugin
- `commands/` — plan.md, config.md, rollback-confirm.md, help-harness.md
- `opencode-fallback.jsonc`, `opencode-context-watch.json`

---

## Verified Working (End-to-End)

- `opencode --version` → v2.0.23
- `npx harness-optimizer --doctor` → 15/15 green
- OpenCode TUI: 9 tools present, `/help-harness` works
- `rip_file_lines` via `execute` wrapper returns correct content
- Metric line `💰 rip_file_lines: saved 3,254 tokens (3,374 baseline → 120 actual)` visible in TUI
- Totals file: `{"totalSaved": 3254, "calls": 1}` — no double-counting

---

## Critical OpenCode V2 Rules Learned

1. **`plugins` config must point at a DIRECTORY**, not a file. File paths are silently dropped.
2. **Plugin file must use ESM**: `export default { id, async setup(ctx) {...} }`. CommonJS fails with nested-default error.
3. **`.ts` extension is transpiled as ESM** regardless of package.json.
4. **No `@opencode/plugin` import needed.** `Plugin.define` is an identity function; plain object works.
5. **`ctx.app.log()` silently no-ops on v2.0.23.** Surface metrics via AGENTS.md instructions instead.
6. **Hook name is `"execute.after"`**, not `"tool.execute.after"`.
7. **Code Mode wraps MCP calls** — filter by `harness-tools_` prefix to avoid double-counting.
8. **`providers` (plural) with `package` + `settings`** is V2. `provider` (singular) with `npm` + `options` is V1 — incompatible.

---

## Known Limitations / Deferred

- `opencode-runtime-fallback`, `opencode-context-watch`, `opencode-studio-server` npm packages are V1-only and don't load on v2.0.23. Config entries are absent so nothing breaks; find V2 forks later.
- Studio extension `status.py` written but not integrated with OpenCode Studio runtime.
- Per-tool model routing not supported by OpenCode (routes per agent, not per tool).
- npm bypass-2FA tokens lose direct-publish capability January 2027; migrate to Trusted Publishing for long-term automation.

---

## Updating the Package

After editing source:

    cd ~/developer/harness-optimizer-npm
    npm version patch    # or minor/major
    npm publish --access public

The publish command prompts for OTP (or works silently if using a bypass-2FA granular access token).

---

## Provider Configuration

Current `~/.config/opencode/opencode.json`:

- `tokenharbor` — default model `tokenharbor/deepseek-v4.1-flash`
- `openrouter`
- `orca-router`
- `nvda-gate`

All V2 format. Agents block preserved from prior config.
