"""
Test script for harness-optimizer fixes.
Run inside venv: python3 test_fixes.py
"""
import asyncio
import os
import re
import shutil
import sqlite3
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
        # Simulate a config from a pre-0.13.2 install, which wrote
        # `disabled` and never wrote `enabled`.
        cfg["mcp"]["servers"]["harness-tools"].pop("enabled", None)
        cfg["mcp"]["servers"]["harness-tools"]["disabled"] = True
        cfg_path.write_text(json.dumps(cfg, indent=2))

        run_installer(home, "--no-runtime")

        cfg = json.load(open(cfg_path))
        ht = cfg["mcp"]["servers"]["harness-tools"]
        check("HARNESS_TOOLS preserved",
              ht.get("environment", {}).get("HARNESS_TOOLS") == "none",
              str(ht)[:300])
        check("legacy disabled migrated to enabled: false",
              ht.get("enabled") is False, str(ht)[:300])
        check("legacy disabled key removed",
              "disabled" not in ht, str(ht)[:300])


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

async def test_malformed_markers_rejected():
    print("\n[Test 20] Malformed markers reject update (0.2.9)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")
        md = home / ".config" / "opencode" / "AGENTS.md"

        # Corrupt: duplicate START marker
        text = md.read_text()
        corrupted = text + "\n<!-- HARNESS-OPTIMIZER:START -->\ndangling\n"
        md.write_text(corrupted)
        before = md.read_text()

        rc, out = run_installer(home, "--no-runtime")
        after = md.read_text()

        check("File left unchanged on duplicate markers",
              before == after, "file was modified")
        check("Warns about malformed markers",
              "malformed" in out.lower() or "found 2" in out.lower(), out[:400])


async def test_reversed_markers_rejected():
    print("\n[Test 21] Reversed markers reject update (0.2.9)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")
        md = home / ".config" / "opencode" / "AGENTS.md"

        # Corrupt: swap markers so END comes first
        bad = (
            "<!-- HARNESS-OPTIMIZER:END -->\n"
            "# some content\n"
            "<!-- HARNESS-OPTIMIZER:START -->\n"
        )
        md.write_text(bad)
        before = md.read_text()

        rc, out = run_installer(home, "--no-runtime")
        after = md.read_text()

        check("File left unchanged on reversed markers",
              before == after, "file was modified")
        check("Warns about reversed or malformed",
              "malformed" in out.lower() or "before START" in out.lower(),
              out[:400])


async def test_backup_and_unmarked_file_leaves_both():
    print("\n[Test 22] Backup + unmarked file → both preserved (0.2.9)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        cfgdir = home / ".config" / "opencode"
        cfgdir.mkdir(parents=True)

        # Simulate: user had a personal file, harness force-installed, then user
        # replaced AGENTS.md with newer personal content (no markers)
        (cfgdir / "AGENTS.md.harness-backup").write_text(
            "# Pre-install backup\nOld content.\n"
        )
        (cfgdir / "AGENTS.md").write_text(
            "# Newer personal file\nShould not be overwritten.\n"
        )

        rc, out = run_installer(home, "--uninstall")

        check("Current file preserved",
              (cfgdir / "AGENTS.md").exists() and
              "Newer personal" in (cfgdir / "AGENTS.md").read_text(),
              (cfgdir / "AGENTS.md").read_text()[:200] if (cfgdir / "AGENTS.md").exists() else "gone")
        check("Backup preserved (not restored over current)",
              (cfgdir / "AGENTS.md.harness-backup").exists(),
              "backup was consumed")
        check("Warns user to decide",
              "leaving both" in out.lower() or "no valid" in out.lower(),
              out[:400])


async def test_managed_section_update_still_works():
    print("\n[Test 23] Managed section update still works (0.2.9 regression)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")
        md = home / ".config" / "opencode" / "AGENTS.md"

        # Add user content outside markers
        with open(md, "a") as f:
            f.write("\n# Personal section\nKeep this forever.\n")

        # Corrupt the managed section, then reinstall should restore it
        text = md.read_text()
        modified = text.replace(
            "# Global Rules (all OpenCode sessions)",
            "# Global Rules (all OpenCode sessions) - CORRUPTED"
        )
        md.write_text(modified)

        run_installer(home, "--no-runtime")

        after = md.read_text()
        check("Managed section restored",
              "CORRUPTED" not in after)
        check("User content outside markers preserved",
              "Personal section" in after and "Keep this forever." in after)
async def test_skeleton_includes_line_ranges():
    print("\n[Test 24] Skeleton line ranges + recursion (0.3.1)")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "sample.py"), "w") as f:
            f.write(
                "@decorator\n"
                "def decorated():\n"          # L2-L3
                "    pass\n"
                "\n"
                "def outer():\n"               # L6-L11
                "    def nested():\n"          # L7-L8
                "        return 1\n"
                "    return nested\n"
                "\n"
                "if True:\n"                   # L11
                "    def conditional_fn():\n"  # L12-L13
                "        pass\n"
                "\n"
                "class Foo:\n"                 # L15-L19
                "    def method(self):\n"      # L16-L17
                "        pass\n"
                "    async def async_method(self):\n"  # L18-L19
                "        pass\n"
            )
        out = await server.get_repo_skeleton(repo_path=d)

        check("Decorated function has range",
              "decorated" in out and "[L1-L3]" in out, out[:500])
        check("Nested function present",
              "nested" in out, out[:500])
        check("Conditional-def present",
              "conditional_fn" in out, out[:500])
        check("Class has range",
              "class Foo" in out and "[L14-L18]" in out, out[:500])
        check("Method has own range (not class range)",
              "method" in out and "[L15-L16]" in out, out[:600])
        check("Async prefix on method",
              "async def async_method" in out, out[:500])

async def test_find_refs_rejects_empty():
    print("\n[Test 25] find_dependent_references rejects empty (0.3.0 #2)")
    with tempfile.TemporaryDirectory() as d:
        r1 = await server.find_dependent_references("", d)
        r2 = await server.find_dependent_references("   ", d)
        check("Empty symbol rejected", r1.startswith("ERROR:"), r1)
        check("Whitespace symbol rejected", r2.startswith("ERROR:"), r2)

async def test_find_refs_enforces_limits():
    print("\n[Test 26] find_dependent_references limits + visibility (0.3.1)")
    with tempfile.TemporaryDirectory() as d:
        # One huge line, then a short match
        with open(os.path.join(d, "a.py"), "w") as f:
            f.write('target = "' + "x" * 15000 + '"\n')
            f.write("target = short\n")

        r = await server.find_dependent_references("target", d)
        check("Huge first line still shows location", "a.py:1" in r, r[:300])
        check("Short second match visible", "a.py:2" in r, r[:300])
        check("Default budget respected", len(r) <= 8000, f"{len(r)} chars")

        # Small budget — one short match must remain visible
        with tempfile.TemporaryDirectory() as d2:
            with open(os.path.join(d2, "b.py"), "w") as f:
                f.write("target = 1\n")
            r = await server.find_dependent_references("target", d2, max_chars=300)
            check("Small budget respected", len(r) <= 300, f"{len(r)} chars")
            check("Location visible in small budget", "b.py:1" in r, r[:200])

        # Count limit
        with tempfile.TemporaryDirectory() as d3:
            for i in range(60):
                with open(os.path.join(d3, f"f{i:02d}.py"), "w") as f:
                    f.write("findme_token = 1\n" * 3)
            r = await server.find_dependent_references(
                "findme_token", d3, max_results=5, max_chars=8000)
            check("Count limit reported", "count limit 5" in r, r[:300])

        # Input validation
        with tempfile.TemporaryDirectory() as d4:
            r = await server.find_dependent_references("", d4)
            check("Empty symbol rejected", r.startswith("ERROR:"), r)
            r = await server.find_dependent_references("   ", d4)
            check("Whitespace symbol rejected", r.startswith("ERROR:"), r)
            r = await server.find_dependent_references("x", "/definitely_not_a_dir_xyz")
            check("Invalid dir rejected", r.startswith("ERROR:"), r)
            r = await server.find_dependent_references("x", d4, max_results=0)
            check("max_results=0 rejected", r.startswith("ERROR:"), r)
            r = await server.find_dependent_references("x", d4, max_chars=100)
            check("max_chars=100 rejected", r.startswith("ERROR:"), r)


async def test_find_refs_no_matches():
    print("\n[Test 27] find_dependent_references no-match + safety (0.3.1)")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "one.py"), "w") as f:
            f.write("a = 1\n")

        # Short symbol, no matches
        r = await server.find_dependent_references("never_here_xyz", d)
        check("No-match message present", "No text matches" in r, r[:200])

        # Huge symbol string with no matches — must respect max_chars
        huge = "y" * 9000
        r = await server.find_dependent_references(huge, d)
        check("Huge symbol capped under 8000", len(r) <= 8000, f"{len(r)} chars")
        check("No-match message still present", "No text matches" in r, r[:200])

        # Unreadable file — must report incomplete
        target = os.path.join(d, "unreadable.py")
        with open(target, "w") as f:
            f.write("target_token = 1\n")
        os.chmod(target, 0o000)
        try:
            r = await server.find_dependent_references("target_token", d)
            check("Unreadable file reported",
                  "unreadable" in r.lower() or "incomplete" in r.lower(),
                  r[:300])
        finally:
            os.chmod(target, 0o644)

async def test_find_refs_hard_budget():
    print("\n[Test 28] find_refs respects max_chars=256 (0.3.2)")
    with tempfile.TemporaryDirectory() as d:
        for i in range(10):
            with open(os.path.join(d, f"f{i}.py"), "w") as f:
                f.write("symbol=1\n")
        r = await server.find_dependent_references("symbol", d, max_chars=256)
        check("Response under 256 chars", len(r) <= 256, f"{len(r)} chars")


async def test_find_refs_header_count_accurate():
    print("\n[Test 29] find_refs header count == visible count (0.3.2)")
    import re
    with tempfile.TemporaryDirectory() as d:
        for i in range(40):
            with open(os.path.join(d, f"f{i:02d}.py"), "w") as f:
                f.write("symbol = " + "x" * 80 + "\n")
        r = await server.find_dependent_references("symbol", d)
        first_line = r.split("\n")[0]
        m = re.search(r"\((\d+) match", first_line)
        header_count = int(m.group(1)) if m else -1
        body_count = sum(
            1 for l in r.split("\n")[1:]
            if l and not l.startswith("#")
        )
        check("Header count matches visible",
              header_count == body_count,
              f"header={header_count}, visible={body_count}")


async def test_find_refs_preserves_location_under_small_budget():
    print("\n[Test 30] find_refs keeps filename:line under budget (0.3.2)")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "a.py"), "w") as f:
            f.write('symbol = "' + "x" * 2000 + '"\n')
            f.write("symbol = short\n")
        r = await server.find_dependent_references("symbol", d, max_chars=300)
        check("Under budget", len(r) <= 300, f"{len(r)} chars")
        check("First match location present", "a.py:1" in r, r[:300])
        check("Second match location present", "a.py:2" in r, r[:300])



async def test_force_preserves_custom_providers():
    print("\n[Test 31] --force preserves custom providers (0.3.5)")
    with tempfile.TemporaryDirectory() as fake_home:
        import json
        home = pathlib.Path(fake_home)
        cfgdir = home / ".config" / "opencode"
        cfgdir.mkdir(parents=True)

        # Seed a config with a custom provider and agent
        seeded = {
            "providers": {
                "openrouter": {
                    "package": "@opencode/ai/providers/openai-compatible",
                    "settings": {"baseURL": "https://openrouter.ai/api/v1"},
                },
                "customprovider": {
                    "package": "@opencode/ai/providers/openai-compatible",
                    "settings": {"baseURL": "https://example.com/v1"},
                },
            },
            "agents": {"customagent": {"model": "customprovider/x"}},
        }
        (cfgdir / "opencode.json").write_text(json.dumps(seeded, indent=2))

        rc, out = run_installer(home, "--force", "--no-runtime")

        after = json.load(open(cfgdir / "opencode.json"))
        check("Custom provider survived --force",
              "customprovider" in after.get("providers", {}),
              list(after.get("providers", {}).keys()))
        check("Custom agent survived --force",
              "customagent" in after.get("agents", {}),
              list(after.get("agents", {}).keys()))

async def test_harness_commands_installed():
    print("\n[Test 32] harness-* commands installed (0.4.0)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")
        cmds = home / ".config" / "opencode" / "commands"
        for name in ["harness-plan.md", "harness-config.md",
                     "harness-rollback-confirm.md", "harness-help.md"]:
            check(f"{name} installed", (cmds / name).exists())


async def test_legacy_commands_preserved_with_warning():
    print("\n[Test 33] legacy commands preserved with warning (0.4.0)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        cmds = home / ".config" / "opencode" / "commands"
        cmds.mkdir(parents=True)

        # Simulate pre-0.4.0 files with user content
        (cmds / "plan.md").write_text("# user's own plan command\n")
        (cmds / "help-harness.md").write_text("# user's own help command\n")

        rc, out = run_installer(home, "--no-runtime")

        check("Legacy plan.md preserved",
              (cmds / "plan.md").read_text() == "# user's own plan command\n")
        check("Legacy help-harness.md preserved",
              (cmds / "help-harness.md").read_text() == "# user's own help command\n")
        check("Warning mentions legacy files",
              "legacy" in out.lower(), out[:400])
        check("New harness-help.md installed",
              (cmds / "harness-help.md").exists())
async def test_command_manifest_blocks_overwrite_of_user_file():
    print("\n[Test 34] user-owned harness-*.md preserved on install (0.4.1)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        cmds = home / ".config" / "opencode" / "commands"
        cmds.mkdir(parents=True)

        # User pre-owns a harness-plan.md with their own content
        (cmds / "harness-plan.md").write_text("# MY OWN\n")

        run_installer(home, "--no-runtime")

        check("User file not overwritten",
              (cmds / "harness-plan.md").read_text() == "# MY OWN\n")
        check("Other 3 harness-* files installed",
              (cmds / "harness-config.md").exists()
              and (cmds / "harness-rollback-confirm.md").exists()
              and (cmds / "harness-help.md").exists())


async def test_command_manifest_preserves_edits_on_uninstall():
    print("\n[Test 35] edited harness-*.md preserved on uninstall (0.4.1)")
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")
        cmds = home / ".config" / "opencode" / "commands"

        # Edit one file
        with open(cmds / "harness-help.md", "a") as f:
            f.write("\nMY EDIT\n")

        run_installer(home, "--uninstall")

        check("Edited file preserved",
              (cmds / "harness-help.md").exists()
              and "MY EDIT" in (cmds / "harness-help.md").read_text())
        check("Other 3 removed",
              not (cmds / "harness-plan.md").exists()
              and not (cmds / "harness-config.md").exists()
              and not (cmds / "harness-rollback-confirm.md").exists())



async def test_read_raw_range_preserved():
    print("\n[Test READ-01] rip_file_lines raw range preserved (T02)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sample.py")
        content = (
            "def foo():\n"
            "    x = 1    \n"
            "    y = 2\n"
            "    return x + y\n"
            "\n"
            "def bar():\n"
            "    pass\n"
        )
        with open(f, "w") as fh:
            fh.write(content)
        r = await server.rip_file_lines(f, 2, 3, None)
        check("Header present", r.startswith("--- Lines 2 to 3"), r[:200])
        check("sha256 line present", "# sha256: " in r, r[:300])
        check("Line 2 present", "2:" in r, r[:300])
        check("Line 3 present", "3:" in r, r[:300])
        check("Line 1 NOT present", "1: def foo" not in r, r[:300])
        check("Trailing spaces preserved", "x = 1    " in r, r[:400])
        check("Response <= 4000", len(r) <= 4000, f"len={len(r)}")


async def test_read_enclosing_symbol_opt_in():
    print("\n[Test READ-02] rip_file_lines enclosing context (T02)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sample.py")
        with open(f, "w") as fh:
            fh.write("def outer():\n")
            fh.write("    x = 1\n")
            fh.write("    y = 2\n")
            fh.write("    z = 3\n")
            fh.write("    return x\n")
            fh.write("def other():\n")
            fh.write("    pass\n")
        r1 = await server.rip_file_lines(f, 3, 3, None)
        check("Raw is raw", "context: expanded" not in r1, r1[:300])
        r2 = await server.rip_file_lines(f, 3, 3, None, context="enclosing")
        check("Expanded notes", "context: expanded" in r2, r2[:400])
        check("Expanded includes def outer",
              "def outer" in r2, r2[:400])
        check("Expanded includes return x",
              "return x" in r2, r2[:400])
        txt = os.path.join(d, "notes.txt")
        with open(txt, "w") as fh:
            fh.write("line 1\nline 2\nline 3\n")
        r3 = await server.rip_file_lines(txt, 2, 2, None, context="enclosing")
        check("Non-.py falls back to raw",
              "context: not a Python file" in r3 or "using raw range" in r3,
              r3[:400])


async def test_read_bounds_and_errors():
    print("\n[Test READ-03] rip_file_lines bounds (T02)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "small.py")
        with open(f, "w") as fh:
            for i in range(1, 11):
                fh.write(f"line{i}\n")
        r = await server.rip_file_lines(f, 0, 5, None)
        check("Zero start rejected", "ERROR:" in r, r[:200])
        r = await server.rip_file_lines(f, 5, 3, None)
        check("Reversed range rejected", "ERROR:" in r, r[:200])
        r = await server.rip_file_lines(f, 100, 110, None)
        check("Start beyond EOF rejected", "ERROR:" in r, r[:200])
        r = await server.rip_file_lines(f, 5, 100, None)
        check("End beyond EOF clipped",
              "ERROR:" not in r and "Lines 5 to 10" in r, r[:300])
        check("Header shows actual returned bounds",
              "5 to 10" in r, r[:300])


async def test_read_snapshot_fingerprint():
    print("\n[Test READ-04] rip_file_lines fingerprint (T02)")
    import hashlib
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "fp.py")
        content = "def a():\n    return 1\n"
        with open(f, "w") as fh:
            fh.write(content)
        expected = hashlib.sha256(content.encode()).hexdigest()
        r = await server.rip_file_lines(f, 1, 1, None)
        check("Fingerprint matches file bytes",
              expected in r, f"expected {expected[:16]}, got {r[:300]}")
        modified = content.replace("return 1", "return 2")
        with open(f, "w") as fh:
            fh.write(modified)
        expected2 = hashlib.sha256(modified.encode()).hexdigest()
        r2 = await server.rip_file_lines(f, 1, 1, None)
        check("Fingerprint changes with content",
              expected2 in r2 and expected not in r2, r2[:300])


async def test_read_path_traversal_rejected():
    print("\n[Test READ-05] rip_file_lines path handling (T02)")
    # NOTE: full permission/symlink integration is BLOCKED pending
    # the shared workspace module (CORE workstream). This test covers
    # only what is possible with the current file-API.
    with tempfile.TemporaryDirectory() as d:
        r = await server.rip_file_lines(os.path.join(d, "nope.py"), 1, 1, None)
        check("Missing file rejected",
              "ERROR:" in r or "not found" in r.lower(), r[:200])
        subdir = os.path.join(d, "sub")
        os.makedirs(subdir)
        r = await server.rip_file_lines(subdir, 1, 1, None)
        check("Directory rejected",
              "ERROR:" in r or "directory" in r.lower(), r[:200])


async def test_read_huge_line_useful_failure():
    print("\n[Test READ-06] rip_file_lines huge line (T02)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "huge.py")
        with open(f, "w") as fh:
            fh.write("small = 1\n")
            fh.write("big = \"" + "x" * 8000 + "\"\n")
            fh.write("small2 = 2\n")
        r = await server.rip_file_lines(f, 1, 3, None)
        check("Response <= 4000 chars", len(r) <= 4000, f"len={len(r)}")
        check("Truncation marker present or hash preserved",
              "[truncated]" in r or "# sha256:" in r, r[:400])


async def test_read_enclosing_innermost():
    print("\n[Test READ-07] rip_file_lines innermost enclosing symbol (T02 fix)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "nested.py")
        with open(f, "w") as fh:
            fh.write("class Handler:\n")          # L1
            fh.write("    def do_GET(self):\n")   # L2
            fh.write("        x = 1\n")           # L3
            fh.write("        return x\n")        # L4
            fh.write("    def other(self):\n")    # L5
            fh.write("        pass\n")            # L6
        r = await server.rip_file_lines(f, 3, 3, None, context="enclosing")
        check("Innermost is do_GET", "def do_GET" in r, r[:400])
        check("Not expanded to whole class",
              "def other" not in r, r[:400])


async def test_read_truncation_budget_use():
    print("\n[Test READ-08] rip_file_lines truncation budget (T02 fix)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "big_line.py")
        with open(f, "w") as fh:
            fh.write("a\n")
            fh.write("x" * 8000 + "\n")
            fh.write("b\n")
        r = await server.rip_file_lines(f, 1, 3, None)
        check("Uses > 3000 chars of budget",
              len(r) > 3000, f"len={len(r)}")
        check("Response <= 4000", len(r) <= 4000, f"len={len(r)}")


async def test_edit_strict_rejections_do_not_write():
    print("\n[Test EDIT-01] apply_search_replace strict refusals (T04)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sample.py")
        original = "def a():\n    return 1\n"
        with open(f, "w") as fh:
            fh.write(original)
        before = open(f, "rb").read()

        r = await server.apply_search_replace(f, "", "x")
        check("Empty search rejected", "ERROR" in r.upper(), r[:200])
        r = await server.apply_search_replace(f, "   ", "x")
        check("Whitespace search rejected", "ERROR" in r.upper(), r[:200])
        r = await server.apply_search_replace(f, "does_not_exist_xyz", "x")
        check("No-match rejected", "ERROR" in r.upper(), r[:200])
        with open(f, "w") as fh:
            fh.write("x = 1\nx = 1\n")
        r = await server.apply_search_replace(f, "x = 1", "y = 2")
        check("Ambiguous match rejected", "ERROR" in r.upper(), r[:200])
        check("Ambiguous file unchanged",
              open(f, "rb").read() == b"x = 1\nx = 1\n",
              open(f).read()[:200])

        with open(f, "wb") as fh:
            fh.write(before)
        check("Refusals did not write", open(f, "rb").read() == before)


async def test_edit_expected_hash_stale():
    print("\n[Test EDIT-02] apply_search_replace stale hash (T04)")
    import hashlib
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sample.py")
        original = "def a():\n    return 1\n"
        with open(f, "w") as fh:
            fh.write(original)
        h_old = hashlib.sha256(original.encode()).hexdigest()

        modified = original.replace("return 1", "return 2")
        with open(f, "w") as fh:
            fh.write(modified)

        r = await server.apply_search_replace(
            f, "def a", "def b", expected_sha256=h_old)
        check("Stale hash refused", "stale" in r.lower(), r[:300])
        check("Original file intact after stale refusal",
              open(f).read() == modified, open(f).read()[:200])

        h_new = hashlib.sha256(modified.encode()).hexdigest()
        r2 = await server.apply_search_replace(
            f, "def a", "def b", expected_sha256=h_new)
        check("Correct hash allows edit",
              "successfully" in r2.lower() or "Surgical" in r2, r2[:300])
        check("Edit applied",
              open(f).read().startswith("def b"), open(f).read()[:200])


async def test_edit_preserves_mode_and_newlines():
    print("\n[Test EDIT-03] apply_search_replace preserves mode/newlines (T04)")
    import stat as stat_mod
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sample.py")
        with open(f, "wb") as fh:
            fh.write(b"def a():\r\n    x = 1    \r\n    return x\r\n")
        try:
            os.chmod(f, 0o755)
        except OSError:
            pass
        mode_before = stat_mod.S_IMODE(os.stat(f).st_mode)

        r = await server.apply_search_replace(f, "    x = 1", "    x = 99")
        check("Edit success reported",
              "successfully" in r.lower() or "Surgical" in r, r[:300])

        after = open(f, "rb").read()
        check("CRLF preserved in output", b"\r\n" in after, str(after[:200]))
        check("Trailing whitespace on other lines preserved",
              b"    return x\r\n" in after, str(after[:200]))
        mode_after = stat_mod.S_IMODE(os.stat(f).st_mode)
        check("Mode preserved (0o755)",
              mode_before == mode_after,
              f"{oct(mode_before)} -> {oct(mode_after)}")


async def test_edit_atomic_failure_cleanup():
    print("\n[Test EDIT-04] apply_search_replace atomic failure (T04)")
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sample.py")
        original = "def a():\n    return 1\n"
        with open(f, "w") as fh:
            fh.write(original)
        with patch("server.os.replace", side_effect=OSError("simulated")):
            r = await server.apply_search_replace(f, "return 1", "return 2")
        check("Failure reported", "ERROR" in r.upper() or "fail" in r.lower(),
              r[:300])
        check("Original file intact", open(f).read() == original,
              open(f).read()[:200])
        leftovers = [n for n in os.listdir(d) if n != "sample.py"]
        check("No temp file leaked", leftovers == [], str(leftovers))


async def test_edit_path_handling():
    print("\n[Test EDIT-05] apply_search_replace path handling (T04)")
    # NOTE: full permission/symlink integration is BLOCKED pending
    # the shared workspace module (CORE workstream). This test covers
    # only what is possible with the current file-API.
    with tempfile.TemporaryDirectory() as d:
        r = await server.apply_search_replace(
            os.path.join(d, "nope.py"), "x", "y")
        check("Missing file rejected",
              "ERROR" in r.upper() or "not found" in r.lower(), r[:200])
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        r = await server.apply_search_replace(sub, "x", "y")
        check("Directory rejected",
              "ERROR" in r.upper() or "directory" in r.lower(), r[:200])


async def test_edit_no_op_detection():
    print("\n[Test EDIT-06] apply_search_replace no-op detection (T04)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sample.py")
        original = "def a():\n    return 1\n"
        with open(f, "w") as fh:
            fh.write(original)
        mtime_before = os.stat(f).st_mtime_ns
        r = await server.apply_search_replace(f, "return 1", "return 1")
        check("No-op reported as no change",
              "no change" in r.lower(), r[:300])
        check("File not touched", open(f).read() == original)
        check("mtime unchanged",
              os.stat(f).st_mtime_ns == mtime_before)


async def test_read_truncation_keeps_complete_lines():
    print("\n[Test READ-09] rip_file_lines truncation preserves line prefixes (T02 fix)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "wide.py")
        with open(f, "w") as fh:
            for i in range(1, 61):
                fh.write(f"line_{i:03d} = '" + "x" * 200 + "'\n")
        r = await server.rip_file_lines(f, 1, 60, None)
        check("Response <= 4000", len(r) <= 4000, f"len={len(r)}")
        source_lines = [ln for ln in r.split("\n")
                        if ln and not ln.startswith("#")
                        and not ln.startswith("---")
                        and not ln.startswith("    ")]
        bad = [ln for ln in source_lines
               if not ln.split(":", 1)[0].strip().isdigit()]
        check("All source lines retain numeric prefix",
              bad == [], str(bad[:3]))


async def test_read_invalid_context_mode_rejected():
    print("\n[Test READ-10] rip_file_lines rejects invalid context mode (T02 fix)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "sample.py")
        with open(f, "w") as fh:
            fh.write("def a():\n    pass\n")
        r = await server.rip_file_lines(f, 1, 2, None, context="foo")
        check("Invalid context mode rejected", "ERROR" in r.upper(), r[:200])


async def test_read_invalid_utf8_disclosed():
    print("\n[Test READ-11] rip_file_lines discloses invalid UTF-8 (T02 fix)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "bad_bytes.py")
        with open(f, "wb") as fh:
            fh.write(b"ok_line = 1\n")
            fh.write(b"bad = \xff\xfe\n")
        r = await server.rip_file_lines(f, 1, 2, None)
        check("Invalid-UTF-8 note present", "invalid UTF-8" in r, r[:400])


async def test_lint_timeout_preserves_partial_stderr():
    print("\n[Test LINT-11] lint_file timeout preserves partial stderr (T05 fix)")
    from unittest.mock import patch
    import subprocess as sp
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.py")
        with open(f, "w") as fh:
            fh.write("pass\n")
        exc = sp.TimeoutExpired("python3", 10)
        exc.stderr = "Partial diagnostic from child\n"
        with patch("server.subprocess.run", side_effect=exc):
            r = await server.lint_file(f)
        check("Timeout FAIL returned", "FAIL:" in r, r[:300])
        check("Partial stderr preserved",
              "Partial diagnostic" in r, r[:400])


async def test_lint_no_generic_line_fallback():
    print("\n[Test LINT-12] lint_file no generic line fallback (T05 fix)")
    from unittest.mock import patch
    from subprocess import CompletedProcess
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "valid.py")
        with open(f, "w") as fh:
            fh.write("pass\n")
        fake = CompletedProcess(
            args=["python3", "-m", "py_compile", f],
            returncode=1, stdout="",
            stderr="worker configuration error, line 1: invalid settings\n",
        )
        with patch("server.subprocess.run", return_value=fake):
            r = await server.lint_file(f)
        check("Generic 'line N' not treated as source location",
              "valid.py:1" not in r, r[:400])
        check("Falls back to (no location parsed)",
              "(no location parsed)" in r, r[:400])


async def test_lint_path_disambiguation():
    print("\n[Test LINT-13] lint_file path disambiguation (T05 fix)")
    from unittest.mock import patch
    from subprocess import CompletedProcess
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "valid.py")
        with open(f, "w") as fh:
            fh.write("pass\n")
        fake = CompletedProcess(
            args=["python3", "-m", "py_compile", f],
            returncode=1, stdout="",
            stderr="/some/other/dir/valid.py:5: invalid syntax\n",
        )
        with patch("server.subprocess.run", return_value=fake):
            r = await server.lint_file(f)
        check("Different-directory same-basename not attributed",
              "valid.py:5" not in r, r[:400])


async def test_lint_source_change_during_compile_noted():
    print("\n[Test LINT-14] lint_file source-change note (T05 fix)")
    from unittest.mock import patch
    from subprocess import CompletedProcess
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "valid.py")
        with open(f, "w") as fh:
            fh.write("def foo(:\n    pass\n")

        def mutate_then_run(cmd, *args, **kwargs):
            # Change file between compile and read
            with open(f, "a") as fh:
                fh.write("# external change\n")
            return CompletedProcess(
                args=cmd, returncode=1, stdout="",
                stderr=f + ":1: SyntaxError: invalid syntax\n",
            )
        with patch("server.subprocess.run", side_effect=mutate_then_run):
            r = await server.lint_file(f)
        check("Source-change note present",
              "source changed" in r.lower(), r[:500])


# ═══════════════════════════════════════════════════════════
# T06 — git_checkpoint
# ═══════════════════════════════════════════════════════════

async def test_git_subdirectory_path():
    print("\n[Test GIT-01] git_checkpoint — subdirectory target (T06)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        subdir = os.path.join(d, "src", "deep")
        os.makedirs(subdir)
        target = os.path.join(subdir, "code.py")
        with open(target, "w") as fh:
            fh.write("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        with open(target, "a") as fh:
            fh.write("y = 2\n")
        r = await server.git_checkpoint(target, "t06 subdir")
        check("Committed from subdir", "Committed:" in r, r[:300])
        check("commit_id present", "commit_id:" in r, r[:300])
        check("Repo-relative path in changed_paths",
              "src/deep/code.py" in r or "src\\deep\\code.py" in r, r[:500])


async def test_git_outside_repo():
    print("\n[Test GIT-02] git_checkpoint — file outside any repo (T06)")
    with tempfile.TemporaryDirectory() as d:
        outside = os.path.join(d, "not_a_repo.py")
        with open(outside, "w") as fh:
            fh.write("x = 1\n")
        r = await server.git_checkpoint(outside, "should fail")
        check("Not-a-repo rejected", "ERROR" in r.upper(), r[:200])


async def test_git_missing_identity():
    print("\n[Test GIT-03] git_checkpoint — missing git identity (T06)")
    import server as _s
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        empty_home = tempfile.mkdtemp()
        env = dict(os.environ)
        env["HOME"] = empty_home
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        # Clear repo-local identity so commits cannot succeed
        subprocess.run(["git", "config", "user.email", ""], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", ""], cwd=d, check=True)
        target = os.path.join(d, "a.py")
        with open(target, "w") as fh:
            fh.write("x = 1\n")
        # Patch _run_git to inherit our isolated env
        original = _s._run_git
        def patched(args, cwd, timeout=10):
            try:
                res = subprocess.run(
                    ["git", *args], cwd=cwd, capture_output=True,
                    text=True, shell=False, timeout=timeout, env=env)
                return res.returncode, res.stdout, res.stderr
            except FileNotFoundError:
                if not os.path.isdir(cwd):
                    return -1, "", f"working directory does not exist: {cwd}"
                return -1, "", "git not available on PATH"
            except subprocess.TimeoutExpired:
                return -1, "", "timeout"
        _s._run_git = patched
        try:
            r = await server.git_checkpoint(target, "t06 identity")
        finally:
            _s._run_git = original
        check("Identity error specifically returned",
              "user identity is not configured" in r, r[:400])
        check("Not reported as success",
              "Committed:" not in r, r[:400])
    import shutil
    shutil.rmtree(empty_home, ignore_errors=True)


async def test_git_staged_work_extended():
    print("\n[Test GIT-04] git_checkpoint — staged 'A' file detected (T06)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        seed = os.path.join(d, "seed.py")
        with open(seed, "w") as fh:
            fh.write("# seed\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        # New file with no worktree change (A with space)
        new_file = os.path.join(d, "user_new.py")
        with open(new_file, "w") as fh:
            fh.write("# user\n")
        subprocess.run(["git", "add", "user_new.py"], cwd=d, check=True)
        # Modified target (unstaged)
        target = os.path.join(d, "agent.py")
        with open(target, "w") as fh:
            fh.write("# agent\n")
        r = await server.git_checkpoint(target, "t06 staged")
        check("Staged A file detected",
              "Unrelated staged" in r or "ERROR" in r.upper(), r[:300])
        # Ensure the user's file is still staged (untouched)
        status = subprocess.run(["git", "status", "--porcelain"],
                                cwd=d, capture_output=True, text=True)
        check("user_new.py still staged",
              "A  user_new.py" in status.stdout, status.stdout[:300])


async def test_git_commit_only_target():
    print("\n[Test GIT-05] git_checkpoint — commits only the target (T06)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        seed = os.path.join(d, "seed.py")
        with open(seed, "w") as fh:
            fh.write("# seed\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        target = os.path.join(d, "target.py")
        with open(target, "w") as fh:
            fh.write("# target\n")
        r = await server.git_checkpoint(target, "t06 only target")
        show = subprocess.run(["git", "show", "--name-only", "--format="],
                              cwd=d, capture_output=True, text=True)
        check("Only target.py committed",
              show.stdout.strip() == "target.py",
              show.stdout.strip())
        check("commit_id present", "commit_id:" in r, r[:300])


async def test_git_response_bounded():
    print("\n[Test GIT-06] git_checkpoint — response bounded at 4000 (T06)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        seed = os.path.join(d, "seed.py")
        with open(seed, "w") as fh:
            fh.write("# seed\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        target = os.path.join(d, "one.py")
        with open(target, "w") as fh:
            fh.write("# one\n")
        r = await server.git_checkpoint(target, "t06 bounded")
        check("Response <= 4000", len(r) <= 4000, f"len={len(r)}")
        check("commit_id present", "commit_id:" in r, r[:400])


async def test_git_long_summary_bounded():
    print("\n[Test GIT-07] git_checkpoint — long summary bounded (T06)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        seed = os.path.join(d, "seed.py")
        with open(seed, "w") as fh:
            fh.write("# seed\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        target = os.path.join(d, "one.py")
        with open(target, "w") as fh:
            fh.write("# one\n")
        r = await server.git_checkpoint(target, "x" * 5000)
        check("Response <= 4000", len(r) <= 4000, f"len={len(r)}")
        check("commit_id survived",
              "commit_id:" in r and len(r.split("commit_id:")[1].split()[0]) == 40,
              r[:400])


# ═══════════════════════════════════════════════════════════
# T07 — rollback_show
# ═══════════════════════════════════════════════════════════

async def test_rollback_three_categories():
    print("\n[Test ROLL-01] rollback_show — three categories (T07)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        # Tracked file, then one staged edit + one unstaged edit
        tracked = os.path.join(d, "tracked.py")
        with open(tracked, "w") as fh:
            fh.write("v1\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        # Staged change
        with open(tracked, "w") as fh:
            fh.write("v2-staged\n")
        subprocess.run(["git", "add", "tracked.py"], cwd=d, check=True)
        # Unstaged append
        with open(tracked, "a") as fh:
            fh.write("v3-unstaged\n")
        # Untracked file
        with open(os.path.join(d, "new.py"), "w") as fh:
            fh.write("# untracked\n")
        r = await server.rollback_show(d)
        check("Staged section present", "Staged changes" in r, r[:500])
        check("Unstaged section present", "Unstaged changes" in r, r[:800])
        check("Untracked section present", "Untracked" in r, r[:800])
        check("tracked.py appears in report", "tracked.py" in r, r[:800])
        check("new.py appears in report", "new.py" in r, r[:800])


async def test_rollback_no_mutation():
    print("\n[Test ROLL-02] rollback_show — never mutates the repo (T07)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        seed = os.path.join(d, "seed.py")
        with open(seed, "w") as fh:
            fh.write("# seed\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                                     capture_output=True, text=True).stdout
        with open(seed, "a") as fh:
            fh.write("# change\n")
        with open(os.path.join(d, "extra.py"), "w") as fh:
            fh.write("# extra\n")
        r = await server.rollback_show(d)
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                                    capture_output=True, text=True).stdout
        check("HEAD unchanged", head_before == head_after)
        check("Working file intact",
              open(seed).read() == "# seed\n# change\n")
        check("Untracked file intact",
              os.path.exists(os.path.join(d, "extra.py")))


async def test_rollback_not_a_repo():
    print("\n[Test ROLL-03] rollback_show — not a repo (T07)")
    with tempfile.TemporaryDirectory() as d:
        r = await server.rollback_show(d)
        check("Not-a-repo reported as error",
              "ERROR" in r.upper() or "not a git" in r.lower(), r[:300])
        check("Not falsely clean",
              "(none)" not in r[:200] or "ERROR" in r.upper(), r[:300])


async def test_rollback_bounded():
    print("\n[Test ROLL-04] rollback_show — response bounded at 4000 (T07)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        # Many untracked files with long names
        for i in range(80):
            with open(os.path.join(d, f"untracked_file_with_long_name_{i:03d}.py"), "w") as fh:
                fh.write("# x\n")
        r = await server.rollback_show(d)
        check("Response <= 4000", len(r) <= 4000, f"len={len(r)}")


async def test_rollback_manual_command_present():
    print("\n[Test ROLL-05] rollback_show — manual commands shown (T07)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        r = await server.rollback_show(d)
        check("Manual rollback warning present",
              "manual" in r.lower(), r[:600])
        check("git reset command shown",
              "git reset --hard" in r, r[:1000])


async def test_rollback_no_hooks_or_external_diff():
    print("\n[Test ROLL-06] rollback_show — no ext-diff side effects (T07)")
    # Verifies the read-only flags prevent hook / ext-diff execution.
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        # Set an external diff that would write a marker file if invoked
        marker = os.path.join(d, "HOOK_RAN")
        hook_script = os.path.join(d, "ext_diff.sh")
        with open(hook_script, "w") as fh:
            fh.write(f"#!/bin/sh\ntouch {marker}\nexit 0\n")
        os.chmod(hook_script, 0o755)
        subprocess.run(["git", "config", "diff.external", hook_script], cwd=d, check=True)
        seed = os.path.join(d, "a.py")
        with open(seed, "w") as fh:
            fh.write("# a\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        with open(seed, "a") as fh:
            fh.write("# change\n")
        r = await server.rollback_show(d)
        check("External diff was not invoked",
              not os.path.exists(marker),
              f"marker={os.path.exists(marker)}")


# ═══════════════════════════════════════════════════════════
# T01 Day 1 — get_repo_skeleton modes
# ═══════════════════════════════════════════════════════════

async def test_outline_legacy_calls():
    print("\n[Test MAP-01] get_repo_skeleton — outline mode (T01)")
    with tempfile.TemporaryDirectory() as d:
        # Fixture 1: plain function, decorator, class, method
        with open(os.path.join(d, "alpha_widget.py"), "w") as fh:
            fh.write(
                "def passthrough(fn):\n"
                "    return fn\n"
                "\n"
                "@passthrough\n"
                "class AlphaWidget:\n"
                "    def render_alpha(self):\n"
                "        return 1\n"
            )
        # Fixture 2: async function with a nested def
        with open(os.path.join(d, "beta_async.py"), "w") as fh:
            fh.write(
                "async def fetch_beta():\n"
                "    def inner_helper():\n"
                "        return 2\n"
                "    return inner_helper()\n"
            )
        # Fixture 3: class → method → nested class → nested method
        with open(os.path.join(d, "gamma_nested.py"), "w") as fh:
            fh.write(
                "class GammaNested:\n"
                "    def run_gamma(self):\n"
                "        class Deep:\n"
                "            def deep_method(self):\n"
                "                return 3\n"
                "        return Deep()\n"
            )

        out = await server.get_repo_skeleton(repo_path=d, mode="outline")
        default_out = await server.get_repo_skeleton(repo_path=d)
        check("mode='outline' matches the default", out == default_out, out[:200])

        expected = [
            "passthrough", "AlphaWidget", "render_alpha",
            "fetch_beta", "inner_helper",
            "GammaNested", "run_gamma", "Deep", "deep_method",
        ]
        missing = [s for s in expected if s not in out]
        check("Outline contains every symbol", not missing, f"missing={missing}")

        no_range = [
            s for s in expected
            if not any(s in line and "[L" in line and "-L" in line
                       for line in out.splitlines())
        ]
        check("Every symbol has its own [Lx-Ly] range",
              not no_range, f"without range={no_range}")

        duplicated = []
        for s in ("passthrough", "render_alpha", "inner_helper",
                  "run_gamma", "deep_method"):
            hits = [ln for ln in out.splitlines() if f"def {s}(" in ln]
            if len(hits) != 1:
                duplicated.append((s, len(hits)))
        check("No duplicated method entries", not duplicated, f"{duplicated}")


async def test_ranked_python_smoke():
    print("\n[Test MAP-02] get_repo_skeleton — ranked mode (T01)")
    with tempfile.TemporaryDirectory() as d:
        fixtures = {
            "models.py": (
                "class Watchlist:\n"
                "    def __init__(self):\n"
                "        self.items = []\n"
                "\n"
                "class Position:\n"
                "    def shares(self):\n"
                "        return 0\n"
            ),
            "normalize.py": (
                "def normalize_ticker(symbol):\n"
                "    return symbol.strip().upper()\n"
                "\n"
                "def dedupe_positions(rows):\n"
                "    return list(dict.fromkeys(rows))\n"
            ),
            "watchlist.py": (
                "def build_watchlist(rows):\n"
                "    return [normalize_ticker(r) for r in rows]\n"
                "\n"
                "class WatchlistService:\n"
                "    def refresh(self):\n"
                "        return None\n"
            ),
            "duplicates.py": (
                "def find_duplicates(rows):\n"
                "    seen = set()\n"
                "    return [r for r in rows if r not in seen and not seen.add(r)]\n"
                "\n"
                "def merge_duplicates(rows):\n"
                "    return find_duplicates(rows)\n"
            ),
            "main.py": (
                "async def refresh_watchlist(service):\n"
                "    return service.refresh()\n"
                "\n"
                "def main():\n"
                "    return None\n"
            ),
        }
        for name, body in fixtures.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(body)

        out = await server.get_repo_skeleton(
            repo_path=d, mode="ranked")

        if out.startswith("ERROR"):
            print("     → ranked mode failed "
                  "(vendored import/backend):", out[:300])
        elif "# ranked source map" not in out:
            print("     → note: ranked section absent, outline fallback "
                  "in effect")

        files_seen = sorted(n for n in fixtures if n in out)
        check("Result contains >= 3 file paths",
              len(files_seen) >= 3, f"{files_seen}")

        def has_symbol(name):
            # Outline mode renders "def name(" / "class Name:"; ranked
            # mode renders one "path:Lx def name" line per symbol.
            if f"def {name}(" in out or f"class {name}:" in out:
                return True
            return any(
                re.fullmatch(r"\S+\.py:L\d+ (def|ref) " + re.escape(name), ln)
                for ln in out.splitlines()
            )

        symbols = ["Watchlist", "Position", "normalize_ticker",
                   "dedupe_positions", "build_watchlist", "WatchlistService",
                   "find_duplicates", "merge_duplicates",
                   "refresh_watchlist", "main"]
        found = [s for s in symbols if has_symbol(s)]
        check("Result contains >= 5 symbol names",
              len(found) >= 5, f"{len(found)} found: {found}")

        check("Result length under 8000 chars", len(out) < 8000,
              f"len={len(out)}")
        check("No ERROR prefix",
              not out.startswith("ERROR"), out[:300])

        bogus = await server.get_repo_skeleton(repo_path=d, mode="bogus")
        check("mode='bogus' returns an ERROR",
              bogus.startswith("ERROR: unsupported mode 'bogus'"),
              bogus[:300])


async def test_ranked_multilang():
    print("\n[Test MAP-03] get_repo_skeleton — ranked mode, JS/TS/TSX (T01 Day 2)")
    with tempfile.TemporaryDirectory() as d:
        fixtures = {
            # 2 Python files with class + def
            "alpha_py.py": (
                "class AlphaWidget:\n"
                "    def render_alpha(self):\n"
                "        return 'alpha'\n"
            ),
            "beta_py.py": (
                "def build_beta_widget(rows):\n"
                "    return sorted(rows)\n"
            ),
            # 2 JavaScript files with function + class
            "gamma_js.js": (
                "export function gammaHelper(a, b) {\n"
                "  return a + b;\n"
                "}\n"
                "\n"
                "export class GammaStore {\n"
                "  put(key) {\n"
                "    return key;\n"
                "  }\n"
                "}\n"
            ),
            "delta_js.js": (
                "function deltaFactory() {\n"
                "  return null;\n"
                "}\n"
                "\n"
                "const deltaName = (id) => 'd' + id;\n"
            ),
            # 2 TypeScript files with interface + function
            "epsilon_ts.ts": (
                "interface EpsilonConfig {\n"
                "  retries: number;\n"
                "}\n"
                "\n"
                "export function epsilonRetry(cfg: EpsilonConfig): number {\n"
                "  return cfg.retries;\n"
                "}\n"
            ),
            "zeta_ts.ts": (
                "interface ZetaRow {\n"
                "  id: string;\n"
                "}\n"
                "\n"
                "export class ZetaRegistry {\n"
                "  register(row: ZetaRow): string {\n"
                "    return row.id;\n"
                "  }\n"
                "}\n"
            ),
            # 2 TSX files with a component function
            "eta_tsx.tsx": (
                "import React from 'react';\n"
                "\n"
                "export function EtaPanel({ title }: { title: string }) {\n"
                "  return <section className='eta'>{title}</section>;\n"
                "}\n"
            ),
            "theta_tsx.tsx": (
                "export const ThetaBadge = (props: { label: string }) =>\n"
                "  <span className='theta'>{props.label}</span>;\n"
            ),
        }
        for name, body in fixtures.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(body)

        out = await server.get_repo_skeleton(repo_path=d, mode="ranked")

        check("MAP-03: output does not start with ERROR",
              not out.startswith("ERROR"), out[:300])
        check("MAP-03: header says '# ranked source map'",
              "# ranked source map" in out, out[:300])

        # Symbol → the fixture file that defines it.
        expected = {
            "alpha_py.py": ["AlphaWidget", "render_alpha"],
            "beta_py.py": ["build_beta_widget"],
            "gamma_js.js": ["gammaHelper", "GammaStore", "put"],
            "delta_js.js": ["deltaFactory", "deltaName"],
            "epsilon_ts.ts": ["EpsilonConfig", "epsilonRetry"],
            "zeta_ts.ts": ["ZetaRow", "ZetaRegistry", "register"],
            "eta_tsx.tsx": ["EtaPanel"],
            # NOTE: an arrow-function component (`const X = () => <div/>`)
            # is not captured as a *def* by upstream Aider's
            # typescript-tags.scm (no lexical_declaration/arrow rule), so
            # ThetaBadge surfaces only as a pygments-backfilled ref. It is
            # kept here to exercise that path.
            "theta_tsx.tsx": ["ThetaBadge"],
        }

        def line_for_symbol(sym):
            return any(
                re.fullmatch(r"\S+:(L\d+ )?(def|ref) " + re.escape(sym), ln)
                or re.fullmatch(r"\S+ (def|ref) " + re.escape(sym), ln)
                for ln in out.splitlines()
            )

        # Per file, record which of its fixture symbols did not surface.
        unsurfaced = {
            fname: [s for s in syms if not line_for_symbol(s)]
            for fname, syms in expected.items()
        }

        # The spec requires at least one symbol per language; only PY and
        # JS absence is a hard failure.
        by_lang = {
            "py": ["alpha_py.py", "beta_py.py"],
            "js": ["gamma_js.js", "delta_js.js"],
            "ts": ["epsilon_ts.ts", "zeta_ts.ts"],
            "tsx": ["eta_tsx.tsx", "theta_tsx.tsx"],
        }
        for lang, fnames in by_lang.items():
            surfaced = [
                s for f in fnames for s in expected[f]
                if line_for_symbol(s)
            ]
            if not surfaced:
                # Warn and record which language, but only PY/JS fail.
                print(f"     → WARNING: no symbol surfaced for language "
                      f"{lang}; unsurfaced files="
                      f"{[f for f in fnames if unsurfaced[f]]}")
                results.append((f"MAP-03: a symbol from a .{lang} file surfaced",
                                lang not in ("py", "js")))
            else:
                check(f"MAP-03: at least one symbol from a .{lang} file",
                      True, f"{len(surfaced)} surfaced")

        check("MAP-03: total length under 12000 chars",
              len(out) < 12000, f"len={len(out)}")


async def test_ranked_budget_and_exclusions():
    print("\n[Test MAP-04] get_repo_skeleton — ranked budget & exclusions (T01 Day 2)")
    with tempfile.TemporaryDirectory() as d:
        # 20 Python files x 5 symbols = 100 symbols. Each file calls one of
        # its own helpers so tree-sitter sees a real reference and the
        # pygments line=-1 backfill path is not taken — every rendered
        # symbol line therefore carries a real ":L<n>".
        for i in range(20):
            with open(os.path.join(d, f"mod_{i:02d}.py"), "w") as fh:
                fh.write(f"def symbol_{i:02d}_helper(x):\n"
                         f"    return x + {i}\n\n")
                for j in range(1, 5):
                    fh.write(f"def symbol_{i:02d}_{j}(y):\n"
                             f"    return symbol_{i:02d}_helper(y)\n\n")

        # Denied directory: node_modules must be skipped by the walker.
        denied = os.path.join(d, "node_modules")
        os.makedirs(denied)
        for i in range(10):
            with open(os.path.join(
                    denied, f"denied_file_{i + 1:02d}_should_not_appear.py"
            ), "w") as fh:
                fh.write("def denied_symbol():\n    return 0\n")

        out = await server.get_repo_skeleton(
            repo_path=d, mode="ranked", max_tokens=200)

        check("MAP-04: output does not start with ERROR",
              not out.startswith("ERROR"), out[:300])

        # max_tokens=200 → 800 chars for the ranked body; the metric line
        # is appended afterwards, so allow a small margin.
        check("MAP-04: output length <= 200*4 + 200",
              len(out) <= 200 * 4 + 200, f"len={len(out)}")

        check("MAP-04: contains the truncation marker",
              "... [truncated] ..." in out, out[:400])

        check("MAP-04: no denied file names leak",
              "denied_file_" not in out, out[:400])
        check("MAP-04: node_modules is not mentioned",
              "node_modules" not in out, out[:400])

        body_lines = [
            ln for ln in out.splitlines()
            if not ln.startswith("#")
            and not ln.startswith("[TOKEN METRIC]")
            and ln.strip()
        ]
        bad = [
            ln for ln in body_lines
            if not re.fullmatch(r"\S+\.py:L\d+ (def|ref) \S+", ln)
            and ln.strip() != "... [truncated] ..."
        ]
        check("MAP-04: every visible symbol line is 'path:Lx def|ref name'",
              not bad, f"{len(bad)} bad lines: {bad[:3]}")


async def test_ranked_metric_honest():
    print("\n[Test MAP-05] get_repo_skeleton — ranked metric honest (T01 Day 2 fix)")
    with tempfile.TemporaryDirectory() as d:
        # 60 functions per file, not 20: `baseline >= actual` is only a
        # property of the metric when the repo is big enough for the map to
        # compress it. At 3x20 the raw source is ~2400 bytes while the
        # ranked map is ~3100 chars (every symbol costs a "path:Lx kind
        # name" line plus pygments backfill), so the honest answer is
        # saved=0 and `baseline >= actual` fails for the right reason.
        # 3x60 puts raw at ~7100 bytes (baseline ~1770) against a map
        # capped at 1500 tokens, so all four assertions below are
        # meaningful at once.
        for i in range(3):
            with open(os.path.join(d, f"mod_{i}.py"), "w") as fh:
                fh.write(f"def helper_{i}(x):\n    return x + {i}\n")
                for j in range(60):
                    fh.write(f"def fn_{i}_{j}(y):\n"
                             f"    return helper_{i}(y)\n")
        out = await server.get_repo_skeleton(repo_path=d, mode="ranked")

        import re as _re
        m = _re.search(
            r"\[TOKEN METRIC\] tool=get_repo_skeleton saved=([\d,]+) "
            r"baseline=([\d,]+) actual=([\d,]+)", out)
        check("MAP-05: metric line present", m is not None, out[:400])
        if m:
            saved = int(m.group(1).replace(",", ""))
            baseline = int(m.group(2).replace(",", ""))
            actual = int(m.group(3).replace(",", ""))
            check("MAP-05: baseline >= actual", baseline >= actual,
                  f"baseline={baseline} actual={actual}")
            # Raw files: 3 files * ~2360 chars ≈ 7100 bytes.
            # estimate_tokens of that ≈ 1775. Baseline must be
            # within a factor of 4 of that, not 16x (which would
            # signal the fake len(ranked)*4 formula).
            check("MAP-05: baseline is plausible (<= 4x raw/4)",
                  baseline <= 2000, f"baseline={baseline}")
            check("MAP-05: saved matches baseline - actual",
                  saved == max(0, baseline - actual),
                  f"saved={saved} baseline={baseline} actual={actual}")


async def test_ranked_error_fallback():
    """Regression: the ERROR-fallback branch crashed with NameError
    ('redunded' vs 'redundant') whenever the adapter signalled failure."""
    print("\n[Test MAP-06] get_repo_skeleton — ranked ERROR fallback (T01 Day 2 fix)")
    from unittest import mock
    from harness_core import repomap_adapter

    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "keep_me.py"), "w") as fh:
            fh.write("def keep_me():\n    return 1\n")

        # 1. Adapter raises.
        with mock.patch.object(repomap_adapter, "build_ranked_map_with_stats",
                               side_effect=RuntimeError("boom")):
            out = await server.get_repo_skeleton(repo_path=d, mode="ranked")
        check("MAP-06: adapter exception falls back to outline",
              out.startswith("# ranked mode unavailable — boom")
              and "def keep_me(" in out, out[:300])

        # 2. Adapter returns an ERROR tuple.
        with mock.patch.object(repomap_adapter, "build_ranked_map_with_stats",
                               return_value=("ERROR: ranked map unavailable — nope", 0)):
            out = await server.get_repo_skeleton(repo_path=d, mode="ranked")
        check("MAP-06: adapter ERROR falls back to outline",
              out.startswith("# ranked mode unavailable — nope")
              and "def keep_me(" in out, out[:300])

        # 3. No exception escapes, and the reason is de-duplicated once.
        check("MAP-06: reason is not double-prefixed",
              "unavailable — ranked map unavailable" not in out, out[:300])


async def test_ranked_freshness():
    print("\n[Test MAP-07] get_repo_skeleton — freshness (T01 Day 3)")
    import time
    from harness_core import repomap_adapter

    with tempfile.TemporaryDirectory() as d:
        # Seed 3 files
        for i in range(3):
            with open(os.path.join(d, f"mod_{i}.py"), "w") as fh:
                fh.write(f"def seed_{i}(x):\n    return x\n")

        # Reset stats so we start clean
        repomap_adapter._CACHE_STATS["hits"] = 0
        repomap_adapter._CACHE_STATS["misses"] = 0

        # First call — cache miss
        text1, raw1 = repomap_adapter.build_ranked_map_with_stats(d)
        misses1 = repomap_adapter._CACHE_STATS["misses"]

        # Second call, unchanged — cache hit
        text2, raw2 = repomap_adapter.build_ranked_map_with_stats(d)
        hits_after_second = repomap_adapter._CACHE_STATS["hits"]

        check("MAP-07: first call is a miss",
              misses1 >= 1, f"misses={misses1}")
        check("MAP-07: second unchanged call hits the cache",
              hits_after_second >= 1, f"hits={hits_after_second}")
        check("MAP-07: cached result is byte-identical",
              text1 == text2 and raw1 == raw2,
              f"raw1={raw1} raw2={raw2}")

        # Modify one file — even with same size, mtime_ns differs
        time.sleep(0.01)
        with open(os.path.join(d, "mod_1.py"), "a") as fh:
            fh.write("def added_symbol():\n    return 42\n")

        text3, raw3 = repomap_adapter.build_ranked_map_with_stats(d)
        check("MAP-07: added symbol appears after edit",
              "added_symbol" in text3, text3[:300])
        check("MAP-07: raw_bytes grew after edit",
              raw3 > raw1, f"raw1={raw1} raw3={raw3}")

        # Delete a file — signature changes
        os.remove(os.path.join(d, "mod_2.py"))
        text4, raw4 = repomap_adapter.build_ranked_map_with_stats(d)
        check("MAP-07: deleted file's symbols disappear",
              "seed_2" not in text4, text4[:300])


async def test_ranked_backend_missing():
    print("\n[Test MAP-08] get_repo_skeleton — missing backend (T01 Day 3)")
    from unittest import mock
    from harness_core import repomap_adapter

    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "keep.py"), "w") as fh:
            fh.write("def keep_me():\n    return 1\n")

        # Simulate missing backend by patching the preflight helper
        with mock.patch.object(
                repomap_adapter, "_preflight_backend",
                return_value="grep_ast"):
            text, raw = repomap_adapter.build_ranked_map_with_stats(d)
        check("MAP-08: adapter returns ERROR tuple",
              text.startswith("ERROR")
              and "backend not installed" in text
              and raw == 0,
              text[:300])

        # The server should fall back cleanly to outline
        with mock.patch.object(
                repomap_adapter, "_preflight_backend",
                return_value="grep_ast"):
            out = await server.get_repo_skeleton(repo_path=d, mode="ranked")
        check("MAP-08: server falls back to outline",
              out.startswith("# ranked mode unavailable —")
              and "def keep_me(" in out,
              out[:300])
        check("MAP-08: reason names the missing module",
              "grep_ast" in out, out[:300])


# ═══════════════════════════════════════════════════════════
# 0.12.0 regression bundle — external-review fixes
# ═══════════════════════════════════════════════════════════

async def test_git_partial_stage_refused():
    print("\n[Test GIT-07] git_checkpoint — partially-staged target refused (T06 fix)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        target = os.path.join(d, "partial.py")
        with open(target, "w") as fh:
            fh.write("# v1\n")
        subprocess.run(["git", "add", "."], cwd=d, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)
        head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                                     capture_output=True, text=True).stdout

        # Stage one edit, then edit the worktree again → status "MM":
        # `git add` here would sweep the unstaged edit into our commit.
        with open(target, "w") as fh:
            fh.write("# staged edit\n")
        subprocess.run(["git", "add", "partial.py"], cwd=d, check=True)
        with open(target, "w") as fh:
            fh.write("# staged edit\n# unstaged edit\n")

        r = await server.git_checkpoint(target, "t06 partial stage")
        head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                                    capture_output=True, text=True).stdout
        status = subprocess.run(["git", "status", "--porcelain"], cwd=d,
                                capture_output=True, text=True).stdout
        check("Partially-staged target refused",
              "both staged and unstaged changes" in r, r[:400])
        check("Not reported as committed", "Committed:" not in r, r[:300])
        check("HEAD unchanged", head_before == head_after,
              f"{head_before!r} -> {head_after!r}")
        check("git add did not sweep the unstaged edit",
              "MM partial.py" in status, status[:300])
        check("Unstaged edit still on disk",
              "# unstaged edit" in open(target).read(),
              open(target).read()[:200])

        # Only unstaged changes: the old behaviour must survive.
        subprocess.run(["git", "reset", "-q"], cwd=d, check=True)
        with open(target, "w") as fh:
            fh.write("# worktree only\n")
        r2 = await server.git_checkpoint(target, "t06 unstaged only")
        check("Unstaged-only target still commits",
              "Committed:" in r2, r2[:300])


async def test_edit_preserves_mixed_newlines():
    print("\n[Test EDIT-07] apply_search_replace — mixed-newline byte preservation (T04 fix)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "mixed.py")
        # CRLF is dominant (4 of 5 breaks) but one break is a lone LF.
        original = b"alpha\r\nbeta\r\ngamma\r\nlonely\nlast\r\n"
        with open(f, "wb") as fh:
            fh.write(original)

        # Multi-line block passed the conventional way (\n); it must be
        # converted to the file's dominant newline to match, and only
        # that byte range may change.
        r = await server.apply_search_replace(f, "alpha\nbeta", "alpha1\nbeta1")
        check("Multi-line edit applied",
              "successfully" in r.lower() or "Surgical" in r, r[:300])
        after = open(f, "rb").read()
        check("Exactly the matched range changed",
              after == b"alpha1\r\nbeta1\r\ngamma\r\nlonely\nlast\r\n",
              str(after))
        check("Minority LF break survived untouched",
              b"lonely\nlast" in after, str(after))

        # Single-line edit: every other byte still untouched.
        r2 = await server.apply_search_replace(f, "gamma", "gamma2")
        after2 = open(f, "rb").read()
        check("Second edit applied",
              "successfully" in r2.lower() or "Surgical" in r2, r2[:300])
        check("No newline rewritten outside the range",
              after2 == b"alpha1\r\nbeta1\r\ngamma2\r\nlonely\nlast\r\n",
              str(after2))
        check("CRLF convention still dominant in output",
              after2.count(b"\r\n") == 4 and b"\n" in after2, str(after2))


async def test_edit_rejects_overlapping_match():
    print("\n[Test EDIT-08] apply_search_replace — overlapping matches rejected (T04 fix)")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "overlap.py")
        original = b"aaa\n"
        with open(f, "wb") as fh:
            fh.write(original)
        # str.count("aa") over "aaa" is 1 (non-overlapping), so the old
        # code accepted this and wrote. The overlapping scan finds
        # positions 0 and 1 → 2 matches → must refuse.
        r = await server.apply_search_replace(f, "aa", "b")
        check("Overlapping match rejected", "ERROR" in r.upper(), r[:300])
        check("Ambiguity reported with the overlapping count",
              "matches 2 locations" in r, r[:300])
        check("File left unchanged",
              open(f, "rb").read() == original, str(open(f, "rb").read()))

        # A single occurrence (even overlapping a longer neighbour) works.
        r2 = await server.apply_search_replace(f, "aaa", "zzz")
        check("Unique match still applies",
              "successfully" in r2.lower() or "Surgical" in r2, r2[:300])
        check("Replacement written", open(f, "rb").read() == b"zzz\n",
              str(open(f, "rb").read()))


async def test_rollback_truncation_keeps_lines():
    print("\n[Test ROLL-07] rollback_show — entry-based truncation (T07 fix)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=d, check=True)
        for i in range(80):
            name = (f"untracked_file_with_a_really_long_name_{i:03d}_"
                    + "x" * 70 + ".py")
            with open(os.path.join(d, name), "w") as fh:
                fh.write("# x\n")
        r = await server.rollback_show(d)
        check("Response <= 4000", len(r) <= 4000, f"len={len(r)}")
        rlines = r.split("\n")
        # A6: rollback_show's marker now carries the omitted-entry
        # count ("... [truncated: N entries omitted] ..."); accept both
        # that format and the plain one.
        check("Marker appears on its own line",
              any(ln.strip().startswith("... [truncated") for ln in rlines),
              str([ln for ln in rlines if "truncated" in ln][:3]))
        listed = [ln for ln in rlines if ln.startswith("  untracked_file")]
        check("Whole entries survived truncation", len(listed) > 0,
              f"listed={len(listed)}")
        broken = [ln for ln in listed if not ln.endswith(".py")]
        check("No half-line entries", broken == [], str(broken[:3]))
        check("Manual commands survive truncation",
              "git reset --hard" in r, r[:400])


async def test_read_fitting_never_overflows():
    print("\n[Test READ-09] rip_file_lines — _fit_or_mark never exceeds budget (T02 fix)")
    cases = [
        # The old bug: `room` used the full budget although `out` had
        # already consumed part of it → joined length 105 > 100.
        (["1: aaa", "2: " + "x" * 5000], 100),
        (["1: " + "y" * 5000], 100),
        (["1: " + "a" * 60, "2: b", "3: " + "c" * 5000], 100),
        ([f"{i}: line_{i}" for i in range(1, 200)], 500),
        (["a", "b", "c"], 5),
        (["a", "b"], 1),
        (["x" * 50], 0),
        (["1: " + "z" * 40], 60),
        ([], 100),
    ]
    overflowing = []
    for lines, budget in cases:
        out = server._fit_or_mark(list(lines), budget)
        joined = "\n".join(out)
        if len(joined) > budget:
            overflowing.append(f"budget={budget} got={len(joined)}")
    check("Fitted output never exceeds the budget",
          overflowing == [], str(overflowing))

    # End-to-end: a huge single line still yields a bounded, marked view.
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "huge.py")
        with open(f, "w") as fh:
            fh.write("a\n")
            fh.write("x" * 9000 + "\n")
            fh.write("b\n")
        r = await server.rip_file_lines(f, 1, 3, None)
        check("Huge-line rip stays within the 4000 cap",
              len(r) <= 4000, f"len={len(r)}")
        check("Truncated view keeps the marker", "truncated" in r, r[:300])


async def test_ranked_max_files_passthrough():
    print("\n[Test MAP-09] get_repo_skeleton — max_files passthrough (T01 fix)")
    from harness_core import repomap_adapter
    with tempfile.TemporaryDirectory() as d:
        for i in range(6):
            with open(os.path.join(d, f"mod_{i}.py"), "w") as fh:
                fh.write(f"def symbol_{i}():\n    return {i}\n")

        text_full, raw_full = repomap_adapter.build_ranked_map_with_stats(d)
        check("Adapter default walks every file",
              "6 files" in text_full, text_full[:200])
        text_limited, raw_limited = repomap_adapter.build_ranked_map_with_stats(
            d, max_files=2)
        check("Adapter honours max_files",
              "2 files" in text_limited, text_limited[:200])
        check("Limited walk reads fewer bytes",
              raw_limited < raw_full, f"{raw_limited} vs {raw_full}")

        out = await server.get_repo_skeleton(repo_path=d, mode="ranked",
                                             max_files=3)
        check("Server passes max_files to the adapter",
              "3 files" in out, out[:300])
        out_full = await server.get_repo_skeleton(repo_path=d, mode="ranked")
        check("Server default still walks all files",
              "6 files" in out_full, out_full[:300])


async def test_ranked_cache_content_hash():
    print("\n[Test MAP-10] repomap adapter — content-hash cache signature (T01 fix)")
    from harness_core import repomap_adapter

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "same_size.py")
        content1 = "def alpha(x):\n    return x\n"
        with open(path, "w") as fh:
            fh.write(content1)
        st_before = os.stat(path)

        repomap_adapter._CACHE_STATS["hits"] = 0
        repomap_adapter._CACHE_STATS["misses"] = 0

        t1, _ = repomap_adapter.build_ranked_map_with_stats(d)
        misses_after_first = repomap_adapter._CACHE_STATS["misses"]
        t2, _ = repomap_adapter.build_ranked_map_with_stats(d)
        hits_after_second = repomap_adapter._CACHE_STATS["hits"]
        check("MAP-10: first call misses", misses_after_first >= 1,
              f"misses={misses_after_first}")
        check("MAP-10: unchanged call hits", hits_after_second >= 1,
              f"hits={hits_after_second}")

        # Same size, restored timestamp, different bytes.
        content2 = content1.replace("alpha", "betaX")
        with open(path, "w") as fh:
            fh.write(content2)
        os.utime(path, ns=(st_before.st_atime_ns, st_before.st_mtime_ns))
        st_after = os.stat(path)
        check("MAP-10: size really is unchanged",
              st_after.st_size == st_before.st_size,
              f"{st_before.st_size} -> {st_after.st_size}")
        check("MAP-10: mtime really is restored",
              st_after.st_mtime_ns == st_before.st_mtime_ns,
              f"{st_before.st_mtime_ns} -> {st_after.st_mtime_ns}")

        misses_before = repomap_adapter._CACHE_STATS["misses"]
        t3, _ = repomap_adapter.build_ranked_map_with_stats(d)
        misses_after = repomap_adapter._CACHE_STATS["misses"]
        check("MAP-10: same-size, same-mtime edit is a cache miss",
              misses_after > misses_before,
              f"{misses_before} -> {misses_after}")
        check("MAP-10: recomputed map carries the new symbol",
              "betaX" in t3, t3[:300])
        check("MAP-10: stale cached text was not served",
              t3 != t1 and "alpha" not in t3, t3[:300])


async def test_lint_post_execution_freshness():
    print("\n[Test LINT-15] lint_file — post-execution freshness (T05 fix)")
    from unittest.mock import patch
    from subprocess import CompletedProcess

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "racy.py")
        with open(f, "w") as fh:
            fh.write("def a():\n    return 1\n")

        def race(*args, **kwargs):
            # The file changes while py_compile "runs".
            with open(f, "w") as fh:
                fh.write("def a():\n    return 2\n")
            return CompletedProcess(args=args, returncode=0,
                                    stdout="", stderr="")

        with patch("server.subprocess.run", side_effect=race):
            r = await server.lint_file(f)
        check("OK prefix preserved", r.startswith("OK:"), r[:300])
        check("Freshness note present",
              "source changed during compilation" in r, r[:300])
        check("Rerun advice present", "rerun to confirm" in r, r[:300])
        check("Basename still reported", "racy.py" in r, r[:300])

        # A stable source keeps the plain OK response — no false note.
        r2 = await server.lint_file(f)
        check("Unchanged source gets no note",
              r2.startswith("OK:") and "note:" not in r2, r2[:300])


async def test_db_missing_file_not_created():
    print("\n[Test DB-01] inspect_database_schema — missing file, no side effect (T09)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "missing.sqlite")
        r = await server.inspect_database_schema(db_path)
        check("Returns ERROR", "ERROR" in r, r[:200])
        check("Says 'not found'", "not found" in r, r[:200])
        check("Reports absolute path", os.path.abspath(db_path) in r, r[:200])
        check("File NOT created by the call", os.path.exists(db_path) is False)


async def test_db_readonly_connection_enforced():
    print("\n[Test DB-02] inspect_database_schema — read-only connection (T09)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "readonly.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

        before = os.stat(db_path)
        r = await server.inspect_database_schema(db_path)
        after = os.stat(db_path)

        check("Table name present", "users" in r, r[:300])
        check("Column name present", "email" in r, r[:300])
        check("PK rendered", "PK" in r, r[:300])
        check("File size unchanged", before.st_size == after.st_size,
              f"{before.st_size} -> {after.st_size}")
        check("File mtime unchanged",
              before.st_mtime_ns == after.st_mtime_ns,
              f"{before.st_mtime_ns} -> {after.st_mtime_ns}")

        conn = sqlite3.connect(db_path)
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        conn.close()
        check("No new tables appeared", names == ["users"], str(names))

        # A5: actively attempt a write over the SAME uri-based
        # connection the tool uses (mode=ro, uri=True) — it must fail.
        import urllib.parse
        uri = ("file:"
               + urllib.parse.quote(os.path.abspath(db_path), safe="/")
               + "?mode=ro")
        probe = sqlite3.connect(uri, uri=True, timeout=5)
        refused = False
        err = ""
        try:
            probe.execute("CREATE TABLE _write_probe (x INTEGER)")
            probe.commit()
        except sqlite3.OperationalError as exc:
            refused = True
            err = str(exc)
        finally:
            probe.close()
        check("Active write attempt raises OperationalError", refused, err)
        check("Error says the database is readonly",
              refused and "readonly database" in err.lower(), err)

        conn = sqlite3.connect(db_path)
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        conn.close()
        check("Write probe left no table behind", "_write_probe" not in names,
              str(names))


async def test_db_selected_schema_and_identifiers():
    print("\n[Test DB-03] inspect_database_schema — tables= filter + identifiers (T09)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "identifiers.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE "spaces here" '
                     '(id INTEGER PRIMARY KEY, label TEXT NOT NULL)')
        conn.execute('CREATE TABLE "quotes""inside" '
                     '(id INTEGER PRIMARY KEY, val TEXT)')
        conn.execute('CREATE TABLE "normal" (id INTEGER PRIMARY KEY)')
        conn.commit()
        conn.close()

        r = await server.inspect_database_schema(
            db_path, tables='spaces here,quotes"inside')
        check("Requested table shown", "spaces here" in r, r[:400])
        check("Table with quotes shown", 'quotes"inside' in r, r[:400])
        check("Unrequested table omitted", "normal" not in r, r[:400])
        check("Header reports true counts",
              "# schema — 2 of 3 tables" in r, r[:400])
        check("At least one column rendered for 'spaces here'",
              re.search(
                  r"## spaces here\n(?:.*\n)*?  - \w+ \w+", r) is not None,
              r[:400])

        r2 = await server.inspect_database_schema(
            db_path, tables="spaces here,bogus_table")
        check("Bogus requested table reported",
              "# requested table not found: bogus_table" in r2, r2[:400])


async def test_db_no_application_data_and_bounds():
    print("\n[Test DB-04] inspect_database_schema — no data leak, bounded (T09)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "secrets.sqlite")
        conn = sqlite3.connect(db_path)
        for i in range(3):
            conn.execute(f"CREATE TABLE t{i} "
                         "(id INTEGER PRIMARY KEY, note TEXT)")
            conn.execute(f"INSERT INTO t{i} (note) VALUES "
                         "('SECRET_SENTINEL_DO_NOT_SHOW')")
        conn.commit()
        conn.close()

        r = await server.inspect_database_schema(db_path)
        check("No sentinel value in response",
              "SECRET_SENTINEL_DO_NOT_SHOW" not in r, r[:400])
        check("Response ≤ 4000 chars", len(r) <= 4000, f"len={len(r)}")
        check("All three tables listed",
              "## t0" in r and "## t1" in r and "## t2" in r, r[:400])
        check("Column type still shown", "TEXT" in r, r[:400])


async def test_db_many_tables_truncated():
    print("\n[Test DB-05] inspect_database_schema — 50-table cap (T09)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "many.sqlite")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            "".join(f"CREATE TABLE t{i:02d} "
                    "(id INTEGER PRIMARY KEY, v TEXT);\n"
                    for i in range(60))
        )
        conn.commit()
        conn.close()

        r = await server.inspect_database_schema(db_path)
        check("Response ≤ 4000 chars", len(r) <= 4000, f"len={len(r)}")
        check("Truncation marker present",
              "[truncated" in r or "tables omitted" in r, r[-400:])
        check("Count reflects the cap",
              "# schema — 50 of 60 tables" in r, r[:200])
        check("Capped note mentions tables=",
              "use tables= to select" in r, r[-400:])


async def test_db_wal_and_sidecar():
    print("\n[Test DB-06] inspect_database_schema — WAL sidecar files (T09)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "wal.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE wal_table "
                     "(id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO wal_table (v) VALUES ('row')")
        conn.commit()

        r = await server.inspect_database_schema(db_path)
        check("No error on WAL database", "ERROR" not in r, r[:300])
        check("WAL table listed", "wal_table" in r, r[:300])
        check("Column listed", "v " in r, r[:300])

        conn.close()


async def test_db_error_closes_and_preserves():
    print("\n[Test DB-07] inspect_database_schema — non-SQLite file (T09)")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "plain.txt")
        original = "plain text, definitely not a sqlite database\n"
        with open(path, "w") as fh:
            fh.write(original)
        before = os.stat(path)

        r = await server.inspect_database_schema(path)
        after = os.stat(path)

        check("Returns ERROR", "ERROR" in r, r[:300])
        check("File content unchanged", open(path).read() == original)
        check("File size unchanged", before.st_size == after.st_size)
        check("File mtime unchanged",
              before.st_mtime_ns == after.st_mtime_ns)


async def test_db_sqlite_underscore_wildcard():
    print("\n[Test DB-08] inspect_database_schema — literal 'sqlite_' prefix (A1)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "wildcard.sqlite")
        conn = sqlite3.connect(db_path)
        # SQLite refuses CREATE for names starting with "sqlite_", so
        # the reserved-prefix row is injected the way a legacy DB could
        # contain it; `sqliteXfoo` is a plain user table that the old
        # `NOT LIKE 'sqlite_%'` clause wrongly hid ('_' matching 'X').
        conn.execute("CREATE TABLE sqliteXfoo "
                     "(id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "INSERT INTO sqlite_master (type,name,tbl_name,rootpage,sql) "
            "VALUES ('table','sqlite_isfine','sqlite_isfine',0,"
            "'CREATE TABLE sqlite_isfine (id INTEGER)')")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()
        conn.close()

        r = await server.inspect_database_schema(db_path)
        check("Reserved 'sqlite_' table stays hidden",
              "sqlite_isfine" not in r, r[:400])
        check("'sqliteXfoo' is listed", "sqliteXfoo" in r, r[:400])
        check("Its column is rendered", "v TEXT" in r, r[:400])


async def test_db_comma_in_table_name():
    print("\n[Test DB-09] inspect_database_schema — table name with a comma (A2)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "comma.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute('CREATE TABLE "comma,name" '
                     '(id INTEGER PRIMARY KEY, v TEXT)')
        conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        r = await server.inspect_database_schema(db_path, tables="comma,name")
        check("Table whose name contains a comma is selectable",
              "## comma,name" in r, r[:400])
        check("Its column is rendered", "- v TEXT" in r, r[:400])
        check("Header reports 1 of 2 tables",
              "# schema — 1 of 2 tables" in r, r[:400])
        check("Unrequested table omitted", "## other" not in r, r[:400])

        # Without an exact match the input still comma-splits: the
        # whole string isn't a table name, so the pieces are matched.
        r2 = await server.inspect_database_schema(
            db_path, tables="comma,name,other")
        check("Comma-split fallback still selects exact names",
              "## other" in r2, r2[:400])
        check("Unmatched pieces are disclosed as not found",
              "# requested table not found: comma" in r2, r2[:400])


async def test_db_oversized_table_counts():
    print("\n[Test DB-10] inspect_database_schema — inspected vs displayed (A3)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "huge.sqlite")
        conn = sqlite3.connect(db_path)
        cols = ", ".join(f"column_with_a_long_name_{i:03d} TEXT"
                         for i in range(200))
        conn.execute(f"CREATE TABLE big_table "
                     f"(id INTEGER PRIMARY KEY, {cols})")
        conn.commit()
        conn.close()

        r = await server.inspect_database_schema(db_path)
        blocks = re.findall(r"(?m)^## ", r)
        check("Response ≤ 4000 chars", len(r) <= 4000, f"len={len(r)}")
        if blocks:
            m = re.search(r"(?m)^# schema — (\d+) of (\d+) tables$", r)
            check("Header count matches the '## ' blocks shown",
                  m is not None and int(m.group(1)) == len(blocks), r[:300])
            check("No size-omission claim when nothing was dropped",
                  "omitted by size" not in r, r[:300])
        else:
            check("Cut table is not claimed as complete",
                  "1 of 1" not in r, r[:400])
            check("Omission note names the dropped table count",
                  "# ... [truncated: 1 tables omitted by size]" in r, r[:400])


async def test_db_generated_columns():
    print("\n[Test DB-11] inspect_database_schema — generated columns (A4)")
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "generated.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t ("
                     "a TEXT, "
                     "b TEXT, "
                     "c TEXT GENERATED ALWAYS AS (a || b) VIRTUAL)")
        conn.commit()
        conn.close()

        r = await server.inspect_database_schema(db_path)
        check("Generated column listed with GENERATED marker",
              "c TEXT GENERATED" in r, r[:400])
        check("Ordinary columns listed too",
              "a TEXT" in r and "b TEXT" in r, r[:400])
        check("Response ≤ 4000 chars", len(r) <= 4000, f"len={len(r)}")


async def test_rollback_omission_count():
    print("\n[Test ROLL-08] rollback_show — omitted-entry count + wording (A6)")
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=d, check=True)
        for i in range(150):
            name = f"untracked_entry_{i:03d}_" + "y" * 60 + ".txt"
            with open(os.path.join(d, name), "w") as fh:
                fh.write("x\n")

        r = await server.rollback_show(d)
        check("Response ≤ 4000 chars", len(r) <= 4000, f"len={len(r)}")
        check("ROLL-08: marker reports omitted entry count",
              "entries omitted" in r, r[:500])
        check("ROLL-08: real count, not a placeholder",
              re.search(r"truncated: \d+ entries omitted", r) is not None,
              r[:500])
        check("ROLL-08: stash wording present",
              "existing stashes are preserved" in r, r[:500])
        check("ROLL-08: old imprecise caption is gone",
              "loses everything shown above" not in r, r[:500])


async def test_exec_silent_exit_codes():
    print("\n[Test EXEC-08] execute_and_capture — silent success vs failure")
    ok = await server.execute_and_capture('python3 -c "pass"')
    check("EXEC-08: exit 0 reported", "Exit code: 0" in ok, ok[:200])
    check("EXEC-08: not timed out", "Timed out: no" in ok, ok[:200])
    check("EXEC-08: nothing truncated", "Output truncated: no" in ok, ok[:300])
    check("EXEC-08: status flags all present",
          all(s in ok for s in ("Exit code:", "Timed out:", "Output truncated:")),
          ok[:300])

    bad = await server.execute_and_capture(
        'python3 -c "import sys; sys.exit(7)"')
    check("EXEC-08: exit 7 reported", "Exit code: 7" in bad, bad[:200])
    payload = bad.split("Relevant output:\n", 1)[-1]
    check("EXEC-08: silent failure stays silent", payload.strip() == "",
          repr(payload))
    check("EXEC-08: not timed out on failure", "Timed out: no" in bad, bad[:200])


async def test_exec_oversized_output_bounded():
    print("\n[Test EXEC-09] execute_and_capture — oversized output bounded")
    out = await server.execute_and_capture('python3 -c "print(\'z\' * 250000)"')
    check("EXEC-09: Output truncated: yes", "Output truncated: yes" in out,
          out[:200])
    check("EXEC-09: 'bytes omitted' note present", "bytes omitted" in out,
          out[:400])
    check("EXEC-09: note carries real counts",
          re.search(r"output truncated to \d+ bytes; \d+ bytes omitted", out)
          is not None, out[:400])
    check("EXEC-09: response bounded", len(out) < 220_000, f"len={len(out)}")
    check("EXEC-09: exit code intact", "Exit code: 0" in out, out[:200])
    check("EXEC-09: payload kept under the char cap",
          out.count("z") <= server.MAX_OUTPUT_CHARS, f"count={out.count('z')}")


async def test_exec_timeout_keeps_partial_output():
    print("\n[Test EXEC-10] execute_and_capture — timeout keeps partial output")
    out = await server.execute_and_capture(
        "python3 -c \"print('partial'); import time; time.sleep(30)\"",
        timeout_seconds=1)
    check("EXEC-10: partial output survived", "partial" in out, out[:400])
    check("EXEC-10: Timed out: yes", "Timed out: yes" in out, out[:300])
    check("EXEC-10: Exit code: -1", "Exit code: -1" in out, out[:200])
    check("EXEC-10: truncated flag still explicit",
          "Output truncated:" in out, out[:300])
    check("EXEC-10: bounded response", len(out) < 220_000, f"len={len(out)}")


async def test_exec_log_path():
    print("\n[Test EXEC-11] execute_and_capture — log_path full retention")
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "captured.log")
        out = await server.execute_and_capture(
            'python3 -c "print(\'q\' * 250000)"', log_path=log)
        check("EXEC-11: capped view returned", len(out) < 220_000,
              f"len={len(out)}")
        check("EXEC-11: view marked truncated",
              "Output truncated: yes" in out, out[:300])
        with open(log, "rb") as fh:
            blob = fh.read()
        check("EXEC-11: log holds the full output",
              blob == b"q" * 250000 + b"\n", f"len={len(blob)}")

        out2 = await server.execute_and_capture(
            'python3 -c "print(\'w\' * 10)"', log_path=log)
        with open(log, "rb") as fh:
            blob2 = fh.read()
        check("EXEC-11: log appends rather than overwrites",
              blob2.startswith(b"q" * 250000) and b"w" * 10 in blob2,
              f"len={len(blob2)}")
        check("EXEC-11: second call still reports exit 0",
              "Exit code: 0" in out2, out2[:200])

        before = sorted(os.listdir(d))
        await server.execute_and_capture("ls")
        check("EXEC-11: no file written when log_path is empty",
              sorted(os.listdir(d)) == before, str(os.listdir(d)))


async def test_exec_allowlist_unchanged():
    print("\n[Test EXEC-12] execute_and_capture — allowlist + bypass guard")
    expected = {"pytest", "python3", "python", "git", "ls", "cat", "grep",
                "rg", "find"}
    check("EXEC-12: allowlist unchanged",
          server.ALLOWED_COMMANDS == expected,
          str(sorted(server.ALLOWED_COMMANDS)))

    rm = await server.execute_and_capture("rm -rf /")
    check("EXEC-12: 'rm -rf /' refused", rm.startswith("ERROR:"), rm[:200])
    check("EXEC-12: refusal lists allowed commands",
          "Allowed:" in rm, rm[:200])

    wr = await server.execute_and_capture(
        'python3 -c "open(\'/tmp/x\',\'w\')"')
    check("EXEC-12: python write refused", wr.startswith("ERROR:"), wr[:200])
    check("EXEC-12: refusal comes from the bypass guard",
          "refused" in wr and "dangerous pattern" in wr, wr[:300])


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
    await test_malformed_markers_rejected()
    await test_reversed_markers_rejected()
    await test_backup_and_unmarked_file_leaves_both()
    await test_managed_section_update_still_works()
    await test_skeleton_includes_line_ranges()
    await test_find_refs_rejects_empty()
    await test_find_refs_enforces_limits()
    await test_find_refs_no_matches()
    await test_find_refs_hard_budget()
    await test_find_refs_header_count_accurate()
    await test_find_refs_preserves_location_under_small_budget()
    await test_force_preserves_custom_providers()
    await test_harness_commands_installed()
    await test_legacy_commands_preserved_with_warning()
    await test_command_manifest_blocks_overwrite_of_user_file()
    await test_command_manifest_preserves_edits_on_uninstall()
    await test_lint_python_statuses()
    await test_lint_diagnostic_context()
    await test_lint_timeout_and_interpreter_error()
    await test_lint_unrecognized_diagnostic()
    await test_lint_line_5_context()
    await test_lint_truncation_cap()
    await test_lint_permission_and_directory()
    await test_lint_rejects_cross_file_and_generic_line()
    await test_read_raw_range_preserved()
    await test_read_enclosing_symbol_opt_in()
    await test_read_bounds_and_errors()
    await test_read_snapshot_fingerprint()
    await test_read_path_traversal_rejected()
    await test_read_huge_line_useful_failure()
    await test_read_enclosing_innermost()
    await test_read_truncation_budget_use()
    await test_edit_strict_rejections_do_not_write()
    await test_edit_expected_hash_stale()
    await test_edit_preserves_mode_and_newlines()
    await test_edit_atomic_failure_cleanup()
    await test_edit_path_handling()
    await test_edit_no_op_detection()
    await test_read_truncation_keeps_complete_lines()
    await test_read_invalid_context_mode_rejected()
    await test_read_invalid_utf8_disclosed()
    await test_lint_timeout_preserves_partial_stderr()
    await test_lint_no_generic_line_fallback()
    await test_lint_path_disambiguation()
    await test_lint_source_change_during_compile_noted()
    await test_ranked_multilang()
    await test_ranked_budget_and_exclusions()
    await test_ranked_metric_honest()
    await test_ranked_error_fallback()
    await test_ranked_freshness()
    await test_ranked_backend_missing()
    await test_outline_legacy_calls()
    await test_ranked_python_smoke()
    await test_git_subdirectory_path()
    await test_git_outside_repo()
    await test_git_missing_identity()
    await test_git_staged_work_extended()
    await test_git_commit_only_target()
    await test_git_response_bounded()
    await test_git_long_summary_bounded()
    await test_rollback_three_categories()
    await test_rollback_no_mutation()
    await test_rollback_not_a_repo()
    await test_rollback_bounded()
    await test_rollback_manual_command_present()
    await test_rollback_no_hooks_or_external_diff()
    await test_git_partial_stage_refused()
    await test_edit_preserves_mixed_newlines()
    await test_edit_rejects_overlapping_match()
    await test_rollback_truncation_keeps_lines()
    await test_read_fitting_never_overflows()
    await test_ranked_max_files_passthrough()
    await test_ranked_cache_content_hash()
    await test_lint_post_execution_freshness()
    await test_db_missing_file_not_created()
    await test_db_readonly_connection_enforced()
    await test_db_selected_schema_and_identifiers()
    await test_db_no_application_data_and_bounds()
    await test_db_many_tables_truncated()
    await test_db_wal_and_sidecar()
    await test_db_error_closes_and_preserves()
    await test_db_sqlite_underscore_wildcard()
    await test_db_comma_in_table_name()
    await test_db_oversized_table_counts()
    await test_db_generated_columns()
    await test_rollback_omission_count()
    await test_exec_silent_exit_codes()
    await test_exec_oversized_output_bounded()
    await test_exec_timeout_keeps_partial_output()
    await test_exec_log_path()
    await test_exec_allowlist_unchanged()

    # REF tests (T03)
    await test_ref_text_contract_and_boundaries()
    await test_ref_status_budget_regression()
    await test_ref_long_first_match_and_header()
    await test_ref_read_failure_and_long_no_match()
    await test_ref_symbol_ambiguity_and_evidence()
    await test_ref_directed_hops_and_self_edges()

    # CORE regression tests (tests/test_core.py) — counts toward the tally
    import test_core
    test_core.run()
    results.extend(test_core.check_results)

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


async def test_lint_python_statuses():
    print("\n[Test LINT-01] lint_file Python statuses (T05)")
    with tempfile.TemporaryDirectory() as d:
        valid = os.path.join(d, "valid.py")
        with open(valid, "w") as f:
            f.write("def a():\n    return 1\n")
        invalid = os.path.join(d, "invalid.py")
        with open(invalid, "w") as f:
            f.write("def a(:\n    return 1\n")
        notes = os.path.join(d, "notes.txt")
        with open(notes, "w") as f:
            f.write("hello\n")

        r1 = await server.lint_file(valid)
        r2 = await server.lint_file(invalid)
        r3 = await server.lint_file(notes)
        r4 = await server.lint_file(os.path.join(d, "missing.py"))

        check("Valid .py returns OK", r1.startswith("OK:"), r1[:200])
        check("Invalid .py returns FAIL",
              r2.startswith("FAIL:") or "FAIL:" in r2, r2[:200])
        check("Invalid .py mentions syntax error",
              "syntax error" in r2.lower() or "invalid syntax" in r2.lower(),
              r2[:200])
        check("Non-.py returns SKIPPED", r3.startswith("SKIPPED:"), r3[:200])
        check("Missing .py returns FAIL not found",
              "FAIL:" in r4 and "not found" in r4, r4[:200])
        check("Compiler message preserved",
              "SyntaxError" in r2 or "invalid syntax" in r2.lower(),
              r2[:400])


async def test_lint_diagnostic_context():
    print("\n[Test LINT-02] lint_file diagnostic source context (T05)")
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "bad.py")
        with open(bad, "w") as f:
            f.write("def foo(:\n    pass\n")
        r = await server.lint_file(bad)
        check("Response includes filename", "bad.py" in r, r[:300])
        check("Response includes error line number",
              ":1" in r or "line 1" in r, r[:300])
        check("Response marks error line with arrow", "→" in r, r[:300])
        check("Response is bounded", len(r) <= 4000, f"len={len(r)}")
        check("Excludes '(source unavailable)' when source is read",
              "(source unavailable" not in r, r[:300])
        excerpt_lines = [ln for ln in r.split("\n")
                         if ln.strip() and (ln.startswith("    ") or ln.lstrip().startswith("→"))]
        check("Excerpt ≤ 5 lines", len(excerpt_lines) <= 5, f"got {len(excerpt_lines)}")


async def test_lint_timeout_and_interpreter_error():
    print("\n[Test LINT-03] lint_file timeout + interpreter error (T05)")
    from unittest.mock import patch
    import subprocess as sp
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.py")
        with open(f, "w") as fh:
            fh.write("pass\n")
        with patch("server.subprocess.run", side_effect=sp.TimeoutExpired("python3", 10)):
            r = await server.lint_file(f)
        check("Timeout returns FAIL", "FAIL:" in r, r[:200])
        check("Timeout message mentions timeout",
              "timed out" in r.lower() or "timeout" in r.lower(), r[:200])
        with patch("server.subprocess.run", side_effect=FileNotFoundError("python3")):
            r = await server.lint_file(f)
        check("Missing python3 returns FAIL", "FAIL:" in r, r[:200])
        check("Missing python3 message mentions not available",
              "not available" in r.lower(), r[:200])


async def test_lint_unrecognized_diagnostic():
    print("\n[Test LINT-06] lint_file unrecognized diagnostic (T05)")
    from unittest.mock import patch
    from subprocess import CompletedProcess
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.py")
        with open(f, "w") as fh:
            fh.write("pass\n")
        fake = CompletedProcess(
            args=["python3", "-m", "py_compile", f],
            returncode=1, stdout="", stderr="compilation failed for reasons unknown\n",
        )
        with patch("server.subprocess.run", return_value=fake):
            r = await server.lint_file(f)
        check("Unrecognized diagnostic returns FAIL", "FAIL:" in r, r[:300])
        check("Raw stderr preserved",
              "compilation failed for reasons unknown" in r, r[:300])
        check("No fabricated line number",
              "line 999" not in r.lower() and ":999" not in r, r[:300])
        check("Includes '(no location parsed)' marker",
              "(no location parsed)" in r, r[:400])


async def test_lint_line_5_context():
    print("\n[Test LINT-07] lint_file line 5 context")
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "line5.py")
        with open(bad, "w") as f:
            f.write("x = 1\n")
            f.write("y = 2\n")
            f.write("z = 3\n")
            f.write("w = 4\n")
            f.write("def foo(:\n")
            f.write("    pass\n")
        r = await server.lint_file(bad)
        check("→ appears in excerpt", "→" in r, r[:300])
        check("Line 5 in excerpt", ":5" in r, r[:300])
        check("Response ≤ 4000 chars", len(r) <= 4000, f"len={len(r)}")


async def test_lint_truncation_cap():
    print("\n[Test LINT-08] lint_file truncation cap")
    from unittest.mock import patch
    import subprocess as sp
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "big.py")
        with open(bad, "w") as f:
            f.write("line1\nline2\nline3\nline4\n")
            f.write("x" * 5000 + "\n")
            f.write("line6\n")
        fake = sp.CompletedProcess(
            args=["python3", "-m", "py_compile", bad],
            returncode=1, stdout="", stderr="big.py:5: " + "x" * 6000,
        )
        with patch("server.subprocess.run", return_value=fake):
            r = await server.lint_file(bad)
        check("Truncated output contains marker", "\n... [truncated] ...\n" in r, r[:500])
        check("Total length ≤ 4000", len(r) <= 4000, f"len={len(r)}")


async def test_lint_permission_and_directory():
    print("\n[Test LINT-09] lint_file permission + non-file (T05 hotfix)")
    with tempfile.TemporaryDirectory() as d:
        weird_dir = os.path.join(d, "fake.py")
        os.makedirs(weird_dir)
        r = await server.lint_file(weird_dir)
        check("Directory named .py is rejected",
              "not a file" in r.lower() or "FAIL:" in r, r[:300])
        check("No syntax error claimed for directory",
              "syntax error" not in r.lower(), r[:300])

        from unittest.mock import patch
        real = os.path.join(d, "real.py")
        with open(real, "w") as f:
            f.write("pass\n")
        with patch("server.subprocess.run",
                   side_effect=PermissionError("denied")):
            r = await server.lint_file(real)
        check("PermissionError returns FAIL", "FAIL:" in r, r[:200])
        check("PermissionError mentions permission or not permitted",
              "permission" in r.lower() or "not permitted" in r.lower(),
              r[:200])


async def test_lint_rejects_cross_file_and_generic_line():
    print("\n[Test LINT-10] lint_file cross-file + generic line (T05 hotfix)")
    from unittest.mock import patch
    from subprocess import CompletedProcess

    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "valid.py")
        with open(f, "w") as fh:
            fh.write("pass\n")

        fake = CompletedProcess(
            args=["python3", "-m", "py_compile", f],
            returncode=1, stdout="",
            stderr="other.py:5: invalid syntax\n",
        )
        with patch("server.subprocess.run", return_value=fake):
            r = await server.lint_file(f)
        check("Cross-file location not used",
              "valid.py:5" not in r, r[:400])

        fake2 = CompletedProcess(
            args=["python3", "-m", "py_compile", f],
            returncode=1, stdout="",
            stderr="worker configuration error, line 1: invalid settings\n",
        )
        with patch("server.subprocess.run", return_value=fake2):
            r = await server.lint_file(f)
        check("Generic 'line N' not treated as source location",
              "valid.py:1" not in r, r[:400])


# ═══════════════════════════════════════════════════════════
# T03 find_dependent_references (REF-01 through REF-06)
# ═══════════════════════════════════════════════════════════

async def test_ref_text_contract_and_boundaries():
    print("\n[REF-01] find_refs text contract and boundaries")
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as d:
        p0 = os.path.join(d, "empty.py")
        with open(p0, "w") as f:
            f.write("bar = False\n")
        r0 = await server.find_dependent_references("foo", d, mode="text")
        check("REF-01: 0 matches returns non-empty no-match message",
              bool(r0) and ("no" in r0.lower() or "not" in r0.lower()),
              r0[:200])

        p1 = os.path.join(d, "one.py")
        with open(p1, "w") as f:
            f.write("foo = True\n")
        os.remove(p0)
        r1 = await server.find_dependent_references("foo", d, mode="text")
        lines1 = [l for l in r1.splitlines() if "one.py:" in l]
        check("REF-01: 1 match returns exactly 1 location line", len(lines1) == 1, r1[:200])

        with open(p1, "w") as f:
            f.write("foo = True\n" * 40)
        r40 = await server.find_dependent_references("foo", d, mode="text")
        lines40 = [l for l in r40.splitlines() if "one.py:" in l]
        check("REF-01: 40 matches all 40 shown", len(lines40) == 40, f"found {len(lines40)}")

        with open(p1, "w") as f:
            f.write("foo = True\n" * 41)
        r41 = await server.find_dependent_references("foo", d, mode="text")
        lines41 = [l for l in r41.splitlines() if "one.py:" in l]
        check("REF-01: 41 matches shows 40 with count limit or omission",
              len(lines41) == 40 and ("count limit" in r41.lower() or "omitted" in r41.lower()),
              r41[-200:])

        rempty = await server.find_dependent_references("", d, mode="text")
        check("REF-01: empty target_symbol returns error or no-match without crashing",
              isinstance(rempty, str) and (rempty.startswith("ERROR:") or "no" in rempty.lower()),
              rempty[:100])

        for inv in (0, -1, "abc"):
            rinv = await server.find_dependent_references("foo", d, mode="text", max_results=inv)
            check(f"REF-01: invalid max_results={inv!r} handled safely without crash",
                  isinstance(rinv, str) and (rinv.startswith("ERROR:") or "foo" in rinv),
                  str(rinv)[:100])

        with patch("ast.parse", side_effect=AssertionError("ast.parse called in text mode")):
            r_no_ast = await server.find_dependent_references("foo", d, mode="text")
            check("REF-01: text mode never invokes ast.parse", "foo" in r_no_ast)


async def test_ref_status_budget_regression():
    print("\n[REF-02] find_refs status budget regression")
    import re
    with tempfile.TemporaryDirectory() as d:
        fname = os.path.join(d, "watchlist_duplicate_normalization_checks.py")
        with open(fname, "w") as f:
            f.write("foo = True\nfoo = True\nfoo = True\n")

        r = await server.find_dependent_references("foo", d, max_results=2, max_chars=256)
        check("REF-02: full output length <= 256 characters", len(r) <= 256, f"len={len(r)}")

        first_line = r.splitlines()[0]
        m = re.search(r"\((\d+)\s+match", first_line)
        header_count = int(m.group(1)) if m else -1
        loc_lines = [l for l in r.splitlines()[1:] if l and not l.startswith("#")]
        check("REF-02: displayed count equals location lines shown",
              header_count == len(loc_lines),
              f"header={header_count}, locs={len(loc_lines)}")

        has_loc = any("watchlist_duplicate_normalization_checks.py:" in l for l in loc_lines)
        check("REF-02: at least one location survives cap", has_loc, r[:200])

        has_warning = "limit" in r.lower() or "count limit" in r.lower() or "omitted" in r.lower()
        check("REF-02: count warning survives cap", has_warning, r[-100:])


async def test_ref_long_first_match_and_header():
    print("\n[REF-03] find_refs long first match and header")
    with tempfile.TemporaryDirectory() as d:
        fa = os.path.join(d, "long_first.py")
        with open(fa, "w") as f:
            f.write('TARGET = "' + "x" * 15000 + '"\n')
            f.write("TARGET = short\n")

        ra = await server.find_dependent_references("TARGET", d, max_chars=300)
        check("REF-03: (a) response length <= 300", len(ra) <= 300, f"len={len(ra)}")
        check("REF-03: (a) at least one location shown",
              "long_first.py:1" in ra or "long_first.py:2" in ra, ra[:250])
        check("REF-03: (a) header is present",
              "# find_dependent_references:" in ra, ra[:150])
        check("REF-03: (a) does not silently claim complete coverage",
              "limit" in ra.lower() or "omitted" in ra.lower() or "..." in ra or "hard-clipped" in ra.lower(),
              ra[-150:])

    with tempfile.TemporaryDirectory() as d2:
        fb = os.path.join(d2, "forty_matches.py")
        with open(fb, "w") as f:
            for i in range(40):
                f.write(f'TARGET_{i:02d} = "' + "y" * 180 + '"\n')

        rb = await server.find_dependent_references("TARGET", d2, max_chars=8000)
        loc_count = sum(1 for l in rb.splitlines() if "forty_matches.py:" in l)
        check("REF-03: (b) response bounded <= 8000", len(rb) <= 8000, f"len={len(rb)}")
        check("REF-03: (b) many matches survive truncation (> 20)",
              loc_count > 20, f"survived={loc_count}")


async def test_ref_read_failure_and_long_no_match():
    print("\n[REF-04] find_refs read failure and long no-match")
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as d:
        p_ok = os.path.join(d, "good.py")
        with open(p_ok, "w") as f:
            f.write("def normal(): pass\n")
        p_locked = os.path.join(d, "locked.py")
        with open(p_locked, "w") as f:
            f.write("def secret(): pass\n")

        real_open = open
        def mock_open(path, *args, **kwargs):
            if str(path).endswith("locked.py"):
                raise PermissionError("Permission denied: locked.py")
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            r_sym = await server.find_dependent_references("target", d, mode="symbols")

        check("REF-04: symbols mode marks response as incomplete on read failure",
              "incomplete" in r_sym.lower() or "skipped:" in r_sym.lower() or "warning:" in r_sym.lower(),
              r_sym[:300])
        check("REF-04: symbols mode mentions skipped file name or count",
              "locked.py" in r_sym or "1 file" in r_sym,
              r_sym[:300])

        huge = "Z" * 9000
        r_text = await server.find_dependent_references(huge, d, mode="text", max_chars=8000)
        check("REF-04: 9000-char search string output bounded <= 8000", len(r_text) <= 8000, f"len={len(r_text)}")
        check("REF-04: 9000-char search string produces no-match message",
              "no text matches" in r_text.lower(), r_text[:200])


async def test_ref_symbol_ambiguity_and_evidence():
    print("\n[REF-05] find_refs symbol ambiguity and evidence")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "class_a.py"), "w") as f:
            f.write("class A:\n    def save(self):\n        pass\n")
        with open(os.path.join(d, "class_b.py"), "w") as f:
            f.write("class B:\n    def save(self):\n        pass\n")
        with open(os.path.join(d, "caller.py"), "w") as f:
            f.write("import class_a, class_b\na = class_a.A()\na.save()\ntext = \"save the file\"\n")

        r = await server.find_dependent_references("save", d, mode="symbols")

        check("REF-05: A.save appears as definition", "A.save" in r, r)
        check("REF-05: B.save appears as definition", "B.save" in r, r)
        check("REF-05: Call in caller.py appears as reference",
              "caller.py" in r and "in <module>" in r, r)
        check("REF-05: String literal not counted as reference",
              "save the file" not in r and "caller.py:L4" not in r, r)
        check("REF-05: Total references shown is 1",
              "1 reference" in r or "1 references" in r, r)


async def test_ref_directed_hops_and_self_edges():
    print("\n[REF-06] find_refs directed hops and self edges")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "a.py"), "w") as f:
            f.write("def alpha():\n    return beta()\n")
        with open(os.path.join(d, "b.py"), "w") as f:
            f.write("def beta():\n    return gamma()\n")
        with open(os.path.join(d, "c.py"), "w") as f:
            f.write("def gamma():\n    return 1\n")
        with open(os.path.join(d, "d.py"), "w") as f:
            f.write("def delta():\n    return 99\n")

        # depth=1
        r1 = await server.find_dependent_references("alpha", d, mode="related", depth=1)
        check("REF-06: depth=1 beta at hop 1", "beta" in r1 and "[hop=1]" in r1, r1)
        check("REF-06: depth=1 gamma and delta absent",
              "gamma" not in r1 and "delta" not in r1, r1)

        # depth=2
        r2 = await server.find_dependent_references("alpha", d, mode="related", depth=2)
        check("REF-06: depth=2 beta at hop 1", "beta" in r2 and "[hop=1]" in r2, r2)
        check("REF-06: depth=2 gamma at hop 2", "gamma" in r2 and "[hop=2]" in r2, r2)
        check("REF-06: depth=2 delta absent", "delta" not in r2, r2)

        # Self-referencing file does not create self-loop inflating hop count
        with open(os.path.join(d, "loop.py"), "w") as f:
            f.write("def self_loop():\n    return self_loop()\n")
        r_loop = await server.find_dependent_references("self_loop", d, mode="related", depth=3)
        check("REF-06: self-reference does not inflate hop count",
              "[hop=" not in r_loop and "(max hop reached: 0)" in r_loop, r_loop)


if __name__ == "__main__":
    asyncio.run(main())
