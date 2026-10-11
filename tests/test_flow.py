#!/usr/bin/env python3
"""FLOW end-to-end tests (FLOW-01 … FLOW-05).

Run directly::

    python3 tests/test_flow.py

Standalone: every check builds its own fixture under a temp directory. The
CTX hook checks (FLOW-05c) drive templates/auto-context.ts through a throwaway
OpenCode run, so the `opencode` binary is required for that one check.

Exits nonzero if any check fails.
"""

import asyncio
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "templates"))
import server  # noqa: E402  (needs the templates/ path above)

AUTO_CONTEXT = REPO_ROOT / "templates" / "auto-context.ts"

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []

FIXTURE_BUGGY = (
    "def helper(a, b):\n"
    "    return a - b\n"
    "\n"
    "def main():\n"
    "    print(helper(2, 3))\n"
)
FIXTURE_FIXED = FIXTURE_BUGGY.replace("    return a - b", "    return a + b")

HOOK_PROBE_TIMEOUT = 120


def check(label, cond, detail=""):
    mark = PASS if cond else FAIL
    print(f"{mark}  {label}")
    if detail and not cond:
        print(f"     → {detail}")
    results.append((label, bool(cond)))
    return bool(cond)


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30
    )


# ─────────────────────────────────────────────────────────────────────────
# FLOW-01 — read → edit → lint
# ─────────────────────────────────────────────────────────────────────────


async def test_read_edit_check_flow():
    print("\n[FLOW-01] rip → apply_search_replace → lint_file")
    with tempfile.TemporaryDirectory(prefix="flow-01-") as tmp:
        path = os.path.join(tmp, "fixture.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE_BUGGY)

        ripped = await server.rip_file_lines(path, 1, 2, None)
        rip_ok = (
            isinstance(ripped, str)
            and ripped.startswith("--- Lines 1 to 2 of")
            and "# sha256:" in ripped
            and "return a - b" in ripped
        )
        check(
            "FLOW-01: rip_file_lines returns the buggy window with a fingerprint",
            rip_ok,
            ripped[:300],
        )

        edited = await server.apply_search_replace(
            path, "    return a - b", "    return a + b"
        )
        edit_ok = (
            isinstance(edited, str)
            and edited.startswith("Surgical replacement applied successfully")
            and "before_sha256:" in edited
            and "after_sha256:" in edited
        )
        check(
            "FLOW-01: apply_search_replace fixes the bug",
            edit_ok,
            edited[:300],
        )

        with open(path, "r", encoding="utf-8") as fh:
            after = fh.read()
        check(
            "FLOW-01: file content changed correctly",
            after == FIXTURE_FIXED and "a - b" not in after,
            repr(after),
        )

        linted = await server.lint_file(path)
        check(
            "FLOW-01: lint_file verifies the fixed file",
            isinstance(linted, str) and linted.startswith("OK: Python syntax check passed"),
            linted[:300],
        )


# ─────────────────────────────────────────────────────────────────────────
# FLOW-02 — a refused edit never mutates and never routes around
# ─────────────────────────────────────────────────────────────────────────


class _SubprocessGuard:
    """Any subprocess entry point reached during a refused edit is a bypass."""

    def __init__(self, attempts):
        self._attempts = attempts

    def __getattr__(self, name):
        self._attempts.append(name)
        raise AssertionError("server.subprocess.%s attempted during a refused edit" % name)


async def test_refused_edit_no_bypass():
    print("\n[FLOW-02] refused edit — no mutation, no bypass")
    with tempfile.TemporaryDirectory(prefix="flow-02-") as tmp:
        path = os.path.join(tmp, "fixture.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE_BUGGY)
        before = pathlib.Path(path).read_bytes()
        before_sha = hashlib.sha256(before).hexdigest()

        attempts = []
        real_subprocess = server.subprocess
        server.subprocess = _SubprocessGuard(attempts)
        try:
            out = await server.apply_search_replace(
                path,
                "def never_matches(x):\n    return x\n",
                "def replacement(x):\n    return x\n",
            )
        finally:
            server.subprocess = real_subprocess

        refused = isinstance(out, str) and out.startswith("ERROR:") and "NOT FOUND" in out
        check(
            "FLOW-02: non-matching block is refused",
            refused,
            str(out)[:300],
        )

        after = pathlib.Path(path).read_bytes()
        check(
            "FLOW-02: refused edit leaves the file byte-identical",
            after == before and hashlib.sha256(after).hexdigest() == before_sha,
        )

        leftovers = sorted(os.listdir(tmp))
        check(
            "FLOW-02: no temp/partial files left behind",
            leftovers == ["fixture.py"],
            repr(leftovers),
        )

        check(
            "FLOW-02: no attempted bypass path (zero subprocess entry points)",
            attempts == [],
            repr(attempts),
        )


# ─────────────────────────────────────────────────────────────────────────
# FLOW-03 — git_checkpoint refuses unrelated staged work, then commits clean
# The only commits made anywhere are inside this throwaway temp repo.
# ─────────────────────────────────────────────────────────────────────────


async def test_git_checkpoint_sequence():
    print("\n[FLOW-03] git_checkpoint — refusal on unrelated staged work, then clean commit")
    with tempfile.TemporaryDirectory(prefix="flow-03-") as tmp:
        check("FLOW-03: temp repo initialises", _git(["init", "-q"], tmp).returncode == 0)
        _git(["config", "user.email", "flow-test@example.invalid"], tmp)
        _git(["config", "user.name", "FLOW Test"], tmp)

        target = os.path.join(tmp, "target.py")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("VALUE = 1\n")
        user_file = os.path.join(tmp, "user_note.txt")
        with open(user_file, "w", encoding="utf-8") as fh:
            fh.write("user work in progress\n")

        _git(["add", "user_note.txt"], tmp)
        staged = _git(["diff", "--cached", "--name-only"], tmp).stdout.split()
        check("FLOW-03: user file staged", staged == ["user_note.txt"], repr(staged))

        out = await server.git_checkpoint(target, "flow target fix")
        refused = isinstance(out, str) and "Unrelated staged files detected" in out
        no_commit = _git(["rev-parse", "--verify", "HEAD"], tmp).returncode != 0
        check(
            "FLOW-03: checkpoint on a different file is refused (unrelated staged work)",
            refused and no_commit,
            str(out)[:300],
        )

        _git(["reset", "-q"], tmp)
        out2 = await server.git_checkpoint(target, "flow target fix")
        committed = isinstance(out2, str) and out2.startswith("Committed:") and "commit_id:" in out2
        message = _git(["log", "-1", "--format=%s"], tmp).stdout.strip()
        changed = [
            line.strip()
            for line in _git(["show", "--name-only", "--format=", "HEAD"], tmp)
            .stdout.splitlines()
            if line.strip()
        ]
        status = _git(["status", "--porcelain"], tmp).stdout
        check(
            "FLOW-03: retry after unstaging commits cleanly and only the target file",
            committed
            and message == "[harness] flow target fix"
            and changed == ["target.py"]
            and "target.py" not in status
            and "user_note.txt" in status,
            "resp=%r msg=%r changed=%r status=%r" % (out2[:200], message, changed, status),
        )


# ─────────────────────────────────────────────────────────────────────────
# FLOW-04 — schema inspection is read-only (no row data may be selected)
# ─────────────────────────────────────────────────────────────────────────


class _SchemaOnlyCursor:
    """Raises the moment anything but schema metadata is selected."""

    def __init__(self, inner, executed):
        self._inner = inner
        self._executed = executed

    def execute(self, sql, *args, **kwargs):
        if re.search(r"select\s+\*", str(sql), re.I):
            raise AssertionError("row-data read attempted: %s" % sql)
        self._executed.append(str(sql))
        return self._inner.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _SchemaOnlyConnection:
    def __init__(self, inner, executed):
        self._inner = inner
        self._executed = executed

    def cursor(self):
        return _SchemaOnlyCursor(self._inner.cursor(), self._executed)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _SchemaOnlySqlite:
    """Drop-in for server.sqlite3 that fails on any SELECT *."""

    Error = sqlite3.Error

    def __init__(self):
        self.executed = []

    def connect(self, *args, **kwargs):
        return _SchemaOnlyConnection(sqlite3.connect(*args, **kwargs), self.executed)


async def test_database_schema_read_only():
    print("\n[FLOW-04] inspect_database_schema — schema only, never rows")
    with tempfile.TemporaryDirectory(prefix="flow-04-") as tmp:
        db_path = os.path.join(tmp, "flow.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
        )
        conn.execute("CREATE INDEX idx_widgets_name ON widgets(name)")
        conn.execute("INSERT INTO widgets (name) VALUES ('gizmo')")
        conn.commit()
        conn.close()

        guard = _SchemaOnlySqlite()
        real_sqlite = server.sqlite3
        server.sqlite3 = guard
        try:
            out = await server.inspect_database_schema(db_path)
        finally:
            server.sqlite3 = real_sqlite

        check(
            "FLOW-04: schema response includes the table and its columns",
            isinstance(out, str)
            and "## widgets" in out
            and "id INTEGER PK" in out
            and "name TEXT NOT NULL" in out
            and "idx_widgets_name" in out,
            str(out)[:400],
        )

        statements = [s.strip() for s in guard.executed]
        metadata_only = all(
            s.upper().startswith("SELECT NAME FROM SQLITE_MASTER") or s.upper().startswith("PRAGMA")
            for s in statements
        )
        check(
            "FLOW-04: no rows read — every statement is schema metadata",
            metadata_only and len(statements) > 0,
            repr(statements[:8]),
        )


# ─────────────────────────────────────────────────────────────────────────
# FLOW-05a/b — context tools are individually callable and never drag each
# other in.
# ─────────────────────────────────────────────────────────────────────────

CONTEXT_TOOLS = ("get_repo_skeleton", "rip_file_lines", "find_dependent_references")


async def _tool_isolation(tmp):
    calls = []
    originals = {name: getattr(server, name) for name in CONTEXT_TOOLS}

    def wrap(name):
        async def wrapper(*args, **kwargs):
            calls.append(name)
            return await originals[name](*args, **kwargs)

        return wrapper

    for name in CONTEXT_TOOLS:
        setattr(server, name, wrap(name))

    outputs = {}
    invocations = {}
    try:
        for name in CONTEXT_TOOLS:
            del calls[:]
            if name == "get_repo_skeleton":
                out = await server.get_repo_skeleton(str(tmp), 50, "outline", 1500)
                ok = isinstance(out, str) and bool(out) and not out.startswith("ERROR")
            elif name == "rip_file_lines":
                out = await server.rip_file_lines(os.path.join(str(tmp), "fixture.py"), 1, 1, None)
                ok = isinstance(out, str) and "# sha256:" in out
            else:
                out = await server.find_dependent_references("helper", str(tmp))
                ok = isinstance(out, str) and bool(out)
            outputs[name] = (bool(ok), str(out)[:200])
            invocations[name] = list(calls)
    finally:
        for name, original in originals.items():
            setattr(server, name, original)

    for name in CONTEXT_TOOLS:
        ok, out = outputs[name]
        check(
            "FLOW-05: %s callable individually" % name,
            ok and invocations[name] == [name],
            "invocations=%r out=%r" % (invocations[name], out),
        )

    check(
        "FLOW-05: no automatic tool call is made by any other tool",
        all(invocations[name] == [name] for name in CONTEXT_TOOLS),
        repr({name: invocations[name] for name in CONTEXT_TOOLS}),
    )


# ─────────────────────────────────────────────────────────────────────────
# FLOW-05c — the CTX context hook never spawns the worker off the MAP branch
# ─────────────────────────────────────────────────────────────────────────

HOOK_PROBE_TS = r'''
import { writeFileSync } from "fs"
import autoContext, { decideGate } from "../lib/auto-context.ts"

const OUT = "__OUT__"
const results: any = { hookCalls: 0, gates: {} }

function event(text: string): any {
  return {
    messages: [{ id: "m_" + text.length, role: "user", content: [{ type: "text", text }] }],
    agent: "primary",
    model: "stub/stub-1",
    system: [],
    options: {},
    tools: {},
  }
}

export default {
  id: "flow-hook-probe",
  async setup(_ctx: any) {
    try {
      let hook: any = null
      await autoContext.setup({
        location: { directory: process.cwd() },
        session: {
          hook: async (_name: string, callback: any) => {
            hook = callback
            return { dispose: async () => {} }
          },
        },
      } as any)
      results.registered = typeof hook === "function"
      if (typeof hook !== "function") throw new Error("context hook was not registered")

      const general = event("hi, how are you today?")
      const file = event("Please look at notes/todo.txt and tell me what it says.")
      const repo = event("How is this repository structured? Give me a brief overview.")

      const root = process.cwd()
      results.gates.general = decideGate(general, root).decision
      results.gates.file = decideGate(file, root).decision
      results.gates.repo = decideGate(repo, root).decision

      // Only the non-MAP decisions are fired through the hook: MAP is the
      // one branch that may spawn the worker, and the gate alone must not.
      await hook(general)
      results.hookCalls++
      await hook(file)
      results.hookCalls++
    } catch (err: any) {
      results.error = String(err && err.stack ? err.stack : err)
    }
    try {
      writeFileSync(OUT, JSON.stringify(results, null, 2))
    } catch {}
  },
}
'''


def _running_workers(pattern):
    try:
        proc = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5
        )
    except Exception:
        return []
    if proc.returncode not in (0, 1):
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


async def test_context_tools_not_forced():
    print("\n[FLOW-05] context tools not forced + CTX hook gate")
    with tempfile.TemporaryDirectory(prefix="flow-05-") as tmp:
        path = os.path.join(tmp, "fixture.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(FIXTURE_BUGGY)

        await _tool_isolation(pathlib.Path(tmp))

        # The worker client may only be reachable from the MAP branch.
        source = AUTO_CONTEXT.read_text(encoding="utf-8")
        call_sites = [m.start() for m in re.finditer(r"await callWorker\(", source)]
        map_branch = source.find('if (gate.decision === "MAP")')
        check(
            "FLOW-05: worker client has a single call site, inside the MAP branch",
            len(call_sites) == 1 and map_branch >= 0 and call_sites[0] > map_branch,
            "call_sites=%d map_branch=%d" % (len(call_sites), map_branch),
        )

        probe = await _run_hook_probe(tmp)
        if probe is None:
            check(
                "FLOW-05: context hook spawns no worker unless the gate returns MAP",
                False,
                "opencode is not on PATH — cannot drive the CTX hook",
            )
            return

        gates = probe.get("gates") or {}
        check(
            "FLOW-05: gate decisions — NONE / FOCUSED for ordinary prompts, MAP only for repo questions",
            probe.get("registered") is True
            and probe.get("error") is None
            and gates.get("general") == "NONE"
            and gates.get("file") == "FOCUSED"
            and gates.get("repo") == "MAP"
            and probe.get("hookCalls") == 2,
            json.dumps(probe)[:400],
        )

        # A dummy worker that sleeps would be visible if anything spawned it
        # on those non-MAP hook firings.
        pattern = probe.get("worker_pattern", "")
        pids = _running_workers(pattern) if pattern else []
        for pid in pids:
            try:
                os.kill(int(pid), 15)
            except Exception:
                pass
        check(
            "FLOW-05: context hook spawns no worker unless the gate returns MAP",
            probe.get("error") is None and probe.get("hookCalls") == 2 and pids == [],
            "workers=%r error=%r" % (pids, probe.get("error")),
        )


async def _run_hook_probe(tmp):
    """Fire the real CTX hook with non-MAP events; return the probe's JSON."""
    if not shutil.which("opencode"):
        print("SKIP: opencode not on PATH — CTX hook probe skipped")
        return None

    config_dir = os.path.join(tmp, "config")
    plugins_dir = os.path.join(config_dir, "plugins")
    lib_dir = os.path.join(config_dir, "lib")
    workspace = os.path.join(tmp, "workspace")
    result_path = os.path.join(tmp, "hook-probe.json")
    for directory in (plugins_dir, lib_dir, workspace):
        os.makedirs(directory, exist_ok=True)

    # Sleep-forever stand-in: if the hook ever spawned the worker off the MAP
    # branch, this process outlives the run and pgrep finds it.
    dummy_worker = os.path.join(plugins_dir, "context_worker.py")
    with open(dummy_worker, "w", encoding="utf-8") as fh:
        fh.write("import time\n\ntime.sleep(120)\n")

    shutil.copyfile(str(AUTO_CONTEXT), os.path.join(lib_dir, "auto-context.ts"))
    with open(os.path.join(plugins_dir, "probe.ts"), "w", encoding="utf-8") as fh:
        fh.write(HOOK_PROBE_TS.replace("__OUT__", result_path))

    config = {
        "$schema": "https://opencode.ai/config.json",
        "agents": {"primary": {"model": "missing/model"}},
        "plugins": [plugins_dir],
    }
    with open(os.path.join(config_dir, "opencode.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2)

    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.path.join(tmp, "home"),
        "OPENCODE_CONFIG_DIR": config_dir,
        "OPENCODE_TEST_HOME": os.path.join(tmp, "testhome"),
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "HARNESS_CTX": "on",
    }
    os.makedirs(env["HOME"], exist_ok=True)

    try:
        # Plugin setup — where the probe runs — happens before the model
        # request, so the deliberately unavailable model ref keeps this run
        # off the network without weakening the check.
        proc = subprocess.run(
            ["opencode", "run", "--standalone", "--auto", "-m", "missing/model", "flow probe"],
            cwd=workspace,
            env=env,
            capture_output=True,
            timeout=HOOK_PROBE_TIMEOUT,
        )
        print("[opencode] flow hook probe exit=%s" % proc.returncode)
        if not os.path.isfile(result_path):
            return {"error": "probe wrote no result file"}
        with open(result_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["worker_pattern"] = dummy_worker
        return payload
    except subprocess.TimeoutExpired:
        return {"error": "hook probe exceeded %ds" % HOOK_PROBE_TIMEOUT}
    except Exception as exc:
        return {"error": str(exc)}


async def main():
    print("Running FLOW Test Suite...")
    await test_read_edit_check_flow()
    await test_refused_edit_no_bypass()
    await test_git_checkpoint_sequence()
    await test_database_schema_read_only()
    await test_context_tools_not_forced()

    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULTS: {passed}/{total} checks passed")
    print("=" * 60)

    failed = [name for name, ok in results if not ok]
    if failed:
        print(f"FAILED: {len(failed)} check(s):")
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
