# Researcher 5 - Practitioner / Applied / Integration Perspective
# Repository: https://github.com/openclaw/openclaw
# Date: 2026-06-22
# Sources: README.md, docs/channels/telegram.md (raw GitHub), docs/tools/skills.md + docs/gateway/configuration.md (docs.openclaw.ai mirror)

## 1. Telegram Channel Setup (botToken, dmPolicy, allowlist, groupPolicy)

### 1.1 Bot Token
Telegram uses grammY; long polling default, webhook optional. Token set in config (NOT via login command):
- Primary: channels.telegram.botToken
- Env fallback: TELEGRAM_BOT_TOKEN (default account only)
- Per-account: channels.telegram.accounts.<id>.botToken
- File-based: channels.telegram.tokenFile (regular file; symlinks rejected)
Token resolution is account-aware (config wins over env). Bot identity cached 24h after startup.

Quick setup:
```json5
{ channels: { telegram: { enabled: true, botToken: "123:abc", dmPolicy: "pairing", groups: { "*": { requireMention: true } } } } }
```

### 1.2 DM Policy (dmPolicy)
Four modes:
- pairing (DEFAULT): unknown senders get one-time code; approve with `openclaw pairing approve telegram <CODE>`; codes expire 1h. First approved pairing bootstraps commands.ownerAllowFrom.
- allowlist: only senders in allowFrom. Empty allowFrom + this policy = rejected by validation.
- open: all DMs allowed. REQUIRES allowFrom: ["*"]. Dangerous - any user guessing bot username can command it.
- disabled: ignore all DMs.
allowFrom accepts numeric Telegram user IDs; telegram:/tg: prefixes normalized. Prefer allowlist with numeric IDs for one-owner bots (durable in config).
Security boundary (2026.2.25+): group sender auth does NOT inherit DM pairing-store approvals. Pairing stays DM-only.

### 1.3 Group Policy (groupPolicy) and Group Allowlists
Two independent controls:
1. Which groups allowed (channels.telegram.groups): negative supergroup chat IDs (e.g. -1001234567890). No groups config + groupPolicy "open" = any group; + "allowlist" (default) = blocked until added. groups configured = acts as allowlist.
2. Which senders allowed in groups (groupPolicy): open/allowlist(default)/disabled. groupAllowFrom filters senders (numeric user IDs). If unset, falls back to allowFrom (NOT pairing store). Do NOT put group chat IDs in groupAllowFrom.
Per-group and per-topic overrides supported. Topics: channels.telegram.groups.<chatId>.topics.<threadId>. Topics inherit group settings unless overridden. agentId is topic-only (does NOT inherit).
Runtime default: if channels.telegram missing, defaults to fail-closed groupPolicy="allowlist" unless channels.defaults.groupPolicy set.

### 1.4 Practical One-Owner Setup
```json5
{ channels: { telegram: { enabled: true, dmPolicy: "pairing", allowFrom: ["<USER_ID>"], groupPolicy: "allowlist", groups: { "<GROUP_CHAT_ID>": { requireMention: true } } } } }
```

### 1.5 Multi-Account
Multiple bots under channels.telegram.accounts.<id>. Set defaultAccount for explicit routing. Named accounts inherit allowFrom/groupAllowFrom but NOT accounts.default.* values. Concurrent getMe probes bounded on startup.

### 1.6 Privacy Mode (Telegram-side)
Bots default to Privacy Mode. For full group visibility: disable via BotFather /setprivacy OR make bot admin. When toggling: remove + re-add bot in each group.

### 1.7 Webhook Mode
Set webhookUrl + webhookSecret; optional webhookPath (/telegram-webhook), webhookHost (127.0.0.1), webhookPort (8787). Validates request guards, secret token, JSON body before 200. Processes async through same per-chat lanes.

### 1.8 Streaming
streaming: off|partial(default)|block|progress. preview.toolProgress (default true) controls tool status lines. preview.commandText: raw(default)|status. Reasoning stream via /reasoning stream (deleted after final delivery).

### 1.9 Exec Approvals via Telegram
- execApprovals.enabled (auto-enables when approvers resolvable)
- execApprovals.approvers (numeric user IDs; falls back to commands.ownerAllowFrom)
- execApprovals.target: dm(default)|channel|both
- agentFilter, sessionFilter available
- Inline buttons require capabilities.inlineButtons for target surface
- Approvals expire 30 min default
- Channel delivery shows command text - only enable channel/both in trusted groups

### 1.10 Other Telegram Features
- Rich messages (Bot API 10.1): richMessages: true for tables/details/rich media. Default off.
- Inline buttons: capabilities.inlineButtons: off|dm|group|all|allowlist(default)
- Custom commands: customCommands array; names a-z0-9_, length 1-32
- Reply threading: replyToMode: off(default)|first|all. Tags [[reply_to_current]], [[reply_to:<id>]]
- Forum topics: session keys append :topic:<threadId>. Per-topic agent routing via agentId
- Audio/video/stickers: voice notes vs audio, video notes vs video. Sticker actions gated (default disabled)
- Reactions: reactionNotifications (off|own|all), reactionLevel (off|ack|minimal|extensive). ackReaction + ackReactionScope (default group-mentions)
- Error controls: errorPolicy (reply|silent), errorCooldownMs (60000)
- Config writes: configWrites (default true) - group migration events, /config set/unset
- Proxy: channels.telegram.proxy (socks5), network.autoSelectFamily, network.dangerouslyAllowPrivateNetwork

## 2. Skills System (SKILL.md, YAML frontmatter, gating, allowlists)

### 2.1 SKILL.md Format
Each skill = directory with SKILL.md (YAML frontmatter + markdown body). Follows AgentSkills spec. Minimum: name + description. Parser supports single-line keys only; metadata must be single-line JSON. Use {baseDir} for skill folder path.
```markdown
---
name: image-lab
description: Generate or edit images via a provider-backed image workflow
---
When the user asks to generate an image, use the image_generate tool...
```

### 2.2 Optional Frontmatter
- homepage (string): URL in macOS Skills UI
- user-invocable (bool, default true): exposed as slash command
- disable-model-invocation (bool, default false): keeps instructions out of prompt; still slash command
- command-dispatch: "tool" - bypasses model, dispatches to registered tool
- command-tool (string): tool name for command-dispatch
- command-arg-mode: "raw" (default) - forwards raw args to tool

### 2.3 Loading Order (highest precedence first)
1. Workspace skills: <workspace>/skills
2. Project agent skills: <workspace>/.agents/skills
3. Personal agent skills: ~/.agents/skills
4. Managed/local: ~/.openclaw/skills
5. Bundled: shipped with install
6. Extra dirs: skills.load.extraDirs + plugin skills
SKILL.md discovered anywhere under configured root. Folder path for organization only; name from frontmatter or directory name.

### 2.4 Per-Agent vs Shared
Per-agent (<workspace>/skills) = only that agent. Project-agent (<workspace>/.agents/skills) = only workspace agent. Personal-agent (~/.agents/skills) = all agents on machine. Shared managed (~/.openclaw/skills) = all agents. Extra dirs = all agents.

### 2.5 Agent Allowlists
Skill location (precedence) and visibility (which agent) are SEPARATE controls.
```json5
{ agents: { defaults: { skills: ["github", "weather"] }, list: [ { id: "writer" }, { id: "docs", skills: ["docs-search"] }, { id: "locked-down", skills: [] } ] } }
```
Rules: omit defaults.skills = unrestricted. omit list[].skills = inherit. list[].skills: [] = no skills. Non-empty list[].skills = FINAL set (no merge with defaults). Applies across prompt building, slash-command discovery, sandbox sync, skill snapshots.

### 2.6 Gating (metadata.openclaw)
Filtered at load time. No metadata.openclaw = always eligible.
```markdown
metadata: { "openclaw": { "requires": { "bins": ["uv"], "env": ["GEMINI_API_KEY"], "config": ["browser.enabled"] }, "primaryEnv": "GEMINI_API_KEY" } }
```
Gates: always, emoji, homepage, os (darwin|linux|win32), requires.bins, requires.anyBins, requires.env, requires.config, primaryEnv, install (object[] for macOS UI). Legacy metadata.clawdbot accepted when metadata.openclaw absent.

### 2.7 Config Overrides (skills.entries)
```json5
{ skills: { entries: { "image-lab": { enabled: true, apiKey: { source: "env", provider: "default", id: "GEMINI_API_KEY" }, env: { GEMINI_API_KEY: "..." }, config: { endpoint: "...", model: "nano-pro" } } } } }
```
enabled (false disables even bundled). apiKey = plaintext or SecretRef. env = injected for run (only if not already set). config = custom bag. allowBundled = allowlist for bundled only. coding-agent bundled skill is opt-in.

### 2.8 Environment Injection Flow
1. Read metadata, apply gating/allowlists/overrides. 2. Inject env/apiKey into process.env for run duration. 3. Build system prompt (compact XML block). 4. Restore env after run.
WARNING: env injection scoped to HOST agent run, NOT sandbox. Inside sandbox, env/apiKey have no effect.

### 2.9 Snapshots and Refresh
Snapshotted at session START; reused for all turns. Changes take effect next new session. Mid-session refresh: skills watcher detects SKILL.md change OR new eligible remote node connects. Watcher: skills.load.watch (default true), watchDebounceMs (250).
```json5
{ skills: { load: { extraDirs: ["~/Projects/agent-scripts/skills"], allowSymlinkTargets: ["~/Projects/manager/skills"], watch: true, watchDebounceMs: 250 } } }
```

### 2.10 Token Impact
total = 195 + Sum(97 + len(name) + len(description) + len(filepath)). ~24 tokens per skill before field lengths. Keep descriptions short.

### 2.11 ClawHub Registry
clawhub.ai - public skills registry. Install: openclaw skills install <slug> [--global]. Git: git:owner/repo@ref. Local: ./path --as my-tool. Update: openclaw skills update --all [--global]. Verify: openclaw skills verify <slug>. Publish: clawhub sync --all.

### 2.12 Skill Workshop
Proposal queue - agent drafts proposal instead of writing to SKILL.md. Operator reviews/approves: openclaw skills workshop list/inspect/apply <id>.

### 2.13 Security
Treat third-party skills as UNTRUSTED CODE. Path containment: realpath must stay inside configured root (unless allowSymlinkTargets). Operator install policy: security.installPolicy runs trusted command before installs (fails closed). Secret injection: env/apiKey into host process only, not sandbox.

## 3. Config-as-Code (JSON5, TypeBox schema validation, hot reload)

### 3.1 Config File
JSON5 config at ~/.openclaw/openclaw.json (comments + trailing commas allowed). Must be regular file (symlinks unsupported for OpenClaw-owned writes). Override via OPENCLAW_CONFIG_PATH. Missing = safe defaults.
Minimal: { agents: { defaults: { workspace: "~/.openclaw/workspace" } }, channels: { whatsapp: { allowFrom: ["+15555550123"] } } }

### 3.2 Strict Validation (TypeBox schema)
Only configs fully matching schema accepted. Unknown keys/malformed types/invalid values = Gateway REFUSES TO START. Only root-level exception: $schema (string).
- openclaw config schema prints canonical JSON Schema
- config.schema.lookup RPC fetches path-scoped node + child summaries
- Field title/description docs through nested objects, wildcards, array-items, anyOf/oneOf/allOf
- Runtime plugin/channel schemas merge in when manifest registry loaded
On failure: Gateway does not boot. Only diagnostic commands work (doctor, logs, health, status). Run openclaw doctor --fix. Last-known-good kept but NOT auto-restored. Destructive clobbers (dropping gateway.mode, shrinking >50%) rejected as .rejected.*. Promotion to last-known-good skipped when redacted secrets (***).

### 3.3 Editing Methods
1. Interactive: openclaw onboard / openclaw configure
2. CLI: openclaw config get/set/unset <path>
3. Control UI: http://127.0.0.1:18789 Config tab (form from live schema + Raw JSON escape hatch)
4. Direct edit: Gateway watches and applies automatically

### 3.4 Hot Reload
Watches ~/.openclaw/openclaw.json. Direct edits treated as untrusted until validated. Watcher waits for editor churn to settle, rejects invalid edits without rewriting.
Reload modes (gateway.reload.mode):
- hybrid (default): hot-applies safe changes, auto-restarts for critical
- hot: hot-applies safe only, logs warning for restart-needed
- restart: restarts on any change
- off: disables watching
Hot-apply (no restart): channels, agents, models, routing, hooks, cron, heartbeat, session, messages, tools, browser, skills, mcp, audio, talk, ui, logging, identity, bindings.
Restart needed: gateway.* (port, bind, auth, TLS, HTTP), discovery, plugins.
Exceptions: gateway.reload and gateway.remote changes do NOT trigger restart.
Reload planning: when editing $include source file, plans from source-authored layout. Fails closed if ambiguous.

### 3.5 Config Splitting ($include)
Single file = replaces containing object. Array = deep-merged in order (later wins). Sibling keys merged after includes. Nested up to 10 levels. Relative to including file. Confinement: must resolve under openclaw.json dir (OPENCLAW_INCLUDE_ROOTS for additional). OpenClaw-owned writes update single-file included section; root includes/arrays/sibling overrides fail closed.

### 3.6 Config RPC (Programmatic)
- config.schema.lookup: inspect subtree
- config.get: fetch snapshot + hash
- config.patch: partial updates (JSON merge patch; null deletes; arrays replace with replacePaths confirmation)
- config.apply: replace entire config
- update.run: self-update + restart (continuationMessage for post-restart follow-up)
- update.status: inspect restart sentinel
Rate-limited: 3 req/60s per deviceId+clientIp. Restart coalesce + 30s cooldown.

### 3.7 Env Vars and Secret Refs
Sources: parent process + .env (cwd) + ~/.openclaw/.env (global). Neither overrides existing. Inline: { env: { OPENROUTER_API_KEY: "...", vars: { GROQ_API_KEY: "..." }, shellEnv: { enabled: true, timeoutMs: 15000 } } }
Substitution: ${VAR_NAME} (uppercase, missing/empty throws, escape $${VAR}). Works in $include files.
SecretRef: { source: "env"|"file"|"exec", provider, id }. Used for apiKey, serviceAccountRef, etc.

## 4. Exec Approval Flow for Dangerous Operations

### 4.1 Telegram-Specific
- channels.telegram.execApprovals.enabled: auto-enables when approvers resolvable
- execApprovals.approvers: numeric Telegram user IDs (falls back to commands.ownerAllowFrom)
- execApprovals.target: dm(default)|channel|both
- agentFilter, sessionFilter available
- Approvals expire 30 min default
- Inline buttons require capabilities.inlineButtons for target surface (dm/group/all)
- Channel delivery shows command text - only enable channel/both in trusted groups/topics
- Forum topic delivery preserves topic for prompt and follow-up
- allowFrom/groupAllowFrom/defaultTo control who talks to bot and where replies go - NOT who is approver
- First approved DM pairing bootstraps commands.ownerAllowFrom when no owner exists
- Approval IDs prefixed plugin: resolve through plugin approvals; others through exec approvals first

### 4.2 Related Docs (confirmed via GitHub API, not fetched)
- docs/tools/exec-approvals.md (20,789 bytes)
- docs/tools/exec-approvals-advanced.md (22,717 bytes)
- docs/tools/exec.md (17,247 bytes)
- docs/tools/permission-modes.md (6,008 bytes)
- docs/tools/elevated.md (4,905 bytes)
- docs/gateway/sandbox-vs-tool-policy-vs-elevated.md (8,370 bytes)

## 5. Cron / Webhooks for Automation

### 5.1 Cron Jobs
```json5
{ cron: { enabled: true, maxConcurrentRuns: 8, sessionRetention: "24h", runLog: { maxBytes: "2mb", keepLines: 2000 } } }
```
sessionRetention: prune completed isolated run sessions (default 24h; false to disable). runLog: prune per-job history. Hot-applies without restart. Full docs at docs/automation/cron-jobs.

### 5.2 Webhooks (Hooks)
```json5
{ hooks: { enabled: true, token: "shared-secret", path: "/hooks", defaultSessionKey: "hook:ingress", allowRequestSessionKey: false, allowedSessionKeyPrefixes: ["hook:"], mappings: [ { match: { path: "gmail" }, action: "agent", agentId: "main", deliver: true } ] } }
```
Security: treat payloads as UNTRUSTED. Use dedicated hooks.token (NOT Gateway auth secrets). Auth header-only (Authorization: Bearer / x-openclaw-token); query-string rejected. path cannot be /. Keep unsafe-content bypass flags disabled. If allowRequestSessionKey enabled, set allowedSessionKeyPrefixes. Prefer strong models + strict tool policy for hook-driven agents. Gmail Pub/Sub available. Hot-applies without restart.

### 5.3 Heartbeat
```json5
{ agents: { defaults: { heartbeat: { every: "30m", target: "last", directPolicy: "allow" } } } }
```
every: duration (30m, 2h, 0m to disable). target: last|none|<channel-id>. Hot-applies without restart.

## 6. Multi-Agent Routing

### 6.1 Agent Configuration
```json5
{ agents: { list: [ { id: "home", default: true, workspace: "~/.openclaw/workspace-home" }, { id: "work", workspace: "~/.openclaw/workspace-work" } ] }, bindings: [ { agentId: "home", match: { channel: "whatsapp", accountId: "personal" } }, { agentId: "work", match: { channel: "whatsapp", accountId: "biz" } } ] }
```
Each agent: own workspace, sessions, skills, memory. Bindings route inbound channels/accounts/peers to isolated agents.

### 6.2 Per-Topic Agent Routing (Telegram)
```json5
{ channels: { telegram: { groups: { "-1001234567890": { topics: { "1": { agentId: "main" }, "3": { agentId: "zu" }, "5": { agentId: "coder" } } } } } } }
```
Each topic: isolated session key (agent:zu:telegram:group:-1001234567890:topic:3).

### 6.3 ACP Bindings
Forum topics pin ACP harness sessions via top-level bindings[] with type: "acp", match.channel: "telegram", peer.kind: "group", peer.id: "-1001234567890:topic:42". Thread-bound ACP spawn: /acp spawn <agent> --thread here|auto. Requires channels.telegram.threadBindings.spawnSessions (default true).

### 6.4 Session Scoping
dmScope: main|per-peer|per-channel-peer|per-account-channel-peer. threadBindings: { enabled, idleHours, maxAgeHours }. reset: { mode: "daily", atHour, idleMinutes }.

### 6.5 Skills Per Agent
Non-empty agents.list[].skills REPLACES defaults entirely. agents.defaults.skills = shared baseline. See section 2.5.

## 7. Sandbox Configuration (Docker)

### 7.1 Sandbox Modes
```json5
{ agents: { defaults: { sandbox: { mode: "non-main", scope: "agent" } } } }
```
off = no sandbox (tools on host, default for main). non-main = non-main sessions sandboxed (recommended for group/channel safety). all = all sandboxed. scope: session|agent|shared.

### 7.2 Backends
Docker = DEFAULT sandbox backend. SSH and OpenShell also available.

### 7.3 Typical Sandbox Tool Policy
Allow: bash, process, read, write, edit, sessions_list, sessions_history, sessions_send, sessions_spawn. Deny: browser, canvas, nodes, cron, discord, gateway.

### 7.4 Docker Image
Build via scripts/sandbox-setup.sh (source checkout) or inline docker build (npm install).

### 7.5 Sandbox and Skills
requires.bins checked on HOST at load time. If agent in sandbox, binary must exist INSIDE container. Install via agents.defaults.sandbox.docker.setupCommand (runs once after container creation; needs network egress, writable root FS, root user) or custom image. Env injection (env/apiKey) scoped to HOST only, NOT sandbox. See skills-config for sandboxed secret passing.

## 8. Additional Practical Observations

### 8.1 Installation
Runtime: Node 24 (recommended) or 22.19+. npm install -g openclaw@latest then openclaw onboard --install-daemon. Gateway daemon: launchd (macOS)/systemd (Linux). Foreground: openclaw gateway --port 18789 --verbose. openclaw doctor surfaces risky DM policies (--fix/--yes). Update channels: stable/beta/dev.

### 8.2 Security Defaults
Default DM access: dmPolicy="pairing" on Telegram/WhatsApp/Signal/iMessage/Teams/Discord/Google Chat/Slack. Unknown senders get pairing code. Public DMs require explicit opt-in (dmPolicy="open" + "*" in allowlist). Treat inbound DMs as UNTRUSTED. Before remote exposure: read Security docs, Exposure runbook, Sandboxing docs.

### 8.3 Workspace
Root: ~/.openclaw/workspace (configurable via agents.defaults.workspace). Injected prompt files: AGENTS.md, SOUL.md, TOOLS.md. Skills: ~/.openclaw/workspace/skills/<skill>/SKILL.md.

### 8.4 Supported Channels (22+)
WhatsApp, Telegram, Slack, Discord, Google Chat, Signal, iMessage, IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost, Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, Zalo Personal, WeChat, QQ, WebChat. All share same DM policy pattern.

### 8.5 Docs Inventory (confirmed via GitHub API)
docs/tools/: 48 files (acp-agents, exec-approvals, exec-approvals-advanced, exec, permission-modes, elevated, skills, skills-config, skill-workshop, creating-skills, slash-commands, subagents, browser, code-execution, etc.)
docs/gateway/: 38 files (configuration, configuration-reference 71KB, config-agents 70KB, config-channels 47KB, sandboxing 28KB, security/ dir, secrets, etc.)

## CONFIDENCE
- Telegram channel setup: HIGH (full docs fetched)
- Skills system: HIGH (full docs via docs.openclaw.ai mirror)
- Config-as-code/hot reload: HIGH (full docs via docs.openclaw.ai mirror)
- Exec approval flow: MEDIUM (Telegram section full; full exec-approvals.md not fetched, existence confirmed)
- Cron/webhooks: HIGH (config section fully documented)
- Multi-agent routing: HIGH (config + Telegram per-topic routing documented)
- Sandbox configuration: MEDIUM (config section + overview documented; full sandboxing.md 28KB not fetched, existence confirmed)
Overall: HIGH for primary focus areas.
