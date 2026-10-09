"""Adapter over the vendored Aider repo-map code (Day 1, Python only).

Public entry point
------------------
``build_ranked_python_map(repo_path, task_hint="", max_tokens=1500)``

Vendored functions this module calls (all from ``._vendor.aider.repomap``,
which is Aider's ``aider/repomap.py`` at commit ``5dc9490``, see
``_vendor/aider/PATCHES.md``):

* ``repomap.RepoMap(root=..., io=repomap.InputOutput(),
  main_model=repomap.Model())`` — constructor; its tag cache is the
  in-memory ``_MemCache``, so no cache directory is written to disk.
* ``RepoMap.get_tags_raw(fname, rel_fname)`` — called once per walked
  ``.py`` file to collect the raw tag inventory.
* ``RepoMap.get_ranked_tags(chat_fnames, other_fnames,
  mentioned_fnames, mentioned_idents, progress=None)`` — called once with
  the walked files to obtain the PageRank-based ordering.  Internally it
  re-uses the vendored ``RepoMap.get_tags`` (which calls ``get_tags_raw``
  again through the in-memory cache), ``get_rel_fname`` and
  ``get_scm_fname``.
* ``repomap.InputOutput`` / ``repomap.Model`` — the local stubs defined
  in the vendored module (no aider.io / aider.models dependency).
* ``repomap.Tag`` — the namedtuple ``(rel_fname, fname, line, name, kind)``
  used to render each symbol.

Behaviour guarantees
--------------------
* Only files whose extension is ``.py`` are walked.
* Nothing is ever written to stdout: this runs inside an MCP server whose
  stdout is the protocol channel (no ``print``, no console).
* The returned string is capped at ``max_tokens * 4`` characters.
* Any exception is swallowed and reported as
  ``ERROR: ranked map unavailable — <exc>``.
"""

import os
import re

from ._vendor.aider import repomap

# Same exclusions get_repo_skeleton uses when walking for the outline.
_SKIP_DIRS = {
    ".git",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "site-packages",
}

_MAX_FILES = 500
_TRUNCATION_MARKER = "\n... [truncated] ...\n"


def _python_files(root, limit=_MAX_FILES):
    """Return an absolute, sorted list of ``.py`` files under *root*."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fname in sorted(filenames):
            if not fname.endswith(".py"):
                continue
            found.append(os.path.join(dirpath, fname))
            if len(found) >= limit:
                return found
    return found


def _hint_idents(task_hint):
    """Turn a free-text hint into identifier candidates.

    Both the original and the lower-cased forms are returned, because the
    vendored ranker matches ``mentioned_idents`` exactly (against path
    components and against definition idents).
    """
    if not task_hint:
        return set()
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", task_hint)
    idents = set()
    for word in words:
        idents.add(word)
        idents.add(word.lower())
    return {i for i in idents if i}


def _entry_key(entry):
    if len(entry) >= 5:
        return (entry[0], entry[2], entry[3])
    return (entry[0],)


def _render(entry):
    """One compact line per ranked entry.

    tree-sitter rows are 0-based, so they are shown 1-based to match the
    outline mode.  A line of ``-1`` is Aider's "unknown line" marker for
    pygments-backfilled references, which are rendered without a line.
    """
    if len(entry) >= 5:
        rel_fname, _fname, line, name, kind = entry
        if line is None or line < 0:
            return f"{rel_fname} {kind} {name}"
        return f"{rel_fname}:L{line + 1} {kind} {name}"
    return str(entry[0])


def _bias_matches(entry, words):
    if not words:
        return False
    haystacks = []
    if len(entry) >= 5:
        haystacks.append(str(entry[3]).lower())  # symbol name
    haystacks.append(str(entry[0]).lower())      # relative path
    joined = " ".join(haystacks)
    return any(word.lower() in joined for word in words)


def build_ranked_python_map(repo_path, task_hint="", max_tokens=1500):
    """Return a bounded ranked map of Python symbols in repo_path.
    task_hint biases ranking when provided. Returns a string."""
    try:
        root = os.path.realpath(os.path.abspath(repo_path))
        if not os.path.isdir(root):
            return f"ERROR: ranked map unavailable — not a directory: {root}"

        py_files = _python_files(root)
        if not py_files:
            return f"# ranked python map — 0 files, 0 symbols\n# {root}"

        repo = repomap.RepoMap(
            root=root,
            io=repomap.InputOutput(),
            main_model=repomap.Model(),
        )

        # 1. Raw inventory — one get_tags_raw call per Python file.
        raw_entries = []
        for fname in py_files:
            rel = os.path.relpath(fname, root)
            tags = repo.get_tags_raw(fname, rel)
            if tags:
                raw_entries.extend(tags)

        # 2. Ranking — one get_ranked_tags call over the Python files.
        ident_hint = _hint_idents(task_hint)
        ranked_entries = repo.get_ranked_tags(
            [],
            py_files,
            set(),
            ident_hint,
        )

        # 3. Merge: keep the ranker's order, backfill anything it dropped.
        seen = set()
        merged = []
        for entry in list(ranked_entries) + list(raw_entries):
            key = _entry_key(entry)
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)

        # 4. Bias: task-hint hits float to the top (stable within group).
        hint_words = sorted(ident_hint)
        if hint_words:
            hits = [e for e in merged if _bias_matches(e, hint_words)]
            misses = [e for e in merged if not _bias_matches(e, hint_words)]
            merged = hits + misses

        # 5. Render, bounded at max_tokens * 4 characters.
        cap = max(64, int(max_tokens) * 4)
        header = f"# ranked python map — {len(py_files)} files, {len(merged)} symbols"
        lines = [header]
        if task_hint:
            lines.append(f"# task: {str(task_hint).strip()}")

        budget = max(1, cap - len(_TRUNCATION_MARKER))
        used = 0
        truncated = False
        for line in lines:
            used += len(line) + 1
        if used > budget:
            # Degenerate: even the header does not fit.
            return header[: max(1, cap - len(_TRUNCATION_MARKER))] + _TRUNCATION_MARKER

        kept = list(lines)
        for entry in merged:
            line = _render(entry)
            need = len(line) + 1
            if used + need > budget:
                truncated = True
                break
            kept.append(line)
            used += need

        text = "\n".join(kept)
        if truncated:
            text += _TRUNCATION_MARKER
        if len(text) > cap:
            text = text[: max(1, cap - len(_TRUNCATION_MARKER))] + _TRUNCATION_MARKER
        return text
    except Exception as exc:
        return f"ERROR: ranked map unavailable — {exc}"
