# Hermes Agent — Bird's Eye Overview (Researcher 1)

**Repository:** https://github.com/NousResearch/hermes-agent
**Owner:** Nous Research (GitHub Organization, id 134168893)
**Perspective:** Bird's Eye — Overview
**Research date:** 2026-06-22
**Sources:** README.md, AGENTS.md, pyproject.toml, SECURITY.md, GitHub REST API, official architecture docs (hermes-agent.nousresearch.com/docs/developer-guide/architecture)

---

## 1. What Is This Project? — Definition & Purpose

**Hermes Agent** (tagline: "The agent that grows with you" / "The self-improving AI agent built by Nous Research") is an open-source, single-tenant **personal AI agent** that runs the same agent core across a CLI, a full TUI, an Electron desktop app, and a messaging gateway spanning ~20 platforms (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Email, SMS, DingTalk, Feishu, WeCom, WeChat, QQ, Home Assistant, and more).

Its defining differentiator is a **closed learning loop**: the agent autonomously creates skills from experience, self-improves those skills during use, maintains agent-curated persistent memory with periodic nudges, searches its own past conversations via FTS5, and builds a deepening dialectic user model across sessions (via Honcho). It is compatible with the open **agentskills.io** standard.

It is model-agnostic and provider-agnostic — it works with Nous Portal, OpenRouter (200+ models), NovitaAI, NVIDIA NIM, Xiaomi MiMo, z.ai/GLM, Kimi/Moonshot, MiniMax, Hugging Face, OpenAI, Anthropic, AWS Bedrock, Azure, and custom endpoints. Switching is done via `hermes model` with no code changes and no lock-in.

It is designed to **run anywhere**, not just on a laptop: six terminal backends (local, Docker, SSH, Singularity, Modal, Daytona) with serverless persistence (Modal/Daytona hibernate when idle, costing nearly nothing). Talk to it from Telegram while it works on a cloud VM.

It is also **research-ready**: batch trajectory generation and trajectory compression for training the next generation of tool-calling models.

### Place in the Ecosystem

- Built by **Nous Research**, a well-known open-weights AI research lab (creators of the Hermes/Nous-Hermes fine-tuned model series). Hermes Agent is the agent runtime that complements their model lineup.
- It is the **direct successor to OpenClaw** — the README ships a full migration path (`hermes claw migrate`) that imports SOUL.md, memories, skills, command allowlists, messaging settings, API keys, and TTS assets. Topics include `openclaw`, `clawdbot`, `moltbot`, confirming this lineage.
- It targets the intersection of three categories: (1) terminal/CLI coding agents (à la Claude Code, Codex), (2) personal assistant bots on messaging platforms, and (3) self-improving agent frameworks with persistent memory. No single competitor spans all three the way Hermes does.
- License: **MIT** (permissive, maximum adoption surface).

---

## 2. Key Metrics (as of 2026-06-22, from GitHub REST API)

| Metric | Value |
|---|---|
| Stars | **199,183** (~199k) |
| Forks | **35,383** (~35.4k) |
| Watchers (subscribers) | 786 |
| Contributors | **1,524** |
| Commits (main) | **12,500** |
| Open issues | 22,541 (5k+ issues, 5k+ PRs) |
| Releases | 18 (latest: v0.17.0 / v2026.6.19, Jun 19 2026) |
| Created | 2025-07-22 |
| Last push | 2026-06-22 (updated today) |
| Repo size | ~355 MB |
| Default branch | main |
| Discussions | disabled (community on Discord) |
| Has Pages | yes (Docusaurus docs site) |
| License | MIT |

**Languages:** Python 82.5%, TypeScript 13.5%, JavaScript 1.3%, TeX 0.8%, Shell 0.5%, PowerShell 0.3%, Other 1.1%.

**Test suite:** ~25,000 tests across ~1,250 files (per architecture docs); AGENTS.md notes ~17k tests across ~900 files as of May 2026 — the suite is growing rapidly.

**Adoption signals:** 199k stars in <12 months is exceptional growth, placing Hermes Agent among the fastest-growing open-source AI agent projects on GitHub. The 35k forks and 1,524 contributors indicate a large, active community. PyPI package name: `hermes-agent` (v0.17.0), installed via `uv`/pip or a one-line installer.

---

## 3. Key Components & Their Interaction

### System Overview (from official architecture docs)

```
Entry Points: CLI (cli.py) | Gateway (gateway/run.py) | ACP (acp_adapter/) | Batch Runner | API Server | Python Library
                 |
                 v
        AIAgent (run_agent.py)  <-- single core conversation loop
   | Prompt Builder | Provider Resolution | Tool Dispatch |
   | Compression+Caching | 3 API modes (chat_completions, codex_responses, anthropic) | Tool Registry (70+ tools, 28 toolsets) |
                 |
        v                        v
  Session Storage           Tool Backends
  (SQLite + FTS5)           Terminal (6 backends), Browser (5), Web (4), MCP (dynamic), File, Vision
```

### Core Components

| Component | File(s) | Role |
|---|---|---|
| **AIAgent** | `run_agent.py` (~12k LOC) | The synchronous conversation loop: provider selection, prompt construction, tool execution, retries, fallback, callbacks, compression, persistence. ~60 constructor params. The single core shared by every entry point. |
| **HermesCLI** | `cli.py` (~11k LOC) | Interactive terminal UI. Rich for panels, prompt_toolkit for input/autocomplete, KawaiiSpinner, skin engine theming. |
| **Tool System** | `model_tools.py`, `tools/registry.py`, `toolsets.py` | Central registry; 70+ tools across ~28 toolsets. Auto-discovery via top-level `registry.register()`. Service-gated via `check_fn`. Handlers return JSON strings. |
| **Provider Resolution** | `hermes_cli/runtime_provider.py`, `hermes_cli/auth.py` | Maps `(provider, model)` -> `(api_mode, api_key, base_url)`. 18+ providers, OAuth, credential pools, alias resolution. 3 API modes. |
| **Session Storage** | `hermes_state.py` | SQLite + FTS5 full-text search. Session lineage (parent/child across compressions), per-platform isolation, atomic writes. |
| **Messaging Gateway** | `gateway/run.py`, `gateway/platforms/` (~20 adapters) | Long-running process: message dispatch, user authorization (allowlists + DM pairing), slash commands, hooks, cron ticking, background maintenance. |
| **Plugin System** | `hermes_cli/plugins.py`, `plugins/` | Three discovery sources (user dir, project dir, pip entry points). Registers tools, lifecycle hooks (pre/post tool, pre/post LLM, session start/end), CLI subcommands. Specialized: memory providers, context engines, model-providers. |
| **Memory Providers** | `plugins/memory/` (honcho, mem0, supermemory, byterover, hindsight, holographic, openviking, retaindb) | Pluggable memory backends implementing `MemoryProvider` ABC, orchestrated by `agent/memory_manager.py`. Single-select. |
| **Skills System** | `skills/`, `optional-skills/`, `agent/skill_commands.py` | Procedural memory. Built-in skills (always available) + optional skills (installed explicitly). SKILL.md frontmatter standard. Compatible with agentskills.io. Slash commands injected as user messages (preserves prompt caching). |
| **Cron Scheduler** | `cron/` | First-class agent tasks (not shell tasks). JSON job store, multiple schedule formats, skill/script attachment, delivery to any platform. |
| **TUI** | `ui-tui/` (Ink/React/TypeScript) + `tui_gateway/` (Python JSON-RPC) | Full replacement for classic CLI via `hermes --tui`. TypeScript owns the screen; Python owns sessions/tools/model calls. Newline-delimited JSON-RPC over stdio. |
| **Desktop App** | `apps/desktop/` | Electron + React + nanostore renderer (@assistant-ui/react), separate chat surface talking to tui_gateway over JSON-RPC. |
| **ACP Adapter** | `acp_adapter/` | Exposes Hermes as an editor-native agent (stdio/JSON-RPC) for VS Code, Zed, JetBrains. |
| **Batch Runner / Trajectories** | `batch_runner.py`, `trajectory_compressor.py` | Parallel batch processing; ShareGPT-format trajectory generation for training tool-calling models. |
| **Prompt System** | `agent/prompt_builder.py`, `agent/prompt_caching.py`, `agent/context_compressor.py` | Ordered system-prompt tiers (stable -> context -> volatile). Anthropic cache breakpoints. Lossy summarization when context exceeds thresholds. |
| **Skin Engine** | `hermes_cli/skin_engine.py` | Data-driven CLI theming (pure data, no code changes). Built-in: default, ares, mono, slate. User skins as YAML drops. |

### Key Interaction Patterns

- **Prompt caching is sacred.** Long-lived conversations reuse a cached prefix every turn. Anything mutating past context invalidates the cache and multiplies cost. The one exception is explicit context compression.
- **Core is a narrow waist; capability lives at the edges.** New capability arrives via plugins/skills/MCP, not core tools. Every core tool is sent on every API call, so the bar is high. The "Footprint Ladder" (extend existing -> CLI+skill -> service-gated tool -> plugin -> MCP server -> new core tool) governs this.
- **Platform-agnostic core.** One AIAgent class serves CLI, gateway, ACP, batch, and API server. Platform differences live in entry points.
- **Profile isolation.** Each profile (`hermes -p <name>`) gets its own HERMES_HOME, config, memory, sessions, gateway PID. Multiple profiles run concurrently.
- **Delegation.** `delegate_task` spawns isolated subagents for parallel workstreams; Python scripts can call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns.

### Data Flow Examples

**CLI:** User input -> HermesCLI.process_input() -> AIAgent.run_conversation() -> prompt_builder -> runtime_provider -> API call -> tool_calls? -> model_tools.handle_function_call() -> loop -> final response -> display -> SessionDB.

**Gateway:** Platform event -> Adapter.on_message() -> MessageEvent -> GatewayRunner._handle_message() -> authorize user -> resolve session -> create AIAgent with history -> run_conversation() -> deliver response back through adapter.

**Cron:** Scheduler tick -> load due jobs -> fresh AIAgent (no history) -> inject skills -> run job prompt -> deliver to target platform -> update job state.

---

## 4. Competitors & Alternatives (Comparison Table)

Hermes Agent sits at the intersection of coding agents, messaging bots, and self-improving agent frameworks. No single competitor covers all three.

| Project | Type | Self-improving (skills/memory) | Messaging platforms | Multi-provider | Runs anywhere (serverless) | License | Notes vs Hermes |
|---|---|---|---|---|---|---|---|
| **Hermes Agent** | Personal AI agent (CLI+TUI+desktop+messaging) | Yes (skills creation, self-improvement, Honcho user model) | ~20 (Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Email, SMS, DingTalk, Feishu, WeCom, WeChat, QQ, HA...) | Yes (18+ providers) | Yes (6 terminal backends, Modal/Daytona serverless) | MIT | The reference; only one with closed learning loop + messaging + serverless |
| **OpenClaw** | Personal AI agent (CLI+messaging) | Partial (memory, skills) | Several (Telegram, Discord...) | Yes | Partial | Open | Direct predecessor; Hermes ships migration tool. Hermes is the evolution. |
| **Claude Code** (Anthropic) | Terminal coding agent | No (no skill creation loop) | No | No (Anthropic only) | No | Proprietary | Topic-tagged as a competitor. Coding-focused, single provider. |
| **OpenAI Codex CLI** | Terminal coding agent | No | No | No (OpenAI only) | No | Proprietary | Topic-tagged (`codex`). Coding-focused. |
| **Aider** | Terminal coding agent (pair programming) | Partial (repo map, conventions) | No | Yes (many LLMs) | No | Apache-2.0 | Coding-focused; no messaging, no skill loop. |
| **Goose** (Block) | Local AI agent | Partial (sessions) | No | Yes | No | Apache-2.0 | Desktop/local; no messaging gateway, no skill creation. |
| **OpenHands** (ex-OpenDevin) | Autonomous SWE agent | No | No | Yes | Yes (Docker) | MIT | Coding/agent tasks; no personal-assistant or messaging angle. |
| **Letta** (ex-MemGPT) | Memory-centric agent framework | Yes (memory core) | No | Yes | No | Apache-2.0 | Closest on memory/self-improvement, but framework-oriented, no messaging/CLI product. |
| **Continue.dev** | IDE coding assistant | No | No | Yes | No | Apache-2.0 | IDE-integrated; no agent loop, no messaging. |
| **Cursor** | AI IDE | No | No | Partial | No | Proprietary | IDE product; different category. |
| **Devin** (Cognition) | Autonomous SWE agent | No | No | No | Yes (cloud) | Proprietary | Cloud SWE; closed, paid. |
| **Telegram/Discord bot frameworks** | Messaging bots | No | 1-2 platforms | Varies | Varies | Varies | Single-platform, no agent loop or skill system. Hermes is a superset. |

**Key differentiators of Hermes Agent vs all listed:**
1. The only project with a **closed learning loop** (autonomous skill creation + self-improvement + dialectic user modeling) that ALSO runs on messaging platforms AND is provider-agnostic.
2. ~20 messaging platform adapters from a single gateway process — unmatched breadth.
3. Serverless persistence (Modal/Daytona) — agent hibernates when idle, near-zero idle cost.
4. Backed by a model lab (Nous Research) with tight integration to the Hermes model lineup + Nous Portal.
5. Research-ready trajectory generation for training.

---

## 5. Who Uses It? — Known Companies / Projects

- **Nous Research** (builder) — AI research lab known for open-weights models (Nous-Hermes, Hermes-Pro series). Hermes Agent is their agent runtime; tightly integrated with Nous Portal (their hosted model + tool gateway subscription).
- **NVIDIA** — explicitly referenced in SECURITY.md as a partner via **NVIDIA OpenShell** (per-session sandboxes with declarative FS/network/process/inference policy), listed as a supported whole-process wrapping posture. NVIDIA NIM (Nemotron) is a listed provider.
- **NovitaAI** — listed provider (AI-native cloud for Model API, Agent Sandbox, GPU Cloud).
- **OpenRouter** — listed provider (200+ models).
- **Plastic Labs** — creators of **Honcho** (dialectic user modeling), integrated as a memory provider and referenced in the README.
- **Community integrations:**
  - `computer-use-linux` (by avifenesh) — Linux desktop-control MCP server for Hermes.
  - **HermesClaw** (by AaronWong1999) — community WeChat bridge running Hermes + OpenClaw on the same WeChat account.
- **Packaging channels:** Homebrew tap, Nix flake, AUR, Termux — indicating adoption across Linux distros, macOS, and Android.
- **End users:** Positioned as a *personal* agent for individuals (single-tenant by design). The 199k stars / 35k forks indicate very large individual developer adoption, though no enterprise case studies are published in the repo. The Nous Portal subscription model targets individual power users who want one subscription instead of collecting API keys.

No Fortune-500 enterprise adoption is publicly documented in the repository; the trust model explicitly states "single-tenant personal agent" and the security posture is oriented toward individual operators, not multi-tenant enterprise deployments.

---

## 6. Security Posture (from SECURITY.md)

- **Single-tenant personal agent.** The only load-bearing security boundary is the **operating system**. In-process heuristics (approval gate, output redaction, Skills Guard) are explicitly NOT boundaries — they catch cooperative-mode mistakes, not adversarial output.
- Two OS-level isolation postures: (1) terminal-backend isolation (container/remote/sandbox for shell+file tools), (2) whole-process wrapping (Docker Compose, or NVIDIA OpenShell with declarative policy + Provider store credentials).
- Credential scoping: env filtered for lower-trust subprocesses (shell, MCP, cron, code-exec); credentials stripped by default. Not containment — in-process components can read what the agent reads.
- External surfaces (gateway adapters, HTTP API, dashboard, ACP, TUI gateway) require authorization at every trust-boundary crossing; allowlists required for network-exposed adapters.
- **No bug bounty program.** 90-day coordinated disclosure window. Report via GHSA or security@nousresearch.com.
- **Supply-chain defense is a first-class concern:** all dependencies are exact-pinned (==X.Y.Z) or bounded (>=floor,<next_major). Policy was tightened after the litellm compromise and the "Mini Shai-Hulud" worm campaign (May 2026, malicious mistralai 2.4.6 on PyPI). Lazy-install for opt-in backends reduces blast radius. Multiple CVE pins are documented inline in pyproject.toml.

---

## 7. Technology Stack (from pyproject.toml)

- **Python 3.11–3.13** (capped `<3.14` pending Rust-backed transitive cp314 wheels).
- **Build:** setuptools>=77 (PEP 639 SPDX license), uv for env/lock management.
- **Core deps (exact-pinned):** openai==2.24.0, httpx[socks], pydantic==2.13.4, rich, prompt_toolkit, tenacity, pyyaml/ruamel.yaml, jinja2, fastapi+uvicorn, websockets, Pillow, psutil, croniter, PyJWT[crypto], packaging, Markdown.
- **Optional extras (lazy-installed):** anthropic, exa, firecrawl, fal, edge-tts, modal, daytona, messaging (python-telegram-bot, discord.py, slack-bolt, aiohttp), matrix (mautrix[encryption]), bedrock (boto3), azure-identity, mistral, voice (faster-whisper), honcho, google-workspace, youtube, web (dashboard), teams, dingtalk, feishu, computer-use, acp, nemo-relay.
- **TypeScript stack (TUI/desktop/website):** Ink (React), nanostores, xterm.js, @assistant-ui/react, Docusaurus. Tooling: tsc, eslint, prettier, vitest.
- **Dev deps:** debugpy, pytest, pytest-asyncio, mcp, starlette, ty (ty type checker), ruff, setuptools.
- **Entry points:** `hermes` (hermes_cli.main:main), `hermes-agent` (run_agent:main), `hermes-acp` (acp_adapter.entry:main).
- **Linting:** ruff (preview mode, PLW1514 encoding rule load-bearing). Type checking via ty (Python 3.13 environment).

---

## 8. Maturity & Velocity Assessment

- **Age:** ~11 months (created Jul 2025). 12,500 commits and 18 releases in that window = extremely high velocity (~34 commits/day averaged, release cadence ~ every ~3 weeks).
- **Contributor base:** 1,524 contributors — large and diversified, not a single-vendor project despite Nous Research leadership. Top contributors include teknium1 (Nous), OutThisLife, kshitijk4poor, and the `claude` bot account (indicating AI-assisted development).
- **Issue/PR volume:** 22,541 open issues, 5k+ open PRs — very high engagement but also a large backlog. The AGENTS.md describes an "automated triage sweeper" for PR triage, indicating the team uses automation to manage scale.
- **Internationalization:** README translated to Chinese (zh-CN), Urdu (ur-PK), Spanish (es). SECURITY.md also in Spanish. locales/ shipped as data files. Indicates global community reach.
- **Documentation:** Full Docusaurus site at hermes-agent.nousresearch.com/docs with ~15 doc sections covering quickstart, CLI, config, messaging, security, tools, skills, memory, MCP, cron, context files, architecture, agent loop, prompt assembly, compression/caching, gateway internals, session storage, provider runtime, programmatic integration. High documentation maturity.

---

## 9. Risks & Open Questions (for downstream research)

- **Open issues backlog (22.5k)** may indicate quality/scaling challenges at this growth rate.
- **Single-tenant by design** — not suited for multi-user/multi-tenant enterprise SaaS without significant wrapping (though OpenShell is referenced for production).
- **Trust model caveat** — the project is explicit that in-process heuristics are not security boundaries; users handling untrusted input (web, email, multi-user channels) must adopt whole-process wrapping. This is well-documented but easy to miss for casual users.
- **Dependency on Nous Portal** as the recommended "easy path" — the project remains fully open and provider-agnostic, but the business model nudges toward Nous's hosted subscription.
- **Relative youth** (~11 months) for a project at this scale — API stability and long-term maintenance commitments are not yet proven over multiple years.

---

## 10. Confidence Notes

- **High confidence:** project definition, metrics (from GitHub API), components (from AGENTS.md + architecture docs), security posture (from SECURITY.md), tech stack (from pyproject.toml), competitor positioning (from README topics + feature comparison).
- **Medium confidence:** specific company/user adoption — only Nous Research, NVIDIA (OpenShell), NovitaAI, OpenRouter, Plastic Labs (Honcho) are explicitly named; broader enterprise adoption is not documented.
- **The raw.githubusercontent.com fetches initially failed** with transport errors but succeeded on retry; the GitHub API and docs site were fully accessible. All key files (README, AGENTS.md, pyproject.toml, SECURITY.md) were obtained and analyzed.
