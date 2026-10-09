# Tools

| Tool | Purpose | Token saving |
|---|---|---|
| get_repo_skeleton | Python symbol map with inclusive line ranges | ★★★ |
| rip_file_lines | Read a line window | ★★★ |
| apply_search_replace | Surgical block edit | ★★★ |
| find_dependent_references | Bounded Python text search (40 matches / 8,000 chars). Not semantic caller analysis. | ★★ |
| lint_file | py_compile check | — |
| git_checkpoint | Stage + commit one file | — |
| rollback_show | Read-only impact report | — |
| execute_and_capture | Run whitelisted command | ★★ |
| inspect_database_schema | SQLite table listing | ★ |

## get_repo_skeleton
Returns a bounded AST/ranked map of a repository.
- mode="outline" (default): Python symbol outline with line ranges.
- mode="ranked": task-biased ranked map of Python, JavaScript,
  TypeScript, and TSX symbols. Bounded by max_tokens.
  Top-ranked symbols first. If truncated, a marker shows where.
  Uses a vendored copy of Aider's repomap (Apache-2.0; see
  LICENSES/ and THIRD_PARTY_NOTICES.md).

  Freshness: mode="ranked" reuses an in-process map when no walked
  file has changed (by mtime_ns + size). When a file is added,
  edited, or removed, the next call recomputes silently. The cache
  holds at most 5 repository roots; older entries are evicted LRU.
  Deleted files are noticed on the next call, not eagerly.

  Dependencies: ranked mode requires grep-ast and networkx (installed
  by the harness venv). If they are not importable, mode="ranked"
  returns a clear error and get_repo_skeleton falls back to outline.
