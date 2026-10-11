# Harness Tool Subsets

## Why
Every tool's JSON schema (name + description + parameters) ships on
every model request. The 9 tools cost ~357 tokens/request after
compression. Registering only the tools a user needs cuts that
proportionally — a 4-tool subset costs ~130 tokens/request.

## How
Set HARNESS_TOOLS before launching OpenCode:

    export HARNESS_TOOLS=none
    export HARNESS_TOOLS=all
    export HARNESS_TOOLS=get_repo_skeleton,rip_file_lines

Add to ~/.bashrc to persist.

## Recommended subsets

### read-only (2 tools) — ~75 tokens/request
    HARNESS_TOOLS=get_repo_skeleton,rip_file_lines
Use when: exploring code, answering questions, generating docs.

### read + edit (4 tools) — ~180 tokens/request
    HARNESS_TOOLS=get_repo_skeleton,rip_file_lines,apply_search_replace,lint_file
Use when: refactoring code, adding features.

### full dev without exec (5 tools) — ~240 tokens/request
    HARNESS_TOOLS=get_repo_skeleton,rip_file_lines,apply_search_replace,lint_file,git_checkpoint
Use when: focused code work where you handle git manually.

### all (9 tools, default) — ~357 tokens/request
    HARNESS_TOOLS=all
Use when: full workflow including exec, database, rollback.

## Impact table

| Subset | Tools | Schema tokens/req | Session savings (5-turn) |
|---|---|---|---|
| none    | 0 | 0   | ~1,785 |
| read    | 2 | ~75 | ~1,410 |
| read+edit | 4 | ~180 | ~885 |
| full-no-exec | 5 | ~240 | ~585 |
| all     | 9 | ~357 | 0 |

## Notes
- Disabled tools are not registered with the MCP client.
- Restart OpenCode after changing the env var.
- Verify:

    python3 -c "
    import os; os.environ['HARNESS_TOOLS']='get_repo_skeleton,rip_file_lines'
    import sys; sys.path.insert(0,'templates')
    import asyncio, server
    tools = asyncio.run(server.mcp.list_tools())
    print([t.name for t in tools])
    "
