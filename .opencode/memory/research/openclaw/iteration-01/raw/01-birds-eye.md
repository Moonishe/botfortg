# OpenClaw — Bird's Eye Perspective

## Tools used
- `webfetch` on github.com/openclaw/openclaw (repo page + README.md + AGENTS.md + VISION.md + package.json + pnpm-workspace.yaml + CHANGELOG.md + SECURITY.md + CONTRIBUTING.md)
- `bash` for local sparse clone and high-level statistics
- `glob` for directory structure and file counts
- `read` for directory listings (docs, src, extensions, security)

## Repository identity
- **Repo**: https://github.com/openclaw/openclaw
- **Description**: "Your own personal AI assistant. Any OS. Any Platform. The lobster way."
- **License**: MIT
- **Latest release**: `v2026.6.9` (as of 2026-06-22)
- **Version scheme**: `YYYY.M.PATCH` (monthly release train), with `beta.N` and `alpha.N` prerelease tags

## Public metrics (from GitHub page)
- Stars: ~380k
- Forks: ~79.5k
- Commits: 61,228
- Open issues: ~3.3k
- Open PRs: ~3.2k
- Watchers: ~1.8k
- Latest tag: `v2026.6.9` (2026-06-21)
- Previous tag: `v2026.6.8`

## Stack and workspace
- **Runtime**: Node.js 22.19+ (Node 24 recommended)
- **Package manager**: pnpm workspace
- **Language**: TypeScript 91.4%, Swift 3.4%, JavaScript 2.9%, Kotlin 1.0%, Shell 0.7%, CSS 0.4%
- **Module system**: ESM, type: "module"
- **CLI entry**: `openclaw.mjs` -> `dist/index.js` / `src/entry.ts`
- **Build output**: `dist/`
- **Test runner**: Vitest
- **Formatter**: oxfmt
- **Linter**: oxlint
- **Lockfiles**: `pnpm-lock.yaml` + `npm-shrinkwrap.json`

## Workspace layout (pnpm-workspace.yaml)
```yaml
packages:
  - .
  - ui
  - packages/*
  - extensions/*
```

## Top-level directories (from sparse clone)
- `src/` — 8934 .ts files, ~2.4M lines of TypeScript; 3713 test files
- `extensions/` — 350 .ts files (channel/provider plugins, e.g., telegram, discord, whatsapp)
- `packages/` — shared packages (gateway-protocol, terminal-core, etc.)
- `docs/` — 676 Markdown files, extensive user/dev docs
- `ui/` — Control UI web frontend (Lit-based)
- `apps/` — companion apps (iOS, Android, macOS, Windows)
- `scripts/` — build, release, sandbox, and helper scripts
- `security/` — OpenGrep rules, security docs, README
- `skills/` — bundled skills
- `qa/` — YAML scenario-based QA/e2e test definitions
- `test/` — test helpers
- `config/`, `deploy/`, `patches/` — packaging/deployment

## Product positioning
OpenClaw is a personal, local-first AI assistant gateway. It connects to many messaging channels, runs agents on the operator's own hardware, supports voice, canvas, multi-agent routing, and skill/plugin extensions. The core design is "gateway as control plane" — the assistant is the product, the gateway is the plumbing.

## Supported channels (from README)
WhatsApp, Telegram, Slack, Discord, Google Chat, Signal, iMessage, IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost, Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, Zalo Personal, WeChat, QQ, WebChat, plus macOS/iOS/Android nodes and Windows Hub.

## Key subsystems visible at a glance
- `src/agents/` — agent runtime, model selection, tools, subagents, compaction, exec approval
- `src/gateway/` — WebSocket gateway, HTTP server, control UI, pairing, channels, cron
- `src/channels/` — channel abstraction, allowlists, DM/group policies, routing
- `src/plugins/` — plugin loading, registry, SDK, ClawHub, install/update
- `src/tools/` — tool registration, filesystem, browser, media, etc.
- `src/cli/` — Commander-based CLI, fast paths, onboarding, config
- `src/sessions/` — session storage, transcript, compaction, lifecycle
- `src/memory/` — memory/search backends (QMD, Honcho, built-in, etc.)
- `src/security/` — dangerous-config audit, install policy, sandbox gates
- `src/tui/` — terminal UI / local shell

## Evidence references
- README.md: install, quick-start, channels, security defaults
- VISION.md: "AI that actually does things", security-first, plugin-driven, TypeScript because hackable
- AGENTS.md: strict architectural rules, plugin boundaries, SQLite-only state, no raw SQL, no runtime shims
- package.json: exports list shows ~120 plugin-sdk submodules
- pnpm-workspace.yaml: hoisted nodeLinker, many overrides, allowBuilds, patchedDependencies
- Local clone stats: 8934 src .ts files, 2.4M lines, 3713 test files

## Initial impressions
- Very large, mature, fast-moving codebase with strong engineering discipline
- Heavily documented; docs-first culture
- Plugin-centric architecture: core stays lean, channels/providers are plugins
- Security-aware: explicit trust model, dangerous flags, sandboxing, pairing/allowlists
- Not a simple library: it is a full-stack runtime, CLI, gateway, and mobile companion ecosystem
