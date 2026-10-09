"""Tool output bounding — CORE home for truncation and rendering.

Consolidates the semantics that live inline in ``templates/server.py``
(``_truncate_middle`` / ``_bound_4000``) into one importable module, so
every tool response is bounded the same way:

* truncation is line-aware — whole lines are kept or dropped, so an
  entry is never sliced in half, and the marker lands on its own line;
* the head *and* the tail of the input survive;
* rendering is bounded both by item count and by character budget, and
  caller-supplied tail notes are never dropped.

All functions are pure: no I/O, no ``print()`` — the caller decides
what to do with the returned text.
"""
from __future__ import annotations

import os
from typing import NamedTuple

#: Hard character cap for a rendered tool response.
DEFAULT_MAX_CHARS = 4000
#: Maximum number of lines kept from a stdout head in a failure summary.
DEFAULT_MAX_LINES = 200
#: Maximum number of blocks a renderer will display.
DEFAULT_MAX_ITEMS = 40

#: Lines kept from the start of stderr when it must be condensed.
_STDERR_HEAD_LINES = 10
#: Marker inserted between the kept head and tail of a long stderr.
_STDERR_MARKER = "\n... [stderr middle omitted] ...\n"
#: Separator that introduces the stdout head of a failure summary.
_STDOUT_LABEL = "\n--- stdout ---\n"


class RenderResult(NamedTuple):
    """Outcome of a bounded render: what was shown and what was dropped."""

    text: str
    displayed: int
    total: int
    omitted: int
    truncated: bool


def truncate_middle(text: str, limit: int,
                    count_label: str = None) -> str:
    """Elide the middle of *text* so the result fits in *limit* chars.

    Truncation is line-aware: whole lines are kept or dropped, so an
    entry is never sliced in half. The head and the tail of the text
    both survive and the marker always lands on its own line.

    With ``count_label=None`` the marker is exactly
    ``... [truncated] ...``. When a label is given (e.g. ``"entries"``)
    the marker also reports how many lines the head/tail selection
    dropped: ``... [truncated: N entries omitted] ...``. The budget is
    reserved with the worst-case (largest) count first, so the real
    marker can only be shorter than the space reserved for it.
    """
    if len(text) <= limit:
        return text
    lines = text.splitlines(keepends=True)
    if count_label is None:
        marker = "\n... [truncated] ...\n"
    else:
        # Placeholder count: at most every line can be omitted.
        marker = (f"\n... [truncated: {len(lines)} {count_label} "
                  f"omitted] ...\n")
    if limit <= len(marker):
        return text[:limit]
    budget = limit - len(marker)
    head_lines = []
    head_used = 0
    for ln in lines:
        if head_used + len(ln) > budget // 2:
            break
        head_lines.append(ln)
        head_used += len(ln)
    tail_lines = []
    tail_used = 0
    for ln in reversed(lines):
        if tail_used + len(ln) > budget - head_used:
            break
        tail_lines.insert(0, ln)
        tail_used += len(ln)
    if count_label is not None:
        omitted = len(lines) - len(head_lines) - len(tail_lines)
        marker = (f"\n... [truncated: {omitted} {count_label} "
                  f"omitted] ...\n")
    result = "".join(head_lines) + marker + "".join(tail_lines)
    if len(result) > limit:
        result = result[:limit]
    return result


def render_blocks(header: str, blocks, *,
                  max_chars: int = DEFAULT_MAX_CHARS,
                  max_items: int = DEFAULT_MAX_ITEMS,
                  item_label: str = "items",
                  tail_notes=()) -> RenderResult:
    """Render *blocks* under an item budget and a character budget.

    Layout is ``header + "\\n\\n" + "\\n\\n".join(displayed blocks)``,
    followed by an omission marker (when blocks were dropped) and then
    every item of *tail_notes* verbatim, one per line. Tail notes take
    priority over blocks: notes are never dropped, blocks are.

    Blocks are dropped whole — a block is never cut in half while the
    renderer shrinks. Only if ``header + notes + a single block`` still
    overflows *max_chars* (a degenerate budget) does the final
    ``truncate_middle`` guard run, which keeps the head and the tail
    (so the tail notes survive) and elides the middle.
    """
    items = list(blocks)
    total = len(items)
    shown = items[:max(0, max_items)]
    notes = tuple(tail_notes)
    notes_part = "".join("\n" + str(note) for note in notes)

    def assemble(displayed, omitted):
        body = (header + "\n\n" if header else "") + "\n\n".join(displayed)
        if omitted > 0:
            body += f"\n... [truncated: {omitted} {item_label} omitted] ..."
        return body + notes_part

    text = assemble(shown, total - len(shown))
    while len(text) > max_chars and shown:
        shown.pop()
        text = assemble(shown, total - len(shown))
    if len(text) > max_chars:
        text = truncate_middle(text, max_chars)

    displayed = len(shown)
    omitted = total - displayed
    return RenderResult(text, displayed, total, omitted, omitted > 0)


def render_diagnostics(diags, *,
                       max_chars: int = DEFAULT_MAX_CHARS,
                       max_items: int = DEFAULT_MAX_ITEMS) -> RenderResult:
    """Render ``(path, line, col, severity, message)`` diagnostics.

    Each entry renders as ``{path}:{line}:{col}: {severity}: {message}``.
    A missing position (``None`` or ``0``) drops that field while the
    path always survives, e.g. ``/a/b.py: warning: unchecked``. Long
    messages are never cut while the renderer shrinks: whole entries
    are dropped instead, bounded by *max_chars* / *max_items*.
    """
    blocks = [_format_diagnostic(diag) for diag in diags]
    return render_blocks("", blocks,
                         max_chars=max_chars,
                         max_items=max_items,
                         item_label="diagnostics")


def summarize_failure(exit_code: int, stdout: str, stderr: str, *,
                      max_chars: int = 2000,
                      tail_lines: int = 20) -> str:
    """Summarize a failed command, always starting with its exit code.

    stderr is included verbatim while it fits; otherwise its first ten
    lines and its last *tail_lines* lines are kept with
    ``... [stderr middle omitted] ...`` between them (when a real
    middle exists). A bounded stdout head follows when stdout is
    non-empty, and the whole result is capped by *max_chars*.
    """
    text = f"exit_code: {exit_code}\n"

    err = stderr or ""
    if err:
        if len(text) + len(err) <= max_chars:
            text += err if err.endswith("\n") else err + "\n"
        else:
            text += _condense_stderr(err, tail_lines)

    out = stdout or ""
    if out:
        room = max_chars - len(text) - len(_STDOUT_LABEL)
        if room > 0:
            text += _STDOUT_LABEL + truncate_middle(
                _stdout_head(out), room, count_label="stdout lines")

    if len(text) > max_chars:
        text = truncate_middle(text, max_chars)
    return text


def _format_diagnostic(diag) -> str:
    """Render one ``(path, line, col, severity, message)`` tuple."""
    path, line, col, severity, message = diag
    location = str(path)
    if line:  # None or 0 means "no position" — drop it, keep the path
        if col:
            location = f"{location}:{line}:{col}"
        else:
            location = f"{location}:{line}"
    return f"{location}: {severity}: {message}"


def _condense_stderr(err: str, tail_lines: int) -> str:
    """Keep the first 10 and last *tail_lines* lines of *err*.

    The middle marker is only inserted when lines are genuinely
    dropped, so a short-but-wide stderr is never duplicated.
    """
    lines = err.splitlines()
    head = lines[:_STDERR_HEAD_LINES]
    tail = lines[-tail_lines:] if tail_lines and tail_lines > 0 else []
    if len(lines) <= len(head) + len(tail):
        body = "\n".join(lines)
    else:
        body = "\n".join(head) + _STDERR_MARKER + "\n".join(tail)
    return body + "\n"


def _stdout_head(out: str) -> str:
    """First ``DEFAULT_MAX_LINES`` lines of *out*, newline-terminated."""
    lines = out.splitlines()
    if len(lines) <= DEFAULT_MAX_LINES:
        return out
    return "\n".join(lines[:DEFAULT_MAX_LINES]) + "\n"
