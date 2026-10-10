"""
tests/test_met.py — MET Day 1 test suite (MET-02, 07, 09, 11, 13, 14, 15).
Run directly: python3 tests/test_met.py
"""
import datetime
import os
import pathlib
import re
import subprocess
import sys
import tempfile

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HELPERS_MJS = REPO_ROOT / "templates" / "token-metrics-helpers.mjs"
TOOL_CALLS_SH = REPO_ROOT / "scripts" / "tool-calls.sh"


def check(label, cond, detail=""):
    mark = PASS if cond else FAIL
    print(f"{mark}  {label}")
    if detail and not cond:
        print(f"     → {detail}")
    results.append((label, bool(cond)))


def test_stream_replay_and_real_retries():
    # MET-02
    r = subprocess.run(
        ["node", str(HELPERS_MJS), "--selftest", "met-02"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    ok = r.returncode == 0 and "PASS" in r.stdout and "440" in r.stdout
    check("MET-02: stream replay and real retries idempotent", ok, r.stdout + r.stderr)


def test_no_fabricated_credit():
    # MET-07
    r = subprocess.run(
        ["node", str(HELPERS_MJS), "--selftest", "met-07"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    ok = r.returncode == 0 and "PASS" in r.stdout
    check("MET-07: no fabricated credit or zero-value drift", ok, r.stdout + r.stderr)


def test_legacy_totals_preserved():
    # MET-09
    r = subprocess.run(
        ["node", str(HELPERS_MJS), "--selftest", "met-09"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    ok = r.returncode == 0 and "PASS" in r.stdout
    check("MET-09: legacy totals preserved and estimate rebuilt from log", ok, r.stdout + r.stderr)


def test_metrics_do_not_trigger_work():
    # MET-11
    r = subprocess.run(
        ["node", str(HELPERS_MJS), "--selftest", "met-11"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    selftest_ok = r.returncode == 0 and "PASS" in r.stdout

    src = HELPERS_MJS.read_text()
    forbidden_pattern = re.search(
        r'import\s+.*?from\s+["\'](fs|os|child_process|node:fs|node:os|node:child_process)["\']',
        src,
    )
    no_forbidden = forbidden_pattern is None

    node_check = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            f"""
            import * as helpers from "{HELPERS_MJS.as_uri()}";
            if (typeof helpers.parseMetric !== 'function' ||
                typeof helpers.classifyTotals !== 'function' ||
                typeof helpers.isHarnessTool !== 'function') {{
              process.exit(1);
            }}
            """,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    node_ok = node_check.returncode == 0

    check(
        "MET-11: pure helpers do not import fs/os/child_process",
        selftest_ok and no_forbidden and node_ok,
        f"selftest={selftest_ok}, no_forbidden={no_forbidden}, node_ok={node_ok}, stderr={node_check.stderr}",
    )


def test_tool_calls_default_view():
    # MET-13
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        cfg = home / ".config" / "opencode"
        cfg.mkdir(parents=True)
        log_file = cfg / ".harness-token-metrics.log"

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        yesterday_str = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        ).strftime("%Y-%m-%d")

        log_lines = [
            f"[{today_str}T10:00:00.000Z] tool=rip_file_lines saved=1500 baseline=2000 actual=500 | total: 1 calls, 1500 saved",
            f"[{today_str}T11:00:00.000Z] tool=apply_search_replace saved=2500 baseline=3000 actual=500 | total: 2 calls, 4000 saved",
            f"[{yesterday_str}T12:00:00.000Z] tool=rip_file_lines saved=1000 baseline=1500 actual=500 | total: 3 calls, 5000 saved",
        ]
        log_file.write_text("\n".join(log_lines) + "\n")

        env = dict(os.environ)
        env["HOME"] = str(home)

        r = subprocess.run(
            ["bash", str(TOOL_CALLS_SH)],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

        out = r.stdout
        rc_ok = r.returncode == 0
        sum_ok = "5,000 tokens" in out or "5000 tokens" in out
        no_today = "today" not in out.lower()
        no_session = "session" not in out.lower()
        blocks_ok = "by tool" in out and "last 5 calls" in out

        check(
            "MET-13: tool_calls default view shows log sum without today/session",
            rc_ok and sum_ok and no_today and no_session and blocks_ok,
            f"rc={r.returncode}, sum_ok={sum_ok}, no_today={no_today}, no_session={no_session}, blocks_ok={blocks_ok}\nOut: {out}",
        )


def test_tool_calls_today_flag():
    # MET-14
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        cfg = home / ".config" / "opencode"
        cfg.mkdir(parents=True)
        log_file = cfg / ".harness-token-metrics.log"

        today_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        yesterday_str = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        ).strftime("%Y-%m-%d")

        log_lines = [
            f"[{today_str}T10:00:00.000Z] tool=rip_file_lines saved=1500 baseline=2000 actual=500 | total: 1 calls, 1500 saved",
            f"[{today_str}T11:00:00.000Z] tool=apply_search_replace saved=2500 baseline=3000 actual=500 | total: 2 calls, 4000 saved",
            f"[{yesterday_str}T12:00:00.000Z] tool=rip_file_lines saved=1000 baseline=1500 actual=500 | total: 3 calls, 5000 saved",
        ]
        log_file.write_text("\n".join(log_lines) + "\n")

        env = dict(os.environ)
        env["HOME"] = str(home)

        r = subprocess.run(
            ["bash", str(TOOL_CALLS_SH), "--today"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

        out = r.stdout.strip()
        lines = out.splitlines()
        rc_ok = r.returncode == 0
        single_line = len(lines) == 1
        starts_today = lines[0].startswith("💵 today:") if lines else False
        sum_ok = "4,000 tokens" in out or "4000 tokens" in out
        calls_ok = "2 calls" in out

        check(
            "MET-14: tool_calls --today outputs single line with today's sum only",
            rc_ok and single_line and starts_today and sum_ok and calls_ok,
            f"rc={r.returncode}, single_line={single_line}, starts_today={starts_today}, sum_ok={sum_ok}, calls_ok={calls_ok}\nOut: {out}",
        )


def test_tool_calls_session_flag():
    # MET-15
    with tempfile.TemporaryDirectory() as fake_home:
        home = pathlib.Path(fake_home)
        cfg = home / ".config" / "opencode"
        cfg.mkdir(parents=True)
        log_file = cfg / ".harness-token-metrics.log"

        loaded_ts = "2026-10-10T11:00:00.000Z"
        log_lines = [
            "[2026-10-10T10:00:00.000Z] tool=rip_file_lines saved=1000 baseline=2000 actual=1000 | total: 1 calls, 1000 saved",
            f"[{loaded_ts}] plugin loaded; totals rebuilt from log: estimate: 1000 saved / 1 calls; provider: 0 / 0; legacy: no",
            "[2026-10-10T11:05:00.000Z] tool=apply_search_replace saved=3000 baseline=4000 actual=1000 | total: 2 calls, 4000 saved",
            "[2026-10-10T11:10:00.000Z] tool=rip_file_lines saved=500 baseline=1000 actual=500 | total: 3 calls, 4500 saved",
        ]
        log_file.write_text("\n".join(log_lines) + "\n")

        env = dict(os.environ)
        env["HOME"] = str(home)

        r = subprocess.run(
            ["bash", str(TOOL_CALLS_SH), "--session"],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

        out = r.stdout.strip()
        rc_ok = r.returncode == 0
        has_session = "🕐 session:" in out
        sum_ok = "3,500 tokens" in out or "3500 tokens" in out
        calls_ok = "2 calls" in out
        since_ok = loaded_ts in out

        check(
            "MET-15: tool_calls --session reflects lines after last plugin load",
            rc_ok and has_session and sum_ok and calls_ok and since_ok,
            f"rc={r.returncode}, has_session={has_session}, sum_ok={sum_ok}, calls_ok={calls_ok}, since_ok={since_ok}\nOut: {out}",
        )


def main():
    print("Running MET Day 1 Test Suite...")
    test_stream_replay_and_real_retries()
    test_no_fabricated_credit()
    test_legacy_totals_preserved()
    test_metrics_do_not_trigger_work()
    test_tool_calls_default_view()
    test_tool_calls_today_flag()
    test_tool_calls_session_flag()

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
