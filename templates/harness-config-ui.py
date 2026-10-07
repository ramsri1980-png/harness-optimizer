#!/usr/bin/env python3
"""Harness-Optimizer Configuration UI.

Local web UI for OpenCode agent models, fallback chains, MCP tool toggles,
and config backups. Standard library only — no npm, no Node, no dependencies.
"""
import argparse
import json
import shutil
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

CONFIG_DIR = Path.home() / ".config" / "opencode"
OPENCODE_JSON = CONFIG_DIR / "opencode.json"
TOTALS_PATH = CONFIG_DIR / ".harness-token-totals.json"
BACKUP_PREFIX = "opencode-backup-"

HARNESS_TOOLS = [
    "get_repo_skeleton",
    "rip_file_lines",
    "find_dependent_references",
    "apply_search_replace",
    "lint_file",
    "git_checkpoint",
    "rollback_show",
    "execute_and_capture",
    "inspect_database_schema",
]

DEFAULT_PORT = 8765


def load_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def atomic_write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def list_backups():
    if not CONFIG_DIR.exists():
        return []
    dirs = [p.name for p in CONFIG_DIR.glob(BACKUP_PREFIX + "*") if p.is_dir()]
    return sorted(dirs, reverse=True)


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Harness-Optimizer Config</title>
<style>
:root{--bg:#0d1117;--fg:#e6edf3;--muted:#7d8590;--border:#30363d;--accent:#58a6ff;--err:#f85149;--ok:#3fb950;--free:#3fb950}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--fg);font-size:14px;line-height:1.5}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:32px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}
.sub{color:var(--muted);font-size:13px;margin-bottom:12px}
.card{background:#161b22;border:1px solid var(--border);border-radius:6px;padding:16px;margin-bottom:10px}
.agent{border-left:3px solid var(--accent)}
.agent-name{font-weight:600;font-size:14px;margin-bottom:10px}
.row{display:flex;gap:8px;align-items:center;margin-bottom:6px}
.row label{min-width:80px;color:var(--muted);font-size:12px;flex:none}
input,select{background:#0d1117;border:1px solid var(--border);color:var(--fg);border-radius:4px;padding:6px 10px;font-family:inherit;font-size:13px;flex:1;min-width:0}
select{cursor:pointer}
input:focus,select:focus{outline:none;border-color:var(--accent)}
input.is-free{border-color:#23863680;color:var(--free)}
.dd{position:relative;flex:1;min-width:0}
.dd-input{width:100%;padding-right:32px;cursor:pointer}
.dd-input.is-free{border-color:#23863680;color:var(--free)}
.dd-arrow{position:absolute;right:8px;top:50%;transform:translateY(-50%);color:var(--accent);font-size:11px;pointer-events:none;font-weight:700;text-shadow:0 0 1px var(--accent)}
.dd-panel{position:absolute;top:100%;left:0;right:0;margin-top:4px;background:#0d1117;border:1px solid var(--accent);border-radius:4px;max-height:320px;overflow:hidden;display:none;z-index:50;box-shadow:0 8px 24px rgba(0,0,0,.5)}
.dd-panel.open{display:flex;flex-direction:column}
.dd-search{padding:8px;border-bottom:1px solid var(--border)}
.dd-search input{width:100%;padding:6px 10px;font-size:12px}
.dd-list{overflow-y:auto;max-height:260px}
.dd-item{padding:6px 10px;font-size:12px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:ui-monospace,Menlo,Consolas,monospace;display:flex;justify-content:space-between;gap:8px}
.dd-item:hover,.dd-item.active{background:#161b22}
.dd-item.free{color:var(--free)}
.dd-item .tag{font-size:10px;color:var(--free);font-weight:700;flex:none}
.dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
button{background:#21262d;border:1px solid var(--border);color:var(--fg);border-radius:4px;padding:6px 12px;font-family:inherit;font-size:12px;cursor:pointer}
button:hover{background:#30363d}
button.primary{background:var(--accent);border-color:var(--accent);color:#000;font-weight:600}
button.danger{background:transparent;border-color:var(--err);color:var(--err);padding:6px 9px}
.tools{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px}
.tool{display:flex;align-items:center;gap:8px;padding:8px 10px;background:#0d1117;border:1px solid var(--border);border-radius:4px;cursor:pointer;font-size:13px;user-select:none}
.tool input{flex:none;min-width:auto;width:16px;height:16px;cursor:pointer}
.fb{display:flex;gap:6px;align-items:center;margin-bottom:4px}
.fb label{min-width:80px;color:var(--muted);font-size:12px;flex:none}
.fb input{flex:1;min-width:0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px;border-bottom:1px solid var(--border)}
th{color:var(--muted);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.actions{position:sticky;bottom:0;background:var(--bg);border-top:1px solid var(--border);padding:16px 0;margin-top:24px;display:flex;gap:8px;align-items:center}
.toast{position:fixed;top:20px;right:20px;padding:12px 20px;border-radius:6px;font-weight:500;z-index:100;opacity:0;transition:opacity .2s;pointer-events:none}
.toast.show{opacity:1}
.toast.ok{background:#238636;color:#fff}
.toast.err{background:#da3633;color:#fff}
.stat{display:inline-block;margin-right:32px}
.stat-num{font-size:26px;font-weight:600;color:var(--accent);line-height:1.2}
.stat-lbl{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
.hint{color:var(--muted);font-size:12px;margin-top:8px}
.legend{color:var(--muted);font-size:12px;margin-top:8px}
.legend .free{color:var(--free);font-weight:600}
</style>
</head>
<body>
<div class="wrap">
<h1>Harness-Optimizer Config</h1>
<div class="sub" id="configPath">loading…</div>

<h2>Token Savings</h2>
<div class="card">
  <div class="stat"><div class="stat-num" id="statCalls">0</div><div class="stat-lbl">Tool Calls</div></div>
  <div class="stat"><div class="stat-num" id="statSaved">0</div><div class="stat-lbl">Tokens Saved</div></div>
  <div class="hint">Cumulative since install. Reset by deleting ~/.config/opencode/.harness-token-totals.json</div>
</div>

<h2>Model Priority</h2>
<div class="sub">Primary model is tried first. Fallbacks fire in order on rate-limit or provider errors.</div>
<div class="legend">Models ending in <code>:free</code> or <code>-free</code> appear in <span class="free">green</span> and are marked <b>FREE</b> in the dropdown.</div>
<div id="agents" style="margin-top:12px"></div>

<h2>MCP Tools</h2>
<div class="sub">Uncheck to hide tools from the model (saves context tokens). Restart OpenCode to apply.</div>
<div class="tools" id="tools"></div>

<h2>Backups</h2>
<div class="sub">Automatic snapshot created before every save.</div>
<div class="card">
  <button id="btnBackup">+ Create Backup Now</button>
  <table id="backups" style="margin-top:12px">
    <thead><tr><th>Name</th><th style="width:100px">Actions</th></tr></thead>
    <tbody></tbody>
  </table>
</div>

<div class="actions">
  <button class="primary" id="btnSave">Save All Changes</button>
  <button id="btnReload">Reload from Disk</button>
  <span id="status" style="margin-left:auto;color:var(--muted);font-size:12px"></span>
</div>
</div>
<div class="toast" id="toast"></div>
<datalist id="model-datalist"></datalist>

<script>
let STATE = null;

async function api(path, opts) {
  const r = await fetch(path, opts);
  const txt = await r.text();
  if (!r.ok) throw new Error(txt || r.statusText);
  return txt ? JSON.parse(txt) : {};
}

function toast(msg, ok = true) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + (ok ? 'ok' : 'err') + ' show';
  setTimeout(() => { t.className = 'toast'; }, 2600);
}

function isFree(model) {
  if (!model) return false;
  const m = String(model).toLowerCase().trim();
  return m.endsWith(':free') || m.endsWith('-free');
}

function refreshFreeStyle(input) {
  input.classList.toggle('is-free', isFree(input.value));
}

function populateDatalist() {
  const dl = document.getElementById('model-datalist');
  dl.innerHTML = '';
  const models = (STATE.models && STATE.models.length) ? STATE.models : [];
  if (models.length === 0) return;
  for (const m of models) {
    const opt = document.createElement('option');
    opt.value = m;
    if (isFree(m)) opt.label = '💚 FREE';
    dl.appendChild(opt);
  }
}

function setModelInput(input, value) {
  // Legacy helper kept for compatibility; new code uses makeDropdown()
  input.value = value || '';
  refreshFreeStyle(input);
}

// Build a searchable dropdown replacing a plain <input>
function makeDropdown(input, initialValue, onChange) {
  input.classList.remove('dd-input');
  const wrapper = document.createElement('div');
  wrapper.className = 'dd';
  input.parentNode.insertBefore(wrapper, input);
  wrapper.appendChild(input);
  input.classList.add('dd-input');
  input.setAttribute('autocomplete', 'off');
  input.setAttribute('spellcheck', 'false');
  input.value = initialValue || '';
  refreshFreeStyle(input);

  const arrow = document.createElement('span');
  arrow.className = 'dd-arrow';
  arrow.textContent = '▼';
  wrapper.appendChild(arrow);

  const panel = document.createElement('div');
  panel.className = 'dd-panel';
  panel.innerHTML =
    '<div class="dd-search"><input type="text" placeholder="Search models…"></div>' +
    '<div class="dd-list"></div>';
  wrapper.appendChild(panel);

  const searchInput = panel.querySelector('.dd-search input');
  const list = panel.querySelector('.dd-list');

  function renderList(filter) {
    const all = (STATE.models && STATE.models.length) ? STATE.models : [];
    const f = (filter || '').toLowerCase();
    const shown = f ? all.filter(m => m.toLowerCase().includes(f)) : all;
    list.innerHTML = '';
    if (shown.length === 0) {
      list.innerHTML = '<div class="dd-empty">' +
        (all.length ? 'No models match' : 'No models loaded — check opencode on PATH') +
        '</div>';
      return;
    }
    for (const m of shown) {
      const item = document.createElement('div');
      item.className = 'dd-item' + (isFree(m) ? ' free' : '');
      item.innerHTML = '<span>' + m + '</span>' +
        (isFree(m) ? '<span class="tag">FREE</span>' : '');
      item.onclick = () => {
        input.value = m;
        refreshFreeStyle(input);
        close();
        if (onChange) onChange(m);
      };
      list.appendChild(item);
    }
  }

  function open() {
    document.querySelectorAll('.dd-panel.open').forEach(p => {
      if (p !== panel) p.classList.remove('open');
    });
    panel.classList.add('open');
    searchInput.value = '';
    renderList('');
    setTimeout(() => searchInput.focus(), 10);
  }

  function close() {
    panel.classList.remove('open');
  }

  input.addEventListener('focus', open);
  input.addEventListener('click', open);
  searchInput.addEventListener('input', () => renderList(searchInput.value));
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { close(); input.focus(); }
    if (e.key === 'Enter') {
      const first = list.querySelector('.dd-item');
      if (first) first.click();
    }
  });

  document.addEventListener('click', (e) => {
    if (!wrapper.contains(e.target)) close();
  });

  return { input, open, close };
}

async function loadState() {
  try {
    STATE = await api('/api/state');
    document.getElementById('configPath').textContent = 'Config: ' + STATE.configPath;
    document.getElementById('status').textContent = 'Loading models…';

    try {
      const md = await api('/api/models');
      STATE.models = md.models || [];
      if (md.error) {
        document.getElementById('status').textContent = 'Model list unavailable: ' + md.error;
      } else {
        const freeCount = STATE.models.filter(isFree).length;
        document.getElementById('status').textContent =
          STATE.models.length + ' models loaded' +
          (freeCount ? ' · ' + freeCount + ' free' : '');
      }
    } catch (e) {
      STATE.models = [];
      document.getElementById('status').textContent = 'Model list fetch failed';
    }

    renderAgents();
    renderTools();
    renderBackups();
    renderTotals();
  } catch (e) {
    toast('Load failed: ' + e.message, false);
  }
}

function renderTotals() {
  document.getElementById('statCalls').textContent = (STATE.totals.calls || 0).toLocaleString();
  document.getElementById('statSaved').textContent = (STATE.totals.totalSaved || 0).toLocaleString();
}

function renderAgents() {
  const c = document.getElementById('agents');
  c.innerHTML = '';
  const names = Object.keys(STATE.agents);
  if (names.length === 0) {
    c.innerHTML = '<div class="card" style="color:var(--muted)">No agents configured. Run the installer to write defaults.</div>';
    return;
  }
  for (const [name, agent] of Object.entries(STATE.agents)) {
    const div = document.createElement('div');
    div.className = 'card agent';
    div.dataset.agent = name;
    div.innerHTML =
      '<div class="agent-name">' + name + '</div>' +
      '<div class="row"><label>Model</label><input class="agent-model" placeholder="Click to select…"></div>' +
      '<div class="agent-fallbacks"></div>' +
      '<button class="btn-add-fb" style="margin-top:6px">+ Add Fallback</button>';
    makeDropdown(div.querySelector('.agent-model'), agent.model || '');
    const fbBox = div.querySelector('.agent-fallbacks');
    (agent.fallback_models || []).forEach(m => addFbRow(fbBox, m));
    div.querySelector('.btn-add-fb').onclick = () => addFbRow(fbBox, '');
    c.appendChild(div);
  }
}

function addFbRow(container, value) {
  const row = document.createElement('div');
  row.className = 'fb';
  row.innerHTML =
    '<label>Fallback</label>' +
    '<input class="fb-input" placeholder="Click to select…">' +
    '<button class="btn-up" title="Move up">↑</button>' +
    '<button class="btn-down" title="Move down">↓</button>' +
    '<button class="danger btn-del" title="Remove">×</button>';
  makeDropdown(row.querySelector('.fb-input'), value);
  row.querySelector('.btn-up').onclick = () => {
    const prev = row.previousElementSibling;
    if (prev && prev.classList.contains('fb')) container.insertBefore(row, prev);
  };
  row.querySelector('.btn-down').onclick = () => {
    const next = row.nextElementSibling;
    if (next && next.classList.contains('fb')) container.insertBefore(next, row);
  };
  row.querySelector('.btn-del').onclick = () => row.remove();
  container.appendChild(row);
}

function renderTools() {
  const c = document.getElementById('tools');
  c.innerHTML = '';
  const on = new Set(STATE.mcpToolsEnabled);
  for (const tool of STATE.allTools) {
    const lbl = document.createElement('label');
    lbl.className = 'tool';
    lbl.innerHTML = '<input type="checkbox" value="' + tool + '"><span>' + tool + '</span>';
    lbl.querySelector('input').checked = on.has(tool);
    c.appendChild(lbl);
  }
}

async function renderBackups() {
  const list = await api('/api/backups');
  const tb = document.querySelector('#backups tbody');
  tb.innerHTML = '';
  if (!list.length) {
    tb.innerHTML = '<tr><td colspan="2" style="color:var(--muted)">No backups yet</td></tr>';
    return;
  }
  for (const name of list) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td>' + name + '</td><td><button class="btn-restore">Restore</button></td>';
    tr.querySelector('.btn-restore').onclick = async () => {
      if (!confirm('Restore ' + name + '? Current config will be overwritten.')) return;
      try {
        await api('/api/restore', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name})
        });
        toast('Restored from ' + name);
        await loadState();
      } catch (e) { toast('Restore failed: ' + e.message, false); }
    };
    tb.appendChild(tr);
  }
}

document.getElementById('btnSave').onclick = async () => {
  try {
    const agents = {};
    document.querySelectorAll('.card.agent').forEach(card => {
      const name = card.dataset.agent;
      const model = card.querySelector('.agent-model').value.trim();
      const fallbacks = [...card.querySelectorAll('.agent-fallbacks .fb .fb-input')]
        .map(i => i.value.trim()).filter(Boolean);
      agents[name] = {model, fallback_models: fallbacks};
    });
    const tools = [...document.querySelectorAll('.tools input:checked')].map(x => x.value);
    await api('/api/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({agents, tools})
    });
    toast('Saved. Restart OpenCode to apply.');
    await loadState();
  } catch (e) { toast('Save failed: ' + e.message, false); }
};

document.getElementById('btnReload').onclick = loadState;
document.getElementById('btnBackup').onclick = async () => {
  try {
    await api('/api/backup', {method: 'POST'});
    toast('Backup created');
    await renderBackups();
  } catch (e) { toast('Backup failed: ' + e.message, false); }
};

loadState();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _err(self, msg, code=400):
        self._json({"error": msg}, code)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, HTML.encode(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(self._state())
        elif path == "/api/backups":
            self._json(list_backups())
        elif path == "/api/models":
            self._json(self._models())
        else:
            self._err("not found", 404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        try:
            if path == "/api/save":
                self._json(self._save(body))
            elif path == "/api/backup":
                self._json(self._backup())
            elif path == "/api/restore":
                self._json(self._restore(body.get("name", "")))
            else:
                self._err("not found", 404)
        except Exception as e:
            self._err(str(e), 500)


    def _models(self):
        """Return the model list from `opencode models`. Cached 60s per server."""
        import subprocess
        import time
        cache = getattr(self.server, "_models_cache", None)
        if cache and time.time() - cache["at"] < 60:
            return cache["data"]
        try:
            candidates = [
                str(Path.home() / ".opencode" / "bin" / "opencode"),
                "opencode",
            ]
            binary = None
            for c in candidates:
                if c == "opencode" or Path(c).exists():
                    binary = c
                    break

            r = subprocess.run(
                [binary, "models"],
                capture_output=True, text=True, timeout=15, shell=False,
            )
            if r.returncode != 0:
                data = {"models": [], "error": (r.stderr or r.stdout or "").strip()[:300]}
            else:
                lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
                models = [ln for ln in lines if "/" in ln and not ln.startswith("#")]
                data = {"models": sorted(set(models))}
                if not models:
                    data["error"] = (
                        "opencode returned no models. "
                        "Raw output (first 200 chars): " + r.stdout[:200]
                    )
        except FileNotFoundError:
            data = {"models": [], "error": "opencode not found on PATH"}
        except subprocess.TimeoutExpired:
            data = {"models": [], "error": "opencode models timed out"}
        except Exception as e:
            data = {"models": [], "error": str(e)[:300]}
        self.server._models_cache = {"at": time.time(), "data": data}
        return data
    def _state(self):
        cfg = load_json(OPENCODE_JSON, {})
        totals = load_json(TOTALS_PATH, {"calls": 0, "totalSaved": 0})

        # V2 first, V1 fallback
        agents_cfg = cfg.get("agents") or cfg.get("agent") or {}
        agents = {}
        for name, spec in agents_cfg.items():
            if not isinstance(spec, dict):
                continue
            agents[name] = {
                "model": spec.get("model", ""),
                "fallback_models": list(spec.get("fallback_models") or []),
            }

        # V2 mcp.servers["harness-tools"] first, then V1 mcp["harness-tools"]
        mcp = cfg.get("mcp") or {}
        servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else {}
        htools = servers.get("harness-tools") or mcp.get("harness-tools") or {}
        env = htools.get("environment") or {}

        cur = str(env.get("HARNESS_TOOLS", "all")).strip().lower()
        if cur == "all":
            enabled = list(HARNESS_TOOLS)
        elif cur in ("", "none"):
            enabled = []
        else:
            enabled = [t.strip() for t in cur.split(",") if t.strip()]

        return {
            "configPath": str(OPENCODE_JSON),
            "agents": agents,
            "allTools": HARNESS_TOOLS,
            "mcpToolsEnabled": enabled,
            "totals": totals,
        }

    def _save(self, body):
        cfg = load_json(OPENCODE_JSON, {})

        # Migrate V1 agent -> V2 agents
        if cfg.get("agent") and not cfg.get("agents"):
            cfg["agents"] = cfg.pop("agent")

        cfg.setdefault("agents", {})
        for name, spec in (body.get("agents") or {}).items():
            cfg["agents"].setdefault(name, {})
            if spec.get("model"):
                cfg["agents"][name]["model"] = spec["model"]
            cfg["agents"][name]["fallback_models"] = list(spec.get("fallback_models") or [])

        # Migrate V1 mcp["harness-tools"] -> V2 mcp.servers["harness-tools"]
        cfg.setdefault("mcp", {})
        cfg["mcp"].setdefault("servers", {})
        legacy = cfg["mcp"].pop("harness-tools", None)
        if legacy and "harness-tools" not in cfg["mcp"]["servers"]:
            cfg["mcp"]["servers"]["harness-tools"] = legacy

        htools = cfg["mcp"]["servers"].setdefault("harness-tools", {})
        htools.setdefault("environment", {})

        tools = list(body.get("tools") or [])
        if set(tools) == set(HARNESS_TOOLS):
            tools_str = "all"
        elif not tools:
            tools_str = "none"
        else:
            tools_str = ",".join(tools)
        htools["environment"]["HARNESS_TOOLS"] = tools_str

        self._backup()
        atomic_write(OPENCODE_JSON, cfg)
        return {"ok": True}

    def _backup(self):
        if not OPENCODE_JSON.exists():
            raise RuntimeError("opencode.json not found")
        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        name = BACKUP_PREFIX + ts
        dest = CONFIG_DIR / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OPENCODE_JSON, dest / "opencode.json")
        return {"ok": True, "name": name}

    def _restore(self, name):
        if not name or "/" in name or ".." in name or not name.startswith(BACKUP_PREFIX):
            raise ValueError("invalid backup name")
        src = CONFIG_DIR / name / "opencode.json"
        if not src.exists():
            raise FileNotFoundError("backup not found")
        shutil.copy2(src, OPENCODE_JSON)
        return {"ok": True}


def main():
    ap = argparse.ArgumentParser(description="Harness-Optimizer Config UI")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-open", action="store_true", help="don't auto-open browser")
    args = ap.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"Harness-Optimizer Config UI: {url}")
    print(f"Config: {OPENCODE_JSON}")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
