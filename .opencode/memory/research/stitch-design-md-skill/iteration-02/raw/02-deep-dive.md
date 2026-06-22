# Deep Dive Research: Stitch DESIGN.md Skill
# Researcher 2 (Deep Dive) — iteration-02
# Source: https://www.skills.sh/google-labs-code/stitch-skills/design-md
# Raw SKILL.md: https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/SKILL.md
# Gold-standard example: https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/examples/DESIGN.md
# Date: 2026-06-22

---

## SUMMARY

The Stitch `design-md` skill is a utility skill in the `stitch-utilities` plugin that analyzes Stitch design projects via the Stitch MCP Server and synthesizes a "Semantic Design System" into a `DESIGN.md` file. The file acts as a "source of truth" for prompting Stitch to generate new screens that align with existing design language. The skill's core innovation is the **semantic translation technique**: converting technical CSS/Tailwind values (e.g., `rounded-lg`, `#294056`) into evocative natural language descriptions (e.g., "subtly rounded corners", "Deep Muted Teal-Navy") that Stitch's generation engine interprets better than raw technical tokens.

**Repository:** google-labs-code/stitch-skills (GitHub, 6.1K stars, Apache-2.0)
**Skill path:** `plugins/stitch-utilities/skills/design-md/`
**Skill structure:**
```
design-md/
├── SKILL.md           — Core instructions & workflow
├── examples/          — Sample DESIGN.md outputs (Furniture Collections List)
└── README.md          — Skill overview
```

**Frontmatter (from SKILL.md):**
```yaml
name: design-md
description: Analyze Stitch projects and synthesize a semantic design system into DESIGN.md files
allowed-tools:
  - "stitch*:*"
  - "Read"
  - "Write"
  - "web_fetch"
```

**Allowed tools:** All Stitch MCP tools (wildcard `stitch*:*`), Read, Write, web_fetch. No bash, no grep, no edit — this is a retrieval + synthesis skill, not a code-editing skill.

---

## WORKFLOW

The SKILL.md defines a 6-step retrieval and networking pipeline, followed by a 5-step analysis & synthesis phase.

### Phase 1: Retrieval and Networking (6 steps)

**Step 1 — Namespace discovery:**
- Run `list_tools` to find the Stitch MCP prefix.
- Use this prefix (e.g., `mcp_stitch:`) for all subsequent calls.
- Purpose: MCP tool prefixes are dynamic per environment; this step discovers the actual prefix.

**Step 2 — Project lookup (if Project ID not provided):**
- Call `[prefix]:list_projects` with `filter: "view=owned"` to retrieve all user projects.
- Identify the target project by title or URL pattern.
- Extract the Project ID from the `name` field (e.g., `projects/13534454087919359824`).
- Note: Project ID is the full path string `projects/{numeric_id}`.

**Step 3 — Screen lookup (if Screen ID not provided):**
- Call `[prefix]:list_screens` with the `projectId` (just the numeric ID, NOT the full path).
- Review screen titles to identify the target screen (e.g., "Home", "Landing Page").
- Extract the Screen ID from the screen's `name` field.
- Note: Both `projectId` and `screenId` passed as numeric IDs only.

**Step 4 — Metadata fetch:**
- Call `[prefix]:get_screen` with both `projectId` and `screenId` (both as numeric IDs only).
- Returns complete screen object including:
  - `screenshot.downloadUrl` — Visual reference of the design
  - `htmlCode.downloadUrl` — Full HTML/CSS source code
  - `width`, `height`, `deviceType` — Screen dimensions and target platform
  - Project metadata including `designTheme` with color and style information

**Step 5 — Asset download:**
- Use `web_fetch` or `read_url_content` to download the HTML code from `htmlCode.downloadUrl`.
- Optionally download the screenshot from `screenshot.downloadUrl` for visual reference.
- Parse the HTML to extract Tailwind classes, custom CSS, and component patterns.

**Step 6 — Project metadata extraction:**
- Call `[prefix]:get_project` with the project `name` (full path: `projects/{id}`) to get:
  - `designTheme` object with color mode, fonts, roundness, custom colors
  - Project-level design guidelines and descriptions
  - Device type preferences and layout principles

### Phase 2: Analysis & Synthesis (5 steps)

**Step 1 — Extract Project Identity (JSON):**
- Locate the Project Title.
- Locate the specific Project ID (e.g., from the `name` field in the JSON).

**Step 2 — Define the Atmosphere (Image/HTML):**
- Evaluate the screenshot and HTML structure to capture the overall "vibe."
- Use evocative adjectives to describe the mood (e.g., "Airy," "Dense," "Minimalist," "Utilitarian").

**Step 3 — Map the Color Palette (Tailwind Config/JSON):**
- Identify the key colors in the system. For each color, provide:
  - A descriptive, natural language name that conveys its character (e.g., "Deep Muted Teal-Navy")
  - The specific hex code in parentheses for precision (e.g., "#294056")
  - Its specific functional role (e.g., "Used for primary actions")

**Step 4 — Translate Geometry & Shape (CSS/Tailwind):**
- Convert technical `border-radius` and layout values into physical descriptions:
  - `rounded-full` → "Pill-shaped"
  - `rounded-lg` → "Subtly rounded corners"
  - `rounded-none` → "Sharp, squared-off edges"

**Step 5 — Describe Depth & Elevation:**
- Explain how the UI handles layers.
- Describe the presence and quality of shadows (e.g., "Flat," "Whisper-soft diffused shadows," or "Heavy, high-contrast drop shadows").

### Phase 3: Output Generation
- Generate a clean Markdown file following the prescribed DESIGN.md structure.
- Ensure all color codes are accurate.
- Use evocative, designer-friendly language.
- Create `DESIGN.md` in the project directory.

### Complete workflow condensed:
```
list_tools (discover prefix)
  → [prefix]:list_projects (filter: "view=owned") → extract Project ID
  → [prefix]:list_screens (projectId: numeric) → extract Screen ID
  → [prefix]:get_screen (projectId + screenId: both numeric) → get downloadUrls
  → web_fetch htmlCode.downloadUrl → parse HTML/CSS/Tailwind
  → [prefix]:get_project (name: "projects/{id}") → get designTheme
  → ANALYZE: identity, atmosphere, colors, geometry, depth
  → SYNTHESIZE: semantic translation → write DESIGN.md
```

---

## OUTPUT_FORMAT

The SKILL.md prescribes an exact Markdown structure for DESIGN.md. Five mandatory sections, plus the gold-standard example adds a sixth "Design System Notes for Stitch Generation" section.

### Prescribed Structure (from SKILL.md):
```markdown
# Design System: [Project Title]
**Project ID:** [Insert Project ID Here]

## 1. Visual Theme & Atmosphere
(Description of the mood, density, and aesthetic philosophy.)

## 2. Color Palette & Roles
(List colors by Descriptive Name + Hex Code + Functional Role.)

## 3. Typography Rules
(Description of font family, weight usage for headers vs. body, and letter-spacing character.)

## 4. Component Stylings
* **Buttons:** (Shape description, color assignment, behavior).
* **Cards/Containers:** (Corner roundness description, background color, shadow depth).
* **Inputs/Forms:** (Stroke style, background).

## 5. Layout Principles
(Description of whitespace strategy, margins, and grid alignment.)
```

### Section-by-section detail (from gold-standard example):

**Section 1 — Visual Theme & Atmosphere:**
- Describes the mood, density, and aesthetic philosophy.
- Uses evocative adjectives: "sophisticated, minimalist sanctuary," "airy yet grounded," "spacious and tranquil."
- Includes "Key Characteristics" bullet list (6 items in example).
- Key pattern: describe the "vibe" first, then list characteristics.

**Section 2 — Color Palette & Roles:**
- Groups colors into sub-categories: Primary Foundation, Accent & Interactive, Typography & Text Hierarchy, Functional States.
- Each color entry format: `**[Descriptive Name]** ([hex code]) – [functional role description]`
- Example: `**Deep Muted Teal-Navy** (#294056) – The sole vibrant accent in the palette. Used exclusively for primary call-to-action buttons...`
- Functional roles are specific: "Primary background color," "Secondary surface color," "Primary text color for headlines."

**Section 3 — Typography Rules:**
- States primary font family and character description.
- Hierarchy & Weights sub-section: lists each text level (H1, H2, H3, Body, Small/Meta, CTA) with weight, letter-spacing, size, and usage.
- Spacing Principles sub-section: letter-spacing strategy, line-height, vertical rhythm, margins.
- Example entry: `**Display Headlines (H1):** Semi-bold weight (600), generous letter-spacing (0.02em for elegance), 2.75-3.5rem size.`

**Section 4 — Component Stylings:**
- Sub-sections per component: Buttons, Cards & Product Containers, Navigation, Inputs & Forms, Product Cards (Specific Pattern).
- Each component includes: Shape/Corner Style, Background, Shadow, Border, Padding, Hover/Focus states, Typography.
- Example: `**Shape:** Subtly rounded corners (8px/0.5rem radius) – approachable and modern without appearing playful or childish`

**Section 5 — Layout Principles:**
- Sub-sections: Grid & Structure, Whitespace Strategy, Alignment & Visual Balance, Responsive Behavior & Touch.
- Includes concrete values: max content width (1440px), grid columns, breakpoints, base spacing units, touch target sizes (44x44px WCAG AAA).
- Whitespace strategy is marked as "Critical to the Design."

**Section 6 (bonus from example) — Design System Notes for Stitch Generation:**
- Not in the prescribed template, but included in the gold-standard example.
- Provides explicit prompting language for Stitch: "Language to Use," "Color References," "Component Prompts," "Incremental Iteration."
- Translates the design system back into prompt-ready phrases.
- Example: `**Button Shapes:** "Subtly rounded corners" (not "rounded-md" or "8px")`

### Output Guidelines (from SKILL.md):
- **Language:** Use descriptive design terminology and natural language exclusively.
- **Format:** Clean Markdown following the structure above.
- **Precision:** Include exact hex codes for colors while using descriptive names.
- **Context:** Explain the "why" behind design decisions, not just the "what."

### Best Practices (from SKILL.md):
- **Be Descriptive:** Avoid generic terms like "blue" or "rounded." Use "Ocean-deep Cerulean (#0077B6)" or "Gently curved edges."
- **Be Functional:** Always explain what each design element is used for.
- **Be Consistent:** Use the same terminology throughout the document.
- **Be Visual:** Help readers visualize the design through your descriptions.
- **Be Precise:** Include exact values (hex codes, pixel values) in parentheses after natural language descriptions.

### Common Pitfalls to Avoid:
- Using technical jargon without translation (e.g., "rounded-xl" instead of "generously rounded corners").
- Omitting color codes or using only descriptive names.
- Forgetting to explain functional roles of design elements.
- Being too vague in atmosphere descriptions.
- Ignoring subtle design details like shadows or spacing patterns.

---

## SEMANTIC TRANSLATION TECHNIQUE

This is the core innovation of the skill. The technique converts technical CSS/Tailwind values into evocative natural language descriptions that Stitch's generation engine interprets better than raw technical tokens.

### Translation Rules (from SKILL.md Step 4):

| Technical Value | Semantic Description |
|---|---|
| `rounded-full` | "Pill-shaped" |
| `rounded-lg` | "Subtly rounded corners" |
| `rounded-none` | "Sharp, squared-off edges" |

### Color Translation (from SKILL.md Step 3 + example):

| Technical Value | Semantic Name | Hex Code |
|---|---|---|
| Tailwind class / hex | "Deep Muted Teal-Navy" | #294056 |
| Tailwind class / hex | "Warm Barely-There Cream" | #FCFAFA |
| Tailwind class / hex | "Crisp Very Light Gray" | #F5F5F5 |
| Tailwind class / hex | "Charcoal Near-Black" | #2C2C2C |
| Tailwind class / hex | "Soft Warm Gray" | #6B6B6B |
| Tailwind class / hex | "Ultra-Soft Silver Gray" | #E0E0E0 |

### Pattern for semantic naming:
- Format: `[Evocative adjective] [Mood/character] [Base color family]`
- The name conveys character, not just hue: "Deep Muted Teal-Navy" tells you it's dark, desaturated, and between teal and navy.
- The hex code provides precision in parentheses.
- The functional role explains usage: "Used exclusively for primary call-to-action buttons."

### Shadow Translation:
- `shadow-none` / flat → "Flat by default"
- `shadow-sm` → "Whisper-soft diffused shadows"
- `shadow-lg` / `shadow-xl` → "Heavy, high-contrast drop shadows"
- Example exact value: `0 2px 8px rgba(0,0,0,0.06)` → "whisper-soft diffused shadow"

### Anti-patterns (what NOT to do):
- "rounded-md" or "8px" → should be "Subtly rounded corners"
- "shadow-sm" → should be "Whisper-soft diffused shadows on hover"
- "blue" → should be "Ocean-deep Cerulean (#0077B6)"
- Technical jargon without translation is explicitly forbidden.

### The "Why" behind semantic translation:
From the SKILL.md: "Stitch interprets design through 'Visual Descriptions' supported by specific color values." The semantic language IS the interface to Stitch's generation engine. Technical tokens (Tailwind classes) are the wrong abstraction level for prompting; natural language descriptions align with how Stitch processes design intent.

---

## GOLD-STANDARD EXAMPLE: Furniture Collections List

The example DESIGN.md is located at:
`plugins/stitch-utilities/skills/design-md/examples/DESIGN.md`

### Key metadata:
- **Project Title:** Furniture Collections List
- **Project ID:** 13534454087919359824
- **Design Style:** Scandinavian minimalist + luxury editorial
- **Primary Font:** Manrope
- **Primary Accent Color:** Deep Muted Teal-Navy (#294056)
- **Background:** Warm Barely-There Cream (#FCFAFA)

### Color Palette (from example):

**Primary Foundation:**
- Warm Barely-There Cream (#FCFAFA) — Primary background
- Crisp Very Light Gray (#F5F5F5) — Secondary surface / card backgrounds

**Accent & Interactive:**
- Deep Muted Teal-Navy (#294056) — Sole vibrant accent, primary CTAs, active nav, selected filters

**Typography & Text Hierarchy:**
- Charcoal Near-Black (#2C2C2C) — Headlines, product names
- Soft Warm Gray (#6B6B6B) — Body copy, descriptions, metadata
- Ultra-Soft Silver Gray (#E0E0E0) — Borders, dividers, structural elements

**Functional States:**
- Success Moss (#10B981) — Stock availability, confirmations
- Alert Terracotta (#EF4444) — Low stock, errors, critical alerts
- Informational Slate (#64748B) — Neutral system messages

### Typography (from example):
- **Font:** Manrope (modern geometric sans-serif with humanist warmth)
- **H1:** Semi-bold 600, letter-spacing 0.02em, 2.75-3.5rem
- **H2:** Semi-bold 600, letter-spacing 0.01em, 2-2.5rem
- **H3:** Medium 500, normal letter-spacing, 1.5-1.75rem
- **Body:** Regular 400, line-height 1.7, 1rem
- **Small/Meta:** Regular 400, line-height 1.5, 0.875rem
- **CTA:** Medium 500, letter-spacing 0.01em, 1rem

### Component Stylings (from example):

**Buttons:**
- Shape: Subtly rounded corners (8px/0.5rem)
- Primary CTA: Deep Muted Teal-Navy (#294056) bg, white text, padding 0.875rem/2rem
- Hover: Subtle darkening, 250ms ease-in-out
- Focus: Soft outer glow

**Cards:**
- Corners: Gently rounded (12px/0.75rem)
- Background: Alternates Cream / Light Gray
- Shadow: Flat default, whisper-soft on hover (`0 2px 8px rgba(0,0,0,0.06)`)
- Border: Optional 1px Ultra-Soft Silver Gray
- Padding: 2-2.5rem internal

**Navigation:**
- Clean horizontal, 2-3rem between items
- Medium weight 500, uppercase, letter-spacing 0.06em
- Active: Deep Muted Teal-Navy, 2px underline
- Mobile: Hamburger with sliding drawer

**Inputs:**
- 1px Soft Warm Gray border
- Cream bg, Light Gray on focus
- 8px/0.5rem corners (matches buttons)
- Focus: Teal-Navy border + glow
- Padding: 0.875rem/1.25rem

### Layout (from example):
- **Max width:** 1440px
- **Grid:** 12-column, gutters 24px mobile / 32px desktop
- **Product grid:** 4 col desktop / 3 / 2 tablet / 1 mobile
- **Breakpoints:** <768px, 768-1024px, 1024-1440px, >1440px
- **Base spacing:** 8px micro, 16px component, 32px (2rem) vertical rhythm
- **Section margins:** 5-8rem (80-128px)
- **Touch targets:** 44x44px (WCAG AAA)

### Section 6 (Stitch Generation Notes from example):
This section is NOT in the SKILL.md template but was added in the example. It provides:
- **Language to Use:** Prompt-ready phrases for atmosphere, button shapes, shadows, spacing
- **Color References:** Descriptive names WITH hex codes
- **Component Prompts:** Full example prompts for Stitch
- **Incremental Iteration:** 3-step process for refining existing screens

Example prompt language from Section 6:
- "Sophisticated minimalist sanctuary with gallery-like spaciousness"
- "Subtly rounded corners" (not "rounded-md" or "8px")
- "Whisper-soft diffused shadows on hover" (not "shadow-sm")
- "Generous breathing room" and "expansive whitespace"

---

## CONFIDENCE

**Overall confidence: HIGH (95%)**

### Confidence breakdown:

| Aspect | Confidence | Rationale |
|---|---|---|
| Workflow steps (6 retrieval + 5 synthesis) | 99% | Fetched complete SKILL.md from raw GitHub — full content, no truncation |
| Output format (5 sections + template) | 99% | Exact Markdown template quoted verbatim from SKILL.md |
| Semantic translation technique | 95% | Translation rules explicitly stated in SKILL.md Step 4; color examples from gold-standard DESIGN.md |
| Gold-standard example (Furniture Collections) | 95% | Full example DESIGN.md fetched from raw GitHub — all 6 sections captured verbatim |
| MCP tool call signatures | 85% | Tool names and parameters described in SKILL.md prose, but exact JSON schemas not provided — inferred from usage patterns |
| Section 6 (Stitch Generation Notes) | 90% | Present in example but NOT in prescribed template — unclear if it's mandatory or optional enrichment |

### Sources fetched:
1. skills.sh page (truncated, showed summary + frontmatter only)
2. GitHub repo README.md (full — revealed skill path structure)
3. SKILL.md raw (full — complete workflow + template + best practices)
4. examples/DESIGN.md raw (full — complete gold-standard example, all 6 sections)

### Gaps / Limitations:
- Exact MCP tool JSON schemas (request/response) are NOT in the SKILL.md — only described in prose. Would need Stitch MCP Server docs for exact schemas.
- The `list_tools` call for namespace discovery is mentioned but the expected return format is not documented.
- The `read_url_content` tool is mentioned as alternative to `web_fetch` but not in the `allowed-tools` frontmatter — possible discrepancy.
- Section 6 of the example is not in the prescribed template — ambiguity about whether it's a recommended addition or just the example author's enrichment.
- No information on how the skill handles multi-screen projects (the workflow fetches ONE screen; multi-screen synthesis strategy is not documented).
- No information on error handling (what if project has no screens, what if downloadUrl is expired, etc.).
