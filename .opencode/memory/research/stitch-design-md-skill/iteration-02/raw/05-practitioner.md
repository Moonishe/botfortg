# Researcher 5 — Practitioner Perspective

## Source
- Primary SKILL.md: `plugins/stitch-utilities/skills/design-md/SKILL.md` (172 lines, 7.47 KB)
- Skills.sh page: https://www.skills.sh/google-labs-code/stitch-skills/design-md
- GitHub repo: https://github.com/google-labs-code/stitch-skills (6.1K stars, 50.3K installs)
- First seen: Jan 22, 2026
- Alternative skill: `plugins/stitch-design/skills/extract-design-md/SKILL.md` (365 lines, 13.7 KB)

---

## SUMMARY

The `design-md` skill is a **post-render design-audit tool** for Google Stitch projects. It assumes you already have at least one designed screen living inside a Stitch project and want to extract a portable, semantic design-system document (`DESIGN.md`) from it. The document then becomes the "source of truth" for prompting Stitch to generate *new* screens that are visually consistent with the existing ones.

**Core mechanic — six-step retrieval pipeline via Stitch MCP:**
1. `list_tools` → discover the Stitch MCP namespace prefix (e.g. `mcp_stitch:`)
2. `list_projects` (filter `view=owned`) → find the target project ID
3. `list_screens(projectId)` → find the target screen ID
4. `get_screen(projectId, screenId)` → fetch screenshot URL, HTML/CSS URL, dimensions, deviceType
5. `web_fetch` the `htmlCode.downloadUrl` → download and parse the rendered HTML (Tailwind classes, custom CSS, component patterns)
6. `get_project(name)` → fetch `designTheme` object (color mode, fonts, roundness, custom colors)

**Synthesis phase — five analysis dimensions → DESIGN.md sections:**
- Atmosphere (evocative adjectives: "Airy," "Dense," "Minimalist")
- Color palette (descriptive name + hex code + functional role, e.g. "Deep Muted Teal-Navy (#294056) — primary CTA")
- Geometry/shape (translates `rounded-full` → "Pill-shaped," `rounded-lg` → "Subtly rounded corners")
- Depth/elevation (shadow quality descriptions)
- Component styling + layout principles (buttons, cards, inputs, whitespace strategy)

**Key design philosophy:** The skill explicitly forbids raw technical jargon in the output. It translates Tailwind classes and CSS values into *evocative natural language* that Stitch's prompt interpreter understands. This is the semantic bridge — `rounded-xl` becomes "generously rounded corners," a hex becomes "Ocean-deep Cerulean (#0077B6)."

**Output format:** Fixed 5-section markdown template (Visual Theme, Color Palette, Typography, Component Stylings, Layout Principles). No YAML frontmatter (unlike `extract-design-md` which requires it).

**Alternative — `extract-design-md`:** A sibling skill (365 lines, ~2x larger) that does the *same* DESIGN.md synthesis but reads **frontend source code** instead of rendered Stitch screens. It works on React, Vue, Svelte, Angular, or plain HTML/CSS — no build, no runtime, no Stitch MCP required. It has a 4-phase workflow (Project Discovery → Deep Extraction → Write DESIGN.md → Optional Stitch Integration) with framework-specific reference files. Critically, it *requires* YAML frontmatter with `name` and `colors` mapping for machine-parseability by downstream skills.

---

## USAGE_PATTERNS

### Pattern 1: Design audit of an existing Stitch project (primary use case)
**When:** You have a Stitch project with designed screens and want a portable design-system doc.
**How:** Invoke the skill with a project name/ID. The skill auto-discovers projects and screens via MCP, downloads the HTML, parses Tailwind/CSS, and synthesizes DESIGN.md.
**Prompt example:** `"Analyze Stitch project projects/123 and generate a DESIGN.md."`
**Output:** `DESIGN.md` in the project directory with 5 structured sections.

### Pattern 2: Source-of-truth creation for team alignment
**When:** Multiple agents or humans are generating screens in the same Stitch project and visual consistency is degrading.
**How:** Run the skill once on the "canonical" screen, commit the resulting DESIGN.md to the repo, then reference it in all future generation prompts. The shared vocabulary ("Deep Muted Teal-Navy" not "#294056") becomes the team's design language.
**Value:** The descriptive naming convention is the onboarding mechanism — a new agent reads DESIGN.md and immediately understands the design intent without parsing CSS.

### Pattern 3: Pre-build documentation (with extract-design-md)
**When:** You have a frontend codebase that hasn't been deployed to Stitch yet but you want to document its design system.
**How:** Use `extract-design-md` instead — it reads source files directly (no render needed). Then optionally hand off the resulting DESIGN.md to `manage-design-system` to push it into Stitch.
**Workflow:** `extract-design-md` (source → DESIGN.md) → `manage-design-system` (DESIGN.md → Stitch project)

### Pattern 4: Agent onboarding via shared vocabulary
**When:** A new agent joins a project and needs to understand the visual language quickly.
**How:** The DESIGN.md serves as a single readable document. The skill's "Common Pitfalls" section explicitly warns against technical jargon — the output is designed to be read by both humans and LLM agents without CSS knowledge. The evocative language ("Airy," "Pill-shaped," "Whisper-soft diffused shadows") is the shared vocabulary that eliminates ambiguity.

### Pattern 5: Design migration into Stitch
**When:** Migrating an existing app's design into Stitch for further iteration.
**How:** Two paths:
- If the app is already in Stitch → use `design-md` directly
- If only source code exists → use `extract-design-md` → then `upload-to-stitch` or `code-to-design`

### When to AVOID design-md (use extract-design-md instead)
| Condition | Why design-md fails | Why extract-design-md works |
|-----------|-------------------|---------------------------|
| Non-Stitch project | Requires Stitch MCP server + project with rendered screens | Reads source files directly, no Stitch needed |
| Offline / no MCP access | All 6 retrieval steps depend on Stitch MCP Server | Only needs `Read`, `Bash`, `Write` tools |
| No rendered screens yet | Requires at least one designed screen in Stitch | Works on source code before any render |
| Build is broken / deps missing | Can't get rendered HTML if app won't build | Reads source files only, no build needed |
| Need machine-parseable output | design-md produces plain markdown (no YAML) | extract-design-md requires YAML frontmatter with `name` + `colors` for downstream skill consumption |
| Multi-framework codebase | design-md only reads Stitch's HTML output | extract-design-md has framework-specific references for React, Vue, Svelte, Angular, plain CSS |

### When to AVOID both skills
- **No design system to extract** (greenfield project with no existing visual language)
- **Non-web projects** (mobile native, desktop GUI — neither skill handles these; `react-native` skill exists for RN but design-md/extract-design-md are web-focused)
- **Trivial designs** (single-screen, no meaningful token system — the overhead of DESIGN.md exceeds the value)

### Skill comparison matrix
| Dimension | design-md | extract-design-md |
|-----------|-----------|-------------------|
| Input source | Stitch MCP (rendered screens) | Frontend source code |
| MCP required | Yes (Stitch MCP Server) | No |
| Build required | No (reads Stitch's rendered output) | No (reads source files) |
| Framework support | N/A (Stitch output is HTML) | React, Vue, Svelte, Angular, plain CSS |
| Output format | Plain markdown, 5 sections | Markdown + **required YAML frontmatter** |
| Line count | 172 lines | 365 lines |
| Plugin | stitch-utilities | stitch-design |
| Phase structure | Linear (retrieve → analyze → synthesize) | 4-phase (discovery → extraction → write → optional integration) |
| Framework references | None | 5 framework-specific reference files |
| Downstream compatibility | Manual handoff to `manage-design-system` | YAML frontmatter enables automated parsing by other skills |
| Quality checklist | Implicit (best practices section) | Explicit 8-point checklist |
| Deduplication guidance | No | Yes (consolidate near-duplicate colors) |

---

## CONFIDENCE

**Overall: High (0.85)**

### What I'm confident about (0.90+)
- **Skill mechanics:** Full SKILL.md retrieved (172 lines) — the 6-step retrieval pipeline and 5-section output format are explicitly documented with usage examples.
- **Prerequisites and limitations:** Clearly stated — requires Stitch MCP Server, at least one designed screen, and the Effective Prompting Guide.
- **Alternative skill existence and behavior:** Full `extract-design-md` SKILL.md retrieved (365 lines) — the "Why This Exists" section explicitly states "The design-md skill works from rendered HTML. But often you have a codebase..." confirming the division of labor.
- **Output format difference:** design-md produces plain markdown; extract-design-md *requires* YAML frontmatter. This is an explicit "Important" callout in extract-design-md.
- **When to avoid:** Both skills' "When to Use" and "Prerequisites" sections make the boundary conditions unambiguous.

### What I'm less confident about (0.70-0.80)
- **Real-world retrieval reliability:** The 6-step MCP pipeline assumes the Stitch MCP Server exposes `list_tools`, `list_projects`, `list_screens`, `get_screen`, `get_project` with the described signatures. I verified the SKILL.md content but did not test against a live Stitch MCP instance.
- **Output quality in practice:** The skill prescribes "evocative natural language" but the actual quality depends on the LLM's design vocabulary. The "Common Pitfalls" section suggests this is a known failure mode.
- **YAML frontmatter absence in design-md:** The skills.sh summary page showed the SKILL.md truncated at "Retrieval and Networking / Show more." I retrieved the full content from GitHub's blob view, which showed no YAML frontmatter in the design-md output template. However, it's possible the skill was updated after my retrieval or that the frontmatter is implied by the `manage-design-system` skill's parsing logic. The extract-design-md skill's explicit warning about YAML suggests design-md's output may need manual wrapping before downstream consumption.
- **Install count accuracy:** Skills.sh reports 50.3K installs and 6.1K GitHub stars, first seen Jan 22, 2026. These are platform-reported metrics I could not independently verify.

### What I could not verify
- **Stitch MCP Server tool signatures:** The exact parameter names and response shapes for `list_projects`, `list_screens`, `get_screen`, `get_project` — documented in SKILL.md but not tested against a live server.
- **Framework reference file contents:** extract-design-md references 5 framework-specific markdown files (react-tailwind.md, vue.md, svelte.md, angular.md, plain-css.md) — I did not fetch these as they're supplementary to the core comparison.
- **Example DESIGN.md output:** Neither skill's example output file was fetched; the output format is inferred from the template structure in the SKILL.md.
- **Integration with `manage-design-system`:** The handoff between design-md → manage-design-system is implied but not explicitly documented in the design-md SKILL.md (extract-design-md explicitly mentions it in Phase 4).
