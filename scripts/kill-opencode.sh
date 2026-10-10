#!/usr/bin/env bash
# scripts/kill-opencode.sh — cleanly kill all OpenCode-related processes.
#
# Usage:
#   ./scripts/kill-opencode.sh           # SIGTERM (graceful)
#   ./scripts/kill-opencode.sh --force   # SIGKILL after 3s if still alive
#   ./scripts/kill-opencode.sh --dry     # show what would be killed

set -uo pipefail

MODE="graceful"
case "${1:-}" in
  --force) MODE="force" ;;
  --dry)   MODE="dry" ;;
esac

declare -a PATTERNS=(
  "opencode serve --service"
  "opencode run"
  "\.opencode/bin/opencode"
  "harness-optimizer/server.py"
  "harness-optimizer/venv/bin/python.*server\.py"
)

collect_pids() {
  local pid
  for pat in "${PATTERNS[@]}"; do
    pgrep -f "$pat" 2>/dev/null || true
  done | sort -u | grep -v "^$$\$" || true
}

pids="$(collect_pids)"

if [[ -z "$pids" ]]; then
  echo "No OpenCode processes found."
  exit 0
fi

echo "OpenCode-related PIDs:"
for pid in $pids; do
  ps -o pid=,cmd= -p "$pid" 2>/dev/null | sed 's/^/  /'
done

if [[ "$MODE" == "dry" ]]; then
  echo "(--dry) nothing killed."
  exit 0
fi

if [[ "$MODE" == "graceful" ]]; then
  echo "Sending SIGTERM..."
  echo "$pids" | xargs -r kill 2>/dev/null || true
  sleep 3
  still="$(collect_pids)"
  if [[ -z "$still" ]]; then
    echo "All clean."
    exit 0
  fi
  echo "Still alive after SIGTERM:"
  for pid in $still; do
    ps -o pid=,cmd= -p "$pid" 2>/dev/null | sed 's/^/  /'
  done
  echo "Run with --force to SIGKILL."
  exit 1
fi

# --force
echo "Sending SIGKILL..."
echo "$pids" | xargs -r kill -9 2>/dev/null || true
sleep 1
still="$(collect_pids)"
if [[ -z "$still" ]]; then
  echo "All clean (SIGKILL)."
  exit 0
fi
echo "Still alive:"
for pid in $still; do
  ps -o pid=,cmd= -p "$pid" 2>/dev/null | sed 's/^/  /'
done
exit 1
