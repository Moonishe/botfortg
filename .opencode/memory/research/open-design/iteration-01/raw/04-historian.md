# 04 Historian — Evolution, changelog, roadmap, and lineage

## SUMMARY
Open Design's evolution shows a fast cadence: 0.6 → 0.7 → 0.8 → 0.9 → 0.10 → 0.11 in roughly two months. The product has shifted from a research-preview architecture to a "plugin-first" engine, then to an AMR-first onboarding model. Key lineage includes Open CoDesign (iframe preview), Multica (daemon architecture), VoltAgent/awesome-design-md (DESIGN.md schema), guizang-ppt-skill (deck default), and HyperFrames (video). The roadmap is explicit about what is in scope (local-first, agent-native, file-based artifacts) and what is not (multi-user, hosted marketplace, collaborative editing, Figma export).

## CHANGES
No code changes; read-only historical analysis.

## EVIDENCE
Tools used:
- `read` of `CHANGELOG.md`, `docs/roadmap.md`, `README.md` §References & lineage.
- `webfetch` pre-flight of CHANGELOG.md (full web-rendered copy) and README.md.
- `grep` for version tags and milestone markers in CHANGELOG and roadmap.
- `bash` for shallow-clone commit metadata (single commit, HEAD SHA `618a07d8`).

### Release timeline (from CHANGELOG.md)
- **0.9.0** (2026-05-29) — "install-and-create" release. Open Design AMR (official model router) built in; Aider, Trae, Antigravity, DeepSeek Reasonix adapters added; queue sends; plugins become first-class; design-system rename/pin/swatches; Windows portable zip; Linux Docker/Podman one-click; MCP write_file/delete_file.
- **0.8.0** (2026-05-20) — "everything is a plugin" rebuild. Plugin engine (`packages/plugin-runtime`, `registry-protocol`, `host`), Critique Theater Phases 9–16, 149 design systems with tokens.css, Leonardo/ElevenLabs/SenseAudio media providers, packaged auto-update for macOS/Windows, manual edit UX overhaul, PostHog v2.
- **0.7.0** (2026-05-12) — memory-plus-UI release. Auto-memory store, Critique Theater Phase 7, HyperFrames HTML-in-Canvas, Designs tab redesign, in-context preview comments, unified Media tab, tweaks palette, scheduled routines, macOS Intel builds, Nix flake.
- Earlier versions (0.6.x and below) covered in CHANGELOG.md but not detailed here.

### Roadmap phases (docs/roadmap.md)
- **Phase 0** — Spec finalization (mostly complete).
- **Phase 1** — MVP (6–8 weeks): web app, daemon, Claude Code adapter, API fallback, `saas-landing` + `magazine-web-ppt` skills, prototype/deck/design-system modes.
- **Phase 2** — v1 (8 weeks after MVP): Codex/Cursor adapters, comment mode, sliders, PDF/PPTX export, template gallery, Docker, Topology B (Vercel + tunneled daemon).
- **Phase 3** — v2 (12 weeks after v1): skill marketplace, skill signing, Gemini/OpenCode/OpenClaw, Windows support, collaborative mode, Figma export, telemetry, optional SaaS.
- **Self-evolution track** (`specs/current/automation-self-evolution.md`): memory tree, automation templates, design-system/skill proposals, connector ingestion, token compression.

### Decision log (from docs/roadmap.md)
- 2026-04-24 — Plain files + `history.jsonl` over SQLite for artifacts (git-reviewable, matches skills-as-files ethos).
- 2026-04-24 — Adopt `DESIGN.md` (awesome-claude-design) verbatim rather than inventing a new format.
- 2026-04-24 — Do not ship Electron/Tauri wrapper initially; focus on skills.
- 2026-04-24 — Delegate entire agent loop to user's CLI; ecosystem compatibility beats control.

### Lineage (README.md §References & lineage)
- `alchaincyf/huashu-design` — design-philosophy compass, anti-AI-slop checklist, five-dimensional critique.
- `op7418/guizang-ppt-skill` — magazine-style web PPT skill, default for deck mode.
- `lewislulu/html-ppt-skill` — HTML PPT Studio family (15 templates, 36 themes).
- `OpenCoworkAI/open-codesign` — streaming-artifact loop, sandboxed iframe, live agent panel.
- `multica-ai/multica` — daemon + adapter architecture, PATH-scan agent detection.
- `VoltAgent/awesome-design-md` — 9-section DESIGN.md schema and 70 product systems.
- `bergside/awesome-design-skills` — 57 design skills.
- `heygen-com/hyperframes` — HTML→MP4 motion graphics.
- Anthropic Claude Code skills — SKILL.md convention.

## RISKS
- **Pace risk**: The project is moving extremely fast (weekly-ish releases). The codebase may accumulate technical debt and boundary violations.
- **Scope creep**: The roadmap explicitly defers multi-user, Figma export, hosted marketplace, and collaborative editing, but user pressure could pull them in early.
- **Upstream dependency risk**: `guizang-ppt-skill`, HyperFrames, and awesome-design-md are upstream projects; format changes or license changes could force OD to fork or migrate.
- **MVP vs current reality**: The current repo already contains many v2-ish features (plugins, AMR, Electron packaging, 18 locales). The roadmap is partly a historical document that has been overtaken.
- **Self-evolution complexity**: The automation-self-evolution track introduces memory trees, source packets, and proposal review gates — a significant architectural expansion.

## BLOCKERS
- CHANGELOG.md is large (149 KB) and was truncated by `webfetch`; full version history was not fully read.
- Shallow clone prevents git history analysis (commit frequency, contributor attribution, file churn).
- Release dates are future-dated relative to today (2026-06-22), suggesting the repo may be from a simulated timeline; real-world availability of some upstream tools (e.g., Claude Design, AMR) should be verified.
- No review of actual GitHub Issues/Discussions to see what is currently breaking.
