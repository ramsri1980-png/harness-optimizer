"""Harness-Optimizer MCP tools for OpenCode.

Token-optimized code editing helpers exposed over MCP (stdio).

Environment:
  HARNESS_TOOLS  Optional. "all" (default) or a comma-separated whitelist of
                 tool names. Disabled tools are not registered with the MCP
                 client, so the model never sees them.
"""
import ast
import hashlib
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile

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
async def get_repo_skeleton(repo_path: str = ".", max_files: int = 500,
                            mode: str = "outline", max_tokens: int = 1500):
    import ast
    if mode not in ("outline", "ranked"):
        return f"ERROR: unsupported mode '{mode}' — use 'outline' or 'ranked'"
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

    outline = skeleton_text + "\n\n" + metric
    if mode == "outline":
        return outline

    # mode == "ranked": return ONLY the ranked map + metric (no outline).
    try:
        from harness_core import repomap_adapter
        ranked, raw_bytes = repomap_adapter.build_ranked_map_with_stats(
            root, max_tokens=max_tokens)
    except Exception as exc:
        ranked = f"ERROR: ranked map unavailable — {exc}"
        raw_bytes = 0

    if isinstance(ranked, str) and ranked.startswith("ERROR"):
        reason = ranked.split("ERROR:", 1)[-1].strip()
        redundant = "ranked map unavailable — "
        if reason.startswith(redundant):
            reason = reason[len(redundant):]
        note = f"# ranked mode unavailable — {reason}; falling back to outline"
        return note + "\n" + outline

    # Honest metric: the ranked text is a truncation of the raw sources,
    # so the baseline is the token count of the bytes actually read
    # (``raw_bytes``, summed from os.path.getsize over the walked files),
    # and the actual is the token count of the returned ranked text.
    # No estimate is derived from the output length itself.
    ranked_actual = estimate_tokens(ranked)
    ranked_baseline = estimate_tokens(" " * raw_bytes)
    ranked_saved = max(0, ranked_baseline - ranked_actual)
    ranked_metric = (
        f"[TOKEN METRIC] tool=get_repo_skeleton "
        f"saved={ranked_saved} baseline={ranked_baseline} actual={ranked_actual} "
        f"type=payload_reduction"
    )
    return ranked + "\n\n" + ranked_metric

# T02 tuning constants for rip_file_lines
_RIP_MAX_CHARS = 4000          # hard response cap
_RIP_MAX_EXPAND = 500          # enclosing-symbol expansion cap
_RIP_HEAD_LINES = 20           # source lines kept at head when truncating
_RIP_TAIL_LINES = 20           # source lines kept at tail when truncating
_RIP_METRIC_RESERVE = 160      # conservative room for [TOKEN METRIC] line


def _fit_lines(lines, budget):
    """Return complete lines from `lines` whose joined length fits
    within `budget`. Preserves order. Never slices a line."""
    out = []
    used = 0
    for ln in lines:
        add = len(ln) + (1 if out else 0)
        if used + add > budget:
            break
        out.append(ln)
        used += add
    return out


def _fit_or_mark(lines, budget, inline=" ... [truncated] ..."):
    """_fit_lines, with one documented exception: when the next line
    alone exceeds `budget`, keep that line's leading slice with an
    explicit in-line marker instead of silently dropping it. The numeric
    prefix sits at the start of every line, so it survives the slice."""
    out = _fit_lines(lines, budget)
    if len(out) < len(lines):
        nxt = lines[len(out)]
        if len(nxt) > budget:
            room = budget - len(inline) - (1 if out else 0)
            if room > 0:
                out = out + [nxt[:room] + inline]
    return out


def _enclosing_definition(lines, start_line):
    """Innermost class/function containing start_line, as (kind, name,
    def_start, def_end). def_start includes decorator lines. None if the
    file is unparsable or nothing encloses the line."""
    src = "\n".join(lines)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None

    best = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        def_end = getattr(node, "end_lineno", None)
        if def_end is None:
            continue
        decs = getattr(node, "decorator_list", None) or []
        def_start = min([node.lineno] + [d.lineno for d in decs])
        if def_start <= start_line <= def_end:
            # best = (kind, name, def_start, def_end); compare def_start
            # so the deepest nested symbol containing the line wins.
            if best is None or def_start > best[2]:
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                best = (kind, node.name, def_start, def_end)
    return best


@register("rip_file_lines")
async def rip_file_lines(file_path: str, start_line: int, end_line: int, ctx: Context,
                         context: str = "raw") -> str:
    """View a precise line window from a file. Use this instead of reading
    full files. Line numbers are 1-indexed and printed for reference.

    Response format:
        --- Lines A to B of T ---
        # sha256: <hex of raw file bytes>
        A: <source line>
        ...

    The sha256 line fingerprints the exact bytes the excerpt was built
    from, so a later rip can be checked for staleness.

    context="raw" (default) returns exactly the requested range.
    context="enclosing" expands the range to the whole enclosing
    class/function (up to 500 lines) when the request falls inside one.

    Bounds: start_line < 1 or end_line < start_line is an ERROR; a
    start beyond EOF is an ERROR; an end beyond EOF is silently clipped
    and the header reports the actual returned bounds. An empty file
    yields the header "Lines 1 to 0 of 0" plus the sha256 of the empty
    string (e3b0c442...b855).

    Whitespace: only the line terminator is removed when splitting
    (\r\n or \n); trailing spaces/tabs are preserved in the output and
    CRLF input is emitted as LF.

    Output is capped at 4000 characters; oversized excerpts keep the
    first and last 20 source lines with a truncation marker."""
    file_path = os.path.expanduser(file_path)
    if not os.path.exists(file_path):
        return f"Error: File {file_path} not found."
    if os.path.isdir(file_path):
        return f"ERROR: {file_path} is a directory, not a file"
    if start_line < 1 or end_line < start_line:
        return "ERROR: invalid range — start_line must be >= 1 and end_line >= start_line"
    if context not in ("raw", "enclosing"):
        return f"ERROR: unsupported context mode '{context}' - use 'raw' or 'enclosing'"

    basename = os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
    except OSError as exc:
        return f"Failed to rip file ranges: {exc}"

    fingerprint = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8", errors="replace")
    # Disclose any substitution so the caller knows the view is lossy.
    utf8_lossy = "\ufffd" in text
    # Split on \n and drop a single trailing \r per line; a trailing
    # newline does not create an extra phantom line.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in lines]
    total = len(lines)

    if total == 0:
        # Empty file: emit the sha256 of the empty string and a
        # "Lines 1 to 0 of 0" header (documented convention).
        out = [f"--- Lines 1 to 0 of 0 ---", f"# sha256: {fingerprint}"]
        ripped = "\n".join(out)
        if ctx is not None:
            baseline = estimate_tokens(text)
            actual = estimate_tokens(ripped)
            saved = max(0, baseline - actual)
            ripped += "\n\n" + await emit_metric(ctx, "rip_file_lines", saved, baseline, actual)
        return ripped

    if start_line > total:
        return f"ERROR: start_line {start_line} exceeds file length {total} — {basename}"

    # Clip the end to the real file length; the header reports the
    # bounds actually returned.
    end_line = min(end_line, total)
    req_start, req_end = start_line, end_line

    notes = []
    if context == "enclosing":
        if not file_path.lower().endswith(".py"):
            notes.append("# context: not a Python file, or parse failed; using raw range")
        else:
            enc = _enclosing_definition(lines, start_line)
            if enc is None:
                notes.append("# context: not a Python file, or parse failed; using raw range")
            else:
                kind, name, def_start, def_end = enc
                span = def_end - def_start + 1
                if span > _RIP_MAX_EXPAND:
                    notes.append(f"# context: enclosing {kind} {name} is {span} lines; exceeds {_RIP_MAX_EXPAND}-line cap")
                    notes.append("# context: falling back to raw range")
                else:
                    new_start = min(start_line, def_start)
                    new_end = max(end_line, def_end)
                    if (new_start, new_end) != (req_start, req_end):
                        # req_end is already the clipped value, so the
                        # note describes the range actually read, not
                        # the user's unfulfilled request beyond EOF.
                        notes.append(
                            f"# context: expanded from lines {req_start}-{req_end} "
                            f"to lines {new_start}-{new_end} (enclosing {kind} {name})"
                        )
                        start_line, end_line = new_start, new_end

    header = f"--- Lines {start_line} to {end_line} of {total} ---"
    body = [f"{idx}: {lines[idx - 1]}" for idx in range(start_line, end_line + 1)]
    head_lines = [header, f"# sha256: {fingerprint}"]
    if utf8_lossy:
        head_lines.append(
            "# note: file contained invalid UTF-8; some bytes were "
            "replaced with U+FFFD in this view"
        )
    head_lines += notes

    ripped = "\n".join(head_lines + body)

    if len(ripped) > _RIP_MAX_CHARS:
        head = body[:_RIP_HEAD_LINES]
        tail = body[-_RIP_TAIL_LINES:] if len(body) > _RIP_HEAD_LINES else []
        marker = "    ... [truncated] ..."
        footer = "    (truncated; request a narrower range for full content)"
        prefix = "\n".join(head_lines)
        avail = (_RIP_MAX_CHARS - len(prefix) - len(marker)
                 - len(footer) - 4 - _RIP_METRIC_RESERVE)
        if avail > 0:
            if tail:
                hb = avail // 2
                tb = avail - hb
                # Take complete lines only: the tail is consumed in
                # reverse so the LAST line survives, then re-reversed.
                head_txt = "\n".join(_fit_or_mark(head, hb))
                tail_txt = "\n".join(
                    reversed(_fit_or_mark(list(reversed(tail)), tb)))
            else:
                # Fewer head lines than the head budget: the whole
                # avail goes to head instead of being half-wasted.
                head_txt = "\n".join(_fit_or_mark(head, avail))
                tail_txt = ""
            ripped = "\n".join(p for p in (prefix, head_txt, marker, tail_txt, footer) if p)
        else:
            ripped = "\n".join([prefix, marker, footer])

    if ctx is not None:
        baseline = estimate_tokens(text)
        actual = estimate_tokens(ripped)
        saved = max(0, baseline - actual)
        metric = await emit_metric(ctx, "rip_file_lines", saved, baseline, actual)
        if metric:
            ripped = ripped + "\n\n" + metric

    if len(ripped) > _RIP_MAX_CHARS:
        footer = "    (truncated; request a narrower range for full content)"
        ripped = ripped[:_RIP_MAX_CHARS - len(footer) - 1] + "\n" + footer

    return ripped

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

    SYMBOL_DISPLAY_MAX = 80
    PER_LINE_MAX = 200
    HARD_CLIP_SUFFIX = "\n# ... hard-clipped to max_chars"

    # ── collect matches ──
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

    # ── helpers ──
    symbol_display = target_symbol
    if len(symbol_display) > SYMBOL_DISPLAY_MAX:
        symbol_display = symbol_display[: SYMBOL_DISPLAY_MAX - 3] + "..."

    def hard_clip(text: str) -> str:
        if len(text) <= max_chars:
            return text
        if max_chars <= len(HARD_CLIP_SUFFIX):
            return text[:max_chars]
        return text[: max_chars - len(HARD_CLIP_SUFFIX)] + HARD_CLIP_SUFFIX

    def shorten_text(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return text[: limit - 3] + "..."

    truncated_by_count = overflow_by_count and len(matches) > max_results
    if truncated_by_count:
        matches = matches[:max_results]

    base_status_bits: list[str] = []
    if truncated_by_count:
        base_status_bits.append(f"count limit {max_results} reached")
    if unreadable_count > 0:
        base_status_bits.append(
            f"{unreadable_count} file(s) unreadable; search may be incomplete"
        )

    def build_status(extra: str = "") -> str:
        bits = base_status_bits[:]
        if extra:
            bits.append(extra)
        if not bits:
            return ""
        # Compact, single line; hard_clip protects total size.
        return "\n# " + "; ".join(bits)

    # ── no-match branch ──
    if not matches:
        out = f"No text matches for '{symbol_display}' in the searched scope."
        out += build_status()
        return hard_clip(out)

    # ── prepare (loc, text) pairs with per-line cap ──
    prepared: list[tuple[str, str]] = []
    for m in matches:
        if " \u2192 " in m:
            loc, _, text = m.partition(" \u2192 ")
            prepared.append((loc, shorten_text(text, PER_LINE_MAX)))
        else:
            prepared.append(("", m))

    def make_header(n_shown: int) -> str:
        word = "match" if n_shown == 1 else "matches"
        return (
            f"# find_dependent_references: '{symbol_display}' "
            f"({n_shown} {word} shown)\n"
            f"# Text match only \u2014 not proof of callers or semantic dependencies."
        )

    # ── fit matches greedily, keeping locations ──
    body_lines: list[str] = []
    skipped = 0

    for i, (loc, text) in enumerate(prepared):
        prospective = len(body_lines) + 1
        header_len = len(make_header(prospective))
        remaining = len(prepared) - prospective
        extra_status = ""
        if remaining > 0:
            extra_status = (
                f"char limit {max_chars} reached ({remaining} more match(es) omitted)"
            )
        status_len = len(build_status(extra_status))
        budget = max_chars - header_len - status_len - 1

        sep = 1 if body_lines else 0
        used = sum(len(x) for x in body_lines) + max(0, len(body_lines) - 1)
        available = budget - used - sep

        if available < 12:
            skipped = len(prepared) - i
            break

        full = f"{loc} \u2192 {text}" if loc else text

        if len(full) <= available:
            body_lines.append(full)
            continue

        # Try shortened form that keeps the location marker
        if loc:
            min_form = f"{loc} \u2192 ..."
            if len(min_form) <= available:
                overhead = len(loc) + len(" \u2192 ") + 3
                keep = available - overhead
                if keep > 0:
                    body_lines.append(f"{loc} \u2192 {text[:keep]}...")
                else:
                    body_lines.append(min_form)
                continue

        skipped = len(prepared) - i
        break

    # ── build final output (accurate header count) ──
    header = make_header(len(body_lines))
    extra = ""
    if skipped > 0:
        extra = f"char limit {max_chars} reached ({skipped} more match(es) omitted)"

    out = header
    if body_lines:
        out += "\n" + "\n".join(body_lines)
    out += build_status(extra)

    return hard_clip(out)

# ═══════════════════════════════════════════════════════════
# B. Surgical code modification
# ═══════════════════════════════════════════════════════════

# T04 tuning constants for apply_search_replace
_EDIT_MAX_CHARS = 4000        # hard response cap
_EDIT_MAX_DIFF_LINES = 20     # combined -/+ lines kept in the diff


def _detect_newline(text: str) -> str:
    """Dominant newline of a text: CRLF when it accounts for at least
    half of all newline characters, else LF."""
    crlf = text.count("\r\n")
    if crlf and crlf >= text.count("\n") / 2:
        return "\r\n"
    return "\n"


def _normalize_newlines(text: str, newline: str) -> str:
    """Rewrite every line break to `newline`."""
    if newline == "\n":
        return text.replace("\r\n", "\n")
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _trim_middle(lines, budget, marker="    ... [truncated] ..."):
    """Join `lines` to at most `budget` chars, dropping from the middle
    and leaving `marker` in its place."""
    text = "\n".join(lines)
    if len(text) <= budget:
        return text
    room = budget - len(marker) - 1
    if room <= 0:
        return marker[:budget]
    half = room // 2
    head, used = [], 0
    for ln in lines:
        if used + len(ln) + 1 > half:
            break
        head.append(ln)
        used += len(ln) + 1
    tail, used2 = [], 0
    for ln in reversed(lines):
        if used2 + len(ln) + 1 > room - used:
            break
        tail.append(ln)
        used2 += len(ln) + 1
    tail.reverse()
    out = "\n".join(head + [marker] + tail)
    return out[:budget] if len(out) > budget else out


@register("apply_search_replace")
async def apply_search_replace(file_path: str, search_block: str, replace_block: str,
                               expected_sha256: str = None) -> str:
    """Replace the exact `search_block` with `replace_block` once.

    Matching is a strict, case-sensitive substring match against the
    file text (newline-normalized). Zero matches or more than one
    match are errors - callers must add context to disambiguate.

    expected_sha256 (optional) is a stale-content guard: when given,
    the file's current sha256 must match or the edit is refused and
    nothing is written.

    The file's dominant newline convention and POSIX mode are
    preserved, and the write is atomic (temp file in the same
    directory + os.replace)."""
    path = os.path.expanduser(file_path)
    if not os.path.exists(path):
        return f"ERROR: File not found: {path}"
    if os.path.isdir(path):
        return f"ERROR: {path} is a directory, not a file"
    if not search_block.strip():
        return "ERROR: Search block cannot be empty or whitespace only."

    basename = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            raw_before = f.read()
    except OSError as exc:
        return f"ERROR: read failed - {basename}: {exc}"

    before_sha = hashlib.sha256(raw_before).hexdigest()

    # Optional stale-content guard. Refuse rather than write anyway.
    if expected_sha256 is not None and before_sha != expected_sha256:
        return (
            f"ERROR: stale content - expected {expected_sha256[:12]}, "
            f"found {before_sha[:12]}\n"
            f"current_sha256: {before_sha}"
        )

    text = raw_before.decode("utf-8", errors="surrogateescape")
    newline = _detect_newline(text)

    # Match on LF-normalized text so callers pass conventional \n
    # blocks regardless of the file's own convention.
    norm_text = text.replace("\r\n", "\n")
    norm_search = search_block.replace("\r\n", "\n")
    norm_replace = replace_block.replace("\r\n", "\n")

    count = norm_text.count(norm_search)
    if count == 0:
        return f"ERROR: TARGET SEARCH BLOCK NOT FOUND EXACTLY in {path}"
    if count > 1:
        return (f"ERROR: Search block matches {count} locations. "
                f"Please provide more context to make it unique.")

    pos = norm_text.find(norm_search)
    start_line = norm_text[:pos].count("\n") + 1
    end_line = start_line + norm_search.count("\n")

    new_norm = norm_text.replace(norm_search, norm_replace, 1)
    new_text = _normalize_newlines(new_norm, newline)
    raw_after = new_text.encode("utf-8", errors="surrogateescape")
    after_sha = hashlib.sha256(raw_after).hexdigest()

    # No-op guard: identical bytes means nothing to write.
    if raw_after == raw_before:
        return (
            "No change: replacement is identical to original.\n"
            f"before_sha256: {before_sha}\n"
            f"after_sha256:  {after_sha}"
        )

    # Compact unified-ish diff: the replaced lines vs the new lines.
    removed = [f"    - {ln}" for ln in norm_search.split("\n")]
    added = [f"    + {ln}" for ln in norm_replace.split("\n")]
    diff_lines = removed + added
    if len(diff_lines) > _EDIT_MAX_DIFF_LINES:
        # Keep at most _EDIT_MAX_DIFF_LINES combined -/+ lines.
        keep_head = (_EDIT_MAX_DIFF_LINES - 1) // 2
        keep_tail = _EDIT_MAX_DIFF_LINES - 1 - keep_head
        diff_lines = (diff_lines[:keep_head]
                      + ["    ... [truncated] ..."]
                      + diff_lines[-keep_tail:])

    # Build the response, reserving room for everything but the diff so
    # before/after sha256 and changed_range always survive intact.
    full_rewrite = estimate_tokens(new_text)
    patch = estimate_tokens(search_block + replace_block)
    saved = max(0, full_rewrite - patch)
    metric = (f"[TOKEN METRIC] tool=apply_search_replace saved={saved:,} "
              f"baseline={full_rewrite:,} actual={patch:,} type=payload_reduction")

    head = (
        "Surgical replacement applied successfully.\n"
        f"before_sha256: {before_sha}\n"
        f"after_sha256:  {after_sha}\n"
        f"changed_range: lines {start_line}-{end_line}\n"
        "diff:"
    )
    tail = "\n\n" + metric
    diff_budget = _EDIT_MAX_CHARS - len(head) - len(tail)
    diff_text = _trim_middle(diff_lines, diff_budget)
    response = head + "\n" + diff_text + tail

    # Last-resort guard; the budget above should already fit.
    if len(response) > _EDIT_MAX_CHARS:
        response = response[:_EDIT_MAX_CHARS]

    # Atomic write: temp file in the same directory, then os.replace.
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    is_win = sys.platform.startswith("win")
    mode_before = None
    if not is_win:
        try:
            mode_before = os.stat(path).st_mode
        except OSError:
            mode_before = None

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".asr-", suffix=".tmp")
        with os.fdopen(fd, "wb") as tf:
            tf.write(raw_after)
            tf.flush()
            os.fsync(tf.fileno())
        if mode_before is not None:
            os.chmod(tmp_path, mode_before)
        os.replace(tmp_path, path)
        tmp_path = None
    except Exception as exc:
        if tmp_path is not None and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return f"ERROR: write failed - {basename}: {exc}"

    return response

# ═══════════════════════════════════════════════════════════
# C. Safety and version control
# ═══════════════════════════════════════════════════════════

def _path_matches(cand_path, target_path):
    """True when a diagnostic's path plausibly refers to our target:
    equal after normalization, or one is a suffix of the other at a
    path boundary (so 'other/dir/main.py' does not match 'my/dir/main.py')."""
    c = cand_path.replace("\\", "/").rstrip()
    t = target_path.replace("\\", "/").rstrip()
    if c == t:
        return True
    # One must be a suffix of the other, anchored at a path boundary
    if t.endswith("/" + c) or c.endswith("/" + t):
        return True
    return False


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

    if not os.path.isfile(path):
        return f"FAIL: not a file — {path}"

    cwd = os.path.dirname(os.path.abspath(path))
    cmd = ["python3", "-m", "py_compile", path]
    basename = os.path.basename(path)

    # Fingerprint the source before compiling so we can detect an edit
    # racing between py_compile and the context read below.
    try:
        with open(path, "rb") as _sf:
            sha_before = hashlib.sha256(_sf.read()).hexdigest()
    except OSError:
        sha_before = None

    try:
        res = subprocess.run(
            cmd,
            capture_output=True, text=True, shell=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired as exc:
        partial = ""
        err = getattr(exc, "stderr", None)
        if err:
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            partial = "\n  partial stderr:\n  " + err.strip()[:800]
        return f"FAIL: py_compile timed out after 10s — {basename}{partial}"
    except FileNotFoundError:
        return "FAIL: python3 not available on PATH"
    except PermissionError:
        return f"FAIL: py_compile not permitted — {basename}"
    except OSError as e:
        return f"FAIL: py_compile launch failed — {basename}: {e}"

    # Build execution header only for non-OK results
    if res.returncode == 0:
        return f"OK: Python syntax check passed — {os.path.basename(path)}"

    # Build execution header for FAIL responses
    header = (
        f"lint_file: python3 -m py_compile\n"
        f"  file dir: {cwd}\n"
        f"  exit: {res.returncode}\n"
    )

    # Parse stderr for location info and compiler message
    stderr = (res.stderr or "").strip()
    target_base = os.path.basename(path)
    location_line = None

    # Preserve the compiler's own error type + message (e.g.
    # "SyntaxError: invalid syntax") for the FAIL section.
    compile_msg = None
    for line in stderr.splitlines():
        s = line.strip()
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b', s):
            compile_msg = s
            break

    # Pattern 1: file:line: message (requires .py path)
    # Pattern 2: py_compile "File ".../x.py", line N
    # Any diagnostic that names another file is NOT attributed to the
    # target, and there is deliberately no generic ", line N" fallback:
    # plain prose ("config error, line 1") is not a source location.
    foreign_paths = []
    for line in stderr.splitlines():
        m = re.search(r'^(.+?\.py):(\d+):', line)
        if m:
            if _path_matches(m.group(1), path):
                location_line = int(m.group(2))
                break
            foreign_paths.append(m.group(1))
            continue
        m = re.search(r'^File\s+"([^"]+\.py)",\s*line\s+(\d+)', line)
        if m:
            if _path_matches(m.group(1), path):
                location_line = int(m.group(2))
                break
            foreign_paths.append(m.group(1))
            continue

    # Build diagnostic output
    read_error = None
    lines = []
    source_changed = False
    if location_line:
        # Read source file and extract context around error line
        try:
            with open(path, 'r') as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError) as e:
            read_error = str(e)

        # Did the source move between compile and this read?
        if sha_before is not None and lines:
            try:
                with open(path, "rb") as _sf:
                    sha_after = hashlib.sha256(_sf.read()).hexdigest()
                source_changed = sha_after != sha_before
            except OSError:
                source_changed = True

        # Validate the extracted line number against the real file
        if lines and not (1 <= location_line <= len(lines)):
            location_line = None

    if location_line and lines:
        # Get up to 5 lines total centered on error line (1-indexed)
        err_idx = location_line - 1
        start = max(0, err_idx - 2)
        end = min(len(lines), err_idx + 3)
        context_lines = lines[start:end]

        diagnostic = f"  {target_base}:{location_line}\n"
        if compile_msg:
            diagnostic += f"  {compile_msg}\n"
        if source_changed:
            diagnostic += ("  # note: source changed during compilation; "
                           "excerpt may not match diagnostic\n")
        for i, src_line in enumerate(context_lines):
            actual_line = start + i + 1
            prefix = "→ " if actual_line == location_line else "  "
            diagnostic += f"    {prefix}{actual_line}: {src_line.rstrip()}\n"
    elif location_line and read_error:
        diagnostic = f"  {target_base}:{location_line}\n    (source unavailable: {read_error})\n"
    else:
        # No location parsed - return raw stderr (capped)
        raw = stderr or "(no stderr)"
        # A rejected foreign diagnostic must not look like an assertion
        # about our file: drop its line number and label it as foreign.
        for fp in sorted(set(foreign_paths), key=len, reverse=True):
            raw = re.sub(re.escape(fp) + r':\d+:',
                         fp + ' (other file):', raw)
        if len(raw) > 4000:
            suffix = "\n... [truncated] ...\n"
            budget = 4000 - len(suffix)
            half = budget // 2
            raw = raw[:half] + suffix + raw[-half:]
        diagnostic = f"  {raw}\n  (no location parsed)\n"
        # Unknown nonzero returncode with no parseable location: do not
        # assert a source defect we cannot confirm.
        result = f"{header}FAIL: Python syntax check failed — {target_base}\n{diagnostic}"

        # Cap total output at 4000 chars
        if len(result) > 4000:
            suffix = "\n... [truncated] ...\n"
            budget = 4000 - len(suffix)
            half = budget // 2
            result = result[:half] + suffix + result[-half:]

        return result

    result = f"{header}FAIL: Python syntax error — {target_base}\n{diagnostic}"

    # Cap total output at 4000 chars
    if len(result) > 4000:
        suffix = "\n... [truncated] ...\n"
        budget = 4000 - len(suffix)
        half = budget // 2
        result = result[:half] + suffix + result[-half:]

    return result

def _run_git(args, cwd, timeout=10):
    """Run a git subcommand safely.

    Returns (returncode, stdout, stderr). On missing git binary or
    timeout, returncode is -1 and stderr carries a short reason.
    Never uses shell=True. Never modifies git config.
    """
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
        )
        return res.returncode, res.stdout, res.stderr
    except FileNotFoundError:
        if not os.path.isdir(cwd):
            return -1, "", f"working directory does not exist: {cwd}"
        return -1, "", "git not available on PATH"
    except subprocess.TimeoutExpired:
        return -1, "", f"git {' '.join(args[:2])} timed out after {timeout}s"


def _git_detail(err: str) -> str:
    """First line of git stderr, or 'unknown' when stderr is empty."""
    err = (err or "").strip()
    return err.splitlines()[0] if err else "unknown"


def _truncate_middle(text: str, limit: int) -> str:
    """Elide the middle of *text* so the result fits in *limit* chars.
    The head and the tail of the text both survive."""
    if len(text) <= limit:
        return text
    suffix = "\n... [truncated] ...\n"
    if limit <= len(suffix):
        return text[:limit]
    budget = limit - len(suffix)
    half = budget // 2
    return text[:half] + suffix + text[-half:]


def _bound_4000(text: str) -> str:
    """Hard cap for git tool responses (T06-6 / T07-6)."""
    return _truncate_middle(text, 4000)


@register("git_checkpoint")
async def git_checkpoint(file_path: str, change_summary: str) -> str:
    """Commit ONLY file_path under a [harness]-prefixed message.

    Refuses when unrelated files are staged, so the user's own work is
    never swept into our commit. Never modifies git config.
    """
    if len(change_summary) > 200:
        change_summary = change_summary[:197] + "..."

    # ── T06-2: canonical repository root discovery ──
    start_dir = os.path.dirname(os.path.realpath(file_path))
    rc, out, err = _run_git(["rev-parse", "--show-toplevel"], cwd=start_dir)
    if rc != 0:
        return _bound_4000(
            "ERROR: not a git repository — "
            f"{os.path.dirname(os.path.abspath(file_path))}"
        )
    repo_dir = out.strip()

    # The target has to live inside that repository.
    real_target = os.path.realpath(file_path)
    real_root = os.path.realpath(repo_dir)
    if not real_target.startswith(real_root + os.sep):
        return _bound_4000(
            "ERROR: target file is outside the repository root — "
            f"{os.path.basename(file_path)}"
        )

    # ── T06-3: robust staged-work detection (NUL-delimited) ──
    rc, out, err = _run_git(["diff", "--cached", "--name-only", "-z"], repo_dir)
    if rc != 0:
        return _bound_4000(f"ERROR: git diff failed — {_git_detail(err)}")
    staged = [p for p in out.split("\0") if p]
    rel_target = os.path.relpath(real_target, real_root)
    if staged and staged != [rel_target]:
        return _bound_4000(
            "ERROR: Unrelated staged files detected. "
            "Please commit or unstage them before using git_checkpoint.\n"
            f"Staged files: {', '.join(sorted(staged))}"
        )

    # ── T06-4: every git invocation is return-code checked ──
    rc, out, err = _run_git(["add", rel_target], repo_dir)
    if rc != 0:
        return _bound_4000(f"ERROR: git add failed — {_git_detail(err)}")

    # '-- <path>' commits ONLY this file, ignoring any other staged work.
    rc, out, err = _run_git(
        ["commit", "-m", f"[harness] {change_summary}", "--", rel_target],
        repo_dir,
    )
    if rc != 0:
        if "Please tell me who you are" in (err + out):
            return _bound_4000(
                "ERROR: git user identity is not configured — set "
                "user.email and user.name before committing"
            )
        return _bound_4000(
            f"ERROR: git commit failed — {err.strip() or 'unknown'}"
        )

    # ── T06-5: success response ──
    rc, commit_id, err = _run_git(["rev-parse", "HEAD"], repo_dir)
    if rc != 0:
        return _bound_4000(f"ERROR: git rev-parse failed — {_git_detail(err)}")
    commit_id = commit_id.strip()

    rc, out, err = _run_git(["show", "--name-only", "--format=", "HEAD"], repo_dir)
    if rc != 0:
        return _bound_4000(f"ERROR: git show failed — {_git_detail(err)}")
    changed_paths = sorted(p for p in out.splitlines() if p.strip())

    # ── T06-6: bounded response, header and commit_id always intact ──
    head = (
        f"Committed: {change_summary}\n"
        f"commit_id: {commit_id}\n"
        "changed_paths:"
    )
    body = "\n".join(f"  {p}" for p in changed_paths)
    resp = f"{head}\n{body}" if body else head
    if len(resp) > 4000:
        room = 4000 - len(head) - 1
        if body and room > len("\n... [truncated] ...\n"):
            resp = f"{head}\n{_truncate_middle(body, room)}"
        else:
            resp = _bound_4000(resp)
    return resp

@register("rollback_show")
async def rollback_show(repo_path: str) -> str:
    """READ-ONLY. Shows what would be lost if the user resets the repo.
    Does NOT execute any rollback. Presents copyable commands."""
    try:
        # ── T07-2: canonical repository root discovery ──
        real_root = os.path.realpath(os.path.abspath(repo_path))
        rc, out, err = _run_git(["rev-parse", "--show-toplevel"], cwd=real_root)
        if rc != 0:
            return f"ERROR: not a git repository — {real_root}"
        repo_dir = out.strip()

        # ── T07-3: read-only status gathering ──
        # These invocations never trigger hooks, external diff drivers
        # or textconv filters, and never write to the repo.
        rc_st, out_st, err_st = _run_git(
            ["diff", "--cached", "--stat", "--no-ext-diff", "--no-textconv"],
            repo_dir,
        )
        rc_un, out_un, err_un = _run_git(
            ["-c", "diff.external=", "diff", "--no-ext-diff", "--no-textconv",
             "--stat"],
            repo_dir,
        )
        rc_sts, out_sts, err_sts = _run_git(
            ["--no-optional-locks", "status", "--porcelain"], repo_dir
        )
        rc_sh, out_sh, err_sh = _run_git(["stash", "list"], repo_dir)

        def section(cmd_rc, cmd_out, cmd_err):
            """Show the git output, or disclose the failure — never a
            pretend '(none)' when the inspection itself broke."""
            if cmd_rc != 0:
                return f"(inspection failed: {_git_detail(cmd_err)})"
            return cmd_out.strip() or "(none)"

        # ── T07-4: accurate categories and counts ──
        r = ["═══ ROLLBACK IMPACT REPORT ═══", ""]
        r.append("Staged changes (in index, would be lost by reset):")
        r.append(section(rc_st, out_st, err_st))
        r.append("")
        r.append("Unstaged changes (working tree, would be lost by checkout/reset):")
        r.append(section(rc_un, out_un, err_un))
        r.append("")
        r.append("Untracked files (would be DELETED by git clean):")
        if rc_sts != 0:
            r.append(f"(inspection failed: {_git_detail(err_sts)})")
        else:
            untracked = [
                line[3:] if line.startswith("?? ") else line[2:]
                for line in out_sts.splitlines()
                if line.startswith("??")
            ]
            if untracked:
                r.extend(f"  {u}" for u in untracked)
            else:
                r.append("(none)")
        r.append("")
        r.append("Existing stashes (preserved by reset):")
        r.append(section(rc_sh, out_sh, err_sh))

        # T07-5 is structural: only the read-only calls above were made.
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
        # ── T07-6: bounded response; header and manual commands survive ──
        return _truncate_middle("\n".join(r), 4000)
    except Exception as exc:
        return f"ERROR: rollback inspection failed — {exc}"


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
