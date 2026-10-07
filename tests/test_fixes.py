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
