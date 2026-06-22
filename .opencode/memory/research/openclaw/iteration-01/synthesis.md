# OpenClaw Deep Research — Final Synthesis

## Output contract
- **SUMMARY**: See below.
- **CHANGES**: No code changes were made to OpenClaw or TelegramHelper. This is a research-only deliverable.
- **EVIDENCE**: Raw files saved in `.opencode/memory/research/openclaw/iteration-01/raw/` (`01-birds-eye.md` through `05-practitioner.md`). Evidence sources include `webfetch` of GitHub repo page, README.md, AGENTS.md, VISION.md, CHANGELOG.md, SECURITY.md, CONTRIBUTING.md, package.json, pnpm-workspace.yaml; sparse local clone via `bash`; `read` of `docs/concepts/architecture.md`, `docs/gateway/configuration.md`, `docs/channels/telegram.md`, `docs/tools/skills.md`, `src/entry.ts`, `src/index.ts`, `src/gateway/server.ts`, `src/cli/run-main.ts`, `src/channels/registry.ts`, `src/security/dangerous-config-flags-core.ts`, `src/security/core-dangerous-config-flags.ts`; `grep` for security/execution patterns and channel policy terms; `glob` for directory/file counts; `git log`/`git tag` for history.
- **RISKS**: See RISKS section.
- **BLOCKERS**: No blockers. GitHub API returned 403 for programmatic repo queries, so metrics were read from the rendered GitHub page; tools `warpgrep_github_search`, `codegraph_*`, and `serena_*` were not available in this environment and were replaced with the available webfetch/glob/grep/read/bash set.

---

## SUMMARY
OpenClaw is a mature, personal, local-first AI assistant platform. It is a Node.js/TypeScript monorepo centered on a single long-lived WebSocket gateway that hosts agents, connects to 20+ messaging channels (Telegram, WhatsApp, Slack, Discord, etc.), serves a Control UI and canvas, and supports companion apps on macOS/iOS/Android/Windows. The architecture is plugin-centric: core stays lean, channels/providers/tools/skills are plugins, and the public surface is a ~120-submodule plugin SDK. The project is huge (~2.4M lines of TypeScript, 3.7k test files, 676 docs), fast-moving (423 PRs in the latest release), and security-aware but with a deliberately powerful default that runs tools on the host. The recommended usage path is `openclaw onboard` -> configure channels and allowlists -> run the daemon; power users add multi-agent routing, skills, cron, webhooks, MCP, and sandboxing.

## KEY_FINDINGS
1. **Gateway-centric control plane**: Everything (clients, nodes, automations, UI) connects over WebSocket to the gateway. Channels are transport-only plugins; the gateway owns sessions, routing, auth, and events.
2. **Plugin-centric growth**: The core exports a very granular plugin SDK. Channels, providers, tools, memory, and skills are all plugins. The project is moving more capabilities out of core into standalone npm packages (e.g., StepFun, official provider plugins).
3. **Security model is explicit and personal**: One trusted operator per gateway. It is not designed for multi-tenant adversarial use. Pairing/allowlists, sandboxing, and exec approvals are opt-in controls; the default main session runs on the host.
4. **Config is strict and JSON5**: `~/.openclaw/openclaw.json` is validated against a TypeBox schema. Unknown keys prevent the gateway from starting. Hot reload, `$include`, env substitution, and SecretRefs are supported.
5. **State is SQLite-first**: Shared state DB and per-agent DB. AGENTS.md forbids runtime JSON/JSONL state files and raw SQL; migrations are doctor-driven.
6. **Telegram is a first-class channel**: Bundled Telegram plugin with rich messages, streaming previews, forum-topic routing, group mention gating, ack reactions, polls, stickers, and exec approvals.
7. **Skills are markdown instructions**: SKILL.md with YAML frontmatter, gating metadata, and per-agent allowlists. The Skill Workshop provides a proposal/approval flow.
8. **Release train versioning**: `YYYY.M.PATCH` with many alpha/beta tags; 423 merged PRs in `v2026.6.9`.
9. **Mature engineering discipline**: PR limits, AI-assisted PR transparency, Codex review expectation, docs-first, YAML-only QA scenarios, OpenGrep security scanning.
10. **Large attack surface**: Native deps, mobile apps, browser control, media decoding, MCP, many providers, and in-process plugins create a broad security surface.

## ARCHITECTURE

### Textual scheme
```
--------------------------------------------------------------------------¬
¦                              OpenClaw Gateway                            ¦
¦  --------------¬  --------------¬  --------------¬  -----------------¬  ¦
¦  ¦  WS server  ¦  ¦ HTTP server ¦  ¦  Control UI ¦  ¦  Canvas / A2UI ¦  ¦
¦  ¦  127.0.0.1  ¦  ¦  same port  ¦  ¦  /__openclaw¦  ¦  /__openclaw/* ¦  ¦
¦  ¦  :18789     ¦  ¦             ¦  ¦             ¦  ¦                ¦  ¦
¦  L------T-------  L------T-------  L--------------  L-----------------  ¦
¦         ¦                 ¦                                              ¦
¦         L---------T--------                                              ¦
¦                   ¦                                                      ¦
¦     --------------+-------------¬                                       ¦
¦     ¦  Channel + Provider plugins ¦                                      ¦
¦     ¦  Telegram, WhatsApp, Slack, Discord, Signal, etc.                 ¦
¦     L------------------------------                                   ¦
¦                   ¦                                                      ¦
¦     --------------+-------------¬                                       ¦
¦     ¦  Agent runtime (src/agents) ¦                                      ¦
¦     ¦  - model selection/fallback ¦                                      ¦
¦     ¦  - tool execution / policy  ¦                                      ¦
¦     ¦  - subagent registry          ¦                                      ¦
¦     ¦  - compaction / context window  ¦                                      ¦
¦     ¦  - exec approvals / sandbox     ¦                                      ¦
¦     L------------------------------                                       ¦
¦                   ¦                                                      ¦
¦     --------------+-------------¬                                       ¦
¦     ¦  Plugin loader/registry (src/plugins) ¦                            ¦
¦     ¦  - manifest, SDK, ClawHub, install, hooks                       ¦
¦     L--------------------------------------                             ¦
¦                   ¦                                                      ¦
¦     --------------+-------------¬                                       ¦
¦     ¦  State (SQLite) + config   ¦                                       ¦
¦     ¦  ~/.openclaw/openclaw.json ¦                                       ¦
¦     ¦  state/openclaw.sqlite     ¦                                       ¦
¦     ¦  agents/<id>/agent/*.sqlite¦                                       ¦
¦     L------------------------------                                     ¦
L--------------------------------------------------------------------------
              ^
              ¦ WebSocket / HTTP
--------------+-------------¬
¦ Clients: CLI, macOS app,  ¦
¦ Web UI, automations,      ¦
¦ iOS/Android/Windows nodes ¦
L----------------------------
```

### Data flow
1. **Inbound**: A channel plugin (e.g., Telegram via grammY) receives a message, normalizes it to a shared channel envelope, applies allowlist/DM/group/mention gating, and routes it to the agent runtime.
2. **Agent turn**: The agent runtime builds the system prompt (skills + identity), calls the configured model provider, runs tool calls (with policy/approval gates), and produces a response stream.
3. **Outbound**: The response is chunked and formatted for the originating channel (HTML/markdown for Telegram, etc.) and delivered back. Streaming previews can be edited in place.
4. **State**: Sessions, transcripts, allowlists, and plugin state are persisted in SQLite; config is watched and reloaded.
5. **Control**: CLI and Control UI interact with the gateway via the typed WebSocket protocol; nodes declare capabilities and commands on connect.

## API_SURFACE

### CLI commands
Key command families from `src/cli/run-main.ts` and README:
- `openclaw onboard` — interactive setup wizard
- `openclaw gateway` — run/stop/status/call/logs
- `openclaw agent` — run a single agent turn
- `openclaw message send` — send a message to a channel target
- `openclaw message poll` — create a poll (Telegram-specific flags)
- `openclaw pairing` — list/approve device or sender pairing
- `openclaw config get|set|unset|apply|patch|validate|schema` — config management
- `openclaw skills install|update|verify|workshop` — skill management
- `openclaw doctor` — diagnostics and repair
- `openclaw security audit` — dangerous config audit
- `openclaw nodes` / `openclaw devices` — node/device pairing
- `openclaw browser` — browser control
- `openclaw cron` — cron job management
- `openclaw tools` — tool surface introspection

### Chat commands (available from any connected channel)
`/status`, `/new`, `/reset`, `/compact`, `/think <level>`, `/verbose on|off`, `/trace on|off`, `/usage`, `/restart`, `/activation mention|always`, `/name`.

### WebSocket gateway methods
From `docs/concepts/architecture.md`:
- `connect` — mandatory handshake with auth
- `health`, `status`, `send`, `agent`, `system-presence`
- `config.get`, `config.patch`, `config.apply`
- `sessions.list`, `sessions.history`, `sessions.send`, `sessions.spawn`
- Events: `agent`, `chat`, `presence`, `health`, `heartbeat`, `cron`, `tick`

### HTTP compatibility endpoints
- `POST /v1/chat/completions`
- `POST /v1/responses`
- `POST /tools/invoke`
These authenticate with the shared gateway secret and treat the caller as full operator.

### Key internal functions/classes (from source)
- `src/entry.ts` — process entry, argv normalization, fast paths
- `src/cli/run-main.ts` — `runCli` orchestrator
- `src/gateway/server.ts` — `startGatewayServer` lazy facade
- `src/channels/registry.ts` — channel registry helpers
- `src/plugins/loader.ts` — plugin loading
- `src/agents/embedded-agent-runner.ts` — model loop
- `src/agents/tool-policy.ts` / `src/agents/bash-tools/exec-approval*.ts` — policy/approval

### Plugin SDK exports
`package.json` lists ~120 exports under `openclaw/plugin-sdk/*`, including:
- `runtime`, `cli-runtime`, `gateway-runtime`, `agent-runtime`
- `channel-runtime`, `channel-config-*`, `outbound-*`, `reply-*`
- `sandbox`, `exec-approvals-runtime`, `security-runtime`
- `skills-runtime`, `memory-core`, `config-*`, `provider-*`

## RISKS
1. **Default host execution**: `sandbox.mode: off` means the main session can run host commands. A compromised model key, a malicious skill, or a prompt-injection chain that bypasses tool policy can execute arbitrary code.
2. **Shared-secret auth**: The gateway token/password grants full operator access. Leakage or exposure of the Control UI/HTTP endpoints is a full compromise.
3. **Trust model confusion**: The project is explicit that one gateway = one operator, but users may expose it publicly or share a host, collapsing the boundary.
4. **Large codebase and native surface**: ~2.4M TS lines, many native dependencies, mobile apps, browser control, media codecs, and MCP integrations create a broad attack surface.
5. **Plugin in-process trust**: Plugins run with gateway privileges. A malicious or compromised plugin is equivalent to a compromised gateway.
6. **Prompt injection not treated as a vulnerability**: The project triages prompt-injection-only reports as out of scope unless they cross a documented boundary. Operators must enforce those boundaries.
7. **Dangerous break-glass flags**: Multiple `dangerously*` flags exist for private networks, container namespace joining, device-auth disabling, etc. They are surfaced by audit but easy to enable accidentally.
8. **Config strictness can lock users out**: Invalid config prevents gateway startup; recovery requires `openclaw doctor --fix` or restoring last-known-good.
9. **Skills inject env into host**: `skills.entries.*.env` and `.apiKey` are injected into the host process for the agent turn, not the sandbox, which may surprise users.
10. **AI-generated PR velocity**: The project welcomes AI-assisted PRs but requires transparency and evidence; the high volume (20 PR cap per author) suggests rapid but controlled growth.

## USAGE_PATTERNS
1. **Personal assistant on a single machine**: Install globally, run `openclaw onboard`, connect Telegram, use DMs and groups.
2. **Headless server with remote clients**: Run gateway on a VPS, keep it on loopback, access via Tailscale/SSH tunnel and macOS/iOS/Android nodes.
3. **Multi-agent routing**: Define multiple agents with separate workspaces and route inbound messages by channel/account/group/topic.
4. **Automation**: Use cron jobs and webhooks to trigger agent turns on a schedule or from external services.
5. **Skill-driven workflows**: Install or author SKILL.md files to teach agents reusable patterns; use Skill Workshop to approve agent-drafted skills.
6. **Sandboxed coding**: Enable Docker sandboxing for non-main agents and run code-mode skills with exec approvals.
7. **Companion app setup**: Pair iOS/Android nodes over WebSocket for voice, camera, screen capture, and canvas.

## CONFIG_EXAMPLES

### Minimal model + channel
```json5
{
  agent: {
    model: "anthropic/claude-sonnet-4-6",
  },
  channels: {
    telegram: {
      enabled: true,
      botToken: "123:abc",
      dmPolicy: "pairing",
    },
  },
}
```

### Owner-only Telegram with explicit allowlists
```json5
{
  channels: {
    telegram: {
      enabled: true,
      botToken: "123:abc",
      dmPolicy: "allowlist",
      allowFrom: ["<your_user_id>"],
      groupPolicy: "allowlist",
      groups: {
        "-1001234567890": { requireMention: true },
      },
    },
  },
}
```

### Multi-agent routing
```json5
{
  agents: {
    list: [
      { id: "home", default: true, workspace: "~/.openclaw/workspace-home" },
      { id: "work", workspace: "~/.openclaw/workspace-work" },
    ],
  },
  bindings: [
    { agentId: "home", match: { channel: "telegram", accountId: "personal" } },
    { agentId: "work", match: { channel: "slack", accountId: "work" } },
  ],
}
```

### Sandbox default
```json5
{
  agents: {
    defaults: {
      sandbox: {
        mode: "non-main",
        scope: "agent",
      },
    },
  },
}
```

### Skills + cron
```json5
{
  agents: {
    defaults: { skills: ["github", "weather"] },
  },
  cron: {
    enabled: true,
    maxConcurrentRuns: 8,
  },
}
```

### Webhook
```json5
{
  hooks: {
    enabled: true,
    token: "hooks-only-secret",
    path: "/hooks",
    mappings: [
      { match: { path: "gmail" }, action: "agent", agentId: "main", deliver: true },
    ],
  },
}
```

## RECOMMENDATIONS_FOR_TELEGRAMHELPER
TelegramHelper is a Python aiogram/Telethon assistant. The most concrete ideas to borrow from OpenClaw:

1. **Adopt a pairing/allowlist security model**
   - Implement `dmPolicy` modes: `pairing`, `allowlist`, `open`, `disabled`.
   - Default to `pairing` so unknown Telegram senders get a one-time code and must be approved.
   - Add explicit `allowFrom` numeric user ID lists and `groupPolicy`/`groupAllowFrom` for groups.
   - Separate DM pairing approvals from group authorization (do not let DM pairing implicitly authorize group commands).

2. **Add group/topic mention gating**
   - `requireMention: true` in groups by default.
   - Support text mention patterns and native @bot mentions.
   - Per-group/per-topic config, including `includeGroupHistoryContext` controls.

3. **Richer Telegram delivery**
   - Render agent replies as Telegram HTML by default.
   - Support streaming preview edits (`editMessageText`) for partial answers.
   - Support progress drafts for tool execution.
   - Add `richMessages` opt-in for Bot API 10.1 blocks.
   - Handle stickers, polls, voice/video notes, and inline buttons.

4. **Config-as-code with strict validation**
   - Use a JSON5/JSON config file (e.g., `~/.telegramhelper/config.json`) with Pydantic schema validation.
   - Hot reload the file and refuse to apply invalid config.
   - Provide `telegramhelper doctor` and `telegramhelper config get/set` commands.
   - Support env substitution and SecretRef objects for tokens.

5. **SQLite-first state**
   - Move allowlists, sessions, and credentials from ad-hoc files into SQLite (or the existing SQLAlchemy models).
   - Use Alembic migrations for schema changes.
   - Avoid runtime JSON/JSONL sidecars.

6. **Skills system**
   - Add markdown `SKILL.md` files with YAML frontmatter (`name`, `description`, `metadata.openclaw`-style gating).
   - Restrict skills per agent via an allowlist.
   - Provide a `skills` command to install/verify from a registry.

7. **Exec approval flow**
   - For dangerous operations (shell, file writes, etc.), route approval prompts to a configured Telegram user ID.
   - Timeout approvals, log decisions, and allow per-tool/per-agent policy.

8. **Cron/webhooks**
   - Add a cron tool for scheduled agent turns.
   - Add HTTP webhook ingress so external services can trigger the assistant.

9. **Security audit command**
   - Add `telegramhelper security audit` that flags dangerous config (e.g., public bind, disabled auth, broad allowlists, dangerous flags).
   - Detect misconfigured DM policies.

10. **Command menu and chat commands**
    - Register native Telegram commands via `setMyCommands`.
    - Implement `/status`, `/new`, `/reset`, `/think`, `/verbose`, `/activation` chat commands.

11. **Plugin architecture**
    - Keep core generic; implement Telegram/WhatsApp/etc. as plugins with a small SDK contract.
    - This makes it easier to add new channels without touching core logic.

12. **Voice/canvas are NOT a priority for TelegramHelper**
    - OpenClaw invests heavily in voice, canvas, and mobile nodes. TelegramHelper can skip these unless the product roadmap calls for them.

## CONFIDENCE
**Medium-High** for architecture, usage patterns, security model, and Telegram channel behavior: we read the official docs, the README, and multiple source files.
**Medium** for exact internal API details and plugin SDK contracts: the codebase is ~2.4M lines, we only inspected a representative subset; `server.impl.ts` and many provider/channel plugin internals were not opened.
**Medium** for metrics: stars/forks/commits/PRs came from the rendered GitHub page because the GitHub API returned 403.

## GAPS
- Could not use the requested `warpgrep_github_search`, `codegraph_*`, or `serena_*` tools; they are not available in this environment. We replaced them with `webfetch`, `bash`, `glob`, `grep`, `read`.
- Sparse clone did not include `apps/`, `ui/`, full `packages/`, or every `extensions/` plugin. We focused on `src/`, `extensions/telegram`, `docs`, `security`, `scripts`, and metadata files.
- Did not read the full `src/gateway/server.impl.ts` or any compiled `dist/` output.
- Did not run tests or the build; all observations are static.
- Did not inspect the full `taxonomy.yaml` or `vitest.config.ts` beyond filenames.
- Did not review the iOS/Android/macOS/Windows app code or the Control UI source.
- Did not inspect the `security/opengrep/` rule definitions in detail.
- Did not enumerate every provider/model plugin; the surface is large and only a sample was read.
