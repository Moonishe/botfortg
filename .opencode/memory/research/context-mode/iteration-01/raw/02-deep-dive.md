# 02-deep-dive.md — context-mode (Deep Dive Perspective)

## Research Methods
- read of src/server.ts, src/store.ts, src/executor.ts, src/security.ts, src/session/db.ts, src/session/snapshot.ts
- read of hooks/pretooluse.mjs, hooks/posttooluse.mjs, hooks/sessionstart.mjs, hooks/core/routing.mjs
- read of src/adapters/detect.ts, src/adapters/opencode/plugin.ts
- grep for `server.registerTool`, `ctx_`, `fts5`, `BM25`, `SessionDB`

## Architecture Overview

### MCP Server (src/server.ts)
- Uses `@modelcontextprotocol/sdk` McpServer with stdio transport.
- Registers 11 tools via `server.registerTool()`.
- Wraps every handler with `wrapToolHandler()` to convert storage errors into ToolResult.
- Has suppression logic for OpenCode/Kilo native plugin hosts (#623, #637).
- Auto-indexes session events written by hooks into FTS5 via `maybeIndexSessionEvents()`.
- Tracks per-session stats and can output a version-upgrade hint.

### ContentStore / FTS5 Knowledge Base (src/store.ts)
- SQLite FTS5 virtual table with two tokenizers:
  - `porter unicode61` — exact/stemmed matching
  - `trigram` — substring matching
- Search uses Reciprocal Rank Fusion (RRF) merging both ranked lists.
- Multi-term queries get proximity reranking.
- Fuzzy correction via Levenshtein distance.
- Stopwords list filters common terms.
- Chunks markdown by headings, keeps code blocks intact, caps chunk size at 4096 bytes.
- Stores source attribution, session_id, event_id, timestamps.
- Auto-cleanup of stale DBs (>14 days).

### SessionDB (src/session/db.ts)
- Per-project persistent SQLite database at `~/.<platform>/context-mode/sessions/<hash>.db`.
- Stores events captured by hooks: tool calls, file edits, git ops, errors, decisions, user prompts, etc.
- Multi-writer-safe via WAL + busy_timeout + `withRetry()`.
- Supports both canonical and legacy hash variants for case-insensitive filesystems.

### Snapshot Builder (src/session/snapshot.ts)
- Pure function, no DB I/O.
- Converts stored events into a compact XML resume snapshot.
- Priority tiers: P1 (files, tasks, plans, rules, user prompts), P2 (errors, decisions, git), P3 (latency, MCP), P4 (intent, data).
- Injects search queries instead of raw data; model retrieves details via `ctx_search`.

### Polyglot Executor (src/executor.ts + src/runtime.ts)
- 12 languages: JavaScript, TypeScript, Python, Shell, Ruby, Go, Rust, PHP, Perl, R, Elixir, C#.
- Auto-detects Bun, Node, Python, etc.
- Spawns isolated subprocess, captures stdout, only stdout enters context.
- Handles Windows Git Bash, PowerShell, cmd quirks.
- Authenticated CLIs inherit env/config.

### Security (src/security.ts)
- Reads `.claude/settings.json` style permission rules from project-local and global settings.
- Supports `deny`, `allow`, `ask` patterns.
- Splits chained commands (`&&`, `||`, `;`, `|`, `&`) and extracts subshells.
- File-path globs and bash globs converted to regex.
- `ctx_fetch_and_index` hard-blocks metadata/link-local/reserved IP ranges; loopback/RFC1918 allowed by default, strict mode blocks them too.
- PostToolUse redacts auth/token/cookie fields before persistence.

### Hooks
- **PreToolUse** (`hooks/pretooluse.mjs`) — intercepts Bash/Read/Grep/WebFetch/Agent tools, routes to sandbox, blocks curl/wget/inline HTTP.
- **PostToolUse** (`hooks/posttooluse.mjs`) — extracts events and writes to SessionDB.
- **SessionStart** (`hooks/sessionstart.mjs`) — injects routing block, restores resume snapshot, cleans old data.
- **PreCompact** (`hooks/precompact.mjs`) — builds snapshot before compaction.
- **UserPromptSubmit** (`hooks/userpromptsubmit.mjs`) — captures user prompts and decisions.
- **Stop** (`hooks/stop.mjs`) — captures turn-end state.
- Platform-specific hooks live in `hooks/<platform>/`.

### Platform Adapters (src/adapters/)
- `detect.ts` — algorithmic env-var detection for 17+ platforms, with disambiguation (e.g. VS Code PID vs Claude Code plugin).
- `opencode/plugin.ts` — TypeScript plugin entry for OpenCode/Kilo; uses in-process hooks: `tool.execute.before`, `tool.execute.after`, `experimental.session.compacting`, `experimental.chat.system.transform`, `chat.message`.
- Other adapters: claude-code, gemini-cli, codex, cursor, vscode-copilot, jetbrains-copilot, openclaw, omp, kiro, kimi, qwen-code, antigravity.

### Routing Block (hooks/routing-block.mjs)
- Shared XML/prompt instructions for all platforms.
- Tool naming abstraction via `createToolNamer()` so platform-specific names are injected.
- Hierarchy: MEMORY → GATHER → FOLLOW-UP → PROCESSING → WEB → INDEX.
- Includes guidance on parallel I/O, subagent routing, file writing policy, output constraints.

### db-base.ts
- Lazy loads SQLite backend: bun:sqlite, node:sqlite, or better-sqlite3.
- Probes node:sqlite for FTS5 support before use.
- WAL pragma setup, prepared statement interface, retry logic.

## Key Design Decisions
- **Privacy-first**: all data local; no telemetry.
- **Fail-open hooks**: if a hook errors, the tool is allowed rather than blocked.
- **Suppression diagnostic**: emits stderr when OpenCode/Kilo have both legacy MCP and plugin entries.
- **Routing via prompt injection + programmatic enforcement**: hooks block/rewrite, and routing instructions guide the model.

## Sources
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\src\server.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\src\store.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\src\session\db.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\src\session\snapshot.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\src\executor.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\src\security.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\hooks\pretooluse.mjs`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\hooks\posttooluse.mjs`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\hooks\sessionstart.mjs`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\hooks\core\routing.mjs`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\src\adapters\detect.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\src\adapters\opencode\plugin.ts`
