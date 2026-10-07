"""Harness-Optimizer MCP tools for OpenCode.

Token-optimized code editing helpers exposed over MCP (stdio).

Environment:
  HARNESS_TOOLS  Optional. "all" (default) or a comma-separated whitelist of
                 tool names. Disabled tools are not registered with the MCP
                 client, so the model never sees them.
"""
import ast
import os
import re
import shlex
import sqlite3
import subprocess

from fastmcp import Context, FastMCP

mcp = FastMCP("HarnessTools")

# ── Tool toggle ─────────────────────────────────────────────
_raw = os.environ.get("HARNESS_TOOLS", "all").strip().lower()
if _raw in {"", "none"}:
    _ENABLED = set()
elif _raw == "all":
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
MAX_OUTPUT_LINES = 60
MAX_OUTPUT_CHARS = 200_000

def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


async def emit_metric(ctx: Context, tool: str, saved: int, baseline: int, actual: int) -> str:
    line = (
        f"[TOKEN METRIC] tool={tool} saved={saved:,} "
        f"baseline={baseline:,} actual={actual:,} type=payload_reduction"
    )
    await ctx.info(line)
    return line

# ═══════════════════════════════════════════════════════════
# A. Context reading
# ═══════════════════════════════════════════════════════════

@register("get_repo_skeleton")
async def get_repo_skeleton(repo_path: str = ".", max_files: int = 500):
    import ast
    root = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(root):
        return f"ERROR: Not a directory: {root}"

    SKIP_DIRS = {".git", "venv", ".venv", "env", "__pycache__",
                 "node_modules", "dist", "build", ".tox", ".mypy_cache",
                 ".pytest_cache", "site-packages"}

    skeleton_lines = []
    total_raw_chars = 0
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            if file_count >= max_files:
                break
            file_count += 1
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, root)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    src = f.read()
                total_raw_chars += len(src)
                tree = ast.parse(src)
            except (SyntaxError, OSError):
                skeleton_lines.append(f"{rel}: (parse failed)")
                continue

            skeleton_lines.append(f"{rel}:")
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    skeleton_lines.append(f"  class {node.name}:")
                    for sub in node.body:
                        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            args = [a.arg for a in sub.args.args]
                            skeleton_lines.append(
                                f"    def {sub.name}({', '.join(args)}):"
                            )
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Skip methods: already handled inside ClassDef
                    if any(isinstance(p, ast.ClassDef) for p in ast.walk(tree)
                           if hasattr(p, "body") and node in getattr(p, "body", [])):
                        continue
                    args = [a.arg for a in node.args.args]
                    skeleton_lines.append(
                        f"  def {node.name}({', '.join(args)}):"
                    )

    skeleton_text = "\n".join(skeleton_lines)

    baseline = estimate_tokens(" " * total_raw_chars)
    actual = estimate_tokens(skeleton_text)
    saved = max(0, baseline - actual)
    metric = (
        f"[TOKEN METRIC] tool=get_repo_skeleton "
        f"saved={saved} baseline={baseline} actual={actual} type=payload_reduction"
    )

    return skeleton_text + "\n\n" + metric

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
async def apply_search_replace(file_path: str, search_block: str, replace_block: str):
    path = os.path.expanduser(file_path)
    if not os.path.exists(path):
        return f"ERROR: File not found: {path}"
        
    if not search_block.strip():
        return "ERROR: Search block cannot be empty or whitespace only."
        
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        
    count = content.count(search_block)
    if count == 0:
        return f"ERROR: TARGET SEARCH BLOCK NOT FOUND EXACTLY in {path}"
    if count > 1:
        return f"ERROR: Search block matches {count} locations. Please provide more context to make it unique."
        
    new_content = content.replace(search_block, replace_block, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
        
    full_rewrite = estimate_tokens(new_content)
    patch = estimate_tokens(search_block + replace_block)
    saved = max(0, full_rewrite - patch)
    metric = f"[TOKEN METRIC] tool=apply_search_replace saved={saved} baseline={full_rewrite} actual={patch} type=payload_reduction"
    return "Surgical replacement applied successfully.\n\n" + metric

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
async def git_checkpoint(file_path: str, change_summary: str):
    import subprocess
    path = os.path.abspath(os.path.expanduser(file_path))
    repo_dir = os.path.dirname(path)
    
    # Check if there are other staged files before we commit
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_dir,
        capture_output=True,
        text=True
    )
    # Porcelain output for staged files begins with characters like 'M ', 'A ', 'D ', 'R '
    staged_files = [
        line[3:].strip() for line in status.stdout.splitlines()
        if len(line) > 3 and line[0] not in (' ', '?')
    ]
    
    rel_path = os.path.relpath(path, repo_dir)
    
    if staged_files and staged_files != [rel_path]:
        return (
            "ERROR: Unrelated staged files detected. "
            "Please commit or unstage them before using git_checkpoint.\n"
            f"Staged files: {', '.join(staged_files)}"
        )
        
    subprocess.run(["git", "add", rel_path], cwd=repo_dir)
    
    # Use '-- <path>' to commit ONLY this file, ignoring any other staged changes
    result = subprocess.run(
        ["git", "commit", "-m", f"[harness] {change_summary}", "--", rel_path],
        cwd=repo_dir,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        return f"ERROR: Commit failed:\n{result.stderr}\n{result.stdout}"
        
    return f"Committed: {change_summary}"

@register("rollback_show")
async def rollback_show(repo_path: str) -> str:
    """READ-ONLY. Shows what would be lost if the user resets the repo.
    Does NOT execute any rollback. Presents copyable commands."""
    try:
        diff_staged = subprocess.run(
            ["git", "diff", "--cached", "--stat"], cwd=repo_path,
            capture_output=True, text=True, shell=False,
        )
        diff_unstaged = subprocess.run(
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
        staged_text = diff_staged.stdout.strip()
        unstaged_text = diff_unstaged.stdout.strip()
        r.append("Staged changes (in index, would be lost by reset):")
        r.append(staged_text or "  (none)")
        r.append("")
        r.append("Unstaged changes (working tree, would be lost by checkout/reset):")
        r.append(unstaged_text or "  (none)")
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

# ── Bypass guard for execute_and_capture ──
# The whitelist is a convenience filter, NOT a sandbox. Interpreters like
# python3 are Turing-complete and can write files or spawn subprocesses,
# sidestepping the surgical edit tools. Refuse the most obvious cases so
# apply_search_replace / OpenCode's native write tools are used instead.
_BYPASS_PATTERNS = [
    re.compile(r"\bopen\s*\([^)]*['\"][wa]['\"]"),
    re.compile(r"\bos\.system\s*\("),
    re.compile(r"\bsubprocess\.(run|Popen|call|check_output|check_call)"),
    re.compile(r"\bshutil\.(rmtree|move|copy|copyfile)"),
    re.compile(r"\bos\.(remove|unlink|rmdir|rename|chmod|chown|mkdir|makedirs)"),
    re.compile(r"\.write_text\s*\(|\.write_bytes\s*\("),
    re.compile(r"\bPath\s*\([^)]*\)\.(unlink|write_text|write_bytes)\s*\("),
    re.compile(r"-exec\s+(rm|mv|cp|sh|bash|python|perl)"),
    re.compile(r"(?<!\w)-delete(?!\w)"),
]

def _bypass_reason(parts: list):
    """Return a reason string if a whitelisted command is being abused
    as a shell escape hatch. Best-effort, not a security boundary."""
    if not parts:
        return None
    binname = parts[0]
    joined = " ".join(parts[1:])
    if binname in ("python", "python3"):
        if "-c" in parts:
            for pat in _BYPASS_PATTERNS:
                if pat.search(joined):
                    return f"refused: python -c matches dangerous pattern ({pat.pattern})"
    if binname == "find":
        for pat in _BYPASS_PATTERNS:
            if pat.search(joined):
                return f"refused: find argument matches dangerous pattern ({pat.pattern})"
    if binname == "git":
        if "config" in parts and ("--global" in parts or "--system" in parts):
            return "refused: git config --global is not permitted via execute_and_capture"
    return None

@register("execute_and_capture")
async def execute_and_capture(command: str, timeout_seconds: int = 15):
    import shlex
    import subprocess
    parts = shlex.split(command)
    if not parts or parts[0] not in ALLOWED_COMMANDS:
        return f"ERROR: Command '{parts[0] if parts else ''}' is not allowed. Allowed: {', '.join(ALLOWED_COMMANDS)}"
    
    reason = _bypass_reason(parts)
    if reason:
        return f"ERROR: {reason}"
    
    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False
        )

        output = result.stdout + "\n" + result.stderr
        lines = output.splitlines()
        over_lines = len(lines) > MAX_OUTPUT_LINES
        over_chars = len(output) > MAX_OUTPUT_CHARS

        response = [
            f"Exit code: {result.returncode}",
            f"Timed out: no",
            f"Output truncated: {'yes' if (over_lines or over_chars) else 'no'}",
        ]

        if over_lines:
            truncated = "\n".join(
                lines[: MAX_OUTPUT_LINES // 2]
                + ["\n... [ LOGS CLIPPED: line limit reached ] ...\n"]
                + lines[-(MAX_OUTPUT_LINES // 2):]
            )
            response.append("Relevant output:\n" + truncated)
        elif over_chars:
            half = MAX_OUTPUT_CHARS // 2
            head = output[:half]
            tail = output[-half:]
            dropped = len(output) - MAX_OUTPUT_CHARS
            response.append(
                "Relevant output:\n"
                + head
                + f"\n... [ CLIPPED: {dropped} chars dropped to stay under {MAX_OUTPUT_CHARS} ] ...\n"
                + tail
            )
        else:
            response.append("Relevant output:\n" + output)

        return "\n".join(response)

    except subprocess.TimeoutExpired:
        return (
            "Exit code: -1\n"
            "Timed out: yes\n"
            "Output truncated: no\n"
            "Relevant output:\n"
            f"Command timed out after {timeout_seconds} seconds."
        )

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
