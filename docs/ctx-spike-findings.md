# CTX Spike — FINDINGS (OpenCode SDK discovery)

Spike date: 2026-10-09
Repo: `harness-optimizer` @ branch `spike/ctx-sdk-discovery`
Mode: read-only discovery. No tracked file was modified, nothing committed or pushed.
All artifacts live under `/tmp/opencode/ctx_spike/`.

---

## 1. Installed OpenCode

| Item | Value |
|---|---|
| Binary path | `/home/sriram/.opencode/bin/opencode` (single 193 MB Bun-compiled executable; wrapper `/home/sriram/.opencode/bin/opencode2`) |
| Version | `opencode v2.0.24` |
| Config dir (real) | `/home/sriram/.config/opencode/` (`opencode.json`, `plugins/harness.ts`, …) |
| Data dir (real) | `/home/sriram/.local/share/opencode/` (`opencode.db`, logs, …) |
| Plugin SDK types (only copy on disk) | `/home/sriram/.config/opencode/node_modules.bak/@opencode/plugin/` — package `@opencode/plugin@0.0.0-beta-19507` |
| Sibling SDK packages | `/home/sriram/.config/opencode/node_modules.bak/@opencode/{ai,client,plugin,protocol,schema,util}` (same `0.0.0-beta-19507`) |
| Type entry points | `@opencode/plugin` → `dist/promise/index.d.ts` (promise API), `dist/effect/index.d.ts` (effect API), `dist/host.d.ts`, `dist/tui/index.d.ts` |
| Live `node_modules` | **none** — `~/.config/opencode/node_modules` does not exist; only `node_modules.bak` (snapshot from 2026-10-06) |
| Schema types (permissions, messages, …) | `~/.config/opencode/node_modules.bak/@opencode/schema/dist/*.d.ts` (e.g. `permission.d.ts`, `provider.d.ts`) |
| Existing plugin in repo/templates | `/home/sriram/developer/harness-optimizer/templates/token-metrics.ts` (installed copy: `~/.config/opencode/plugins/harness.ts`) |
| Global npm plugin installs (cached) | `~/.cache/opencode/npm/{opencode-context-watch,opencode-runtime-fallback,opencode-ollama-toolcall-proxy}@latest` |

Type/version caveat: the types are a dev build (`0.0.0-beta-19507`) sitting in a `.bak`
snapshot, while the binary reports `v2.0.24`. Alignment was cross-checked against the
binary itself (see §2): **every hook name declared in the `.d.ts` files also appears in
the bundled runtime code**, and runtime behavior matched the types exactly in Phases 3–4.
The binary additionally exposes internal-only surfaces not present in the public types
(`provider.transform`, `session.hook("experimental.ws.handshake")`), i.e. built-in
plugins get a slightly richer context than typed plugins do.

Isolating env vars discovered in the binary (used by this spike):
`OPENCODE_CONFIG_DIR` (config directory), `OPENCODE_CONFIG_CONTENT` (virtual config doc),
`OPENCODE_TEST_HOME` (redirects the app "home" used for data/state),
`OPENCODE_DISABLE_MODELS_FETCH`, `--standalone` (private server instead of shared service).

---

## 2. Hook inventory (from the installed `.d.ts` + confirmed in the binary)

Hook registration helper types — verbatim from
`node_modules.bak/@opencode/plugin/dist/promise/registration.d.ts`:

```ts
export interface Registration {
    readonly dispose: () => Promise<void>;
}
export interface ModelHookOptions {
    /** Limits the hook to one provider. Unscoped hooks apply to every provider. */
    readonly providerID?: string;
}
export type Hooks<Spec> = <Name extends keyof Spec>(name: Name, callback: (input: Spec[Name]) => Promise<void> | void) => Promise<Registration>;
export type ModelHooks<Spec> = <Name extends keyof Spec>(name: Name, callback: (input: Spec[Name]) => Promise<void> | void, options?: Spec[Name] extends {
    readonly model: unknown;
} ? ModelHookOptions : never) => Promise<Registration>;
export type Transform<Input> = (callback: (input: Input) => void) => Promise<Registration>;
```

Plugin entry point — verbatim from `dist/promise/plugin.d.ts`:

```ts
export interface Plugin {
    readonly id: string;
    readonly setup: (context: Context) => Promise<Cleanup | void> | Cleanup | void;
}
export declare function define(plugin: Plugin): Plugin;
```

| Domain accessor | Kind | Events / capability | In `.d.ts` | Seen in binary |
|---|---|---|---|---|
| `ctx.session.hook(name, cb)` | hooks | `prompt`, `context`, `compaction`, `generate`, `title`, `model.request`, `http.request`, `http.response`, `retry` | yes | yes (`context`×10, `generate`×9, `compaction`×9, `http.request`×3, `title`×2, `retry`×2, `model.request`×2, `http.response`×2, `experimental.ws.handshake`×1 — last one is internal-only, not in the public types) |
| `ctx.tool.hook(name, cb)` | hooks | `execute.before`, `execute.after` | yes | yes (`execute.before`×2, `execute.after`×1 in built-ins; used by the installed harness plugin) |
| `ctx.tool.transform(cb)` | transform | add/update/remove/namespace tools at boot | yes | yes |
| `ctx.permission.hook("evaluate", cb)` | hooks | permission evaluation for tool calls | yes | yes (×1) |
| `ctx.permission` API | api | `list`, `get`, `reply`, `rules` | yes | (schema present) |
| `ctx.shell.hook("create.before", cb)` | hooks | mutate `{command, cwd, timeout, shell, env}` before a shell spawns | yes | (shell hooks exist) |
| `ctx.aisdk.hook(name, cb)` | hooks | `sdk`, `language` (swap the AI SDK / LanguageModelV3) | yes | yes (`sdk`×6, `language`×4) |
| `ctx.agent.transform(cb)` | transform | mutate agent definitions | yes | yes |
| `ctx.provider.transform(cb)` | transform | mutate provider definitions | **not in public `Context`** | yes (internal) |
| `ctx.{catalog,command,integration,mcp,skill,vcs,websearch,worktree,reference}.transform(cb)` | transform | mutate those registries | yes | yes |
| `ctx.event.subscribe` | api | event stream subscription | yes | — |
| `ctx.storage.{get,set,remove,scan}` | api | plugin-private KV store | yes | — |
| `ctx.rpc.register(def, handlers)` | api | expose RPC methods + emit events | yes | — |
| `ctx.generate` / `ctx.session.*` | api | full client API (`prompt`, `generate`, `create`, `synthetic`, …) | yes | — |

`Context` (verbatim shape, `dist/promise/plugin.d.ts`):

```ts
export interface Context {
    readonly app: App;
    readonly location: Location.Info;
    readonly options: PluginOptions;
    readonly agent: AgentDomain;
    readonly aisdk: AISDKDomain;
    readonly catalog: CatalogDomain;
    readonly command: CommandDomain;
    readonly event: EventDomain;
    readonly experimental: {
        readonly terminal: Pick<OpenCodeClient["experimental"]["persistentPty"], "read">;
    };
    readonly integration: IntegrationDomain;
    readonly mcp: MCPDomain;
    readonly generate: GenerateApi;
    readonly permission: PermissionDomain;
    readonly plugin: Pick<PluginApi, "list">;
    readonly reference: ReferenceDomain;
    readonly rpc: RpcDomain;
    readonly session: SessionDomain;
    readonly shell: ShellDomain;
    readonly skill: SkillDomain;
    readonly storage: StorageDomain;
    readonly tool: ToolDomain;
    readonly vcs: VcsDomain;
    readonly websearch: WebSearchDomain;
    readonly worktree: WorktreeDomain;
}
```

---

## 3. Q1 — primary-context hook

**Answer: EXISTS, exactly as documented. `ctx.session.hook("context", callback)` is the
pre-model-call hook; it exposes (and lets you mutate) the outgoing message array.**

Verbatim types from `dist/promise/session.d.ts`:

```ts
export interface SessionPrompt {
    readonly sessionID: Session.ID;
    readonly messageID: SessionMessage.ID;
    prompt: Types.DeepMutable<PromptInput.Prompt>;
    metadata?: Record<string, unknown>;
    delivery: SessionInbox.Delivery;
}
/** Request overrides. Typed keys are generation settings; any other key is a provider option. */
export type SessionRequestOptions = Types.DeepMutable<GenerationOptionsFields> & Record<string, unknown>;
export interface SessionRequest {
    readonly sessionID: Session.ID;
    readonly model: Model.Ref;
    system: Array<SystemPart>;
    messages: Array<Message>;
    options: SessionRequestOptions;
}
export interface SessionContext extends SessionRequest {
    readonly agent: Agent.ID;
    tools: Record<string, {
        description: string;
        input: JsonSchema.JsonSchema;
    }>;
}
export interface SessionCompaction extends SessionContext {
    /** Set to use this compaction and skip the model request. */
    result?: SessionCompactionResult;
}
export interface SessionGenerate extends SessionContext {
}
export interface SessionTitle extends SessionRequest {
    /** Set to use this title and skip the model request. */
    result?: string;
}
export type SessionRequestKind = "primary" | "compaction" | "title" | "generate";
export interface SessionHooks {
    readonly prompt: SessionPrompt;
    readonly context: SessionContext;
    readonly compaction: SessionCompaction;
    readonly generate: SessionGenerate;
    readonly title: SessionTitle;
    readonly "model.request": SessionModelRequest;
    readonly "http.request": SessionHttpRequest;
    readonly "http.response": SessionHttpResponse;
    readonly retry: SessionRetry;
}
export type SessionDomain = Pick<SessionApi, "create" | "get" | "switchAgent" | "switchModel" | "prompt" | "generate" | "command" | "synthetic" | "interrupt" | "rename" | "move" | "wait" | "context"> & {
    readonly hook: ModelHooks<SessionHooks>;
};
```

Key payload facts (confirmed live, §4 evidence in hooks.log):

- `system` is `Array<SystemPart>` — the 4 default parts observed were all `{type:"text", text:...}`.
- `messages` is `Array<Message>`; runtime shape observed was
  `{id, role:"user", content:[{type:"text", text}], metadata:{}}` and
  `{role:"tool", content:[{type:"tool-result", id, name, result:{type:"text", value}, providerExecuted:false}]}`.
  Note: the runtime array is `content`-keyed, **not** `parts`-keyed.
- `tools` is a plain map of tool name → `{description, input(json-schema)}` (observed:
  `edit, glob, grep, question, read, shell, skill, subagent, webfetch, websearch, write, execute`).
- The callback returns `void` — mutation happens **in place** on the arrays
  (the bundled OpenCode plugins do exactly that, e.g.
  `s.messages.splice(f, 0, dH.user(_))` inside a `session.hook("context", …)` callback).
- The `context` hook fired **once per model call**: twice for a 2-turn exchange
  (initial turn, then again with the tool result appended).
- `compaction`/`title` hooks accept a `result` field that **skips the model request**
  (the fastest CTX injection/suppression path for those auxiliary requests).
- Limitation: `SessionContext` (the `context` hook payload) has **no `kind` field**, so
  from inside a `context` callback you cannot directly distinguish primary-loop vs
  compaction vs generate the way `http.request`/`model.request` can (`kind: SessionRequestKind`).
  Bundled plugins register the same callback on `context` + `compaction` + `generate`,
  which suggests they are meant to be treated uniformly.

---

## 4. Q2 — tool-result hook

**Answer: CONFIRMED. `ctx.tool.hook("execute.after", callback)` is the real installed
signature, and the existing `templates/token-metrics.ts` (`ctx.tool.hook("execute.after", …)`)
is correct.**

Verbatim types from `dist/promise/tool.d.ts`:

```ts
interface ToolHooks {
    readonly "execute.before": {
        tool: string;
        readonly sessionID: Session.ID;
        readonly agent: Agent.ID;
        readonly messageID: SessionMessage.ID;
        readonly id: Tool.CallID;
        input: unknown;
    };
    readonly "execute.after": {
        readonly tool: string;
        readonly sessionID: Session.ID;
        readonly agent: Agent.ID;
        readonly messageID: SessionMessage.ID;
        readonly id: Tool.CallID;
        readonly input: unknown;
    } & ({
        readonly status: "completed";
        result: Tool.Result;
    } | {
        readonly status: "error";
        error: Tool.Error;
    });
}
export interface ToolDomain {
    readonly transform: Transform<ToolEditor>;
    readonly reload: () => Promise<void>;
    readonly hook: Hooks<ToolHooks>;
}
```

### Captured real event payload (scratch plugin `spike-tool-result.ts`, /tmp/opencode/ctx_spike/events.log)

```json
{"marker":"plugin.setup entered","at":"2026-10-09T20:12:26.250Z"}
{"marker":"hook registered","event":"tool.execute.after","registrationKeys":["dispose"]}
{"hook":"tool.execute.after","at":"2026-10-09T20:17:33.524Z","event":{"tool":"read","sessionID":"ses_eddafa958ffej5TFLNqfu01QCP","agent":"build","messageID":"msg_1225058f6001GCMacSEBkYLmDh","id":"call_read_1","input":{"path":"/tmp/opencode/ctx_spike/workspace/hello.py"},"status":"completed","result":{"output":{"type":"file","uri":"file:///tmp/opencode/ctx_spike/workspace/hello.py","name":"hello.py","content":"def greet(name):\n    return f\"hello {name}\"\n\n\nprint(greet(\"spike\"))\n","encoding":"utf8","mime":"application/octet-stream"},"content":[{"type":"text","text":"Read file /tmp/opencode/ctx_spike/workspace/hello.py, lines 1-5\n1: def greet(name):\n2:     return f\"hello {name}\"\n3: \n4: \n5: print(greet(\"spike\"))"}],"metadata":{"truncated":false}}}}
```

(Verbatim lines from `events.log`.)
Observed facts: built-in tools are addressed by bare name (`read`); `result` is
`{output?, content[], metadata?}`; the hook fired for the successful `read`; registration
returns `{dispose}` exactly as typed.

---

## 5. Q3 — file authorization for plugins

**Plugin-level permission API: YES (for tool calls), but it does NOT govern plugin I/O.**

Verbatim types from `dist/promise/permission.d.ts` and `@opencode/schema/dist/permission.d.ts`:

```ts
export interface PermissionEvaluation {
    readonly sessionID: Session.ID;
    readonly agent?: Agent.ID;
    readonly action: string;
    readonly resources: ReadonlyArray<string>;
    readonly metadata?: Record<string, unknown>;
    readonly source?: Permission.Source;
    effect: Permission.Effect;
    message?: string;
}
export interface PermissionHooks {
    readonly evaluate: PermissionEvaluation;
}
export type PermissionDomain = Pick<PermissionApi, "list" | "get" | "reply" | "rules"> & {
    readonly hook: Hooks<PermissionHooks>;
};
```

```ts
export declare const Effect: Schema.Literals<readonly ["allow", "deny", "ask"]>;
export interface Rule extends Schema.Schema.Type<typeof Rule> {
}
export declare const Rule: Schema.Struct<{
    readonly action: Schema.String;
    readonly resource: Schema.String;
    readonly effect: Schema.Literals<readonly ["allow", "deny", "ask"]>;
}>;
```

Default agent rules (extracted from the installed binary):

```js
{action:"*",resource:"*",effect:"allow"},
{action:"external_directory",resource:"*",effect:"ask"},
{action:"read",resource:"*.env",effect:"ask"},
{action:"read",resource:"*.env.*",effect:"ask"},
{action:"read",resource:"*.env.example",effect:"allow"}
```

Permission actions observed in the binary's resource-mapping code:
`shell` (workdir/cwd), `read`/`edit`/`write` (workspace root), `patch` (paths + move paths),
`external_directory` (filepath), `grep`/`glob`/`context`/`context7_*` (path), `webfetch` (url),
`websearch` (query). So "outside the workspace" is governed by the **`external_directory`**
action, evaluated in the **native tool layer** before the tool runs.

Live evidence (`/tmp/opencode/ctx_spike/permission.log`) — permission hook fired for the tool call:

```json
{"hook":"permission.evaluate","sessionID":"ses_eddafa958ffej5TFLNqfu01QCP","agent":"build","action":"read","resources":["hello.py"],"effect":"allow","source":{"type":"tool","messageID":"msg_1225058f6001GCMacSEBkYLmDh","id":"call_read_1"}}
```

Live evidence — **a plugin reads outside the workspace with no permission involvement**:

```json
{"probe":"plugin.fs.read_outside_workspace","workspace":"/tmp/opencode/ctx_spike/workspace","readPath":"/etc/hostname","readOk":true,"content":"sriram-Latitude-6430U","alsoRead":"/home/sriram/.config/opencode/tui.json","alsoReadOk":true,"alsoReadHead":"{\n  \"$schema\": \"https://opencode.ai/tui.json\",\n  \"mouse\": true,\n  \"keybinds\": {\n"}
```

**Path that governs reads outside the workspace:** for the *agent's tools* it is
permission rules → `external_directory` (+ `read` resource rules like `*.env`) → TUI/approval
flow (`ctx.permission.reply`, `permission.replied` events with `once|always|reject`).
For *plugins* there is **no filesystem gate at all**: plugin `setup()` runs as native JS inside
the OpenCode process with ordinary Node/Bun `fs`, i.e. any path the OS user can read/write
is readable/writable, subject only to OS permissions. The existing harness plugin already
relies on this (it reads/writes `~/.config/opencode/.harness-*` files).

---

## 6. Request capture

**Could a local test capture the final outgoing request? YES.**

Mechanism (SDK-native): `ctx.session.hook("http.request", async (event) => { … })`, where
`event.request` is a fetch `Request`; `await event.request.clone().text()` yields the exact
serialized body that goes to the provider. Payload type (verbatim):

```ts
export interface SessionHttpRequest {
    readonly sessionID: Session.ID;
    readonly agent: Agent.ID;
    readonly model: Model.Ref;
    readonly kind: SessionRequestKind;
    request: Request;
}
```

Observed in `/tmp/opencode/ctx_spike/hooks.log` (local stub provider, zero paid calls):

- `index 1` — `agent:"title"`, `kind:"title"`, 2446 bytes (session title generation)
- `index 2` — `agent:"build"`, `kind:"primary"`, 21162 bytes (first model call)
- `index 3` — `agent:"build"`, `kind:"primary"`, 21564 bytes (after the tool result)

Full serialized bodies: `/tmp/opencode/ctx_spike/request.json` (latest) and
`request_hook.jsonl` (all three, with url + parsed body). Cross-check: the stub provider
independently recorded the same bodies (`request.provider-run1.jsonl`, 15 lines — the
extra lines are the stub's own repeat-answer loop, not extra SDK requests; the SDK-side
capture shows exactly 3).

Other pre-request surfaces, in increasing invasiveness:
- `ctx.session.hook("context")` — mutate `system`/`messages`/`options`/`tools` (recommended for CTX).
- `ctx.session.hook("model.request")` — `{sessionID, agent, model, kind, baseURL?, headers}`
  (headers only; bundled plugins use it to inject auth headers, provider-scoped).
- `ctx.session.hook("http.request")` — the actual `Request` (headers + body).
- `ctx.session.hook("http.response")` — `{…, request, response}` (response visible/mutable).

Wire shape seen at the provider (OpenAI-compatible, `stream:true`): keys
`model, messages, store, stream, stream_options, tools?, max_completion_tokens?`;
tools list was
`edit, glob, grep, question, read, shell, skill, subagent, webfetch, websearch, write, execute`.

---

## 7. CTX feasibility verdict

**CAN build CTX as specified: YES.**

Everything CTX needs exists and was executed against the installed `v2.0.24`:

1. Inspect the outgoing context before every model call → `session.hook("context")`
   (fired twice in a 2-turn exchange; payload carries `system`, `messages`, `tools`, `options`).
2. Mutate it in place → observed/typed as in-place array mutation; bundled plugins splice
   messages and delete tools inside the same hook.
3. Observe tool results as they land → `tool.hook("execute.after")` (the shipped
   `token-metrics.ts` already does this correctly).
4. Gate/decide file access → `permission.hook("evaluate")` + `permission.{rules,reply}`.
5. Measure/verify the wire effect → `session.hook("http.request")` with a local stub provider.

What the handoff got right:
- `ctx.session.hook("context", …)` exists with exactly that name.
- `ctx.tool.hook("execute.after", …)` (as used in `templates/token-metrics.ts`) is the real,
  installed signature, and the event fields it reads (`event.tool`, `event.status`,
  `event.id`, `event.result.output.result`) are plausible; only `result.output.result` is
  harness-server-specific, not SDK-specific (the SDK's `result.output` for MCP tools is the
  tool's JSON output).
- Hooks return a `Registration` with `dispose()` — cleanup/teardown is supported.

What the handoff got wrong / left unstated:
- Nothing was found to be *wrong*; the gaps are omissions:
  - `SessionContext` has **no `kind` field**, so a `context` hook cannot trivially tell
    primary vs compaction vs generate (only `http.request`/`model.request` carry `kind`).
  - The runtime message shape is `content: [...]`, not `parts: [...]`.
  - Hooks are per-model-call, so CTX logic must be idempotent (the existing harness plugin
    already dedupes by call id — that pattern is required, not optional).
  - `@opencode/plugin` types are **not** installed in a live `node_modules` on this box;
    the only copy is `~/.config/opencode/node_modules.bak/@opencode/plugin@0.0.0-beta-19507`.
    Runtime does not need them (plugins are transpiled by the binary and `import type`
    is erased), but editor/typecheck workflows do.
  - The public typed `Context` is a subset of what internal plugins use
    (`provider.transform`, `experimental.ws.handshake` exist in the binary only).

Reproduction commands (all against the local stub, no network beyond 127.0.0.1):

```bash
python3 /tmp/opencode/ctx_spike/stub_provider.py &   # 127.0.0.1:8123, OpenAI-compatible + SSE
export OPENCODE_CONFIG_DIR=/tmp/opencode/ctx_spike/config \
       OPENCODE_TEST_HOME=/tmp/opencode/ctx_spike/home \
       OPENCODE_DISABLE_MODELS_FETCH=1
cd /tmp/opencode/ctx_spike/workspace
opencode run --standalone --auto -m stub/stub-1 \
  "Use the read tool to read hello.py, then tell me the function name defined in it."
```

Result: `→ Read hello.py` / `Fixture file read; spike complete.` with hooks firing as documented.

---

## 8. Open questions for the human

1. **Compaction vs primary disambiguation** — if CTX must apply only to the primary loop,
   there is no `kind` in the `context` payload. Do we (a) also register `http.request` and
   correlate by session/turn, (b) accept running the same logic for compaction/generate
   (what bundled plugins do), or (c) use `compaction`'s `result` field to skip those requests?
2. **Where does the CTX plugin live** — global `~/.config/opencode/plugins/` (like
   `harness.ts`), or shipped via `templates/` and installed by `bin/install.js`? Also: should
   the repo vendor `@opencode/plugin@0.0.0-beta-19507` for typechecking, given there is no
   live `node_modules` in the config dir today?
3. **Out-of-workspace reads from CTX** — plugin `fs` is unrestricted (proven in §5), so CTX
   can read anything the OS user can, bypassing the `external_directory` approval path that
   the tools obey. Should CTX self-gate (e.g. consult `ctx.permission.rules` or only read
   paths the agent has already touched), or is unbounded plugin I/O acceptable for this
   workstream? This is a policy decision, not a technical blocker.
4. **Injection point** — system-part injection (`event.system.push(...)`) vs message injection
   (`event.messages.splice(...)`) vs `options` overrides. Which does CTX want, and does it
   need to run before or after OpenCode's own context builders (hook order is not documented)?
5. **Provider-scoped vs global hooks** — `session.hook` accepts an optional `{providerID}`
   scope only for hooks whose payload carries `model`. CTX presumably wants it unscoped; confirm.
6. **Version pinning** — types are `0.0.0-beta-19507`, binary is `v2.0.24`. Confirm this
   pairing is the supported one before depending on exact payload shapes in CORE work.

---

## Files created under /tmp/opencode/ctx_spike/

| Path | Purpose |
|---|---|
| `FINDINGS.md` | this report |
| `config/opencode.json` | copy of the user config + `stub` provider + MCP disabled (real config untouched) |
| `config/plugins/spike-tool-result.ts` | Phase 3 scratch plugin (`tool.hook("execute.after")` only) |
| `config/plugins/spike-request-probe.ts` | Phase 4 scratch plugin (`session.hook("context")` + `session.hook("http.request")`) |
| `config/plugins/spike-permission-probe.ts` | Q3 scratch plugin (fs read outside workspace + `permission.hook("evaluate")`) |
| `stub_provider.py` | local OpenAI-compatible stub provider (SSE), records raw bodies |
| `summarize_requests.py` | summarizes `request*.jsonl` captures |
| `workspace/hello.py` | fixture workspace file |
| `home/` | `OPENCODE_TEST_HOME` redirect (isolated app data) |
| `events.log` | captured `tool.execute.after` payloads |
| `hooks.log` | captured `session.context` / `session.http.request` payloads |
| `permission.log` | captured `permission.evaluate` + plugin fs probe |
| `request.json` | latest full serialized outgoing request (via `http.request` hook) |
| `request_hook.jsonl` | all 3 captured outgoing requests (hook-side) |
| `request.provider-run1.jsonl` | raw bodies recorded by the stub provider (cross-check) |
| `request.jsonl` | raw bodies recorded by the stub provider (final run) |
| `stub.out` | stub server log |

Not touched: any tracked repo file, `~/.config/opencode/*`, commits, pushes, installs.
