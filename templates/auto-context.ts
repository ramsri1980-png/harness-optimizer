// CTX Day 5 — auto-context plugin: gate + inject a labeled reference-data
// packet into the outgoing model request.  The MAP branch spawns a lazy
// Python worker to produce a ranked repo map (no tool call).
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
import { existsSync, readFileSync, realpathSync, statSync } from "fs"
import { spawn, type ChildProcess } from "child_process"
import { fileURLToPath } from "url"
import { dirname, isAbsolute, join, relative, resolve, sep } from "path"

const MAX_BYTES = 8192
const HEADER = "[HARNESS REPOSITORY CONTEXT — reference data]"
const FOOTER = "[/HARNESS REPOSITORY CONTEXT]"

const PATH_RE =
  /(?<![A-Za-z0-9_])([A-Za-z0-9_./-]+\.(py|ts|tsx|js|jsx|json|md|toml|yaml|yml|txt))/g

// --- CTX Day 5: lazy MAP worker client ---------------------------------------

// Resolve the worker script next to this plugin.  The plugin is loaded
// by the OpenCode binary from a copied directory (see the test harness
// comment about "configured plugin path must be a directory"), so we key
// off our own source location rather than process.cwd().  fileURLToPath
// is only needed when this file is imported as a module URL; in the
// bundled runtime __filename is already an absolute path, and the
// dirname() call below is a no-op in that case.
const __filename = (() => {
  try {
    return typeof __filename !== "undefined"
      ? __filename
      : fileURLToPath(import.meta.url)
  } catch {
    return ""
  }
})()

const WORKER_DIR = (() => {
  try {
    const d = dirname(__filename || "")
    if (d) return d
  } catch {}
  return process.cwd()
})()

const WORKER_PATH = (() => {
  const candidates: string[] = []

  // 1. Next to this plugin (the plugin dir is known via __filename/import.meta.url).
  candidates.push(join(WORKER_DIR, "context_worker.py"))

  // 2. In the OPENCODE_CONFIG_DIR plugins dir (real install location).
  const configDir = process.env.OPENCODE_CONFIG_DIR
  if (typeof configDir === "string" && configDir) {
    candidates.push(join(configDir, "plugins", "context_worker.py"))
    candidates.push(join(configDir, "context_worker.py"))
  }

  // 3. Legacy ~/.config/opencode/plugins fallback.
  const home = process.env.HOME
  if (typeof home === "string" && home) {
    candidates.push(join(home, ".config", "opencode", "plugins", "context_worker.py"))
  }

  // 4. Repo dev layout templates/ (for local testing when cwd=repo root).
  candidates.push(join(process.cwd(), "templates", "context_worker.py"))

  for (const c of candidates) {
    try {
      if (c && existsSync(c)) return c
    } catch {}
  }
  // As a last resort, return the first candidate even if missing;
  // the spawn will fail open and we'll log the error.
  return candidates[0] || join(process.cwd(), "templates", "context_worker.py")
})()

let worker: ChildProcess | null = null
let workerReady = false

// Ring buffer for the worker's stderr (last 2 KiB) — useful when a MAP
// request fails and we need to know why without corrupting stdout.
const STDERR_RING_BYTES = 2048
let stderrBuffer = ""

function captureStderr(chunk: Buffer | string): void {
  try {
    stderrBuffer += typeof chunk === "string" ? chunk : chunk.toString("utf8")
    if (stderrBuffer.length > STDERR_RING_BYTES) {
      stderrBuffer = stderrBuffer.slice(-STDERR_RING_BYTES)
    }
    console.error("[harness.auto-context worker stderr]", typeof chunk === "string" ? chunk : chunk.toString("utf8"))
  } catch {}
}

/**
 * Replies arrive as a byte stream, so stdout is buffered and split on
 * newlines; each complete line is handed to the oldest waiter (FIFO).
 * One request in flight at a time is enough for CTX Day 5 — no pools.
 */
type PendingReply = {
  done: boolean
  settle: (value: any | null) => void
}

const replyQueue: PendingReply[] = []
let stdoutBuffer = ""

/** Hand one reply line to the oldest waiter; unparseable → null (fail open). */
function onWorkerLine(line: string): void {
  let parsed: any
  try {
    parsed = JSON.parse(line)
  } catch {
    try {
      console.error("[harness.auto-context] worker reply was not JSON:", line.slice(0, 200))
    } catch {}
    parsed = null
  }
  while (replyQueue.length > 0) {
    const entry = replyQueue.shift()!
    if (!entry.done) {
      entry.settle(parsed)
      return
    }
  }
}

/** The worker died with waiters outstanding: settle them all with null. */
function drainQueue(): void {
  while (replyQueue.length > 0) {
    const entry = replyQueue.shift()!
    if (!entry.done) entry.settle(null)
  }
}

/** Spawn (once) and wire up the lazy MAP worker. */
function ensureWorker(): ChildProcess | null {
  if (worker !== null && workerReady) return worker

  let child: ChildProcess
  try {
    child = spawn("python3", [WORKER_PATH], {
      stdio: ["pipe", "pipe", "pipe"],
    })
  } catch (err) {
    console.error("[harness.auto-context] worker spawn failed:", err)
    worker = null
    workerReady = false
    return null
  }

  worker = child
  workerReady = true
  stdoutBuffer = ""

  child.stderr?.on("data", (chunk: Buffer) => captureStderr(chunk))

  child.stdout?.on("data", (chunk: Buffer) => {
    stdoutBuffer += chunk.toString("utf8")
    let idx: number
    while ((idx = stdoutBuffer.indexOf("\n")) >= 0) {
      const line = stdoutBuffer.slice(0, idx)
      stdoutBuffer = stdoutBuffer.slice(idx + 1)
      if (line.trim()) onWorkerLine(line)
    }
  })
  child.stdout?.on("error", (err: Error) => {
    try {
      console.error("[harness.auto-context] worker stdout error:", err.message)
    } catch {}
    drainQueue()
  })

  // Never let a broken pipe become an unhandled stream error.
  child.stdin?.on("error", (err: Error) => {
    try {
      console.error("[harness.auto-context] worker stdin error:", err.message)
    } catch {}
    drainQueue()
  })

  child.on("exit", () => {
    // Only clear module state if this child is still the current one.
    if (worker === child) {
      worker = null
      workerReady = false
    }
    drainQueue()
  })
  child.on("error", (err: Error) => {
    try {
      console.error("[harness.auto-context] worker error:", err.message)
    } catch {}
    if (worker === child) {
      worker = null
      workerReady = false
    }
    drainQueue()
  })

  return child
}

/**
 * Sends one request and resolves with its parsed reply.
 *
 * On timeout the child this call spawned is killed and null is returned;
 * on any parse error, write error, or worker exit the same happens, so
 * the caller always fails open instead of blocking the request.
 */
async function callWorker(request: object, timeoutMs: number): Promise<any | null> {
  try {
    const child = ensureWorker()
    if (!child || !child.stdin || !child.stdout) return null

    return await new Promise<any | null>((resolve) => {
      let timer: any = null
      let entry: PendingReply | null = null

      const finish = (value: any | null): void => {
        if (entry && entry.done) return
        if (entry) entry.done = true
        if (timer) clearTimeout(timer)
        if (entry) {
          const at = replyQueue.indexOf(entry)
          if (at >= 0) replyQueue.splice(at, 1)
        }
        resolve(value)
      }

      entry = { done: false, settle: finish }
      replyQueue.push(entry)

      timer = setTimeout(() => {
        try {
          console.error(
            "[harness.auto-context] worker timed out after %dms; killing",
            timeoutMs,
            stderrBuffer ? " stderr=" + stderrBuffer.slice(-200) : "",
          )
        } catch {}
        try {
          child.kill("SIGTERM")
        } catch {}
        finish(null)
      }, timeoutMs)

      try {
        child.stdin.write(JSON.stringify(request) + "\n", (err?: Error | null) => {
          if (err) {
            try {
              console.error("[harness.auto-context] worker stdin write failed:", err)
            } catch {}
            finish(null)
          }
        })
      } catch (err) {
        try {
          console.error("[harness.auto-context] worker stdin write threw:", err)
        } catch {}
        finish(null)
      }
    })
  } catch (err) {
    try {
      console.error("[harness.auto-context] callWorker failed:", err)
    } catch {}
    return null
  }
}

// --- end worker client -------------------------------------------------------

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

/** Upper bound on remembered file fingerprints (insertion-order LRU). */
const READ_CACHE_MAX = 32
const fingerprintCache = new Map<string, { sha256: string; size: number; mtimeMs: number }>()

/**
 * Bounded, insertion-order-LRU memo of a file's fingerprint.
 * A hit whose size and mtimeMs still match returns without reading the file,
 * so repeated hook firings inside one session never re-hash an unchanged file.
 * Fail open: any error returns null and the caller treats that as a miss.
 */
function cachedFingerprint(absPath: string): { sha256: string; size: number; mtimeMs: number } | null {
  let stat: { size: number; mtimeMs: number }
  try {
    const info = statSync(absPath)
    stat = { size: info.size, mtimeMs: info.mtimeMs }
  } catch {
    return null
  }

  const hit = fingerprintCache.get(absPath)
  if (hit && hit.size === stat.size && hit.mtimeMs === stat.mtimeMs) {
    // Insertion-order LRU: re-insert to bump the entry to most-recent.
    fingerprintCache.delete(absPath)
    fingerprintCache.set(absPath, hit)
    return hit
  }

  let entry: { sha256: string; size: number; mtimeMs: number } | null
  try {
    const buffer = readFileSync(absPath)
    const digest = sha256Of(buffer)
    if (!digest) return null
    // Key on the bytes actually hashed: if the file changed between the
    // statSync above and this read, the size mismatch forces a future miss.
    entry = { sha256: digest, size: buffer.length, mtimeMs: stat.mtimeMs }
  } catch {
    return null
  }

  fingerprintCache.delete(absPath)
  fingerprintCache.set(absPath, entry)
  while (fingerprintCache.size > READ_CACHE_MAX) {
    const oldest = fingerprintCache.keys().next()
    if (oldest.done) break
    fingerprintCache.delete(oldest.value)
  }
  return entry
}

/** One direct read + hash, used when the cache layer could not help (fail open). */
function directDigest(absPath: string): string | null {
  try {
    return sha256Of(readFileSync(absPath))
  } catch {
    return null
  }
}

/**
 * Canonicalizes `relPath` inside `root` and fingerprints the file.
 * Returns null when the path escapes the root, is sensitive, or is unreadable.
 * This is the single authorization code path shared by buildPacket and
 * decideGate (Q3); decideGate only needs the digest for REUSE comparison.
 */
function fingerprintInRoot(
  root: string,
  relPath: string,
): { rel: string; absPath: string; sha256: string } | null {
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

  const cached = cachedFingerprint(resolved)
  if (cached) return { rel, absPath: resolved, sha256: cached.sha256 }

  const digest = directDigest(resolved)
  if (!digest) return null
  return { rel, absPath: resolved, sha256: digest }
}

/**
 * Fingerprint plus raw bytes of the file. The packet's sha256 line reuses the
 * fingerprint computed by `fingerprintInRoot`, so the content is never hashed
 * a second time and the REUSE digest always matches the injected packet.
 */
function readFileInRoot(root: string, relPath: string): { rel: string; buffer: any; sha256: string } | null {
  const fingerprint = fingerprintInRoot(root, relPath)
  if (!fingerprint) return null

  let buffer: any
  try {
    buffer = readFileSync(fingerprint.absPath)
  } catch {
    return null
  }
  if (!buffer || typeof buffer.length !== "number") return null

  return { rel: fingerprint.rel, buffer, sha256: fingerprint.sha256 }
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

  const digest = read.sha256
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
 * Everything except the `architecture of` / `where is` clauses still
 * requires an explicit repo/project/codebase mention, so a plain
 * conversational prompt ("hi, how are you today?") stays NONE.
 * The `structure`/`overview` clauses are the broad forms of the Day 2
 * `structure of the` / `overview of the` probes so that
 * "How is this repository structured?" reads as a MAP signal.
 */
function looksLikeRepoQuestion(text: string): boolean {
  const t = text.toLowerCase()
  const mentionsRepo =
    t.includes("repo") || t.includes("project") || t.includes("codebase")
  if (t.includes("where is") && (t.includes("implemented") || t.includes("defined") || t.includes("used"))) return true
  if (t.includes("how does the") && mentionsRepo) return true
  if (t.includes("architecture of")) return true
  if (t.includes("structure") && mentionsRepo) return true
  if (t.includes("overview") && mentionsRepo) return true
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
  // Only the fingerprint is needed here; the gate never reads file content.
  const fingerprint = fingerprintInRoot(workspaceRoot, token)
  if (!fingerprint) return { decision: "FOCUSED", token, reason: "packet-missing-or-stale" }

  const rel = fingerprint.rel.split(sep).join("/")
  for (const packet of existingPacketDigests(messages)) {
    if (packet.source === rel && packet.sha256 === fingerprint.sha256) {
      return { decision: "REUSE", token, reason: "fresh-packet-present" }
    }
  }
  return { decision: "FOCUSED", token, reason: "packet-missing-or-stale" }
}

export default {
  id: "harness.auto-context",
  async setup(ctx: any) {
    if (process.env.HARNESS_CTX === "off") return

    const registration = await ctx.session.hook("context", async (event: any) => {
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
        // MAP: spawn a lazy Python worker to produce a ranked repo map,
        // inject it as a single labeled packet (no tool call).  The worker's
        // in-process fingerprint cache (Day 3) makes repeat calls cheap; this
        // branch always spawns on every repo question — the worker handles
        // deduplication via its own cache, so we do not REUSE here.
        if (gate.decision === "MAP") {
          try {
            const mapReply = await callWorker(
              { cmd: "map", root, max_tokens: 1500 },
              30000,
            )
            if (!mapReply || !mapReply.ok || typeof mapReply.text !== "string"
                || !mapReply.text.trim()) {
              // Fail open — do not block the request.
              return
            }

            // Idempotency: remove any stale harness-owned packet first
            // (same as FOCUSED).
            for (let i = messages.length - 1; i >= 0; i--) {
              const metadata = messages[i]?.metadata
              if (metadata && metadata.harness_auto_context === true) {
                messages.splice(i, 1)
              }
            }

            const digest = createHash("sha256").update(mapReply.text, "utf8").digest("hex")
            if (!digest) {
              return
            }
            const packet = [
              HEADER,
              "# source: <ranked-map>",
              `# sha256: ${digest}`,
              `# bytes: ${mapReply.text.length} (truncated: no)`,
              "",
              mapReply.text,
              FOOTER,
            ].join("\n")

            const target = lastUserText(messages)
            if (!target) return

            messages.splice(target.index, 0, {
              id: "ctx_" + Date.now().toString(36),
              role: "user",
              content: [{ type: "text", text: packet }],
              metadata: { harness_auto_context: true },
            })
          } catch (err) {
            // Fail open: any worker error leaves the request unmodified.
            try {
              console.error("[harness.auto-context] MAP worker failed:", err)
            } catch {}
          }
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
      // CTX Day 5: kill the lazy MAP worker so it never outlives the plugin.
      if (worker !== null) {
        try {
          worker.kill("SIGTERM")
        } catch {}
        worker = null
        workerReady = false
      }
    }
  },
}
