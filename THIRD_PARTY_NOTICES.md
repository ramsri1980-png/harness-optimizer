# Third-party notices

This package vendors third-party code. Each entry lists the upstream
project, the exact revision used, the files taken, the license, and any
adaptation applied.

---

## Aider

- **Project:** [Aider](https://github.com/Aider-AI/aider) — AI pair programming in the terminal.
- **Pinned commit:** `5dc9490bb35f9729ef2c95d00a19ccd30c26339c` (short `5dc9490`)
- **License:** Apache License, Version 2.0 — full text in
  [`LICENSES/Aider-Apache-2.0.txt`](LICENSES/Aider-Apache-2.0.txt)
- **Files copied:**

  | Upstream | Vendored as |
  |---|---|
  | `aider/repomap.py` | `templates/harness_core/_vendor/aider/repomap.py` |
  | `aider/special.py` | `templates/harness_core/_vendor/aider/special.py` |
  | `aider/queries/tree-sitter-language-pack/python-tags.scm` | `templates/harness_core/_vendor/aider/queries/tree-sitter-language-pack/python-tags.scm` |
  | `aider/queries/tree-sitter-languages/python-tags.scm` | `templates/harness_core/_vendor/aider/queries/tree-sitter-languages/python-tags.scm` |
  | `aider/queries/tree-sitter-language-pack/javascript-tags.scm` | `templates/harness_core/_vendor/aider/queries/tree-sitter-language-pack/javascript-tags.scm` |
  | `aider/queries/tree-sitter-languages/javascript-tags.scm` | `templates/harness_core/_vendor/aider/queries/tree-sitter-languages/javascript-tags.scm` |
  | `aider/queries/tree-sitter-languages/typescript-tags.scm` | `templates/harness_core/_vendor/aider/queries/tree-sitter-languages/typescript-tags.scm` |

  All `.scm` files are byte-identical to upstream (verified with `cmp`
  against the pinned commit); none of them carry adaptations.

- **Query coverage — Python, JavaScript, TypeScript, and TSX.**  The
  vendored tag queries cover those four languages:

  | Language | `tree-sitter-language-pack/` | `tree-sitter-languages/` |
  |---|---|---|
  | Python | `python-tags.scm` | `python-tags.scm` |
  | JavaScript | `javascript-tags.scm` | `javascript-tags.scm` |
  | TypeScript | *(not shipped upstream)* | `typescript-tags.scm` |
  | TSX | *(not shipped upstream)* | *(not shipped upstream)* |

  Upstream Aider ships **no `tsx-tags.scm`** in either query backend, so
  none was copied. TSX files are still handled: the extension→language
  lookup reports `.tsx` as `typescript`, so `.tsx` is parsed with the
  TypeScript grammar and tagged with `typescript-tags.scm`. Because that
  grammar is used rather than the dedicated TSX grammar, and because
  upstream's TypeScript query has no arrow-function rule, a TSX *arrow*
  component (`export const X = () => <div/>`) is captured only as a
  backfilled `ref`, not as a `def`; `function`-declared and `class`
  components are captured normally. The same applies to JSX arrow
  components in `.jsx` files (which also have no upstream query and are
  tagged with `javascript-tags.scm`). Full detail, including the exact
  upstream paths checked, is in
  [`PATCHES.md`](templates/harness_core/_vendor/aider/PATCHES.md).

- **Adaptation:** the vendored files were adapted for this package and are
  **not** unmodified upstream copies. The changes are documented, line by
  line (original → replacement), in
  [`templates/harness_core/_vendor/aider/PATCHES.md`](templates/harness_core/_vendor/aider/PATCHES.md).
  In summary:

  - Aider-internal imports (`aider.dump`, `aider.special`, `aider.waiting`)
    were replaced with local no-op stubs / a relative import.
  - `diskcache` was replaced with an in-memory dict cache (`_MemCache`),
    so no `.aider.tags.cache*` directory is ever written.
  - `tqdm` (progress bar) was replaced with a no-op.
  - All stdout output was removed (`print(...)`, the `__main__` runner),
    because this code runs inside an MCP server whose stdout is the
    protocol channel.
  - `networkx.pagerank` now falls back to networkx's pure-Python power
    iteration when numpy/scipy are not installed.
  - Local `InputOutput` / `Model` stubs were added so a `RepoMap` can be
    constructed without the rest of Aider.
  - Day 1 vendors tag queries for **Python only** (`python-tags.scm` in
    both query backends). Day 2 extended the set to **Python, JavaScript,
    TypeScript and TSX** by adding `javascript-tags.scm` (both backends)
    and `typescript-tags.scm` (`tree-sitter-languages/` only). TSX needs
    no query of its own — see "Query coverage" above. No other languages
    are included.

- **Wrapper:** `templates/harness_core/repomap_adapter.py` is original
  code for this package; it calls the vendored functions listed in its
  module docstring.
- **Runtime dependencies** (installed by `bin/install.js` into the tool's
  venv): `grep-ast`, `networkx` (plus their transitive dependencies).
