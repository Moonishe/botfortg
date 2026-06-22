# 02 — Deep Dive: core architecture and data flow

## Output contract
- **SUMMARY**: Hermes is a synchronous, tool-calling agent loop wrapped in a broad adapter layer. The core is `AIAgent` → `agent.conversation_loop.run_conversation` → LLM → `model_tools.handle_function_call` → tool registry → result → loop. Memory, skills, gateway, terminal backends, MCP, and cron are all externalized to keep the model-facing schema as small as possible.
- **CHANGES**: None.
- **EVIDENCE**: `run_agent.py:320-490`, `agent/conversation_loop.py:469-620`, `tools/registry.py`, `toolsets.py`, `agent/memory_manager.py`, `agent/memory_provider.py`, `agent/prompt_builder.py`, `tools/skill_manager_tool.py`, `tools/session_search_tool.py`, `tools/mcp_tool.py`, `tools/terminal_tool.py`, `gateway/run.py`, `hermes_cli/commands.py`.
- **RISKS**: The core loop is a single-threaded synchronous while-loop inside a large function; the 12k-line `run_agent.py` and 17k-line `gateway/run.py` are historical god-files that have been partially extracted but still carry enormous scope. Tool schema bloat, race conditions in gateway agent caching, and plugin/MCP arbitrary code execution are real concerns.
- **BLOCKERS**: None.

## Core agent loop (verbatim from `agent/conversation_loop.py`)
```python
def run_conversation(agent, user_message, ...):
    # ... prologue: build_turn_context, sanitize, system prompt, memory prefetch
    while (api_call_count < agent.max_iterations and agent.iteration_budget.remaining > 0) \
            or agent._budget_grace_call:
        if agent._interrupt_requested: break
        # budget/interrupt/callback handling
        # LLM call
        # if tool_calls: execute each, append results, loop
        # else: return final_response
        # context compression if needed
```
The loop is entirely synchronous; async handlers are bridged via `tools.registry._run_async`.

## `AIAgent` facade
- Defined in `run_agent.py` (now ~5.5k LOC, down from the historical 12k).
- `__init__` forwards to `agent.agent_init.init_agent` with ~60 parameters (provider, model, toolsets, callbacks, session IDs, budget, credentials, checkpoints, etc.).
- Public methods: `chat(message)` (returns string), `run_conversation(...)` (returns dict with `final_response`, `messages`, `api_calls`, `completed`, `failed`, `error`).
- The actual loop body was extracted to `agent/conversation_loop.py` (4.5k LOC).

## Tool system: registry + toolsets
### `tools/registry.py`
- `ToolRegistry` singleton (`registry`).
- `ToolEntry` holds `name`, `toolset`, `schema`, `handler`, `check_fn`, `requires_env`, `is_async`, `emoji`, `max_result_size_chars`, `dynamic_schema_overrides`.
- `register()` accepts `override=True` only for intentional plugin replacements; rejects shadowing between toolsets unless both are MCP toolsets.
- `get_definitions()` filters by `check_fn()` (cached 30s TTL) and returns OpenAI-format function schemas.
- `dispatch()` runs handlers, catches exceptions, returns JSON error strings.
- Helper exports: `tool_error()`, `tool_result()`.

### `toolsets.py`
- Static `TOOLSETS` dict with 35+ toolsets.
- `_HERMES_CORE_TOOLS` is the shared default bundle for CLI/messaging platforms.
- `get_toolset(name)`, `resolve_toolset(name)` (recursive with cycle detection), `resolve_multiple_toolsets`, `get_all_toolsets`, `validate_toolset`, `create_custom_toolset`.
- Plugin/MCP toolsets are discovered dynamically from the registry.

### Tool discovery
- `discover_builtin_tools()` in `tools/registry.py` parses every `tools/*.py` with a top-level `registry.register(...)` call via AST.
- Auto-imports them; failures are logged but not fatal.
- `model_tools.py` triggers discovery and wires schemas into the agent.

## Memory system
### `agent/memory_provider.py`
- `MemoryProvider` ABC: `is_available`, `initialize`, `system_prompt_block`, `prefetch`, `queue_prefetch`, `sync_turn`, `get_tool_schemas`, `handle_tool_call`, `shutdown`, plus optional hooks.
- Built-in provider name is `"builtin"`; external providers: Honcho, Hindsight, Mem0, SuperMemory, ByteRover, Holographic, OpenViking, RetainDB (all in `plugins/memory/`).

### `agent/memory_manager.py`
- `MemoryManager` orchestrates one built-in + one external provider.
- Rejects a second external provider; core tool names are reserved and cannot be shadowed by memory tools.
- Uses a background `ThreadPoolExecutor` for `sync_turn`/`prefetch`.
- Routes memory tool calls via `_tool_to_provider` map.

### Built-in memory files
- `~/.hermes/memories/MEMORY.md` (facts about the user/world)
- `~/.hermes/memories/USER.md` (user profile)
- Updated via the `memory` tool.

## Skills system
- Skills are markdown-based procedural memory: `~/.hermes/skills/<category>/<skill>/SKILL.md`.
- Frontmatter carries `name`, `description`, `version`, `author`, `platforms`, `required_environment_variables`, `metadata.hermes.tags/category/related_skills/fallback_for_toolsets/requires_toolsets`.
- `agent/prompt_builder.py:build_skills_system_prompt()` builds a compact skill index for the system prompt with a two-layer cache (in-process LRU + disk snapshot `.skills_prompt_snapshot.json`).
- Conditional activation: `fallback_for_toolsets` hides a skill when the primary tool is available; `requires_toolsets` hides it when dependencies are missing.
- `tools/skill_manager_tool.py` provides `skill_manage` (create, edit, patch, delete, write/remove supporting files). Agent-created skills are scanned only when `skills.guard_agent_created` is enabled (default off).
- `tools/skills_hub.py` (not copied) handles optional-skills browsing/install.

## Session store & search
- `hermes_state.py` → SQLite `state.db` with WAL, FTS5 virtual table, schema v16.
- Stores sessions, messages, model config, parent_session_id chains (for branching/compression), source tags (`cli`, `telegram`, etc.).
- `tools/session_search_tool.py` provides three modes: `DISCOVERY` (FTS5 + anchored windows), `SCROLL` (around a message id), `BROWSE` (recent sessions). No LLM calls.

## Terminal / execution environments
- `tools/terminal_tool.py` dispatches to backends in `tools/environments/`.
- Backends: `local`, `docker`, `ssh`, `singularity`, `modal`, `daytona`, `managed_modal`.
- Config in `config.yaml`: `terminal.backend`, `terminal.cwd`, `terminal.timeout`, container images, resource limits, volume mounts, persistent shell, etc.
- Approval gate (`tools/approval.py`) detects destructive commands and prompts for approval; supports `YOLO` mode (`HERMES_YOLO_MODE`) but it is frozen at import time.

## MCP integration
- `tools/mcp_tool.py` connects external MCP servers via stdio, HTTP/StreamableHTTP, SSE.
- Long-lived background asyncio loop in a daemon thread; tools are registered dynamically into the registry with `mcp-<server>` toolset prefix.
- Supports sampling (server-initiated LLM calls), parallel tool calls, per-server timeouts, env var filtering, credential stripping.
- Config key: `mcp_servers` in `config.yaml`; optional-mcps catalog in `optional-mcps/`.

## Gateway / messaging
- `gateway/run.py` `GatewayRunner` manages platform adapters, message routing, cron delivery, agent cache (LRU 128, idle TTL 1h), approval hooks, and gateway slash commands.
- `gateway/platforms/` contains one adapter per platform; each platform is a plugin or built-in module.
- Session state is persisted via `gateway/session.py` and `hermes_state.py`.
- Gateway knows the central `COMMAND_REGISTRY` from `hermes_cli/commands.py`.

## CLI / TUI / Desktop
- `hermes_cli/commands.py` defines the central `COMMAND_REGISTRY` of `CommandDef` objects. CLI, gateway, Telegram, Slack, autocomplete, and help all derive from this.
- `hermes_cli/main.py` is the `hermes` CLI entry point.
- `ui-tui/` is an Ink/React terminal UI; `tui_gateway/` is the Python JSON-RPC backend.
- `apps/desktop/` is an Electron + React + `@assistant-ui/react` app with its own composer/transcript.
- `web/` is a dashboard SPA that embeds the real TUI via xterm.js over a WebSocket PTY.

## Cron / scheduled automations
- `cron/jobs.py` and `cron/scheduler.py` implement the scheduler.
- Cron jobs are delivered to any platform (Telegram, Discord, Slack, etc.) via natural language prompts.
- `tools/cronjob_tools.py` exposes `cronjob` tool for create/list/pause/resume/remove/trigger.

## Data flow (one user turn)
```
User message
  ↓
AIAgent.run_conversation()
  ↓ build_turn_context()
      - restore system prompt
      - load skills index
      - memory prefetch (builtin + external provider)
      - context files / soul / environment hints
      - sanitize messages
  ↓
while loop:
  LLM chat.completions.create(model, messages, tools)
  ↓
  if text: persist session, return final_response
  if tool_calls:
    for each tool_call:
      model_tools.handle_function_call()
      ↓ tools.registry.dispatch(name, args)
      ↓ handler (terminal/web/search/memory/...)
      ↓ JSON result appended to messages
  ↓
  context compression if near token limit
  ↓
Memory sync_turn() (background)
```

## Key design strengths
1. **Prompt-caching aware**: system prompt is stable, skills are indexed once, context mutations are minimized.
2. **Plugin/skill-first**: new capabilities are pushed to edges, not to the core tool schema.
3. **Tool registry is clean**: single source of truth for schemas, availability, dispatch, overrides.
4. **Memory provider abstraction**: pluggable recall without forking the core.
5. **Cross-platform**: Windows is treated as a first-class target, not a WSL afterthought.

## Key design concerns
1. **God files remain**: `gateway/run.py` (17k), `hermes_cli/main.py` (13k), `hermes_cli/config.py` (6.6k), `agent/conversation_loop.py` (4.5k). Refactors are happening but the inertia is visible.
2. **Synchronous core loop**: all async work is bridged; heavy concurrency is handled by subagents/threads, not the main loop.
3. **Tool schema size**: `_HERMES_CORE_TOOLS` already contains ~40 names; even with `check_fn` gating, the schema is large and every core tool is sent on every call.
4. **Agent cache in gateway**: 128-entry LRU with 1h idle TTL; shared mutable state across concurrent gateway sessions requires careful locking.
5. **Arbitrary code execution by design**: terminal, execute_code, skills, plugins, and MCP servers all run code; the security model relies on OS isolation, not in-process gates.
