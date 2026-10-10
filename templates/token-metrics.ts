import { readFileSync, writeFileSync, existsSync, appendFileSync } from "fs"
import { join } from "path"
import { homedir } from "os"

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
      const raw = JSON.parse(readFileSync(TOTALS_PATH, "utf8"))
      return { totalSaved: Number(raw.totalSaved) || 0, calls: Number(raw.calls) || 0 }
    }
  } catch {}
  return { totalSaved: 0, calls: 0 }
}

function saveTotals(state: { totalSaved: number; calls: number }) {
  try { writeFileSync(TOTALS_PATH, JSON.stringify(state, null, 2) + "\n") } catch {}
}

function log(line: string) {
  try { appendFileSync(LOG_PATH, `[${new Date().toISOString()}] ${line}\n`) } catch {}
}

const seen = new Set<string>()

export default {
  id: "harness.token-metrics",
  async setup(ctx: any) {
    log("plugin loaded")

    await ctx.tool.hook("execute.after", async (event: any) => {
      const tool = String(event?.tool ?? "")
      if (!tool.startsWith("harness-tools_")) return
      if (event?.status !== "completed") return

      const callId = String(event?.id ?? "")
      if (callId && seen.has(callId)) return
      if (callId) seen.add(callId)

      const text = extractToolText(event)
      if (!text.includes("[TOKEN METRIC]")) return

      const match = text.match(
        /\[TOKEN METRIC\] tool=(\S+) saved=([\d,]+) baseline=([\d,]+) actual=([\d,]+)/
      )
      if (!match) return

      const [, toolName, savedStr, baselineStr, actualStr] = match
      const saved = parseInt(savedStr.replace(/,/g, ""), 10)
      const baseline = parseInt(baselineStr.replace(/,/g, ""), 10)
      const actual = parseInt(actualStr.replace(/,/g, ""), 10)
      if (!Number.isFinite(saved)) return

      const totals = loadTotals()
      totals.totalSaved += saved
      totals.calls += 1
      saveTotals(totals)

      log(`tool=${toolName} saved=${saved} baseline=${baseline} actual=${actual} | total: ${totals.calls} calls, ${totals.totalSaved} saved`)

    })
  },
}
