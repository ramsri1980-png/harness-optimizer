"""Harness-Optimizer MCP tools for OpenCode.

Token-optimized code editing helpers exposed over MCP (stdio).

Environment:
  HARNESS_TOOLS  Optional. "all" (default) or a comma-separated whitelist of
                 tool names. Disabled tools are not registered with the MCP
                 client, so the model never sees them.
"""
import ast
import os
import shlex
import sqlite3
import subprocess

from fastmcp import Context, FastMCP

mcp = FastMCP("HarnessTools")

# ── Tool toggle ─────────────────────────────────────────────
_raw = os.environ.get("HARNESS_TOOLS", "all").strip().lower()
if _raw in {"", "all"}:
    _ENABLED = None
else:
    _ENABLED = {t.strip() for t in _raw.split(",") if t.strip()}


def enabled(name: str) -> bool:
    return _ENABLED is None or name in _ENABLED


def register(name: str):
    """Decorator that only attaches the function if enabled(name)."""
    def deco(fn):
        if enabled(name):
            return mcp.tool()(fn)
        return fn
    return deco


ALLOWED_COMMANDS = {
    "pytest", "python3", "python", "git", "ls", "cat", "grep", "rg", "find",
}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


async def emit_metric(ctx: Context, tool: str, saved: int, baseline: int, actual: int) -> str:
    line = (
        f"[TOKEN METRIC] tool={tool} saved={saved:,} "
        f"baseline={baseline:,} actual={actual:,}"
    )
    await ctx.info(line)
    return line

# ═══════════════════════════════════════════════════════════
# A. Context reading
# ═══════════════════════════════════════════════════════════

@register("get_repo_skeleton")
async def get_repo_skeleton(repo_path: str, ctx: Context) -> str:
    """Map Python code structure with AST. Returns class names, function
    signatures, and file paths — NOT full file bodies. Call this first
    before reading any source file."""
    if not os.path.exists(repo_path):
        return f"Error: Path {repo_path} does not exist."

    skeleton: list[str] = []
    total_raw_chars = 0

    for root, _, files in os.walk(repo_path):
        if any(p in root for p in ["venv", ".git", "__pycache__", "node_modules"]):
            continue
        for file in sorted(files):
            if not file.endswith(".py"):
                continue
            file_path = os.path.join(root, file)
            rel_path = os.path.relpath(file_path, repo_path)
            skeleton.append(f"\n📁 FILE: {rel_path}")
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    raw_data = f.read()
                total_raw_chars += len(raw_data)
                tree = ast.parse(raw_data, filename=file_path)
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        skeleton.append(f"  class {node.name}:")
                        for sub in node.body:
                            if isinstance(sub, ast.FunctionDef):
                                args = [a.arg for a in sub.args.args]
                                skeleton.append(f"    def {sub.name}({', '.join(args)}):")
                    elif isinstance(node, ast.FunctionDef):
                        args = [a.arg for a in node.args.args]
                        skeleton.append(f"  def {node.name}({', '.join(args)}):")
            except Exception as exc:
                skeleton.append(f"  [Parse Error: {exc}]")

    text = "\n".join(skeleton)
    baseline = estimate_tokens(" " * total_raw_chars)
    actual = estimate_tokens(text)
    saved = max(0, baseline - actual)
    metric = await emit_metric(ctx, "get_repo_skeleton", saved, baseline, actual)
    return text + "\n\n" + metric


@register("rip_file_lines")
async def rip_file_lines(file_path: str, start_line: int, end_line: int, ctx: Context) -> str:
    """View a precise line window from a file. Use this instead of reading
    full files. Line numbers are 1-indexed and printed for reference."""
    file_path = os.path.expanduser(file_path)
    if not os.path.exists(file_path):
        return f"Error: File {file_path} not found."
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            all_content = f.read()
        lines = all_content.splitlines()

        s_idx = max(0, start_line - 1)
        e_idx = min(len(lines), end_line)

        out = [f"--- Lines {start_line} to {end_line} of {len(lines)} ---"]
        for idx, line in enumerate(lines[s_idx:e_idx], start=start_line):
            out.append(f"{idx}: {line.rstrip()}")
        ripped = "\n".join(out)

        baseline = estimate_tokens(all_content)
        actual = estimate_tokens(ripped)
        saved = max(0, baseline - actual)
        metric = await emit_metric(ctx, "rip_file_lines", saved, baseline, actual)
        return ripped + "\n\n" + metric
    except Exception as exc:
        return f"Failed to rip file ranges: {exc}"


@register("find_dependent_references")
async def find_dependent_references(target_symbol: str, repo_path: str) -> str:
    """Scan a repo for imports or callers of a symbol before changing it.
    Read-only. Returns file:line → matching text for every hit."""
    repo_path = os.path.expanduser(repo_path)
    found: list[str] = []
    for root, _, files in os.walk(repo_path):
        if any(p in root for p in ["venv", ".git", "__pycache__", "node_modules"]):
            continue
        for file in sorted(files):
            if not file.endswith(".py"):
                continue
            f_path = os.path.join(root, file)
            try:
                with open(f_path, "r", encoding="utf-8") as f:
                    for idx, line in enumerate(f, 1):
                        if target_symbol in line:
                            rel = os.path.relpath(f_path, repo_path)
                            found.append(f"{rel}:{idx} → {line.strip()}")
            except Exception:
                continue
    if not found:
        return f"No references found for '{target_symbol}'."
    return "\n".join(found)


# ═══════════════════════════════════════════════════════════
# B. Surgical code modification
# ═══════════════════════════════════════════════════════════

@register("apply_search_replace")
async def apply_search_replace(
    file_path: str, search_block: str, replace_block: str, ctx: Context
) -> str:
    """Swap an exact block of code for a new one. First occurrence only.
    The search_block must match byte-for-byte including indentation."""
    if not os.path.exists(file_path):
        return f"Error: File {file_path} not found."
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        if search_block not in content:
            return (
                "Error: TARGET SEARCH BLOCK NOT FOUND EXACTLY. "
                "Check spacing, indentation, and line endings."
            )

        updated = content.replace(search_block, replace_block, 1)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(updated)

        full_rewrite = estimate_tokens(updated)
        patch = estimate_tokens(search_block + replace_block)
        saved = max(0, full_rewrite - patch)
        metric = await emit_metric(ctx, "apply_search_replace", saved, full_rewrite, patch)
        return "Surgical replacement applied successfully.\n\n" + metric
    except Exception as exc:
        return f"Replacement engine exception: {exc}"


# ═══════════════════════════════════════════════════════════
# C. Safety and version control
# ═══════════════════════════════════════════════════════════

@register("lint_file")
async def lint_file(file_path: str) -> str:
    """Run py_compile on a single Python file. Returns 'Syntax clean'
    or the compiler error message."""
    if not os.path.exists(file_path):
        return f"Error: File {file_path} not found."
    res = subprocess.run(
        ["python3", "-m", "py_compile", file_path],
        capture_output=True, text=True, shell=False,
    )
    if res.returncode != 0:
        return f"SYNTAX ERROR:\n{res.stderr.strip()}"
    return f"Syntax clean: {os.path.basename(file_path)}"


@register("git_checkpoint")
async def git_checkpoint(file_path: str, change_summary: str) -> str:
    """Stage and commit a single file with a descriptive message.
    Call only at meaningful milestones, not after every edit."""
    repo_dir = os.path.dirname(os.path.abspath(file_path))
    try:
        subprocess.run(
            ["git", "add", file_path], cwd=repo_dir,
            check=True, shell=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_dir,
            capture_output=True, text=True, shell=False,
        )
        if not status.stdout.strip():
            return "Nothing to commit — file unchanged since last commit."
        subprocess.run(
            ["git", "commit", "-m", f"[harness] {change_summary}"],
            cwd=repo_dir, check=True, shell=False,
        )
        return f"Committed: {change_summary}"
    except Exception as exc:
        return f"Git checkpoint failed: {exc}"


@register("rollback_show")
async def rollback_show(repo_path: str) -> str:
    """READ-ONLY. Shows what would be lost if the user resets the repo.
    Does NOT execute any rollback. Presents copyable commands."""
    try:
        diff_stat = subprocess.run(
            ["git", "diff", "--stat"], cwd=repo_path,
            capture_output=True, text=True, shell=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_path,
            capture_output=True, text=True, shell=False,
        )
        stash = subprocess.run(
            ["git", "stash", "list"], cwd=repo_path,
            capture_output=True, text=True, shell=False,
        )

        r = ["═══ ROLLBACK IMPACT REPORT ═══", ""]
        r.append("Modified tracked files:")
        r.append(diff_stat.stdout.strip() or "  (none)")

        untracked = [l for l in status.stdout.splitlines() if l.startswith("??")]
        if untracked:
            r.append("")
            r.append("Untracked files (would be DELETED by git clean):")
            r += [f"  {u}" for u in untracked]

        if stash.stdout.strip():
            r.append("")
            r.append("Existing stashes (preserved by reset):")
            r.append(stash.stdout.strip())

        r += [
            "",
            "⚠️  Review the above. Copy a command below and run it yourself,",
            "   or reply with the command and I will run it for you.",
            "",
            "Full rollback (loses everything shown above):",
            "    git reset --hard HEAD && git clean -fd",
            "",
            "Undo tracked-file edits only (keeps untracked files):",
            "    git checkout -- .",
            "",
            "Reversible stash instead of delete:",
            "    git stash push -u -m 'harness rollback'",
        ]
        return "\n".join(r)
    except Exception as exc:
        return f"Rollback inspection failed: {exc}"


# ═══════════════════════════════════════════════════════════
# D. Execution and introspection
# ═══════════════════════════════════════════════════════════

@register("execute_and_capture")
async def execute_and_capture(command: str, timeout_seconds: int = 15) -> str:
    """Run a whitelisted command. Output is clipped to 30 head + 30 tail
    lines to prevent token burn. Uses shell=False — no command injection."""
    parts = shlex.split(command)
    if not parts:
        return "Error: empty command."
    if parts[0] not in ALLOWED_COMMANDS:
        return (
            f"Error: '{parts[0]}' is not whitelisted. "
            f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
        )
    try:
        res = subprocess.run(
            parts, capture_output=True, text=True,
            timeout=timeout_seconds, shell=False,
        )
        raw = f"{res.stdout}\n{res.stderr}".strip()
        lines = raw.splitlines()
        if len(lines) > 60:
            return "\n".join(
                lines[:30]
                + ["\n... [ LOGS CLIPPED TO PREVENT TOKEN BURN ] ...\n"]
                + lines[-30:]
            )
        return raw if raw else "Execution completed with zero logs."
    except subprocess.TimeoutExpired:
        return f"CRITICAL: timed out after {timeout_seconds}s."
    except Exception as exc:
        return f"Process failure: {exc}"


@register("inspect_database_schema")
async def inspect_database_schema(db_path: str) -> str:
    """Read-only. Lists every SQLite table with columns, types, and PKs."""
    db_path = os.path.expanduser(db_path)
    if not os.path.exists(db_path):
        return f"Error: Database not found at {db_path}"
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cur.fetchall()
        if not tables:
            conn.close()
            return "SQLite DB exists but contains no tables."
        out = []
        for (t_name,) in tables:
            out.append(f"\n📊 TABLE: {t_name}")
            cur.execute(f"PRAGMA table_info({t_name});")
            for col in cur.fetchall():
                pk = " [PRIMARY KEY]" if col[5] else ""
                out.append(f"  - {col[1]} ({col[2]}){pk}")
        conn.close()
        return "\n".join(out)
    except Exception as exc:
        return f"Schema extraction failed: {exc}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
