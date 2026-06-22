# Researcher 5: Practitioner — Applied/Integration Perspective
# Repository: https://github.com/nousresearch/hermes-agent
# Date: 2026-06-22

## Sources Fetched

1. **README.md** (raw) — full content: install, quickstart, CLI vs messaging, migration, contributing
2. **Skills System docs** (hermes-agent.nousresearch.com/docs/user-guide/features/skills) — comprehensive
3. **Configuration docs** (hermes-agent.nousresearch.com/docs/user-guide/configuration) — comprehensive
4. **Contributing docs** (hermes-agent.nousresearch.com/docs/developer-guide/contributing) — full
5. **session_search_tool.py** (GitHub API, base64 decoded) — full source (32KB)
6. **skill_manager_tool.py** (GitHub API, truncated) — 47KB, confirmed exists
7. **hermes_cli/commands.py** (GitHub API, truncated) — 83KB, confirmed exists
8. **docker-compose.yml** (raw) — full deployment config
9. **Repo structure** (GitHub API) — root + tools/ + hermes_cli/ listings
10. **.env.example** — confirmed exists (23KB), fetch failed (transient transport errors)
11. **cli-config.yaml.example** — confirmed exists (65KB), the canonical config reference

---

## 1. REAL-WORLD USAGE EXAMPLES

### 1.1 Quickstart (2-minute path)

**Linux/macOS/WSL2:**
```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.bashrc
hermes              # start chatting
```

**Windows (native PowerShell):**
```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```
The installer handles: uv, Python 3.11, Node.js, ripgrep, ffmpeg, and a portable Git Bash (MinGit, no admin required).

### 1.2 CLI Usage Patterns

```bash
hermes              # Interactive CLI — start a conversation
hermes model        # Choose LLM provider and model
hermes tools        # Configure which tools are enabled
hermes config set   # Set individual config values
hermes gateway      # Start messaging gateway (Telegram, Discord, etc.)
hermes setup        # Full setup wizard
hermes doctor       # Diagnose issues
hermes update       # Update to latest version
```

### 1.3 In-Conversation Slash Commands (shared across CLI + messaging)

| Action | Command |
|--------|---------|
| Start fresh | `/new` or `/reset` |
| Change model | `/model [provider:model]` |
| Set personality | `/personality [name]` |
| Retry/undo | `/retry`, `/undo` |
| Compress context | `/compress` |
| Check usage | `/usage`, `/insights [--days N]` |
| Browse skills | `/skills` or `/<skill-name>` |
| Interrupt | `Ctrl+C` (CLI) or `/stop` (messaging) |

### 1.4 Messaging Gateway Setup

```bash
hermes gateway setup    # Configure platforms (Telegram, Discord, Slack, WhatsApp, Signal, Email)
hermes gateway start    # Start the gateway process
# Then send the bot a message from any configured platform
```

### 1.5 Portal One-Command Setup

```bash
hermes setup --portal    # OAuth login → sets Nous as provider + turns on Tool Gateway
hermes portal info       # Check what's wired up
```

---

## 2. CONFIGURATION

### 2.1 Directory Structure

```
~/.hermes/
├── config.yaml     # Settings (model, terminal, TTS, compression, etc.)
├── .env            # API keys and secrets
├── auth.json       # OAuth provider credentials (Nous Portal, etc.)
├── SOUL.md         # Primary agent identity (slot #1 in system prompt)
├── memories/       # Persistent memory (MEMORY.md, USER.md)
├── skills/         # Agent-created + bundled skills
├── cron/           # Scheduled jobs
├── sessions/       # Gateway sessions
├── logs/           # Logs (errors.log, gateway.log — secrets auto-redacted)
└── skill-bundles/  # YAML bundle files
```

### 2.2 Configuration Precedence (highest first)

1. **CLI arguments** — `hermes chat --model anthropic/claude-sonnet-4`
2. **~/.hermes/config.yaml** — primary config for non-secret settings
3. **~/.hermes/.env** — fallback for env vars; required for secrets
4. **Built-in defaults** — hardcoded safe defaults

**Rule:** Secrets go in `.env`. Everything else goes in `config.yaml`. `hermes config set` auto-routes to the right file.

### 2.3 Environment Variable Substitution in config.yaml

```yaml
auxiliary:
  vision:
    api_key: ${GOOGLE_API_KEY}
    base_url: ${CUSTOM_VISION_URL}
delegation:
  api_key: ${DELEGATION_KEY}
```
Supports `${VAR}` syntax only (not bare `$VAR`). Undefined vars kept verbatim.

### 2.4 Key Config Sections

**Model:**
```yaml
model:
  provider: openrouter    # or nous, anthropic, openai, custom
  name: anthropic/claude-opus-4
  context_length: 200000
```

**Terminal Backend (6 options):**
```yaml
terminal:
  backend: local    # local | docker | ssh | modal | daytona | singularity
  cwd: "."
  timeout: 180
  home_mode: auto   # auto | real | profile
  env_passthrough: []
```

**Memory:**
```yaml
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200   # ~800 tokens
  user_char_limit: 1375     # ~500 tokens
  write_approval: false     # true = require approval
```

**Context Compression:**
```yaml
compression:
  enabled: true
  threshold: 0.50           # Compress at 50% of context limit
  target_ratio: 0.20        # Preserve 20% as recent tail
  protect_last_n: 20        # Min recent messages uncompressed
  protect_first_n: 3        # Non-system head messages pinned

auxiliary:
  compression:
    model: ""               # Empty = use main model. Override for cheaper compression.
    provider: "auto"
    base_url: null
```

**Tool Output Limits:**
```yaml
tool_output:
  max_bytes: 50000        # terminal output cap
  max_lines: 2000         # read_file pagination cap
  max_line_length: 2000   # per-line cap
```

**Skills:**
```yaml
skills:
  write_approval: false          # true = stage every skill write for review
  guard_agent_created: false     # true = scan for dangerous patterns
  external_dirs:                 # External skill directories
    - ~/.agents/skills
    - /home/shared/team-skills
  config:                        # Per-skill non-secret settings
    myplugin:
      path: ~/myplugin-data
```

**Agent:**
```yaml
agent:
  max_turns: 90            # Max iterations per conversation turn
  api_max_retries: 3       # Retries per provider before fallback
  disabled_toolsets:       # Global toolset disable
    - memory
    - web
```

### 2.5 .env File (API Keys)

Key environment variables (from .env.example, 23KB file):
- `OPENROUTER_API_KEY` — OpenRouter provider
- `ANTHROPIC_API_KEY` — Anthropic direct
- `OPENAI_API_KEY` — OpenAI / custom endpoints
- `TELEGRAM_BOT_TOKEN` — Telegram gateway
- `DISCORD_BOT_TOKEN` — Discord gateway
- `FIRECRAWL_API_KEY` — Web search
- `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` — Modal backend
- `DAYTONA_API_KEY` — Daytona backend
- `GITHUB_TOKEN` — Skills hub (rate limit increase)
- Provider-specific keys for TTS, image generation, etc.

### 2.6 Config Management Commands

```bash
hermes config              # View current configuration
hermes config edit         # Open config.yaml in editor
hermes config set KEY VAL  # Set specific value (auto-routes to .env or config.yaml)
hermes config check        # Check for missing options
hermes config migrate      # Interactively add missing options
```

---

## 3. SKILL SYSTEM

### 3.1 How Skills Work

Skills are **on-demand knowledge documents** following a progressive disclosure pattern to minimize token usage. Compatible with the [agentskills.io](https://agentskills.io) open standard.

**Progressive Disclosure Levels:**
```
Level 0: skills_list()           → [{name, description, category}, ...]   (~3k tokens)
Level 1: skill_view(name)        → Full content + metadata       (varies)
Level 2: skill_view(name, path)  → Specific reference file       (varies)
```

### 3.2 SKILL.md Format

```markdown
---
name: my-skill
description: Brief description of what this skill does
version: 1.0.0
platforms: [macos, linux]     # Optional — restrict to specific OS
metadata:
  hermes:
    tags: [python, automation]
    category: devops
    fallback_for_toolsets: [web]      # Show ONLY when web toolset unavailable
    requires_toolsets: [terminal]     # Show ONLY when terminal toolset available
    config:                           # Optional config.yaml settings
      - key: my.setting
        description: "What this controls"
        default: "value"
        prompt: "Prompt for setup"
---

# Skill Title

## When to Use
Trigger conditions for this skill.

## Procedure
1. Step one
2. Step two

## Pitfalls
- Known failure modes and fixes

## Verification
How to confirm it worked.
```

### 3.3 Skill Directory Structure

```
~/.hermes/skills/                  # Single source of truth
├── mlops/                         # Category directory
│   ├── axolotl/
│   │   ├── SKILL.md               # Main instructions (required)
│   │   ├── references/            # Additional docs
│   │   ├── templates/             # Output formats
│   │   ├── scripts/               # Helper scripts
│   │   └── assets/                # Supplementary files
│   └── vllm/
│       └── SKILL.md
├── devops/
│   └── deploy-k8s/
│       ├── SKILL.md
│       └── references/
├── .hub/                          # Skills Hub state
│   ├── lock.json
│   ├── quarantine/
│   └── audit.log
└── .bundled_manifest              # Tracks seeded bundled skills
```

### 3.4 Using Skills

```bash
# In CLI or any messaging platform:
/gif-search funny cats
/axolotl help me fine-tune Llama 3 on my dataset
/github-pr-workflow create a PR for the auth refactor
/plan design a rollout for migrating our auth provider

# Just the skill name loads it:
/excalidraw

# Natural conversation:
hermes chat --toolsets skills -q "What skills do you have?"
```

### 3.5 Creating Skills

**Agent-Managed (skill_manage tool):**
The agent creates skills autonomously via the `skill_manage` tool:
- After completing a complex task (5+ tool calls) successfully
- When it hit errors/dead ends and found the working path
- When the user corrected its approach
- When it discovered a non-trivial workflow

**Actions:**
| Action | Use for | Key params |
|--------|---------|------------|
| `create` | New skill from scratch | `name`, `content`, optional `category` |
| `patch` | Targeted fixes (preferred) | `name`, `old_string`, `new_string` |
| `edit` | Major structural rewrites | `name`, `content` (full replacement) |
| `delete` | Remove a skill | `name` |
| `write_file` | Add/update supporting files | `name`, `file_path`, `file_content` |
| `remove_file` | Remove supporting file | `name`, `file_path` |

**Manual creation:** Place a `SKILL.md` file in `~/.hermes/skills/<category>/<skill-name>/`

### 3.6 Skill Bundles

Group multiple skills under one slash command:
```bash
hermes bundles create backend-dev \
  --skill github-code-review \
  --skill test-driven-development \
  --skill github-pr-workflow \
  -d "Backend feature work — review, test, PR workflow"
```
Then: `/backend-dev refactor the auth middleware` loads all three skills.

**Bundle YAML schema** (`~/.hermes/skill-bundles/<slug>.yaml`):
```yaml
name: backend-dev
description: Backend feature work — review, test, PR workflow.
skills:
  - github-code-review
  - test-driven-development
  - github-pr-workflow
instruction: |
  Always start by writing failing tests, then implement.
  Open the PR through the standard workflow with co-author tags.
```

### 3.7 Skills Hub (Install from registries)

```bash
hermes skills browse                              # Browse all hub skills
hermes skills search kubernetes                   # Search all sources
hermes skills install openai/skills/k8s           # Install from GitHub
hermes skills install official/security/1password # Official optional
hermes skills install https://sharethis.chat/SKILL.md  # Direct URL
hermes skills check                               # Check for updates
hermes skills update                              # Update hub skills
hermes skills audit                               # Security re-scan
```

**Supported sources:** official, skills-sh, well-known, url, github, clawhub, lobehub, browse-sh, claude-marketplace

**Trust levels:** builtin → official → trusted → community

**Security:** All hub-installed skills go through a security scanner (data exfiltration, prompt injection, destructive commands, supply-chain signals). `--force` overrides caution/warn findings but NOT dangerous verdicts.

### 3.8 Conditional Activation (Fallback Skills)

```yaml
metadata:
  hermes:
    fallback_for_toolsets: [web]      # Show ONLY when web toolset unavailable
    requires_toolsets: [terminal]     # Show ONLY when terminal toolset available
    fallback_for_tools: [web_search]  # Show ONLY when specific tools unavailable
    requires_tools: [terminal]        # Show ONLY when specific tools available
```

Example: `duckduckgo-search` skill uses `fallback_for_toolsets: [web]` — appears only when FIRECRAWL_API_KEY is missing.

### 3.9 Secure Setup on Load

```yaml
required_environment_variables:
  - name: TENOR_API_KEY
    prompt: Tenor API key
    help: Get a key from https://developers.google.com/tenor
    required_for: full functionality
```
Missing values prompt securely in local CLI only. Messaging surfaces tell users to use `hermes setup` or `~/.hermes/.env`. Declared env vars auto-passed to `execute_code` and `terminal` sandboxes.

### 3.10 Write Approval Gate

```yaml
skills:
  write_approval: true   # Stage every skill write for review
```
When on, skill writes are staged under `~/.hermes/pending/skills/`:
```bash
/skills pending             # list staged writes
/skills diff <id>           # full unified diff
/skills approve <id>        # apply
/skills reject <id>         # drop
/skills approval on|off     # toggle
```

### 3.11 Publishing Custom Skill Taps

Any GitHub repo with `skills/` directory of `SKILL.md` files:
```bash
hermes skills tap add my-org/hermes-skills
hermes skills search deploy
hermes skills install my-org/hermes-skills/deploy-runbook
```

### 3.12 External Skill Directories

```yaml
skills:
  external_dirs:
    - ~/.agents/skills
    - /home/shared/team-skills
    - ${SKILLS_REPO}/skills
```
- Local precedence: if same skill name in local + external, local wins
- External skills appear in system prompt index, slash commands, etc.
- Non-existent paths silently skipped

---

## 4. SESSION SEARCH TOOL (source code analysis)

### 4.1 Architecture

`tools/session_search_tool.py` (32KB) — single-shape tool with three calling modes inferred from args (no explicit mode parameter):

1. **DISCOVERY** — pass `query`. Runs FTS5, dedupes hits by session lineage, returns top N sessions each with: snippet, ±5 message window around match, bookend_start (first 3 user+assistant msgs), bookend_end (last 3). Zero LLM cost. ~20ms.

2. **SCROLL** — pass `session_id` + `around_message_id`. Returns window of ±N messages centered on anchor. No FTS5, no bookends. ~1ms.

3. **BROWSE** — no args. Returns recent sessions chronologically (titles, previews, timestamps).

4. **READ** — pass `session_id` only (no anchor). Dumps whole session (first 20 + last 10 when large).

### 4.2 Key Implementation Patterns

- **No LLM calls anywhere** — every shape returns actual messages from SQLite DB
- **FTS5-backed** — uses `db.search_messages()` with role filtering
- **Lineage dedup** — `_resolve_to_parent()` walks `parent_session_id` chain to root; dedupes by lineage root
- **Cross-profile support** — `profile` parameter reads another profile's `state.db` read-only
- **Anchored view** — `db.get_anchored_view(hit_sid, msg_id, window=5, bookend=3)` returns window + bookends
- **Session rebind** — if anchor lives in a child session (compaction/delegation), silently rebinds to owning session
- **Hidden sources** — `subagent` and `tool` sources excluded from browse/search by default
- **Tool schema** — registered via `registry.register()` with full JSON schema, check_fn for DB availability, emoji 🗞️

### 4.3 Performance

- Old version: aux-LLM-powered, ~$0.30/call, ~30-90 seconds, confabulation risk
- New version: ~20ms discovery, ~1ms scroll, $0 cost, 4,500× faster (PR #27590)

---

## 5. INTEGRATION PATTERNS

### 5.1 Messaging Platforms

Single gateway process supports: Telegram, Discord, Slack, WhatsApp, Signal, Email, Microsoft Teams, Google Chat, ntfy (23 platforms total).

**Docker Compose deployment:**
```yaml
services:
  gateway:
    build: .
    image: hermes-agent
    container_name: hermes
    restart: unless-stopped
    network_mode: host
    volumes:
      - ~/.hermes:/opt/data
    environment:
      - HERMES_UID=${HERMES_UID:-10000}
      - HERMES_GID=${HERMES_GID:-10000}
    command: ["gateway", "run"]

  dashboard:
    image: hermes-agent
    container_name: hermes-dashboard
    restart: unless-stopped
    network_mode: host
    depends_on:
      - gateway
    volumes:
      - ~/.hermes:/opt/data
    command: ["dashboard", "--host", "127.0.0.1", "--no-open"]
```

### 5.2 MCP Integration

Hermes can connect any MCP server for extended capabilities. The `tools/mcp_tool.py` is 202KB — the largest tool file. Supports MCP OAuth (`tools/mcp_oauth.py`, 30KB). Hermes can also serve as an MCP server (`mcp_serve.py`, 31KB).

### 5.3 Tool Gateway (Nous Portal)

One subscription covers: web search (Firecrawl), image generation (FAL), TTS (OpenAI), cloud browser (Browser Use). Per-backend, not all-or-nothing — can still bring your own keys per tool.

### 5.4 Terminal Backends (6 options)

| Backend | Where commands run | Isolation | Best for |
|---------|-------------------|-----------|----------|
| local | Your machine | None | Development |
| docker | Docker container | Full (cap-drop) | Safe sandboxing, CI/CD |
| ssh | Remote server | Network | Remote dev, powerful HW |
| modal | Modal cloud | Full (cloud VM) | Ephemeral cloud compute |
| daytona | Daytona workspace | Full (cloud container) | Managed cloud dev |
| singularity | Apptainer container | Namespaces | HPC clusters |

**Docker backend key features:**
- Single persistent container shared across sessions/subagents
- Labeled lookup: `hermes-agent=1`, `hermes-task-id=<id>`, `hermes-profile=<profile>`
- Security hardening: `--cap-drop ALL`, `--security-opt no-new-privileges`, `--pids-limit 256`
- Credential forwarding via `docker_forward_env`
- Volume mounts, GPU support, host-user mapping
- Orphan reaper for abandoned containers

### 5.5 Delegate/Subagent System

Spawn isolated subagents for parallel workstreams:
- `delegate_task(tasks=[...])` — parallel subagent spawning
- Subagents share the single Docker container (concurrent ops may collide)
- Per-task image overrides for isolated sandboxes via `register_task_env_overrides()`
- `tools/delegate_tool.py` is 139KB — major subsystem
- `tools/async_delegation.py` (21KB) — async delegation support

### 5.6 Cron Scheduling

Built-in cron scheduler with delivery to any platform:
```bash
# Daily reports, nightly backups, weekly audits — all in natural language
```
`tools/cronjob_tools.py` (45KB), `hermes_cli/cron.py` (15KB)

### 5.7 Context Files

Files injected into system prompt: `SOUL.md`, `.hermes.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules`
- `context_file_max_chars: 20000` (default)
- Truncation: head/tail applied

### 5.8 OpenClaw Migration

```bash
hermes claw migrate              # Interactive migration
hermes claw migrate --dry-run    # Preview
hermes claw migrate --preset user-data   # Without secrets
hermes claw migrate --overwrite  # Overwrite conflicts
```
Imports: SOUL.md, memories, skills, command allowlist, messaging settings, API keys, TTS assets, workspace instructions.

---

## 6. TESTING

### 6.1 Running Tests

```bash
scripts/run_tests.sh    # CI-parity test runner
```

### 6.2 Development Setup for Contributors

```bash
# Standard installer path (recommended):
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
cd "${HERMES_HOME:-$HOME/.hermes}/hermes-agent"
uv pip install -e ".[all,dev]"
npm install  # Optional: browser tools
scripts/run_tests.sh

# Manual clone fallback:
git clone https://github.com/NousResearch/hermes-agent.git
cd hermes-agent
uv venv venv --python 3.11
export VIRTUAL_ENV="$(pwd)/venv"
uv pip install -e ".[all,dev]"
```

### 6.3 Configure for Development

```bash
mkdir -p ~/.hermes/{cron,sessions,logs,memories,skills}
cp cli-config.yaml.example ~/.hermes/config.yaml
touch ~/.hermes/.env
echo 'OPENROUTER_API_KEY=sk-or-v1-your-key' >> ~/.hermes/.env
```

### 6.4 Test Guidelines

- No real Telegram API calls in tests
- Test on all platforms if touching file I/O, process management, terminal handling
- `scripts/check-windows-footguns.py` before pushing Windows-impacting changes
- Conventional Commits: `<type>(<scope>): <description>`
- Scopes: `cli`, `gateway`, `tools`, `skills`, `agent`, `install`, `whatsapp`, `security`

### 6.5 Batch Trajectory Generation

`batch_runner.py` (57KB) — batch trajectory generation for training tool-calling models. `mini_swe_runner.py` (28KB) — SWE-bench style evaluation.

---

## 7. DEPLOYMENT

### 7.1 Docker Deployment

```bash
# Build and run with docker-compose:
HERMES_UID=$(id -u) HERMES_GID=$(id -g) docker compose up -d

# Or use the Dockerfile directly:
docker build -t hermes-agent .
docker run -d --name hermes \
  -v ~/.hermes:/opt/data \
  -e HERMES_UID=$(id -u) \
  -e HERMES_GID=$(id -g) \
  --network host \
  hermes-agent gateway run
```

**Security notes from docker-compose.yml:**
- Dashboard binds to 127.0.0.1 by default (stores API keys — don't expose without auth)
- For remote access: SSH tunnel or reverse proxy with auth
- API server off unless `API_SERVER_KEY` + `API_SERVER_HOST` set
- `/init` (s6-overlay) must be first command — runs cont-init.d scripts
- `docker-compose.windows.yml` available for Windows-specific deployment

### 7.2 Serverless/Cloud Deployment

**Modal (serverless, hibernates when idle):**
```yaml
terminal:
  backend: modal
  container_cpu: 1
  container_memory: 5120
  container_disk: 51200
  container_persistent: true   # Snapshot/restore filesystem
```
Requires: `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` or `~/.modal.toml`

**Daytona (managed cloud dev, stop/resume):**
```yaml
terminal:
  backend: daytona
  container_persistent: true   # Stop/resume instead of delete
```
Requires: `DAYTONA_API_KEY`. Max 10 GiB disk.

### 7.3 VPS Deployment ($5 VPS)

The README explicitly states: "Run it on a $5 VPS, a GPU cluster, or serverless infrastructure that costs nearly nothing when idle."

Pattern:
1. Install on VPS via installer script
2. Configure gateway for Telegram/Discord
3. Run `hermes gateway start` (or via Docker)
4. Talk to it from messaging platforms

### 7.4 SSH Remote Backend

```bash
TERMINAL_SSH_HOST=my-server.example.com
TERMINAL_SSH_USER=ubuntu
# Optional: TERMINAL_SSH_PORT=22, TERMINAL_SSH_KEY=/path/to/key
```
Uses ControlMaster for connection reuse. Persistent shell enabled by default.

### 7.5 Profiles (Multi-Instance)

```bash
hermes profile create research --no-skills    # Named profile with no bundled skills
hermes -p coder    # Run with a specific profile
```
Each profile has its own `HERMES_HOME`, config, skills, sessions, containers.

### 7.6 Git Worktree Isolation

```yaml
worktree: true         # Always create a worktree
worktree_sync: true    # Branch from fetched remote tip (default)
```
Each CLI session creates a fresh worktree under `.worktrees/` with its own branch.

### 7.7 Remote-to-Host File Sync

For SSH/Modal/Daytona backends, on session teardown, modified files sync back to `~/.hermes/cache/remote-syncs/<session-id>/`:
```yaml
terminal:
  file_sync_max_mb: 100      # default — sync files up to 100 MB
  file_sync_enabled: true    # default
```

---

## 8. CODE STYLE & SECURITY

### 8.1 Code Style

- PEP 8 with practical exceptions (no strict line length)
- Comments only for non-obvious intent, trade-offs, API quirks
- Error handling: catch specific exceptions, use `logger.warning()`/`logger.error()` with `exc_info=True`
- Cross-platform: never assume Unix
- Profile-safe paths: use `get_hermes_home()` from `hermes_constants`

### 8.2 Cross-Platform Rules

- No unguarded `signal.SIGKILL` (not on Windows) — use `gateway.status.terminate_pid()`
- Catch `OSError` alongside `ProcessLookupError` on `os.kill(pid, 0)`
- Gate POSIX-only: `os.setsid`, `os.killpg`, `os.getpgid`, `os.fork`
- Always open files with `encoding="utf-8"` (Windows default is cp1252)
- Use `pathlib.Path` / `os.path.join` — never manual `/` concat

### 8.3 Security Layers

| Layer | Implementation |
|-------|---------------|
| Sudo password piping | `shlex.quote()` |
| Dangerous command detection | Regex in `tools/approval.py` (89KB) with approval flow |
| Cron prompt injection | Scanner blocks instruction-override patterns |
| Write deny list | `os.path.realpath()` to prevent symlink bypass |
| Skills guard | Security scanner for hub-installed skills |
| Code execution sandbox | Child process with API keys stripped |
| Container hardening | Docker: cap-drop ALL, no-new-privileges, PID limits |
| Path security | `tools/path_security.py` |
| OSV check | `tools/osv_check.py` — vulnerability scanning |

### 8.4 Environment Variable Passthrough

```yaml
terminal:
  env_passthrough: []  # Env var names to forward to sandboxed execution
```
Skills declaring `required_environment_variables` are merged automatically.

---

## 9. TOOL ECOSYSTEM (40+ tools)

Key tools from repo structure:
- `approval.py` (89KB) — command approval flow
- `delegate_tool.py` (139KB) — subagent delegation
- `browser_tool.py` (170KB) — browser automation
- `mcp_tool.py` (202KB) — MCP integration (largest tool)
- `file_operations.py` (106KB) — file read/write/edit
- `file_tools.py` (80KB) — file utilities
- `code_execution_tool.py` (75KB) — Python script execution
- `memory_tool.py` (45KB) — persistent memory
- `skill_manager_tool.py` (47KB) — skill CRUD
- `session_search_tool.py` (32KB) — FTS5 session recall
- `cronjob_tools.py` (45KB) — scheduled tasks
- `kanban_tools.py` (61KB) — task management
- `image_generation_tool.py` (61KB) — image gen
- `checkpoint_manager.py` (61KB) — session checkpoints
- `mixture_of_agents_tool.py` (22KB) — MoA ensemble
- `async_delegation.py` (21KB) — async subagents
- `budget_config.py` (5KB) — iteration budget
- `clarify_tool.py` (7KB) — clarification requests
- `interrupt.py` (3KB) — interrupt handling
- `fuzzy_match.py` (33KB) — fuzzy string matching
- `patch_parser.py` (23KB) — unified diff parsing

**Toolset keys:** `browser`, `clarify`, `code_execution`, `cronjob`, `debugging`, `delegation`, `discord`, `feishu_doc`, `feishu_drive`, `file`, `homeassistant`, `image_gen`, `kanban`, `memory`, `messaging`, `moa`, `rl`, `safe`, `search`, `session_search`, `skills`, `spotify`, `terminal`, `todo`, `tts`, `video`, `vision`, `web`, `yuanbao`

---

## 10. REPO STRUCTURE

```
NousResearch/hermes-agent/
├── .env.example                    # 23KB — env var reference
├── cli-config.yaml.example         # 65KB — canonical config reference
├── Dockerfile                      # 19KB
├── docker-compose.yml              # 3.4KB
├── docker-compose.windows.yml      # 1KB
├── AGENTS.md                       # 69KB — agent instructions
├── CONTRIBUTING.md                 # 47KB
├── SECURITY.md                     # 15KB
├── cli.py                          # 690KB — main CLI (monolith)
├── hermes_state.py                 # 218KB — state management
├── hermes_cli/                     # CLI modules
│   ├── commands.py                 # 83KB
│   ├── config.py                   # 321KB — config management
│   ├── gateway.py                  # 253KB — messaging gateway
│   ├── doctor.py                   # 104KB — diagnostics
│   ├── auth.py                     # 329KB — auth/OAuth
│   ├── kanban.py / kanban_db.py    # Kanban multi-agent platform
│   ├── cron.py                     # 15KB
│   ├── curator.py                  # 22KB — memory curator
│   ├── env_loader.py               # 15KB — .env loading
│   └── ...
├── tools/                          # 50+ tool modules
│   ├── (see section 9)
│   ├── environments/               # Terminal backend implementations
│   ├── computer_use/               # Desktop automation
│   └── ...
├── agent/                          # Agent loop modules (14 modules, refactored from 16K-line run_agent.py)
├── gateway/                        # Gateway platform implementations
├── skills/                         # Bundled skills
├── optional-skills/                # Official optional skills
├── optional-mcps/                  # Optional MCP servers
├── plugins/                        # Plugin system
├── providers/                      # LLM provider implementations
├── acp_adapter/                    # ACP protocol adapter
├── acp_registry/                   # ACP registry
├── batch_runner.py                 # 57KB — trajectory generation
├── mini_swe_runner.py              # 28KB — SWE-bench eval
├── mcp_serve.py                    # 31KB — MCP server mode
├── model_tools.py                  # 55KB — model management
├── hermes_constants.py             # 26KB — constants
├── hermes_logging.py               # 22KB — logging
├── hermes_bootstrap.py             # 5KB — bootstrap
└── docs/                           # Documentation site
```

---

## 11. KEY ARCHITECTURAL OBSERVATIONS

1. **Monolithic core with modular tools** — `cli.py` (690KB), `hermes_state.py` (218KB), `hermes_cli/config.py` (321KB) are very large files. The agent loop was refactored from 16,083-line `run_agent.py` to 3,821 lines across 14 `agent/*` modules (-76%).

2. **Progressive disclosure everywhere** — skills, session_search, tools all use lazy loading to minimize token usage.

3. **FTS5 as first-class citizen** — session search, skill discovery, and likely code search all use SQLite FTS5. No vector DB for session search — pure lexical.

4. **Self-improving loop** — agent creates skills from experience, memory persists across sessions, session_search recalls past conversations. Background review can suggest skill changes.

5. **Six terminal backends** — local, Docker, SSH, Modal, Daytona, Singularity — covering $5 VPS to GPU cluster to serverless.

6. **23 messaging platforms** — single gateway process handles all platforms with cross-platform conversation continuity.

7. **No vendor lock-in** — works with 300+ models from any provider. Switch with `hermes model` — no code changes.

8. **Security is layered** — command approval, dangerous command detection, symlink bypass prevention, skill security scanning, container hardening, API key stripping in sandboxes.

9. **Profile system** — multiple isolated agents on same machine, each with own config/skills/sessions/containers.

10. **Research-ready** — batch trajectory generation, trajectory compression for training, SWE-bench evaluation built in.
