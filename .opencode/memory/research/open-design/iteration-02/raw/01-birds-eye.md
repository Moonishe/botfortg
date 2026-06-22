# Researcher 1 — Bird's Eye Findings

**Repository:** https://github.com/nexu-io/open-design
**Sources fetched:** README.md, AGENTS.md, package.json (from `main` branch, raw.githubusercontent.com)
**Date:** 2026-06-22
**Role:** Researcher 1 (Bird's Eye) — high-level architecture, product positioning, technology stack, ecosystem scale

---

## SUMMARY

Open Design (OD) is a **local-first, open-source alternative to Anthropic's Claude Design** — an agentic design workspace that turns coding-agent CLIs into a design engine. It is a **pnpm monorepo** built on three layers: a **Next.js 16 web frontend**, a **Node 24 Express daemon** (with SQLite via `better-sqlite3`), and an **Electron desktop shell** with sidecar IPC. The project ships **100+ skills**, **150 brand-grade `DESIGN.md` design systems**, **261 official plugins**, and supports **15+ coding-agent CLIs** (with prose claiming "22 local CLIs" total) through a unified MCP server + `od` CLI interface. Licensed **Apache-2.0**. Current version per `package.json` is **0.11.1** (README headline mentions 0.10.0 as the latest named release). The product generates prototypes, live dashboards, decks, images, video (HyperFrames — HTML/CSS/GSAP rendered to MP4 via headless Chrome + FFmpeg), with exports to HTML/PDF/PPTX/MP4/ZIP/Markdown. BYOK (Bring Your Own Key) is supported at every layer via an SSRF-guarded proxy supporting OpenAI, Anthropic, Azure, Google Gemini, Ollama, and any OpenAI-compatible endpoint. The project also offers **Open Design AMR** (Agentic Model Router) — an official hosted model service with 20+ flagship models.

---

## KEY_FINDINGS

### 1. Product Identity & Positioning

- **Tagline:** "The open-source Claude Design alternative" and "Figma alternative for the agent era."
- **Core loop:** brief → plugin → direction → design system → artifact → handoff → memory. Mirrors Anthropic's Claude Design loop (discover brief, lock direction, stream artifact, critique, deliver) but open-source and model-agnostic.
- **Origin context:** Anthropic released Claude Design in April 2026 (per README); OD is the open-source response — same artifact-first mental model, no lock-in.
- **Comparison targets:** Claude Design (closed, paid, cloud-only), Figma (canvas-based, no agent), Lovable/v0/Bolt (cloud agent only). OD differentiates on: open-source Apache-2.0, self-host/desktop, agent-native (runs in user's CLI), brand-grade DESIGN.md, HyperFrames, repo refresh capability.
- **Fellow program:** Recruiting "Open Design Fellows" globally ($1,000/MR, free LLM credits, direct review track).

### 2. Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16 App Router + React 18 + TypeScript |
| Daemon | Node ~24, Express, SSE streaming, `better-sqlite3` (SQLite) |
| Desktop | Electron shell + sandboxed renderer + sidecar IPC |
| Preview | Sandboxed `srcdoc` iframe + streaming `<artifact>` parser |
| Export | HTML (inlined), PDF (browser print), PPTX (agent-driven), ZIP, Markdown, MP4 (HyperFrames) |
| Package manager | pnpm 10.33.2 (via Corepack) |
| Module system | ESM (`"type": "module"`) |
| TypeScript | 5.9.3 |
| Lifecycle | Single entry point: `pnpm tools-dev` (start/stop/run/status/logs/inspect/check) |
| Build/pack | `pnpm tools-pack` (macOS/Windows/Linux builds) |
| Fixture service | `pnpm tools-serve` (deterministic updater metadata) |

### 3. Monorepo Structure (from AGENTS.md)

- **`apps/web`** — Next.js 16 App Router + React 18 web runtime (NOT `apps/nextjs`, which was removed)
- **`apps/daemon`** — local privileged daemon + `od` CLI bin. Owns `/api/*`, agent spawning, skills, design systems, artifacts, static serving
- **`apps/desktop`** — Electron shell; discovers web URL via sidecar IPC
- **`apps/packaged`** — thin packaged Electron runtime entry; starts packaged sidecars, owns `od://` entry glue
- **`packages/contracts`** — pure TypeScript web/daemon app contract layer (DTOs, SSE event unions, error shapes; no Next.js/Express/Node fs/process/browser/SQLite dependencies)
- **`packages/sidecar-proto`** — Open Design sidecar business protocol (constants, namespace validation, IPC message schema, status shapes)
- **`packages/sidecar`** — generic sidecar runtime (bootstrap, IPC transport, path resolution)
- **`packages/platform`** — generic OS process primitives (stamp serialization, command parsing, process matching)
- **`packages/components`** — shared UI primitives (Button, VisuallyHidden, etc.) consumed by web app
- **`tools/dev`** — local development lifecycle control plane
- **`tools/pack`** — packaged build/start/stop/logs, updater harness, installer identity/registry validation
- **`tools/serve`** — local fixture-service control plane
- **`e2e/`** — end-to-end smoke tests + Playwright UI automation
- **Removed/inactive:** `apps/nextjs`, `packages/shared` — do not recreate
- **Content directories:** `skills/`, `design-templates/`, `design-systems/`, `craft/`, `mocks/`, `prompt-templates/`, `plugins/`

### 4. Agent CLI Support (Platform Compatibility)

**MCP install table (15 agents with explicit ✅ Supported):**

1. Claude Code — `od mcp install claude`
2. Codex CLI — `od mcp install codex`
3. Cursor — `od mcp install cursor`
4. VS Code + GitHub Copilot — `od mcp install copilot`
5. GitHub Copilot CLI — `od mcp install copilot`
6. Gemini CLI — `od mcp install gemini`
7. OpenCode — `od mcp install opencode`
8. OpenClaw — `od mcp install openclaw`
9. Antigravity — `od mcp install antigravity`
10. Cline — `od mcp install cline`
11. Trae — `od mcp install trae`
12. Kimi CLI — `od mcp install kimi`
13. Pi Agent — `od mcp install pi`
14. Mistral Vibe CLI — `od mcp install vibe`
15. Hermes Agent — `od mcp install hermes`

**Architecture diagram lists additional agents:** qwen, qoder, kiro, kilo, deepseek (bringing total to ~20+)

**Hero image alt text enumerates 21 CLIs:** Claude Code, Codex, OpenCode, Hermes, Antigravity, Gemini, Grok Build, Kimi, Cursor Agent, Qwen, Qoder, GitHub Copilot, Pi, Kiro, Kilo, Mistral Vibe, DeepSeek, Reasonix, Aider, Devin, Trae

**Prose claims:** "22 local CLIs" (comparison table), "Runs on ... and 22 local CLIs" (What is Open Design section)

**Agent adapter contract:** Lives in `apps/daemon/src/agents.ts`. Adding a new CLI = one entry + stream parser. See `docs/agent-adapters.md`.

**Agent runtime conventions (AGENTS.md):**
- `RuntimeAgentDef.promptInputFormat`: default `'text'` (writes prompt, closes stdin). Claude uses `'stream-json'` (wraps as JSONL, keeps stdin open for mid-turn streaming).
- `claude-stream.ts` emits `turn_end` after iterating content blocks.
- Host asks clarifying questions via `<question-form>` markdown artifact (NOT stdin-injected tool_result). No `AskUserQuestion` tool wiring.

**Communication protocols noted:** ACP (Agent Communication Protocol) for hermes, kimi, vibe; RPC for pi.

**Mock CLIs:** `mocks/` directory has replay-based mock CLIs for opencode, claude, codex, gemini, cursor-agent, deepseek, qwen, grok, and ACP family (devin, hermes, kilo, kimi, kiro, vibe) + AMR vela CLI. Built from anonymized Langfuse traces. Used for agent-stream/parser testing without burning provider budget.

### 5. BYOK Proxy & Model Support

- **Endpoint:** `POST /api/proxy/{anthropic,openai,azure,google,ollama,senseaudio}/stream` (SSE)
- **Supported providers:** OpenAI, Anthropic, Azure OpenAI, Google Gemini, Ollama, LM Studio, vLLM, or any OpenAI-compatible endpoint
- **Security:** Per-target SSRF protection blocks internal IPs / link-local / CGNAT at the daemon edge
- **Open Design AMR (Agentic Model Router):** Official hosted model service — 20+ flagship models (GPT, Claude, Gemini, DeepSeek), zero config, billed by real token usage. Built into the app as of 0.9.0.
- **Daemon binds to `127.0.0.1`** by default. LAN exposure requires explicit `OD_BIND_HOST` + `OD_ALLOWED_ORIGINS`. Connector credentials and live-artifact preview routes stay loopback-only.

### 6. Design Systems (150 shipped)

- **Format:** `DESIGN.md` — single Markdown file with 9-section schema: color, typography, spacing, layout, components, motion, voice, brand, anti-patterns
- **Source:** Originally from `VoltAgent/awesome-design-md` (70 product systems) + `bergside/awesome-design-skills` (57 design skills)
- **Catalog by category:**
  - AI & LLM: claude, cohere, mistral-ai, minimax, together-ai, replicate, runwayml, elevenlabs, ollama, x-ai
  - Developer Tools: cursor, vercel, linear-app, framer, expo, clickhouse, mongodb, supabase, hashicorp, posthog, sentry, warp, webflow, sanity, mintlify, lovable, composio, opencode-ai, voltagent
  - Productivity: notion, figma, miro, airtable, superhuman, intercom, zapier, cal, clay, raycast
  - Fintech: stripe, coinbase, binance, kraken, mastercard, revolut, wise
  - E-commerce: shopify, airbnb, uber, nike, starbucks, pinterest
  - Media: spotify, playstation, wired, theverge, meta
  - Automotive: tesla, bmw, ferrari, lamborghini, bugatti, renault
  - Other: apple, ibm, nvidia, vodafone, resend, spacex
  - Starters: default (Neutral Modern), warm-editorial
- **Extensibility:** Drop a `DESIGN.md` into `design-systems/<brand>/` → picker finds it. Re-import via `scripts/sync-design-systems.ts`.
- **142 design systems also wrapped as plugins** in `plugins/_official/design-systems/`.

### 7. Skills (100+ shipped)

- **Convention:** Claude Code `SKILL.md` format, extended with `od:` frontmatter (mode, platform, scenario, preview.type, design_system.requires, default_for, fidelity, example_prompt)
- **Modes:** prototype, deck, image, video, audio, template, design-system, utility
- **Scenarios:** design, marketing, operation, engineering, product, finance, hr, sale, personal
- **Notable skills:** web-prototype, saas-landing, dashboard, mobile-app, mobile-onboarding, social-carousel, email-marketing, magazine-poster, motion-frames, sprite-animation, pm-spec, team-okrs, eng-runbook, finance-report, hr-onboarding, guizang-ppt (deck default), html-ppt-* (15 templates × 36 themes), hyperframes, critique, tweaks
- **Registry endpoint:** `GET /api/skills`
- **Location:** `skills/` directory + `design-templates/` for rendering catalogue

### 8. Plugins (261 official)

- **Location:** `plugins/_official/`
- **Structure:** `SKILL.md` (required) + optional `open-design.json` manifest (marketplace metadata, inputs, previews, pipelines, capabilities)
- **Categories:**

| Category | Count | Contents |
|---|---|---|
| scenarios/ | 11 | Complete design scenarios (od-default, od-design-refine, od-figma-migration, od-code-migration, od-react-export, od-nextjs-export, od-vue-export, od-media-generation, od-new-generation, od-tune-collab, od-plugin-authoring) |
| image-templates/ | 45 | One-shot image prompts |
| video-templates/ | 50 | HyperFrames / Seedance / Veo motion templates |
| design-systems/ | 142 | Brand DESIGN.md wrapped as plugins |
| atoms/ | 13 | Reusable UI fragments (buttons, heroes, KPI cards) |
| examples/ | 140 | Remixable reference outputs |

- **Plugin manifest spec:** `specVersion` 1.0.0, `od.kind` (skill/scenario/atom/bundle), `od.taskKind` (new-generation/figma-migration/code-migration/tune-collab), `od.mode`, `od.capabilities[]`, `od.inputs[]`
- **CLI commands:** `od plugin list/search/info/install/apply/upgrade/uninstall` (all support `--json`)
- **Registry endpoint:** `GET /api/plugins`
- **Community plugins:** `plugins/community/`
- **Publishing flow:** `plugins/registry/`
- **Plugin spec:** `plugins/spec/SPEC.md`, agent dev guide: `plugins/spec/AGENT-DEVELOPMENT.md`

### 9. Artifact Types & Output Surfaces

1. **Prototypes** — single-page HTML artifacts (web/desktop/mobile), sandboxed iframe, device frames in `assets/frames/`
2. **Live artifacts & dashboards** — editable KPI walls, decision rooms, live dashboards with tweaks panel
3. **Decks** — magazine decks, pitch decks; 15 templates × 36 themes; exports HTML/PDF/PPTX/ZIP/Markdown
4. **Images** — 93 ready-to-replicate prompts in `prompt-templates/`; supports gpt-image-2, ImageRouter, custom API
5. **Video & HyperFrames** — HTML+CSS+GSAP → MP4 via headless Chrome + FFmpeg; 11 HyperFrames templates + 39 Seedance prompts; pairs with Seedance 2.0, Veo 3, Sora 2, Kling 2, Suno v5, Lyria 2

### 10. Architecture (Daemon-Centric)

```
Browser (Next.js 16) / Electron shell
  → /api/* → local daemon (Express + SQLite)
    → /api/skills, /api/plugins, /api/design-systems
    → /api/chat (SSE), /api/proxy/* (BYOK SSE)
    → /api/projects/:id/files/..., /api/artifacts/{save,lint}
    → /api/import/claude-design
    → MCP stdio server
  → spawn(cli, [...], { cwd: managed project cwd })
    → claude, codex, cursor-agent, copilot, openclaw, antigravity, gemini, opencode, qwen, qoder, hermes (ACP), kimi (ACP), pi (RPC), kiro, kilo, vibe (ACP), cline, trae, deepseek
```

- **Sidecar IPC protocol:** STATUS, EVAL, SCREENSHOT, CONSOLE, CLICK, SHUTDOWN
- **Sidecar process stamps:** exactly 5 fields: app, mode, namespace, ipc, source
- **Daemon data directory contract:** `OD_DATA_DIR` → `RUNTIME_DATA_DIR` (resolved in `apps/daemon/src/server.ts`). All daemon data paths derive from it. `PROJECTS_DIR`, `ARTIFACTS_DIR`, SQLite, config, memory, MCP config/tokens, automation state, plugin state, connector credentials, logs. Strict rules — AGENTS.md is the single source of truth.
- **Sanctioned exceptions:** `OD_MEDIA_CONFIG_DIR` (narrow override for media-config.json), `OD_LEGACY_DATA_DIR` (migration source only)
- **No root `pnpm dev`/`pnpm start`** — all lifecycle via `pnpm tools-dev` to ensure consistent env/port/namespace/log paths

### 11. MCP Server Integration

- **Type:** stdio MCP server
- **Install:** `od mcp install <agent>` wires MCP server into agent's config
- **Per-agent install:** places `~/.config/<agent>/open-design.json` + MCP snippet. Cursor gets deeplink, Claude Code gets `claude mcp add-json` one-liner, others get JSON in their schema.
- **Security model:** Read-only by default, daemon binds to `127.0.0.1`, SSRF blocked at proxy edge
- **CLI operations:** `od search-files`, `od get-file`, `od get-artifact`, `od plugin run`, `od skill list --scenario`
- **One-line install:** `curl -fsSL https://open-design.ai/install.sh | sh -s <agent>`

### 12. Capability Exposure (UI/CLI Dual-Track)

- Every user-facing capability must be reachable through **both** web UI and `od` CLI
- Both surfaces call same `/api/*` endpoints; `packages/contracts` carries shared DTOs
- CLI must support `--json` and `--prompt-file <path|->`
- Adding capability = 3-step closure: HTTP endpoint + UI surface + `od` subcommand (all in same PR)
- Reference patterns: `od automation`, `od plugin`, `od ui`, `od project`, `od media`, `od mcp`, `od research`

### 13. Release Channels

| Channel | Purpose |
|---|---|
| beta | Daily R&D/development validation (not part of stable promotion gate) |
| nightly | Internal validation for stable delivery (stable gated by nightly artifacts) |
| preview | Independent early-access with stable-like rigor (uses `X.Y.Z-preview.N` versions) |
| stable | Formal delivery channel (depends on nightly only, not preview) |

- App identity must stay channel-distinct: stable = "Open Design", beta = "Open Design Beta", preview = "Open Design Preview"

### 14. Deployment Options

1. **Desktop app download** (recommended, zero config) — macOS (Apple Silicon + Intel), Windows (x64), Linux (AppImage, optional)
2. **Install into coding agent** (no UI) — `curl -fsSL https://open-design.ai/install.sh | sh -s <agent>`
3. **Docker** — `docker compose up -d` in `deploy/` directory, port 7456
4. **Sealos** — App Store template with persistent storage + Basic Auth
5. **Run from source** — `git clone`, `corepack enable`, `pnpm install`, `pnpm tools-dev run web`

### 15. package.json Details

- **Version:** 0.11.1 (private monorepo root)
- **Bin:** `od` → `./apps/daemon/bin/od.mjs`
- **Scripts:** postinstall, tools-dev, tools-pack, tools-serve, nix:update-hash, guard (includes style-policy, product-neutrality, web-import-isolation, cross-app-imports, fork-pr-workflows, postinstall, lint-craft-references tests), lint:craft, i18n:check, i18n:coverage, sync:community-pets, bake:community-pets, seed:test-projects, seed:curated-design-skills, backfill:failed-runs, typecheck
- **pnpm overrides** (security/compat pinning): brace-expansion 5.0.6, devalue 5.8.1, fast-uri 3.1.2, hono 4.12.19, ip-address 10.2.0, postcss 8.5.15, protobufjs 8.4.0, qs 6.15.2, tmp 0.2.7, yaml 2.9.0
- **onlyBuiltDependencies:** better-sqlite3, core-js, electron, electron-winstaller, esbuild, protobufjs, sharp
- **engines:** node ~24, pnpm >=10.33.2 <11

### 16. Roadmap Status

**Shipped:**
- Daemon + 22 coding-agent CLI adapters + skill registry + design-system catalog
- Web app + chat + question form + 5-direction picker + todo progress + sandboxed preview
- 100+ skills, 150 design systems, 5 visual directions, 5 device frames
- SQLite-backed projects, conversations, messages, tabs, templates
- Multi-provider BYOK proxy + SSRF guard
- Claude Design ZIP import
- Sidecar protocol + Electron desktop + IPC automation
- Artifact lint API + 5-dim self-critique pre-emit gate
- 0.8.0 — Plugin marketplace (261 plugins, manifest spec, per-agent install scripts)
- 0.9.0 — Open Design AMR (Model Router, zero config, one-click sign-in)
- Packaged Electron builds (macOS + Windows + Linux AppImage)

**In progress / planned:**
- Comment-mode surgical edits (partially shipped)
- AI-emitted tweaks panel UX (not yet implemented)
- `npx od init` project scaffolding
- Plugin SDK + `od plugin {add,list,remove,test,publish}` CLI
- Figma/Pencil → React/Next/Vue migration plugins (alpha)
- Refresh-existing-codebase plugin (point at git repo + DESIGN.md)

### 17. Lineage & References

| Project | Role |
|---|---|
| Claude Design (Anthropic) | Closed-source product OD is the alternative to |
| alchaincyf/huashu-design | Design-philosophy compass — junior-designer workflow, brand-asset protocol, anti-AI-slop checklist, 5D critique |
| op7418/guizang-ppt-skill | Magazine-style web PPT skill (bundled verbatim, MIT license preserved) |
| lewislulu/html-ppt-skill | HTML PPT Studio — 15 deck templates, 36 themes, 31 page layouts, animation runtime |
| OpenCoworkAI/open-codesign | First open-source Claude Design alternative; UX patterns borrowed (streaming-artifact loop, sandboxed iframe) |
| multica-ai/multica | Daemon + adapter architecture — PATH-scan agent detection, local daemon as privileged process |
| VoltAgent/awesome-design-md | Source of 9-section DESIGN.md schema and 70 product systems |
| bergside/awesome-design-skills | Source of 57 design skills |
| heygen-com/hyperframes | HTML→MP4 motion-graphics framework (Apache-2.0), first-class integration |
| Claude Code skills (SKILL.md) | Convention adopted verbatim |

### 18. i18n

- **README translations:** 12 languages (English, Español, Português, Deutsch, Français, 简体中文, 繁體中文, 한국어, 日本語, العربية, Русский, Українська, Türkçe)
- **Web app i18n:** 18 locale files (ar, de, en, es-ES, fa, fr, hu, id, ja, ko, pl, pt-BR, ru, th, tr, uk, zh-CN, zh-TW)
- **Typed Dict:** `apps/web/src/i18n/types.ts` — every key must be defined in all 18 locale files (typecheck error if missing)

### 19. Development & Contribution

- **Primary platforms:** macOS, Linux, WSL2. Windows native is best-effort.
- **Node 24 required** (not Node 22 — lockfile/dependency incompatibilities)
- **Windows gotchas:** `corepack enable` fails with EPERM (use `npm install -g pnpm@10.33.2`); `better-sqlite3` has no prebuilt binary for win32/Node 24 (compiles from source via node-gyp, ~2 min, needs VS Build Tools 2022+)
- **Guard checks:** `pnpm guard` runs style-policy, product-neutrality, web-import-isolation, cross-app-imports, fork-pr-workflows, postinstall, and lint-craft-references tests
- **No Co-authored-by trailers** in git commits
- **PR template:** `.github/pull_request_template.md` with Surface area checklist (both UI + CLI must be checked)
- **Bug workflow:** Red spec (falsifiable test that goes red before fix) → cheapest test layer → fix reads as invariant → link issue with Fixes/Closes/Resolves #N

### 20. Notable Discrepancies (Task Description vs. Actual)

| Task claim | Actual finding | Notes |
|---|---|---|
| 24+ agent CLI support | 15 in MCP table, 21 in hero image alt, "22 local CLIs" in prose | Count varies by source; ~15-22 confirmed |
| 151 design systems | "150 brand-grade DESIGN.md systems" in README | Close; may have grown since README written |
| 155+ skills | "100+ skills" in README | Significant gap; README consistently says 100+ |
| 261+ plugins | "261 official plugins" / "261 ready-to-use plugins" | Matches exactly |
| Apache-2.0 | Confirmed in README, package.json, badge | Matches |
| Local-first BYOK | Confirmed extensively | Matches |
| Next.js + Node daemon + Electron | Confirmed: Next.js 16, Node 24 Express daemon, Electron shell | Matches |

---

## CONFIDENCE

**Overall confidence: HIGH (0.85)**

**High confidence (directly verified from source files):**
- Technology stack (Next.js 16, Node 24, Express, SQLite/better-sqlite3, Electron, React 18, TypeScript 5.9.3) — confirmed in README architecture table + AGENTS.md + package.json
- License: Apache-2.0 — confirmed in all three sources
- Monorepo structure (apps/web, apps/daemon, apps/desktop, apps/packaged, packages/*, tools/*, e2e) — confirmed in AGENTS.md
- Plugin count: 261 — confirmed in README (multiple references)
- BYOK proxy endpoints and SSRF protection — confirmed in README
- MCP server + od CLI dual-track — confirmed in README + AGENTS.md
- Release channel model — confirmed in AGENTS.md
- Daemon data directory contract — confirmed in AGENTS.md
- package.json version 0.11.1, engines, scripts, pnpm overrides — confirmed directly

**Medium confidence (single source or ambiguous counting):**
- Agent CLI count: 15-22 depending on source/counting method — the task's "24+" may include agents not listed in the MCP table but referenced elsewhere (Grok Build, Aider, Devin, Reasonix appear in hero image alt but not in the MCP install table)
- Design system count: README says 150, task says 151 — off by one, likely grew
- Skills count: README says 100+, task says 155+ — significant discrepancy; README may be undercounting or task description may be from a different version/fork

**Low confidence / unverifiable from these three files:**
- Actual code quality, test coverage, real-world performance
- Community engagement metrics (Discord member count, star count)
- Whether all 261 plugins are functional or some are stubs
- Whether HyperFrames integration is production-ready
- Security audit status of the BYOK proxy beyond stated SSRF guards
- Actual AMR service reliability and pricing

**Caveats:**
- README headline says 0.10.0 but package.json says 0.11.1 — README may not be fully synced with latest package version
- Some counts in README use "100+" / "150" which may be rounded or stale
- The hero image alt text mentions "14 Media Providers" and "21 Coding Agents" — these are not explicitly enumerated in the README body text beyond what's captured above
