# OpenClaw — Practitioner Perspective

## Tools used
- `webfetch` on README.md
- `read` on `docs/gateway/configuration.md`, `docs/channels/telegram.md`, `docs/tools/skills.md`, `docs/concepts/architecture.md`
- `grep` for CLI command patterns in `src/cli/run-main.ts`
- `glob` for `docs/cli/*.md` and `docs/automation/*.md`

## Installation
Recommended install:
```bash
npm install -g openclaw@latest
# or: pnpm add -g openclaw@latest
openclaw onboard --install-daemon
```

Development checkout:
```bash
git clone https://github.com/openclaw/openclaw.git
cd openclaw
pnpm install
pnpm openclaw setup        # first run only
pnpm ui:build              # optional prebuild Control UI
pnpm gateway:watch         # dev loop with auto-reload
```

Runtime: Node.js 22.19+ (Node 24 recommended).

## Quick start commands
```bash
# Check status
openclaw gateway status

# Run foreground/debug
openclaw gateway stop
openclaw gateway --port 18789 --verbose

# Send a message
openclaw message send --target +1234567890 --message "Hello from OpenClaw"

# Talk to the assistant
openclaw agent --message "Ship checklist" --thinking high

# Diagnostics
openclaw doctor
openclaw logs --follow
```

## Configuration basics
Config file: `~/.openclaw/openclaw.json` (JSON5 with comments and trailing commas).

Minimal config:
```json5
{
  agent: {
    model: "<provider>/<model-id>",
  },
}
```

A more complete starting point:
```json5
{
  agents: {
    defaults: {
      workspace: "~/.openclaw/workspace",
      model: {
        primary: "anthropic/claude-sonnet-4-6",
        fallbacks: ["openai/gpt-5.4"],
      },
      skills: ["github", "weather"],
    },
  },
  channels: {
    telegram: {
      enabled: true,
      botToken: "123:abc",
      dmPolicy: "pairing",
      allowFrom: ["<your_user_id>"],
      groups: {
        "-1001234567890": { requireMention: true },
      },
    },
  },
}
```

## Editing config
Four ways:
1. Interactive wizard: `openclaw onboard` or `openclaw configure`
2. CLI one-liners:
   ```bash
   openclaw config get agents.defaults.workspace
   openclaw config set agents.defaults.heartbeat.every "2h"
   openclaw config unset plugins.entries.brave.config.webSearch.apiKey
   ```
3. Control UI: http://127.0.0.1:18789 -> Config tab
4. Direct edit of `openclaw.json` (gateway watches and hot-reloads)

## Telegram setup example
From `docs/channels/telegram.md`:
1. Create a bot with @BotFather and get a token.
2. Add config:
   ```json5
   {
     channels: {
       telegram: {
         enabled: true,
         botToken: "123:abc",
         dmPolicy: "pairing",
         groups: { "*": { requireMention: true } },
       },
     },
   }
   ```
3. Start the gateway: `openclaw gateway`
4. Approve the first DM:
   ```bash
   openclaw pairing list telegram
   openclaw pairing approve telegram <CODE>
   ```
5. Add the bot to a group; get group chat ID from logs or `getUpdates`.

Telegram-specific features:
- Long polling default; webhook optional (`webhookUrl`, `webhookSecret`).
- Streaming preview modes: `off`, `partial`, `block`, `progress`.
- Rich messages via `channels.telegram.richMessages: true` (Bot API 10.1).
- Forum topics and per-topic agent routing (`agentId` per topic).
- Inline buttons, native commands, reactions, stickers, polls, exec approvals.
- Group history context controls: `includeGroupHistoryContext: "mention-only" | "recent" | "none"`.

## Skills usage
Skills are markdown instruction files with YAML frontmatter.

Install from ClawHub:
```bash
openclaw skills install <slug>
openclaw skills install <slug> --global
openclaw skills update --all
openclaw skills verify <slug>
```

Skill roots (highest precedence first):
1. `<workspace>/skills`
2. `<workspace>/.agents/skills`
3. `~/.agents/skills`
4. `~/.openclaw/skills`
5. Bundled skills
6. Extra dirs / plugin skills

Restrict per agent:
```json5
{
  agents: {
    defaults: { skills: ["github", "weather"] },
    list: [
      { id: "writer" },
      { id: "docs", skills: ["docs-search"] },
      { id: "locked-down", skills: [] },
    ],
  },
}
```

Skill gating via `metadata.openclaw`:
```markdown
---
name: image-lab
description: Generate images via a provider workflow
metadata: { "openclaw": { "requires": { "bins": ["uv"], "env": ["GEMINI_API_KEY"], "config": ["browser.enabled"] } } }
---
```

## Automation patterns

### Cron jobs
```json5
{
  cron: {
    enabled: true,
    maxConcurrentRuns: 8,
    sessionRetention: "24h",
  },
}
```
Create via CLI or the `cron` tool in chat.

### Webhooks
```json5
{
  hooks: {
    enabled: true,
    token: "shared-secret",
    path: "/hooks",
    defaultSessionKey: "hook:ingress",
    allowedSessionKeyPrefixes: ["hook:"],
    mappings: [
      {
        match: { path: "gmail" },
        action: "agent",
        agentId: "main",
        deliver: true,
      },
    ],
  },
}
```
Use a dedicated `hooks.token`; do not reuse the gateway auth secret.

### Heartbeat
```json5
{
  agents: {
    defaults: {
      heartbeat: {
        every: "30m",
        target: "last",
      },
    },
  },
}
```

## Multi-agent routing
```json5
{
  agents: {
    list: [
      { id: "home", default: true, workspace: "~/.openclaw/workspace-home" },
      { id: "work", workspace: "~/.openclaw/workspace-work" },
    ],
  },
  bindings: [
    { agentId: "home", match: { channel: "whatsapp", accountId: "personal" } },
    { agentId: "work", match: { channel: "whatsapp", accountId: "biz" } },
  ],
}
```

## Sandboxing
```json5
{
  agents: {
    defaults: {
      sandbox: {
        mode: "non-main",  // off | non-main | all
        scope: "agent",    // session | agent | shared
      },
    },
  },
}
```
Build the sandbox image with `scripts/sandbox-setup.sh` or the inline docker build command.

## CLI command families
- `openclaw gateway` — run, status, stop, call, logs
- `openclaw agent` — run an agent turn
- `openclaw message` — send messages/polls
- `openclaw pairing` — approve device/sender pairing
- `openclaw config` — get/set/unset/validate/apply
- `openclaw skills` — install, update, verify, workshop
- `openclaw doctor` — diagnose and fix config/state
- `openclaw security` — audit, status
- `openclaw nodes` / `openclaw devices` — node management
- `openclaw browser` — browser control
- `openclaw cron` — cron jobs
- `openclaw tools` — tool surface

Chat commands available from any connected channel:
`/status`, `/new`, `/reset`, `/compact`, `/think <level>`, `/verbose on|off`, `/trace on|off`, `/usage`, `/restart`, `/activation mention|always`, `/name`.

## Environment variables and secrets
- `.env` in cwd or `~/.openclaw/.env` are loaded.
- Config supports `${VAR_NAME}` substitution.
- SecretRef objects support `env`, `file`, and `exec` sources:
  ```json5
  { apiKey: { source: "env", provider: "default", id: "OPENAI_API_KEY" } }
  ```
- Secrets live in `~/.openclaw/credentials/` and per-agent `auth-profiles.json`.

## Recommended operational checklist
1. Install Node 24 and `openclaw` globally.
2. Run `openclaw onboard` to generate config and install the daemon.
3. Set a strong gateway auth token or password; keep it in env or SecretRef.
4. Configure one channel (Telegram is the easiest to start).
5. Set `dmPolicy: allowlist` or `pairing` and add your own numeric ID.
6. Run `openclaw doctor` and `openclaw security audit` before adding skills.
7. Enable sandboxing (`non-main`) if you plan to run any untrusted or broadly reachable agent.
8. Keep the gateway on loopback; use Tailscale or SSH tunnel for remote access.
9. Pin `plugins.allow` and verify skills before installing.
10. Use `openclaw logs --follow` and `openclaw gateway status` for debugging.

## Troubleshooting entry points
- `openclaw doctor --fix`
- `openclaw logs --follow`
- `openclaw channels status --probe`
- `openclaw config validate`
- `openclaw security audit`

## Bottom line for a practitioner
OpenClaw is a batteries-included personal assistant platform. The happy path is `openclaw onboard` -> add a channel token -> configure allowlists -> start the daemon. The power user path is deep: multi-agent routing, skills, cron, webhooks, sandboxing, MCP, and a rich plugin ecosystem. The main practical gotchas are config strictness, the default host-execution model, and the need to keep the gateway within its intended trust boundary.
