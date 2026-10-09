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
  - Day 1 vendors **Python-only** tag queries (`python-tags.scm` in both
    query backends); other languages are intentionally not included.

- **Wrapper:** `templates/harness_core/repomap_adapter.py` is original
  code for this package; it calls the vendored functions listed in its
  module docstring.
- **Runtime dependencies** (installed by `bin/install.js` into the tool's
  venv): `grep-ast`, `networkx` (plus their transitive dependencies).
