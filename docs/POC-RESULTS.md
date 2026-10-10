# POC — Real Provider-Reported Token Usage (2026-10-10)

## What this measures

Real, provider-reported token usage across three controlled runs of the
same task on the same model. **Not** estimates from the payload metric —
these are the `input`/`output` counts stored by OpenCode in
`~/.local/share/opencode/opencode.db` at JSON path `$.tokens.input` /
`$.tokens.output`.

## Setup

- **Task**: "Explain the structure of templates/harness_core/. List the
  6 modules and describe what each does in one sentence. Do not modify
  any files."
- **Model**: `mimo-v2.6-flash-free` (free tier)
- **Config**: single-run, fresh OpenCode session each time
- **Measurement**: `scripts/poc-measure.sh`
  (`snapshot` before + after; `window` to sum in range)
- **Environment**: `HARNESS_TOOLS` and `HARNESS_CTX` set per-run

## Runs

| # | Env | Input | Output | Turns |
|---|---|---|---|---|
| 1 | `HARNESS_TOOLS=none`, `HARNESS_CTX=off` (baseline) | 11,982 | 912 | 7 |
| 2 | `HARNESS_TOOLS=none`, `HARNESS_CTX` unset (Experiment A) | 10,887 | 1,211 | 7 |
| 3 | `HARNESS_TOOLS` unset, `HARNESS_CTX` unset (full harness) | 16,985 | 874 | 4 |

## Interpretation

- **Experiment A ≈ Baseline** (10,887 vs 11,982, within model variance).
  CTX added ~0 net tokens on this task. The task prompt does not match
  `looksLikeRepoQuestion` (no "repository"/"project"/"codebase" word),
  so the MAP branch did not fire.
- **Treatment − Experiment A = +6,098 tokens.** This is the cost of
  registering the 9 MCP tool schemas with the model on every request.
  ~1,200–1,500 tokens per turn across 4–5 turns.
- **The 41% input increase is entirely from tool schemas.** CTX is
  neutral here; the harness tools' own payload savings did not offset
  the schema cost on a short navigational task.
- **Fewer turns (4 vs 7)** — the harness enabled fewer exploration
  round-trips even though raw input grew. That is the intended
  architectural behavior.

## What this implies

The 9 MCP tool schemas are a **fixed cost per request** that only
amortizes over longer tasks. On short tasks (≤ 5 turns) the cost
dominates. On long tasks (10+ turns, multiple file reads) the tool
payload savings should outpace the schema cost.

**Actions taken (planned)**:

1. Compress the schemas — drop `default` fields where safe.
2. Document `HARNESS_TOOLS=<subset>` so users ship only the tools they
   use. This is the largest lever for short-task parity.

## Reproduction

    # Install
    npx harness-optimizer@latest

    # Snapshot T0
    ./scripts/poc-measure.sh snapshot

    # Restart with the env you want to test, run the task, exit
    # Then snapshot T1 and compute:
    ./scripts/poc-measure.sh window <T0> <T1>

## Caveats

- **Single run each.** Free-model variance is ±15-20%. Directional only.
- **One model, one task.** Not generalizable without more samples.
- **Cold-start effects.** First-run CTX rebuild and worker spawn add
  small overhead that a warm session would not pay.
- **Provider-reported only.** No counterfactual comparison is possible
  without a matched baseline run, which we now have.
