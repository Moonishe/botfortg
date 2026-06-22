# synthesis.md — context-mode deep research synthesis

## Research Scope
- Repository: https://github.com/mksglu/context-mode
- Methods: webfetch of key files, full clone, glob, grep, read of core source/tests/configs, review of TelegramHelper `.opencode/memory/checkpoint.md`
- Date: 2026-06-22
- Version examined: v1.0.163 (package.json)

## SUMMARY
context-mode is a mature, cross-platform MCP server + plugin that reduces LLM context-window consumption by keeping raw data in local sandboxes and FTS5-indexed memory instead of the conversation. It supports 17+ AI coding platforms, provides 11 MCP tools (6 sandbox + 5 meta), and implements session continuity via SQLite + FTS5 snapshots. It is licensed under ELv2 (source-available, no SaaS repackaging), written in TypeScript/Bun, and has a strong test-driven culture. The core techniques are highly relevant to TelegramHelper's own context management needs, but the product itself is a Node/Bun runtime with a large surface area, so the best approach is to adopt the design patterns rather than embed the package.

## KEY FINDINGS
1. **Context savings are real and measured.** Benchmarks show 315 KB raw → 5.4 KB context (98%), 56 KB Playwright snapshot → 299 B, 58.9 KB GitHub issues → 1.1 KB. The mechanism is sandboxed execution + on-demand FTS5 retrieval.
2. **"Think in Code" is the central paradigm.** The LLM writes code in `ctx_execute`/`ctx_execute_file` and only the `stdout` summary enters context. This is enforced by hooks + prompt instructions, not by the tools alone.
3. **Session continuity is a second database.** A per-project SessionDB captures tool events, user decisions, errors, git ops, etc. Before compaction a priority-tiered XML snapshot is built; after compaction the model gets search queries, not raw data.
4. **Search is multi-strategy.** ContentStore uses two FTS5 tokenizers (porter + trigram), RRF merge, proximity rerank, and Levenshtein fuzzy correction. This is overkill for simple use but robust for doc/code retrieval.
5. **Routing is two-layer.** Programmatic hooks block/rewrite dangerous tools (curl, wget, inline HTTP, unbounded Bash/Read); prompt instructions (CLAUDE.md/AGENTS.md) guide the model. Hook-capable platforms achieve ~98% savings; non-hook platforms drop to ~60%.
6. **Security is opt-in and fail-open.** Permission rules reuse `.claude/settings.json` format; if absent, nothing is blocked. Network fetch hardening blocks metadata IPs but allows RFC1918 by default. Hooks are designed to allow tools on error.
7. **Platform support is extensive but uneven.** Claude Code, Gemini CLI, VS Code Copilot, JetBrains Copilot, GitHub Copilot CLI, OpenCode, KiloCode, Codex CLI have full/high coverage. Cursor, Kiro, Antigravity CLI are partial. Antigravity IDE and Zed have no hooks.
8. **ELv2 is a strategic license.** It prevents repackaging as a competing managed service while keeping source available. This matters for any code reuse in TelegramHelper.
9. **Codebase is large and tightly coupled.** `src/server.ts` is 4893 lines; `src/store.ts` 2071; `src/session/db.ts` 1687. Bundles are required for distribution. Releasing touches many plugin manifest files.
10. **The product has a commercial layer.** `ctx_insight` now opens a hosted B2B dashboard at context-mode.com/insight; the open-source plugin is the acquisition surface.

## ARCHITECTURE

### Hooks
- **PreToolUse**: intercepts Bash/Read/Grep/WebFetch/Agent tools; denies curl/wget/inline HTTP; nudges unbounded commands to sandbox; injects routing guidance.
- **PostToolUse**: extracts 26 categories of events from tool responses and writes to SessionDB.
- **SessionStart**: injects routing block + restores resume snapshot; cleans stale data.
- **PreCompact**: builds priority-tiered XML snapshot before context compaction.
- **UserPromptSubmit**: captures user prompts and corrections.
- **Stop**: captures turn-end state.
- Platform-specific adapters translate event names and response formats (e.g. Gemini `BeforeTool`, OpenCode `tool.execute.before`).

### Sandbox Tools
- `ctx_execute` spawns an isolated subprocess (12 languages), captures stdout only.
- `ctx_execute_file` loads a file into `FILE_CONTENT` variable inside the sandbox.
- `ctx_batch_execute` runs multiple commands, auto-indexes each output, optionally searches inline.
- Runtimes: Bun preferred, Node.js 22.5+, with better-sqlite3/bun:sqlite/node:sqlite SQLite backend.

### SQLite + FTS5
- **SessionDB**: per-project `~/.<platform>/context-mode/sessions/<hash>.db`; stores events with priority, timestamps, attribution.
- **ContentStore**: per-project `~/.<platform>/context-mode/content/<hash>.db`; FTS5 virtual tables with porter + trigram tokenizers; BM25 ranking; source/session/event attribution.
- **db-base.ts**: lazy SQLite backend selection, WAL pragmas, retry logic, adapters for bun:sqlite and node:sqlite.

## MCP TOOLS
1. `ctx_batch_execute` — batch shell commands + auto-index + inline search
2. `ctx_execute` — sandbox code execution (12 languages)
3. `ctx_execute_file` — sandbox file processing
4. `ctx_index` — index text/file/directory into FTS5
5. `ctx_search` — BM25 search with RRF, proximity, fuzzy, source scoping
6. `ctx_fetch_and_index` — fetch URL(s), convert to markdown, index, TTL cache
7. `ctx_stats` — context savings, call counts, diagnostics
8. `ctx_doctor` — runtime/hook/FTS5/version checks
9. `ctx_upgrade` — upgrade, rebuild, migrate hooks
10. `ctx_purge` — delete session/project knowledge base
11. `ctx_insight` — open hosted Insight dashboard

## RISKS
- **License**: ELv2 prohibits offering as a hosted/managed service. Borrow ideas, not code, to avoid legal friction.
- **Runtime**: Node.js 22.5+ or Bun required; TelegramHelper is Python. Embedding would add a second runtime.
- **Security**: arbitrary code execution in sandbox; network fetch can reach internal networks by default; credential redaction is regex-based; fail-open hooks.
- **Maintenance**: 17+ platform adapters, large bundled codebase, frequent releases, native SQLite dependency edge cases.
- **Model compliance**: routing depends on prompt instructions; weak/non-compliant models can bypass the intended workflow.
- **Platform gaps**: non-hook platforms (Zed, Antigravity IDE) rely only on prompt instructions, cutting effectiveness.

## USAGE PATTERNS
- **Gather + Search**: `ctx_batch_execute(commands, queries, concurrency)` for multi-source research.
- **Sandbox Analysis**: `ctx_execute_file(path, language, code)` for log/CSV/JSON analysis.
- **Doc Fetch + Query**: `ctx_fetch_and_index(url, source)` → `ctx_search(queries, source)`.
- **Session Resume**: `ctx_search(sort: "timeline")` and `ctx_search(source: "compaction", queries: [...])` after compaction.
- **Stats & Hygiene**: `ctx stats`, `ctx doctor`, `ctx purge`.

## CONFIG EXAMPLES
- OpenCode: `{"plugin": ["context-mode"]}` in `opencode.json` + optional `AGENTS.md`.
- Claude Code: `/plugin marketplace add mksglu/context-mode` / `/plugin install context-mode@context-mode`.
- Gemini CLI: `~/.gemini/settings.json` with `mcpServers` + `hooks` (BeforeTool, AfterTool, PreCompress, SessionStart).
- VS Code Copilot: `.vscode/mcp.json` + `.github/hooks/context-mode.json`.
- Cursor: `.cursor/mcp.json` + `.cursor/hooks.json` + `.cursor/rules/context-mode.mdc`.

## RECOMMENDATIONS FOR TELEGRAMHELPER
TelegramHelper (Python 3.13, aiogram, SQLAlchemy, SQLite + Qdrant, strict async/await) can adapt several context-mode techniques without adopting the Node runtime:

1. **Adopt "Think in Code" in AGENTS.md**
   - Add a rule: for analysis/count/filter/parse tasks, prefer Python sandbox execution (via `ctx_execute`-equivalent or local Python snippet) and only return summary/stdout to context.
   - Avoid raw Bash/Read dumps for large outputs.

2. **Build a lightweight FTS5 memory layer**
   - TelegramHelper already uses SQLite. Add an FTS5 table for indexed session knowledge, docs, and tool outputs.
   - Use SQLAlchemy ORM + raw DDL only in `init_db()` (per project rules), or use a dedicated repository with FTS5 created via Alembic-compatible path.
   - Source labels and `sort: "timeline"` are easy to implement.

3. **Session snapshots before compression**
   - Before OpenCode DCP/context compaction, write a compact XML/markdown snapshot of active files, decisions, errors, blockers.
   - Inject it into the system prompt after compaction, plus search queries so the model retrieves details.

4. **Tool routing rules**
   - Extend TelegramHelper's `AGENTS.md` with a whitelist: Bash only for git/mkdir/rm/mv/cd/ls/npm/pip; large outputs go through a sandbox or indexed query.
   - Since TelegramHelper is a bot, not an IDE, hooks are not available; rely on prompt instructions and self-discipline.

5. **Qdrant for long-term semantic memory, SQLite FTS5 for exact retrieval**
   - Qdrant (already used) handles semantic similarity; SQLite FTS5 handles exact code examples, decisions, and session events.
   - Mirror context-mode's hybrid: semantic for fuzzy recall, FTS5 for exact snippets.

6. **Sandbox for dangerous operations**
   - Use `asyncio.to_thread()` or a constrained subprocess to run user-triggered analysis scripts, returning only stdout.
   - Do not expose arbitrary code execution to untrusted users; gate behind admin/role checks.

7. **Do NOT embed context-mode npm package**
   - It requires Node/Bun and adds a huge dependency surface. Adopt patterns, not the package.

## CONFIDENCE
**High** for the architecture, tool list, license, and integration patterns (we read the core source, README, configs, and tests). **Medium** for historical depth (clone was `--depth 1`, so only the latest commit was available). **Medium** for exact benchmark reproducibility (fixtures exist, but we did not run the suite).

## GAPS
- Did not run the test suite or live benchmarks (no Bun/Node setup in the target environment).
- Did not inspect the hosted Insight dashboard internals (only the launcher URL).
- Did not read every platform adapter end-to-end (focused on OpenCode/Claude/Gemini/Cursor/Codex).
- Did not inspect the bundled `server.bundle.mjs` / `cli.bundle.mjs` contents.
- `--depth 1` clone limits commit history; full evolution timeline is inferred from issue references and README.
- Did not verify npm download/user stats independently beyond the README badges.
