# Researcher 2 — Deep Dive

**Repo:** https://github.com/nexu-io/open-design
**Sources fetched (raw, branch `main`):**
- `docs/architecture.md`
- `docs/skills-protocol.md`
- `apps/daemon/src/runtimes/registry.ts`

**Date:** 2026-06-22

---

## SUMMARY

open-design (OD) is a local-first AI design studio: a Next.js 16 web app paired with a long-running local daemon that brokers coding-agent CLIs (Claude Code, Codex, Cursor, Gemini, OpenCode, Qwen, and 18 more) into a unified design workflow. Skills (Claude Code `SKILL.md` format + OD optional `od:` extensions) and a `DESIGN.md` design-system contract drive artifact generation; output renders in a sandboxed iframe.

**Corrections to the iteration brief (verified against source):**

1. **Storage is NOT SQLite.** architecture.md §3.6 states explicitly: *"history.jsonl not SQLite → append-only, git-friendly, greppable. Open CoDesign uses SQLite; we deliberately don't."* Storage is plain files: `artifact.json` metadata + append-only `history.jsonl`. The brief's "Express+SSE+SQLite" is **wrong on the SQLite leg**. Transport is HTTP REST + Server-Sent Events (confirmed §7); the web↔daemon protocol in the worked-example §4 still shows a legacy `{method:"session.generate"}` JSON-RPC sketch, but §7 supersedes it with `POST /api/chat -> text/event-stream`. Express is not named in these three files (only "Node, long-running"); likely Express given the `/api/*` route surface, but unconfirmed from sources alone.
2. **24 agent adapters = 24 BASE defs, plus dynamic local profiles.** `registry.ts` exports exactly 24 entries in `BASE_AGENT_DEFS`, then layers `readLocalAgentProfileDefs()` on top and de-duplicates by `id` (throws on collision). So the runtime adapter count is **24 + N local profiles**.
3. **MCP server embeddability is NOT covered in these three files.** Neither doc mentions "MCP". The closest concept is `orchestrator-workspaces.md` ("embedding OD behind another control plane"), which was not fetched. Confidence on MCP embeddability is **low** from this evidence set.

**What IS confirmed solidly:**
- 3 deployment topologies (fully-local default / Vercel-web + local-daemon / Vercel-web direct-API).
- Daemon: localhost:7456, REST+SSE under `/api/*`, session manager + agent adapter pool + skill registry + design-system resolver + artifact store + preview compile pipeline + export pipeline.
- Skill registry: 3-location scan with strict priority precedence; `chokidar` FS-watch in dev, `SIGHUP` rescan in prod.
- DESIGN.md: 9-section schema (awesome-claude-design convention, not OD-invented), injected 3 ways (system-prompt prefix, CWD file, `{{ design_system }}` template var).
- Preview: `<iframe sandbox="allow-scripts">` (no `allow-same-origin`); HTML via `srcdoc`, JSX via vendored React 18 + Babel standalone; debounced 100ms full reload.
- Artifact lifecycle: plain files on disk; daemon never holds artifacts in memory; `artifact.json` metadata lets OD reconstruct the tree without a DB.
- Comment mode captures `[data-od-id]` → surgical-edit round trip.
- Slider UI for live `od.parameters` tweaks (re-prompt without full chat round-trip).
- Notable security hardening: HMAC-gated folder import for desktop (PR #974) with single-use tokens, TTL 60s, sticky in-process flag, `OD_REQUIRE_DESKTOP_AUTH=1` orchestrator pin, `fromTrustedPicker` marker, daemon-restart re-handshake.

---

## ARCHITECTURE

### Topologies (architecture.md §1)
- **A (default):** browser → Next.js (localhost:3000) → daemon (localhost:7456) → spawns agent CLIs. `pnpm tools-dev run web` starts both; `pnpm tools-dev` adds desktop shell. Zero config, no accounts.
- **B:** Vercel web + user's local daemon via user-provided tunnel URL (`od daemon --expose` prints it; user pastes into "Connect daemon" UI). Daemon holds secrets; Vercel holds nothing sensitive.
- **C:** Vercel web + direct Anthropic Messages API, BYOK in browser `localStorage`. Degraded: no Claude Code skills, no filesystem artifacts (IndexedDB instead), no PPTX export. Same web bundle; only enabled transports differ.

### Component diagram (§2)
Web App: chat pane · artifact tree · preview iframe · comment/slider overlay, all over an in-memory session bus → Transport layer (daemon SSE | api-direct | browser-only).
Daemon: session manager · skill registry · agent adapter pool · design-system resolver · artifact store · preview compile pipeline · export pipeline · detection service.
Downstream: agent CLIs (claude/codex/cursor-agent/gemini/opencode/qwen) + filesystem (daemon data root, skills/, DESIGN.md).

### Daemon (`od daemon`, §3.2)
- Single binary (`pkg`) or thin Node script over npm.
- Listens localhost:7456; REST/SSE under `/api/*`.
- One **session** per web tab: holds active agent, active skill, active artifact, in-flight tool calls, design-system reference.
- **Agent adapter pool:** one detected CLI = one adapter instance, reused across sessions.
- **Skill registry:** scans `~/.claude/skills/`, `./skills/`, `./.claude/skills/` on startup + FS-watch.
- **Artifact store:** writes to disk, never memory.
- **Preview compile pipeline:** Babel transform for JSX, CSS inliner for HTML exports.
- Export hooks: HTML/PDF/ZIP + skill-defined deck outputs.

### Agent adapter pool (§3.3)
Each adapter: (1) Detects CLI via PATH lookup + config-dir probe; (2) Spawns CLI with standardized wrapper prompt + skill context + design-system context + CWD = artifact root; (3) Streams stdout/stderr as structured events (JSON Lines if supported, else line parser); (4) Reports capabilities (multi-turn? surgical edits? native skill loading? tool use?). Full interface in `agent-adapters.md` (not fetched).

### Agent registry (`apps/daemon/src/runtimes/registry.ts`)
- **24 BASE_AGENT_DEFS** (exact list, in array order):
  amr, claude, codex, devin, gemini, opencode, hermes, trae-cli, grok-build, kimi, cursor-agent, qwen, qoder, copilot, amp, pi, kiro, kilo, vibe, deepseek, aider, antigravity, reasonix, codebuddy.
- Each imported from `./defs/<name>.js`.
- `readLocalAgentProfileDefs(baseDefs)` layers user-defined local profiles on top of base (loaded from `./local-profiles.js`).
- `AGENT_DEFS = [...BASE, ...local]`.
- **Duplicate-id guard:** iterates all defs, `Set`-tracks `def.id`, `throw new Error('Duplicate agent definition id: ' + id)` on collision — fail-fast at module load.
- `getAgentDef(id)` → `RuntimeAgentDef | null` lookup helper.
- Type import: `RuntimeAgentDef` from `./types.js`.

### Skill registry (§3.4 / skills-protocol §3)
| Location | Priority | Purpose |
|---|---|---|
| `./.claude/skills/` | 1 (highest) | project-private |
| `./skills/` | 2 | project-committed |
| `~/.claude/skills/` | 3 | user-global |
Conflicts by `name` → higher priority wins. Parsed once; `chokidar`-watched in dev, `SIGHUP` rescan in prod. Symlink strategy (cc-switch model) optional: one central skill dir symlinked into each agent's expected location.

### Design-system resolver (§3.5)
Lookup order: `./DESIGN.md` → `./design-system/DESIGN.md` → user-configured path. Parses 9-section awesome-claude-design schema. Injected as prepended system message on every agent run + `{{ design_system }}` template var. Hot-reloads on file change in dev.

### Artifact store (§3.6) — **NOT SQLite**
- Plain files on disk under daemon-managed storage.
- **`artifact.json` metadata** → reconstruct artifact tree without a DB.
- **`history.jsonl`** append-only, git-friendly, greppable. Explicit rejection of SQLite (Open CoDesign uses it; OD deliberately doesn't).
- Sessions are ephemeral UI state; artifacts are durable. Separated by design.
- Storage path rules live **only** in root `AGENTS.md` → "Daemon data directory contract" (not in these files; deferred).

### Export pipeline (§3.7)
| Format | Method |
|---|---|
| HTML (self-contained) | inline CSS, asset URLs → data: URIs |
| PDF | puppeteer `page.pdf()` on rendered HTML |
| PPTX | deck-skill JSON intermediate `slides.json` → `pptxgenjs` |
| ZIP | `archiver` over `artifacts/<id>/` |
| Markdown | direct copy if `.md`, else skill-defined render |

### Data flow — generate turn (§4)
1. User prompt → web sends to daemon (legacy sketch: `{method:"session.generate"}`; §7 supersedes with `POST /api/chat` SSE).
2. Daemon: pick skill → load DESIGN.md → materialize new artifact on disk → invoke adapter (system = SKILL.md body + DESIGN.md; user = prompt; cwd = new artifact dir) → stream events back (`tool_call`, `text_delta`, `thinking`).
3. Web: tool-call feed side panel + artifact tree updates + preview iframe loads primary output on "done" + slider/comment overlay activates.
4. On completion daemon appends `{ts, sessionId, artifactId, action:"generate", skill, promptHash}` to `history.jsonl`.
5. Comment → `{method:"session.refine", {sessionId, artifactId, elementId, note}}` → adapter translates by capability (Claude Code = native tool loop region-only; Codex/API = regenerate file with "only change element X" constraint).

### Preview renderer (§5)
- Always `<iframe sandbox="allow-scripts">` — **no `allow-same-origin`** (isolates from window/cookies/parent DOM).
- Static HTML: `srcdoc` load of inlined artifact.
- JSX: bootstrap imports vendored React 18 + Babel standalone, dynamically evals Babel-transformed JSX (Open CoDesign approach).
- Agent writes → debounced 100ms rebuild + `srcdoc` replace (full reload; React state loss acceptable).

### Web↔daemon protocol (§7) — **HTTP + SSE, not WS**
Routes:
```
GET  /api/health
GET  /api/agents
GET  /api/skills
GET  /api/design-systems
GET  /api/projects
POST /api/projects
POST /api/import/folder
GET  /api/projects/:id/files
POST /api/projects/:id/upload
POST /api/chat              -> text/event-stream
POST /api/artifacts/save
```
SSE keepalives: `Cache-Control: no-cache, no-transform` + `X-Accel-Buffering: no` + SSE comment keepalives. nginx needs `proxy_buffering off; gzip off;` long `proxy_read/send_timeout` or browsers get `net::ERR_INCOMPLETE_CHUNKED_ENCODING 200 (OK)`.

### Folder import + desktop HMAC gate (§7, PR #974)
- `POST /api/import/folder` roots project at existing local `baseDir` (no copy/shadow tree; user owns VCS).
- `baseDir` canonicalized via `realpath()` before storage (symlink-safe).
- `resolveSafe`/`sanitizePath` bounds checks still apply; `metadata.baseDir` only moves root, not bounds.
- Desktop build adds `shell.openPath` IPC → HMAC gate to stop renderer laundering arbitrary paths:
  - Desktop main generates 32-byte secret at startup, registers via `SIDECAR_MESSAGES.REGISTER_DESKTOP_AUTH`.
  - Token: `${nonce}~${expISO}~${signatureB64url}`, `signature = HMAC-SHA256(secret, baseDir + "\n" + nonce + "\n" + exp)`. Separator `~` (not `.`) because ISO 8601 expiries embed `.`.
  - Single-use nonces, TTL 60s, expiries > 2× TTL rejected.
  - **Fail-closed:** sticky in-process flag (once secret registered, stays active for process lifetime) + `OD_REQUIRE_DESKTOP_AUTH=1` env pin (active from request 0; pre-registration races get 503 `DESKTOP_AUTH_PENDING`).
  - `metadata.fromTrustedPicker: true` stamped on gate-passing imports; `shell:open-path` IPC refuses projects lacking it; `POST /api/projects` + `PATCH /api/projects/:id` reject client-supplied `fromTrustedPicker` (no smuggling/stripping).
  - Legacy projects (pre-gate) lose "Continue in CLI" button until re-import.
  - Daemon-restart edge: new daemon is `OD_REQUIRE_DESKTOP_AUTH` mode but no secret → first import 503 → desktop re-handshakes, mints fresh token, retries once.
  - Headless packaged mode (`apps/packaged/src/headless.ts`): `requireDesktopAuth: false`, gate dormant. Electron entry (`apps/packaged/src/index.ts`): `true`.
  - `tools-dev start desktop` introspects daemon STATUS IPC; if ungated (`desktopAuthGateActive: false`), stops daemon+web, respawns daemon with env pinned, restarts web, then launches desktop main.

### Security model (§9)
| Surface | Mitigation |
|---|---|
| Daemon HTTP/SSE | bind localhost; harden before exposing |
| Preview artifact code | sandboxed iframe, no same-origin |
| Agent fs access | adapter sets cwd to artifact dir; inherits agent's own permission model (Claude `--allowed-tools`, Codex sandbox, Cursor containment) |
| User secrets | BYOK in daemon `config.toml` (0600) or browser localStorage (Topology C); never sent to OD servers (none exist) |
| Untrusted skills | install-time warning; run under agent permission model, not OD's |
| Vercel bundle | standard Vercel integrity; zero secrets |

OD deliberately **inherits the agent's permission model** rather than inventing its own sandbox.

### Performance (§10)
Daemon startup <500ms (lazy adapter init); agent detection <200ms (parallel PATH probes); first-gen OD overhead <50ms (model time dominates); preview reload debounced 100ms; skill cold scan <100ms for ~50 skills.

### Out of scope for MVP (§11)
Multi-user/RBAC/orgs; hosted skill marketplace (git URLs only v1); Figma export (post-1.0); collaborative editing; mobile web; offline mode beyond local agent.

---

## DESIGN_MD_CONTRACT

### Source & status
The 9-section DESIGN.md format is **NOT invented by OD** — it's the [awesome-claude-design](https://github.com/VoltAgent/awesome-claude-design) convention, reproduced in `docs/skills-protocol.md` §5 for convenience. OD consumes it; it doesn't define it.

### The 9 sections (exact, in order)
```markdown
# <Brand Name>

## Visual Theme & Atmosphere
## Color Palette & Roles
## Typography Rules
## Component Stylings
## Layout Principles
## Depth & Elevation
## Do's and Don'ts
## Responsive Behavior
## Agent Prompt Guide
```

### Injection (3 channels, skills-protocol §5)
For non–design-system skills (modes prototype/deck/template):
1. **System-prompt prefix** — required sections only, pruned per `od.design_system.sections` (token savings).
2. **File in CWD** named `DESIGN.md` — skills `Read` it directly via their agent.
3. **Template variable** `{{ design_system }}` — Mustache-style, if skill body references it.

### Resolver lookup order (architecture §3.5)
`./DESIGN.md` → `./design-system/DESIGN.md` → user-configured path. Hot-reloads on file change in dev.

### Section pruning
`od.design_system.sections: [color, typography]` in skill frontmatter tells OD which sections the skill actually uses → only those sections injected into the system prompt (token savings). Full file still available as CWD `DESIGN.md`.

### Craft references — third axis (skills-protocol §5.5)
Universal brand-agnostic craft lives at `<projectRoot>/craft/` (typography.md, color.md, anti-ai-slop.md, …). Skills opt in via `od.craft.requires: [typography, color, anti-ai-slop]`. Resolution: `skills.ts` reads `od.craft.requires`; `craft.ts` reads each `<slug>.md` from `CRAFT_DIR` (missing files dropped silently — forward-reference safe); `prompts/system.ts` injects concatenated craft body **between** DESIGN.md and skill body. Brand tokens in DESIGN.md win on conflict; craft covers everything DESIGN.md doesn't override.

### design-system-skill (mode 4, skills-protocol §4.4)
Produces a `DESIGN.md` from inputs (brand brief, screenshot, URL). Preview = `markdown` (DESIGN.md + sample-components preview). Primary output = `DESIGN.md`. Workflow: analyze input → draft 9 sections per schema → generate sample component preview → finalize. Post-run: OD prompts user to set this DESIGN.md as project's active design system.

### Example reference
`docs/examples/DESIGN.sample.md` (not fetched).

---

## CONFIDENCE

| Claim | Source | Confidence | Notes |
|---|---|---|---|
| 3 topologies | arch §1 | **High** | explicit diagrams |
| Daemon localhost:7456, REST+SSE | arch §3.2, §7 | **High** | route table explicit |
| **NOT SQLite** (artifact.json + history.jsonl) | arch §3.6 | **High** | explicit rejection statement |
| Express specifically | — | **Low** | "Node, long-running" only; `/api/*` surface suggests it but not named in these files |
| 24 base agent adapters | registry.ts | **High** | exact count + names verified in source |
| Local profiles layered on top | registry.ts | **High** | `readLocalAgentProfileDefs` + dedup throw |
| Skill registry 3-location precedence | arch §3.4, skills §3 | **High** | consistent across both docs |
| Frontmatter parsing (base + od: ext) | skills §1-2 | **High** | full YAML grammar documented |
| DESIGN.md 9-section schema | skills §5 | **High** | exact section list + awesome-claude-design attribution |
| DESIGN.md 3-channel injection | skills §5 | **High** | explicit |
| Craft references third axis | skills §5.5 | **High** | resolution pipeline named per file |
| Sandboxed iframe preview (allow-scripts, no same-origin) | arch §5, §9 | **High** | explicit + security rationale |
| Artifact lifecycle (plain files, no DB) | arch §3.6, §4 | **High** | data-flow + rationale |
| Comment mode `[data-od-id]` surgical edit | arch §3.1, §4 | **High** | explicit |
| Slider UI for od.parameters | arch §3.1, skills §2 | **High** | explicit |
| HMAC folder-import gate (PR #974) | arch §7 | **High** | exhaustive detail |
| **MCP server embeddability** | — | **Low** | not mentioned in any of the 3 fetched files; closest is `orchestrator-workspaces.md` (unfetched) |
| Web↔daemon as WS (legacy sketch) | arch §4 | **Medium-Low** | §4 shows JSON-RPC `{method:...}` sketch but §7 supersedes with HTTP+SSE; treat §7 as authoritative |
| Agent adapter full interface | — | **Low** | lives in `agent-adapters.md` (not fetched) |
| Daemon data directory paths | — | **None** | explicitly deferred to root `AGENTS.md` "Daemon data directory contract" (not fetched) |

**Overall confidence:** **High** on daemon architecture, skill protocol, DESIGN.md contract, preview sandboxing, artifact lifecycle. **Low** on MCP embeddability (not in evidence set — needs `orchestrator-workspaces.md` or a daemon MCP source file) and on the "Express" specifically (needs a daemon `package.json` or server entrypoint).
