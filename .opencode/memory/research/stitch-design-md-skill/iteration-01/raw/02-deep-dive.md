# 02 — Deep Dive: how design-md works

## Perspective
Line-by-line technical decomposition of the skill instructions, workflow, and output contract.

## Tools used
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/SKILL.md (full skill prompt)
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/README.md
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/examples/DESIGN.md (gold-standard output)
- `grep` on downloaded files for `Tailwind`, `border-radius`, `shadow`, `hex`, `get_screen`, `htmlCode`

## Skill metadata (frontmatter)
```yaml
---
name: design-md
description: Analyze Stitch projects and synthesize a semantic design system into DESIGN.md files
allowed-tools:
  - "stitch*:*"
  - "Read"
  - "Write"
  - "web_fetch"
---
```
The agent is allowed to call any Stitch MCP tool, read local files, write the output file, and fetch public URLs (HTML/CSS/screenshots).

## Retrieval workflow (MCP calls)
1. **Namespace discovery**: run `list_tools` to find the Stitch MCP prefix (e.g. `mcp_stitch:`).
2. **Project lookup**: `list_projects` with `filter: "view=owned"`; extract numeric ID from `name` (`projects/{id}`).
3. **Screen lookup**: `list_screens` with project ID; extract screen ID from target screen's `name`.
4. **Screen fetch**: `get_screen` with both IDs. Returns:
   - `screenshot.downloadUrl`
   - `htmlCode.downloadUrl`
   - `width`, `height`, `deviceType`
   - project metadata incl. `designTheme`
5. **Asset download**: `web_fetch` or `read_url_content` on `htmlCode.downloadUrl` (and optionally the screenshot).
6. **Project metadata**: `get_project` (full `projects/{id}`) for `designTheme` (color mode, fonts, roundness, custom colors) and device preferences.

## Analysis & synthesis steps
1. **Project Identity** — project title + numeric project ID.
2. **Atmosphere** — from screenshot + HTML, describe mood with evocative adjectives (airy, dense, minimalist, utilitarian).
3. **Color Palette** — for each key color provide:
   - descriptive natural name (e.g. "Deep Muted Teal-Navy")
   - hex code in parentheses
   - functional role (primary CTA, background, text, etc.)
4. **Geometry & Shape** — translate CSS/Tailwind values into physical language:
   - `rounded-full` → pill-shaped
   - `rounded-lg` → subtly rounded corners
   - `rounded-none` → sharp, squared-off edges
5. **Depth & Elevation** — describe shadow strategy (flat, whisper-soft, heavy drop shadows).

## Output format (DESIGN.md)
```markdown
# Design System: [Project Title]
**Project ID:** [Insert Project ID Here]

## 1. Visual Theme & Atmosphere
## 2. Color Palette & Roles
## 3. Typography Rules
## 4. Component Stylings
## 5. Layout Principles
```

The example file (`examples/DESIGN.md`) expands this into a richer real-world document covering:
- Primary Foundation / Accent & Interactive / Typography & Text Hierarchy / Functional States
- Hierarchy & Weights, Spacing Principles
- Buttons, Cards, Navigation, Inputs, Product Cards
- Grid & Structure, Whitespace Strategy, Alignment & Visual Balance, Responsive Behavior & Touch
- A section 6: "Design System Notes for Stitch Generation" with example prompts

## Best-practice rules enforced by the prompt
- Descriptive, not generic ("Ocean-deep Cerulean" not "blue").
- Include hex codes and pixel values in parentheses after natural language.
- Explain functional role, not just appearance.
- Be consistent in terminology.
- Translate technical jargon ("rounded-xl" → "generously rounded corners").

## Common pitfalls listed
- Using technical jargon without translation.
- Omitting color codes.
- Forgetting functional roles.
- Vague atmosphere descriptions.
- Ignoring subtle details (shadows, spacing).

## Bottom line
The skill is a highly structured prompt template. The actual heavy lifting is done by the **Stitch MCP server** (data retrieval) and the **agent's synthesis skill** (token extraction, translation, and markdown writing). No code or validation scripts are bundled with the skill.

## Sources
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/SKILL.md
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/README.md
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/examples/DESIGN.md
- Downloaded skill files in `%LOCALAPPDATA%\Temp\opencode\stitch-design-md-research`
