# 05 — Practitioner: how to install, configure, and use Hermes

## Output contract
- **SUMMARY**: Hermes is installed via a one-line script, configured through `~/.hermes/config.yaml` + `~/.hermes/.env`, and operated via the `hermes` CLI, the TUI, or the messaging gateway. Skills and plugins are the primary extension mechanisms; terminal backends can be local, Docker, SSH, Modal, or Daytona.
- **CHANGES**: None.
- **EVIDENCE**: `README.md`, `CONTRIBUTING.md`, `hermes_cli/config.py` (`DEFAULT_CONFIG`, `OPTIONAL_ENV_VARS`), `hermes_cli/commands.py` (`COMMAND_REGISTRY`), `tools/terminal_tool.py`, `tools/registry.py`, `cli-config.yaml.example`, `docker-compose.yml`, `AGENTS.md`.
- **RISKS**: Setup has many moving parts (API keys, optional backends, platform-specific deps). The default local backend is not a sandbox; mistakes can affect the host.
- **BLOCKERS**: `cli-config.yaml.example` was not copied locally; default config values were read from `hermes_cli/config.py`.

## Installation
### Linux / macOS / WSL2
```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.bashrc  # or ~/.zshrc
hermes
```

### Windows (native PowerShell)
```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
hermes
```

### Manual / dev
```bash
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[all,dev]"
scripts/run_tests.sh
```

## Initial configuration
User state lives in `~/.hermes/`:
| Path | Purpose |
|------|---------|
| `~/.hermes/config.yaml` | Settings (model, terminal, toolsets, compression, display, security, etc.) |
| `~/.hermes/.env` | API keys / secrets only |
| `~/.hermes/auth.json` | OAuth credentials (Nous Portal) |
| `~/.hermes/skills/` | Active skills |
| `~/.hermes/memories/` | Persistent memory files |
| `~/.hermes/state.db` | SQLite session store |
| `~/.hermes/cron/` | Scheduled job data |
| `~/.hermes/logs/` | Logs |

### Minimal setup
```bash
mkdir -p ~/.hermes/{skills,memories,logs,cron}
cp cli-config.yaml.example ~/.hermes/config.yaml
# or just run `hermes setup`
```

## CLI commands
From `COMMAND_REGISTRY` in `hermes_cli/commands.py`:

### Session
- `/new` or `/reset` — fresh session
- `/resume [name]` — resume previous session
- `/branch [name]` — fork session
- `/compress [here N]` — compress context
- `/history`, `/save`, `/title`, `/status`
- `/retry`, `/undo N`
- `/quit`, `/exit`

### Configuration
- `/model [provider:model]` — switch model
- `/personality [name]` — set persona
- `/config` — show current config
- `/tools [list|enable|disable] [name...]` — manage tools
- `/toolsets` — list toolsets
- `/skin [name]` — change CLI theme
- `/yolo` — toggle dangerous-command approval bypass
- `/reasoning [level|show|hide]`

### Tools & Skills
- `/skills [search|browse|inspect|install|audit|pending|approve|reject]`
- `/memory [pending|approve|reject|approval]`
- `/cron [list|add|create|edit|pause|resume|run|remove]`
- `/browser [connect|disconnect|status]`
- `/plugins` — list installed plugins
- `/reload-mcp`, `/reload-skills`

### Info
- `/help`, `/commands`, `/usage`, `/insights [days]`, `/platforms`, `/version`

### Messaging-only
- `/start`, `/topic`, `/sethome`, `/approve`, `/deny`, `/restart`, `/platform`

## Gateway setup
```bash
hermes gateway setup   # configure platforms
hermes gateway start    # run the gateway
```
Supported platforms: Telegram, Discord, Slack, WhatsApp, Signal, Email, SMS, Matrix, Mattermost, Feishu, DingTalk, WeCom/WeChat, QQ, Yuanbao, BlueBubbles, iMessage/Photon, Home Assistant, webhook, API server, Raft.

## Model providers
- OpenRouter, OpenAI, Anthropic, Nous Portal, Google/Gemini, xAI, NVIDIA NIM, Kimi/Moonshot, Z.AI/GLM, MiniMax, StepFun, LM Studio, Azure, Bedrock, and custom endpoints.
- Provider resolution via `hermes_cli/config.py` `OPTIONAL_ENV_VARS` and `providers/` / `plugins/model-providers/`.
- `hermes model` opens the model picker.

## Terminal backends
Set in `config.yaml`:
```yaml
terminal:
  backend: local        # or docker, ssh, modal, daytona, singularity
  cwd: .
  timeout: 180
  docker_image: nikolaik/python-nodejs:python3.11-nodejs20
  container_cpu: 1
  container_memory: 5120
  container_disk: 51200
  container_persistent: true
  docker_volumes: []
  persistent_shell: true
```

## Skills
- Bundled skills live in `skills/`; optional skills in `optional-skills/`; user skills in `~/.hermes/skills/`.
- Skills are loaded by `build_skills_system_prompt()` and invoked via slash commands (e.g., `/arxiv`) or by the agent using `skill_view` / `skills_list`.
- A skill is a directory with a `SKILL.md` file, optionally `scripts/`, `references/`, `templates/`.
- Example from `skills/research/arxiv/SKILL.md`:
```yaml
---
name: arxiv
description: Search arXiv papers by keyword, author, category, or ID.
version: 1.0.0
author: Nous Research
license: MIT
metadata:
  hermes:
    tags: [research, arxiv, papers]
    category: research
---
# ArXiv Skill
...
```

## Writing a minimal skill
Create `~/.hermes/skills/my-category/my-skill/SKILL.md` with frontmatter and a procedure. Then `/reload-skills`.

## Creating a tool (only for core contributions)
1. Add `tools/my_tool.py` that calls `registry.register(...)` at module level.
2. Add the tool name to the appropriate toolset in `toolsets.py`.
3. For local-only tools, use a plugin instead.

## Plugins
Plugins live in `~/.hermes/plugins/<name>/` or are pip packages. A plugin exposes `register(ctx)` and can:
- register lifecycle hooks (`pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `on_session_start`, `on_session_end`)
- register tools via `ctx.register_tool(...)`
- register CLI subcommands via `ctx.register_cli_command(...)`

## Cron / scheduled tasks
- `tools/cronjob_tools.py` exposes `cronjob` tool.
- CLI: `/cron add "every day at 9am" "send a daily summary"`.
- Jobs are delivered to any configured platform.

## MCP servers
```yaml
mcp_servers:
  filesystem:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    timeout: 120
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "..."
```
Then `hermes mcp catalog` / `hermes mcp add` or restart.

## Docker / serverless deployment
- `docker/Dockerfile` and `docker-compose.yml` provide a containerized run.
- Modal/Daytona backends offer serverless persistence.
- The default local install is under `~/.hermes` (Linux/macOS) or `%LOCALAPPDATA%\hermes` (Windows).

## Example `config.yaml` snippets
```yaml
model: anthropic/claude-sonnet-4
providers:
  openrouter:
    api_key: ${OPENROUTER_API_KEY}
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}

toolsets:
  - hermes-cli

agent:
  max_turns: 90
  gateway_timeout: 1800
  api_max_retries: 3
  coding_context: auto

terminal:
  backend: docker
  docker_image: nikolaik/python-nodejs:python3.11-nodejs20
  docker_volumes:
    - "/home/user/projects:/workspace/projects"

memory:
  provider: builtin    # or honcho, hindsight, mem0, etc.

skills:
  external_dirs: []
  guard_agent_created: false

gateway:
  telegram:
    enabled: true
    allowed_users:
      - my_telegram_username
```

## Example `.env`
```bash
OPENROUTER_API_KEY=...
ANTHROPIC_API_KEY=...
TELEGRAM_BOT_TOKEN=...
```

## Recommended workflow
1. `hermes setup` or `hermes setup --portal` for Nous Portal.
2. `hermes` to start chatting.
3. `hermes model` to pick the model.
4. `hermes tools` to enable/disable toolsets.
5. `hermes gateway setup` + `hermes gateway start` to chat from messaging apps.
6. Install skills from the hub or create your own for repeated workflows.

## Gotchas
- `terminal.cwd` in `config.yaml` is canonical; `MESSAGING_CWD` / `TERMINAL_CWD` in `.env` are deprecated.
- `.env` is for secrets only; behavioral settings go in `config.yaml`.
- Nous Portal bundles model + search + image + TTS + cloud browser under one subscription.
- The default local terminal backend can modify the host filesystem.
