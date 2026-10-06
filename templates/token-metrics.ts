import { readFileSync, writeFileSync, existsSync, appendFileSync } from "fs"
import { join } from "path"
import { homedir } from "os"

const CONFIG_DIR = join(homedir(), ".config", "opencode")
const TOTALS_PATH = join(CONFIG_DIR, ".harness-token-totals.json")
const LOG_PATH = join(CONFIG_DIR, ".harness-token-metrics.log")

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

      const direct = event?.result?.output?.result
      const text = typeof direct === "string" ? direct : ""
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
