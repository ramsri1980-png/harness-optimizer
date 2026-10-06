"""Status card for OpenCode Studio — mount at /harness-status."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path
import json

router = APIRouter()

CONFIG_DIR = Path.home() / ".config" / "opencode"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


@router.get("/harness-status", response_class=HTMLResponse)
def status_page():
    cfg = _read_json(CONFIG_DIR / "opencode.json") or {}
    mcp = cfg.get("mcp", {}).get("harness-tools", {})
    plugins = cfg.get("plugin", [])
    agents = cfg.get("agent", {})

    rows = ["<h2>Harness-Optimizer Status</h2>"]
    rows.append("<h3>MCP Server</h3>")
    if mcp:
        enabled = mcp.get("enabled", True)
        rows.append(f"<p>Registered: <b>yes</b> · Enabled: <b>{enabled}</b></p>")
        cmd = mcp.get("command", [])
        rows.append(f"<p><code>{' '.join(cmd) if cmd else '(no command)'}</code></p>")
    else:
        rows.append("<p style='color:red'>Not registered.</p>")

    rows.append("<h3>Plugins</h3><ul>")
    for p in plugins:
        rows.append(f"<li>{p}</li>")
    rows.append("</ul>")

    rows.append("<h3>Agents</h3><table border='1' cellpadding='6'>")
    rows.append("<tr><th>Agent</th><th>Model</th><th>Fallbacks</th></tr>")
    for name, spec in agents.items():
        model = spec.get("model", "—")
        fbs = ", ".join(spec.get("fallback_models", [])) or "—"
        rows.append(f"<tr><td>{name}</td><td>{model}</td><td>{fbs}</td></tr>")
    rows.append("</table>")

    return ("<html><body style='font-family:system-ui;padding:2em'>"
            + "\n".join(rows) + "</body></html>")


@router.get("/harness-status.json")
def status_json():
    cfg = _read_json(CONFIG_DIR / "opencode.json") or {}
    return JSONResponse({
        "mcp": cfg.get("mcp", {}).get("harness-tools"),
        "plugins": cfg.get("plugin", []),
        "agents": cfg.get("agent", {}),
    })
