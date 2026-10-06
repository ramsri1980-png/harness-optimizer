# Troubleshooting

## Tools missing after editing server.py
OpenCode does not hot-reload the MCP server. Restart OpenCode completely
after changing server.py or the HARNESS_TOOLS environment variable.

## Config not picked up
Check that ~/.config/opencode/opencode.json is valid JSON.
Run: npx harness-optimizer --doctor

## Plugin errors
Check ~/.config/opencode/opencode-fallback.jsonc and
opencode-context-watch.json for syntax errors.
