# Researcher 1 — Bird's Eye View
## Target: design-md skill (google-labs-code/stitch-skills)
## Date: 2026-06-22
## Sources: skills.sh page + GitHub README + SKILL.md raw

---

# SUMMARY

The `design-md` skill is a **prompt-only Agent Skill** (no executable code) from Google Labs' `stitch-skills` repository. It is part of the `stitch-utilities` plugin and instructs an AI coding agent to act as a "Design Systems Lead" that analyzes Google Stitch design projects and synthesizes a **DESIGN.md** file — a semantic, natural-language design system specification. The skill has a **hard dependency on the Stitch MCP Server** (it cannot function without it). It retrieves screen metadata, HTML/CSS source, and design assets via MCP tools, then translates technical values (Tailwind classes, border-radius, hex codes, shadows) into evocative natural-language descriptions. Output follows a rigid 5-section structure. Popularity: 50.3K installs, 6.1K GitHub stars, first seen Jan 22 2026.

---

# KEY_FINDINGS

## 1. Skill Identity & Metadata

| Field | Value |
|-------|-------|
| Name | `design-md` |
| Full path | `plugins/stitch-utilities/skills/design-md/` |
| Repo | `google-labs-code/stitch-skills` |
| Installs | 50.3K (skills.sh) |
| GitHub Stars | 6.1K |
| Forks | 739 |
| First seen | Jan 22, 2026 |
| License | Apache-2.0 |
| Languages (repo) | TypeScript 89.6%, Shell 5.2%, Python 5.2% |
| Security audits | Gen Agent Trust Hub: Pass, Socket: Pass, Snyk: Warn |
| Standard | Agent Skills open standard (agentskills.io) |

## 2. Prompt-Only Nature (No Executable Code)

The skill's `SKILL.md` is **172 lines (128 loc), 7.47 KB** — pure prompt instructions. The skill folder structure is minimal:

```
design-md/
├── SKILL.md           — Core instructions & workflow (the only functional artifact)
├── examples/          — Sample DESIGN.md outputs (reference materials)
└── README.md          — Skill documentation
```

Unlike the Agent Skills standard which includes `scripts/` and `resources/`, this skill has **no scripts/ directory and no resources/ directory**. It is entirely prompt-driven. The `allowed-tools` frontmatter declares: `stitch*:*`, `Read`, `Write`, `web_fetch`.

## 3. Hard Stitch MCP Dependency

The skill **cannot operate without the Stitch MCP Server**. This is non-negotiable:

- Prerequisites explicitly state: "Access to the Stitch MCP Server"
- The repository README states: "These skills require the Stitch MCP server to be configured and running in your agent's environment."
- Setup link: https://stitch.withgoogle.com/docs/mcp/setup/

The retrieval workflow is a 6-step MCP orchestration:
1. **Namespace discovery** — `list_tools` to find Stitch MCP prefix (e.g., `mcp_stitch:`)
2. **Project lookup** — `[prefix]:list_projects` with `filter: "view=owned"`
3. **Screen lookup** — `[prefix]:list_screens` with numeric `projectId`
4. **Metadata fetch** — `[prefix]:get_screen` returns `screenshot.downloadUrl`, `htmlCode.downloadUrl`, `width`, `height`, `deviceType`, `designTheme`
5. **Asset download** — `web_fetch` / `read_url_content` on download URLs
6. **Project metadata extraction** — `[prefix]:get_project` returns `designTheme` (color mode, fonts, roundness, custom colors)

**Implication: This skill is unusable outside the Stitch ecosystem.** It is not a generic design-doc generator. It is a Stitch-specific analysis tool.

## 4. Core Mechanism: Semantic Translation (CSS → Natural Language)

The skill's central innovation is **translating technical CSS/Tailwind values into evocative natural language**. Examples from SKILL.md:

| Technical Value | Semantic Translation |
|-----------------|---------------------|
| `rounded-full` | "Pill-shaped" |
| `rounded-lg` | "Subtly rounded corners" |
| `rounded-none` | "Sharp, squared-off edges" |
| `#0077B6` | "Ocean-deep Cerulean (#0077B6)" |
| generic "blue" | DISCOURAGED — use descriptive name + hex |
| generic "rounded" | DISCOURAGED — use specific physical description |

Design philosophy: **DESIGN.md serves as the "source of truth" for prompting Stitch to generate new screens.** Stitch interprets design through "Visual Descriptions" supported by specific color values. The semantic language is calibrated for Stitch's prompt interpreter.

## 5. Fixed Output Format (5-Section DESIGN.md)

The skill enforces a rigid output template. The agent MUST produce:

```markdown
# Design System: [Project Title]
**Project ID:** [Insert Project ID Here]

## 1. Visual Theme & Atmosphere
   (mood, density, aesthetic philosophy — evocative adjectives: "Airy," "Dense," "Minimalist," "Utilitarian")

## 2. Color Palette & Roles
   (per color: Descriptive Name + Hex Code in parentheses + Functional Role)

## 3. Typography Rules
   (font family, weight usage headers vs body, letter-spacing character)

## 4. Component Stylings
   * **Buttons:** (Shape, color, behavior)
   * **Cards/Containers:** (Corner roundness, background, shadow depth)
   * **Inputs/Forms:** (Stroke style, background)

## 5. Layout Principles
   (Whitespace strategy, margins, grid alignment)
```

The 5 sections map to 5 analysis steps:
1. Extract Project Identity (JSON metadata)
2. Define the Atmosphere (from screenshot/HTML)
3. Map the Color Palette (from Tailwind config/JSON)
4. Translate Geometry & Shape (CSS border-radius → physical descriptions)
5. Describe Depth & Elevation (shadow quality)

## 6. Position in the Plugin Ecosystem

The skill sits in `stitch-utilities` alongside 3 sibling skills:

| Skill | Plugin | Purpose |
|-------|--------|---------|
| **design-md** | stitch-utilities | Analyze Stitch projects → generate DESIGN.md (this skill) |
| enhance-prompt | stitch-utilities | Vague UI ideas → polished Stitch-optimized prompts |
| stitch-loop | stitch-utilities | Single prompt → complete multi-page website with validation |
| taste-design | stitch-utilities | DESIGN.md enforcing premium, anti-generic UI standards |

Related skills in other plugins:
- `extract-design-md` (stitch-design) — extracts DESIGN.md from **frontend source code** (NOT from Stitch projects — different input source)
- `manage-design-system` (stitch-design) — uploads DESIGN.md to Stitch and applies themes

**Key distinction:** `design-md` analyzes existing Stitch projects. `extract-design-md` analyzes local frontend code. `taste-design` generates a DESIGN.md from scratch with premium constraints. There is overlap but each has a different input pipeline.

## 7. Agent Compatibility

The skill follows the Agent Skills open standard and is compatible with:
- Codex
- Antigravity
- Gemini CLI
- Claude Code
- Cursor
- (and any agent supporting the standard)

Installation: `npx skills add google-labs-code/stitch-skills --skill design-md --global`

## 8. Best Practices & Pitfalls (Embedded in Prompt)

The SKILL.md embeds guardrails directly in the prompt:
- **Be Descriptive:** Avoid generic terms; use "Ocean-deep Cerulean (#0077B6)"
- **Be Functional:** Explain what each design element is used for
- **Be Consistent:** Same terminology throughout
- **Be Visual:** Help readers visualize through descriptions
- **Be Precise:** Include exact values in parentheses after natural language

Common pitfalls flagged:
- Using technical jargon without translation ("rounded-xl" instead of "generously rounded corners")
- Omitting color codes or using only descriptive names
- Forgetting functional roles
- Being too vague in atmosphere descriptions
- Ignoring subtle details like shadows or spacing

## 9. External Reference Dependency

The skill references the **Stitch Effective Prompting Guide** (https://stitch.withgoogle.com/docs/learn/prompting/) as a prerequisite for best results. The agent is instructed to use language and patterns from this guide. This is a soft dependency — the skill works without it but quality degrades.

## 10. Repository Maturity Signals

- 66 commits on main branch
- 2 releases (latest: "Stitch Skills Plugins Release" May 18, 2026)
- 10 open issues, 8 open pull requests
- 54 watchers
- Not an officially supported Google product (explicit disclaimer)
- Not eligible for Google OSS Vulnerability Rewards Program

---

# CONFIDENCE

**Overall: HIGH (0.90)**

| Dimension | Confidence | Rationale |
|-----------|------------|-----------|
| Skill structure & prompt content | 0.98 | Full SKILL.md retrieved verbatim (172 lines) |
| MCP dependency | 0.98 | Explicitly stated in prerequisites, README, and retrieval workflow |
| Output format | 0.98 | Exact template reproduced from SKILL.md |
| Install/usage mechanics | 0.95 | Confirmed from both skills.sh and GitHub README |
| Ecosystem positioning | 0.90 | Inferred from plugin/skill tables in README; sibling skill boundaries clear |
| Semantic translation approach | 0.95 | Direct examples from SKILL.md |
| Repo activity/maturity | 0.85 | GitHub metadata retrieved; contributor graph failed to load |

**Gaps for other researchers to fill:**
- The `examples/` directory contents (sample DESIGN.md outputs) were not fetched — would confirm actual output quality
- The `taste-design` sibling skill was not analyzed for comparison (premium enforcement vs. extraction)
- The `extract-design-md` skill (stitch-design plugin) was not fetched — would clarify overlap boundaries
- The Stitch Effective Prompting Guide content was not fetched — would reveal how semantic language maps to Stitch's prompt interpreter
- No information on error handling when MCP is unavailable or project has no screens
