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

    def symbol_span(node) -> str:
        start = node.lineno
        decs = getattr(node, "decorator_list", None) or []
        if decs:
            start = min(start, min(d.lineno for d in decs))
        end = getattr(node, "end_lineno", None) or start
        return f"[L{start}-L{end}]"

    CONTROL_FLOW = (ast.If, ast.Try, ast.With, ast.For, ast.While,
                    ast.AsyncWith, ast.AsyncFor)

    def collect_symbols(nodes, depth=1):
        """Return (depth, node) for every class/function, recursing into
        bodies — including nested defs and defs inside conditional blocks.
        Each node is visited exactly once."""
        out = []
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((depth, node))
                out.extend(collect_symbols(node.body, depth + 1))
            elif isinstance(node, CONTROL_FLOW):
                out.extend(collect_symbols(node.body, depth))
                orelse = getattr(node, "orelse", None) or []
                out.extend(collect_symbols(orelse, depth))
                finalbody = getattr(node, "finalbody", None) or []
                out.extend(collect_symbols(finalbody, depth))
                for handler in getattr(node, "handlers", None) or []:
                    out.extend(collect_symbols(handler.body, depth))
        return out

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
            for depth, node in collect_symbols(tree.body, depth=1):
                indent = "  " * depth
                if isinstance(node, ast.ClassDef):
                    skeleton_lines.append(
                        f"{indent}class {node.name}: {symbol_span(node)}"
                    )
                else:
                    args = [a.arg for a in node.args.args]
                    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                    skeleton_lines.append(
                        f"{indent}{prefix} {node.name}({', '.join(args)}): {symbol_span(node)}"
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
async def find_dependent_references(
    target_symbol: str,
    repo_path: str,
    max_results: int = 40,
    max_chars: int = 8000,
) -> str:
    """Bounded text search for a symbol in Python files.

    Substring search, NOT semantic dependency analysis. Matches any
    line containing the symbol text, including comments and strings.
    Use as a hint, not a verdict.

    Args:
        target_symbol: text to search for (non-empty, not whitespace-only).
        repo_path: repository root to search (must be a directory).
        max_results: cap on returned matches (default 40, min 1).
        max_chars: total response size cap in characters (default 8000,
            min 256). Applies to header + matches + status line.

    Reports truncation explicitly and notes unreadable files so the
    caller knows whether the search was complete.
    """
    if not target_symbol or not target_symbol.strip():
        return "ERROR: target_symbol must be non-empty and not whitespace-only."
    if max_results < 1:
        return "ERROR: max_results must be >= 1."
    if max_chars < 256:
        return "ERROR: max_chars must be >= 256."

    root_path = os.path.abspath(os.path.expanduser(repo_path))
    if not os.path.isdir(root_path):
        return f"ERROR: Not a directory: {root_path}"

    SKIP_DIRS = {"venv", ".venv", "env", ".git", "__pycache__",
                 "node_modules", "dist", "build", ".tox", ".mypy_cache",
                 ".pytest_cache", "site-packages"}

    PER_LINE_MAX = 200
    SYMBOL_DISPLAY_MAX = 80

    def shorten_match(match: str) -> str:
        if " \u2192 " in match:
            loc, _, text = match.partition(" \u2192 ")
            if len(text) > PER_LINE_MAX:
                text = text[: PER_LINE_MAX - 3] + "..."
            return f"{loc} \u2192 {text}"
        return match

    matches: list[str] = []
    overflow_by_count = False
    unreadable_count = 0
    cap = max_results + 1

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for file in sorted(filenames):
            if not file.endswith(".py"):
                continue
            if len(matches) >= cap:
                overflow_by_count = True
                break
            f_path = os.path.join(dirpath, file)
            try:
                with open(f_path, "r", encoding="utf-8", errors="ignore") as f:
                    for idx, line in enumerate(f, 1):
                        if target_symbol in line:
                            rel = os.path.relpath(f_path, root_path)
                            matches.append(f"{rel}:{idx} \u2192 {line.rstrip()}")
                            if len(matches) >= cap:
                                overflow_by_count = True
                                break
            except Exception:
                unreadable_count += 1
                continue
            if overflow_by_count:
                break
        if overflow_by_count:
            break

    symbol_display = target_symbol
    if len(symbol_display) > SYMBOL_DISPLAY_MAX:
        symbol_display = symbol_display[: SYMBOL_DISPLAY_MAX - 3] + "..."

    truncated_by_count = overflow_by_count and len(matches) > max_results
    if truncated_by_count:
        matches = matches[:max_results]

    base_status_bits: list[str] = []
    if truncated_by_count:
        base_status_bits.append(f"count limit {max_results} reached")
    if unreadable_count > 0:
        base_status_bits.append(f"{unreadable_count} file(s) unreadable; search may be incomplete")

    def build_status(extra: str = "") -> str:
        bits = base_status_bits[:]
        if extra:
            bits.append(extra)
        if not bits:
            return ""
        return ("\n# Truncated: " + "; ".join(bits)
                + ". Narrow the symbol or subdirectory, or increase max_results/max_chars.")

    if not matches:
        out = f"No text matches for '{symbol_display}' in the searched scope." + build_status()
        if len(out) > max_chars:
            out = out[: max_chars - 20] + "\n# ... clipped to max_chars"
        return out

    word = "match" if len(matches) == 1 else "matches"
    header = (
        f"# find_dependent_references: '{symbol_display}' "
        f"({len(matches)} {word} shown)\n"
        f"# Text match only \u2014 not proof of callers or semantic dependencies."
    )

    matches = [shorten_match(m) for m in matches]

    # Reserve for status line; scale down for small budgets.
    probe = build_status("char limit 999999 reached (99999 more match(es) omitted)")
    if max_chars >= 1000:
        status_reserve = len(probe)
    elif max_chars >= 500:
        status_reserve = min(len(probe), 150)
    else:
        status_reserve = 0  # too tight; skip status and rely on header count

    body_budget = max_chars - len(header) - status_reserve - 1
    if body_budget < 40 and matches:
        # Fallback: minimal header, drop the second descriptive line.
        header = (f"# find_dependent_references: '{symbol_display}' "
                  f"({len(matches)} {word})")
        body_budget = max_chars - len(header) - status_reserve - 1

    body_lines: list[str] = []
    body_used = 0
    skipped = 0
    for i, match in enumerate(matches):
        sep = 1 if body_lines else 0
        if body_used + sep + len(match) <= body_budget:
            body_lines.append(match)
            body_used += sep + len(match)
        else:
            skipped = len(matches) - i
            break

    extra = ""
    if skipped > 0:
        extra = f"char limit {max_chars} reached ({skipped} more match(es) omitted)"

    out = header + "\n" + "\n".join(body_lines) + build_status(extra)
    if len(out) > max_chars:
        out = out[: max_chars - 20] + "\n# ... hard-clipped to max_chars"
    return out


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
    """Python syntax check only (py_compile). Not a linter, type checker, or
    behavioral test runner. Returns one of:
      - OK: ... on successful Python syntax check
      - FAIL: ... on syntax error or missing file
      - SKIPPED: unsupported file type for non-.py files — use the
        repository's own configured checks instead.
    """
    path = os.path.expanduser(file_path)
    ext = os.path.splitext(path)[1].lower()

    if ext != ".py":
        shown = ext if ext else "(no extension)"
        return (
            f"SKIPPED: unsupported file type {shown} — use the repository's "
            f"configured checks. lint_file only performs Python syntax checks."
        )

    if not os.path.exists(path):
        return f"FAIL: file not found — {path}"

    try:
        res = subprocess.run(
            ["python3", "-m", "py_compile", path],
            capture_output=True, text=True, shell=False,
        )
    except FileNotFoundError:
        return "FAIL: python3 not available on PATH; cannot run py_compile."

    if res.returncode != 0:
        detail = (res.stderr or "").strip() or "(no stderr)"
        return f"FAIL: Python syntax error — {os.path.basename(path)}\n{detail}"

    return f"OK: Python syntax check passed — {os.path.basename(path)}"

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
            "⚠️  Review the above. Rollback is manual — copy a command below and run",
            "   it yourself in your terminal. This tool does not execute rollback.",
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
        raw_len = len(output)
        lines = output.splitlines()
        over_lines = len(lines) > MAX_OUTPUT_LINES
        over_chars = raw_len > MAX_OUTPUT_CHARS

        # Apply limits cumulatively: trim lines first, then apply char cap.
        # Metadata notes are collected BEFORE the payload so they survive
        # any downstream char truncation.
        kept = output
        limit_notes = []

        if over_lines:
            kept = "\n".join(
                lines[: MAX_OUTPUT_LINES // 2]
                + [f"... [ LOGS CLIPPED: line limit reached ({len(lines)} lines > {MAX_OUTPUT_LINES}) ] ..."]
                + lines[-(MAX_OUTPUT_LINES // 2):]
            )
            limit_notes.append(f"line limit reached ({len(lines)} lines > {MAX_OUTPUT_LINES})")

        if len(kept) > MAX_OUTPUT_CHARS:
            half = MAX_OUTPUT_CHARS // 2
            dropped = len(kept) - MAX_OUTPUT_CHARS
            kept = (
                kept[:half]
                + f"\n... [ CLIPPED: {dropped} chars dropped to stay under {MAX_OUTPUT_CHARS} ] ...\n"
                + kept[-half:]
            )
            limit_notes.append(f"{dropped} chars dropped to stay under {MAX_OUTPUT_CHARS}")

        response = [
            f"Exit code: {result.returncode}",
            f"Timed out: no",
            f"Output truncated: {'yes' if limit_notes else 'no'}",
        ]
        if limit_notes:
            response.append("Applied limits: " + "; ".join(limit_notes))
        response.append("Relevant output:\n" + kept)

        return "\n".join(response)
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
