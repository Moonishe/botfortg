# 05 Practitioner — Usage, setup, and examples

## SUMMARY
Open Design can be consumed in three ways: download a prebuilt desktop app, install the MCP server into an existing coding agent, or run from source/Docker. The local workflow is: `pnpm tools-dev run web` starts daemon + web; pick a skill + design system; type a brief; the agent streams an `<artifact>`; preview renders in a sandboxed iframe; export to HTML/PDF/PPTX/MP4. For coding agents, `od mcp install <agent>` wires the stdio MCP server so the agent can list skills, get files, and run plugins without leaving the chat.

## CHANGES
No code changes; read-only usage research.

## EVIDENCE
Tools used:
- `read` of `QUICKSTART.md`, `docs/skills-protocol.md`, `design-templates/web-prototype/SKILL.md`, `design-systems/default/DESIGN.md`, `plugins/README.md`.
- `webfetch` pre-flight of `QUICKSTART.md` and `README.md`.
- `glob` of `plugins/_official/scenarios/*` to see runnable scenario plugins.
- `bash` to verify the daemon CLI entrypoint path (`apps/daemon/bin/od.mjs`) and `tools-dev` commands.

### Installation paths
1. **Desktop app (zero config)**
   - macOS (Apple Silicon/Intel) / Windows x64 / Linux AppImage → https://open-design.ai or GitHub Releases.
   - Auto-detects installed coding-agent CLIs on PATH.

2. **Coding agent (no UI)**
   ```bash
   curl -fsSL https://open-design.ai/install.sh | sh -s <agent>
   # agent = claude | codex | cursor | copilot | openclaw | antigravity | gemini | pi | vibe | hermes | cline | kimi | trae | opencode
   ```
   Then inside the agent:
   ```
   > Use open-design to generate a landing page with the Linear design system
   ```

3. **Docker**
   ```bash
   git clone https://github.com/nexu-io/open-design.git
   cd open-design/deploy
   cp .env.example .env
   echo "OD_API_TOKEN=$(openssl rand -hex 32)" >> .env
   docker compose up -d
   # open http://localhost:7456
   ```

4. **Source**
   ```bash
   git clone https://github.com/nexu-io/open-design.git
   cd open-design
   corepack enable && pnpm install
   pnpm tools-dev run web   # foreground daemon + web
   pnpm tools-dev          # background daemon + web + desktop
   ```
   Node `~24`, pnpm `10.33.2`.

### Common commands
```bash
# Lifecycle (single entry point)
pnpm tools-dev run web
pnpm tools-dev start web
pnpm tools-dev status --json
pnpm tools-dev logs --json
pnpm tools-dev stop

# Quality checks
pnpm guard
pnpm typecheck

# Package builds
pnpm --filter @open-design/daemon build
pnpm --filter @open-design/web build
pnpm --filter @open-design/desktop build

# CLI (od)
od mcp install claude
od skill list
od skill add https://github.com/op7418/guizang-ppt-skill
od plugin list
od plugin search "landing page"
od plugin info od-default
od plugin apply od-default --input brief="..."

# Design system
od get-file design-systems/linear-app/DESIGN.md
od get-artifact <slug>
```

### Typical workflow
1. Pick a skill (e.g., `web-prototype` for landing page, `guizang-ppt` for deck, `hyperframes` for video).
2. Pick a design system (e.g., `default` Neutral Modern, `linear-app`, `stripe`).
3. Type a brief.
4. Daemon composes system prompt: `BASE_SYSTEM_PROMPT + DESIGN.md + SKILL.md`.
5. Agent streams `<artifact>` with HTML/JSX.
6. Web app parses `<artifact>` and loads the entry file in a sandboxed iframe.
7. Use comment mode or tweak sliders to refine; the agent gets a surgical edit prompt.
8. Export: HTML (self-contained), PDF (browser print), PPTX (agent-driven slides.json), ZIP, Markdown, MP4 (HyperFrames).

### Example prompts
- "Create a SaaS landing page for a project-management tool with the Linear design system."
- "Make a 8-slide investor pitch deck in magazine style from this brief."
- "Generate a live dashboard showing GitHub repo metrics."
- "Create a 30-second product promo video in 16:9 using HyperFrames."
- "Design a mobile onboarding flow for a fitness app using the iPhone 15 Pro frame."

### Skill + design system pairing
- `web-prototype` + `default` — generic clean landing page.
- `saas-landing` + `linear-app` — B2B SaaS page.
- `dashboard` + `vercel` — analytics dashboard.
- `mobile-app` + `apple` — iOS-style prototype.
- `guizang-ppt` + `warm-editorial` — magazine deck.
- `hyperframes` + `stripe` — motion-graphics promo.

## RISKS
- **Setup friction**: Source install requires Node 24 and pnpm; Windows users need build tools for better-sqlite3.
- **CLI dependency**: Many features require an installed coding agent (Claude Code, Codex, etc.) or BYOK API key. Without either, the app is limited.
- **Docker macOS networking**: Docker Desktop bridge can cause the daemon to see non-loopback requests and reject them due to `OD_API_TOKEN` auth.
- **Preview vs. source mismatch**: Agents may emit text without `<artifact>` tags; the README warns that switching to a more capable model or stricter skill may be needed.
- **Plugin capabilities**: The plugin catalog is large but not all plugins are guaranteed to work with every agent or design system.

## BLOCKERS
- No live run of `pnpm tools-dev run web` or any agent was performed; actual setup steps are inferred from docs.
- No verification of the install script or download URLs.
- The MCP install flow per agent (`~/.config/<agent>/open-design.json`) was not tested.
- Exact artifact export behavior for PPTX/MP4 depends on skill support and local tooling (Puppeteer, FFmpeg, HyperFrames) that was not validated.
