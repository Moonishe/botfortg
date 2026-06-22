# 01-birds-eye.md — context-mode (Bird's Eye Perspective)

## Research Methods
- webfetch on README.md, CLAUDE.md, CONTRIBUTING.md, BENCHMARK.md, package.json, LICENSE
- glob on the cloned repo to enumerate file structure
- read of package.json, README.md metadata

## What It Is
context-mode is an MCP server + plugin designed to optimize the LLM context window. It is marketed as "The other half of the context problem." The repository is `https://github.com/mksglu/context-mode`.

## Public Metrics
- GitHub stars: 17.9k
- Forks: 1.3k
- Commits: 1,999 (per the prompt; the repo is actively maintained)
- npm package: `context-mode` v1.0.163 (as of the cloned package.json)
- License: Elastic License 2.0 (ELv2)

## Four Pillars (from README)
1. **Context Saving** — sandbox tools keep raw data out of the context window. 315 KB raw becomes 5.4 KB, ~98% reduction.
2. **Session Continuity** — every file edit, git op, task, error, and user decision is tracked in SQLite + FTS5. When the conversation compacts, the model restores state via BM25 search instead of dumping raw events back into context.
3. **Think in Code** — the LLM should write code to analyze/count/filter data and only `console.log()` the answer, not read raw data into context.
4. **No prose enforcement** — context-mode does not dictate how the model writes its final answer; it only controls where data goes.

## 11 MCP Tools
### Sandbox / data tools (6)
- `ctx_batch_execute` — run multiple commands, auto-index output, search inline
- `ctx_execute` — run code in a sandbox (12 languages), only stdout enters context
- `ctx_execute_file` — run code over a file in sandbox
- `ctx_index` — index content into FTS5 knowledge base
- `ctx_search` — BM25 search over indexed content
- `ctx_fetch_and_index` — fetch URL, convert to markdown, index, with TTL cache

### Meta / control tools (5)
- `ctx_stats` — context savings & session diagnostics
- `ctx_doctor` — installation/runtime diagnostics
- `ctx_upgrade` — upgrade plugin, fix hooks, migrate cache
- `ctx_purge` — permanently delete indexed content
- `ctx_insight` — opens hosted dashboard (context-mode.com/insight)

## Supported Platforms
README lists 17+ platforms:
Claude Code, Codex CLI, Cursor, Gemini CLI, VS Code Copilot, JetBrains Copilot, GitHub Copilot CLI, OpenCode, KiloCode, OpenClaw, Pi Agent, Kimi Code, Qwen Code, Antigravity, Antigravity CLI (`agy`), Kiro, Zed, OMP (Oh My Pi).

## Tech Stack
- TypeScript
- Bun preferred (3-5x speed boost), Node.js 22.5+ required
- vitest for tests
- esbuild for bundling (`server.bundle.mjs`, `cli.bundle.mjs`)
- SQLite with FTS5 (better-sqlite3 fallback, bun:sqlite, node:sqlite on Linux)
- MCP SDK v1.26.0

## Repository Layout
- `src/` — server.ts, store.ts, executor.ts, security.ts, runtime.ts, db-base.ts, session/, adapters/, search/
- `hooks/` — plain JS hooks (.mjs) for pretooluse, posttooluse, precompact, sessionstart, userpromptsubmit, stop, plus per-platform subdirs
- `configs/` — per-platform install files (settings.json, mcp.json, AGENTS.md, CLAUDE.md, etc.)
- `skills/` — Claude-style skills (context-mode, ctx-*) and `.claude/skills/context-mode-ops/`
- `tests/` — vitest suite covering server, store, search, routing, hooks, adapters, session, executor
- `web/` — static landing page (context-mode.com) including Insight/Platform marketing
- `scripts/` — postinstall, install-openclaw, heal bundles, version sync

## Key Observations
- The project is heavily cross-platform: each platform has its own adapter and hook config.
- The codebase is large and mature: 4893 lines in `src/server.ts`, 2071 in `src/store.ts`, 1687 in `src/session/db.ts`, 6718 lines in `tests/core/server.test.ts`.
- It bundles everything via esbuild to avoid `node_modules` issues at plugin runtime.
- There is no telemetry/cloud; data stays local.

## Sources
- `https://github.com/mksglu/context-mode/blob/main/README.md`
- `https://github.com/mksglu/context-mode/blob/main/package.json`
- `https://github.com/mksglu/context-mode/blob/main/BENCHMARK.md`
- `https://github.com/mksglu/context-mode/blob/main/CONTRIBUTING.md`
- `https://github.com/mksglu/context-mode/blob/main/CLAUDE.md`
- cloned repo at `C:\Users\My\AppData\Local\Temp\opencode\context-mode`
