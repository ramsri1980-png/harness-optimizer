# PATCHES — vendored Aider `repomap.py` / `special.py`

Vendored from: <https://github.com/Aider-AI/aider>
Pinned commit: `5dc9490bb35f9729ef2c95d00a19ccd30c26339c` (short `5dc9490`)
License: Apache-2.0 (see `LICENSES/Aider-Apache-2.0.txt`)

Files vendored:

- `aider/repomap.py` → `templates/harness_core/_vendor/aider/repomap.py`
- `aider/special.py` → `templates/harness_core/_vendor/aider/special.py`
  (unchanged — its only import is the stdlib `import os`)
- `aider/queries/tree-sitter-language-pack/python-tags.scm`
  → `templates/harness_core/_vendor/aider/queries/tree-sitter-language-pack/python-tags.scm`
- `aider/queries/tree-sitter-languages/python-tags.scm`
  → `templates/harness_core/_vendor/aider/queries/tree-sitter-languages/python-tags.scm`
- `aider/queries/tree-sitter-language-pack/javascript-tags.scm` (Day 2)
  → `templates/harness_core/_vendor/aider/queries/tree-sitter-language-pack/javascript-tags.scm`
- `aider/queries/tree-sitter-languages/javascript-tags.scm` (Day 2)
  → `templates/harness_core/_vendor/aider/queries/tree-sitter-languages/javascript-tags.scm`
- `aider/queries/tree-sitter-languages/typescript-tags.scm` (Day 2)
  → `templates/harness_core/_vendor/aider/queries/tree-sitter-languages/typescript-tags.scm`

Every edit made to the vendored `repomap.py` is listed below as
`original` → `replacement`.

## Import block

- **Original**
  ```
  from diskcache import Cache
  ```
  **Replacement** — import removed; local `_MemCache` class defined
  (see "diskcache usage" below).

- **Original**
  ```
  from tqdm import tqdm
  ```
  **Replacement** — import removed; local no-op
  `def tqdm(iterable=None, *_args, **_kwargs): return iterable`.

- **Original**
  ```
  from aider.dump import dump
  ```
  **Replacement** — import removed; local no-op `def dump(*_args, **_kwargs): pass`.

- **Original**
  ```
  from aider.special import filter_important_files
  ```
  **Replacement**
  ```
  from .special import filter_important_files
  ```

- **Original**
  ```
  from aider.waiting import Spinner
  ```
  **Replacement** — import removed; local no-op `class Spinner` with
  `__init__(*a, **k)` and `step(*a, **k)` methods.

- **Not present at this commit** (recorded for the record; no change was
  needed): `from aider.io import InputOutput`, `from aider.llm import litellm`,
  `from aider.models import Model`, `from aider.utils import ...`,
  `from rich...`, `import rich`, `from rich.console import Console`.
  There is **no** module-level `Console()` and no `console.print` anywhere in
  the vendored file.

## diskcache usage

- **Original**
  ```
              new_cache = Cache(path)
  ```
  **Replacement**
  ```
              new_cache = _MemCache(path)
  ```
- **Original**
  ```
              self.TAGS_CACHE = Cache(path)
  ```
  **Replacement**
  ```
              self.TAGS_CACHE = _MemCache(path)
  ```
- Added class `_MemCache` — an in-memory dict wrapper with the call
  surface this module uses: `get`, `set`, `keys`, `__contains__`,
  `__getitem__`, `__setitem__`, plus `__len__` and `__delitem__`, which
  the original `diskcache.Cache` also provided and which this module
  relies on (`len(self.TAGS_CACHE)`, `del new_cache[test_key]`).
  No `.aider.tags.cache.v*` directory is ever written to disk now.

## stdout safety

The MCP server uses stdout as its protocol channel, so no vendored code
may print there.

- **Original**
  ```
              print(f"Skipping file {fname}: {err}")
  ```
  **Replacement**
  ```
              except Exception:
                  return
  ```
  (the `print` statement was deleted; the early `return` is unchanged, so
  unreadable/unsupported files are still skipped)

- **Original**
  ```
  if __name__ == "__main__":
      fnames = sys.argv[1:]
      ...
      dump(len(repo_map))
      print(repo_map)
  ```
  **Replacement** — the whole `__main__` runner block was deleted (it was
  dead code for this package and called `dump(...)` / `print(...)`).
  A comment marks where it was.

Remaining `print(` occurrences are comments only (e.g. `# print(f"{rank:.03f} ..."`).

## Stubs added for our own callers (not used by the original module)

These exist so the adapter can construct a `RepoMap` without pulling in
the rest of Aider:

- `class InputOutput` — `read_text(fname)` plus no-op `tool_output`,
  `tool_warning`, `tool_error`. (`RepoMap` calls `self.io.read_text`,
  `self.io.tool_output`, `self.io.tool_warning`, `self.io.tool_error`.)
- `class Model` — `token_count(text)` returning `len(text) // 4`
  (consumed by `RepoMap.token_count`).

## Queries layout

Upstream keeps the `.scm` files one level deeper than the vendoring
instructions assumed. Aider's `get_scm_fname()` resolves
`resources.files(__package__).joinpath("queries", subdir, f"{lang}-tags.scm")`
with `subdir` in (`tree-sitter-language-pack`, `tree-sitter-languages`),
so the vendored copies mirror that layout instead of sitting flat in
`queries/`:

- `queries/*.scm` does not exist upstream; the files live in
  `queries/tree-sitter-language-pack/*.scm` and
  `queries/tree-sitter-languages/*.scm`.
- Day 1 ships **Python only**: both `python-tags.scm` files were copied
  (both subdirs are needed because `USING_TSL_PACK` selects between them).
- **Day 2 adds JavaScript, TypeScript and TSX.**  Three more `.scm` files
  were copied from the pinned commit:

  | Language | `tree-sitter-language-pack/` | `tree-sitter-languages/` |
  |---|---|---|
  | javascript | `javascript-tags.scm` | `javascript-tags.scm` |
  | typescript | **not shipped upstream** | `typescript-tags.scm` |
  | tsx | **not shipped upstream** | **not shipped upstream** |

  `get_scm_fname(lang)` already falls back: when `USING_TSL_PACK` is true
  it tries `tree-sitter-language-pack/<lang>-tags.scm` first and, if that
  path does not exist, falls through to `tree-sitter-languages/<lang>-tags.scm`.
  Both subdirs are still vendored, so whichever backend is in use resolves.

### TSX (deviation — recorded as required)

Upstream Aider ships **no `tsx-tags.scm` in either query directory**, so
none was copied. TSX still works, because the extension→language mapping
used by the vendored module (`grep_ast.filename_to_lang`) reports `.tsx`
as **`typescript`**, not `tsx`:

```
a.ts  → typescript
a.tsx → typescript
```

So `.tsx` files are parsed with the `typescript` grammar and tagged with
`typescript-tags.scm`.  This is verified behaviour, not an assumption:
`export function PanelWidget(...)` / `export class BoxWidget` inside
JSX-bearing `.tsx` files produce normal `def` tags with correct line
numbers (tree-sitter's error recovery handles the JSX nodes).

**Known limitation (reported, not silently skipped):** because the
`typescript` grammar is used rather than the dedicated `tsx` grammar, and
because upstream `typescript-tags.scm` has no `lexical_declaration` /
arrow-function rule, a TSX *arrow* component is **not** captured as a
`def`:

```tsx
export const FooterWidget = () => <footer>f</footer>;   // ← no def tag
export function PanelWidget() { return <div/>; }        // ← def tag ✓
```

Arrow components still appear in the map as pygments-backfilled `ref`
tags, so the file is never silently dropped — only the `def` tag is
missing. Fixing this properly needs an upstream `tsx-tags.scm` (or an
arrow rule in `typescript-tags.scm`) plus teaching the language lookup to
map `.tsx` → `tsx`; both would be changes to vendored files, so they are
deliberately **not** made here.

### `.jsx`

Upstream also ships no `jsx-tags.scm`; `filename_to_lang(".jsx")` reports
`javascript`, so `.jsx` is tagged with `javascript-tags.scm`.  Note the
JavaScript query has no JSX-specific rules either, so JSX *arrow*
components in `.jsx` files have the same `def`-tag limitation as TSX above.

## PageRank without numpy/scipy

- **Original**
  ```
              ranked = nx.pagerank(G, weight="weight", **pers_args)
              ...
                  ranked = nx.pagerank(G, weight="weight")
  ```
  **Replacement** — both call sites now go through a local `_pagerank`
  helper defined right after `import networkx as nx` inside
  `RepoMap.get_ranked_tags`:
  ```
          def _pagerank(graph, **kwargs):
              try:
                  return nx.pagerank(graph, **kwargs)
              except ImportError:
                  from networkx.algorithms.link_analysis.pagerank_alg import (
                      _pagerank_python,
                  )
                  return _pagerank_python(graph, **kwargs)
  ```
  **Why:** networkx's public `pagerank()` always dispatches to
  `_pagerank_scipy()`, which does `import numpy as np` (verified on
  networkx 3.7: `ModuleNotFoundError: No module named 'numpy'`).
  Aider's own requirements.txt pulls in numpy+scipy, but this package
  installs only `grep-ast networkx` (see `bin/install.js`), so the fast
  path is used when numpy/scipy happen to be present and networkx's pure
  Python power iteration is used otherwise. The original
  `except ZeroDivisionError` handling (Issue #1536) is preserved.

## Files left untouched

- `special.py` — copied verbatim, no changes.
