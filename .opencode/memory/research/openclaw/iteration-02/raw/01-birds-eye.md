# OpenClaw — Bird's Eye Research (Researcher 1)

## Repository Identity

- **Name:** openclaw/openclaw
- **URL:** https://github.com/openclaw/openclaw
- **Homepage:** https://openclaw.ai
- **Docs:** https://docs.openclaw.ai
- **Owner:** openclaw (GitHub Organization, id: 252820863)
- **Created:** 2025-11-24T10:16:47Z
- **Last pushed:** 2026-06-22T05:02:26Z (active daily)
- **Default branch:** main
- **Primary language:** TypeScript
- **License:** MIT (README badge; API reports NOASSERTION/Other)
- **Description (API):** "Your own personal AI assistant. Any OS. Any Platform. The lobster way."
- **Description (package.json):** "Multi-channel AI gateway with extensible messaging integrations"
- **Topics:** ai, assistant, crustacean, molty, openclaw, own-your-data, personal
- **Discord:** https://discord.gg/clawd (server id: 1456350064065904867)

## Key Metrics (as of 2026-06-22)

| Metric | Value |
|--------|-------|
| Stars | 379,846 |
| Forks | 79,517 |
| Open issues | 6,468 |
| Subscribers (watchers) | 1,806 |
| Repo size | ~1.6 GB (1,605,732 KB) |
| Contributors (avatars in README) | ~645 |
| Top contributor | steipete (Peter Steinberger) — 32,416 contributions |
| Current version | 2026.6.9 |
| Has wiki | No |
| Has discussions | No |
| Has projects | No |
| Archived | No |

## What Is OpenClaw?

OpenClaw is a **personal AI assistant** you run on your own devices. It is a **gateway-centric** platform: the Gateway is the local control plane for sessions, channels, tools, and events. The product is the assistant that answers you on the channels you already use.

Key positioning:
- **Local-first**: runs on your own hardware, owns your data
- **Multi-channel inbox**: 23+ messaging platform integrations
- **Multi-agent routing**: route inbound channels/accounts/peers to isolated agents
- **Voice-capable**: Voice Wake + Talk Mode on macOS/iOS/Android
- **Visual**: Live Canvas with A2UI (agent-to-UI) rendering
- **Extensible**: massive plugin SDK + ClawHub skills registry

The project was built for "Molty", a space lobster AI assistant. It evolved through names: Warelay -> Clawdbot -> Moltybot -> OpenClaw. Created by Peter Steinberger (@steipete) and the community.

Sponsors: OpenAI, GitHub, NVIDIA, Vercel, Blacksmith, Convex.

## Key Components

### 1. Gateway (Control Plane)
- WebSocket-based local daemon (launchd/systemd user service)
- Default port: 18789
- Manages sessions, channels, tools, events
- CLI: `openclaw gateway status|start|stop|run`
- HTTP compatibility endpoints (OpenAI-compatible: `/v1/chat/completions`, `/v1/responses`)
- Gateway protocol in `packages/gateway-protocol/`
- Loopback-only by default (`gateway.bind="loopback"`)

### 2. Channels (23+ messaging surfaces)
**Supported channels:** WhatsApp, Telegram, Slack, Discord, Google Chat, Signal, iMessage, IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost, Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, Zalo Personal, WeChat, QQ, WebChat, macOS, iOS/Android.

Channel implementation lives in `src/channels/*`. Plugin authors get SDK seams. Channels are transport-only: they render portable presentation/actions, enforce transport limits, map native callback envelopes. They do NOT own product command trees or plugin/provider policy.

### 3. Plugins (57 bundled extensions)
Two plugin styles:
- **Code plugins**: run OpenClaw plugin code in-process, appropriate for deeper runtime extension (providers, channels, tools, hooks)
- **Bundle-style plugins**: package stable external surfaces such as skills, MCP servers, and configuration

**Bundled extensions (57):**
- **Channels:** discord, slack, whatsapp, feishu, googlechat, google-meet, line, matrix, msteams, nextcloud-talk, nostr, qqbot, synology-chat, tlon, twitch, zalo, zalouser
- **Model providers:** amazon-bedrock, amazon-bedrock-mantle, anthropic-vertex, arcee, cerebras, chutes, cloudflare-ai-gateway, codex, copilot, deepinfra, deepseek, gmi, gradium, groq, kimi-coding, llama-cpp, qianfan, qwen, stepfun, perplexity, parallel
- **Tools/capabilities:** brave, exa, firecrawl, inworld, kilocode, pixverse, voice-call, memory-lancedb, tokenjuice, lobster, openshell
- **Diagnostics:** diagnostics-otel, diagnostics-prometheus
- **QA:** qa-channel, qa-lab, qa-matrix
- **Other:** acpx, diffs, diffs-language-pack

### 4. Plugin SDK Surface (320 export submodules)
The `package.json` exports field exposes **320 unique submodules** under `./plugin-sdk/*`. This is a significantly larger surface than the "~120 submodules" initially estimated.

Major SDK categories:
- **Channel runtime**: channel-runtime, channel-contract, channel-envelope, channel-inbound, channel-outbound, channel-reply-pipeline, channel-streaming, channel-pairing, channel-policy, channel-targets, channel-lifecycle, channel-ingress, channel-logging, channel-mention-gating, channel-activity-runtime, channel-config-*
- **Agent runtime**: agent-runtime, agent-harness, agent-harness-runtime, agent-harness-task-runtime, agent-harness-exec-review-runtime, agent-media-payload, agent-config-primitives, agent-sessions
- **Approval workflows**: approval-auth-runtime, approval-client-runtime, approval-delivery-runtime, approval-gateway-runtime, approval-handler-runtime, approval-handler-adapter-runtime, approval-native-runtime, approval-reaction-runtime, approval-reply-runtime, approval-runtime
- **Config**: config-runtime, config-contracts, config-types, config-schema, config-mutation, plugin-config-runtime, bundled-channel-config-schema
- **Media**: media-runtime, media-store, media-mime, media-understanding, media-understanding-runtime, media-generation-runtime, image-generation, image-generation-runtime, image-generation-core, music-generation, music-generation-core, video-generation, video-generation-runtime, video-generation-core
- **Memory**: memory-core, memory-core-engine-runtime, memory-core-host-embedding-registry, memory-core-host-engine-embeddings, memory-core-host-engine-foundation, memory-core-host-engine-qmd, memory-core-host-engine-storage, memory-core-host-multimodal, memory-core-host-query, memory-core-host-secret, memory-core-host-events, memory-core-host-status, memory-core-host-runtime-cli, memory-core-host-runtime-core, memory-core-host-runtime-files, memory-host-core, memory-host-events, memory-host-files, memory-host-markdown, memory-host-search, memory-host-status
- **Security**: ssrf-policy, ssrf-runtime, ssrf-dispatcher, ssrf-runtime-internal, security-runtime, command-auth, command-auth-native, command-gating, dangerous-name-runtime
- **Sessions**: session-binding-runtime, session-key-runtime, session-store-runtime, session-transcript-runtime, session-transcript-hit, session-visibility, session-binding-runtime
- **Speech**: speech-core, tts-runtime, realtime-transcription, realtime-voice, realtime-bootstrap-context
- **Providers**: provider-auth, provider-auth-runtime, provider-auth-api-key, provider-auth-result, provider-auth-login, provider-oauth-runtime, models-provider-runtime, provider-setup, self-hosted-provider-setup
- **Infrastructure**: sqlite-runtime, runtime-store, json-store, persistent-dedupe, keyed-async-queue, file-lock, file-access-runtime, heartbeat-runtime, delivery-queue-runtime, dedupe-runtime, async-lock-runtime, concurrency-runtime, poll-runtime, pair-loop-guard-runtime
- **Gateway/CLI**: gateway-method-runtime, gateway-runtime, cli-runtime, cli-backend
- **ACP (Agent Communication Protocol)**: acp-runtime, acp-runtime-backend, acp-binding-runtime, acp-binding-resolve-runtime
- **Codex integration**: codex-mcp-projection, codex-native-task-runtime
- **Skills**: skills-runtime, skill-commands-runtime
- **Sandbox**: sandbox
- **Routing**: routing
- **Health/Doctor**: health, runtime-doctor, runtime-env, runtime-logger
- **And many more**: conversation-runtime, text-runtime, text-chunking, reply-runtime, reply-dedupe, reply-dispatch-runtime, reply-reference, reply-chunking, reply-payload, reply-history, thread-bindings-runtime, etc.

### 5. Agents
- Multi-agent routing with isolated workspaces (`~/.openclaw/workspace`)
- Per-agent sessions, per-agent SQLite DB (`agents/<agentId>/agent/openclaw-agent.sqlite`)
- Shared state DB (`state/openclaw.sqlite`) for global runtime state
- Agent prompt injection: `AGENTS.md`, `SOUL.md`, `TOOLS.md`
- Agent run terminal outcome normalization via `src/agents/agent-run-terminal-outcome.ts`
- Sandbox modes: off (default for main), non-main, all
- Sandbox backends: Docker (default), SSH, OpenShell

### 6. Skills & ClawHub
- Skills: `~/.openclaw/workspace/skills/<skill>/SKILL.md`
- Skills registry/marketplace: ClawHub (https://clawhub.ai)
- Bundled skills ship for baseline UX
- New skills should be published through ClawHub first, not added to core by default

### 7. Companion Apps (all optional)
- **Windows Hub**: native companion app (setup, tray status, chat, node mode, local MCP mode)
- **macOS (OpenClaw.app)**: menu bar control, Voice Wake, push-to-talk overlay, WebChat + debug tools, SSH remote gateway control
- **iOS node**: pairs as a node over Gateway WebSocket, Voice trigger forwarding + Canvas surface
- **Android node**: pairs as WS node via device pairing, Connect/Chat/Voice tabs + Canvas, Camera, Screen capture, Android device command families

### 8. MCP Support
- Both server and runtime integration surface
- Pragmatic MCP support without duplicating existing agent, tool, ACPX, plugin, or ClawHub paths

## Architecture Principles

From AGENTS.md:
- Core stays **plugin-agnostic**. No bundled ids/defaults/policy in core when manifest/registry/capability contracts work.
- Plugins cross into core only via `openclaw/plugin-sdk/*`, manifest metadata, injected runtime helpers, documented barrels (`api.ts`, `runtime-api.ts`).
- **Plugin prod code**: no core `src/**`, no `src/plugin-sdk-internal/**`, no other plugin `src/**`, no relative outside package.
- **Core/tests**: no deep plugin internals. Use public barrels, SDK facade, generic contracts.
- **Dependency ownership** follows runtime ownership: plugin-only deps stay plugin-local; root deps only for core imports or intentionally internationalized bundled plugin runtime.
- **Config/env surface bar is high**: before adding a config option or env var, first prove existing product behavior, provider selection, defaults, or doctor migration cannot resolve it.
- **Storage default: SQLite only**. No JSON/JSONL/TXT/sidecar files for OpenClaw-owned runtime state.
- **Compatibility is opt-in**: "Shipped" means reachable from a release Git tag; main/GitHub/PR/unreleased code is not shipped.

## Security Model

From SECURITY.md (35,289 bytes):
- **Local-first agent infrastructure for trusted operators** — NOT designed as a shared multi-tenant boundary
- **One-user trust model**: personal assistant (one trusted operator, potentially many agents)
- **Operator trust model**: authenticated Gateway callers = trusted operators for that Gateway
- **Shared-secret bearer auth** for HTTP compatibility endpoints (token/password)
- **Plugins are in-process** and treated as trusted code (same OS privileges as OpenClaw process)
- **DM pairing** default for untrusted senders on Telegram/WhatsApp/Signal/iMessage/Teams/Discord/Google Chat/Slack
- **Sandbox modes**: off (default for main session), non-main, all — Docker/SSH/OpenShell backends
- **Exec approvals**: operator guardrails to reduce accidental command execution
- **Temp folder boundary**: `/tmp/openclaw` (preferred), `os.tmpdir()/openclaw` (fallback)
- **SSRF protection**: operator-managed HTTP/WebSocket proxy-routing feature (fail-closed)
- **Node.js 22.19.0+ required** (CVE-2025-59466, CVE-2026-21636 patches)
- **Security scanning layers**: detect-private-key pre-commit hook, CodeQL, OpenGrep (Semgrep-compatible), E2E/live validation, package access checks

## Vision & Roadmap

From VISION.md (5,984 bytes):
- **Current priorities**: Security/safe defaults, bug fixes/stability, setup reliability/first-run UX
- **Next priorities**: Supporting all major model providers, improving messaging channels, performance/test infrastructure, computer-use/agent harness capabilities, ergonomics across CLI/web frontend, companion apps on all platforms
- **Not merging (for now)**: New core skills (use ClawHub), full-doc translation sets, commercial service integrations outside model-provider category, wrapper channels around already-supported channels, agent-hierarchy frameworks, heavy orchestration layers
- **Why TypeScript**: OpenClaw is primarily an orchestration system (prompts, tools, protocols, integrations). TypeScript keeps it hackable by default — widely known, fast to iterate, easy to read/modify/extend.

## Development & Release

- **Package manager**: pnpm (workspace monorepo)
- **Runtime**: Node 24 (recommended) or Node 22.19+
- **Build**: `pnpm build` produces `dist/`; `pnpm gateway:watch` for dev loop
- **Test**: Vitest (`pnpm test`), E2E (`pnpm test:e2e`), live (`pnpm test:live`), Docker (`pnpm test:docker:all`)
- **Typecheck**: tsgo (`pnpm tsgo`)
- **Formatting**: oxlint (not Prettier)
- **Release channels**: stable (tagged releases), beta (prerelease tags), dev (moving head of main)
- **Version format**: `YYYY.M.PATCH` (e.g., 2026.6.9); beta: `YYYY.M.PATCH-beta.N`
- **Release generation**: CHANGELOG.md derived from merged PRs + direct main commits

## Competitors / Alternatives

OpenClaw does not explicitly list competitors, but based on its positioning as a local-first, multi-channel AI assistant gateway:

| Competitor | Overlap | Key Difference |
|-----------|---------|----------------|
| **OpenAI ChatGPT** | AI assistant | Cloud-only, no local gateway, no multi-channel |
| **Claude/Anthropic** | AI assistant | Cloud-only, no local control plane |
| **LibreChat** | Multi-provider chat UI | Web UI focused, no messaging channel bridging |
| **TypingMind** | Multi-provider chat UI | Web UI, no local gateway or channel integration |
| **Jan.ai** | Local AI | Desktop app, no multi-channel or gateway architecture |
| **AnythingLLM** | Local AI + docs | Document-focused, no messaging channel bridging |
| **Botpress** | Chatbot platform | Cloud-first, enterprise-focused, no local-first model |
| **n8n** | Workflow automation | General automation, not AI-assistant focused |
| **Home Assistant** | Home automation | IoT/home, not AI assistant, but shares local-first philosophy |
| **Mattermost/Rocket.Chat** | Self-hosted messaging | Messaging platform, not AI assistant (could be OpenClaw channel) |

**Key differentiators of OpenClaw:**
1. Local-first, self-hosted, own-your-data
2. Multi-channel bridging (23+ real messaging platforms)
3. Gateway as control plane (not just a chat UI)
4. Massive plugin SDK (320 submodules)
5. Multi-agent routing with sandboxing
6. Voice + Canvas + mobile node support
7. MCP integration (both server and client)
8. Active community (379K+ stars, 645+ contributors)

## File Fetch Results

| File | Status | Size |
|------|--------|------|
| README.md | Fetched (raw) | ~36KB+ (truncated, full saved to tool output) |
| AGENTS.md | Fetched (GitHub API, base64) | 36,640 bytes |
| package.json | Fetched (raw) | ~95KB+ (truncated, full saved to tool output) |
| VISION.md | Fetched (GitHub API, base64) | 5,984 bytes |
| SECURITY.md | Fetched (GitHub API, base64) | 35,289 bytes |
| GitHub API (repo) | Fetched | Full JSON |
| GitHub API (contributors) | Partial (rate-limited on 100; got 1 with per_page=1) | steipete: 32,416 contributions |

## Notable Observations

1. **Explosive growth**: 379K stars in ~7 months (created Nov 2025) — one of the fastest-growing open-source AI projects
2. **Massive SDK surface**: 320 plugin-sdk export submodules far exceeds the ~120 initially estimated — this is an extremely granular, composable architecture
3. **57 bundled extensions** covering channels, model providers, tools, diagnostics, and QA
4. **Strong security governance**: detailed SECURITY.md with clear trust model, out-of-scope patterns, and multi-layer scanning
5. **Active development**: pushed today (2026-06-22), version 2026.6.9
6. **Organization-owned**: openclaw org (not individual), suggesting structured governance
7. **OpenAI Codex integration**: folded into `openai` namespace, no separate `openai-codex` provider/plugin
8. **SQLite-first storage**: strict policy against JSON/sidecar files for runtime state
9. **pnpm workspace monorepo**: extensions load from `extensions/*` during development
10. **Community-driven**: 645+ contributor avatars, AI/vibe-coded PRs explicitly welcomed
