# Researcher 4 (Historian) — open-design Version Evolution & Trajectory

**Repository:** https://github.com/nexu-io/open-design
**Sources fetched:** GitHub main page, CHANGELOG.md (raw), docs/roadmap.md (raw), releases page, RELEASE-NOTES-0.10.0.md
**Date of research:** 2026-06-22
**Focus:** version evolution (0.6→0.11 rapid), 8523 tracked files, release cadence, contributor growth, roadmap direction

---

## SUMMARY

Open Design (nexu-io/open-design) is a local-first, open-source alternative to Anthropic's Claude Design, positioned as "the Figma alternative for the agent era." The repository reached **68.7k stars and 7.7k forks** with **2,405 commits** as of 2026-06-22. It shipped **13 tagged releases in ~48 days** (2026-05-01 to 2026-06-17), progressing from 0.1.0 to 0.11.0 with extraordinary velocity — multiple releases landed on the same day or within 1–2 days of each other during the early sprint (0.1→0.3 shipped on three consecutive days: May 1, 2, 3).

The project launched on **2026-05-01** — directly in response to Anthropic's April 2026 release of the closed-source Claude Design. Its founding thesis (from the roadmap decision log, dated 2026-04-24) is that the moat is "the uses user's existing agent angle" — delegating the agent loop to whatever coding CLI the user already has installed (Claude Code, Codex, Cursor, Copilot, Gemini, OpenCode, Hermes, Kimi, and 13+ others) rather than shipping its own agent.

**Contributor growth scaled aggressively**: from ~27 contributors at 0.2.0 to a peak of 88 contributors on the 0.9.0 release (310 PRs in 7 days). The 0.10.0 release was the largest single release by PR volume (405 PRs, 68 contributors, 9 days, +36k lines in one consolidation PR). The cadence then compressed: 0.11.0 shipped 137 PRs from 57 contributors in just 4 days. The 0.10.1 patch (2 contributors, 1 day) shows a mature pattern of rapid stability patches between feature releases.

The codebase grew from 72 design systems + 31 skills at 0.1.0 to 150+ design systems, 259+ skills, and 261 plugins by 0.11.0. The ~8,523 tracked files figure is consistent with the monorepo's stated structure (apps/daemon, apps/web, packages/*, skills/, design-systems/, design-templates/, plugins/, prompt-templates/, e2e/, plus 18+ locale READMEs and extensive docs/).

The roadmap is explicitly phased (Phase 0→3) but the project has already blown past its own MVP and v1 estimates — Phase 1 (MVP, ~6–8 weeks) and Phase 2 (v1, ~8 weeks after MVP) were effectively collapsed into the first 48 days. The "Self-evolution track" (SE0–SE5) represents the forward-looking direction: automation templates, editable memory trees, skill crystallization from usage patterns, and token compression.

---

## TIMELINE

### Release History (chronological)

| Version | Date | PRs | Contributors | Cycle Days | Key Theme |
|---------|------|-----|--------------|------------|-----------|
| 0.1.0 | 2026-05-01 | — (initial) | — | Day 0 | First public release. 72 design systems, 31 skills, 9 locales, macOS arm64 + Windows x64 packaging |
| 0.2.0 | 2026-05-02 | 45 | 27 | 1 day | Dark mode, xAI Grok Imagine, headless deploy, OpenClaude fallback, 4 new locales |
| 0.3.0 | 2026-05-03 | 39 | 25 | 1 day | Richer design workflows, packaged-agent reliability, export/deploy flows, i18n |
| 0.4.0 | 2026-05-05 | 71 | 40+ | 2 days | MCP server, Critique Theater Phase 4, live-reload + Tweaks mode, 5 new agent adapters |
| 0.4.1 | 2026-05-06 | — | — | 1 day | Patch release |
| 0.5.0 | 2026-05-07 | 51 | — | 1 day | Live-data dashboards, Inspect mode, Critique Theater Phase 5, Qoder CLI, Nano Banana, Indonesian locale (16 beta cycles accumulated) |
| 0.6.0 | 2026-05-09 | 136 | — | 2 days | Bidirectional MCP citizen (39 templates), Cloudflare Pages deploy, Critique Theater Phase 6, PDF export, Ollama Cloud, Gemini 3 + GPT-5.1 + DeepSeek v4, Turkish + Thai |
| 0.7.0 | 2026-05-12 | 107 | — | 3 days | Auto-memory store, Critique Theater Phase 7, HyperFrames HTML-in-Canvas, Designs tab redesign, in-context comments, scheduled routines, macOS Intel, Nix flake |
| 0.8.0 | 2026-05-20 | 305 | 75 | 8 days | **Architecture rebuild**: everything is a plugin, headless by default, plugins create plugins. Critique Theater Phase 16. 149 design systems with tokens.css. Packaged auto-update. Italian locale. Leonardo.ai + ElevenLabs + SenseAudio |
| 0.9.0 | 2026-05-29 | 310 | 88 | 9 days (7-day dev cycle) | **Open Design AMR** (official model router, zero-config). Aider, Trae, Antigravity, DeepSeek Reasonix adapters. Plugin marketplace. 19 locales |
| 0.10.0 | 2026-06-11 | 405 | 68 | 13 days (9-day dev cycle) | **All-in-one Agentic design workspace**. Lexical composer, interactive terminals, reference board, conversation forking, sandbox runtime, BYOK validation. Largest release by PR volume |
| 0.10.1 | 2026-06-12 | 3 | 2 | 1 day (patch) | Stability & performance: leaner prompts, lighter transcripts, tool-boundary run fix |
| 0.11.0 | 2026-06-17 | 137 | 57 | 5 days (4-day dev cycle) | **"The Bazaar"**: Amp + Codebuddy adapters, live preview clips for 56 decks + 23 community kits, guided onboarding, biggest stability pass, loopback preview binding |

### Key Inflection Points

1. **0.1.0→0.3.0 (May 1–3):** Three releases in three days. Pure velocity sprint establishing the baseline — multi-agent detection, design systems, sandboxed preview, i18n. The project went from nothing to a working product in 72 hours.

2. **0.4.0 (May 5):** The MCP server leap. Open Design became both an MCP server AND consumed external MCP. This was the architecture decision that made it a platform, not just a tool. Critique Theater (the automated design review system) debuted at Phase 4.

3. **0.6.0 (May 9):** Bidirectional MCP citizenship. Cloudflare Pages deployment gave artifacts a public hosting path. The project now had deployment, export (PDF), and connectivity stories complete.

4. **0.8.0 (May 20):** **The architecture rebuild.** The research-preview architecture was replaced with a plugin-first engine. "Everything is a plugin, headless by default, plugins create plugins." This was the largest structural change — 305 PRs, 75 contributors. The desktop app became a thin wrapper around the OD CLI. Critique Theater matured through Phase 16 (Prometheus metrics, Grafana dashboard, rollout ratchet).

5. **0.9.0 (May 29):** **The zero-config release.** Open Design AMR (Agentic Model Router) eliminated the API-key scavenger hunt. This was the onboarding breakthrough — "sign in once, pick a model, and go." Peak contributor count: 88.

6. **0.10.0 (Jun 11):** **The workspace consolidation.** 405 PRs, the single largest release. 211 files and +36k lines in one PR (#3516). Lexical composer, interactive terminals, reference board, conversation forking. The product stopped being "a design generator" and became "a design studio."

7. **0.11.0 (Jun 17):** **"The Bazaar" — community marketplace maturity.** Live preview clips, 56 official + 23 community deck kits, guided onboarding, and the biggest stability pass yet. The codename references Eric Raymond's "cathedral vs. bazaar" — the project explicitly framed itself as the open, community-built alternative.

### Repository Metrics (as of 2026-06-22)

| Metric | Value |
|--------|-------|
| Stars | 68,700 |
| Forks | 7,700 |
| Commits | 2,405 |
| Open Issues | 280 |
| Open Pull Requests | 114 |
| License | Apache-2.0 |
| Tracked files | ~8,523 (inferred from monorepo structure) |
| Design systems | 150+ (shipped) / 142+ (header badge) |
| Skills | 259+ |
| Plugins | 261 |
| Supported coding agents | 21+ CLIs |
| Supported locales | 19+ (en, zh-CN, zh-TW, ja, de, es, ru, fa, pt-BR, ko, fr, ar, uk, tr, it, id, th, + more) |

---

## TRENDS

### 1. Release Cadence: Compressing, Not Slowing Down

The release cycle **accelerated** rather than decelerating as the project matured. Early releases (0.1→0.3) shipped daily with small PR counts (39–45). Mid-cycle releases (0.6→0.8) had 3–8 day gaps but 107–305 PRs each. Late releases (0.10→0.11) returned to short cycles (4–9 days) but with 137–405 PRs. This is the inverse of the typical open-source lifecycle where cadence slows as complexity grows. The pattern suggests a well-staffed core team plus aggressive community contribution intake.

### 2. Contributor Growth: Explosive, Then Stabilizing Around a Core

Contributor counts grew from 27 (0.2.0) → 40+ (0.4.0) → 75 (0.8.0) → 88 (0.9.0, peak) → 68 (0.10.0) → 57 (0.11.0). The peak at 0.9.0 (88 contributors, AMR launch) was likely driven by the zero-config onboarding reducing the contribution barrier. The slight decline in 0.10.0/0.11.0 raw contributor counts is offset by higher PR volume per contributor (405 PRs / 68 contributors ≈ 6 PRs/contributor at 0.10.0), suggesting the core team is doing heavy lifting while the community contributes plugins, design systems, and i18n.

### 3. Architecture: From Monolith to Plugin-First (0.8.0 Pivot)

The 0.8.0 release was a deliberate architectural reset. The research-preview architecture was replaced with a plugin engine where "design systems, slices, prototypes, exports, and even Figma itself all live as plugins." The desktop app became a thin wrapper around the OD CLI. This mirrors the "boring engine + rich plugin surface" pattern seen in successful developer tools (VS Code, Obsidian). The pivot was risky (305 PRs, 8 days) but positioned the project for the 0.10.0 workspace consolidation and the 0.11.0 "Bazaar" marketplace model.

### 4. Agent Compatibility: Constantly Expanding the Bench

The project's core differentiation is agent-agnosticism. The supported agent list grew continuously: 11 CLIs at 0.1.0 (Claude Code, Codex, Cursor, Gemini, OpenCode, Qwen, Copilot, Hermes, Kimi, Pi, Kiro) → 15+ by 0.6.0 → 21+ by 0.11.0 (adding Aider, Trae, Antigravity, DeepSeek Reasonix, Amp, Codebuddy). Each adapter is "one entry" per the docs, making this a low-cost, high-value expansion. This is the moat the roadmap explicitly identified: "our moat is the uses user's existing agent angle; Anthropic is unlikely to ship that."

### 5. Design Systems & Skills: Inventory Scaling as Content Moat

Design systems grew 72 → 149 → 150+ with structured tokens.css. Skills grew 31 → 131 → 259+. Plugins reached 261. This content inventory is a second moat — each design system (Linear, Stripe, Vercel, Apple, etc.) and skill bundle is a reusable asset that makes the platform more valuable with zero marginal infrastructure cost. The 0.11.0 "Bazaar" codename formalizes this: community members can contribute decks, skills, and plugins to a live gallery.

### 6. Critique Theater: Phased Quality Engineering

A distinctive trend is the "Critique Theater" — an automated design review system that matured through 16+ phases (Phase 4 at 0.4.0 → Phase 7 at 0.7.0 → Phase 16 at 0.8.0). It includes Prometheus metrics, Grafana dashboards, rollout ratchets, conformance APIs, and Playwright stage suites. This is enterprise-grade quality infrastructure inside a project that is only 7 weeks old, suggesting the core team has significant platform engineering experience.

### 7. Roadmap: Already Past Its Own Estimates

The roadmap's Phase 1 (MVP, ~6–8 weeks) and Phase 2 (v1, ~8 weeks after MVP) were effectively completed within the first 48 days. The project is now operating in Phase 3 territory (ecosystem + robustness) and beyond, into the "Self-evolution track" (SE0–SE5) which envisions automation templates, editable memory trees, skill crystallization from usage patterns, and token compression. The roadmap's risk register notably lists "Anthropic ships an open-source Claude Design" as a risk with the mitigation: "our moat is the 'uses user's existing agent' angle; Anthropic is unlikely to ship that."

### 8. Revenue Model Emergence: AMR (Agentic Model Router)

The 0.9.0 release introduced Open Design AMR — "one recharge to use GPT, Claude, Gemini, and DeepSeek inside Open Design: 20+ flagship models, zero config, billed by real token usage." This is the project's revenue path: a model-routing service that monetizes the zero-config onboarding without locking users in (BYOK remains available at every layer). The "Open Design Fellow program" (0.10.0) adds a community governance layer.

### 9. Internationalization: Aggressive From Day One

9 locales at 0.1.0, growing to 19+ by 0.9.0. The project translated README, QUICKSTART, and UI into Chinese (simplified + traditional), Japanese, German, Spanish, Russian, Farsi, Portuguese, Korean, French, Arabic, Ukrainian, Turkish, Italian, Indonesian, Thai. This is unusual depth for a project this young and suggests the core team or early community has strong non-English-market awareness (likely Chinese-origin given the zh-CN/zh-TW priority and contributor handles).

### 10. Security Maturation

Early releases focused on features, but by 0.11.0, security hardened significantly: preview URL handling, loopback-only preview server binding, sandbox contract ownership guards, SSRF protection on the BYOK proxy, plugin manifest name validation, symlink rejection. The 0.10.0 release resolved a vulnerable `tmp` transitive dependency. The 0.10.1 patch fixed a run-completion false-positive at tool boundaries.

---

## CONFIDENCE

| Finding | Confidence | Basis |
|---------|-----------|-------|
| Release dates & version sequence | **High** | Directly from CHANGELOG.md and GitHub releases page (cross-verified) |
| PR counts & contributor numbers | **High** | CHANGELOG.md summaries + GitHub release pages (cross-verified; minor discrepancy on 0.10.0 where RELEASE-NOTES draft says 141 PRs/50 contributors but published release says 405 PRs/68 contributors — used published release as authoritative) |
| Repository metrics (stars, forks, commits) | **High** | GitHub main page, fetched 2026-06-22 |
| 8,523 tracked files | **Medium** | Not explicitly stated in fetched sources; inferred from monorepo structure (apps/, packages/, skills/ with 259+, design-systems/ with 150+, design-templates/, plugins/ with 261, prompt-templates/, e2e/, docs/ with 18+ locale READMEs, plus root config files). The figure is plausible but unverified from the sources fetched. |
| Architecture pivot at 0.8.0 | **High** | Explicitly stated in CHANGELOG.md 0.8.0 summary |
| Roadmap direction & phases | **High** | Directly from docs/roadmap.md (raw) |
| Contributor growth trend | **High** | Tracked across 8 releases with explicit counts |
| AMR revenue model | **High** | Stated in README, CHANGELOG 0.9.0, and release pages |
| Critique Theater phase progression | **High** | Tracked Phase 4→16 across CHANGELOG entries |
| "Anthropic released Claude Design in April 2026" as founding trigger | **Medium-High** | Stated in README "Why Open Design" section with X/Twitter link, but the X post ID (2045156267690213649) could not be independently verified in this research session |
| Project origin / core team composition | **Low** | Not directly researched; contributor handles suggest Chinese and international community, but no org/maintainer analysis was performed |

**Overall confidence: High.** The version evolution, release cadence, contributor growth, and roadmap direction are well-documented in primary sources (CHANGELOG.md, release pages, roadmap.md). The main uncertainty is the 8,523 tracked files figure (inferred, not confirmed) and the project's internal team structure (not researched).
