// CTX Day 2 — auto-context plugin: gate + inject a labeled reference-data
// packet into the outgoing model request.
//
// Decision gate (NONE / REUSE / FOCUSED / MAP) decides WHETHER to inject.
// Only FOCUSED currently injects. MAP is detected but the repomap engine
// is Day 3. REUSE leaves an existing fresh packet alone. NONE returns.
//
// Registered hook: ctx.session.hook("context", cb)
//   (SessionContext = { sessionID, agent, model, system[], messages[], options, tools })
//   Payload shape and in-place mutation semantics confirmed against the
//   installed SDK in docs/ctx-spike-findings.md §3.
//
// Contract (docs/ctx-decisions.md Q3 + Q4):
//   - Reads are restricted to the approved workspace root, canonicalized.
//   - Sensitive files (.env, keys, credentials, .git/config) are excluded.
//   - One bounded, labeled packet per outgoing request — reference data,
//     not a fake user/assistant exchange.
//   - Fails open: any error leaves the request unmodified.

import { createHash } from "crypto"
import { readFileSync, realpathSync } from "fs"
import { isAbsolute, relative, resolve, sep } from "path"

const MAX_BYTES = 8192
const HEADER = "[HARNESS REPOSITORY CONTEXT — reference data]"
const FOOTER = "[/HARNESS REPOSITORY CONTEXT]"

const PATH_RE =
  /(?<![A-Za-z0-9_])([A-Za-z0-9_./-]+\.(py|ts|tsx|js|jsx|json|md|toml|yaml|yml|txt))/g

/** Day 1 = at most one file per request. Returns the first path-looking token. */
function extractPath(text: string): string | null {
  PATH_RE.lastIndex = 0
  const match = PATH_RE.exec(text)
  if (!match) return null
  const token = match[1]
  if (!token) return null
  if (token.startsWith("/") || token.startsWith("~")) return null
  if (token.includes("..")) return null
  if (isAbsolute(token)) return null
  return token
}

/** Excludes credentials, keys and ignored sensitive files (Q3). */
function isSensitive(relPath: string): boolean {
  const parts = relPath.split("/").filter(Boolean)
  const base = parts.length ? parts[parts.length - 1] : relPath
  if (base === ".env") return true
  if (base.includes(".env.")) return true
  if (base.endsWith(".pem")) return true
  if (base.endsWith(".key")) return true
  if (base === "id_rsa" || base === "id_ed25519") return true
  if (base === "credentials" || base === "credentials.json") return true
  if (parts.length >= 2 && parts[parts.length - 2] === ".git" && base === "config") return true
  return false
}

/** Text of every text part of a message, regardless of content/parts keying. */
function textOf(message: any): string {
  const container = Array.isArray(message?.content)
    ? message.content
    : Array.isArray(message?.parts)
      ? message.parts
      : null
  if (!container) return ""
  let out = ""
  for (const part of container) {
    if (part && part.type === "text" && typeof part.text === "string") out += part.text + "\n"
  }
  return out
}

/** Index + text of the last human-authored user text message. */
function lastUserText(messages: any[]): { index: number; text: string } | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i]
    if (!message || message.role !== "user") continue
    if (message.metadata && message.metadata.harness_auto_context === true) continue
    const text = textOf(message)
    if (text.trim()) return { index: i, text }
  }
  return null
}

/**
 * Canonicalizes `relPath` inside `root` and reads the raw bytes.
 * Returns null when the path escapes the root, is sensitive, or is unreadable.
 * This is the single code path used by both buildPacket and decideGate (Q3).
 */
function readFileInRoot(root: string, relPath: string): { rel: string; buffer: any } | null {
  let realRoot: string
  try {
    realRoot = realpathSync(root)
  } catch {
    return null
  }

  let resolved: string
  try {
    resolved = realpathSync(resolve(realRoot, relPath))
  } catch {
    return null
  }

  if (resolved !== realRoot && !resolved.startsWith(realRoot + sep)) return null

  const rel = relative(realRoot, resolved)
  if (!rel || rel.startsWith("..") || isAbsolute(rel)) return null
  if (isSensitive(rel)) return null

  let buffer: any
  try {
    buffer = readFileSync(resolved)
  } catch {
    return null
  }
  if (!buffer || typeof buffer.length !== "number") return null

  return { rel, buffer }
}

/** Full lowercase hex sha256 of the raw buffer, or null on failure (fail open). */
function sha256Of(buffer: any): string | null {
  try {
    return createHash("sha256").update(buffer).digest("hex")
  } catch {
    return null
  }
}

/**
 * Builds the labeled packet. The digest covers the raw file bytes BEFORE
 * truncation so REUSE can compare whole-file fingerprints.
 * Returns null when the read, the hash, or the workspace boundary fails.
 */
function buildPacket(root: string, relPath: string): string | null {
  const read = readFileInRoot(root, relPath)
  if (!read) return null

  const digest = sha256Of(read.buffer)
  if (!digest) return null

  const truncated = read.buffer.length > MAX_BYTES
  const body = truncated
    ? read.buffer.subarray(0, MAX_BYTES).toString("utf8")
    : read.buffer.toString("utf8")

  return [
    HEADER,
    `# source: ${read.rel.split(sep).join("/")}`,
    `# sha256: ${digest}`,
    `# bytes: ${truncated ? MAX_BYTES : read.buffer.length} (truncated: ${truncated ? "yes" : "no"})`,
    "",
    body,
    FOOTER,
  ].join("\n")
}

export type GateDecision = "NONE" | "REUSE" | "FOCUSED" | "MAP"

/**
 * Deliberately short, explicit list of repo-orientation phrasings.
 * This is a stub for Day 3's real MAP detection — false negatives are fine
 * (the user can always name a file explicitly).
 */
function looksLikeRepoQuestion(text: string): boolean {
  const t = text.toLowerCase()
  const mentionsRepo =
    t.includes("repo") || t.includes("project") || t.includes("codebase")
  if (t.includes("where is") && (t.includes("implemented") || t.includes("defined") || t.includes("used"))) return true
  if (t.includes("how does the") && mentionsRepo) return true
  if (t.includes("architecture of")) return true
  if (t.includes("structure of the") && mentionsRepo) return true
  if (t.includes("overview of the") && mentionsRepo) return true
  return false
}

/** Source + sha256 of every labeled packet already present in the messages. */
function existingPacketDigests(messages: any[]): Array<{ source: string; sha256: string }> {
  const out: Array<{ source: string; sha256: string }> = []
  for (const message of messages) {
    const text = textOf(message)
    if (!text.includes(HEADER)) continue
    const sourceMatch = text.match(/^# source: (.+)$/m)
    const shaMatch = text.match(/^# sha256: ([0-9a-f]{64})$/m)
    if (sourceMatch && shaMatch) out.push({ source: sourceMatch[1].trim(), sha256: shaMatch[1] })
  }
  return out
}

/**
 * Day 2 gate: decides WHETHER to inject (see ctx-day2 brief, Phase 2).
 * Rules are checked in order; every error path fails open as NONE/FOCUSED.
 */
export function decideGate(event: any, root?: string): {
  decision: GateDecision
  token: string | null
  reason: string
} {
  if (process.env.HARNESS_CTX === "off") return { decision: "NONE", token: null, reason: "kill-switch" }

  const messages = event?.messages
  if (!Array.isArray(messages)) return { decision: "NONE", token: null, reason: "no-messages" }

  if (event.agent === "title") return { decision: "NONE", token: null, reason: "title-request" }
  if (event.agent === "compaction") return { decision: "NONE", token: null, reason: "compaction-request" }

  const target = lastUserText(messages)
  if (!target) return { decision: "NONE", token: null, reason: "no-user-text" }

  const token = extractPath(target.text)
  if (!token) {
    if (looksLikeRepoQuestion(target.text)) return { decision: "MAP", token: null, reason: "map-signal" }
    return { decision: "NONE", token: null, reason: "no-path-token" }
  }

  const workspaceRoot = root || process.cwd()
  const read = readFileInRoot(workspaceRoot, token)
  if (!read) return { decision: "FOCUSED", token, reason: "packet-missing-or-stale" }
  const digest = sha256Of(read.buffer)
  if (!digest) return { decision: "FOCUSED", token, reason: "packet-missing-or-stale" }

  const rel = read.rel.split(sep).join("/")
  for (const packet of existingPacketDigests(messages)) {
    if (packet.source === rel && packet.sha256 === digest) {
      return { decision: "REUSE", token, reason: "fresh-packet-present" }
    }
  }
  return { decision: "FOCUSED", token, reason: "packet-missing-or-stale" }
}

export default {
  id: "harness.auto-context",
  async setup(ctx: any) {
    if (process.env.HARNESS_CTX === "off") return

    const registration = await ctx.session.hook("context", (event: any) => {
      try {
        if (process.env.HARNESS_CTX === "off") return
        const messages = event?.messages
        if (!Array.isArray(messages)) return

        let root = ""
        try {
          root = typeof ctx?.location?.directory === "string" ? ctx.location.directory : ""
        } catch {
          root = ""
        }
        if (!root) root = process.cwd()

        const gate = decideGate(event, root)

        // REUSE: a fresh, matching packet is already in messages — leave it as-is.
        if (gate.decision === "REUSE") return
        // MAP: detected, but the repomap engine is Day 3 — log only.
        if (gate.decision === "MAP") {
          try {
            console.error("[harness.auto-context] MAP signal — engine not wired (Day 3). token=null")
          } catch {}
          return
        }
        if (gate.decision === "NONE") return

        // FOCUSED: replace any stale harness-owned packet, then inject anew.
        for (let i = messages.length - 1; i >= 0; i--) {
          const metadata = messages[i]?.metadata
          if (metadata && metadata.harness_auto_context === true) messages.splice(i, 1)
        }

        const token = gate.token
        if (!token) return

        const packet = buildPacket(root, token)
        if (!packet) return

        const target = lastUserText(messages)
        if (!target) return

        messages.splice(target.index, 0, {
          id: "ctx_" + Date.now().toString(36),
          role: "user",
          content: [{ type: "text", text: packet }],
          metadata: { harness_auto_context: true },
        })
      } catch (err) {
        // Fail open: never block or corrupt the request.
        try {
          console.error("[harness.auto-context]", err)
        } catch {}
      }
    })

    return () => {
      try {
        registration?.dispose?.()
      } catch {}
    }
  },
}
