// CTX Day 1 — proof of concept: inject a labeled reference-data packet into
// the outgoing model request.
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
 * Canonicalizes `relPath` inside `root` and builds the labeled packet.
 * Returns null when the path escapes the root, is sensitive, or is unreadable.
 */
function buildPacket(root: string, relPath: string): string | null {
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

  const truncated = buffer.length > MAX_BYTES
  const body = truncated
    ? buffer.subarray(0, MAX_BYTES).toString("utf8")
    : buffer.toString("utf8")

  return [
    HEADER,
    `# source: ${rel.split(sep).join("/")}`,
    `# bytes: ${truncated ? MAX_BYTES : buffer.length} (truncated: ${truncated ? "yes" : "no"})`,
    "",
    body,
    FOOTER,
  ].join("\n")
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

        // Idempotency: drop any harness-owned packet left over from an
        // earlier attempt in the same session before deciding anew.
        for (let i = messages.length - 1; i >= 0; i--) {
          const metadata = messages[i]?.metadata
          if (metadata && metadata.harness_auto_context === true) messages.splice(i, 1)
        }

        const target = lastUserText(messages)
        if (!target) return

        const token = extractPath(target.text)
        if (!token) return

        let root = ""
        try {
          root = typeof ctx?.location?.directory === "string" ? ctx.location.directory : ""
        } catch {
          root = ""
        }
        if (!root) root = process.cwd()

        const packet = buildPacket(root, token)
        if (!packet) return

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
