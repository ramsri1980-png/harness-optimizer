import { readFileSync, writeFileSync, renameSync, existsSync, appendFileSync } from "fs"
import { join } from "path"
import { homedir } from "os"
import {
  parseMetric,
  isHarnessTool,
  classifyTotals,
  rebuildFromLog,
  formatTotals,
  extractLogLine,
} from "./token-metrics-helpers.mjs"

const CONFIG_DIR = join(homedir(), ".config", "opencode")
const TOTALS_PATH = join(CONFIG_DIR, ".harness-token-totals.json")
const LOG_PATH = join(CONFIG_DIR, ".harness-token-metrics.log")

function extractToolText(event: any): string {
  const parts: string[] = []
  const push = (v: any) => { if (typeof v === "string" && v) parts.push(v) }

  // Path 1 — event.result.output as a raw string
  const output = event?.result?.output
  if (typeof output === "string") push(output)

  // Path 2 — output as an object with any of several string fields
  if (output && typeof output === "object") {
    push(output.result)
    push(output.text)
    push(output.content)
    push(output.value)
    push(output.message)
  }

  // Path 3 — event.result.content as an array of {type,text} parts
  const content = event?.result?.content
  if (Array.isArray(content)) {
    for (const p of content) {
      if (p && typeof p.text === "string") push(p.text)
      else if (typeof p === "string") push(p)
    }
  }

  // Path 4 — event.result as a raw string (unlikely, harmless)
  if (typeof event?.result === "string") push(event.result)

  return parts.join("\n")
}

function loadTotals() {
  try {
    if (existsSync(TOTALS_PATH)) {
      return JSON.parse(readFileSync(TOTALS_PATH, "utf8"))
    }
  } catch {}
  return null
}

function saveTotals(state: any) {
  try {
    const tmp = `${TOTALS_PATH}.tmp.${process.pid}.${Date.now()}`
    writeFileSync(tmp, JSON.stringify(state, null, 2) + "\n")
    renameSync(tmp, TOTALS_PATH)
  } catch {}
}

function log(line: string) {
  try { appendFileSync(LOG_PATH, `[${new Date().toISOString()}] ${line}\n`) } catch {}
}

const seen = new Set<string>()

export default {
  id: "harness.token-metrics",
  async setup(ctx: any) {
    let existing = loadTotals()
    let logText = ""
    try {
      if (existsSync(LOG_PATH)) {
        logText = readFileSync(LOG_PATH, "utf8")
      }
    } catch {}

    const totals = rebuildFromLog(logText)
    if (existing && !existing.schema) {
      totals.legacy_v1 = {
        totalSaved: Number(existing.totalSaved) || 0,
        calls: Number(existing.calls) || 0,
      }
    } else if (existing && existing.legacy_v1) {
      totals.legacy_v1 = existing.legacy_v1
    }

    saveTotals(totals)
    log(`plugin loaded; totals rebuilt from log: ${formatTotals(totals)}`)

    await ctx.tool.hook("execute.after", async (event: any) => {
      const tool = String(event?.tool ?? "")
      if (!isHarnessTool(tool)) return
      if (event?.status !== "completed") return

      const callId = String(event?.id ?? "")
      if (callId && seen.has(callId)) return
      if (callId) seen.add(callId)

      const text = extractToolText(event)
      if (!text.includes("[TOKEN METRIC]")) return

      const metric = parseMetric(text)
      if (!metric || metric.saved <= 0) return

      const prev = loadTotals()
      const updatedTotals = classifyTotals(prev, {
        id: callId,
        saved: metric.saved,
        kind: metric.kind,
      })
      saveTotals(updatedTotals)

      const ts = new Date().toISOString()
      const runTotal = {
        calls: updatedTotals.estimate?.calls || 0,
        totalSaved: updatedTotals.estimate?.totalSaved || 0,
      }
      const logLine = extractLogLine(text, ts, runTotal)
      try {
        appendFileSync(LOG_PATH, `${logLine}\n`)
      } catch {}
    })
  },
}
