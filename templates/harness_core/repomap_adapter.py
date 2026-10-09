"""Adapter over the vendored Aider repo-map code (Day 1 Python-only; Day 2
adds JavaScript, TypeScript and TSX).

Public entry points
-------------------
``build_ranked_map(repo_path, task_hint="", max_tokens=1500)``
    Returns the ranked map as a string.

``build_ranked_map_with_stats(repo_path, task_hint="", max_tokens=1500)``
    Same map, but returns ``(text, raw_bytes)`` where ``raw_bytes`` is the
    sum of ``os.path.getsize()`` for every walked source file.  Callers
    should use this to build an **honest** token baseline: the ranked text
    is a truncation of the raw sources, so the baseline must be derived
    from the bytes actually read, never from the output length.

``build_ranked_python_map`` is a backward-compatible alias of
``build_ranked_map`` (kept because Day 1 callers/tests may still use that
name).

Vendored functions this module calls (all from ``._vendor.aider.repomap``,
which is Aider's ``aider/repomap.py`` at commit ``5dc9490``, see
``_vendor/aider/PATCHES.md``):

* ``repomap.RepoMap(root=..., io=repomap.InputOutput(),
  main_model=repomap.Model())`` — constructor; its tag cache is the
  in-memory ``_MemCache``, so no cache directory is written to disk.
* ``RepoMap.get_tags_raw(fname, rel_fname)`` — called once per walked
  source file to collect the raw tag inventory.
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
* ``grep_ast.filename_to_lang`` (pulled in transitively by the vendored
  module) is what maps a file extension to a tree-sitter language.  Note
  it reports ``.tsx`` as ``typescript``, so the same grammar and
  ``typescript-tags.scm`` query are used for both ``.ts`` and ``.tsx``.

Behaviour guarantees
--------------------
* Only files whose extension is in ``SUPPORTED_EXTENSIONS`` are walked
  (``.py``, ``.js``, ``jsx``, ``.ts``, ``.tsx``).
* Nothing is ever written to stdout: this runs inside an MCP server whose
  stdout is the protocol channel (no ``print``, no console).
* The returned string is capped at ``max_tokens * 4`` characters.
* Any exception is swallowed and reported as
  ``ERROR: ranked map unavailable — <exc>``.
* ``build_ranked_map_with_stats`` keeps a small, bounded **in-process**
  cache (no on-disk state) keyed by the walked files' freshness
  signature.  An unchanged tree is served from the cache; an added,
  edited, deleted or touched file changes the signature and the map is
  recomputed silently.  At most ``_CACHE_MAX`` roots are retained
  (LRU eviction).
* The backend dependencies are checked by ``_preflight_backend()``
  before the walk, so a half-installed backend reports
  ``ERROR: ranked map unavailable — backend not installed: <module>``
  instead of a generic import failure.  The vendored ``repomap`` module
  is imported lazily (inside the call), so this module itself always
  imports cleanly.
"""

import os
import re
import threading

# Extension → language label, as reported in the ``# languages:`` header
# line.  Only languages that actually contributed at least one file are
# listed.  ``.jsx`` maps to the same ``javascript`` grammar/tree-sitter
# query as ``.js``.
SUPPORTED_EXTENSIONS = {
    ".py":   "python",
    ".js":   "javascript",
    ".jsx":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "tsx",
}

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

# In-process freshness cache.  Bounded to _CACHE_MAX roots and pruned
# least-recently-used first; nothing is ever written to disk and no cache
# activity is ever printed (stdout is the MCP protocol channel).
_CACHE = {}                 # root -> {"sig": <tuple>, "text": str, "raw_bytes": int}
_CACHE_ORDER = []           # list of roots, most-recently-used first
_CACHE_MAX = 5              # bound the number of cached roots
_CACHE_STATS = {"hits": 0, "misses": 0}
_CACHE_LOCK = threading.Lock()


def _source_files(root, limit=_MAX_FILES):
    """Return an absolute list of source files under *root* whose
    extension is in ``SUPPORTED_EXTENSIONS``.  Sub-directory and file
    names are sorted, so the walk order is deterministic.  Skips the same
    common vendor/build directories as Day 1 (``_SKIP_DIRS``)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fname in sorted(filenames):
            if os.path.splitext(fname)[1] not in SUPPORTED_EXTENSIONS:
                continue
            found.append(os.path.join(dirpath, fname))
            if len(found) >= limit:
                return found
    return found


def _signature(files, root):
    """Return a hashable signature of the walked source files:
    sorted (relative_path, mtime_ns, size) tuples. mtime_ns alone is
    not enough — a same-size, same-mtime write should still be caught
    by size, and a same-size same-second edit is caught by ns
    resolution on all supported filesystems.

    ``root`` is only used to render the relative paths (the cache key is
    the root itself, so the relative form is a cheap stability guard, not
    the freshness signal).  A file that vanishes between the walk and the
    stat is skipped, exactly like ``_raw_bytes`` skips it.
    """
    rows = []
    for fname in files:
        try:
            st = os.stat(fname)
        except OSError:
            continue
        rows.append((os.path.relpath(fname, root), st.st_mtime_ns, st.st_size))
    return tuple(sorted(rows))


def cache_stats():
    """Return a dict with 'hits', 'misses', 'size'.
    Intended for tests; safe to call from production."""
    with _CACHE_LOCK:
        return {
            "hits": _CACHE_STATS["hits"],
            "misses": _CACHE_STATS["misses"],
            "size": len(_CACHE),
        }


def _languages_present(files):
    """Return the distinct language labels (in ``SUPPORTED_EXTENSIONS``
    order) that at least one file in *files* belongs to.  Extensions that
    share a grammar (``.js``/``.jsx``) are reported once."""
    seen = {
        SUPPORTED_EXTENSIONS[ext]
        for ext in (os.path.splitext(f)[1] for f in files)
        if ext in SUPPORTED_EXTENSIONS
    }
    ordered = []
    for lang in SUPPORTED_EXTENSIONS.values():
        if lang in seen and lang not in ordered:
            ordered.append(lang)
    return ordered


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


def _raw_bytes(files):
    """Sum of ``os.path.getsize()`` for every walked source file.

    This is the real "what the ranked map stands in for" figure that the
    callers use as the token baseline.  A file that disappears between the
    walk and this call is skipped rather than aborting the whole map.
    """
    total = 0
    for fname in files:
        try:
            total += os.path.getsize(fname)
        except OSError:
            continue
    return total


def _preflight_backend():
    """Verify the Python deps required by the ranked map are
    importable. Return None on success, or a short reason string
    naming the missing module.

    ``grep_ast`` and ``networkx`` are checked explicitly because the
    vendored module only imports ``grep_ast`` at module scope while
    ``networkx`` is imported later, during ranking — either one missing
    would otherwise surface as a generic failure.
    """
    for name in ("grep_ast", "networkx"):
        try:
            __import__(name)
        except ImportError:
            return name
    try:
        from ._vendor.aider import repomap  # noqa: F401
    except ImportError as exc:
        return getattr(exc, "name", None) or str(exc)
    return None


def build_ranked_map_with_stats(repo_path, task_hint="", max_tokens=1500):
    """Same as ``build_ranked_map`` but returns ``(text, raw_bytes)``
    where ``raw_bytes`` is the sum of ``os.path.getsize()`` for every
    source file that was walked.

    ``raw_bytes`` lets the caller build an honest token baseline from the
    actual bytes read, instead of guessing from the (already truncated)
    output length.  On the error paths it is ``0``.
    """
    try:
        reason = _preflight_backend()
        if reason:
            return (
                f"ERROR: ranked map unavailable — backend not installed: {reason}",
                0,
            )

        from ._vendor.aider import repomap

        root = os.path.realpath(os.path.abspath(repo_path))
        if not os.path.isdir(root):
            return f"ERROR: ranked map unavailable — not a directory: {root}", 0

        files = _source_files(root)
        if not files:
            return f"# ranked source map — 0 files, 0 symbols\n# {root}", 0

        # Freshness signature: walk + stat only (cheap next to ranking).
        # ``task_hint`` and ``max_tokens`` are part of the signature so a
        # differently-parameterised call can never be served the wrong
        # cached rendering.
        sig = (_signature(files, root), task_hint, max_tokens)

        with _CACHE_LOCK:
            cached = _CACHE.get(root)
            if cached is not None and cached["sig"] == sig:
                _CACHE_STATS["hits"] += 1
                # move root to front of LRU
                if root in _CACHE_ORDER:
                    _CACHE_ORDER.remove(root)
                _CACHE_ORDER.insert(0, root)
                return cached["text"], cached["raw_bytes"]
            _CACHE_STATS["misses"] += 1

        raw_bytes = _raw_bytes(files)

        repo = repomap.RepoMap(
            root=root,
            io=repomap.InputOutput(),
            main_model=repomap.Model(),
        )

        # 1. Raw inventory — one get_tags_raw call per walked source file.
        raw_entries = []
        for fname in files:
            rel = os.path.relpath(fname, root)
            tags = repo.get_tags_raw(fname, rel)
            if tags:
                raw_entries.extend(tags)

        # 2. Ranking — one get_ranked_tags call over the walked files.
        ident_hint = _hint_idents(task_hint)
        ranked_entries = repo.get_ranked_tags(
            [],
            files,
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
        header = f"# ranked source map — {len(files)} files, {len(merged)} symbols"
        lines = [header]
        languages = _languages_present(files)
        if len(languages) > 1:
            lines.append("# languages: " + ", ".join(languages))
        if task_hint:
            lines.append(f"# task: {str(task_hint).strip()}")

        budget = max(1, cap - len(_TRUNCATION_MARKER))
        used = 0
        truncated = False
        for line in lines:
            used += len(line) + 1
        if used > budget:
            # Degenerate: even the header does not fit.
            return (
                header[: max(1, cap - len(_TRUNCATION_MARKER))] + _TRUNCATION_MARKER,
                raw_bytes,
            )

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

        # Normal, rendered map only: error tuples and the degenerate
        # header-only early return above are never cached.
        with _CACHE_LOCK:
            _CACHE[root] = {"sig": sig, "text": text, "raw_bytes": raw_bytes}
            if root in _CACHE_ORDER:
                _CACHE_ORDER.remove(root)
            _CACHE_ORDER.insert(0, root)
            while len(_CACHE_ORDER) > _CACHE_MAX:
                evict = _CACHE_ORDER.pop()
                _CACHE.pop(evict, None)

        return text, raw_bytes
    except Exception as exc:
        return f"ERROR: ranked map unavailable — {exc}", 0


def build_ranked_map(repo_path, task_hint="", max_tokens=1500):
    """Return a bounded ranked map of source symbols in repo_path.
    Supports Python, JavaScript, TypeScript, and TSX.
    task_hint biases ranking when provided. Returns a string."""
    text, _raw = build_ranked_map_with_stats(repo_path, task_hint, max_tokens)
    return text


# Backward-compat alias: Day 1 exposed this name for the Python-only map.
# The implementation is now multi-language, but the old name still works.
build_ranked_python_map = build_ranked_map
