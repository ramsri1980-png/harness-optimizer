"""
Test script for harness-optimizer fixes.
Run inside venv: python3 test_fixes.py
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "templates"))
import server


PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []


def check(name, condition, detail=""):
    mark = PASS if condition else FAIL
    print(f"{mark}  {name}")
    if detail and not condition:
        print(f"     → {detail}")
    results.append((name, condition))


async def test_execute_and_capture_success():
    print("\n[Test 1] execute_and_capture — successful command")
    out = await server.execute_and_capture("ls")
    check("Returns 'Exit code: 0'", "Exit code: 0" in out, out[:200])
    check("Has 'Timed out: no'", "Timed out: no" in out)
    check("Has 'Output truncated'", "Output truncated:" in out)


async def test_execute_and_capture_failure():
    print("\n[Test 2] execute_and_capture — failing command (Fix #1)")
    out = await server.execute_and_capture("ls /definitely_nonexistent_path_xyz")
    check("Does NOT return 'Exit code: 0'", "Exit code: 0" not in out, out[:200])
    check("Returns a non-zero exit code", "Exit code: " in out and "Exit code: 0" not in out)
    check("Still reports 'Timed out: no'", "Timed out: no" in out)


async def test_execute_and_capture_whitelist():
    print("\n[Test 3] execute_and_capture — whitelist enforcement")
    out = await server.execute_and_capture("rm -rf /")
    check("Rejects non-whitelisted command", out.startswith("ERROR:"), out[:200])


async def test_apply_search_replace_ambiguous():
    print("\n[Test 4] apply_search_replace — ambiguous match (Fix #2)")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("hello\nhello\n")
        tmp = f.name
    try:
        out = await server.apply_search_replace(tmp, "hello", "world")
        check("Rejects ambiguous match", "matches 2 locations" in out, out[:200])
        with open(tmp) as f:
            content = f.read()
        check("File left unchanged", content == "hello\nhello\n", content)
    finally:
        os.unlink(tmp)


async def test_apply_search_replace_empty():
    print("\n[Test 5] apply_search_replace — empty search block (Fix #2)")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("original content\n")
        tmp = f.name
    try:
        out = await server.apply_search_replace(tmp, "", "INSERTED")
        check("Rejects empty search", "empty" in out.lower(), out[:200])
        with open(tmp) as f:
            content = f.read()
        check("File left unchanged", content == "original content\n", content)
    finally:
        os.unlink(tmp)


async def test_apply_search_replace_success():
    print("\n[Test 6] apply_search_replace — successful unique edit")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        tmp = f.name
    try:
        out = await server.apply_search_replace(tmp, "return 1", "return 42")
        check("Reports success", "successfully" in out.lower(), out[:200])
        with open(tmp) as f:
            content = f.read()
        check("Contains 'return 42'", "return 42" in content)
        check("Still contains 'return 2'", "return 2" in content)
    finally:
        os.unlink(tmp)


async def test_get_repo_skeleton_async():
    print("\n[Test 7] get_repo_skeleton — async functions (Fix #4)")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "sample.py"), "w") as f:
            f.write(
                "def sync_fn():\n    pass\n\n"
                "async def async_fn():\n    pass\n\n"
                "class Foo:\n"
                "    def method(self):\n        pass\n"
                "    async def async_method(self):\n        pass\n"
            )
        out = await server.get_repo_skeleton(repo_path=d)
        check("Finds sync_fn", "sync_fn" in out, out[:400])
        check("Finds async_fn", "async_fn" in out, out[:400])
        check("Finds method", "method" in out)
        check("Finds async_method", "async_method" in out)


async def test_git_checkpoint_unrelated_staged():
    print("\n[Test 8] git_checkpoint — unrelated staged files (Fix #3)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)

        # Create user.py and stage it
        user_path = os.path.join(d, "user.py")
        with open(user_path, "w") as f:
            f.write("# user file\n")
        subprocess.run(["git", "add", "user.py"], cwd=d, check=True)

        # Create agent.py
        agent_path = os.path.join(d, "agent.py")
        with open(agent_path, "w") as f:
            f.write("# agent file\n")

        # Try checkpoint on agent.py while user.py is staged
        out = await server.git_checkpoint(agent_path, "test checkpoint")
        check("Refuses with unrelated staged files", "unrelated staged files" in out.lower(), out[:300])

        # Confirm user.py is still staged (not committed)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=d, capture_output=True, text=True)
        check("user.py still staged", "A  user.py" in status.stdout or "A user.py" in status.stdout, status.stdout)


# ═══════════════════════════════════════════════════════════
# Regression tests for installer / uninstall / 0.2.6 fixes
# ═══════════════════════════════════════════════════════════

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "bin" / "install.js"


def run_installer(home_dir, *args):
    """Run bin/install.js in an isolated HOME. Returns (rc, stdout+stderr)."""
    import json
    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    r = subprocess.run(
        ["node", str(INSTALLER), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )
    return r.returncode, (r.stdout + r.stderr)


async def test_uninstall_preserves_git_checkout():
    print("\n[Test 9] uninstall does not delete user's git checkout (0.2.5)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        tools = home / "developer" / "harness-optimizer"
        tools.mkdir(parents=True)
        (tools / ".git").mkdir()  # marker of a user checkout

        run_installer(home, "--no-runtime")
        rc, out = run_installer(home, "--uninstall")
        check("Repo dir still exists after uninstall", tools.exists(), out[:300])
        check("Logs preserved git checkout",
              "git checkout" in out.lower() or "preserved" in out.lower(),
              out[:400])


async def test_uninstall_does_not_crash():
    print("\n[Test 10] uninstall references cfg after declaration (0.2.2 fix A)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        (home / ".config" / "opencode").mkdir(parents=True)
        (home / ".config" / "opencode" / "opencode.json").write_text(
            '{"mcp": {"servers": {"harness-tools": {"type": "local", "command": ["x"]}}}}\n'
        )
        rc, out = run_installer(home, "--uninstall")
        check("Uninstall exits cleanly", rc == 0, out[:300])
        check("No 'Cannot access' crash", "Cannot access" not in out, out[:300])


async def test_agents_md_marker_install_uninstall():
    print("\n[Test 11] AGENTS.md ownership marker (0.2.6 fix 4)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")

        md = home / ".config" / "opencode" / "AGENTS.md"
        marker = home / ".config" / "opencode" / ".harness-owns-agents"

        check("AGENTS.md created on fresh install", md.exists())
        check("Ownership marker written", marker.exists())

        try:
            import json
            payload = json.load(open(marker))
            check("Marker is JSON with content hash",
                  bool(payload.get("hash")), str(payload)[:200])
        except Exception as e:
            check("Marker is JSON with hash", False, str(e))


async def test_agents_md_user_edit_preserved():
    print("\n[Test 12] user-edited AGENTS.md preserved on uninstall (0.2.6 fix 4)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")

        md = home / ".config" / "opencode" / "AGENTS.md"
        with open(md, "a") as f:
            f.write("\n# My personal addition\n")

        rc, out = run_installer(home, "--uninstall")
        check("AGENTS.md still exists", md.exists())
        check("User edit intact",
              "My personal addition" in md.read_text(), md.read_text()[:200])
        check("Logs edited or preserved",
              "edited" in out.lower() or "preserved" in out.lower(), out[:400])


async def test_mcp_entry_preferences_preserved():
    print("\n[Test 13] MCP entry survives re-install (0.2.6 fix 2)")
    with tempfile.TemporaryDirectory() as fake_home:
        import json
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")

        cfg_path = home / ".config" / "opencode" / "opencode.json"
        cfg = json.load(open(cfg_path))
        cfg["mcp"]["servers"]["harness-tools"]["environment"] = {"HARNESS_TOOLS": "none"}
        cfg["mcp"]["servers"]["harness-tools"]["disabled"] = True
        cfg_path.write_text(json.dumps(cfg, indent=2))

        run_installer(home, "--no-runtime")

        cfg = json.load(open(cfg_path))
        ht = cfg["mcp"]["servers"]["harness-tools"]
        check("HARNESS_TOOLS preserved",
              ht.get("environment", {}).get("HARNESS_TOOLS") == "none",
              str(ht)[:300])
        check("disabled flag preserved",
              ht.get("disabled") is True, str(ht)[:300])


async def test_cumulative_limits():
    print("\n[Test 14] cumulative line+char limits (0.2.6 fix 1)")
    out = await server.execute_and_capture(
        'python3 -c "print(chr(10).join(chr(65)*5000 for _ in range(61)))"'
    )
    check("Marked truncated", "Output truncated: yes" in out, out[:200])
    check("Response under 220k chars", len(out) < 220_000, f"got {len(out)} chars")
    check("Line marker present", "line limit reached" in out)
    check("Char marker present", "chars dropped" in out)


async def test_installed_ui_has_v2_logic():
    print("\n[Test 15] installed config UI has V2 patterns (0.2.2 fix C)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")

        ui = home / "developer" / "harness-optimizer" / "harness-config-ui.py"
        check("Installed UI exists", ui.exists())
        if ui.exists():
            content = ui.read_text()
            check("Has V2 agents lookup",
                  'cfg.get("agents")' in content or 'cfg["agents"]' in content)
            check("Has V2 mcp.servers reference",
                  'mcp.servers' in content or '"servers"' in content)


async def test_rollback_show_staged():
    print("\n[Test 16] rollback_show reports staged changes (0.2.2 fix D)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)

        with open(os.path.join(d, "a.txt"), "w") as f:
            f.write("v1\n")
        subprocess.run(["git", "add", "a.txt"], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)

        with open(os.path.join(d, "a.txt"), "w") as f:
            f.write("v2\n")
        subprocess.run(["git", "add", "a.txt"], cwd=d, check=True)

        out = await server.rollback_show(d)
        check("Staged section present", "Staged changes" in out, out[:400])
        check("a.txt appears in report", "a.txt" in out, out[:400])

async def test_agents_md_markers_present():
    print("\n[Test 17] AGENTS.md has managed markers (0.2.8 #24)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")
        md = home / ".config" / "opencode" / "AGENTS.md"
        text = md.read_text() if md.exists() else ""
        check("Marker START present",
              "<!-- HARNESS-OPTIMIZER:START -->" in text)
        check("Marker END present",
              "<!-- HARNESS-OPTIMIZER:END -->" in text)


async def test_agents_md_managed_section_updates():
    print("\n[Test 18] Template change updates managed section only (0.2.8 #24)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")
        md = home / ".config" / "opencode" / "AGENTS.md"

        # User adds content AFTER the END marker
        with open(md, "a") as f:
            f.write("\n# My personal addition\nKeep this.\n")

        # Simulate a template change by manually editing installed file's
        # managed section and re-running install
        text = md.read_text()
        modified = text.replace(
            "# Global Rules (all OpenCode sessions)",
            "# Global Rules (all OpenCode sessions) — SHOULD BE OVERWRITTEN"
        )
        md.write_text(modified)

        run_installer(home, "--no-runtime")

        after = md.read_text()
        check("Managed section was updated by reinstall",
              "SHOULD BE OVERWRITTEN" not in after)
        check("User content after markers preserved",
              "My personal addition" in after and "Keep this." in after)


async def test_agents_md_uninstall_strips_only_marked():
    print("\n[Test 19] Uninstall strips marked section only (0.2.8 #24)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")
        md = home / ".config" / "opencode" / "AGENTS.md"

        with open(md, "a") as f:
            f.write("\n# My personal addition\nKeep this.\n")

        run_installer(home, "--uninstall")

        check("AGENTS.md still exists (user content present)",
              md.exists())
        if md.exists():
            text = md.read_text()
            check("Marked section removed",
                  "HARNESS-OPTIMIZER" not in text)
            check("User content preserved",
                  "My personal addition" in text and "Keep this." in text)


async def main():
    print("=" * 60)
    print("Harness-Optimizer Fix Verification")
    print("=" * 60)
    await test_execute_and_capture_success()
    await test_execute_and_capture_failure()
    await test_execute_and_capture_whitelist()
    await test_apply_search_replace_ambiguous()
    await test_apply_search_replace_empty()
    await test_apply_search_replace_success()
    await test_get_repo_skeleton_async()
    await test_git_checkpoint_unrelated_staged()
    await test_uninstall_preserves_git_checkout()
    await test_uninstall_does_not_crash()
    await test_agents_md_marker_install_uninstall()
    await test_agents_md_user_edit_preserved()
    await test_mcp_entry_preferences_preserved()
    await test_cumulative_limits()
    await test_installed_ui_has_v2_logic()
    await test_rollback_show_staged()
    await test_agents_md_markers_present()
    await test_agents_md_managed_section_updates()
    await test_agents_md_uninstall_strips_only_marked()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULTS: {passed}/{total} checks passed")
    print("=" * 60)
    if passed != total:
        print("\nFailed checks:")
        for name, ok in results:
            if not ok:
                print(f"  ✗ {name}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
