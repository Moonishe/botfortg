# 02 Deep Dive — Architecture, daemon, skills, design systems, plugins

## SUMMARY
Open Design is a three-layer agentic design runtime: a web UI (Next.js 16 + Electron), a local daemon (Node/Express/SSE), and a pool of user-installed coding-agent CLIs. The daemon is the source of truth for sessions, skills, design systems, artifacts, and MCP. Skills are portable `SKILL.md` folders (Claude Code convention) with optional `od:` frontmatter. Design systems are single-file `DESIGN.md` documents following a 9-section schema. Plugins are the newest evolution: portable skill folders with an `open-design.json` manifest that can carry marketplace metadata, inputs, capabilities, and preview pipelines.

## CHANGES
No code changes; read-only research.

## EVIDENCE
Tools used:
- `read` of `docs/architecture.md`, `docs/skills-protocol.md`, `apps/daemon/src/runtimes/registry.ts`, `design-systems/default/DESIGN.md`, `design-templates/web-prototype/SKILL.md`, `plugins/README.md`.
- `glob` of `apps/daemon/src/*.ts` (daemon source), `skills/*/SKILL.md`, `design-systems/*/DESIGN.md`, `plugins/_official/**/open-design.json`.
- `grep` for `sandbox|iframe|SSRF|CORS|auth|token|secret` across daemon TypeScript to confirm security surface.

### Architecture
Three deployment topologies (docs/architecture.md §1):
1. **Fully local** — browser → Next.js dev server → daemon on `:7456` → spawns `claude`/`codex`/etc.
2. **Vercel + local daemon** — browser → Vercel, websocket/tunnel to user's daemon.
3. **Vercel + direct API** — no daemon, keys in browser localStorage, limited features.

Logical component diagram (docs/architecture.md §2):
- Web app: chat pane, artifact tree, preview iframe, comment/slider overlay.
- Transport: daemon SSE or API-direct.
- Daemon: session manager, agent adapters, skill registry, design-system resolver, artifact store, export pipeline.
- Filesystem: daemon data root, skills, DESIGN.md.

### Daemon
The daemon is `apps/daemon/src/server.ts` and is exposed as the `od` CLI (`apps/daemon/src/cli.ts`). Key responsibilities:
- HTTP/SSE routes under `/api/*` (health, agents, skills, design-systems, projects, chat, artifacts, import, proxy).
- Agent adapter pool via `apps/daemon/src/runtimes/registry.ts` (24 base adapters including amr, claude, codex, devin, gemini, opencode, hermes, trae, grok, kimi, cursor, qwen, qoder, copilot, amp, pi, kiro, kilo, vibe, deepseek, aider, antigravity, reasonix, codebuddy).
- Skill registry scanning three locations: `./.claude/skills/` (project-private), `./skills/` (project), `~/.claude/skills/` (user-global).
- Design-system resolver: `./DESIGN.md`, `./design-system/DESIGN.md`, or user path.
- Artifact store: plain files + `history.jsonl` (not SQLite for artifacts, though SQLite is used elsewhere).
- Preview pipeline: sandboxed iframe `srcdoc`, Babel transform for JSX, CSS inlining for export.
- MCP stdio server (`apps/daemon/src/mcp*.ts`) for external agent integration.

### Skills
`docs/skills-protocol.md` defines the skill format:
- Base: Claude Code `SKILL.md` convention (YAML frontmatter + Markdown body).
- Optional OD extensions under `od:`: `mode`, `preview`, `design_system`, `craft`, `inputs`, `parameters`, `outputs`, `capabilities_required`.
- Modes: `prototype`, `deck`, `template`, `design-system`, plus `image`, `video`, `audio`, `utility`.
- Scenarios: `design`, `marketing`, `operation`, `engineering`, `product`, `finance`, `hr`, `sale`, `personal`.
- Example: `design-templates/web-prototype/SKILL.md` declares `mode: prototype`, `platform: desktop`, `scenario: design`, `design_system.requires: true`, `preview.entry: index.html`, and a workflow that composes from `assets/template.html` + `references/layouts.md`.

### Design Systems
`design-systems/default/DESIGN.md` (Neutral Modern) follows the 9-section schema:
1. Visual Theme & Atmosphere
2. Color Palette & Roles
3. Typography Rules
4. Component Stylings
5. Layout Principles
6. Depth & Elevation
7. Do's and Don'ts
8. Responsive Behavior
9. Agent Prompt Guide

The resolver injects the DESIGN.md as a system-prompt prefix and as a `DESIGN.md` file in the agent's CWD. 151 systems ship (default, warm-editorial, vercel, linear-app, stripe, airbnb, apple, tesla, etc.).

### Plugins
`plugins/_official/` contains 261+ first-party plugins organized as:
- `scenarios/` — complete workflows (od-default, od-design-refine, od-figma-migration, od-code-migration, od-react-export, od-nextjs-export, od-vue-export, od-media-generation, od-new-generation, od-tune-collab, od-plugin-authoring, etc.)
- `image-templates/` — 45+ one-shot image prompts
- `video-templates/` — 50+ HyperFrames/Seedance/Veo motion templates
- `design-systems/` — 142 brand DESIGN.md plugins
- `atoms/` — 13 reusable UI fragments
- `examples/` — 140 remixable reference outputs

A plugin needs `SKILL.md` + optional `open-design.json` (manifest with `specVersion`, `name`, `version`, `od.kind`, `od.taskKind`, `od.mode`, `od.capabilities`, `od.inputs`).

## RISKS
- **Adapter fragility**: The registry depends on per-CLI stream parsers (Claude's stream-json, ACP for Kimi/Hermes, etc.). Format changes in upstream CLIs can break adapters.
- **Skill security**: Skills run under the agent's permission model; a malicious skill in `~/.claude/skills/` could instruct the agent to damage the filesystem. The repo mitigates with install-time warnings and agent-level permissions, but OD does not invent its own sandbox.
- **Plugin manifest complexity**: The plugin spec (open-design.json) adds a second metadata layer on top of SKILL.md; divergence between the two could cause marketplace indexing issues.
- **Desktop folder-import HMAC gate**: The security model relies on a desktop main-process secret and HMAC token; a race or missing env (`OD_REQUIRE_DESKTOP_AUTH`) could relax the gate (docs/architecture.md §Folder import).
- **iframe sandbox**: Preview uses `<iframe sandbox="allow-scripts">` without `allow-same-origin`, which isolates artifacts but still allows script execution; agent-generated scripts are not further sandboxed.

## BLOCKERS
- `apps/daemon/src/agents.ts` is only a re-export barrel; the actual adapter definitions are in `apps/daemon/src/runtimes/defs/*.ts`. Only the registry list was read, not every individual adapter definition.
- The exact SSE event shapes and tool-call schemas are in `packages/contracts/src` and were not fully read.
- The plugin runtime (`packages/plugin-runtime`) and registry protocol (`packages/registry-protocol`) were not deeply inspected; their exact capability model is inferred from spec docs.
- No live execution of the daemon or any CLI was performed; behavior is inferred from source.
