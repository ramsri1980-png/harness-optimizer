#!/usr/bin/env node
const { execSync, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

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
};

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
  if (fs.existsSync(TOOLS_DIR)) {
    fs.rmSync(TOOLS_DIR, { recursive: true, force: true });
    info(`Removed ${TOOLS_DIR}`);
  } else {
    info("Tools directory already absent.");
  }
  if (fs.existsSync(OPENCODE_JSON)) {
    try {
      const cfg = JSON.parse(fs.readFileSync(OPENCODE_JSON, "utf8"));
      if (cfg.mcp && cfg.mcp["harness-tools"]) {
        delete cfg.mcp["harness-tools"];
        info("Removed harness-tools from opencode.json");
      }
      if (Array.isArray(cfg.plugins)) {
        cfg.plugins = cfg.plugins.filter(p => p !== "./plugins");
        if (cfg.plugins.length === 0) delete cfg.plugins;
        info("Removed plugin entries from plugins array.");
      }
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
  for (const f of [
    path.join(PLUGINS_DIR, "harness.ts"),
    path.join(COMMANDS_DIR, "plan.md"),
    path.join(COMMANDS_DIR, "config.md"),
    path.join(COMMANDS_DIR, "rollback-confirm.md"),
    path.join(COMMANDS_DIR, "help-harness.md"),
    path.join(CONFIG_DIR, "opencode-fallback.jsonc"),
    path.join(CONFIG_DIR, "opencode-context-watch.json"),
    path.join(CONFIG_DIR, "AGENTS.md"),
    METRICS_FILE,
  ]) {
    if (fs.existsSync(f)) { fs.rmSync(f); info(`Removed ${f}`); }
  }
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
        !!(cfg.mcp && cfg.mcp["harness-tools"]));
      check("plugins dir registered in config",
        Array.isArray(cfg.plugins) && cfg.plugins.includes("./plugins"));
    } catch (e) {
      check("opencode.json parses", false, e.message);
    }
  }
  for (const cmd of ["plan.md", "config.md", "rollback-confirm.md", "help-harness.md"]) {
    check(`command: ${cmd}`, fs.existsSync(path.join(COMMANDS_DIR, cmd)));
  }
  check("harness.ts plugin",
    fs.existsSync(path.join(PLUGINS_DIR, "harness.ts")));

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

head("Phase: MCP Server");
fs.mkdirSync(TOOLS_DIR, { recursive: true });

const venvPy = path.join(TOOLS_DIR, "venv", "bin", "python");
const venvPip = path.join(TOOLS_DIR, "venv", "bin", "pip");

if (!fs.existsSync(venvPy)) {
  info("Creating Python venv...");
  execSync("python3 -m venv venv", { cwd: TOOLS_DIR, stdio: "inherit" });
}
info("Ensuring FastMCP is installed...");
execSync(`${venvPip} install --quiet --upgrade pip`, { cwd: TOOLS_DIR, stdio: "inherit" });
execSync(`${venvPip} install --quiet fastmcp`, { cwd: TOOLS_DIR, stdio: "inherit" });

const serverTemplate = path.join(__dirname, "..", "templates", "server.py");
if (!fs.existsSync(serverTemplate)) fail("templates/server.py missing from package");
fs.copyFileSync(serverTemplate, path.join(TOOLS_DIR, "server.py"));
info("Installed server.py");

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

head("Phase: OpenCode Configuration");
fs.mkdirSync(COMMANDS_DIR, { recursive: true });
fs.mkdirSync(PLUGINS_DIR, { recursive: true });

let cfg = {};
if (fs.existsSync(OPENCODE_JSON) && !FLAGS.force) {
  try {
    cfg = JSON.parse(fs.readFileSync(OPENCODE_JSON, "utf8"));
    info("Existing opencode.json loaded — merging.");
  } catch (e) {
    warn(`Existing opencode.json invalid: ${e.message}. Backing up.`);
    fs.copyFileSync(OPENCODE_JSON, OPENCODE_JSON + ".broken");
    cfg = {};
  }
}

cfg.$schema = cfg.$schema || "https://opencode.ai/config.json";
cfg.plugins = ["./plugins"];
delete cfg.plugin;
cfg.mcp = cfg.mcp || {};
cfg.mcp["harness-tools"] = {
  type: "local",
  command: [
    path.join(TOOLS_DIR, "venv", "bin", "python"),
    path.join(TOOLS_DIR, "server.py"),
  ],
  enabled: true,
};

if (!cfg.provider) {
  cfg.provider = {
    openrouter: {
      npm: "@ai-sdk/openai-compatible",
      options: { baseURL: "https://openrouter.ai/api/v1" },
    },
    "orca-router": {
      npm: "@ai-sdk/openai-compatible",
      options: { baseURL: "https://orcarouter.xyz/api/v1" },
    },
    "nvda-gate": {
      npm: "@ai-sdk/openai-compatible",
      options: { baseURL: "https://nvidia.com/api/v1" },
    },
  };
  info("Wrote default providers (none existed).");
} else {
  info("Existing providers preserved.");
}

if (!cfg.agent) {
  cfg.agent = {
    primary: {
      description: "Primary coding agent using Harness-Optimizer tools.",
      model: "openrouter/deepseek/deepseek-chat",
      fallback_models: ["orca-router/glm-4", "nvda-gate/thm/glm-4-plus"],
    },
    "initial-reviewer": {
      description: "Read-only code review.",
      model: "nvda-gate/thm/glm-4-plus",
      fallback_models: ["orca-router/glm-4"],
    },
    "premier-reviewer": {
      description: "High-context compliance audit.",
      model: "openrouter/anthropic/claude-3-5-sonnet",
    },
    tester: {
      description: "Runs tests and validates code safety gates.",
      model: "orca-router/glm-4-flash",
    },
  };
  info("Wrote default agents (none existed).");
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
for (const cmd of ["plan.md", "config.md", "rollback-confirm.md", "help-harness.md"]) {
  const src = path.join(__dirname, "..", "templates", "commands", cmd);
  if (!fs.existsSync(src)) { warn(`Missing template: ${cmd}`); continue; }
  fs.copyFileSync(src, path.join(COMMANDS_DIR, cmd));
  info(`Installed ${cmd}`);
}

head("Phase: Global AGENTS.md");
const agentsSrc = path.join(__dirname, "..", "templates", "AGENTS.md");
const agentsDst = path.join(CONFIG_DIR, "AGENTS.md");
if (!fs.existsSync(agentsDst) || FLAGS.force) {
  fs.copyFileSync(agentsSrc, agentsDst);
  info("Installed global AGENTS.md");
} else warn("AGENTS.md exists — preserved. Delete it and re-run to replace.");

head("Phase: OpenCode Plugins via npm");
for (const pkg of ["opencode-runtime-fallback", "opencode-context-watch", "opencode-studio-server"]) {
  info(`Installing ${pkg}...`);
  const r = spawnSync("npm", ["install", "-g", pkg], { stdio: "inherit" });
  if (r.status !== 0) warn(`${pkg} install failed — install manually later.`);
}

head("Phase: Documentation");
const docsSrc = path.join(__dirname, "..", "docs");
const docsDst = path.join(TOOLS_DIR, "docs");
fs.mkdirSync(docsDst, { recursive: true });
if (fs.existsSync(docsSrc)) {
  fs.cpSync(docsSrc, docsDst, { recursive: true });
  info(`Docs installed → ${docsDst}`);
}

const studioSrc = path.join(__dirname, "..", "studio_extensions");
const studioDst = path.join(TOOLS_DIR, "studio_extensions");
if (fs.existsSync(studioSrc)) {
  fs.mkdirSync(studioDst, { recursive: true });
  fs.cpSync(studioSrc, studioDst, { recursive: true });
  info("Studio extension written.");
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
console.log("  1. export OPENROUTER_API_KEY=...");
console.log("  2. opencode");
console.log("  3. Ask: 'List your available tools'");
console.log("  4. Type: /help-harness");
console.log("");
console.log("Health check:  npx harness-optimizer --doctor");
console.log("Uninstall:     npx harness-optimizer --uninstall");
