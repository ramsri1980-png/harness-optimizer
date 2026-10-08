#!/usr/bin/env node
const { execSync, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");
const crypto = require("crypto");
const HOME = os.homedir();
const TOOLS_DIR = path.join(HOME, "developer", "harness-optimizer");
const CONFIG_DIR = path.join(HOME, ".config", "opencode");
const COMMANDS_DIR = path.join(CONFIG_DIR, "commands");
const PLUGINS_DIR = path.join(CONFIG_DIR, "plugins");
const OPENCODE_JSON = path.join(CONFIG_DIR, "opencode.json");
const METRICS_FILE = path.join(CONFIG_DIR, ".harness-token-totals.json");
const BACKUP_DIR = path.join(
  HOME, ".config",
  `opencode-backup-${new Date().toISOString().replace(/[:.]/g, "-")}`
);

const FLAGS = {
  uninstall: process.argv.includes("--uninstall"),
  doctor:    process.argv.includes("--doctor"),
  force:     process.argv.includes("--force"),
  help:      process.argv.includes("--help") || process.argv.includes("-h"),
  noRuntime: process.argv.includes("--no-runtime"),
  withPlugins: process.argv.includes("--with-plugins"),
};
// ── Helpers ──
function sha256(buf) {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

function readMarkerHash(markerPath) {
  if (!fs.existsSync(markerPath)) return null;
  try {
    const raw = fs.readFileSync(markerPath, "utf8").trim();
    const parsed = JSON.parse(raw);
    return parsed.hash || null;
  } catch {
    return null;
  }
}
const COMMANDS_MANIFEST = path.join(CONFIG_DIR, ".harness-commands.json");

function loadCommandsManifest() {
  if (!fs.existsSync(COMMANDS_MANIFEST)) return { files: {} };
  try {
    const parsed = JSON.parse(fs.readFileSync(COMMANDS_MANIFEST, "utf8"));
    return parsed && typeof parsed === "object" && parsed.files ? parsed : { files: {} };
  } catch {
    return { files: {} };
  }
}

function saveCommandsManifest(m) {
  fs.writeFileSync(COMMANDS_MANIFEST, JSON.stringify(m, null, 2) + "\n");
}

const C = {
  g: "\x1b[32m", y: "\x1b[33m", r: "\x1b[31m",
  b: "\x1b[34m", d: "\x1b[2m", n: "\x1b[0m"
};
const info = (m) => console.log(`${C.g}[INFO]${C.n} ${m}`);
const warn = (m) => console.log(`${C.y}[WARN]${C.n} ${m}`);
const fail = (m) => { console.error(`${C.r}[FAIL]${C.n} ${m}`); process.exit(1); };
const head = (m) => console.log(`\n${C.b}── ${m} ──${C.n}`);

if (FLAGS.help) {
  console.log(`
harness-optimizer — one-command installer for the Harness-Optimizer MCP suite

Usage:
  npx harness-optimizer              Install or update
  npx harness-optimizer --doctor     Check installation health
  npx harness-optimizer --uninstall  Remove everything installed by this tool
  npx harness-optimizer --force      Overwrite existing config without merging
  npx harness-optimizer --help       Show this help

Platforms: Linux, macOS, WSL. Native Windows is not supported.
`);
  process.exit(0);
}

if (process.platform === "win32") {
  fail("Native Windows is not supported. Please run inside WSL.\n  Install WSL:  wsl --install");
}

function has(cmd) {
  try { execSync(`command -v ${cmd}`, { stdio: "ignore" }); return true; }
  catch { return false; }
}
function need(cmd, why) {
  if (!has(cmd)) fail(`${cmd} is required${why ? ` (${why})` : ""}`);
}

// ── Uninstall ─────────────────────────────────────────────
if (FLAGS.uninstall) {
  head("Uninstalling Harness-Optimizer");
  // Never rm -rf the whole tools directory — it may be the user's git
  // checkout. Only remove installer-created runtime artifacts.
  const runtimeArtifacts = [
    path.join(TOOLS_DIR, "venv"),
    path.join(TOOLS_DIR, "server.py"),
    path.join(TOOLS_DIR, "harness-config-ui.py"),
  ];
  const isUserCheckout = fs.existsSync(path.join(TOOLS_DIR, ".git"));
  for (const f of runtimeArtifacts) {
    if (fs.existsSync(f)) {
      fs.rmSync(f, { recursive: true, force: true });
      info(`Removed ${f}`);
    }
  }
  if (isUserCheckout) {
    info(`Preserved ${TOOLS_DIR} (git checkout — not deleting repository)`);
  }

  if (fs.existsSync(OPENCODE_JSON)) {
    try {
      const cfg = JSON.parse(fs.readFileSync(OPENCODE_JSON, "utf8"));
      // V2: remove from mcp.servers
      if (cfg.mcp && cfg.mcp.servers && cfg.mcp.servers["harness-tools"]) {
        delete cfg.mcp.servers["harness-tools"];
        info("Removed harness-tools from opencode.json");
      }
      // V1 legacy fallback
      if (cfg.mcp && cfg.mcp["harness-tools"]) {
        delete cfg.mcp["harness-tools"];
        info("Removed legacy harness-tools from opencode.json");
      }
      // Note: do NOT touch cfg.plugins / the "./plugins" registration.
      // It is a directory reference; removing it breaks any user-installed
      // plugin that also lives under ~/.config/opencode/plugins/.
      // We remove only the specific file we own (plugins/harness.ts) and
      // the harness-tools MCP registration above.
      if (Array.isArray(cfg.plugin)) {
        cfg.plugin = cfg.plugin.filter(p =>
          !["opencode-runtime-fallback", "opencode-context-watch",
            "./plugins/token-metrics.ts"].includes(p)
        );
        if (cfg.plugin.length === 0) delete cfg.plugin;
        info("Cleaned up legacy plugin array entries.");
      }
      fs.writeFileSync(OPENCODE_JSON, JSON.stringify(cfg, null, 2) + "\n");
    } catch (e) {
      warn(`Could not parse opencode.json — left untouched: ${e.message}`);
    }
  }

  // Remove harness-* commands only if the user hasn't edited them.
  const uninstallManifest = loadCommandsManifest();
  for (const [cmd, recordedHash] of Object.entries(uninstallManifest.files || {})) {
    const f = path.join(COMMANDS_DIR, cmd);
    if (!fs.existsSync(f)) continue;
    const currentHash = sha256(fs.readFileSync(f));
    if (currentHash === recordedHash) {
      fs.rmSync(f);
      info(`Removed ${f}`);
    } else {
      warn(`${cmd} has local edits — preserved, not removed.`);
    }
  }
  if (fs.existsSync(COMMANDS_MANIFEST)) {
    fs.rmSync(COMMANDS_MANIFEST);
    info("Removed commands manifest.");
  }

  // Other harness-owned files (not tracked by the manifest).
  for (const f of [
    path.join(PLUGINS_DIR, "harness.ts"),
    path.join(CONFIG_DIR, "opencode-fallback.jsonc"),
    path.join(CONFIG_DIR, "opencode-context-watch.json"),
    METRICS_FILE,
  ]) {
    if (fs.existsSync(f)) { fs.rmSync(f); info(`Removed ${f}`); }
  }

  // AGENTS.md uninstall — marker-aware + backup-safe
  //   1. Valid markers in current file → strip section, preserve outside
  //      (if nothing left outside AND a backup exists → restore backup)
  //   2. Backup exists, current file has no valid markers → AMBIGUOUS:
  //      leave both files, warn (do NOT overwrite newer personal content)
  //   3. Legacy ownership marker, no backup → hash check
  //   4. Otherwise → leave alone
  const agentsDst = path.join(CONFIG_DIR, "AGENTS.md");
  const agentsBackup = path.join(CONFIG_DIR, "AGENTS.md.harness-backup");
  const agentsMarker = path.join(CONFIG_DIR, ".harness-owns-agents");
  const AGENTS_START_U = "<!-- HARNESS-OPTIMIZER:START -->";
  const AGENTS_END_U = "<!-- HARNESS-OPTIMIZER:END -->";

  const hasFile = fs.existsSync(agentsDst);
  const hasBackup = fs.existsSync(agentsBackup);
  const hasMarkerFile = fs.existsSync(agentsMarker);
  const content = hasFile ? fs.readFileSync(agentsDst, "utf8") : "";

  function validateMarkersU(text) {
    const starts = [];
    const ends = [];
    let i = 0;
    while ((i = text.indexOf(AGENTS_START_U, i)) !== -1) { starts.push(i); i += AGENTS_START_U.length; }
    i = 0;
    while ((i = text.indexOf(AGENTS_END_U, i)) !== -1) { ends.push(i); i += AGENTS_END_U.length; }
    if (starts.length !== 1 || ends.length !== 1) return { valid: false };
    if (ends[0] < starts[0]) return { valid: false };
    return { valid: true, sIdx: starts[0], eIdx: ends[0] + AGENTS_END_U.length };
  }

  const mv = hasFile ? validateMarkersU(content) : { valid: false };

  if (hasFile && mv.valid) {
    const before = content.slice(0, mv.sIdx).trim();
    const after = content.slice(mv.eIdx).trim();
    const remainder = [before, after].filter(Boolean).join("\n\n");
    if (remainder) {
      fs.writeFileSync(agentsDst, remainder + "\n");
      info("Removed harness section from AGENTS.md (your content preserved).");
    } else if (hasBackup) {
      fs.copyFileSync(agentsBackup, agentsDst);
      fs.rmSync(agentsBackup);
      info("Restored your original AGENTS.md from backup.");
    } else {
      fs.rmSync(agentsDst);
      info("Removed harness-created AGENTS.md.");
    }
  } else if (hasFile && hasBackup) {
    // Current file has no valid markers, but a backup exists. We cannot tell
    // whether the current file is user-edited harness content or newer
    // personal content. Refuse to overwrite either — leave both files.
    warn("AGENTS.md has no valid harness markers but a pre-install backup exists.");
    warn(`  Current:  ${agentsDst}`);
    warn(`  Backup:   ${agentsBackup}`);
    warn("  Leaving both files in place — decide which to keep, then delete the other.");
  } else if (hasFile && hasMarkerFile) {
    const storedHash = readMarkerHash(agentsMarker);
    const currentHash = sha256(content);
    if (storedHash === null || storedHash === currentHash) {
      fs.rmSync(agentsDst);
      info("Removed harness-created AGENTS.md.");
    } else {
      info("AGENTS.md has been edited since install — leaving in place.");
    }
  } else if (hasFile) {
    info("AGENTS.md not owned by harness — left in place.");
  }

  if (hasMarkerFile) fs.rmSync(agentsMarker);
  console.log("");
  info("Uninstall complete.");
  process.exit(0);
}

// ── Doctor ────────────────────────────────────────────────
if (FLAGS.doctor) {
  head("Harness-Optimizer Health Check");
  let issues = 0;
  const check = (label, ok, detail) => {
    const mark = ok ? `${C.g}✓${C.n}` : `${C.r}✗${C.n}`;
    console.log(`  ${mark} ${label}${detail ? `  ${C.d}${detail}${C.n}` : ""}`);
    if (!ok) issues++;
  };

  check("python3 available", has("python3"));
  check("npm available", has("npm"));
  check("git available", has("git"));
  check("opencode available", has("opencode"));

  const serverPy = path.join(TOOLS_DIR, "server.py");
  check("server.py present", fs.existsSync(serverPy), serverPy);

  const venvPy = path.join(TOOLS_DIR, "venv", "bin", "python");
  check("venv python present", fs.existsSync(venvPy), venvPy);

  if (fs.existsSync(venvPy) && fs.existsSync(serverPy)) {
    const res = spawnSync(venvPy, ["-c", `
import asyncio
from fastmcp import Client
async def main():
    async with Client('server.py') as c:
        tools = await c.list_tools()
        print(len(tools))
asyncio.run(main())
`], { cwd: TOOLS_DIR, encoding: "utf8", timeout: 20000 });
    const ok = res.status === 0;
    check("MCP server responds", ok, ok ? `${res.stdout.trim()} tools` : (res.stderr || "").split("\n")[0]);
  }

  check("opencode.json present", fs.existsSync(OPENCODE_JSON));
  if (fs.existsSync(OPENCODE_JSON)) {
    try {
      const cfg = JSON.parse(fs.readFileSync(OPENCODE_JSON, "utf8"));
      check("mcp.harness-tools registered",
        !!(cfg.mcp && cfg.mcp.servers && cfg.mcp.servers["harness-tools"]) ||
        !!(cfg.mcp && cfg.mcp["harness-tools"]));
      check("plugins dir registered in config",
        Array.isArray(cfg.plugins) && cfg.plugins.includes("./plugins"));
    } catch (e) {
      check("opencode.json parses", false, e.message);
    }
  }
  for (const cmd of ["harness-plan.md", "harness-config.md", "harness-rollback-confirm.md", "harness-help.md"]) {
    check(`command: ${cmd}`, fs.existsSync(path.join(COMMANDS_DIR, cmd)));
  }
  check("harness.ts plugin",
    fs.existsSync(path.join(PLUGINS_DIR, "harness.ts")));

  check("config UI present",
    fs.existsSync(path.join(TOOLS_DIR, "harness-config-ui.py")));

  console.log("");
  if (issues === 0) info("All checks passed.");
  else {
    warn(`${issues} issue(s) found.`);
    console.log(`  Repair:  ${C.b}npx harness-optimizer${C.n}`);
  }
  process.exit(issues === 0 ? 0 : 1);
}

// ── Install ───────────────────────────────────────────────
head("Harness-Optimizer Installation");
need("python3", "runs the MCP server");
need("npm", "installs OpenCode plugins");
need("git", "provides version control tools");
need("opencode", "the target harness");

if (fs.existsSync(CONFIG_DIR)) {
  fs.cpSync(CONFIG_DIR, BACKUP_DIR, { recursive: true });
  info(`Backed up config → ${BACKUP_DIR}`);
}

const serverTemplate = path.join(__dirname, "..", "templates", "server.py");
head("Phase: MCP Server");
fs.mkdirSync(TOOLS_DIR, { recursive: true });

// Copy files first — fast, needed by both full and --no-runtime installs,
// and required BEFORE the server test runs (the test loads server.py).
if (!fs.existsSync(serverTemplate)) fail("templates/server.py missing from package");
fs.copyFileSync(serverTemplate, path.join(TOOLS_DIR, "server.py"));
info("Installed server.py");

const uiTemplate = path.join(__dirname, "..", "templates", "harness-config-ui.py");
if (fs.existsSync(uiTemplate)) {
  fs.copyFileSync(uiTemplate, path.join(TOOLS_DIR, "harness-config-ui.py"));
  fs.chmodSync(path.join(TOOLS_DIR, "harness-config-ui.py"), 0o755);
  info("Installed harness-config-ui.py");
} else {
  warn("harness-config-ui.py template missing — config UI will not be installed.");
}

// Runtime phase: venv, pip, and the server smoke test.
if (!FLAGS.noRuntime) {
  const venvPy = path.join(TOOLS_DIR, "venv", "bin", "python");
  const venvPip = path.join(TOOLS_DIR, "venv", "bin", "pip");

  if (!fs.existsSync(venvPy)) {
    info("Creating Python venv...");
    execSync("python3 -m venv venv", { cwd: TOOLS_DIR, stdio: "inherit" });
  }
  info("Ensuring FastMCP is installed...");
  execSync(`${venvPip} install --quiet --upgrade pip`, { cwd: TOOLS_DIR, stdio: "inherit" });
  execSync(`${venvPip} install --quiet fastmcp`, { cwd: TOOLS_DIR, stdio: "inherit" });

  info("Testing server...");
  const test = spawnSync(venvPy, ["-c", `
import asyncio
from fastmcp import Client
async def main():
    async with Client('server.py') as c:
        print(','.join(t.name for t in await c.list_tools()))
asyncio.run(main())
`], { cwd: TOOLS_DIR, encoding: "utf8", timeout: 30000 });
  if (test.status !== 0) fail(`Server test failed:\n${test.stderr}`);
  info(`Server OK — tools: ${test.stdout.trim()}`);
}

head("Phase: OpenCode Configuration");
fs.mkdirSync(COMMANDS_DIR, { recursive: true });
fs.mkdirSync(PLUGINS_DIR, { recursive: true });

let cfg = {};
if (fs.existsSync(OPENCODE_JSON)) {
  try {
    cfg = JSON.parse(fs.readFileSync(OPENCODE_JSON, "utf8"));
    info("Existing opencode.json loaded — merging.");
  } catch (e) {
    warn(`Existing opencode.json invalid: ${e.message}. Backing up.`);
    fs.copyFileSync(OPENCODE_JSON, OPENCODE_JSON + ".broken");
    cfg = {};
  }
}
// Ensure V2 schema
cfg.$schema = cfg.$schema || "https://opencode.ai/config.json";

// ── Plugins: merge, don't clobber ──
// Migrate legacy `plugin` key
if (Array.isArray(cfg.plugin) && !Array.isArray(cfg.plugins)) {
  cfg.plugins = cfg.plugin;
  delete cfg.plugin;
}
cfg.plugins = Array.isArray(cfg.plugins) ? cfg.plugins : [];
if (!cfg.plugins.includes("./plugins")) {
  cfg.plugins.push("./plugins");
}

// ── MCP: V2 shape under mcp.servers ──
cfg.mcp = cfg.mcp || {};
cfg.mcp.servers = cfg.mcp.servers || {};
// Migrate any V1-style entries (mcp["foo"]) into mcp.servers
for (const key of Object.keys(cfg.mcp)) {
  if (key === "servers") continue;
  if (cfg.mcp[key] && typeof cfg.mcp[key] === "object") {
    if (!cfg.mcp.servers[key]) cfg.mcp.servers[key] = cfg.mcp[key];
    delete cfg.mcp[key];
  }
}

// Merge into any existing harness-tools entry — do not blow away user
// choices (environment.HARNESS_TOOLS, disabled state, custom fields).
const existingHarnessTools = cfg.mcp.servers["harness-tools"] || {};
cfg.mcp.servers["harness-tools"] = {
  ...existingHarnessTools,
  type: "local",
  command: [
    path.join(TOOLS_DIR, "venv", "bin", "python"),
    path.join(TOOLS_DIR, "server.py"),
  ],
  // Preserve the user's disabled flag if set; default to false only on first install
  disabled: existingHarnessTools.disabled !== undefined
    ? existingHarnessTools.disabled
    : false,
};

// ── Providers: V2 shape ──
// Migrate V1 `provider` → V2 `providers`
if (cfg.provider && !cfg.providers) {
  cfg.providers = {};
  for (const [name, spec] of Object.entries(cfg.provider)) {
    const p = {};
    if (spec.name) p.name = spec.name;
    const npm = spec.npm || "@ai-sdk/openai-compatible";
    p.package = npm.replace(
      "@ai-sdk/openai-compatible",
      "@opencode/ai/providers/openai-compatible"
    );
    if (spec.options) p.settings = spec.options;
    if (spec.models)  p.models   = spec.models;
    if (spec.env)     p.env      = spec.env;
    cfg.providers[name] = p;
  }
  delete cfg.provider;
}

if (!cfg.providers) {
  cfg.providers = {};
  info("No providers configured — set them via /harness-config or your OpenCode config.");
} else {
  info("Existing providers preserved.");
}

// ── Agents: V2 shape ──
// Migrate V1 `agent` → V2 `agents`
if (cfg.agent && !cfg.agents) {
  cfg.agents = cfg.agent;
  delete cfg.agent;
}


if (!cfg.agents) {
  cfg.agents = {};
  info("No agents configured — set them via /harness-config or your OpenCode config.");
} else {
  info("Existing agents preserved.");
}

fs.writeFileSync(OPENCODE_JSON, JSON.stringify(cfg, null, 2) + "\n");
info("opencode.json updated.");

head("Phase: Plugin Configs");
const fallbackCfgPath = path.join(CONFIG_DIR, "opencode-fallback.jsonc");
if (!fs.existsSync(fallbackCfgPath) || FLAGS.force) {
  fs.writeFileSync(fallbackCfgPath, JSON.stringify({
    enabled: true,
    retry_on_errors: [429, 500, 502, 503, 504],
    max_fallback_attempts: 5,
    cooldown_seconds: 60,
    timeout_seconds: 30,
    notify_on_fallback: true,
  }, null, 2) + "\n");
  info("Wrote opencode-fallback.jsonc");
} else info("opencode-fallback.jsonc preserved.");

const watchCfgPath = path.join(CONFIG_DIR, "opencode-context-watch.json");
if (!fs.existsSync(watchCfgPath) || FLAGS.force) {
  fs.writeFileSync(watchCfgPath, JSON.stringify({
    warnPercent: 0.75,
    warnTokens: 150000,
    rearmPercent: 5,
    postCompactContinue: true,
    message: "[context-watch] Context window at {percent}% ({tokens}/{window} tokens). Wrap up soon and be ready for compaction.",
  }, null, 2) + "\n");
  info("Wrote opencode-context-watch.json");
} else info("opencode-context-watch.json preserved.");

head("Phase: Token-Metrics Plugin");
fs.copyFileSync(
  path.join(__dirname, "..", "templates", "token-metrics.ts"),
  path.join(PLUGINS_DIR, "harness.ts")
);
info("Installed harness.ts");

if (!fs.existsSync(METRICS_FILE)) {
  fs.writeFileSync(METRICS_FILE, JSON.stringify({ totalSaved: 0, calls: 0 }) + "\n");
  info("Initialized persistent token totals.");
}

head("Phase: Custom Commands");
// Warn about legacy 0.3.x command files. We do not delete them — the user
// may have owned some of those names before harness ever existed.
const legacyCommandNames = ["plan.md", "config.md", "rollback-confirm.md", "help-harness.md"];
const legacyPresent = legacyCommandNames
  .map(c => path.join(COMMANDS_DIR, c))
  .filter(f => fs.existsSync(f));
if (legacyPresent.length > 0) {
  warn(`Found ${legacyPresent.length} legacy command file(s) from a pre-0.4.0 install:`);
  for (const f of legacyPresent) warn(`  ${f}`);
  warn("These are no longer used by harness-optimizer. They are preserved (not deleted) because they may contain your own content. You may remove them manually.");
}

const commandsManifest = loadCommandsManifest();
const commandFiles = ["harness-plan.md", "harness-config.md", "harness-rollback-confirm.md", "harness-help.md"];

for (const cmd of commandFiles) {
  const src = path.join(__dirname, "..", "templates", "commands", cmd);
  const dst = path.join(COMMANDS_DIR, cmd);
  if (!fs.existsSync(src)) { warn(`Missing template: ${cmd}`); continue; }

  const templateBuf = fs.readFileSync(src);
  const templateHash = sha256(templateBuf);
  const existingHash = fs.existsSync(dst) ? sha256(fs.readFileSync(dst)) : null;
  const recordedHash = commandsManifest.files[cmd] || null;

  if (existingHash === null) {
    fs.copyFileSync(src, dst);
    commandsManifest.files[cmd] = templateHash;
    info(`Installed ${cmd}`);
  } else if (recordedHash === null) {
    warn(`${cmd} exists and is not harness-owned — preserved, skipped.`);
  } else if (existingHash === recordedHash) {
    if (existingHash === templateHash) {
      info(`${cmd} already up to date.`);
    } else {
      fs.copyFileSync(src, dst);
      commandsManifest.files[cmd] = templateHash;
      info(`Updated ${cmd}`);
    }
  } else {
    warn(`${cmd} has local edits — preserved, not updated. Delete it and reinstall to get the latest version.`);
  }
}

saveCommandsManifest(commandsManifest);

head("Phase: Global AGENTS.md");
const agentsSrc = path.join(__dirname, "..", "templates", "AGENTS.md");
const agentsDst = path.join(CONFIG_DIR, "AGENTS.md");
const agentsBackup = path.join(CONFIG_DIR, "AGENTS.md.harness-backup");
const agentsMarker = path.join(CONFIG_DIR, ".harness-owns-agents");
const AGENTS_START = "<!-- HARNESS-OPTIMIZER:START -->";
const AGENTS_END = "<!-- HARNESS-OPTIMIZER:END -->";
const agentsTemplate = fs.readFileSync(agentsSrc, "utf8");

function writeAgentsMarker() {
  const c = fs.readFileSync(agentsDst);
  fs.writeFileSync(agentsMarker, JSON.stringify({
    hash: sha256(c),
    installedAt: new Date().toISOString(),
  }, null, 2) + "\n");
}

// Validate markers in a text: exactly one START, one END, in order.
function validateAgentsMarkers(text) {
  const starts = [];
  const ends = [];
  let i = 0;
  while ((i = text.indexOf(AGENTS_START, i)) !== -1) { starts.push(i); i += AGENTS_START.length; }
  i = 0;
  while ((i = text.indexOf(AGENTS_END, i)) !== -1) { ends.push(i); i += AGENTS_END.length; }
  if (starts.length === 0 && ends.length === 0) return { valid: false, reason: "no markers" };
  if (starts.length !== 1) return { valid: false, reason: `expected 1 START marker, found ${starts.length}` };
  if (ends.length !== 1) return { valid: false, reason: `expected 1 END marker, found ${ends.length}` };
  if (ends[0] < starts[0]) return { valid: false, reason: "END marker before START marker" };
  return { valid: true, sIdx: starts[0], eIdx: ends[0] + AGENTS_END.length };
}

// Template must be valid
const tplV = validateAgentsMarkers(agentsTemplate);
if (!tplV.valid) fail(`templates/AGENTS.md is malformed: ${tplV.reason}`);

if (!fs.existsSync(agentsDst)) {
  fs.copyFileSync(agentsSrc, agentsDst);
  writeAgentsMarker();
  info("Installed global AGENTS.md (harness-owned, managed section).");
} else {
  const existing = fs.readFileSync(agentsDst, "utf8");
  const ev = validateAgentsMarkers(existing);

  if (ev.valid) {
    const newSection = agentsTemplate.slice(tplV.sIdx, tplV.eIdx);
    const merged = existing.slice(0, ev.sIdx) + newSection + existing.slice(ev.eIdx);
    if (merged === existing) {
      info("AGENTS.md managed section already up to date.");
    } else {
      fs.writeFileSync(agentsDst, merged);
      info("Updated harness section in AGENTS.md (user content outside markers preserved).");
    }
    writeAgentsMarker();
  } else if (existing.includes(AGENTS_START) || existing.includes(AGENTS_END)) {
    warn(`AGENTS.md has malformed harness markers (${ev.reason}) — file left unchanged. Fix or remove the markers, or use --force to overwrite.`);
  } else if (FLAGS.force) {
    if (!fs.existsSync(agentsBackup)) {
      fs.copyFileSync(agentsDst, agentsBackup);
      info("Backed up existing AGENTS.md → AGENTS.md.harness-backup");
    }
    fs.copyFileSync(agentsSrc, agentsDst);
    writeAgentsMarker();
    info("Overwrote global AGENTS.md (backup saved, harness-owned).");
  } else {
    warn("AGENTS.md exists without harness markers — preserved. Run with --force to overwrite, or wrap harness content between <!-- HARNESS-OPTIMIZER:START --> and <!-- HARNESS-OPTIMIZER:END --> for managed updates.");
  }
}

if (!FLAGS.noRuntime && FLAGS.withPlugins) {
head("Phase: OpenCode Plugins via npm");
for (const pkg of ["opencode-runtime-fallback", "opencode-context-watch", "opencode-studio-server"]) {
  info(`Installing ${pkg}...`);
  const r = spawnSync("npm", ["install", "-g", pkg], { stdio: "inherit" });
  if (r.status !== 0) warn(`${pkg} install failed — install manually later.`);
}
}
head("Phase: Documentation");
const docsSrc = path.join(__dirname, "..", "docs");
const docsDst = path.join(TOOLS_DIR, "docs");
fs.mkdirSync(docsDst, { recursive: true });
if (fs.existsSync(docsSrc)) {
  const realSrc = fs.realpathSync(docsSrc);
  const realDst = fs.realpathSync(docsDst);
  if (realSrc === realDst) {
    info(`Docs already in place → ${docsDst} (skipped copy)`);
  } else {
    fs.cpSync(docsSrc, docsDst, { recursive: true });
    info(`Docs installed → ${docsDst}`);
  }
} else {
  warn("docs/ source missing — documentation will not be installed.");
}
const studioSrc = path.join(__dirname, "..", "studio_extensions");
const studioDst = path.join(TOOLS_DIR, "studio_extensions");
if (fs.existsSync(studioSrc)) {
  fs.mkdirSync(studioDst, { recursive: true });
  const realSrc = fs.realpathSync(studioSrc);
  const realDst = fs.realpathSync(studioDst);
  if (realSrc === realDst) {
    info("Studio extension already in place (skipped copy)");
  } else {
    fs.cpSync(studioSrc, studioDst, { recursive: true });
    info("Studio extension written.");
  }
} else {
  warn("studio_extensions/ source missing — studio UI will not be installed.");
}

console.log("");
console.log("════════════════════════════════════════════════════");
console.log("  INSTALLATION COMPLETE");
console.log("════════════════════════════════════════════════════");
console.log(`  Server:   ${path.join(TOOLS_DIR, "server.py")}`);
console.log(`  Config:   ${OPENCODE_JSON}`);
console.log(`  Backup:   ${BACKUP_DIR}`);
console.log(`  Docs:     ${docsDst}`);
console.log("");
console.log("Next steps:");
console.log("  1. Configure providers and agents via /harness-config (or your OpenCode config)");
console.log("  2. opencode");
console.log("  3. Ask: 'List your available tools'");
console.log("  4. Type: /harness-help");
console.log("");
console.log("Health check:  npx harness-optimizer --doctor");
console.log("Uninstall:     npx harness-optimizer --uninstall");
