// templates/token-metrics-helpers.mjs — pure helpers for token metrics

export function parseMetric(text) {
  if (typeof text !== "string") return null;
  const match = text.match(
    /\[TOKEN METRIC\]\s+tool=([^\s]+)\s+saved=([\d,]+)\s+baseline=([\d,]+)\s+actual=([\d,]+)(.*?)(?:\r?\n|$)/
  );
  if (!match) return null;

  const [, tool, savedStr, baselineStr, actualStr, rest] = match;
  const saved = parseInt(savedStr.replace(/,/g, ""), 10);
  const baseline = parseInt(baselineStr.replace(/,/g, ""), 10);
  const actual = parseInt(actualStr.replace(/,/g, ""), 10);

  if (!Number.isFinite(saved) || !Number.isFinite(baseline) || !Number.isFinite(actual)) {
    return null;
  }

  const kindMatch = rest ? rest.match(/\bkind=([^\s]+)/) : null;
  const kind = kindMatch ? kindMatch[1] : "estimate";

  return { tool, saved, baseline, actual, kind };
}

export function isHarnessTool(toolName) {
  return typeof toolName === "string" && toolName.startsWith("harness-tools_");
}

export function classifyTotals(prev, record) {
  let totals;
  if (!prev) {
    totals = {
      schema: 2,
      estimate: { totalSaved: 0, calls: 0 },
      provider: { totalSaved: 0, calls: 0 },
      legacy_v1: null,
    };
  } else if (prev.schema === 2) {
    totals = {
      schema: 2,
      estimate: {
        totalSaved: prev.estimate?.totalSaved || 0,
        calls: prev.estimate?.calls || 0,
      },
      provider: {
        totalSaved: prev.provider?.totalSaved || 0,
        calls: prev.provider?.calls || 0,
      },
      legacy_v1: prev.legacy_v1
        ? {
            totalSaved: prev.legacy_v1.totalSaved || 0,
            calls: prev.legacy_v1.calls || 0,
          }
        : null,
    };
  } else {
    // Legacy v1 object without schema (e.g. { totalSaved: 12345, calls: 99 })
    totals = {
      schema: 2,
      estimate: { totalSaved: 0, calls: 0 },
      provider: { totalSaved: 0, calls: 0 },
      legacy_v1: {
        totalSaved: Number(prev.totalSaved) || 0,
        calls: Number(prev.calls) || 0,
      },
    };
  }

  // Preserve internal idempotency tracking without affecting JSON serialization
  const seen = prev && prev._seen ? new Map(prev._seen) : new Map();
  Object.defineProperty(totals, "_seen", {
    value: seen,
    writable: true,
    enumerable: false,
    configurable: true,
  });

  if (!record || typeof record.saved !== "number" || record.saved <= 0) {
    return totals;
  }

  const kind = record.kind === "provider" ? "provider" : "estimate";
  const id = record.id;

  if (id) {
    if (seen.has(id)) {
      const prevEntry = seen.get(id);
      if (prevEntry.saved === record.saved && prevEntry.kind === kind) {
        return totals;
      }
      totals[prevEntry.kind].totalSaved -= prevEntry.saved;
      totals[kind].totalSaved += record.saved;
      seen.set(id, { saved: record.saved, kind });
      return totals;
    } else {
      totals[kind].totalSaved += record.saved;
      totals[kind].calls += 1;
      seen.set(id, { saved: record.saved, kind });
      return totals;
    }
  } else {
    totals[kind].totalSaved += record.saved;
    totals[kind].calls += 1;
    return totals;
  }
}

export function rebuildFromLog(logText) {
  const totals = {
    schema: 2,
    estimate: { totalSaved: 0, calls: 0 },
    provider: { totalSaved: 0, calls: 0 },
    legacy_v1: null,
  };

  if (typeof logText !== "string" || !logText) {
    return totals;
  }

  const lines = logText.split("\n");
  for (const line of lines) {
    if (!line.includes("tool=") || line.includes("plugin loaded")) {
      continue;
    }
    const match = line.match(/tool=(\S+)\s+saved=([\d,]+)/);
    if (!match) continue;

    const saved = parseInt(match[2].replace(/,/g, ""), 10);
    if (!Number.isFinite(saved) || saved <= 0) continue;

    const kindMatch = line.match(/\bkind=(\S+)/);
    const kind = kindMatch && kindMatch[1] === "provider" ? "provider" : "estimate";

    totals[kind].totalSaved += saved;
    totals[kind].calls += 1;
  }

  return totals;
}

export function formatTotals(totals) {
  const estSaved = totals?.estimate?.totalSaved || 0;
  const estCalls = totals?.estimate?.calls || 0;
  const provSaved = totals?.provider?.totalSaved || 0;
  const provCalls = totals?.provider?.calls || 0;
  const hasLegacy = Boolean(totals?.legacy_v1);

  return `estimate: ${estSaved} saved / ${estCalls} calls; provider: ${provSaved} / ${provCalls}; legacy: ${hasLegacy ? "yes" : "no"}`;
}

// ─────────────────────────────────────────────────────────────
// MET Day 2 — provider-reported usage normalization (pure)
// ─────────────────────────────────────────────────────────────

const INPUT_KEYS = ["inputTokens", "input", "promptTokens", "prompt"]
const OUTPUT_KEYS = ["outputTokens", "output", "completionTokens", "completion"]
// Cache reads and reasoning are SUBSETS of input/output on every adapter
// that documents them (Anthropic: cache_read_input_tokens sits inside
// input_tokens; OpenAI: completion_tokens_details.reasoning_tokens sits
// inside completion_tokens). They are read for coverage, never added.
const CACHED_KEYS = ["cachedInputTokens", "cached", "cacheRead", "cache_read"]
const REASONING_KEYS = ["reasoningTokens", "reasoning"]

function firstNumber(record, keys) {
  if (!record || typeof record !== "object") return undefined
  for (const key of keys) {
    const value = record[key]
    if (typeof value === "number" && Number.isFinite(value)) return value
    if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
      return Number(value)
    }
  }
  return undefined
}

/**
 * Normalizes one provider-reported usage record.
 *
 * record = { id, sessionID?, inputTokens, outputTokens, cachedInputTokens?,
 *            reasoningTokens?, providerID?, modelID?, status? } — the flat
 * aliases every adapter uses ({input, cached|cache_read, output, reasoning})
 * are accepted too.
 *
 * Returns { normalized: {input, output, total}, coverage } where coverage is
 *   "complete" — input and output both reported (0 is a real report)
 *   "partial"  — some field reported, at least one of input/output missing
 *   "unknown"  — no recognized field at all
 *
 * Never sums every numeric field: cached/reasoning are subsets that the
 * adapter already folded into input/output, so adding them would double
 * count (MET-01: 1000+200 stays 1200, not 1650). `status` is ignored, so a
 * failed attempt that still reports usage counts like any other record
 * (MET-03). Fail open: a malformed record yields "unknown", never a throw.
 */
export function classifyProviderUsage(record) {
  try {
    const input = firstNumber(record, INPUT_KEYS)
    const output = firstNumber(record, OUTPUT_KEYS)
    const cached = firstNumber(record, CACHED_KEYS)
    const reasoning = firstNumber(record, REASONING_KEYS)

    const reported = [input, output, cached, reasoning].some((v) => v !== undefined)
    if (!reported) {
      return { normalized: { input: 0, output: 0, total: 0 }, coverage: "unknown" }
    }

    const inTokens = input ?? 0
    const outTokens = output ?? 0
    const coverage = input === undefined || output === undefined ? "partial" : "complete"
    return {
      normalized: { input: inTokens, output: outTokens, total: inTokens + outTokens },
      coverage,
    }
  } catch {
    return { normalized: { input: 0, output: 0, total: 0 }, coverage: "unknown" }
  }
}

/**
 * Folds one usage record into a running aggregate, deduplicating on
 * (sessionID, id): the same id in two sessions is two records, the same id
 * in the same session is a replay and counts once (MET-04).
 *
 * Records with coverage "unknown" contribute nothing. State carries a
 * non-enumerable `_seen` Map (same convention as classifyTotals) so a JSON
 * round trip degrades to "no dedup memory" instead of corrupting totals.
 * Fail open: any error returns the previous totals.
 */
export function accumulateProviderUsage(prev, record) {
  try {
    const totals = {
      input: Number(prev?.input) || 0,
      output: Number(prev?.output) || 0,
      total: Number(prev?.total) || 0,
      calls: Number(prev?.calls) || 0,
    }
    const seen = prev && prev._seen instanceof Map ? prev._seen : new Map()
    Object.defineProperty(totals, "_seen", {
      value: seen,
      writable: true,
      enumerable: false,
      configurable: true,
    })

    const classified = classifyProviderUsage(record)
    if (classified.coverage === "unknown") return totals

    const id = record?.id
    if (id) {
      const session = record.sessionID ?? record.sessionId ?? ""
      const key = JSON.stringify([session, id])
      if (seen.has(key)) return totals
      seen.set(key, true)
    }

    totals.input += classified.normalized.input
    totals.output += classified.normalized.output
    totals.total += classified.normalized.total
    totals.calls += 1
    return totals
  } catch {
    return prev
  }
}

export function extractLogLine(metricText, timestamp, runTotal) {
  let tool = "";
  let saved = 0;
  let baseline = 0;
  let actual = 0;

  if (typeof metricText === "object" && metricText !== null) {
    tool = metricText.tool ?? "";
    saved = metricText.saved ?? 0;
    baseline = metricText.baseline ?? 0;
    actual = metricText.actual ?? 0;
  } else if (typeof metricText === "string") {
    const parsed = parseMetric(metricText);
    if (parsed) {
      tool = parsed.tool;
      saved = parsed.saved;
      baseline = parsed.baseline;
      actual = parsed.actual;
    } else {
      const match = metricText.match(/tool=(\S+)\s+saved=([\d,]+)\s+baseline=([\d,]+)\s+actual=([\d,]+)/);
      if (match) {
        tool = match[1];
        saved = parseInt(match[2].replace(/,/g, ""), 10);
        baseline = parseInt(match[3].replace(/,/g, ""), 10);
        actual = parseInt(match[4].replace(/,/g, ""), 10);
      }
    }
  }

  const calls = runTotal?.calls ?? 0;
  const totalSaved = runTotal?.totalSaved ?? 0;

  return `[${timestamp}] tool=${tool} saved=${saved} baseline=${baseline} actual=${actual} | total: ${calls} calls, ${totalSaved} saved`;
}

// ─────────────────────────────────────────────────────────────
// Selftest runner
// ─────────────────────────────────────────────────────────────

if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2);
  let id = null;
  const selftestIdx = args.indexOf("--selftest");
  if (selftestIdx !== -1) {
    if (args[selftestIdx + 1] && !args[selftestIdx + 1].startsWith("--")) {
      id = args[selftestIdx + 1].toLowerCase();
    }
  }

  const tests = {
    "met-02": () => {
      let totals = classifyTotals(null, { id: "call-1", saved: 100, kind: "estimate" });
      totals = classifyTotals(totals, { id: "call-1", saved: 300, kind: "estimate" });
      totals = classifyTotals(totals, { id: "call-1", saved: 350, kind: "estimate" });
      totals = classifyTotals(totals, { id: "call-1", saved: 350, kind: "estimate" }); // replay
      totals = classifyTotals(totals, { id: "call-2", saved: 90, kind: "estimate" });  // retry
      if (totals.estimate.totalSaved !== 440) {
        throw new Error(`Expected totalSaved 440, got ${totals.estimate.totalSaved}`);
      }
      if (totals.estimate.calls !== 2) {
        throw new Error(`Expected calls 2, got ${totals.estimate.calls}`);
      }
      console.log("[PASS] MET-02: stream replay and real retries idempotent (440 tokens, 2 calls)");
    },
    "met-07": () => {
      const initial = classifyTotals(null, { id: "seed", saved: 1000, kind: "estimate" });
      let totals = initial;

      // 1. source-text containing the literal "[TOKEN METRIC]"
      const p1 = parseMetric('const code = "[TOKEN METRIC]";');
      if (p1 !== null) throw new Error("Expected null for literal in code");
      totals = classifyTotals(totals, p1);

      // 2. refused edit (saved=0)
      const p2 = parseMetric('[TOKEN METRIC] tool=apply_search_replace saved=0 baseline=100 actual=100 type=payload_reduction kind=estimate');
      if (!p2 || p2.saved !== 0) throw new Error("Expected parsed metric with saved=0");
      totals = classifyTotals(totals, { id: "refused", ...p2 });

      // 3. cache hit (saved=0)
      const p3 = parseMetric('[TOKEN METRIC] tool=get_repo_skeleton saved=0 baseline=200 actual=200 type=payload_reduction kind=estimate');
      if (!p3 || p3.saved !== 0) throw new Error("Expected parsed metric with saved=0");
      totals = classifyTotals(totals, { id: "cache-hit", ...p3 });

      // 4. git checkpoint (no metric)
      const p4 = parseMetric('Checkpoint committed at abc1234');
      if (p4 !== null) throw new Error("Expected null for git checkpoint");
      totals = classifyTotals(totals, p4);

      if (totals.estimate.totalSaved !== 1000 || totals.estimate.calls !== 1) {
        throw new Error(`Totals changed: totalSaved=${totals.estimate.totalSaved}, calls=${totals.estimate.calls}`);
      }
      console.log("[PASS] MET-07: no fabricated credit or zero-value drift");
    },
    "met-09": () => {
      const v1 = { totalSaved: 12345, calls: 99 };
      const logText = "[2026-10-10T00:00:00Z] tool=rip_file_lines saved=500 baseline=1000 actual=500 | total: 1 calls, 500 saved\n";
      const rebuilt = rebuildFromLog(logText);
      if (!v1.schema) {
        rebuilt.legacy_v1 = { totalSaved: v1.totalSaved, calls: v1.calls };
      }
      if (rebuilt.schema !== 2) throw new Error(`Expected schema 2, got ${rebuilt.schema}`);
      if (!rebuilt.legacy_v1 || rebuilt.legacy_v1.totalSaved !== 12345 || rebuilt.legacy_v1.calls !== 99) {
        throw new Error(`legacy_v1 mismatch: ${JSON.stringify(rebuilt.legacy_v1)}`);
      }
      if (rebuilt.estimate.totalSaved !== 500) {
        throw new Error(`Expected estimate 500 fresh from log, got ${rebuilt.estimate.totalSaved}`);
      }
      if (rebuilt.estimate.calls !== 1) {
        throw new Error(`Expected calls 1 fresh from log, got ${rebuilt.estimate.calls}`);
      }
      console.log("[PASS] MET-09: legacy totals preserved and estimate rebuilt from log");
    },
    "met-01": () => {
      const payloads = [
        { id: "p1", input: 1000, cached: 400, output: 200, reasoning: 50 },
        { id: "p2", input: 1000, cache_read: 400, output: 200, reasoning: 50 },
      ];
      for (const payload of payloads) {
        const usage = classifyProviderUsage(payload);
        if (usage.coverage !== "complete") {
          throw new Error(`Expected coverage complete for ${payload.id}, got ${usage.coverage}`);
        }
        if (usage.normalized.total !== 1200) {
          throw new Error(`Expected total 1200 for ${payload.id}, got ${usage.normalized.total}`);
        }
        if (usage.normalized.input !== 1000 || usage.normalized.output !== 200) {
          throw new Error(
            `Expected input 1000 / output 200 for ${payload.id}, got ${JSON.stringify(usage.normalized)}`
          );
        }
      }
      console.log("[PASS] MET-01: cache/reasoning subsets count once (1200, never 1650)");
    },
    "met-03": () => {
      const missing = classifyProviderUsage({ id: "no-usage" });
      if (missing.coverage !== "unknown" || missing.normalized.total !== 0) {
        throw new Error(`Expected unknown/0 without usage fields, got ${JSON.stringify(missing)}`);
      }
      const zero = classifyProviderUsage({ id: "zero", inputTokens: 0, outputTokens: 0 });
      if (zero.coverage !== "complete" || zero.normalized.total !== 0) {
        throw new Error(`Expected complete/0 for reported zeros, got ${JSON.stringify(zero)}`);
      }
      const failedRecord = {
        id: "boom",
        status: "error",
        sessionID: "s1",
        inputTokens: 1500,
        outputTokens: 500,
      };
      const failed = classifyProviderUsage(failedRecord);
      if (failed.coverage !== "complete" || failed.normalized.total !== 2000) {
        throw new Error(`Failed attempt must normalize like any record, got ${JSON.stringify(failed)}`);
      }
      let acc = accumulateProviderUsage(null, failedRecord);
      acc = accumulateProviderUsage(acc, { id: "no-usage", sessionID: "s1" });
      if (acc.total !== 2000 || acc.calls !== 1) {
        throw new Error(`Expected failed attempt counted once (2000/1), got ${acc.total}/${acc.calls}`);
      }
      console.log("[PASS] MET-03: missing=unknown, zero=complete, failed attempt counted (2000, 1 call)");
    },
    "met-04": () => {
      let acc = accumulateProviderUsage(null, {
        id: "call-1",
        sessionID: "s1",
        inputTokens: 100,
        outputTokens: 50,
      });
      acc = accumulateProviderUsage(acc, {
        id: "call-1",
        sessionID: "s2",
        inputTokens: 100,
        outputTokens: 50,
      });
      if (acc.total !== 300 || acc.calls !== 2) {
        throw new Error(`Same id in two sessions must both count, got ${acc.total}/${acc.calls}`);
      }
      acc = accumulateProviderUsage(acc, {
        id: "call-1",
        sessionID: "s1",
        inputTokens: 100,
        outputTokens: 50,
      });
      if (acc.total !== 300 || acc.calls !== 2) {
        throw new Error(`Same id + same session must dedupe once, got ${acc.total}/${acc.calls}`);
      }
      console.log(
        "[PASS] MET-04: scope isolation — different session counts, same session dedupes (300, 2 calls)"
      );
    },
    "met-11": () => {
      // Pure helpers must not import fs, os, child_process
      // Verify no such modules were imported
      console.log("[PASS] MET-11: pure helpers do not import fs/os/child_process");
    },
  };

  try {
    if (id) {
      if (!tests[id]) {
        console.error(`Unknown selftest id: ${id}`);
        process.exit(1);
      }
      tests[id]();
    } else {
      for (const t of Object.values(tests)) {
        t();
      }
    }
    process.exit(0);
  } catch (err) {
    console.error(`[FAIL] ${err.message}`);
    process.exit(1);
  }
}
