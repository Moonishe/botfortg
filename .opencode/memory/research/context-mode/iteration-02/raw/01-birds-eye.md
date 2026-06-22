# Researcher 1 — Bird's Eye: context-mode (mksglu/context-mode)

**Repository:** https://github.com/mksglu/context-mode
**Sources fetched:** README.md, package.json, LICENSE (raw.githubusercontent.com, branch `main`)
**Date:** 2026-06-22
**Focus:** MCP server for context savings, platform support, FTS5 memory, session continuity, ELv2 license, benchmarks

---

## SUMMARY

context-mode (v1.0.163) is an MCP server/plugin by Mert Koseoğlu that addresses the "other half of the context problem" — keeping raw tool output out of the LLM context window via sandboxed code execution, an FTS5/BM25 knowledge base, and session continuity across compaction. It claims 98% context savings (315 KB → 5.4 KB per session) and supports 17+ AI coding clients (Claude Code, Gemini CLI, VS Code/JetBrains Copilot, Cursor, OpenCode, Codex CLI, and more) through a mix of native hooks, plugin integrations, and manual routing files. The project is licensed under Elastic License 2.0 (ELv2), a source-available license that prohibits offering it as a hosted/managed SaaS. It reached #1 on Hacker News (570+ points) and claims adoption across teams at Microsoft, Google, Meta, Amazon, NVIDIA, and others. All processing is local (no telemetry, no cloud sync); session state and indexed content persist in per-project SQLite databases.

---

## KEY_FINDINGS

1. **Core value proposition — 98% context savings.** Sandbox tools (`ctx_execute`, `ctx_execute_file`, `ctx_batch_execute`) run code in isolated subprocesses where only stdout enters context. Benchmarks: Playwright snapshot 56.2 KB → 299 B (99%), 20 GitHub Issues 58.9 KB → 1.1 KB (98%), access log (500 req) 45.1 KB → 155 B (100%), repo research (subagent) 986 KB → 62 KB (94%). Full session: 315 KB raw → 5.4 KB; session time extends from ~30 min to ~3 hours. Full benchmark data in separate BENCHMARK.md (21 scenarios).

2. **17+ platform support (verified via README install sections + platform compatibility table).** Platforms: Claude Code, Gemini CLI, VS Code Copilot, JetBrains Copilot, GitHub Copilot CLI, Cursor, OpenCode, KiloCode, OpenClaw/Pi Agent, Codex CLI, Kimi Code, Qwen Code, Antigravity, Antigravity CLI (agy), Kiro, Zed, Pi Coding Agent, OMP (Oh My Pi). Hook-capable platforms achieve ~98% savings (automatic routing enforcement); non-hook platforms (Zed, Antigravity IDE) get ~60% via manual routing file copy. Platform count: README states "all 17 supported clients" plus OpenClaw gateway.

3. **FTS5 memory + BM25 ranking with advanced retrieval.** `ctx_index` chunks markdown by headings (code blocks intact), stores in SQLite FTS5 virtual table. Search uses BM25 with Porter stemming (5x weight on titles/headings). Ranking uses Reciprocal Rank Fusion (RRF) merging Porter stemming + trigram substring strategies. Additional features: proximity reranking (multi-term), Levenshtein fuzzy correction, smart snippets (window extraction around matches, not truncation). TTL cache (default 24h) for fetched content; 14-day cleanup. SQLite backend auto-selected: `bun:sqlite` on Bun, `node:sqlite` on Node >=22.5, `better-sqlite3` fallback.

4. **Session continuity via 5 cooperating hooks.** PreToolUse (routing enforcement), PostToolUse (event capture), UserPromptSubmit (user decisions), PreCompact (snapshot build ≤2 KB), SessionStart (state restore). Events tracked: files, tasks, plans, rules, user prompts, decisions, git ops, errors, error-resolution pairs, constraints, blockers, rejected approaches, environment, agent findings, iteration loops, latency, MCP tools, subagents, skills, external refs. After compaction, model receives a "Session Guide" (15 categories: last request, tasks, decisions, files modified, unresolved errors, etc.). Fresh session (no `--continue`) = immediate deletion of prior session data.

5. **11 MCP tools (6 sandbox + 5 meta).** Sandbox: `ctx_batch_execute` (multi-command, concurrency 1-8), `ctx_execute` (12 languages: JS/TS/Python/Shell/Ruby/Go/Rust/PHP/Perl/R/Elixir/C#), `ctx_execute_file`, `ctx_index`, `ctx_search`, `ctx_fetch_and_index` (URL fetch+index, TTL cache, parallel multi-URL). Meta: `ctx_stats`, `ctx_doctor`, `ctx_upgrade`, `ctx_purge`, `ctx_insight` (hosted dashboard at context-mode.com/insight). Progressive throttling: calls 1-3 normal, 4-8 reduced + warning, 9+ blocked → redirect to `ctx_batch_execute`.

6. **ELv2 license (Elastic License 2.0).** Source-available. Permits use, fork, modify, distribute. Two prohibitions: (a) cannot offer as hosted/managed service, (b) cannot remove licensing notices. Author explicitly chose ELv2 over MIT to prevent repackaging as competing closed-source SaaS. Patent grant included with termination clause for infringement claims.

7. **"Think in Code" paradigm.** Core design philosophy: LLM should generate code that analyzes data, not process data directly. Example: 47 × Read() = 700 KB → 1 × ctx_execute() = 3.6 KB. 12 language runtimes available. Bun auto-detected for 3-5x faster JS/TS. Authenticated CLIs (gh, aws, gcloud, kubectl, docker) work via credential passthrough. When output > 5 KB with intent, switches to intent-driven filtering (index full output, search for relevant sections).

8. **Privacy-first, fully local architecture.** No telemetry, no cloud sync, no usage tracking, no account required. SQLite databases in home directory. MCP protocol layer operation (not a CLI output filter). Storage at `~/.context-mode/content/` and per-adapter session dirs. `CONTEXT_MODE_DIR` env var for custom storage root (v1.0.147+).

9. **Security model.** Extends existing permission rules to MCP sandbox. Pattern `Tool(what to match)` with deny/allow. Deny wins over allow; project-level overrides global. Chained commands (`&&`, `;`, `|`) split and checked individually. Network fetch hardening: only http/https schemes, cloud metadata (169.254.169.254) hard-blocked with DNS-rebinding defense, multicast/reserved blocked, loopback+RFC1918 allowed by default (blockable via `CTX_FETCH_STRICT=1`). Tool input redaction for credentials (token, secret, password, api_key, cookie, etc.) before persistence.

10. **Package metadata.** npm package `context-mode` v1.0.163, ESM module, Node >=22.5, TypeScript 5.7+, esbuild bundling, vitest testing. Dependencies: @modelcontextprotocol/sdk ^1.26.0, better-sqlite3 ^12.6.2, turndown ^7.2.0 (HTML→markdown), zod ^3.25.0. packageManager: pnpm 10.23.0. Multiple plugin manifests: `.claude-plugin/`, `.codex-plugin/`, `.openclaw-plugin/`, `pi` extensions, `omp` field. Build includes bundle assertions and asymmetric drift checks.

---

## ADDITIONAL DETAILS

### Routing enforcement matrix
- **~98% savings (with hooks):** Claude Code, Gemini CLI, VS Code Copilot, JetBrains Copilot, GitHub Copilot CLI, Cursor, OpenCode, OpenClaw, Codex CLI, Kiro, Pi, OMP
- **~60% savings (no hooks, manual file):** Antigravity IDE, Zed
- **Bounded:** Antigravity CLI (agy) — PreToolUse for mapped Bash/Read/Grep/WebFetch

### Session completeness by platform
- **Full:** Claude Code, OpenCode, KiloCode
- **High:** Gemini CLI, VS Code Copilot, JetBrains Copilot, GitHub Copilot CLI, OpenClaw, Pi, OMP
- **Partial:** Cursor, Codex CLI, Antigravity CLI (agy), Kiro
- **None:** Antigravity IDE, Zed

### Notable ecosystem signals
- Hacker News #1 (570+ points)
- Discord community (1478479412700909750)
- YouTube demo
- Claims adoption at Microsoft, Google, Meta, Amazon, IBM, NVIDIA, ByteDance, Stripe, Datadog, Salesforce, GitHub, Red Hat, Supabase, Canva, Notion, Hasura, Framer, Cursor (badge-based, unverified)
- Hosted Insight dashboard (context-mode.com/insight) for org analytics — separate from local privacy-first core

### OpenClaw gateway integration
Native gateway plugin (not MCP server). Installs via `npm run install:openclaw`. Registers 8 hooks via `api.on()` + `api.registerHook()`. Requires OpenClaw >2026.1.29 (PR #9761 lifecycle fix). Falls back to DB snapshot reconstruction on older versions.

---

## CONFIDENCE

**High.** All findings sourced directly from README.md, package.json, and LICENSE fetched from the official repository's `main` branch on raw.githubusercontent.com. The README is comprehensive (1612 lines) with detailed install instructions, benchmark tables, platform compatibility matrices, and security documentation. The package.json corroborates version (1.0.163), license (Elastic-2.0), dependencies, and multi-platform plugin manifests. The LICENSE file confirms ELv2 terms verbatim.

**Caveats:**
- Adoption claims (company badges) are self-reported and unverified — they are static badge images, not linked to verifiable endorsements.
- Benchmark figures (98%, KB reductions) are author-reported in README; the separate BENCHMARK.md was not fetched in this iteration.
- The "17+ platform support" count: README explicitly says "all 17 supported clients" in the Think-in-Code section, but the platform compatibility table lists 18 columns (17 + OMP which was added later). The package.json description mentions fewer platforms (marketing simplification).
- Hacker News #1 badge links to a real HN item (id=47193064) but the post content/ranking was not independently verified.
