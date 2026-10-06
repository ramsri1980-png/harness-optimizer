---
description: Open the Harness-Optimizer configuration web UI.
---

Open the Harness-Optimizer configuration web UI.

Steps:
1. Check if `opencode-studio-server` is running: `pgrep -f opencode-studio-server`
2. If not running, start it: `nohup opencode-studio-server > /tmp/opencode-studio.log 2>&1 &`
3. Open the browser: `xdg-open http://localhost:1080 || open http://localhost:1080`
4. Confirm to the user that the browser is opening.

The web UI at localhost:1080 manages ONLY the Harness-Optimizer MCP server,
its model assignments, fallback chains, and the token-metrics plugin.
For all other OpenCode settings, use the built-in `/config` TUI command.
