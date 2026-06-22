# 04-historian.md — context-mode (Historian Perspective)

## Research Methods
- bash `git log --oneline -50` in the cloned repo
- read of README.md history-relevant sections (benchmarks, platform support table, changelog-style notes)
- read of CONTRIBUTING.md (architecture, TDD, migration notes)
- grep for issue numbers (#NNN) and version references in source

## Project Genesis / Positioning
- context-mode positions itself as "the other half of the context problem" — the complement to large context windows.
- Started as a Claude Code plugin and expanded to 17+ platforms.
- Gained significant traction: 17.9k stars, 1.3k forks, Hacker News #1 badge.
- Used by teams at Microsoft, Google, Meta, Amazon, IBM, NVIDIA, Stripe, Datadog, Salesforce, GitHub, etc. (per README badges).

## Version / Release Cadence
- Current version at time of clone: **v1.0.163** (package.json, 2026).
- Frequent small releases; many issue numbers in the 400–800 range, indicating active bug fixing.
- Version sync touches: `package.json`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `.cursor-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.openclaw-plugin/openclaw.plugin.json`, `.openclaw-plugin/package.json`, `openclaw.plugin.json`, `.pi/extensions/context-mode/package.json`, `configs/antigravity-cli/plugin.json`, `configs/copilot-cli/.github/plugin/plugin.json`.

## Evident Evolutionary Patterns
1. **Single-platform → multi-platform**: Claude Code plugin expanded to Gemini, Cursor, OpenCode, Kilo, Codex, etc.
2. **MCP-only → plugin-native**: OpenCode/Kilo moved from stdio MCP child to in-process TypeScript plugin to avoid duplicate tool registration.
3. **Session continuity**: Originally just context saving; later added SessionDB, snapshots, compaction recovery, and cross-session memory.
4. **Search ranking**: Started with simple FTS5; evolved to dual tokenizer (porter + trigram) + RRF + proximity rerank + fuzzy correction.
5. **Storage migration**: Case-fold migration for canonical vs legacy project-dir hashes; platform-isolated storage dirs.
6. **Insight pivot**: `ctx_insight` originally built a local dashboard; now opens hosted B2B product at context-mode.com/insight.

## Notable Issues / Fixes Referenced in Code
- #623 / #637 — OpenCode/Kilo native plugin suppression and empty tools/list handler.
- #482 — prose-style policy; no brevity enforcement in shipped configs.
- #230 — MCP readiness sentinel.
- #347 — hardcoded /tmp to avoid TMPDIR mismatch.
- #454 — Windows non-ASCII path handling in rmSync.
- #461 — node:sqlite without FTS5 on Linux.
- #521 / #545 — project dir detection cascade and foreign env var scrubbing.
- #567 — periodic external-MCP guidance.
- #774 — agy vs claude detection.
- #817 — bash nudge min command bytes threshold.

## TDD / Contribution Culture
- CONTRIBUTING.md enforces red-green-refactor, no new test files (add to existing files), output quality comparison before/after PRs.
- Test suite includes executor, store, search, server, routing, hooks, session, adapters, plugins, security.
- 125 tests reported in BENCHMARK.md; actual repo has many more (server.test.ts alone is huge).

## Commercial Direction
- Open-source MCP plugin is the user acquisition surface.
- Commercial Insight/Platform product at context-mode.com is the monetization layer.
- ELv2 chosen explicitly to prevent competing closed-source SaaS repackaging.

## Sources
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\.git`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\README.md`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\CONTRIBUTING.md`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\BENCHMARK.md`
