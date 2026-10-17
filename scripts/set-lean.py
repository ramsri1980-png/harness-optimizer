#!/usr/bin/env python3
"""Turn harness lean mode on/off in opencode.json (backup first, idempotent).

  python3 scripts/set-lean.py                 # lean on, 4-tool 'lean' profile
  python3 scripts/set-lean.py --tools minimal # 2 tools
  python3 scripts/set-lean.py --tools get_repo_skeleton,rip_file_lines
  python3 scripts/set-lean.py --off           # lean off (tool list untouched)
  python3 scripts/set-lean.py --off --reset-tools
  python3 scripts/set-lean.py --dry-run

Only keys under mcp.servers["harness-tools"].environment are touched.
"""
import argparse
import json
import os
import shutil
import sys
import time

PROFILES = {
    "lean": "get_repo_skeleton,rip_file_lines,apply_search_replace,lint_file",
    "minimal": "get_repo_skeleton,apply_search_replace",
    "read": "get_repo_skeleton,rip_file_lines",
    "all": "all",
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.expanduser("~/.config/opencode/opencode.json"))
    ap.add_argument("--off", action="store_true", help="disable lean mode")
    ap.add_argument("--tools", default="lean", help="profile (%s) or comma list" % "/".join(PROFILES))
    ap.add_argument("--reset-tools", action="store_true", help="with --off: also remove HARNESS_TOOLS")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if not os.path.exists(a.config):
        sys.exit(f"ERROR: {a.config} not found")
    try:
        with open(a.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: {a.config} is not valid JSON ({exc}); left untouched")

    servers = (cfg.get("mcp") or {}).get("servers") or {}
    legacy = (cfg.get("mcp") or {}).get("harness-tools")
    entry = servers.get("harness-tools") if "harness-tools" in servers else legacy
    if not isinstance(entry, dict):
        sys.exit("ERROR: no harness-tools MCP entry found; run `npx harness-optimizer` first")

    env = entry.get("environment")
    env = dict(env) if isinstance(env, dict) else {}
    before = dict(env)
    if a.off:
        env.pop("HARNESS_LEAN", None)
        if a.reset_tools:
            env.pop("HARNESS_TOOLS", None)
    else:
        env["HARNESS_LEAN"] = "1"
        env["HARNESS_TOOLS"] = PROFILES.get(a.tools, a.tools)
    if env:
        entry["environment"] = env
    else:
        entry.pop("environment", None)

    if env == before:
        print("No change needed.")
    elif a.dry_run:
        print("DRY RUN, would set environment to:", json.dumps(env))
    else:
        bak = f"{a.config}.bak.{int(time.time())}"
        n = 0
        while os.path.exists(bak):  # never overwrite an earlier backup
            n += 1
            bak = f"{a.config}.bak.{int(time.time())}.{n}"
        shutil.copy2(a.config, bak)
        tmp = a.config + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(cfg, indent=2) + "\n")
        os.replace(tmp, a.config)
        print(f"Backup: {bak}")
        print("Updated environment:", json.dumps(env))
    if not a.off:
        print("\nThe CTX plugin runs in OpenCode's own process, not the MCP server. To enable its")
        print("lean behaviour too, export these where you launch OpenCode (e.g. ~/.bashrc):")
        print("  export HARNESS_LEAN=1")
        print("  export HARNESS_CTX_MAP_TOKENS=800   # optional, default 1500")
    print("\nRestart OpenCode for changes to take effect.")


if __name__ == "__main__":
    main()
