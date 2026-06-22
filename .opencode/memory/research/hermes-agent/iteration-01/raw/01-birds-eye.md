# 01 — Bird's Eye: Hermes Agent at a glance

## Output contract
- **SUMMARY**: Nous Research's Hermes Agent is a large, self-improving personal AI agent monorepo. It pairs a Python core (agent loop, tool registry, SQLite session store, memory/skills, gateway) with TypeScript/React surfaces (TUI, desktop app, dashboard, website). The repository is extremely active and organized around a "narrow core + plugin/skill edges" philosophy.
- **CHANGES**: None (research-only pass).
- **EVIDENCE**: `README.md`, `AGENTS.md`, `pyproject.toml`, `package.json`, `git ls-tree` of the cloned repo, GitHub release page HTML, local file-count statistics.
- **RISKS**: Huge surface area (40+ tools, 20+ messaging platforms, many optional backends) makes consistent security, testing, and documentation hard. The codebase is a fast-moving target.
- **BLOCKERS**: GitHub API rate-limited; star/fork counts taken from the README context and verified by release page metadata. `VISION.md`/`CHANGELOG.md` do not exist in the repo root.

## Repository metadata
- **Repository**: https://github.com/NousResearch/hermes-agent
- **Version in pyproject.toml**: 0.17.0
- **Latest release tag**: `v2026.6.19` (v0.17.0, "The Reach Release")
- **Main-line commits**: ~11,857 (main branch)
- **Commits across all refs**: ~12,638
- **Version tags**: 18 (`v2026.3.12` … `v2026.6.19`) plus 6 non-version tags
- **Tracked files**: 5,137
- **Stars/forks**: README context reports ~199k stars / ~35.3k forks (GitHub API was rate-limited during this run; verified the repo is the canonical NousResearch/hermes-agent).

## Language / file footprint (from `git ls-tree -r HEAD`)
| Extension | Count |
|-----------|------:|
| .py       | 2,319 |
| .md       | 1,363 |
| .ts       |   591 |
| .tsx      |   306 |
| .yaml     |    98 |
| .json     |    56 |
| .png      |    56 |
| .sh       |    24 |
| .rs       |     9 |
| .html     |     9 |

> Python dominates the backend (≈82.5% by file count), TypeScript/React the frontend (≈13.5% by file count).

## Top-level directories and what they own
| Directory | Purpose |
|-----------|---------|
| `agent/` | Core agent internals: conversation loop, prompt builder, context compressor, memory manager/providers, auxiliary client, retry/budget, model metadata, LSP integration, image routing, etc. |
| `tools/` | Self-registering tool implementations and the central `registry.py`. Includes `environments/` (local, Docker, SSH, Modal, Daytona, Singularity), MCP client, browser, code execution, delegation, cron, skills, session search, terminal, etc. |
| `gateway/` | Messaging gateway: `run.py` (17k+ LOC), `session.py`, `platforms/` (Telegram, Discord, Slack, WhatsApp, Signal, Email, SMS, Matrix, Mattermost, Feishu, WeCom/WeChat, QQ, DingTalk, Yuanbao, BlueBubbles, iMessage/Photon, webhook, API server, etc.). |
| `hermes_cli/` | CLI argument parser, config management, slash-command registry, setup wizard, doctor, gateway, MCP catalog, skills hub, skin engine, dashboard auth, etc. |
| `tui_gateway/` | Python JSON-RPC backend for the Ink/React TUI. |
| `ui-tui/` | Ink/React terminal UI. |
| `apps/` | Electron desktop app. |
| `web/` | Dashboard SPA + API. |
| `skills/` | Built-in bundled skills (research, devops, github, media, productivity, smart-home, etc.). |
| `optional-skills/` | Official but not-default skills (blockchain, creative, security, etc.). |
| `plugins/` | Memory providers, model providers, browser/image/context providers, dashboard auth, kanban, observability, platforms, etc. |
| `providers/` | Provider model profiles (legacy path) + pluginized model providers. |
| `cron/` | Scheduler and cron job definitions. |
| `tests/` | Pytest suite (reportedly ~17k tests across ~900 files). |
| `website/` | Docusaurus documentation site. |
| `docker/` / `nix/` / `packaging/` | Container, Nix, and packaging configs. |

## Key entry points
| File | Role |
|------|------|
| `run_agent.py` | `AIAgent` class and `main()` entry point; forwards the core loop to `agent/conversation_loop.py`. |
| `cli.py` | `HermesCLI` interactive terminal orchestrator. |
| `model_tools.py` | Tool orchestration: `get_tool_definitions`, `handle_function_call`. |
| `toolsets.py` | Static toolset definitions + `get_toolset` / `resolve_toolset`. |
| `tools/registry.py` | Central tool registry singleton (`ToolRegistry`, `ToolEntry`). |
| `hermes_state.py` | SQLite session store with FTS5, WAL, schema v16. |
| `hermes_logging.py` | Profile-aware logging setup. |
| `hermes_constants.py` | `get_hermes_home()` / `display_hermes_home()`. |
| `hermes_cli/main.py` | `hermes` CLI entry point. |
| `gateway/run.py` | Gateway runner. |
| `batch_runner.py` | Parallel batch / trajectory generation. |
| `trajectory_compressor.py` | Trajectory compression for training data. |
| `mcp_serve.py` | Hermes as an MCP server. |

## Quick architectural philosophy (from `AGENTS.md`)
- **Per-conversation prompt caching is sacred.** The system prompt must stay byte-stable; avoid mutating past context or swapping toolsets mid-conversation.
- **Core is a narrow waist; capability lives at the edges.** Every new core tool is sent on every API call, so additions are expensive. Prefer: existing code → CLI+skill → service-gated tool → plugin → MCP server → new core tool.
- **Skills are procedural memory; memory files are declarative.** Skills capture *how to do* a task; `MEMORY.md`/`USER.md` capture facts and preferences.
- **Cross-platform by default.** Native Windows is a first-class citizen alongside Linux/macOS.

## Tools/platforms summary
- **40+ tools** enumerated in `_HERMES_CORE_TOOLS`: web search/extract, terminal/process, file ops, vision, image generation, browser automation, TTS, todo, memory, session search, clarify, code execution, delegation, cronjob, send_message, Home Assistant, kanban, computer use.
- **Toolsets**: `web`, `search`, `browser`, `terminal`, `file`, `vision`, `image_gen`, `tts`, `skills`, `memory`, `cronjob`, `messaging`, `delegation`, `code_execution`, `safe`, `coding`, `hermes-cli`, `hermes-telegram`, `hermes-discord`, `hermes-gateway`, etc.
- **Messaging platforms**: Telegram, Discord, Slack, WhatsApp, Signal, Email, SMS, Matrix, Mattermost, Feishu, DingTalk, WeCom/WeChat, QQ, Yuanbao, BlueBubbles, Home Assistant, iMessage/Photon, Raft, webhook, API server.
- **Terminal backends**: local, Docker, SSH, Singularity, Modal, Daytona, plus NVIDIA OpenShell.

## Dependency posture (from `pyproject.toml`)
- Python 3.11–3.13.
- Core deps are **exact-pinned** (`==`) after supply-chain incidents (litellm, Mini Shai-Hulud worm). Optional backends are lazy-installed via `tools/lazy_deps.py`.
- Node 20+ workspace with root `package.json` and apps/ui-tui/web sub-workspaces.

## Research notes
- `VISION.md` and `CHANGELOG.md` do not exist in the repo root; release notes live on GitHub release pages and in `website/docs/`.
- The repository is far larger than the typical AI-agent project; the real complexity is in the integration matrix, not any single algorithm.
