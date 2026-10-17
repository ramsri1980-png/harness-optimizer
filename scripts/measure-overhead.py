#!/usr/bin/env python3
"""Measure the FIXED per-request token overhead of the harness.

Fixed overhead = (1) MCP tool schemas the model receives on every request and
(2) the global AGENTS.md. Both are paid whether or not a tool is called.

  # run with the harness venv python (it has fastmcp):
  ~/developer/harness-optimizer/venv/bin/python scripts/measure-overhead.py
  ... measure-overhead.py --tiktoken          # exact count if tiktoken is installed
  ... measure-overhead.py --from-json t.json  # use a saved tools/list dump
  ... measure-overhead.py --agents-only       # no fastmcp needed

Token counts are len/4 estimates unless --tiktoken is given (cl100k_base, an
approximation of any provider's tokenizer).
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DUMP = r"""
import asyncio, json, sys
from fastmcp import Client
async def main():
    async with Client(sys.argv[1]) as c:
        out = []
        for t in await c.list_tools():
            out.append({"name": t.name, "description": t.description or "",
                        "input_schema": getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)})
        print(json.dumps(out))
asyncio.run(main())
"""


def counter(use_tiktoken):
    if use_tiktoken:
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return (lambda s: len(enc.encode(s))), "tiktoken cl100k_base"
        except Exception as exc:
            print(f"(tiktoken unavailable: {exc}; falling back to len/4)", file=sys.stderr)
    return (lambda s: max(1, len(s) // 4)), "len/4 estimate"


def schema_tokens(tools, count):
    rows = []
    for t in tools:
        payload = json.dumps({"name": t["name"], "description": t["description"],
                              "input_schema": t["input_schema"]}, separators=(",", ":"))
        rows.append((t["name"], len(payload), count(payload)))
    return rows


def dump_tools(server_py, python, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    r = subprocess.run([python, "-c", DUMP, server_py], capture_output=True, text=True,
                       env=env, cwd=os.path.dirname(server_py), timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "tool dump failed")
    return json.loads(r.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=os.path.join(HERE, "..", "templates", "server.py"))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--agents", default=os.path.join(HERE, "..", "templates", "AGENTS.md"))
    ap.add_argument("--installed-agents", default=os.path.expanduser("~/.config/opencode/AGENTS.md"))
    ap.add_argument("--from-json")
    ap.add_argument("--agents-only", action="store_true")
    ap.add_argument("--tiktoken", action="store_true")
    a = ap.parse_args(argv)
    count, how = counter(a.tiktoken)
    print(f"Counting method: {how}\n")

    grand = 0
    if not a.agents_only:
        scenarios = [("all tools, default", {}),
                     ("lean profile (4 tools)", {"HARNESS_LEAN": "1", "HARNESS_TOOLS": "get_repo_skeleton,rip_file_lines,apply_search_replace,lint_file"}),
                     ("minimal (2 tools)", {"HARNESS_LEAN": "1", "HARNESS_TOOLS": "get_repo_skeleton,apply_search_replace"})]
        for label, env in scenarios:
            try:
                tools = json.load(open(a.from_json)) if a.from_json else dump_tools(os.path.abspath(a.server), a.python, env)
            except Exception as exc:
                print(f"[schemas] cannot list tools ({exc}).")
                print("          Run this script with the harness venv python, or pass --from-json.\n")
                break
            if a.from_json:
                label = "from-json (HARNESS_TOOLS has no effect here)"
            rows = schema_tokens(tools, count)
            total = sum(r[2] for r in rows)
            print(f"[schemas] {label}: {len(rows)} tools, {total} tokens/request")
            for n, chars, tok in sorted(rows, key=lambda r: -r[2]):
                print(f"            {n:28}{chars:>7} chars {tok:>6} tok")
            print()
            if a.from_json:
                break

    for label, path in (("AGENTS.md template", a.agents), ("AGENTS.md installed (whole file)", a.installed_agents)):
        if os.path.exists(path):
            text = open(path, encoding="utf-8").read()
            print(f"[rules]   {label}: {len(text)} bytes, {count(text)} tokens/request   ({os.path.normpath(path)})")
        else:
            print(f"[rules]   {label}: not found ({path})")
    print("\nFixed overhead per request = schemas + AGENTS.md (+ any CTX packet in context).")


if __name__ == "__main__":
    main()
