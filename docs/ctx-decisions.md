# CTX Design Decisions

**Status:** Locked  
**Verified environment:** OpenCode v2.0.24, @opencode/plugin@0.0.0-beta-19507  
**Spike evidence:** docs/ctx-spike-findings.md

These are the six design decisions for the conditional auto-context
(CTX) workstream, informed by the CTX spike. ChatGPT's review refined
Q1, Q3, and Q4 from the initial recommendations.

## Q1 — Request kind: register `context` only

**Decision:** Register `ctx.session.hook("context", cb)` for the
primary agent loop. Do NOT duplicate retrieval logic for compaction,
title, or generate. Treat kind ambiguity as an SDK compatibility
question to test, not to solve with `http.request.kind` correlation.

**Why:** The spike showed `context` fires for both primary and title
requests. `http.request` does carry `kind`, but ChatGPT flagged that
HTTP-event matching is late and fragile with concurrent requests and
WebSocket transports.

**Regression:** CTX-API-01, CTX-VERSION-01.

## Q2 — TypeScript types: dev dependency, not vendored SDK

**Decision:** Pin the compatible `@opencode/plugin` package as a
development-only dependency. Use `import type`. Do not vendor the
entire SDK. Do not use `any` to hide incompatible shapes.

**Why:** Runtime does not need the types (Bun transpiles, `import type`
is erased). Editor/typecheck workflows do need them. Vendoring would
add ~500 KB to the repo for no runtime benefit.

**Regression:** CTX-VERSION-02.

## Q3 — Filesystem access: approved workspace only

**Decision:** Auto-context reads are restricted to explicitly approved
canonical workspace/worktree roots. Full rules:

| Situation | Automatic action |
|---|---|
| No workspace explicitly approved | No source reads |
| File inside approved workspace + permitted under effective rules | Eligible for needed, bounded reads |
| File already read earlier in session | Reuse current evidence when valid; recheck authorization before any new read |
| Relevant file never read, inside approved workspace | May read when the conditional gate establishes a need |
| `.env`, credentials, keys, ignored sensitive files, external symlink targets | Excluded by default |
| Path outside approved workspace | No automatic read without separately established authorization |
| Explicit deny, revoked permission, or uncertain effective authorization | Do not read. Report degraded/unavailable coverage when the task needs it |
| Child session or different Git worktree | Reestablish approved scope; do not inherit unrelated cached eligibility |

A plain workspace-directory check is not sufficient. Implementation
also needs canonical path validation, sensitive-file exclusions, and a
reliable authorization policy. If the installed SDK cannot provide the
effective native/session read decision, be conservative rather than
treating unrestricted `fs.read` as permission.

**Why (ChatGPT's argument, adopted):** The spike proved plugins can
read any file without a permission prompt. A previous-reuse-only policy
(B) would prevent discovering missing code. Option C would trust the
SDK's broader access. Option A with explicit approvals preserves the
"discover missing code inside the approved root" capability while
keeping the safety boundary.

**Regression:** CTX-SEC-01.

## Q4 — Injection format: labeled reference data, not fake dialogue

**Decision:** Inject one bounded, labeled `HARNESS REPOSITORY CONTEXT —
reference data` message through the installed SDK's valid message
schema. Do NOT copy Aider's fake user/assistant exchange. Do NOT
mutate historical messages. Do NOT fabricate tool history. Do NOT
promote raw source text into authoritative system instructions.

**Injection contract:**

| Aspect | Required behavior |
|---|---|
| Content | One small `HARNESS REPOSITORY CONTEXT — reference data` packet with paths, relevant source snippets, fingerprints, coverage limits |
| Representation | Newly constructed message with valid installed-SDK schema; user-level reference content without pretending the human wrote it and without an assistant acknowledgement |
| Placement | Dispatch-only position that preserves chronological semantics and does not separate tool calls from results |
| Trust boundary | Code comments, README instructions, and source text remain untrusted data; a short trusted guidance statement may describe how to consume them |
| Duplication | At most one harness-owned packet per outgoing request; reuse current evidence and remove only obsolete harness-generated context |
| Persistence | Do not change pre-existing message parts or their IDs; confirm the packet does not appear as a real user message in durable history |

**Why (ChatGPT's argument, adopted):** OpenCode already has a real
conversation with tool calls. Injecting a fake assistant acknowledgment
can break tool-call/result pairing and, per a reported OpenCode V2 bug,
message-part mutations can persist unexpectedly. A labeled reference
packet avoids both.

**Regression:** CTX-MSG-01, CTX-MSG-02, CTX-MSG-03.

## Q5 — Provider scope: unscoped

**Decision:** Register `session.hook("context", cb)` unscoped across
providers. Preserve the user's provider/model configuration. Apply
budget/format compatibility adjustments only where the installed
provider actually requires them.

**Regression:** CTX-PROVIDER-01.

## Q6 — Version pinning: record, re-verify on upgrade

**Decision:** Record the tested OpenCode binary version, plugin package
version, resolved SDK/build identity, and successful integration tests
in `templates/harness_core/versions.json`. Rerun compatibility tests
after any OpenCode upgrade. Do not assume beta hooks are stable merely
because TypeScript compiles.

**Regression:** CTX-VERSION-01, CTX-VERSION-02.

## The NONE / REUSE / FOCUSED / MAP gate (unchanged)

From the handoff, retained exactly:

| Request | Gate | Host work | Model receives |
|---|---|---|---|
| "Hi" or general question | NONE | No worker, indexing, parsing, or source read | No additional repository context |
| "Continue" with useful source already current | REUSE | No new retrieval | Existing evidence |
| "Fix function in service.py" with source missing | FOCUSED | Authorized targeted read | Relevant function, location, context |
| "Find where authentication is implemented" | MAP | Aider-ranked repository navigation | Small ranked locations/relationships |
| "Refactor shared authentication flow" | MAP or FOCUSED | Missing candidates + targeted source | Relevant source with uncertainty labels |
| Native edit just changed a function | Re-evaluate | Mark dirty; refresh lazily | Current edit evidence or newly required source |
| Model switches during unrelated conversation | NONE | No parsing/ranking | No unsolicited map |

**Critical principle:** The model is not being asked whether the context
supplier should run. The host determines an established need and supplies
the information.

## Required regressions before shipping CTX

| Test ID | What it proves |
|---|---|
| CTX-API-01 | Only primary activates retrieval; auxiliary hooks don't trigger source reads |
| CTX-API-02 | Zero-tool-call coding model still receives missing context (no MCP needed) |
| CTX-API-03 | General conversation in approved repo → zero worker/source/index ops |
| CTX-SEC-01 | Denied files, `.env`, symlink escape, permission revocation → no leak |
| CTX-MSG-01 | Insert context into active tool-loop request; all legit tool-call/result pairs intact |
| CTX-MSG-02 | No fake assistant acknowledgements, no historical mutation |
| CTX-MSG-03 | Final serialized request has exactly one valid context packet; builder logs alone don't count |
| CTX-VERSION-01 | Installed binary/SDK combination passes registration + injection + serialization |
| CTX-VERSION-02 | Unsupported API → degraded state, no false "context works" claim |
| CTX-PROVIDER-01 | Two providers → same relevance, no duplicate packets |

**The critical one:** CTX-MSG-03. Final-request capture is the only
acceptable proof. A passing registration test is not sufficient.
