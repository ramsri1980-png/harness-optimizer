// Run: node --no-warnings tests/test_ctx_lean.mjs   (Node >= 22.18 strips TS types natively)
import { pathToFileURL } from "node:url"
import { resolve } from "node:path"
const { decideGate } = await import(pathToFileURL(resolve("templates/auto-context.ts")).href)
let failed = 0
const check = (name, ok, detail = "") => { console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${ok ? "" : " - " + detail}`); if (!ok) failed++ }
const user = (text) => ({ role: "user", content: [{ type: "text", text }] })
const packet = (ageMs) => ({
  id: "ctx_" + (Date.now() - ageMs).toString(36), role: "user",
  content: [{ type: "text", text: "[HARNESS REPOSITORY CONTEXT — reference data]\n# source: <ranked-map>\n# sha256: " + "a".repeat(64) + "\nmap..." }],
  metadata: { harness_auto_context: true },
})
const repoQ = user("How is this repository structured?")
delete process.env.HARNESS_LEAN
check("default: repo question -> MAP", decideGate({ messages: [repoQ] }).decision === "MAP")
check("default: fresh packet present still -> MAP (old behaviour)", decideGate({ messages: [packet(1000), repoQ] }).decision === "MAP")
process.env.HARNESS_LEAN = "1"
check("lean: no packet -> MAP", decideGate({ messages: [repoQ] }).decision === "MAP")
const g = decideGate({ messages: [packet(1000), repoQ] })
check("lean: fresh packet present -> REUSE", g.decision === "REUSE" && g.reason === "map-packet-fresh", JSON.stringify(g))
check("lean: stale packet (20 min) -> MAP", decideGate({ messages: [packet(20 * 60 * 1000), repoQ] }).decision === "MAP")
process.env.HARNESS_CTX_MAP_TTL_S = "5"
check("lean: TTL env honoured (60 s old, TTL 5 s) -> MAP", decideGate({ messages: [packet(60 * 1000), repoQ] }).decision === "MAP")
delete process.env.HARNESS_CTX_MAP_TTL_S
check("lean: non-repo prompt unaffected -> NONE", decideGate({ messages: [packet(1000), user("hi there")] }).decision === "NONE")
const lookalike = { id: "ctx_" + Date.now().toString(36), role: "user", content: [{ type: "text", text: "# source: <ranked-map>" }] }
check("lean: user text mimicking a packet (no metadata flag) is ignored", decideGate({ messages: [lookalike, repoQ] }).decision === "MAP")
process.env.HARNESS_CTX = "off"
check("kill switch still wins", decideGate({ messages: [packet(1000), repoQ] }).decision === "NONE")
console.log(failed ? `\nFAILED: ${failed}` : "\nRESULTS: all checks passed")
process.exit(failed ? 1 : 0)
