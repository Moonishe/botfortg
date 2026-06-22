# Researcher 4 — Historian
## Repository: mksglu/context-mode
**Fetched:** 2026-06-22 · **Version studied:** v1.0.163 (latest, released 2026-06-21)
**Sources:** GitHub main page (README), package.json, BENCHMARK.md, stats.json, Releases page (v1.0.157–v1.0.163)

---

# SUMMARY

context-mode is an MCP-server/plugin that optimizes the context window for AI coding agents. Created by **Mert Koseoğlu (mksglu)**, written in **TypeScript (63.9%) / JavaScript (33.0%)**, licensed under **Elastic License 2.0 (ELv2)** — deliberately chosen over MIT to prevent repackaging as a competing closed-source SaaS while keeping source available. As of v1.0.163 it has **17.9k stars, 1.3k forks, 86 watchers, 1,999 commits, 189 releases, and 312.5k+ users** (npm 282.2k+ / marketplace 30.2k+). It hit **Hacker News #1 (570+ points)** and is used across teams at 18 major companies (Microsoft, Google, Meta, Amazon, IBM, NVIDIA, ByteDance, Stripe, Datadog, Salesforce, GitHub, Red Hat, Supabase, Canva, Notion, Hasura, Framer, Cursor).

**Evolution of context-saving techniques** progressed through several generations:
1. **Routing instruction files** auto-written to project dirs on session start — later **disabled** (#158, #164) to prevent git-tree pollution; replaced by runtime hook injection (no file written).
2. **Sandbox execution** — `ctx_execute` (12 languages), only stdout enters context; the "Think in Code" paradigm (LLM as code generator, not data processor). Added `ctx_execute_file` (raw content never leaves sandbox) and `ctx_batch_execute` (multi-call + opt-in concurrency 1–8).
3. **FTS5 BM25 knowledge base** — `ctx_index` + `ctx_search`. Search ranking **evolved**: old cascading fallback (trigram only if porter returned nothing) → **Reciprocal Rank Fusion (RRF)** merging parallel porter-stemming + trigram-substring strategies. Added **Proximity Reranking**, **Fuzzy Correction** (Levenshtein), **Smart Snippets**, headings weighted 5x, Porter stemming at index time.
4. **`ctx_fetch_and_index`** — fetch URL → HTML→markdown → chunk → index; **TTL cache** (default 24h, per-call override via PR #666), 14-day cleanup.
5. **Large-output externalization** — output >100 KB auto-indexes to FTS5 and returns a pointer (no data discarded); >5 KB + `intent` triggers intent-driven filtering.
6. **Progressive throttling** — calls 1–3 normal, 4–8 reduced + warning, 9+ blocked → redirect to `ctx_batch_execute`.
7. **Session continuity** — SQLite event tracking; PreCompact builds ≤2 KB priority-tiered XML snapshot; SessionStart restores via FTS5-indexed events + a 15-category "Session Guide". Resume via `--continue`/`--resume`/`/resume` picker with `session_resume` table fallback.
8. **"No prose-style enforcement"** — added as an explicit 4th design pillar, citing Moonshot AI research that aggressive brevity prompts degrade coding/reasoning benchmarks. Routing governs *where data goes*, not *how the model talks*.
9. **SQLite backend evolution** — `better-sqlite3` → auto-detect `node:sqlite` (Node ≥22.5, avoids sporadic SIGSEGV #564) and `bun:sqlite`; runtime self-heal for missing native binding (#408).
10. **Security hardening** — credential passthrough, permission rules extended into the sandbox, network-fetch SSRF defense (cloud-metadata/link-local/multicast blocked; `CTX_FETCH_STRICT` for private ranges), `tool_input` redaction of secrets before persistence.

**Platform support expansion** grew from an original core (Claude Code, Gemini CLI, VS Code Copilot, OpenCode, Codex CLI — per package.json description) to **17 platforms** by v1.0.163. Adapter count milestones: **14 adapters (v1.0.159)** → 15 → **17 (v1.0.163)**. Added over time: JetBrains Copilot, GitHub Copilot CLI, Cursor, KiloCode, OpenClaw/Pi Agent, Kimi Code, Qwen Code, Antigravity, Antigravity CLI (`agy`), Kiro, Zed, Pi Coding Agent, OMP (Oh My Pi). v1.0.163's headline was adding **GitHub Copilot CLI + Antigravity CLI** (15→17). Each adapter is validated line-by-line against the upstream agent's published contract; hook-capable platforms reach ~98% context savings, instruction-file-only platforms ~60%.

**Benchmark history.** BENCHMARK.md documents **21 scenarios**: 376 KB raw → 16.5 KB context (**96% overall**), 100% code examples preserved exactly. Test suite = **125 tests** (Executor 55, ContentStore 34, MCP Integration 22, Ecosystem Benchmark 14), all passing. README headline: 315 KB → 5.4 KB (**98%**), session lifetime ~30 min → ~3 hours. Newer scenarios: 7.5 MB JSON → 0.9 KB (99%), subagent repo research 986 KB → 62 KB (94%). Routing-enforcement comparison: **hooks ~98% saved vs instruction-file-only ~60% saved**.

**Commercial layer — `ctx_insight` dashboard.** The `ctx_insight` tool/command (`/context-mode:ctx-insight`, `ctx insight`, `context-mode insight`) opens a **hosted Insight dashboard at context-mode.com/insight** — "org analytics for AI-assisted engineering teams." **v1.0.163 explicitly moved ctx_insight from a local dashboard to a hosted product** — this is the commercial pivot. Development of the analytics/insight platform happens in a **separate repo, `context-mode-platform`**, which holds PRDs (e.g. `docs/prds/2026-06-insight-data-flow/`). A novel **cross-PRD cross-repo workflow** is used: an "OSS handoff PRD" plus a separate-LLM "verify-gap PRD" verification pass. Analytics is **opt-in**; the OSS repo's hooks/extractors feed a platform-bridge wire that delivers event payloads. v1.0.162 shipped **multilingual prompt analytics** (10 typed columns + `prompt_word_tokens[]`, script-agnostic via Unicode property escapes — Latin/Cyrillic/Han/Hangul/Arabic/Hebrew/Thai/Greek/Devanagari/Hiragana/Katakana, zero new deps) and **honest cost accounting** (`MODEL_PRICING_USD_PER_MTOK` with verified 2026-06 Anthropic rates; corrected a stale price since v1.0.103 — Opus 4→4.7 was a 3× cut that never propagated). v1.0.160 unified all 5 hook event types through the platform bridge (4 were previously bypassing it). v1.0.158 added a seed-parity envelope (per-event enrichment + 12-field session rollup) and error/command/duration classifiers. v1.0.159 added a universal algorithmic Bash classifier (no regex) across all adapters. The ELv2 license reserves the hosted/managed-service right to the maintainer — making the **hosted Insight dashboard the commercial moat** while the core engine stays source-available and local/privacy-first ("nothing leaves your machine; no telemetry, no cloud sync, no account required"). The opt-in analytics is the bridge between the privacy-first core and the commercial team dashboard.

---

# TIMELINE

> Dates for v1.0.157–v1.0.163 are from the GitHub Releases page (primary source). Earlier milestones are **inferred from issue/PR references and version notes embedded in README/docs** (no early release-date fetch performed); these are marked *(inferred)*.

### Origin & early architecture *(inferred)*
- **Pre-v1.0.140 (inferred):** Initial release as an MCP server. Original supported core: Claude Code, Gemini CLI, VS Code Copilot, OpenCode, Codex CLI (per package.json `description`). Context saving via sandboxed `ctx_execute` + FTS5 `ctx_index`/`ctx_search`. Routing was done by **auto-writing instruction files (CLAUDE.md/GEMINI.md/AGENTS.md) into project dirs on session start**.
- **#158 / #164 (inferred early):** Auto-writing routing files **disabled** to prevent git-tree pollution. Hook-capable platforms switched to runtime hook injection; non-hook platforms (Zed, Antigravity IDE) still require one-time manual copy (~60% compliance).
- **#408 (inferred):** Windows missing-`better_sqlite3.node` self-heal added (postinstall + runtime hook re-fetch prebuild, no manual `npm rebuild`).

### Version-anchored milestones *(from README/docs version notes)*
- **v1.0.103 (inferred):** Pricing baseline — `analytics.ts` carried Opus 4 rates; the Opus 4 → 4.7 3× price cut never propagated here, leaving cost accounting stale until v1.0.162.
- **v1.0.140+ :** Emits a stderr diagnostic when an OpenCode/KiloCode config has BOTH `plugin: ["context-mode"]` AND a legacy `mcp.context-mode` entry (which would register zero `ctx_*` tools); guidance to run `context-mode upgrade`.
- **v1.0.147+ :** `CONTEXT_MODE_DIR` environment variable added — absolute writable root for storage (`<root>/sessions`, `<root>/content`).
- **v1.0.152 :** Established the release-notes style anchor (`STYLE-ANCHOR-v1.0.152.md`); v1.0.153–v1.0.161 later retroactively rewritten to this community-grade style.

### PR/issue-driven technique evolution *(inferred from refs)*
- **PR #529 (inferred):** External-MCP "wrap large payloads in ctx_execute" nudge originally fired once per session — got lost after context compaction in MCP-heavy sessions.
- **#567 (inferred follow-up):** Nudge changed to re-fire every Nth matching call. Shipped as `CONTEXT_MODE_EXTERNAL_MCP_NUDGE_EVERY` (default 10, range [1,100]).
- **#564 (inferred):** Linux + Node <22.5 declared unsupported (node:sqlite unavailable → better-sqlite3 SIGSEGV risk); `npm install` fails with remediation instructions.
- **#473 (inferred):** OMP and Pi storage isolation — OMP roots under `~/.omp/context-mode/` so OMP and Pi never share session DBs/content/stats.
- **#485 / #489 (inferred):** Cursor marketplace plugin submitted, **awaiting Cursor team review**; local-folder install path provided as interim.
- **PR #666 (inferred):** Per-call TTL override for `ctx_fetch_and_index` (`ttl: <ms>`; `ttl: 0` / `force: true` to bypass).
- **#774 (inferred):** `agy` (Antigravity CLI) mis-detected as Claude Code — probed before the generic `~/.claude` fallback so gemini-cli→agy migrants aren't misrouted.
- **#775 (inferred):** GitHub Copilot CLI support tracking issue.

### Recent releases *(primary source: GitHub Releases page)*
- **2026-05-31 — v1.0.157:** (release notes truncated in fetch; immediately preceded the v1.0.158 seed-parity work).
- **2026-06-01 — v1.0.158:** Seed-parity envelope — every event enriched with per-event facts (error category, file paths, session origin, blocker status) + a 12-field session-level rollup (`getSessionRollup`) stamped on every outbound event. New `src/session/error-classifier.ts` (10 categories: auth, network, permission, not_found, syntax, dependency, timeout, crash, validation, unknown), `deriveBashMetadata` (canonical verbs), `readLatencyMs` (PreToolUse→PostToolUse marker delta). Windows test hardening (APPDATA/XDG overrides, drop `/bin/bash` from execSync).
- **2026-06-01 — v1.0.159:** Universal **algorithmic Bash classifier** (`deriveBashMetadata`, pure character-class scan, no regex, no per-language table) rolled out to **all 14 adapters**. `session_start` lifecycle anchor event via `emitSessionStartLifecycle` (startup/resume/compact branches). Codex hook deduplication (#746 by @ken-jo — plugin-owned installs were firing hooks twice via manifest + marketplace).
- **2026-06-01 — v1.0.160:** **4 hook surfaces** (posttooluse, precompact, sessionstart, userpromptsubmit) that were bypassing the platform-bridge wire and writing SessionDB directly now route through `attributeAndInsertEvents` — single canonical insert path. Foundation for v1.0.162's 9 new event types.
- **2026-06-01 — v1.0.161:** Codex **Windows symlink fix** (#748 by @NgoQuocViet2001) — marketplace symlink source rejected by Windows file APIs on first install; macOS/Linux unchanged. Seed-parity integration suite green on `windows-latest`. New `.agents/plugins/marketplace.json` layout.
- **2026-06-02 — v1.0.162:** **Multilingual prompt analytics + honest cost accounting + 13 new extractors.** Layer 1 expanded 5→10 typed columns + `prompt_word_tokens[]` (script-agnostic Unicode property escapes, 11 scripts, zero new deps). `MODEL_PRICING_USD_PER_MTOK` with verified 2026-06 Anthropic rates; `extractAgentUsage` derives `cost_usd`; ctx-stats headline corrected `$1399.73 of Opus 4` → `$466.58 of Opus 4.7`. 9 new event types (`bash_outcome`, `file_read_metadata`, `webfetch_metadata`, `worktree_exit`, `agent_usage`, `session_settings_snapshot`, `git_commit`, `plan_enter` slash, enriched `plan_exit`). POSIX argv tokenizer for commit-message capture (`-m`/`-am`/`--message=`/env-prefixed/`--amend`). Cross-project git attribution (`git -C <dir>`). `/plan` slash fallback (Claude Code Bug #15660). Zero bundle bloat (5 hook bundles = 58 KB total). First end-to-end product of the cross-PRD pipeline (OSS handoff + verify-gap), closing 8 issues from `context-mode-platform` PRDs.
- **2026-06-21 — v1.0.163 (current):** **15 → 17 platforms** — added **GitHub Copilot CLI** (6 hook events: preToolUse/postToolUse/sessionStart/userPromptSubmitted/agentStop/preCompact; MCP via `~/.copilot/mcp-config.json`; plugin or manual install) and **Antigravity CLI (`agy`)** (native plugin: MCP + routing rule + routing skill + bounded PreToolUse/PostToolUse/Stop hooks; `npm run install:agy`). ~30 merged changes / ~20 resolved issues / 14 community contributors. Contributor spotlight @ken-jo (9 PRs). Broad Windows work (PowerShell UTF-8/Unicode paths, Git Bash path conversion, Maven `mvn`→`mvn.cmd`, temp-dir leak fix). Hooks respect per-subagent ctx tool availability (#834). Session-resume no longer re-injects prior-leg events as current (#780). `ctx_search` path-separator normalization on Windows (#827). Per-subagent flood-guard (#769). `mise`/`asdf`/`nvm` Node-upgrade liveness guard (#841). **Product: `ctx_insight` analytics moved from a local dashboard to a hosted product.** Landing site/banners updated to 17 adapters.

### Current metrics snapshot (2026-06-22, from stats.json + repo page)
- Stars **17.9k** · Forks **1.3k** · Watchers **86** · Commits **1,999** · Releases **189**
- Users **312.5k+** (npm **282.2k+** / marketplace **30.2k+**)
- Languages: TypeScript 63.9% · JavaScript 33.0% · HTML 2.2% · Shell 0.9%
- Hacker News #1 (570+ points) — `news.ycombinator.com/item?id=47193064`

---

# CONFIDENCE

| Area | Confidence | Basis |
|------|-----------|-------|
| Current state (v1.0.163): metrics, 17-platform list, tool set, benchmark numbers, license, commercial layer | **High** | Primary sources: README, package.json, BENCHMARK.md, stats.json, Releases page — all fetched directly and cross-consistent. |
| Recent timeline v1.0.157–v1.0.163 (dates + contents) | **High** | GitHub Releases page (primary), with detailed release notes for v1.0.158–v1.0.163. |
| Commercial layer (ctx_insight) pivot & context-mode-platform repo | **High** | v1.0.163 release note explicitly states "moved from a local dashboard to a hosted product"; v1.0.162 notes reference `context-mode-platform` PRDs. |
| Recent platform-count milestones (14 → 15 → 17) | **High** | v1.0.159 notes say "14 adapters"; v1.0.163 says "15 to 17". The intermediate 15 is implied (count grew 14→15→17). |
| Cost-accounting correction (v1.0.103 stale → v1.0.162 fixed) | **High** | Stated verbatim in v1.0.162 release notes. |
| Search-ranking evolution (cascading → RRF + proximity + fuzzy + smart snippets) | **High** | Stated in README "How the Knowledge Base Works" + "Ranking: Reciprocal Rank Fusion" sections. |
| Early technique evolution (#158/#164 routing-file disable, #408 self-heal, #529/#567 nudge, #564 Linux, #666 TTL, #473 OMP isolation) | **Medium** | Inferred from issue/PR references embedded in README/docs — the *existence* and *purpose* of each change is well-attested, but **exact release versions and dates for these early items were not fetched** (no early release-date retrieval performed). |
| Pre-v1.0.140 origin sequencing & original platform core | **Medium** | package.json `description` names the original 5 platforms; early architecture inferred from the "evolution" hints in docs rather than a direct changelog read. |
| First-release date / full 189-release history | **Low** | Not fetched. 189 releases span an unknown start date; only the last ~7 releases were dated. The project clearly predates 2026-05-31 by a wide margin given 1,999 commits and 189 releases. |
| "17 platforms" count reconciliation | **Medium** | README tagline says 17; the compatibility table has 18 columns (OMP + Pi may be counted as one Pi-family, or Antigravity IDE + agy as one). v1.0.163 release explicitly says "15 to 17" adding Copilot CLI + agy, so the canonical count is 17 per the maintainer. |

**Overall confidence: High on the current-state and recent-evolution picture; Medium on pre-v1.0.157 history (inferred from in-doc references rather than a fetched early changelog). Recommend a follow-up fetch of the full releases atom feed / early tags to firm up the origin date and pre-v1.0.152 timeline if precise early dating is required.**
