# OpenClaw Deep Dive - Technical Architecture (Researcher 2, Iteration 02)

> Repository: https://github.com/openclaw/openclaw
> Branch: main
> Date: 2026-06-22
> Sources: raw source files (server.ts, entry.ts, run-main.ts, registry.ts, bundled-capability-runtime.ts),
>          official docs (architecture, agent-loop, session, queue, configuration, multi-agent),
>          GitHub directory listings (src/channels, src/plugins, src/channels/plugins)

---

## 1. Gateway-Centric Architecture

### 1.1 Core model

OpenClaw is built around a single long-lived **Gateway daemon** that owns ALL messaging surfaces
and serves as the sole control plane. Key invariants:

- **One Gateway per host** — it is the only process that opens a WhatsApp (Baileys) session.
- **WebSocket-first** — control-plane clients (macOS app, CLI, web UI, automations) connect over
  WebSocket on the configured bind host (default `127.0.0.1:18789`).
- **HTTP co-located** — the same port serves the Control UI and Canvas host under
  `/__openclaw__/canvas/` (agent-editable HTML/CSS/JS) and `/__openclaw__/a2ui/` (A2UI host).
- **Nodes** (macOS/iOS/Android/headless) connect to the SAME WS server but declare `role: node`
  with explicit caps/commands.

### 1.2 Lazy server entrypoint (`src/gateway/server.ts`)

The public `server.ts` is a **lazy facade** — it re-exports types and dynamically imports
`server.impl.ts` only when `startGatewayServer()` is called. This lets lightweight callers
import server types/helpers without paying the full startup dependency graph.

Key exports:
- `startGatewayServer(...args)` — lazily loads `server.impl.ts` then delegates.
- `resetModelCatalogCacheForTest()` — clears the model-catalog cache between tests.
- `truncateCloseReason` — helper re-exported from `./server/close-reason.js`.
- `emitStartupTrace(name, durationMs, totalMs)` — gated by `OPENCLAW_GATEWAY_STARTUP_TRACE` env.

This reveals a deliberate **startup-performance optimization pattern**: the gateway server
implementation is behind a dynamic import boundary, with optional startup tracing instrumentation.

### 1.3 Wire protocol

Transport: WebSocket, text frames with JSON payloads.
- First frame MUST be `connect` (handshake is mandatory; non-JSON/non-connect first frame = hard close).
- After handshake:
  - Requests: `{type:"req", id, method, params}` -> `{type:"res", id, ok, payload|error}`
  - Events: `{type:"event", event, payload, seq?, stateVersion?}`
- `hello-ok.features.methods` / `events` are discovery metadata.
- Auth: shared-secret via `connect.params.auth.token` or `connect.params.auth.password`;
  Tailscale Serve / trusted-proxy modes satisfy auth from request headers.
- Idempotency keys required for side-effecting methods (`send`, `agent`); server keeps a
  short-lived dedupe cache.
- Protocol typed via **TypeBox schemas** -> JSON Schema generated -> Swift models generated.

### 1.4 Connection lifecycle + pairing

- All WS clients include a **device identity** on `connect`; new device IDs require pairing approval.
- Gateway issues a **device token** for subsequent connects.
- Local loopback connects can be auto-approved; non-local require explicit approval.
- All connects must sign the `connect.challenge` nonce. Signature payload `v3` binds
  `platform` + `deviceFamily`; metadata changes require re-pairing.

### 1.5 Events

Gateway emits: `agent`, `chat`, `presence`, `health`, `heartbeat`, `cron`, `tick`, `shutdown`.
Events are NOT replayed; clients must refresh on gaps.

---

## 2. Channel Plugin System

### 2.1 Supported channels

WhatsApp (Baileys), Telegram (grammY), Slack, Discord, Signal, iMessage, Google Chat,
Microsoft Teams, Matrix, Feishu, LINE, Mattermost, Nextcloud Talk, Nostr, Synology Chat,
Tlon, Twitch, Zalo, Zalo Personal, WeChat, QQ, IRC, WebChat = 23+ channels.

### 2.2 Channel registry (`src/channels/registry.ts`)

A lightweight **facade** for channel ids, metadata, and setup copy — does NOT load channel
plugin implementations (keeps it cheap). Key functions:

- `normalizeChannelId(raw)` — normalizes built-in chat channel ids (no plugin impl load).
- `normalizeAnyChannelId(raw)` — normalizes any registered channel plugin id/alias (post-registry-init).
- `listRegisteredChannelPluginIds()` — lists registered channel plugin ids without importing runtime impls.
- `getRegisteredChannelPluginMeta(id)` — returns lightweight metadata (`aliases`, `markdownCapable`).
- `formatChannelPrimerLine(meta)` / `formatChannelSelectionLine(meta, docsLink)` — setup/status UI helpers.

Delegates to `registry-lookup.ts` (`findRegisteredChannelPluginEntry`, `findRegisteredChannelPluginEntryById`,
`listRegisteredChannelPluginEntries`). Re-exports `getChatChannelMeta`, `CHAT_CHANNEL_ORDER`,
`normalizeChatChannelId` from `chat-meta.ts` / `ids.ts`.

### 2.3 Channel plugin directory (`src/channels/plugins/`)

Subdirectories reveal the contract surface:
- `contracts/` — channel plugin contract definitions.
- `actions/` — channel-specific actions.
- `outbound/` — outbound message delivery.
- `status-issues/` — status reporting.

Key files in `src/channels/plugins/`:
- `bundled.ts` / `bundled-root.ts` — bundled channel plugin loading.
- `catalog.ts` — channel catalog.
- `module-loader.ts` — dynamic module loading for channel plugins.
- `binding-registry.ts` / `binding-routing.ts` / `binding-targets.ts` — binding system.
- `configured-binding-registry.ts` / `configured-binding-compiler.ts` / `configured-binding-match.ts` —
  configured (declarative) bindings compilation and matching.
- `pairing.ts` / `pairing-adapters.ts` — device/sender pairing.
- `dm-access.ts` — DM access policy.
- `gateway-auth-bypass.ts` — gateway auth bypass logic.
- `message-tool-api.ts` — message tool API for agent-driven sends.
- `message-capabilities.ts` / `message-capability-matrix.test.ts` — per-channel capability matrix.
- `lifecycle-startup.ts` — channel lifecycle startup.
- `persisted-auth-state.ts` — persisted auth state.
- `approvals.ts` — approval gates.
- `exec-approval-local.ts` — local exec approvals.

### 2.4 DM policy model

Per-channel `dmPolicy`:
- `"pairing"` (default) — unknown senders get a one-time pairing code.
- `"allowlist"` — only senders in `allowFrom` (or paired allow store).
- `"open"` — allow all (requires `allowFrom: ["*"]`).
- `"disabled"` — ignore all DMs.

Group policy: `groupPolicy` + `groupAllowFrom`, mention gating via `mentionPatterns`.

### 2.5 Multi-account support

Channels support multiple accounts via `accountId` (e.g. WhatsApp `personal` vs `biz`).
Each account can be routed to a different agent via bindings.

---

## 3. Agent Runtime

### 3.1 Agent loop (`runEmbeddedAgent`)

Entry points: Gateway RPC `agent` / `agent.wait`, CLI `agent` command.

Flow:
1. `agent` RPC validates params, resolves session (sessionKey/sessionId), persists session
   metadata, returns `{runId, acceptedAt}` immediately.
2. `agentCommand` runs the agent:
   - resolves model + thinking/verbose/trace defaults
   - loads skills snapshot
   - calls `runEmbeddedAgent` (OpenClaw agent runtime)
   - emits lifecycle end/error if embedded loop doesn't
3. `runEmbeddedAgent`:
   - serializes runs via per-session + global queues (lane-aware FIFO)
   - resolves model + auth profile, builds the OpenClaw session
   - subscribes to runtime events, streams assistant/tool deltas
   - enforces timeout -> aborts run if exceeded
   - for Codex app-server turns: aborts accepted turn that stops producing progress
4. `subscribeEmbeddedAgentSession` bridges runtime events to `agent` stream:
   - tool events => `stream: "tool"`
   - assistant deltas => `stream: "assistant"`
   - lifecycle events => `stream: "lifecycle"` (`phase: "start"|"end"|"error"`)
5. `agent.wait` uses `waitForAgentRun` — waits for lifecycle end/error for `runId`.

### 3.2 Model selection

- Model refs use `provider/model` format (e.g. `anthropic/claude-sonnet-4-6`).
- `agents.defaults.model.primary` + `agents.defaults.model.fallbacks[]` for failover.
- `agents.defaults.models` defines the model catalog AND acts as allowlist for `/model`.
- Auth profiles are **per-agent** at `~/.openclaw/agents/<agentId>/agent/auth-profiles.json`.
- Plugin hook `before_model_resolve` can deterministically override provider/model pre-session.

### 3.3 Plugin hooks (agent + gateway lifecycle)

- `before_model_resolve` — override provider/model before resolution (no messages).
- `before_prompt_build` — inject `prependContext`, `systemPrompt`, `prependSystemContext`,
  `appendSystemContext` after session load (with messages).
- `before_agent_start` — legacy compatibility hook.
- `before_agent_reply` — claim turn, return synthetic reply or silence.
- `agent_end` — inspect final message list + run metadata.
- `before_compaction` / `after_compaction` — observe/annotate compaction.
- `before_tool_call` / `after_tool_call` — intercept tool params/results. `{block: true}` is terminal.
- `tool_result_persist` — synchronously transform tool results before transcript write.
- `message_received` / `message_sending` / `message_sent` — inbound + outbound message hooks.
  `{cancel: true}` on `message_sending` is terminal.
- `session_start` / `session_end` — session lifecycle.
- `gateway_start` / `gateway_stop` — gateway lifecycle.
- `before_install` — inspect staged skill/plugin install material.

Internal hooks (Gateway hooks): `agent:bootstrap` (bootstrap files before system prompt),
command hooks (`/new`, `/reset`, `/stop`, etc.).

### 3.4 Tool execution

- Tool start/update/end events emitted on the `tool` stream.
- Tool results sanitized for size and image payloads before logging/emitting.
- Messaging tool sends tracked to suppress duplicate assistant confirmations.
- Tools: browser, canvas, nodes, cron, sessions, Discord/Slack actions, exec, read, write, edit,
  apply_patch, sessions_list, sessions_history, sessions_send, sessions_spawn.
- Tool policy: `tools.allow` / `tools.deny` per agent; `tools.elevated` is global + sender-based.

### 3.5 Queueing + concurrency

- Lane-aware FIFO queue: per-session lane (`session:<key>`) guarantees one active run per session;
  global lane (`main` default, cap via `agents.defaults.maxConcurrent`) caps overall parallelism.
- Additional lanes: `cron`, `cron-nested`, `nested`, `subagent` for background parallelism.
- Queue modes: `steer` (default — inject into active runtime), `followup`, `collect`, `interrupt`.
- Defaults: `debounceMs: 500`, `cap: 20`, `drop: "summarize"`.
- Typing indicators fire immediately on enqueue.
- No external dependencies; pure TypeScript + promises.

### 3.6 Timeouts

- `agent.wait` default: 30s (wait-only).
- Agent runtime: `agents.defaults.timeoutSeconds` default 172800s (48h).
- Model idle timeout: aborts when no response chunks arrive; capped at 120s by default.
- Provider HTTP timeout: `models.providers.<id>.timeoutSeconds`.

---

## 4. Plugin Loader / Registry

### 4.1 Plugin system overview (`src/plugins/`)

Subdirectories:
- `contracts/` — plugin contract definitions.
- `runtime/` — plugin runtime support.
- `compat/` — compatibility layers.
- `test-helpers/` — test utilities.

### 4.2 Bundled capability runtime loader (`src/plugins/bundled-capability-runtime.ts`)

`loadBundledCapabilityRuntimeRegistry()` is the core loader for bundled capability providers:

1. **Discovery**: `discoverOpenClawPlugins({env})` finds plugin candidates.
2. **Manifest registry**: `loadPluginManifestRegistry()` loads manifests, filters by `origin: "bundled"`
   and requested `pluginIds`.
3. **Module loading**: for each candidate:
   - Resolves safe source path via `openRootFileSync()` (boundary file read, rejects path escapes).
   - Gets a cached module loader (`getCachedPluginModuleLoader`) with SDK alias map
     (`buildPluginLoaderAliasMap`), native-load preference, and Vitest shim support.
   - Loads the module and resolves export via `resolvePluginModuleExport()` — accepts either a
     function (register) or an object with `register`/`activate`.
4. **Registration capture**: `createCapturedPluginRegistration()` creates a capture API; the plugin's
   `register(api)` is called, and all registered capabilities are captured:
   - `cliBackends`, `providers`, `embeddingProviders`, `speechProviders`,
     `realtimeTranscriptionProviders`, `realtimeVoiceProviders`, `mediaUnderstandingProviders`,
     `transcriptSourceProviders`, `imageGenerationProviders`, `videoGenerationProviders`,
     `musicGenerationProviders`, `webFetchProviders`, `webSearchProviders`, `migrationProviders`,
     `memoryEmbeddingProviders`, `agentHarnesses`, `tools`, `textTransforms`.
5. **Registry population**: captured capabilities are pushed into the `PluginRegistry` with
   `pluginId`, `pluginName`, `source`, `rootDir` metadata.
6. **Tool contract validation**: `findUndeclaredPluginToolNames()` enforces that plugins declare
   `contracts.tools` for every tool name; undeclared tools produce diagnostics errors.

### 4.3 PluginRecord shape

Each plugin record tracks:
- `id`, `name`, `version`, `description`, `source`, `rootDir`, `origin` ("bundled"),
  `workspaceDir`, `enabled`, `status` ("loaded"|"error"|"disabled"), `error`.
- Capability ID arrays: `toolNames`, `hookNames`, `channelIds`, `cliBackendIds`, `providerIds`,
  `embeddingProviderIds`, `speechProviderIds`, etc.
- `agentHarnessIds`, `cliCommands`, `services`, `gatewayDiscoveryServiceIds`, `commands`,
  `httpRoutes`, `hookCount`, `configSchema`.

### 4.4 CLI plugin command registration (`src/cli/run-main.ts`)

`registerPluginCliCommandsFromValidatedConfig(program, undefined, undefined, {mode: "lazy", primary})`
registers plugin-contributed CLI commands lazily. The CLI resolves plugin command ownership via:
- `resolveManifestCliCommandSurfaceOwner` — manifest-based command alias owner resolution.
- `resolvePluginCliRootOwnerIds` — plugin CLI root owner resolution.
- `resolveMissingPluginCommandMessage` — generates "Unknown command" errors with suggestions.

### 4.5 Plugin sources

- **Bundled**: `extensions/*` in the repo (loaded during dev via pnpm workspace).
- **Workspace**: `~/.openclaw/workspace/skills/<skill>/`.
- **ClawHub**: external skill registry at clawhub.ai.
- **Managed**: managed skills/plugins.

---

## 5. State Management

### 5.1 File-first, NOT SQLite-first

**Important correction to the "SQLite-first" assumption**: OpenClaw uses a **file-based** state
model, not SQLite. All session state is owned by the gateway and stored as files.

### 5.2 Session state

- **Store**: `~/.openclaw/agents/<agentId>/sessions/sessions.json` (JSON, not a database).
- **Transcripts**: `~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl` (JSONL append-only).
- `sessions.json` tracks lifecycle timestamps:
  - `sessionStartedAt` — when current sessionId began (daily reset uses this).
  - `lastInteractionAt` — last user/channel interaction (idle lifetime).
  - `updatedAt` — last store-row mutation (listing/pruning, not authoritative for freshness).

### 5.3 Session write lock

- File-based, process-aware lock on the session transcript file.
- Transcript writers wait up to `session.writeLock.acquireTimeoutMs` (default 60000ms).
- Non-reentrant by default; `allowReentrant: true` opt-in for nested acquisition.

### 5.4 Config state

- **Config file**: `~/.openclaw/openclaw.json` (JSON5, supports comments + trailing commas).
- **Strict validation**: unknown keys/malformed types cause Gateway to refuse to start.
- **Hot reload**: file watcher with debounce (default 300ms); modes: `hybrid` (default), `hot`,
  `restart`, `off`.
- **Last-known-good**: trusted copy kept after each successful startup; `openclaw doctor --fix`
  restores it.
- **Config RPC**: `config.get`, `config.patch` (JSON merge patch), `config.apply`,
  `config.schema.lookup`, `update.run`, `update.status`. Rate-limited to 3 req/60s per deviceId+clientIp.
- **$include**: multi-file config with deep-merge, up to 10 levels, confinement to config dir.
- **Secret refs**: `source: "env"|"file"|"exec"` with providers.

### 5.5 Per-agent state isolation

- `agentDir`: `~/.openclaw/agents/<agentId>/agent` (auth profiles, model registry, per-agent config).
- Sessions: `~/.openclaw/agents/<agentId>/sessions`.
- Workspace: `~/.openclaw/workspace` (or `~/.openclaw/workspace-<agentId>`).
- Auth profiles: `~/.openclaw/agents/<agentId>/agent/auth-profiles.json` (per-agent, never reuse
  agentDir across agents).

### 5.6 Session routing

| Source | Behavior |
|--------|----------|
| Direct messages | Shared session by default (`main` scope) |
| Group chats | Isolated per group |
| Rooms/channels | Isolated per room |
| Cron jobs | Fresh session per run |
| Webhooks | Isolated per hook |

DM scope options: `main` (default), `per-peer`, `per-channel-peer` (recommended for multi-user),
`per-account-channel-peer`.

### 5.7 Session lifecycle

- **Daily reset** (default): new session at 4:00 AM local.
- **Idle reset** (optional): `session.reset.idleMinutes`.
- **Manual reset**: `/new` or `/reset` in chat.
- Session maintenance: `enforce` (default) or `warn` mode; `pruneAfter: "30d"`, `maxEntries: 500`.

---

## 6. CLI Entry Orchestration

### 6.1 Entry point (`src/entry.ts`)

- Guards against double-execution when bundled (`isMainModule` check with wrapper-entry pairs).
- Compile cache management (`enableOpenClawCompileCache`, respawn logic).
- Argv normalization (Windows argv, profile args, container args).
- Help/version fast paths before loading full CLI.
- Delegates to `runMainOrRootHelp(argv)` -> `runCli(argv)`.

### 6.2 Main CLI orchestration (`src/cli/run-main.ts`)

`runCli(argv)` is the main orchestrator:
1. Argv parsing (container, profile, Windows normalization).
2. Runtime guard (`assertSupportedRuntime`).
3. Dotenv loading (gateway-run vs remote-agent-dispatch paths).
4. Gateway run environment selection (`selectGatewayRunEnvironment`).
5. Proxy lifecycle management (start/stop/replace proxy with signal handlers).
6. Help fast paths (root, browser, secrets, nodes, setup/onboard/configure, precomputed subcommand).
7. Unowned command root detection (rejects typos before help routing).
8. Crestodian / onboarding for bare root and modern onboard.
9. Gateway run fast path (`tryRunGatewayRunFastPath`).
10. CLI routing (`tryRouteCli`).
11. Full program build (`buildProgram`), plugin command registration, Commander parse.
12. Cleanup: gateway run runtime hooks, proxy stop, agent harness disposal, memory manager close.

Key design: extensive use of **fast paths** to avoid loading the full CLI for common operations
(version, help, gateway run). Lazy imports everywhere. Startup tracing via
`OPENCLAW_GATEWAY_STARTUP_TRACE`.

---

## 7. Multi-Agent Routing

- `agents.list[]` defines isolated agents, each with own workspace, agentDir, session store.
- `bindings[]` maps `(channel, accountId, peer)` to `agentId` — deterministic, most-specific wins.
- Match tiers: peer > parentPeer > guildId+roles > guildId > teamId > accountId > channel > default.
- AND semantics: multiple match fields all required.
- Per-agent sandbox (`mode: "off"|"non-main"|"all"`, `scope: "session"|"agent"|"shared"`).
- Per-agent tool allow/deny lists.
- Cross-agent QMD memory search via `memorySearch.qmd.extraCollections`.

---

## 8. Key Architectural Patterns

1. **Lazy loading everywhere** — dynamic imports behind facades (server.ts, run-main.ts fast paths).
2. **TypeBox -> JSON Schema -> Swift codegen** for protocol typing.
3. **Lane-aware FIFO queue** for concurrency (pure TS, no external deps).
4. **Plugin capture registration** — plugins call `register(api)`, capabilities captured into registry.
5. **File-based state** with file locks, hot-reload, last-known-good config.
6. **Boundary file reads** (`openRootFileSync`) — rejects path escapes, security hardening.
7. **Startup tracing** — optional per-phase timing via env var.
8. **Strict config validation** — Gateway refuses to start on unknown keys.
9. **Idempotency keys** for side-effecting RPC methods with dedupe cache.
10. **Device-based pairing** with challenge nonce signing and metadata pinning.

---

## 9. Source Files Successfully Retrieved

| File | Source | Status |
|------|--------|--------|
| `docs/concepts/architecture.md` | raw.githubusercontent.com | OK (via docs site) |
| `src/gateway/server.ts` | raw.githubusercontent.com | OK |
| `src/gateway/server.impl.ts` | raw.githubusercontent.com | FAILED (transport error) |
| `src/channels/registry.ts` | cdn.jsdelivr.net | OK (raw failed, CDN worked) |
| `src/entry.ts` | raw.githubusercontent.com | OK |
| `src/cli/run-main.ts` | raw.githubusercontent.com | OK (via docs site) |
| `src/plugins/bundled-capability-runtime.ts` | cdn.jsdelivr.net | OK |
| `src/channels/plugins/contracts/channel-plugin.ts` | cdn.jsdelivr.net | FAILED (403) |

Note: `server.impl.ts` (the actual gateway server implementation) could not be retrieved due to
transport errors. The lazy facade `server.ts` confirms it exists and exports `startGatewayServer`
and `resetModelCatalogCacheForTest`. The implementation details of the WebSocket server, HTTP
server, and Control UI serving are inferred from the architecture doc + configuration doc but
not directly verified from source.
