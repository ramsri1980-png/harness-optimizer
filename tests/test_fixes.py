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


if __name__ == "__main__":
    asyncio.run(main())
