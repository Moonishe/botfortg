# Hermes Agent — Iteration 01 synthesis

## Output contract
- **SUMMARY**: Nous Research's Hermes Agent is a mature, self-improving personal AI agent with a Python core, a TypeScript/React UI, a huge plugin/skill ecosystem, and a very high development velocity. It is designed around a stable, prompt-cache-friendly core loop and pushes nearly all new capability to the edges (skills, plugins, MCP servers). The project is honest about its security model: the OS is the only real boundary. For TelegramHelper, the most valuable takeaways are the skill registry with conditional activation, the `ToolRegistry` pattern, the SQLite+FTS5 session store, the `CommandDef` slash-command registry, and the narrow-core philosophy.
- **CHANGES**: None (this is a research artifact, not a code change).
- **EVIDENCE**: README, AGENTS.md, SECURITY.md, CONTRIBUTING.md, pyproject.toml, package.json, full git clone of `NousResearch/hermes-agent`, source analysis of `run_agent.py`, `agent/conversation_loop.py`, `tools/registry.py`, `toolsets.py`, `agent/memory_manager.py`, `agent/memory_provider.py`, `agent/prompt_builder.py`, `tools/skill_manager_tool.py`, `tools/session_search_tool.py`, `tools/mcp_tool.py`, `tools/terminal_tool.py`, `gateway/run.py`, `hermes_cli/commands.py`, `hermes_cli/config.py`, GitHub release page HTML for v0.17.0, `git log`/`git tag`.
- **RISKS**: Huge attack surface (40+ tools, 20+ platforms, arbitrary plugins/skills/MCP), in-process heuristics not a real boundary, synchronous core loop in large god-files, high velocity causing regression risk, gateway shared-agent cache, Windows/posix edge-case complexity.
- **BLOCKERS**: GitHub API rate-limited, so live star/fork/PR counts could not be fetched; `VISION.md` and `CHANGELOG.md` do not exist in the repo root; no native `codegraph`/`serena`/`warpgrep_github_search` MCP tools were available in this environment, so analysis used `git`, `webfetch`, `grep`, `read`, and `glob` instead.

---

## SUMMARY (3-5 sentences)
Hermes Agent is a large, self-improving personal AI-agent monorepo built by Nous Research. Its Python core runs a synchronous tool-calling loop (`AIAgent` → `agent.conversation_loop.run_conversation` → `model_tools.handle_function_call` → `tools.registry`), backed by an SQLite+FTS5 session store, pluggable memory providers, and an agent-managed skill system. A TypeScript/React layer provides the CLI/TUI, Electron desktop app, dashboard, and website. The project is deliberately narrow at the core: new capability is added via skills, plugins, MCP servers, or service-gated tools rather than new core tools. It is extremely active (~12,638 commits across all refs, ~5,000 commits in the last 50 days, v0.17.0 released 2026-06-19) and honest about its security model: the OS is the only containment boundary.

---

## KEY_FINDINGS (10)
1. **Narrow-core architecture.** `AGENTS.md` says per-conversation prompt caching is sacred and the core is a narrow waist. New core tools are sent on every API call, so the preferred ladder is: existing code → CLI+skill → service-gated tool → plugin → MCP server → new core tool.
2. **Central tool registry.** `tools/registry.py` provides a thread-safe `ToolRegistry` singleton, `ToolEntry` metadata, TTL-cached `check_fn` availability, dynamic schema overrides, and `tool_error`/`tool_result` helpers. Tools self-register via `registry.register()` at import time.
3. **Toolsets as a public schema knob.** `toolsets.py` defines 35+ toolsets; `_HERMES_CORE_TOOLS` is the shared bundle for CLI and messaging platforms; `resolve_toolset()` recursively composes toolsets. Platform-specific toolsets (e.g., `hermes-telegram`) inherit the core bundle and add platform tools.
4. **Pluggable memory.** `agent/memory_provider.py` defines a clean `MemoryProvider` ABC; `agent/memory_manager.py` orchestrates one built-in + one external provider. Built-in memory is `MEMORY.md`/`USER.md`; external providers live in `plugins/memory/` and are closed to new in-tree additions.
5. **Agent-managed skills.** Skills are markdown `SKILL.md` files with YAML frontmatter. The prompt builder creates a compact, cached skill index; skills can be conditionally shown (`fallback_for_toolsets`, `requires_toolsets`). The agent can create/edit/delete skills via `tools/skill_manager_tool.py`.
6. **SQLite+FTS5 session store.** `hermes_state.py` replaces per-session JSONL with a WAL-enabled SQLite database, schema v16, FTS5 full-text search, and parent_session_id chains for branching/compression. `tools/session_search_tool.py` provides discovery/scroll/browse modes with no LLM calls.
7. **Multi-platform gateway.** `gateway/run.py` runs messaging adapters (Telegram, Discord, Slack, WhatsApp, Signal, Email, SMS, Matrix, Feishu, WeCom, DingTalk, QQ, Yuanbao, BlueBubbles, Photon/iMessage, Home Assistant, webhook, API server, Raft) with per-platform agent caching, approval hooks, and slash-command routing.
8. **Terminal backend matrix.** `tools/terminal_tool.py` + `tools/environments/` support local, Docker, SSH, Singularity, Modal, Daytona, and managed Modal. The default local backend is the fastest and least isolated; the security docs explicitly recommend Docker/OpenShell for untrusted inputs.
9. **MCP as a first-class extension.** `tools/mcp_tool.py` runs MCP servers over stdio/HTTP/SSE, dynamically registers their tools with `mcp-<server>` prefixes, supports sampling, parallel calls, and env/credential filtering. MCP servers are preferred over adding core tools.
10. **Honest security posture.** `SECURITY.md` is explicit: the only boundary is OS-level isolation. In-process heuristics (approval, redaction, Skills Guard) are helpful but not containment. Exact dependency pins, lazy optional installs, and plugin/skill operator review are the mitigations.

---

## ARCHITECTURE

### Textual scheme
```
┌─────────────────────────────────────────────────────────────────────────┐
│                           User surfaces                                  │
│  CLI (prompt_toolkit) | TUI (Ink/React) | Desktop (Electron) | Web dash │
│  Messaging gateway (Telegram/Discord/Slack/WhatsApp/Signal/Email/...)    │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────┐
│                         hermes_cli / gateway                            │
│  Command registry (CommandDef) | config.yaml | .env | session context    │
│  Gateway runner, platform adapters, approval hooks, agent LRU cache    │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────┐
│                         AIAgent core (run_agent.py)                     │
│  init_agent() | chat() | run_conversation()                              │
│  forwards to agent/conversation_loop.py                                 │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────┐
│                    build_turn_context() (agent/turn_context.py)         │
│  system prompt = identity + skills index + context files + memory       │
│  + environment hints + model/provider config                            │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────┐
│              Synchronous loop: LLM.chat.completions.create(...)          │
│   if tool_calls → model_tools.handle_function_call()                    │
│                 → tools.registry.dispatch(name, args)                   │
│                 → tool handler (terminal/web/search/memory/...)          │
│                 → JSON result appended to messages                       │
│   if text → persist session, return final_response                        │
│   context compression when near token limit                              │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────┐
│                         Tool / capability layer                         │
│  tools/registry.py | toolsets.py | model_tools.py                        │
│  tools/*.py (terminal, web, browser, vision, code, file, cron, ...)    │
│  tools/environments/* (local, docker, ssh, modal, daytona, singularity)   │
│  tools/mcp_tool.py (external MCP servers)                                 │
│  plugins/* (memory, model-providers, image-gen, browser, context, ...)   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼───────────────────────────────────────┐
│                         Persistence / recall                            │
│  hermes_state.py (SQLite + FTS5) | session_search tool                 │
│  agent/memory_manager.py + memory providers                             │
│  ~/.hermes/skills/ | ~/.hermes/memories/ | ~/.hermes/cron/              │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data flow (one turn)
1. User message arrives through CLI/TUI/gateway/desktop.
2. `AIAgent.run_conversation()` calls `build_turn_context()` to assemble the system prompt, load skills, prefetch memory, and sanitize the user message.
3. The agent enters a `while` loop (max 90 iterations by default, plus an iteration budget).
4. It calls the LLM with `messages` + `tools` (OpenAI-format function schemas from the registry).
5. If the response is text, it persists the session to SQLite and returns it.
6. If the response contains tool calls, each call is dispatched through `tools.registry.dispatch()` to the matching handler; the JSON result is appended as a `tool` message.
7. The loop continues until completion, interruption, budget exhaustion, or a max-iteration cap.
8. After the turn, memory providers run `sync_turn()` in the background.

---

## API_SURFACE

### Core library
- `run_agent.AIAgent`
  - `chat(message: str) -> str`
  - `run_conversation(user_message, system_message=None, conversation_history=None, task_id=None, stream_callback=None, ...) -> dict`
- `model_tools`
  - `get_tool_definitions(tool_names, quiet=False) -> list`
  - `handle_function_call(name, args, task_id=None) -> str`
  - `check_toolset_requirements() -> (available, unavailable)`
- `tools.registry`
  - `registry.register(name, toolset, schema, handler, check_fn=None, ...)`
  - `registry.get_definitions(tool_names) -> list`
  - `registry.dispatch(name, args, **kwargs) -> str`
  - `tool_error()`, `tool_result()`
- `toolsets`
  - `get_toolset(name)`, `resolve_toolset(name)`, `get_all_toolsets()`, `create_custom_toolset(name, description, tools, includes)`

### CLI commands (`hermes`)
- `hermes` — start interactive CLI
- `hermes model` — model picker
- `hermes tools` — toolset manager
- `hermes config set <key> <value>`
- `hermes gateway [setup|start]`
- `hermes setup [--portal]`
- `hermes doctor`, `hermes update`, `hermes version`
- `hermes skills [search|browse|install|inspect]`
- `hermes cron [list|add|edit|pause|run|remove]`
- `hermes mcp [catalog|add|remove|reload]`
- `hermes --tui` — start Ink/React TUI

### Slash commands (shared CLI + gateway)
Key commands from `COMMAND_REGISTRY`:
- `/new`, `/reset`, `/resume`, `/branch`, `/compress`, `/history`, `/status`
- `/model`, `/personality`, `/config`, `/tools`, `/toolsets`, `/skin`, `/yolo`, `/reasoning`
- `/skills`, `/memory`, `/cron`, `/browser`, `/plugins`, `/reload-skills`, `/reload-mcp`
- `/help`, `/usage`, `/insights`, `/platforms`, `/version`, `/debug`
- `/quit`, `/exit`

### Tool names (core set)
`web_search`, `web_extract`, `terminal`, `process`, `read_terminal`, `read_file`, `write_file`, `patch`, `search_files`, `vision_analyze`, `image_generate`, `text_to_speech`, `skills_list`, `skill_view`, `skill_manage`, `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_press`, `browser_get_images`, `browser_vision`, `browser_console`, `browser_cdp`, `browser_dialog`, `todo`, `memory`, `session_search`, `clarify`, `execute_code`, `delegate_task`, `cronjob`, `send_message`, `ha_list_entities`, `ha_get_state`, `ha_list_services`, `ha_call_service`, `kanban_show/list/complete/block/heartbeat/comment/create/link/unblock`, `computer_use`.

---

## RISKS
1. **In-process approval is bypassable.** The approval gate is regex/heuristic over shell strings; a determined attacker can evade it. YOLO mode is one config/env var away.
2. **Arbitrary code execution by design.** Terminal, `execute_code`, skills, plugins, and MCP servers all execute code; the only containment is the OS/container.
3. **Plugin/skill trust boundary is the operator.** The project does not vet community skills/plugins; a malicious one is considered the operator's responsibility, not a Hermes vulnerability.
4. **Gateway shared-agent cache.** The LRU cache in `gateway/run.py` shares `AIAgent` instances across sessions; a bug in isolation or eviction could leak conversation context.
5. **Massive integration surface.** 20+ messaging platforms, 6+ terminal backends, 40+ tools, many optional dependencies. Each integration is a potential failure point.
6. **High velocity and large files.** 5k+ commits in 50 days, 17k-line gateway, 13k-line CLI entry point. Refactors are active but regression risk is high.
7. **Prompt-injection through tool results.** Web, MCP, and file-tool outputs are fed back into the LLM context; a compromised data source can hijack the agent.
8. **Windows-native complexity.** Many POSIX footguns are explicitly handled, but the cross-platform support matrix is large and error-prone.
9. **Dependency and supply chain.** Exact pins are good, but optional lazy installs can still pull fresh packages from PyPI/npm at runtime if not pinned carefully.
10. **Token/schema bloat.** The default core tool schema is large; every additional tool increases prompt size and cost, even when gated by `check_fn`.

---

## USAGE_PATTERNS

### Personal coding assistant (CLI)
```bash
hermes
cd /my-project
"Add a CI workflow that runs pytest on PRs"
# Hermes uses terminal, file ops, web search, and skills
```

### Remote chat via Telegram
```bash
hermes gateway setup   # configure Telegram bot + allowed users
hermes gateway start
# Send messages to the bot from Telegram
```

### Scheduled reports
```bash
/cron add "every day at 9am" "send a summary of yesterday's commits to Slack"
# Delivered to the configured Slack channel
```

### Skill creation from a successful workflow
```
After a complex task, the agent can call:
skill_manage(action="create", name="deploy-to-vercel", category="devops", content="...")
# Future prompts can use /deploy-to-vercel
```

### Sandboxed execution
```yaml
# config.yaml
terminal:
  backend: docker
  docker_image: nikolaik/python-nodejs:python3.11-nodejs20
  container_persistent: true
```

### MCP extension
```yaml
mcp_servers:
  github:
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "..."
```

---

## CONFIG_EXAMPLES

### Minimal `~/.hermes/config.yaml`
```yaml
model: openrouter/anthropic/claude-sonnet-4
providers:
  openrouter:
    api_key: ${OPENROUTER_API_KEY}

toolsets:
  - hermes-cli

agent:
  max_turns: 90
  gateway_timeout: 1800
  api_max_retries: 3
  coding_context: auto

terminal:
  backend: local
  cwd: .
  timeout: 180

memory:
  provider: builtin
```

### Telegram gateway `~/.hermes/config.yaml`
```yaml
gateway:
  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_users:
      - my_telegram_handle
  default:
    toolsets:
      - hermes-telegram
```

### Docker backend + volume
```yaml
terminal:
  backend: docker
  docker_image: nikolaik/python-nodejs:python3.11-nodejs20
  docker_volumes:
    - "/home/user/projects:/workspace/projects"
  container_cpu: 2
  container_memory: 8192
  container_persistent: true
```

### Nous Portal (bundled keys)
```bash
hermes setup --portal
# OAuth sets Nous as provider and enables Tool Gateway
```

### Minimal `~/.hermes/.env`
```bash
OPENROUTER_API_KEY=sk-or-...
TELEGRAM_BOT_TOKEN=...
ANTHROPIC_API_KEY=sk-...
```

---

## RECOMMENDATIONS_FOR_TELEGRAMHELPER

TelegramHelper is an aiogram/Telethon Python bot with a Constitution, memory, and a rule-driven agent pipeline. The most concrete things it can adopt from Hermes are:

1. **Adopt a `ToolRegistry` singleton.** Move from hardcoded tool imports to a central registry where each tool module calls `registry.register(...)` at import time. This removes the need for a manual tool list and makes plugins/skills/MCP easy to add later.

2. **Introduce toolsets.** Group tools into `messaging`, `web`, `file`, `admin`, `research`, `vision`, etc. The Telegram bot can load `telegram-messaging` toolset by default; an admin can enable `web` or `terminal` per user. Use a `resolve_toolset()` function for composition.

3. **Use a `CommandDef` slash-command registry.** Centralize all `/` commands in one data structure. Telegram BotCommand menus, CLI help, and gateway dispatch can all derive from the same registry. Aliases and per-platform gating should be first-class.

4. **SQLite + FTS5 for session history.** Replace or augment the current session storage with an SQLite database using FTS5. Implement a `session_search` tool so the bot can recall past conversations by semantic/keyword search without LLM calls.

5. **Skill-as-markdown system.** Create a `~/.hermes/skills`-like directory under TelegramHelper's `.opencode/skills/` where each skill is a `SKILL.md` with frontmatter. Build a compact skill index for the system prompt. Add conditional activation (`requires_toolsets`, `fallback_for_toolsets`) so skills only appear when relevant.

6. **Agent-managed skill creation.** Add a `skill_manage` tool that lets the agent create/edit/delete its own skills (with a guard like `tools/skills_guard.py`). This closes the self-improvement loop.

7. **Pluggable memory provider interface.** Define a `MemoryProvider` ABC and a `MemoryManager` that can host one built-in provider (file-based, like the current memory) plus one optional external provider (Honcho, Mem0, etc.) without forking the core.

8. **Service-gated tools.** Add `check_fn` availability checks (e.g., only expose `terminal` when `TERMINAL_BACKEND` is configured, `home_assistant` only when `HASS_TOKEN` is set). This keeps the default schema small and the product safe.

9. **MCP client support.** Add a lightweight MCP client so TelegramHelper can discover and call external MCP servers. This is better than growing core tools.

10. **Adopt the narrow-core philosophy.** Before adding any new tool, ask: can it be a skill, a plugin, an MCP server, or a service-gated tool? Only add to core if nearly every user needs it.

11. **Security model documentation.** Write a short `SECURITY.md` that states clearly: the OS is the only containment boundary; Telegram bot token scoping and user allowlists are the auth layer; skills/plugins run with the bot's privileges.

12. **Dependency pinning policy.** Use exact pins for core dependencies and upper bounds for optional ones, especially after recent supply-chain attacks. Document the rationale in `pyproject.toml`/`requirements.txt`.

13. **Context compression.** Add a simple context-compression step when approaching the model's context window (summarize older turns and continue).

14. **User modeling / periodic memory nudges.** Use a background curator (like `agent/curator.py`) to review skills and memory periodically, archiving stale ones and prompting the user to confirm writes.

15. **Gateway pattern for multi-platform.** If TelegramHelper later wants Discord/Slack bridges, structure it as a gateway with a shared `GatewayRunner` and per-platform adapters, not separate bot implementations.

---

## CONFIDENCE
**Medium–High.**

- **High confidence** on overall architecture, tool registry, memory provider model, skills system, session store, gateway role, CLI command registry, and security posture. These are directly visible in the source files and documentation.
- **Medium confidence** on exact star/fork/PR/issue counts because the GitHub API was rate-limited and the HTML page did not expose server-side counts. The README and release page provided reasonable context numbers.
- **Medium confidence** on implementation details of specific tools (e.g., browser internals, some platform adapters) because we did not deep-read every tool file; we relied on the registry/toolset documentation and representative samples.
- **Low confidence** on the exact state of the live `main` branch beyond the commit we cloned (the repo may have advanced during the research).

---

## GAPS
1. **No `VISION.md` / root `CHANGELOG.md`**. Project history and roadmap are in release pages and `website/docs/`.
2. **GitHub API rate-limited**. Could not fetch live `stargazers_count`, `forks_count`, open issues, or PR counts programmatically.
3. **No `codegraph`/`serena`/`warpgrep` access**. These tools were requested but not available in this environment; analysis used git, webfetch, grep, read, and glob.
4. **Did not run the test suite**. We only inspected file structure; `scripts/run_tests.sh` was not executed.
5. **Did not build/install Hermes**. We did not run the installer or the TUI/desktop; observations are source-based.
6. **Partial clone of deep history**. While we unshallowed, the partial clone still required on-demand blob fetching; some files were read only as needed.
7. **No dynamic runtime inspection**. We cannot confirm the exact tool availability, MCP server startup behavior, or gateway concurrency characteristics from static analysis alone.
8. **Limited documentation of specific provider adapters**. We know the platform list but did not read every adapter implementation.
9. **No contributor / governance metrics**. We have commit counts but not detailed author/organization breakdowns.
10. **No performance / token-cost measurements**. The impact of the large tool schema on latency and cost was not measured.

---

*Synthesis saved to: `C:\Users\My\Desktop\asist\TelegramHelper-main\.opencode\memory\research\hermes-agent\iteration-01\synthesis.md`*
