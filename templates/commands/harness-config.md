---
description: Open the Harness-Optimizer configuration web UI.
---

Open the Harness-Optimizer config UI.

Steps:
1. Check if it's already running:
   `pgrep -f harness-config-ui.py`
2. If not running, start it in the background:
   `nohup python3 ~/developer/harness-optimizer/harness-config-ui.py > /tmp/harness-config-ui.log 2>&1 &`
3. Open the browser:
   `xdg-open http://localhost:8765 || open http://localhost:8765`
4. Confirm to the user that the UI is opening at http://localhost:8765

The UI at localhost:8765 manages:
- Model priority and fallback chains per agent
- MCP tool toggles (HARNESS_TOOLS)
- Config backups and restore
- Token savings totals

Changes save directly to ~/.config/opencode/opencode.json.
Restart OpenCode after saving to apply.
