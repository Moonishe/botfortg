# OpenClaw — Historian Perspective

## Tools used
- `webfetch` on `CHANGELOG.md`
- `bash` for `git log --oneline`, `git tag --sort=-creatordate`, and `git fetch --depth 50 --tags`
- `read` on `VISION.md` for origin story
- `grep` for version numbers in `CHANGELOG.md` and `package.json`

## Origin story
From `VISION.md`:
- OpenClaw started as a personal playground to learn AI and build something useful.
- It evolved through several names: Warelay -> Clawdbot -> Moltbot -> OpenClaw.
- The assistant persona is Molty, a space lobster.
- Primary author: Peter Steinberger (@steipete), with a large community of contributors.

## Versioning and release cadence
- **Version scheme**: `YYYY.M.PATCH` where `PATCH` is a sequential monthly release-train number, not the calendar day.
- **Latest stable**: `v2026.6.9` (2026-06-21).
- **Release channels**: `stable` (latest), `beta`, `dev`.
- `CHANGELOG.md` is release-only; contributors do not edit it.
- The release generation derives the changelog from merged PRs and direct main commits.

## Recent history (from git log --oneline -n 30)
- `eea777c9` chore(deadcode): trim stale facade re-exports
- `0b28a72b` fix(test): reject kova help value bypasses
- `adcba852` fix(test): reject cpuprofile limit help tokens
- `5b79fa13` chore(deadcode): trim doctor alias wrappers
- `124ea485` fix(test): reject docker timing flag limits
- `12756fc4` fix(test): reject env report flag paths
- `5bf459e2` fix(test): reject attestation platform flags
- `d64a27fe` chore(deadcode): drop node daemon runtime alias
- `6c42f736` fix(test): reject i18n report flag values
- `d460f00e` fix(test): reject metadata ref flags
- `b83dce7b` fix(test): reject rpc rtt flag values
- `d3c90719` fix(test): route qa otel smoke parser
- `b47c930e` chore(deadcode): trim runtime plugin selection wrappers
- `0befd3c8` fix(test): route android release wrappers
- `c2ee9b0b` fix(gateway): preserve owner MCP tools for agent RPC
- `9d275831` fix(test): route release signing args
- `04c8c50c` fix(test): route testbox env hydration
- `5abf4ce2` chore(deadcode): trim reply runtime dead helpers
- `07d5cdec` fix(test): route ios release wrappers
- `f5f23e73` fix(test): route proxy CA installer
- `0c183283` fix(test): route release preflight script
- `514b3365` fix(deadcode): move restart sentinels to sqlite
- `2804c24d` fix(test): route plugin dependency helpers
- `94c7b5a8` fix(test): route release ref resolver
- `93ec8b8c` fix(test): route install helper scripts
- `9d83eeac` fix(test): route release wrapper scripts
- `33eb6ab9` fix(test): route release approval script
- `757ab933` fix(test): route release script owners
- `63fdc57b` fix(test): route mac helper script owners
- `a39a3b74` fix(deadcode): move restart handoffs to sqlite

Recent commits are dominated by test/deadcode cleanup and moving more state into SQLite (restart sentinels, restart handoffs). This shows a maturation phase: reducing surface area and hardening persistence.

## Latest release `2026.6.9` highlights (from CHANGELOG.md)
- **Richer Telegram delivery**: HTML, markdown, sticker paths, progress drafts, command output.
- **Agent recovery**: retries, terminal outcomes, usage after compaction, session history repair, reply reconciliation.
- **Codex integration**: automatic plugin approvals, GPT-5.3 Spark OAuth routing, remote-node exec as dynamic tool, app-server teardown.
- **Standalone official provider plugins**: external provider packages as first-class npm releases, external channel plugins load at startup, StepFun provider.
- **Web/native clients**: Control UI session workspace rail, plugin health, iOS Watch controls, Android chat context.
- **Search and skills**: Codex Hosted Search, key-free search providers opt-in, ClawHub skill provenance.
- **Security/privacy**: redact secrets from debug/config output, block internal HTTP session overrides, audit open-DM tool exposure, plugin write ownership checks.
- **Storage/migrations**: avoid SQLite WAL on network filesystems, clean reindex artifacts, setup state out of workspace dot-dirs, import default-agent auth profiles into SQLite.
- **Channels**: Telegram rich delivery, WhatsApp media failure handling, Mattermost thread replies, Discord action handling, Feishu improvements.

The `2026.6.9` release covered the complete `v2026.6.8..HEAD` history: **423 merged PRs**.

## Tag history trends
- Early tags: `v0.1.0` through `v2.0.0-beta*` (classic semver).
- Switched to calendar-train versioning starting `v2026.1.*`.
- From 2026.1 through 2026.6, there are many alpha/beta tags per release train, showing rapid iteration.
- The latest few tags are `v2026.6.9`, `v2026.6.8`, `v2026.6.7-alpha.*`, `v2026.6.6`, etc.

## Growth and scale indicators
- 61,228 commits (per GitHub page).
- 8934 TypeScript files in `src/`, ~2.4M lines, 3713 test files.
- 676 Markdown docs.
- 350 TypeScript files in `extensions/`.
- 213 releases on GitHub.
- 380k stars, 79.5k forks, 3.3k issues, 3.2k PRs.

## Evolution themes
1. **From monolith to plugin economy**: External provider and channel plugins now publish as independent npm packages; the gateway discovers installed channel plugins at startup.
2. **State consolidation**: Recent commits move restart sentinels and handoffs from files into SQLite.
3. **Security hardening**: DM policy boundaries, group allowlist separation, dangerous-config audit, secret redaction.
4. **Mobile/companion maturity**: iOS Watch, Android chat context, Windows Hub, macOS app.
5. **AI backend diversification**: Codex, Gemini, Claude, OpenAI, local providers, custom providers, model failover.
6. **Docs and QA discipline**: YAML-only QA scenarios, release-generation owned changelog, PR limits, review bots.

## Maintainer culture
From `CONTRIBUTING.md`:
- One PR = one issue/topic.
- PRs over ~5k changed lines reviewed only in exceptional circumstances.
- Hard limit: 20 open PRs per author.
- AI/vibe-coded PRs are welcome but must be marked and include evidence.
- Refactor-only PRs are generally not accepted.
- Codex review is treated as the highest standard of AI review.

## Bottom line
OpenClaw is a fast-moving, high-volume project with a mature release train. The recent commit history shows the project is in a consolidation phase: dead code removal, SQLite-first state, and test hardening. The changelog is dense and indicates a team that ships frequently and across many surfaces simultaneously.
