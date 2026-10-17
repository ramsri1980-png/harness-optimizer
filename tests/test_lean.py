"""Lean-mode regression tests (HARNESS_LEAN=1). Run: python3 tests/test_lean.py

Loads templates/server.py twice (default and lean) so both formats are
checked side by side. Calls the tool coroutines directly with a fake ctx
(the other suites pass ctx=None, which skips the metric path entirely).
"""
import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
sys.path.insert(0, str(TEMPLATES))

results = []


def check(name, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" - {detail}" if detail and not cond else ""))
    results.append((name, bool(cond)))


class FakeCtx:
    def __init__(self):
        self.logs = []

    async def info(self, msg):
        self.logs.append(msg)


def load(label, **env):
    keys = ("HARNESS_LEAN", "HARNESS_METRICS_LOG", "HARNESS_DEDUP_WINDOW", "HARNESS_DIFF_LINES", "HARNESS_TOOLS")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(f"server_{label}", TEMPLATES / "server.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


async def main():
    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "m.jsonl")
        dflt = load("dflt")
        lean = load("lean", HARNESS_LEAN="1", HARNESS_METRICS_LOG=log)

        f = os.path.join(d, "a.py")
        write(f, "".join(f"def fn_{i}(x):\n    return x + {i}\n\n" for i in range(60)))

        print("\n[LEAN-01/02] rip_file_lines: metric line + fingerprint")
        r_d = await dflt.rip_file_lines(f, 1, 12, FakeCtx())
        r_l = await lean.rip_file_lines(f, 1, 12, FakeCtx())
        check("LEAN-01 default keeps [TOKEN METRIC]", "[TOKEN METRIC]" in r_d)
        check("LEAN-01 lean drops [TOKEN METRIC]", "[TOKEN METRIC]" not in r_l)
        check("LEAN-02 default sha is 64 hex", any(len(l.split(": ")[1]) == 64 for l in r_d.splitlines() if l.startswith("# sha256: ")))
        check("LEAN-02 lean sha is 16 hex", any(len(l.split(": ")[1]) == 16 for l in r_l.splitlines() if l.startswith("# sha256: ")))
        check("LEAN-02 lean body lines identical", [l for l in r_d.splitlines() if l[:1].isdigit()] == [l for l in r_l.splitlines() if l[:1].isdigit()])
        check("LEAN-01 lean output is shorter", len(r_l) < len(r_d), f"{len(r_l)} vs {len(r_d)}")

        print("\n[LEAN-03] apply_search_replace")
        g1, g2 = os.path.join(d, "g1.py"), os.path.join(d, "g2.py")
        for g in (g1, g2):
            write(g, "a = 1\nb = 2\nc = 3\n")
        o_d = await dflt.apply_search_replace(g1, "b = 2", "b = 20")
        o_l = await lean.apply_search_replace(g2, "b = 2", "b = 20")
        check("LEAN-03 default format unchanged", o_d.startswith("Surgical replacement applied successfully."))
        check("LEAN-03 lean is one short line", o_l.startswith("OK L2-2 sha256:") and "\n" not in o_l.strip(), o_l)
        check("LEAN-03 both wrote the same bytes", open(g1).read() == open(g2).read() == "a = 1\nb = 20\nc = 3\n")
        check("LEAN-03 lean is >=60% smaller", len(o_l) <= 0.4 * len(o_d), f"{len(o_l)} vs {len(o_d)}")
        full = lean.hashlib.sha256(open(g2, "rb").read()).hexdigest()
        ok = await lean.apply_search_replace(g2, "c = 3", "c = 30", expected_sha256=full[:16])
        check("LEAN-03 lean accepts 16-char sha prefix", ok.startswith("OK "), ok)
        bad = await lean.apply_search_replace(g2, "a = 1", "a = 10", expected_sha256="0" * 16)
        check("LEAN-03 lean rejects wrong prefix", bad.startswith("ERROR: stale content"), bad)
        short = await lean.apply_search_replace(g2, "a = 1", "a = 10", expected_sha256="ab")
        check("LEAN-03 lean rejects too-short prefix", short.startswith("ERROR: stale content"), short)
        h = os.path.join(d, "h.py")
        write(h, "x = 1\n")
        pref = await dflt.apply_search_replace(h, "x = 1", "x = 2", expected_sha256=dflt.hashlib.sha256(b"x = 1\n").hexdigest()[:16])
        check("LEAN-03 default still rejects prefixes", pref.startswith("ERROR: stale content"), pref)

        print("\n[LEAN-04] dedup of repeated reads")
        r1 = await lean.rip_file_lines(f, 1, 120, None)
        r2 = await lean.rip_file_lines(f, 1, 120, None)
        check("LEAN-04 repeat returns UNCHANGED", r2.startswith("UNCHANGED:"), r2[:80])
        check("LEAN-04 UNCHANGED is under 25% of a 120-line read", len(r2) < 0.25 * len(r1), f"{len(r2)} vs {len(r1)}")
        write(f, open(f).read() + "# edited\n")
        r3 = await lean.rip_file_lines(f, 1, 120, None)
        check("LEAN-04 changed file returns full content", not r3.startswith("UNCHANGED") and "20:" in r3)
        w = load("win", HARNESS_LEAN="1", HARNESS_DEDUP_WINDOW="2", HARNESS_METRICS_LOG="off")
        await w.rip_file_lines(f, 1, 5, None)
        for n in range(3):
            await w.rip_file_lines(f, 6 + n, 8 + n, None)
        r4 = await w.rip_file_lines(f, 1, 5, None)
        check("LEAN-04 outside the window returns full content", not r4.startswith("UNCHANGED"))
        dd1 = await dflt.rip_file_lines(f, 1, 5, None)
        dd2 = await dflt.rip_file_lines(f, 1, 5, None)
        check("LEAN-04 default mode never dedups", not dd2.startswith("UNCHANGED") and dd1 == dd2)

        print("\n[LEAN-05] outline cap")
        big = os.path.join(d, "big")
        os.makedirs(big)
        for i in range(30):
            write(os.path.join(big, f"m{i}.py"), "".join(f"def f{j}(a, b):\n    return a\n" for j in range(30)))
        s_d = await dflt.get_repo_skeleton(big, mode="outline")
        s_l = await lean.get_repo_skeleton(big, mode="outline", max_tokens=300)
        check("LEAN-05 default outline unbounded (>300 tokens)", len(s_d) > 1200)
        check("LEAN-05 lean outline capped near max_tokens", len(s_l) <= 300 * 4 + 160, len(s_l))
        check("LEAN-05 lean outline says it was truncated", "outline truncated" in s_l)
        s_l2 = await lean.get_repo_skeleton(big, mode="outline", max_tokens=300)
        check("LEAN-05 repeat outline returns UNCHANGED", s_l2.startswith("UNCHANGED:"), s_l2[:60])

        print("\n[LEAN-06] find_dependent_references caps")
        many = os.path.join(d, "many")
        os.makedirs(many)
        write(os.path.join(many, "x.py"), "".join(f"needle_{i} = needle  # needle\n" for i in range(100)))
        q_d = await dflt.find_dependent_references("needle", many)
        q_l = await lean.find_dependent_references("needle", many)
        check("LEAN-06 default shows up to 40 matches", "(40 matches shown)" in q_d, q_d.splitlines()[0])
        check("LEAN-06 lean shows at most 20 matches", "(20 matches shown)" in q_l, q_l.splitlines()[0])
        check("LEAN-06 lean output is shorter", len(q_l) < len(q_d))

        print("\n[LEAN-07] server-side metrics log")
        await lean.rip_file_lines(f, 40, 50, FakeCtx())
        rows = [json.loads(l) for l in open(log, encoding="utf-8") if l.strip()]
        check("LEAN-07 log has entries", len(rows) >= 3, len(rows))
        check("LEAN-07 entries have required fields", all({"ts", "tool", "saved", "baseline", "actual"} <= set(r) for r in rows))
        check("LEAN-07 tools recorded", {"rip_file_lines", "apply_search_replace"} <= {r["tool"] for r in rows}, {r["tool"] for r in rows})
        check("LEAN-07 default mode writes no log by itself", dflt._METRICS_LOG == "")

    print("\n[LEAN-08] templates/AGENTS.md installer contract")
    tpl = (ROOT / "templates" / "AGENTS.md").read_text(encoding="utf-8")
    S, E = "<!-- HARNESS-OPTIMIZER:START -->", "<!-- HARNESS-OPTIMIZER:END -->"
    check("LEAN-08 exactly one START and one END marker", tpl.count(S) == 1 and tpl.count(E) == 1)
    check("LEAN-08 START precedes END", tpl.find(S) < tpl.find(E))
    check("LEAN-08 template stays small (<= 4000 bytes)", len(tpl.encode("utf-8")) <= 4000, len(tpl.encode("utf-8")))
    for cmd in ("harness-plan", "harness-verify", "harness-checkpoint"):
        check(f"LEAN-08 command template exists: {cmd}", (ROOT / "templates" / "commands" / f"{cmd}.md").exists())
    inst = (ROOT / "bin" / "install.js").read_text(encoding="utf-8")
    check("LEAN-08 installer ships the new commands", inst.count("harness-verify.md") >= 2 and inst.count("harness-checkpoint.md") >= 2)

    failed = [n for n, ok in results if not ok]
    print("\n" + "=" * 60)
    print(f"RESULTS: {len(results) - len(failed)}/{len(results)} checks passed")
    print("=" * 60)
    if failed:
        print(f"FAILED: {len(failed)} check(s):")
        for n in failed:
            print("  - " + n)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
