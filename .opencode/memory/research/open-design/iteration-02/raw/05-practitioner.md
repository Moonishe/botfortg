# Researcher 5 (Practitioner) — Open Design Deep Research

**Repository:** https://github.com/nexu-io/open-design
**Sources fetched:** README.md, QUICKSTART.md, AGENTS.md (supplementary), craft/README.md (supplementary)
**Date:** 2026-06-22
**Focus:** Desktop app usage, agent integration (curl install), MCP server (od mcp install), Docker self-hosted, plugin/skill creation, DESIGN.md generation, code-to-design migration, craft/ pattern for universal rules

---

## SUMMARY

Open Design (OD) is an open-source, local-first, agent-native alternative to Anthropic's Claude Design. Version 0.10.0. It ships as a **native desktop app** (macOS/Windows Electron), a **CLI** (`od`), a **stdio MCP server**, and a **Docker image** — all four surfaces consume the same daemon HTTP API (`/api/*`). The core loop: `brief → plugin → direction → design system → artifact → handoff → memory`. Agents read `SKILL.md` + `DESIGN.md` + `craft/*.md`, emit `<artifact>` HTML, which renders in a sandboxed iframe and exports to HTML/PDF/PPTX/MP4.

Three composability axes: **skills/** (artifact shape), **design-systems/** (brand visual language via 9-section DESIGN.md), **craft/** (universal brand-agnostic craft rules). All three are plain files. 100+ skills, 150 design systems, 261 plugins ship in-repo. Supports 22+ coding-agent CLIs (Claude Code, Codex, Cursor, Copilot, OpenCode, Gemini, Hermes, Kimi, etc.) plus BYOK proxy for any OpenAI-compatible endpoint.

**Key practitioner findings:**
- Desktop app is zero-config, auto-detects agent CLIs on PATH, ships 100+ skills + 150 design systems out of the box.
- Agent integration is a single `curl | sh` one-liner per agent, or `od mcp install <agent>` for MCP wiring.
- Docker self-host requires one env var (`OD_API_TOKEN`) and exposes port 7456.
- Plugin creation needs only a `SKILL.md`; marketplace listing adds `open-design.json` manifest.
- DESIGN.md is a 9-section Markdown schema (no theme JSON) — drop a file, the picker finds it.
- Code-to-design migration via dedicated plugins: `od-code-migration`, `od-figma-migration`, `od-react-export`, `od-nextjs-export`, `od-vue-export`.
- `craft/` is a third axis: universal rules a skill opts into via `od.craft.requires` frontmatter — token-efficient, forward-compatible, partially auto-enforced via lint-artifact.ts.

---

## USAGE_PATTERNS

### 1. Desktop App Usage (Electron)

**Install:** Download from open-design.ai or GitHub Releases. macOS (Apple Silicon + Intel x64), Windows (x64), Linux AppImage (optional lane).

**First run behavior:**
- Auto-detects every coding-agent CLI on `PATH` (claude, codex, cursor-agent, copilot, gemini, opencode, etc.)
- Loads 100+ skills and 150 design systems automatically
- Defaults to `web-prototype` skill + `Neutral Modern` design system
- Entry view: pick skill + design system + type brief → hit Send

**Core pages:**
- **Home** — entry point, pick skill + design system + brief
- **Automation** — orchestrate repeatable design workflows (schedulable)
- **Design System** — distill team's DESIGN.md into brand contract
- **Plugin** — browse/install/distribute workflow plugins
- **Integrations** — connect external systems + MCP tools
- **Studio** (per-project) — prototype, HyperFrame, deck, image artifacts

**Desktop architecture:**
- Electron shell + sandboxed renderer + sidecar IPC
- IPC commands: STATUS, EVAL, SCREENSHOT, CONSOLE, CLICK, SHUTDOWN
- Desktop discovers web URL via sidecar IPC (from tools-dev launch status, not port guessing)
- Packaged app identity is channel-distinct: `Open Design` (stable), `Open Design Beta` (beta), `Open Design Preview` (preview)

**Execution modes (picker in UI):**
| Mode | Flow |
|---|---|
| Local CLI (default) | Frontend → daemon `/api/chat` → `spawn(<agent>)` → stdout → SSE → artifact parser → preview |
| API mode (fallback) | Frontend → daemon `/api/proxy/{provider}/stream` → provider SSE → artifact parser → preview |

Both feed the same `<artifact>` parser and same sandboxed iframe.

**Prompt composition (every send):**
```
BASE_SYSTEM_PROMPT (output contract: wrap in <artifact>, no code fences)
  + active design system body (DESIGN.md — palette/type/layout)
  + active skill body (SKILL.md — workflow and output rules)
  + craft sections (injected above skill body, per od.craft.requires)
```

### 2. Agent Integration (curl install)

**One-line install (16+ CLIs):**
```bash
curl -fsSL https://open-design.ai/install.sh | sh -s <agent>
# <agent> = claude | codex | cursor | copilot | openclaw | antigravity | gemini
#         | pi | vibe | hermes | cline | kimi | trae | opencode
```

**What the installer does:**
- Places `~/.config/<agent>/open-design.json` (or platform equivalent)
- Adds copy-paste MCP snippet in the agent's native config schema
- Cursor gets a one-click deeplink
- Claude Code gets a `claude mcp add-json` one-liner
- Every other agent gets JSON in the schema its config expects

**Usage inside the agent (no GUI needed):**
```
> Use open-design to generate a landing page with the Linear design system
```
Agent reads `skills/`, picks the right `SKILL.md`, binds the `DESIGN.md` you named, emits `<artifact>` previewable at `http://localhost:7456`.

**Agent spawning mechanics (daemon side):**
- Daemon scans `PATH` + common user toolchain directories for agent CLIs
- `spawn(cli, [...], { cwd: managed project cwd })` — agent reads SKILL.md + DESIGN.md, writes artifacts to disk
- `RuntimeAgentDef.promptInputFormat`: `'text'` (default, writes prompt + closes stdin) or `'stream-json'` (Claude only, keeps stdin open for mid-turn streaming)
- Adding a new CLI = one adapter entry + stream parser in `apps/daemon/src/agents.ts`

**Daemon environment variables injected into spawned agents:**
- `OD_BIN` — absolute path to `apps/daemon/dist/cli.js`
- `OD_DAEMON_URL` — running daemon URL (must be real port like 7457, not :0)
- `OD_PROJECT_ID` — active project id
- `OD_PROJECT_DIR` — active project's file directory

**BYOK proxy (no CLI installed):**
- `POST /api/proxy/{anthropic,openai,azure,google,ollama,senseaudio}/stream`
- Paste `baseUrl` + `apiKey` + `model`
- Supports OpenAI, Anthropic, Azure OpenAI, Google Gemini, Ollama, LM Studio, vLLM, or any OpenAI-compatible endpoint
- SSRF protection blocks internal IPs / link-local / CGNAT at daemon edge

### 3. MCP Server (`od mcp install`)

**Install per agent:**
```bash
od mcp install <agent>           # wire MCP server into agent config
od mcp install <agent> --print   # dry-run preview
od mcp install <agent> --uninstall  # remove
od mcp install --help             # full list
```

**Supported agents:** claude, codex, cursor, copilot, gemini, opencode, openclaw, antigravity, cline, trae, kimi, pi, vibe, hermes, and more.

**MCP tools exposed (CLI commands the agent calls):**
```bash
od search-files "primary button"          # search files across projects
od get-file design-systems/linear-app/DESIGN.md
od get-artifact <slug>                    # latest rendered artifact
od plugin run web-prototype --brief "..."
od skill list --scenario marketing
```

**Why MCP over export/zip:** Exporting and re-attaching a zip every iteration breaks flow. MCP exposes the design source directly — the agent always sees the live file, not a stale export.

**Security model:**
- Read-only by default
- Daemon binds to `127.0.0.1` (loopback)
- SSRF blocked at proxy edge
- LAN exposure requires explicit `OD_BIND_HOST` + `OD_ALLOWED_ORIGINS`
- Connector credentials and live-artifact preview routes stay loopback-only regardless

**Settings UI:** Settings → MCP server in the desktop app shows per-agent install flow.

### 4. Docker Self-Hosted

**Setup:**
```bash
git clone https://github.com/nexu-io/open-design.git
cd open-design/deploy
cp .env.example .env
echo "OD_API_TOKEN=$(openssl rand -hex 32)" >> .env
docker compose up -d
# open http://localhost:7456
```

**Environment variables (deploy/.env):**
```env
OPEN_DESIGN_PORT=7456                          # Port exposed on host
OPEN_DESIGN_MEM_LIMIT=384m                     # Container memory limit
OPEN_DESIGN_ALLOWED_ORIGINS=https://yourdomain.com  # CORS origins
OPEN_DESIGN_IMAGE=ghcr.io/nexu-io/od:latest   # Docker image tag
OD_API_TOKEN=<32-byte hex>                     # Required API token
```

**Common Docker commands:**
```bash
docker compose logs -f           # view logs
docker compose restart           # restart
docker compose down              # stop
docker compose pull && docker compose up -d  # update
docker compose down -v           # remove all data
```

**macOS Docker Desktop gotcha:** If web UI shows `Authorization: Bearer <OD_API_TOKEN> required`, Docker Desktop bridge networking makes daemon see requests as non-loopback. Fix: enable host networking, use `network_mode: host`. See deploy/README.md.

**Sealos deploy:** One-click via Sealos App Store — runs published Docker image with persistent workspace storage + Basic Auth on public proxy.

**nginx reverse proxy (if used):** Must keep SSE routes unbuffered and uncompressed, or browser gets `net::ERR_INCOMPLETE_CHUNKED_ENCODING` after 80-90s:
```nginx
location /api/ {
    proxy_pass http://127.0.0.1:7456;
    proxy_buffering off;
    gzip off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Daemon-only production mode:** Daemon serves static Next.js export itself at `http://localhost:7456` — no reverse proxy involved.

### 5. Plugin / Skill Creation

#### Skills

**Structure:** Folder under `skills/` with `SKILL.md` (Claude Code SKILL.md convention) + optional `assets/` + `references/`.

**Frontmatter schema (od: extension):**
```yaml
---
name: my-skill
description: What this skill produces
od:
  mode: prototype          # prototype | deck | image | video | audio | template | design-system | utility
  platform: web            # platform target
  scenario: marketing      # design | marketing | operation | engineering | product | finance | hr | sale | personal
  preview:
    type: html
  design_system:
    requires: false        # or true if a design system is mandatory
  default_for: prototype   # if this is the default skill for a mode
  fidelity: high
  craft:
    requires: [typography, color, anti-ai-slop]  # opt into universal craft rules
  example_prompt: "A landing page for a SaaS product"
---
```

**Skill modes:** `prototype` (web/mobile/desktop single-page), `deck` (horizontal-swipe presentations), `image`, `video`, `audio`, `template`, `design-system`, `utility`.

**Skill registry endpoint:** `GET /api/skills`

**Drop a folder in, restart daemon, it appears in the picker.**

#### Plugins

**Minimum structure:**
```
my-plugin/
├── SKILL.md            ← required: YAML frontmatter + trigger phrasing + workflow (< 500 lines)
├── open-design.json    ← needed for marketplace listing
├── README.md           ← optional
├── preview/            ← optional (strongly recommended for visual plugins)
└── examples/           ← optional
```

**open-design.json core fields:**
```json
{
  "specVersion": "1.0.0",
  "name": "my-plugin",
  "version": "1.0.0",
  "compat": {
    "agentSkills": [{ "path": "./SKILL.md" }]
  },
  "od": {
    "kind": "skill",           // skill | scenario | atom | bundle
    "taskKind": "new-generation",  // new-generation | figma-migration | code-migration | tune-collab
    "mode": "prototype",       // prototype | deck | live-artifact | image | video | hyperframes | audio | design-system | scenario
    "capabilities": ["prompt:inject"],  // declare minimum; restricted install grants only prompt:inject by default
    "inputs": []               // apply-time parameters
  }
}
```

**Plugin CLI commands:**
```bash
od plugin list                                # list installed (--task-kind / --mode / --tag filters)
od plugin search "landing page"               # search by keyword
od plugin info od-default                     # inspect metadata, inputs, capabilities
od plugin install od-figma-migration          # install from registry; also ./local-folder or https://...
od plugin apply od-default --input brief="..."# apply a plugin
od plugin upgrade od-default                  # upgrade
od plugin uninstall od-default                # uninstall
od plugin scaffold --id my-plugin --title "My Plugin"  # generate skeleton
od plugin validate ./my-plugin               # check manifest / file layout
```
All commands support `--json` for piping through `jq`/`xargs`.

**Validation:**
```bash
od plugin validate ./my-plugin
pnpm guard && pnpm --filter @open-design/plugin-runtime typecheck
```

**Plugin registry endpoint:** `GET /api/plugins`

**Official plugin categories (261 total):**
| Category | Count | Contents |
|---|---|---|
| scenarios/ | 11 | od-default, od-design-refine, od-figma-migration, od-code-migration, od-react-export, od-nextjs-export, od-vue-export, od-media-generation, od-new-generation, od-tune-collab, od-plugin-authoring |
| image-templates/ | 45 | One-shot image prompts |
| video-templates/ | 50 | HyperFrames / Seedance / Veo motion templates |
| design-systems/ | 142 | Brand DESIGN.md wrapped as plugins |
| atoms/ | 13 | Reusable UI fragments (buttons, heroes, KPI cards) |
| examples/ | 140 | Remixable reference outputs |

**Contributing:** Drop into `plugins/community/` (third-party) or `plugins/_official/` (bundled). Pass validation. Fill PR template. Publish to external registries via `plugins/spec/PUBLISHING-REGISTRIES.md`.

### 6. DESIGN.md Generation

**Schema:** 9-section Markdown file — no theme JSON.
1. Color
2. Typography
3. Spacing
4. Layout
5. Components
6. Motion
7. Voice
8. Brand
9. Anti-patterns

**150 brand-grade systems ship in-repo** at `design-systems/<brand>/`. Categories: AI & LLM, Developer Tools, Productivity, Fintech, E-commerce, Media, Automotive, Other, Starters.

**Adding a custom brand:** Drop a `DESIGN.md` into `design-systems/<brand>/`. The picker finds it automatically. Switch a system → next render uses new tokens. No rebuild needed.

**Source:** 9-section schema and 70 product systems imported from `VoltAgent/awesome-design-md`. Re-import via `scripts/sync-design-systems.ts`.

**In the generation loop:** DESIGN.md body is injected into the system prompt as the second layer (after BASE_SYSTEM_PROMPT, before SKILL.md body). Bodies are cached in-memory per session (single daemon fetch per pick).

**Starters:** `default` (Neutral Modern), `warm-editorial`.

**Roadmap:** `npx od init` to scaffold a project with DESIGN.md — not yet implemented.

### 7. Code-to-Design Migration

**Dedicated plugins (in plugins/_official/scenarios/):**

| Plugin | What it does |
|---|---|
| `od-code-migration` | Point at a `git` repo + `DESIGN.md` → agent refactors real components to brand spec → get a PR |
| `od-figma-migration` | Migrate Figma / Pencil workflows → React / Next.js / Vue source |
| `od-react-export` | Export artifact to React components |
| `od-nextjs-export` | Export artifact to Next.js |
| `od-vue-export` | Export artifact to Vue |
| `od-design-refine` | Refine existing design with critique loop |

**Workflow (from README):**
1. Hand a `git` repo + `DESIGN.md` to the agent
2. Agent refactors your real components to the brand spec
3. Dedicated plugins migrate Figma/Pencil workflows into React/Next/Vue code

**Full workflow — brief to artifact:**
`brief → plugin → direction → design system → artifact → handoff → memory`
1. PM submits a brief (plugin picker offers landing page, pitch deck, dashboard, etc.)
2. Designer/agent locks direction (pick from 5 curated directions, or drop screenshot/URL → agent connects GitHub, imports Figma, codifies DESIGN.md)
3. Agent emits first `<artifact>` (plugin + skill + DESIGN.md bound, streams into sandboxed iframe, editable in place)
4. Hand off to engineering (artifact is real HTML/CSS — drop into Cursor/Codex/Claude Code) or export PPTX/PDF/MP4 to marketing
5. OD gets smarter — screenshots, fonts, palettes, confirmed artifacts accumulate as defaults

**Status:** Figma/Pencil → React/Next/Vue migration plugins are alpha. Refresh-existing-codebase plugin is on roadmap (partially working).

### 8. craft/ Pattern for Universal Rules

**Concept:** `craft/` is a third composability axis alongside `skills/` and `design-systems/`.

| Axis | Scope | Example |
|---|---|---|
| `skills/` | Artifact shape | `saas-landing`, `dashboard`, `pricing-page` |
| `design-systems/` | Brand visual language (9-section DESIGN.md) | `linear-app`, `apple`, `notion` |
| `craft/` | **Universal** craft knowledge — true regardless of brand | letter-spacing rules, accent-overuse caps, anti-AI-slop |

**DESIGN.md** tells the agent which colors/fonts a brand uses. **craft/** tells the agent the universal rules a competent designer applies on top — e.g. ALL CAPS always needs ≥0.06em tracking, regardless of brand.

**How a skill opts in:**
```yaml
od:
  craft:
    requires: [typography, color, anti-ai-slop]
```
Only the listed sections are injected into the system prompt (above the active skill body). Token-efficient — a skill that needs only typography pays no token cost for color/motion content.

**Layered stack for editorial skills:**
```yaml
od:
  craft:
    requires: [typography, typography-hierarchy, typography-hierarchy-editorial]
```

**Craft files (11 sections):**
| File | Section name | When to require |
|---|---|---|
| `typography.md` | `typography` | Any skill that emits typed content (~all) |
| `typography-hierarchy.md` | `typography-hierarchy` | Skills where hierarchy must feel authored — strong entry point, varied levels, intentional rhythm |
| `typography-hierarchy-editorial.md` | `typography-hierarchy-editorial` | Sustained reading surfaces: blog-post, docs-page, digital-eguide. Requires typography + typography-hierarchy |
| `color.md` | `color` | Any skill that emits styled output (~all) |
| `anti-ai-slop.md` | `anti-ai-slop` | Marketing pages, landing pages, decks |
| `state-coverage.md` | `state-coverage` | Skills with stateful UI (dashboards, mobile apps, forms, list/table views) |
| `animation-discipline.md` | `animation-discipline` | Skills that ship motion (mobile apps, multi-screen flows, gamified UI, transitions) |
| `accessibility-baseline.md` | `accessibility-baseline` | Skills with interactive UI (dashboards, forms, mobile flows, focus/labels/keyboard) |
| `rtl-and-bidi.md` | `rtl-and-bidi` | Skills with localized text/layout (Arabic/Hebrew/Persian) |
| `form-validation.md` | `form-validation` | Skills with interactive forms (lead capture, sign-in, signup, settings, multi-step intake) |
| `laws-of-ux.md` | `laws-of-ux` | Skills hitting cognitive limits (pricing=Hick's, dashboards=Pareto, onboarding=Goal-Gradient) |

**Enforcement levels:**
- **Auto-checked:** Rules wired into `apps/daemon/src/lint-artifact.ts` — currently the P0 list in `anti-ai-slop.md` (Tailwind-indigo accent, two-stop hero gradients, emoji-as-icons, etc.). Linter reports findings to UI (P0/P1 badges) and to agent (system reminder for self-correction). Artifact persistence is NOT hard-blocked on P0 hits.
- **Guidance:** The rest. Agent reads rules, reviewers apply them, linter doesn't check.

**Validation:**
```bash
pnpm lint:craft    # reports unresolved slugs with manifest paths; typos can't silently drop a section
```
Unknown values are silently ignored (forward-compatible) — a skill authored today can list a planned slug and benefit the moment the matching `craft/<slug>.md` ships. Intentional forward references go in `craft/FUTURE_SECTIONS.md`.

**Attribution:** Craft content adapted from MIT-licensed [refero_skill](https://github.com/referodesign/refero_skill) project (© Refero Design), with edits to fit OD's house style and link back to OD's design tokens (`var(--accent)` etc.) instead of generic Tailwind hex values.

**PR review lane:** Code review guide includes a dedicated "craft additions" review lane alongside design-system additions and skill additions.

---

## CONFIG_EXAMPLES

### Minimal skill with craft opt-in
```yaml
# skills/my-skill/SKILL.md
---
name: my-skill
description: A SaaS landing page with hero, features, pricing, and CTA
od:
  mode: prototype
  scenario: marketing
  design_system:
    requires: false
  craft:
    requires: [typography, color, anti-ai-slop, accessibility-baseline]
  example_prompt: "A landing page for a developer tools SaaS"
---
Workflow body here...
```

### Minimal plugin manifest (open-design.json)
```json
{
  "specVersion": "1.0.0",
  "name": "my-landing-plugin",
  "version": "1.0.0",
  "compat": {
    "agentSkills": [{ "path": "./SKILL.md" }]
  },
  "od": {
    "kind": "skill",
    "taskKind": "new-generation",
    "mode": "prototype",
    "capabilities": ["prompt:inject"],
    "inputs": [
      {
        "name": "brief",
        "type": "string",
        "required": true,
        "description": "The landing page brief"
      }
    ]
  }
}
```

### Docker .env
```env
OPEN_DESIGN_PORT=7456
OPEN_DESIGN_MEM_LIMIT=384m
OPEN_DESIGN_ALLOWED_ORIGINS=https://yourdomain.com
OPEN_DESIGN_IMAGE=ghcr.io/nexu-io/od:latest
OD_API_TOKEN=<generated via openssl rand -hex 32>
```

### Daemon environment (injected into spawned agents)
```bash
OD_BIN=/path/to/apps/daemon/dist/cli.js
OD_DAEMON_URL=http://127.0.0.1:7457
OD_PROJECT_ID=<uuid>
OD_PROJECT_DIR=/path/to/project/files
```

### nginx SSE proxy
```nginx
location /api/ {
    proxy_pass http://127.0.0.1:7456;
    proxy_buffering off;
    gzip off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### System prompt composition (runtime)
```
BASE_SYSTEM_PROMPT   (output contract: wrap in <artifact>, no code fences)
  + active design system body  (DESIGN.md — palette/type/layout)
  + craft sections              (craft/*.md — per od.craft.requires, injected above skill body)
  + active skill body           (SKILL.md — workflow and output rules)
```

### Plugin CLI automation (JSON piping)
```bash
# List all prototype plugins as JSON, extract names, apply each
od plugin list --mode prototype --json | jq -r '.[].name' | xargs -I {} od plugin apply {} --input brief="..."
```

---

## CONFIDENCE

| Area | Confidence | Notes |
|---|---|---|
| Desktop app usage | **High** | README + QUICKSTART provide detailed first-run behavior, execution modes, prompt composition. Electron architecture confirmed in AGENTS.md. |
| Agent integration (curl install) | **High** | Exact curl one-liner documented. 16+ agents listed with status. Installer behavior (config file placement, per-agent schema) detailed. Daemon spawn mechanics from AGENTS.md. |
| MCP server (od mcp install) | **High** | Exact CLI commands documented. MCP tools listed. Security model clear. `--print`/`--uninstall` flags confirmed. |
| Docker self-hosted | **High** | Complete setup steps, env vars, common commands, macOS gotcha, nginx config all from QUICKSTART. Sealos deploy option confirmed. |
| Plugin/skill creation | **High** | Both structures fully documented. open-design.json field set listed. CLI commands (scaffold, validate, list, search, apply, upgrade, uninstall) all confirmed. 261 official plugins categorized. |
| DESIGN.md generation | **High** | 9-section schema confirmed. 150 systems cataloged. Drop-in workflow clear. Source attribution to VoltAgent/awesome-design-md. Prompt injection layer documented. |
| Code-to-design migration | **Medium-High** | Plugin names confirmed (od-code-migration, od-figma-migration, od-react-export, etc.). Workflow described. BUT: README roadmap marks Figma/Pencil migration as "alpha" and refresh-existing-codebase as partially working — some features may be incomplete. |
| craft/ pattern | **High** | Fully documented in craft/README.md (fetched). Three-axis model clear. 11 section files listed with use cases. Enforcement levels (auto-checked vs guidance) explained. Frontmatter opt-in syntax confirmed. Attribution to refero_skill. Validation via `pnpm lint:craft`. Forward-compatibility design decision explained. |

**Overall confidence: High.** All eight focus areas covered with primary-source documentation. The only caveat is code-to-design migration plugins being explicitly marked alpha/in-progress on the roadmap. The craft/ pattern — the most novel architectural concept — is thoroughly documented with clear examples, enforcement levels, and design rationale.
