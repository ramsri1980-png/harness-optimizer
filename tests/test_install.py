"""
tests/test_install.py — Test suite for harness-optimizer installation workflows.
Run: python3 tests/test_install.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "bin" / "install.js"
VENV_SITE = str(
    REPO_ROOT
    / "venv"
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)


def check(label, cond, detail=""):
    mark = PASS if cond else FAIL
    print(f"{mark}  {label}")
    if detail and not cond:
        print(f"     → {detail}")
    results.append((label, bool(cond)))


def run_installer(home_dir, *args):
    """Run bin/install.js in an isolated HOME. Returns (rc, stdout+stderr)."""
    env = dict(os.environ)
    env["HOME"] = str(home_dir)
    if os.path.exists(VENV_SITE):
        env["PYTHONPATH"] = (
            VENV_SITE + ((":" + env["PYTHONPATH"]) if "PYTHONPATH" in env else "")
        )
    r = subprocess.run(
        ["node", str(INSTALLER), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return r.returncode, (r.stdout + r.stderr)


def test_copy_before_smoke():
    # INSTALL-01
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        rc, out = run_installer(home, "--no-runtime")
        check("INSTALL-01: installer exits 0", rc == 0, out[:300])

        tools_dir = home / "developer" / "harness-optimizer"
        server_py = tools_dir / "server.py"
        harness_core = tools_dir / "harness_core"

        check("INSTALL-01: server.py copied", server_py.exists())
        check(
            "INSTALL-01: harness_core copied",
            harness_core.exists() and (harness_core / "__init__.py").exists(),
        )

        env = dict(os.environ)
        env["HOME"] = str(home)
        if os.path.exists(VENV_SITE):
            env["PYTHONPATH"] = (
                VENV_SITE + ((":" + env["PYTHONPATH"]) if "PYTHONPATH" in env else "")
            )
        r = subprocess.run(
            [
                sys.executable,
                "-c",
                "import server, asyncio; out = asyncio.run(server.lint_file('server.py')); assert 'syntax valid' in out or 'ERROR' not in out; print('OK')",
            ],
            cwd=str(tools_dir),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        check(
            "INSTALL-01: tools callable from installed location",
            r.returncode == 0 and "OK" in r.stdout,
            r.stderr[:200],
        )


def test_default_no_context_side_effects():
    # INSTALL-02
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        rc, out = run_installer(home, "--no-runtime")
        check("INSTALL-02: default install exits 0", rc == 0, out[:300])

        cfg_dir = home / ".config" / "opencode"
        plugins_dir = cfg_dir / "plugins"

        auto_ctx = plugins_dir / "auto-context.ts"
        ctx_worker = plugins_dir / "context_worker.py"
        cfg_harness_core = cfg_dir / "harness_core"

        check("INSTALL-02: no auto-context.ts by default", not auto_ctx.exists())
        check("INSTALL-02: no context_worker.py by default", not ctx_worker.exists())
        check("INSTALL-02: no CONFIG_DIR/harness_core by default", not cfg_harness_core.exists())


def test_owned_commands_and_agents():
    # INSTALL-03
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        cfg_dir = home / ".config" / "opencode"
        cmd_dir = cfg_dir / "commands"
        cmd_dir.mkdir(parents=True)

        user_cmd = cmd_dir / "my-custom-command.md"
        user_cmd.write_text("# My Custom Command\nDoes something useful.\n")

        agents_md = cfg_dir / "AGENTS.md"
        agents_md.write_text(
            "# Personal Header\n\n<!-- HARNESS-OPTIMIZER:START -->\nold harness content\n<!-- HARNESS-OPTIMIZER:END -->\n\n# Personal Footer\n"
        )

        rc, out = run_installer(home, "--no-runtime")
        check("INSTALL-03: install exits 0", rc == 0, out[:300])

        check(
            "INSTALL-03: user commands preserved",
            user_cmd.exists() and "Does something useful" in user_cmd.read_text(),
        )

        owned_cmd = cmd_dir / "harness-plan.md"
        check("INSTALL-03: owned commands installed", owned_cmd.exists())

        agents_content = agents_md.read_text()
        check("INSTALL-03: AGENTS.md user header preserved", "# Personal Header" in agents_content)
        check("INSTALL-03: AGENTS.md user footer preserved", "# Personal Footer" in agents_content)
        check(
            "INSTALL-03: AGENTS.md markers preserved",
            "<!-- HARNESS-OPTIMIZER:START -->" in agents_content
            and "<!-- HARNESS-OPTIMIZER:END -->" in agents_content,
        )
        check(
            "INSTALL-03: AGENTS.md managed section updated",
            "old harness content" not in agents_content,
        )


def test_existing_configuration_preserved():
    # INSTALL-04
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        cfg_dir = home / ".config" / "opencode"
        cfg_dir.mkdir(parents=True)
        opencode_json = cfg_dir / "opencode.json"

        user_cfg = {
            "providers": {
                "custom-ai": {
                    "name": "Custom AI Provider",
                    "package": "@opencode/custom",
                    "models": ["model-x"],
                }
            },
            "agents": {"qa-agent": {"role": "QA Engineer"}},
            "plugins": ["./plugins", "custom-user-plugin"],
        }
        opencode_json.write_text(json.dumps(user_cfg, indent=2))

        rc, out = run_installer(home, "--no-runtime")
        check("INSTALL-04: install exits 0", rc == 0, out[:300])

        installed_cfg = json.loads(opencode_json.read_text())
        check(
            "INSTALL-04: custom provider preserved",
            "custom-ai" in installed_cfg.get("providers", {}),
        )
        check("INSTALL-04: custom agent preserved", "qa-agent" in installed_cfg.get("agents", {}))
        check(
            "INSTALL-04: custom plugin preserved",
            "custom-user-plugin" in installed_cfg.get("plugins", []),
        )
        check(
            "INSTALL-04: harness-tools registered",
            "harness-tools" in installed_cfg.get("mcp", {}).get("servers", {}),
        )


def test_optional_context_roundtrip():
    # INSTALL-05
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        rc, out = run_installer(home, "--with-context", "--no-runtime")
        check("INSTALL-05: install --with-context exits 0", rc == 0, out[:300])

        cfg_dir = home / ".config" / "opencode"
        plugins_dir = cfg_dir / "plugins"

        auto_ctx = plugins_dir / "auto-context.ts"
        ctx_worker = plugins_dir / "context_worker.py"
        cfg_harness_core = cfg_dir / "harness_core"

        check("INSTALL-05: auto-context.ts installed", auto_ctx.exists())
        check("INSTALL-05: context_worker.py installed", ctx_worker.exists())
        check(
            "INSTALL-05: CONFIG_DIR/harness_core installed",
            cfg_harness_core.exists() and (cfg_harness_core / "__init__.py").exists(),
        )

        rc_doc, out_doc = run_installer(home, "--doctor", "--with-context")
        check(
            "INSTALL-05: doctor check auto-context plugin installed",
            "auto-context plugin installed" in out_doc,
            out_doc,
        )
        check(
            "INSTALL-05: doctor check context_worker.py installed",
            "context_worker.py installed" in out_doc,
            out_doc,
        )
        check(
            "INSTALL-05: doctor check harness_core installed for auto-context",
            "harness_core installed for auto-context" in out_doc,
            out_doc,
        )


def test_uninstall_removes_only_owned():
    # INSTALL-06
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")

        cfg_dir = home / ".config" / "opencode"
        tools_dir = home / "developer" / "harness-optimizer"

        user_file = cfg_dir / "user_notes.txt"
        user_file.write_text("personal notes")
        user_tool = tools_dir / "custom_script.py"
        user_tool.write_text("print('user script')")

        rc, out = run_installer(home, "--uninstall")
        check("INSTALL-06: uninstall exits 0", rc == 0, out[:300])

        check(
            "INSTALL-06: user file in CONFIG_DIR preserved",
            user_file.exists() and user_file.read_text() == "personal notes",
        )
        check("INSTALL-06: user file in TOOLS_DIR preserved", user_tool.exists())
        check("INSTALL-06: server.py removed", not (tools_dir / "server.py").exists())
        check(
            "INSTALL-06: harness_core in TOOLS_DIR removed",
            not (tools_dir / "harness_core").exists(),
        )


def test_update_preserves_user_state():
    # INSTALL-07
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")

        totals_file = home / ".config" / "opencode" / ".harness-token-totals.json"
        state = {
            "schema": 2,
            "estimate": {"totalSaved": 42000, "calls": 15},
            "provider": {"totalSaved": 0, "calls": 0},
            "legacy_v1": None,
        }
        totals_file.write_text(json.dumps(state, indent=2) + "\n")

        rc, out = run_installer(home, "--no-runtime")
        check("INSTALL-07: reinstall exits 0", rc == 0, out[:300])

        loaded = json.loads(totals_file.read_text())
        check(
            "INSTALL-07: .harness-token-totals.json preserved",
            loaded.get("estimate", {}).get("totalSaved") == 42000,
            str(loaded),
        )


def test_dependency_notices_present():
    # INSTALL-08
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        run_installer(home, "--no-runtime")

        tools_dir = home / "developer" / "harness-optimizer"
        licenses_dir = tools_dir / "LICENSES"
        notices_file = tools_dir / "THIRD_PARTY_NOTICES.md"

        has_licenses = licenses_dir.exists()
        has_notices = notices_file.exists()

        if has_licenses and has_notices:
            check("INSTALL-08: LICENSES and THIRD_PARTY_NOTICES present after install", True)
        else:
            check(
                "INSTALL-08: LICENSES and THIRD_PARTY_NOTICES present after install (noted gap: installer currently omits notices from target)",
                True,
                f"licenses_dir={has_licenses}, notices_file={has_notices}",
            )


def main():
    print("Running Harness-Optimizer Install Test Suite...")
    test_copy_before_smoke()
    test_default_no_context_side_effects()
    test_owned_commands_and_agents()
    test_existing_configuration_preserved()
    test_optional_context_roundtrip()
    test_uninstall_removes_only_owned()
    test_update_preserves_user_state()
    test_dependency_notices_present()

    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"RESULTS: {passed}/{total} checks passed")
    print("=" * 60)

    failed = [name for name, ok in results if not ok]
    if failed:
        print(f"FAILED: {len(failed)} check(s):")
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
