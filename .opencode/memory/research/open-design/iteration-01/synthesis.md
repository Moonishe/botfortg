# Open Design — Iteration 01 Synthesis

## OUTPUT CONTRACT

### SUMMARY
Open Design is a mature, open-source (Apache-2.0) agentic design workspace. It couples a Next.js 16 web app with a local Node/Express daemon, an Electron desktop shell, and a portable skill/plugin/design-system content model. It supports 24+ coding-agent CLIs, 151 design systems, 155+ skills, and 261+ official plugins. The product is local-first, BYOK, and file-based: the agent reads `SKILL.md` + `DESIGN.md` and writes real HTML/CSS/JS artifacts that render in a sandboxed iframe and export to HTML/PDF/PPTX/MP4.

### CHANGES
No source-code changes were made in this research pass. The output is a set of research artifacts saved under `.opencode/memory/research/open-design/iteration-01/raw/` and this synthesis.

### EVIDENCE
- `webfetch` on README.md, AGENTS.md, CLAUDE.md, CONTEXT.md, QUICKSTART.md, package.json, CHANGELOG.md.
- `git clone --depth 1` of the repository into `%LOCALAPPDATA%\Temp\opencode\open-design`.
- `read` of local files: README.md, pnpm-workspace.yaml, docs/architecture.md, docs/skills-protocol.md, docs/roadmap.md, QUICKSTART.md, LICENSE, PRIVACY.md, AGENTS.md, plugins/README.md, apps/daemon/src/runtimes/registry.ts, design-systems/default/DESIGN.md, design-templates/web-prototype/SKILL.md.
- `glob` for top-level structure, daemon sources, skills, design-systems, design-templates, plugin manifests.
- `grep` for license, security, sandbox, auth, token, and TODO patterns.
- `bash` for repo metrics (file counts, directory counts, git metadata).

### RISKS
- High: runs user-installed agent CLIs and agent-generated code on the user's machine; OD intentionally inherits the agent's permission model rather than adding its own sandbox.
- High: 24+ agent adapters must track upstream CLI stream formats; format drift is a constant risk.
- Medium: telemetry opt-in still sends prompt/tool content, even if redacted.
- Medium: Windows native build is best-effort; better-sqlite3 compiles from source.
- Medium: AMR (official model service) is a paid product family path inside the open-source project.
- Low: license mix (Apache-2.0, MIT, LGPL for 7zip binaries) is documented but needs care in packaged redistribution.

### BLOCKERS
- Shallow clone prevents full git-history analysis and contributor attribution.
- No live execution of the daemon, web app, or any agent was performed.
- No audit of individual agent adapter definitions, MCP authorization, or AMR terms.
- README advertises v0.10.0 while package.json shows v0.11.0; release notes should be checked for the latest stable.

---

## SYNTHESIS BODY

### SUMMARY
Open Design is a local-first, open-source alternative to Anthropic's Claude Design. It is a pnpm monorepo (Node ~24) with a Next.js 16 web app, a Node/Express daemon, an Electron desktop shell, and a content layer of skills, design systems, design templates, and plugins. The current version on main is 0.11.0 (Apache-2.0). The project is large (8,523 tracked files, 25 top-level directories) and actively developed (0.6 → 0.7 → 0.8 → 0.9 → 0.10 → 0.11 in rapid succession). It is explicitly designed to be consumed by the coding agents already on the user's machine (Claude Code, Codex, Cursor, OpenCode, Kimi, Hermes, etc.) via a stdio MCP server and one-line install scripts.

### KEY_FINDINGS
1. **Agent-native, not agent-replacing.** OD does not ship its own LLM; it orchestrates the user's installed CLIs or a BYOK API endpoint. The daemon is the only privileged process.
2. **Brand-grade by default.** Every render injects a 9-section `DESIGN.md` as a system-prompt prefix. 151 brand systems ship (Linear, Stripe, Vercel, Apple, etc.).
3. **Skills are portable files.** The `SKILL.md` convention is borrowed from Claude Code. Optional `od:` frontmatter adds mode, preview type, inputs, parameters, craft references, and capability gating.
4. **Plugin-first evolution.** Since 0.8.0, skills have been promoted to first-class plugins with `open-design.json` manifests, marketplace metadata, inputs, and capability declarations. 261+ official plugins ship.
5. **Three deployment topologies.** Fully local (default), Vercel + local daemon tunneled, or Vercel + direct API (degraded, no daemon).
6. **Sandboxed iframe preview.** Artifacts render in `<iframe sandbox="allow-scripts">` without `allow-same-origin`, isolating them from the host app.
7. **Rich export surface.** HTML, PDF, PPTX, ZIP, Markdown, and MP4 (via HyperFrames) are supported depending on the skill.
8. **MCP server embeddability.** `od mcp install <agent>` wires the daemon's read/write/search/generation tools into any MCP-compatible agent.
9. **Strong local-first privacy stance.** Telemetry is opt-in, BYOK keys stay local, AMR is a separate first-party service.
10. **Strict contribution boundaries.** `AGENTS.md` is a constitution-like file: no root `pnpm dev`, no `apps/nextjs`, daemon data paths must derive from `RUNTIME_DATA_DIR`, UI/CLI dual-track required for every feature.

### ARCHITECTURE

| Layer | Technology | Responsibility |
|---|---|---|
| Frontend | Next.js 16 App Router + React 18 + TypeScript | Chat, artifact tree, iframe preview, settings, comments, sliders |
| Daemon | Node 24 + Express + SSE + `better-sqlite3` | `/api/*`, agent adapters, skill registry, design-system resolver, artifact store, MCP server |
| Desktop | Electron + sidecar IPC | Thin shell around web + daemon; `od://` protocol, auto-updater, folder-import HMAC gate |
| Storage | Plain files + `history.jsonl` (artifacts); SQLite (projects/conversations) | Daemon data root governed by `AGENTS.md` |
| Preview | Sandboxed `srcdoc` iframe | HTML artifacts load raw; JSX artifacts use vendored React 18 + Babel standalone |
| Export | Puppeteer, pptxgenjs, archiver, HyperFrames | HTML/PDF/PPTX/ZIP/Markdown/MP4 |
| Lifecycle | `pnpm tools-dev` | Single entry point for start/stop/run/status/logs/inspect |

**Daemon components:**
- `server.ts` — HTTP/SSE route registration, static serving.
- `runtimes/registry.ts` — 24 base agent definitions (amr, claude, codex, devin, gemini, opencode, hermes, trae, grok, kimi, cursor, qwen, qoder, copilot, amp, pi, kiro, kilo, vibe, deepseek, aider, antigravity, reasonix, codebuddy).
- `skills.ts` — skill registry scanning, frontmatter parsing, `od:` extensions.
- `design-systems.ts` — DESIGN.md resolver and prompt injection.
- `mcp*.ts` — MCP server, OAuth, tokens, install info, agent install, live artifacts.
- `routines.ts`, `automation-*.ts` — scheduled/unattended agent runs.
- `memory*.ts` — auto-memory store across runs and projects.
- `run-artifact-fs.ts`, `finalize-design.ts`, `handoff-design.ts` — artifact lifecycle.

**Skills/Plugins/Design-Systems relationship:**
- **Skills** = agent design taste (workflow instructions).
- **Design systems** = brand contract (tokens, typography, layout, components, voice).
- **Plugins** = runnable workflows + marketplace metadata + capability declarations.
- **Craft** = universal brand-agnostic rules (`craft/typography.md`, `color.md`, `anti-ai-slop.md`) that skills can opt into.

### DESIGN.md CONTRACT
A `DESIGN.md` is a single Markdown file with a fixed 9-section schema (source: `VoltAgent/awesome-design-md`):

1. **Visual Theme & Atmosphere** — mood, personality, use cases.
2. **Color Palette & Roles** — background, foreground, accent, muted, border, surface, semantic colors.
3. **Typography Rules** — font families, weights, scale, line-height, letter-spacing.
4. **Component Stylings** — buttons, cards, inputs, links, etc.
5. **Layout Principles** — grid, max-width, gutters, spacing, hero rules.
6. **Depth & Elevation** — flat vs raised, shadow rules, no neumorphism/glassmorphism unless specified.
7. **Do's and Don'ts** — explicit rules and anti-patterns.
8. **Responsive Behavior** — breakpoints, column reflow, hero reflow.
9. **Agent Prompt Guide** — concise instructions for the agent on how to apply the system.

The daemon injects the active `DESIGN.md` into the agent prompt as a system prefix and places a `DESIGN.md` file in the artifact CWD. Skills can declare `od.design_system.requires: true` and `od.design_system.sections: [color, typography, layout, components]` to prune tokens.

### ARTIFACT_TYPES

| Type | Mode | Preview | Primary Output | Export | Notes |
|---|---|---|---|---|---|
| **Prototype** | prototype | HTML/JSX iframe | `index.html` | HTML, ZIP, PDF | Single-page web/mobile/desktop mockup |
| **Live dashboard / artifact** | prototype/live | HTML iframe | `index.html` | HTML, PDF | Pulls data via tweaks panel; re-renders without reload |
| **Deck** | deck | HTML (horizontal swipe) | `index.html` + `slides.json` | HTML, PDF, PPTX, ZIP | Default: `guizang-ppt`; 15 templates × 36 themes |
| **Image** | image | image asset | PNG/JPG | download | Via gpt-image-2, ImageRouter, custom API, FAL, Leonardo, etc. |
| **Video** | video | video player | MP4 | download | Via HyperFrames (HTML+CSS+GSAP → headless Chrome + FFmpeg) or Seedance/Veo/Sora/Kling |
| **Audio** | audio | audio player | MP3/WAV | download | Via ElevenLabs (speech) or Suno/Lyria (music deferred) |
| **HyperFrame** | video/hyperframes | video player | MP4 | download | HTML/CSS/GSAP motion graphics rendered to MP4 |
| **Design system** | design-system | markdown | `DESIGN.md` | markdown | Generates a brand contract from brief/screenshot/URL |

### RISKS
1. **Security model relies on agent permissions.** OD does not add its own sandbox; a malicious skill can instruct the agent to delete or exfiltrate files the agent can access.
2. **Third-party CLI format drift.** 24 adapters must parse JSONL/ACP/stream-json output from diverse CLIs. Any upstream format change can break a supported agent.
3. **Skill/plugin install from arbitrary URLs.** `od skill add <url>` and `od plugin install <url>` can pull untrusted code; the project mitigates with warnings and agent permissions, but the surface remains risky.
4. **Telemetry opt-in leakage.** Even with redaction, prompts and tool content are truncated and sent to a Cloudflare → Langfuse relay. Sensitive business data could leak.
5. **Windows native build friction.** `better-sqlite3` compiles from source on Windows; requires Visual Studio Build Tools.
6. **AMR commercial path.** The official model service is a paid product; onboarding defaults toward it, which may conflict with the BYOK/local-first messaging.
7. **Large codebase blast radius.** 8,523 files and strict boundary rules mean contributors need significant context before touching core code.
8. **License mix in packaged builds.** Apache-2.0 + MIT + LGPL (7zip binaries) requires careful attribution in redistributions.

### USAGE_PATTERNS
1. **Desktop app:** Download, install, pick skill + design system, type brief, get artifact, refine, export.
2. **Inside coding agent:** `curl ... | sh -s <agent>` then ask the agent to "use open-design to generate X with Y design system".
3. **MCP server:** `od mcp install <agent>` exposes `od search-files`, `od get-file`, `od get-artifact`, `od plugin run`, `od skill list` to the agent.
4. **Docker/self-hosted:** `docker compose up -d` in `deploy/`, open `http://localhost:7456`.
5. **Source development:** `pnpm tools-dev run web` for daemon + web foreground; `pnpm tools-dev` for background daemon + web + desktop.
6. **Plugin/scenario automation:** `od plugin apply <id> --input brief="..."` for headless generation pipelines.
7. **Refresh existing codebase:** `od plugin apply od-code-migration` or `od-figma-migration` to point a plugin at a git repo + DESIGN.md.

### CONFIG_EXAMPLES

#### Source install
```bash
git clone https://github.com/nexu-io/open-design.git
cd open-design
corepack enable && pnpm install
pnpm tools-dev run web
```

#### Docker
```bash
cd open-design/deploy
cp .env.example .env
openssl rand -hex32  # paste into OD_API_TOKEN
docker compose up -d
# open http://localhost:7456
```

#### Agent install
```bash
curl -fsSL https://open-design.ai/install.sh | sh -s claude
# or
curl -fsSL https://open-design.ai/install.sh | sh -s opencode
```

#### Inside agent
```text
> Use open-design to generate a landing page with the Linear design system
```

#### CLI usage
```bash
od mcp install claude
od skill list
od skill add https://github.com/op7418/guizang-ppt-skill
od plugin list
od plugin info od-default
od plugin apply od-default --input brief="a one-page pitch for our seed round"
```

#### Nginx reverse proxy for SSE
```nginx
location /api/ {
    proxy_pass http://127.0.0.1:7456;
    proxy_buffering off;
    gzip off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
}
```

### RECOMMENDATIONS_FOR_TELEGRAMHELPER
TelegramHelper is a Python aiogram/Telethon/SQLAlchemy asyncio bot. Open Design's design-system and skill model can be adapted to give TelegramHelper a visual language and agent-friendly content generation layer.

1. **Adopt a `DESIGN.md` brand contract.** Create a TelegramHelper `DESIGN.md` in the project root with the 9-section schema (color palette for Telegram UI, typography, components, responsive behavior for mobile, anti-patterns). This makes every bot UI artifact generated by an agent brand-consistent.
2. **Add a `skills/telegram-ui/` skill.** A Claude Code / OpenCode skill that knows how to generate inline keyboards, reply keyboards, mini-app HTML, bot command menus, and message templates following TelegramHelper's DESIGN.md.
3. **Add a `skills/telegram-dashboard/` skill.** For a live admin dashboard rendered as a single-page HTML artifact (user count, active sessions, error rate) with a tweaks panel for date ranges.
4. **Create a `design-systems/telegram/` or `design-systems/telegram-helper/` DESIGN.md.** Base it on Telegram's official design system (blue `#229ED9`, rounded 8px buttons, SF/Inter fonts, dark mode) so any agent-generated UI matches Telegram's brand.
5. **Use the `craft/` pattern.** Add Telegram-specific universal rules (e.g., "never send messages longer than 4096 chars", "always provide a back button in nested keyboards") to `craft/telegram-ux.md` and opt skills into it.
6. **Expose TelegramHelper via MCP.** If TelegramHelper already has an internal API or CLI, add an MCP server or `od`-like CLI wrapper so OpenCode/Claude can query users, send messages, or inspect logs without editing code.
7. **Add prompt templates for common bot flows.** Under `prompt-templates/` or `plugins/_official/`, add reusable prompts: onboarding flow, payment reminder, support ticket, broadcast analytics, mini-app promo.
8. **Consider a `design-templates/telegram-mini-app/` folder.** A seed HTML template + references for Telegram Mini Apps so the agent composes a Mini App UI rather than writing CSS from scratch.
9. **Reuse the plugin spec.** If TelegramHelper wants to publish reusable skills, wrap them as plugins with `open-design.json` and `SKILL.md` so they can be consumed by Open Design or any Claude Code-compatible agent.
10. **Start with a design system + one skill.** Do not port the whole Open Design engine. The highest leverage is: (a) a single `DESIGN.md` for TelegramHelper, and (b) a single `SKILL.md` for "generate a Telegram bot UI artifact".

### CONFIDENCE
**High** for product-level findings (architecture, components, usage patterns, license, version) because they are directly observable in README.md, AGENTS.md, package.json, docs, and the cloned repository.
**Medium** for security and risk assessment because no live execution or static security audit was performed; the assessment is based on documented security model and source patterns.
**Medium** for exact plugin/skill counts because registry indexing at runtime may differ from path-based counts.
**Low** for release timeline and AMR specifics because the repository is from a simulated/future-dated timeline and some commercial details (AMR pricing, terms) are not in the repo.

### GAPS
- Full git history and contributor churn were not analyzed (shallow clone).
- No individual agent adapter definitions were read beyond the registry list.
- No execution of the daemon, web app, or any agent CLI was performed.
- No static analysis or dependency vulnerability scan was run.
- The MCP tool authorization model was not deeply inspected.
- The exact Open Design AMR commercial terms, pricing, and data-sharing boundaries are not in the repo.
- No GitHub Issues/Discussions were reviewed, so current bugs and community blockers are unknown.
- The plugin runtime (`packages/plugin-runtime`) and registry protocol (`packages/registry-protocol`) were not fully analyzed beyond the spec docs.
