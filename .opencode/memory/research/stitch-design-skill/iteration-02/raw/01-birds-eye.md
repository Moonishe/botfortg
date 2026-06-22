# Researcher 1 — Bird's Eye View
## Source: skills.sh + GitHub README + raw SKILL.md files
## URL: https://www.skills.sh/google-labs-code/stitch-skills/stitch-design
## Repo: https://github.com/google-labs-code/stitch-skills
## Date: 2026-06-22

---

# SUMMARY

The `stitch-design` skill (published on skills.sh, 25.3K installs, 6.1K GitHub
stars, first seen Mar 13 2026) is the **unified design-system entry point** for
Google Stitch — an MCP-backed AI UI generation service at
stitch.withgoogle.com. It is the headline skill of the
`google-labs-code/stitch-skills` repo (Apache-2.0, 66 commits, v1.0 release
May 18 2026), which packages three plugins: `stitch-design` (core design
workflows), `stitch-build` (code generation), and `stitch-utilities` (helpers).

The skill's central value proposition: **transform vague prompts into
structured, design-system-aware generation instructions**, then route them to
one of four request modes. A project-level design system (DESIGN.md) enforces
visual consistency so that generation prompts never carry color/font tokens —
those live at the Stitch project level. Generated assets (HTML + screenshots)
are auto-downloaded to `.stitch/designs/`. An autonomous companion skill,
`stitch-loop`, enables continuous multi-page site building via a
`.stitch/next-prompt.md` baton file.

The architecture is agent-skill-standard compliant (SKILL.md mission control +
scripts/ + resources/ + examples/), MCP-tool-driven (`stitch*:*` allowed
tools), and orchestration-agnostic (CI/CD, human-in-loop, agent chains, or
manual).

---

# KEY_FINDINGS

## 1. Unified Router — Four Request Modes

The `stitch::generate-design` skill acts as a router. It inspects user intent
and dispatches to one of four flows:

| Mode | Trigger | Stitch MCP Tool | Key Behavior |
|------|---------|-----------------|--------------|
| **Generate from Text** | text description of a new screen | `generate_screen_from_text` | Enhanced prompt → project lookup → generate → present outputComponents → download to `.stitch/designs/` |
| **Generate from Image** | image / screenshot / mockup provided | `upload-to-stitch` then `edit_screens` | Upload image as new screen → refine with descriptive edit prompt |
| **Edit** | modify existing screen | `edit_screens` | Targeted changes by location/visuals/structure; hex codes ALLOWED here (unlike generation); overwrite previous local assets |
| **Generate Variants** | explore layout/color/content variations | `generate_variants` | variantCount 1–5, creativeRange REFINE/EXPLORE/REIMAGINE, aspects LAYOUT/COLOR_SCHEME/IMAGES/TEXT_FONT/TEXT_CONTENT |

**Routing decision logic** (from SKILL.md "Determine the Mode" section):
- text description → Generate from Text
- image/screenshot/mockup → Generate from Image
- modify existing → Edit
- variations wanted → Generate Variants

## 2. Prompt Enhancement Pipeline (Core Differentiator)

Before ANY Stitch tool call, the skill MANDATES a 4-step enhancement pipeline:

1. **Analyze Context** — `list_projects` for projectId; `list_design_systems`
   to check if design tokens already exist at project level. If no design
   system → delegate to `manage-design-system` skill FIRST.
2. **Refine UI/UX Terminology** — Consult `references/design-mappings.md` and
   `references/prompt-keywords.md` to replace vague terms ("nice header" →
   "sticky navigation bar with glassmorphism effect and centered logo").
3. **Structure the Final Prompt** — Template with PLATFORM / PAGE STRUCTURE
   (Header / Hero / Primary Content / Footer). Focus on layout, content,
   structure — NEVER colors/fonts/theme (those are project-level).
4. **Present AI Insights** — After tool call, always surface
   `outputComponents` (Text Description + Suggestions) to user.

**Critical guardrail (CAUTION block in SKILL.md):** Do NOT include hex codes,
font names, color palettes, or roundness values in a GENERATION prompt — they
conflict with project-level design system. Hex codes ARE acceptable in EDIT
prompts for precise color adjustments.

A standalone `enhance-prompt` skill (stitch-utilities plugin) provides the same
pipeline as a reusable utility, with a 4-step process: Assess Input → Check
for DESIGN.md → Apply Enhancements (UI/UX keywords, vibe adjectives, page
structure, color formatting) → Format Output.

## 3. Design System Is Project-Level (Not Per-Screen)

This is the architectural keystone. The `manage-design-system` skill establishes
a "source of truth" so all future screens share one visual language.

**Two-step design system creation in Stitch:**
1. Upload `.stitch/DESIGN.md` — Option A: Python uploader script
   (`upload_to_stitch.py`, base64-encodes markdown, bypasses output token
   limits, sends to `/v1/projects/{projectId}/screens:batchCreate`); Option B:
   direct `upload_design_md` MCP tool for files under ~5KB.
2. Call `create_design_system_from_design_md` with projectId +
   selectedScreenInstance (id + sourceScreen from upload step).

**Result:** Stitch holds design tokens (colorMode, font, roundness,
customColor, saturation) at the PROJECT level. Generation prompts must NOT
duplicate them. `apply_design_system` applies the system to existing screens.

**User-confirmation checkpoint:** Before uploading, the skill MUST pause and
ask the user for explicit approval (display name, key colors, fonts,
roundness) — a trust-boundary validation that is NOT simplified away.

**DESIGN.md structure** is defined by the `design-md` skill
(stitch-utilities). The `extract-design-md` skill can derive it directly from
frontend source code. The `taste-design` skill generates premium/anti-generic
DESIGN.md variants.

## 4. Asset Management — `.stitch/` Directory Convention

A consistent on-disk file structure governs all artifacts:

```
project/
├── .stitch/
│   ├── metadata.json   # Stitch project & screen IDs (persisted across iterations)
│   ├── DESIGN.md       # Visual design system (source of truth)
│   ├── SITE.md         # Site vision, sitemap, roadmap (stitch-loop)
│   ├── next-prompt.md  # The baton — current task (stitch-loop)
│   └── designs/        # Staging area for Stitch output
│       ├── {page}.html
│       └── {page}.png
└── site/public/        # Production pages (stitch-loop integration)
```

**metadata.json schema** (persisted, critical for edit/variant flows):
- `projectId`, `title`, `visibility`, `projectType`, `origin`, `deviceType`
- `designTheme`: colorMode (DARK/LIGHT), font (e.g. INTER), roundness
  (e.g. ROUND_EIGHT), customColor (#hex), saturation (int)
- `screens`: map of page-name → {id, sourceScreen, x, y, width, height}
- `metadata.userRole`: OWNER / EDITOR / VIEWER

**Asset download rules:**
- After EVERY generation/edit/variant, download HTML + screenshot URLs from
  `outputComponents` to `.stitch/designs/`.
- Naming: screen ID or descriptive slug.
- For screenshots: append `=w{width}` to the download URL (Google CDN serves
  low-res thumbnails by default; width comes from screen metadata).
- Edits OVERWRITE previous versions to keep local files current.
- After edit, update `.stitch/metadata.json` with any new screen IDs / title
  changes.

## 5. Autonomous Loops — stitch-loop with `.stitch/next-prompt.md` Baton

The `stitch-loop` skill (stitch-utilities plugin) enables continuous,
autonomous multi-page website development through a baton-passing pattern.

**Each iteration:**
1. **Read the baton** — parse `.stitch/next-prompt.md` (YAML frontmatter
   `page:` field → output filename; markdown body → prompt content, MUST
   include design system block from DESIGN.md Section 6).
2. **Consult context files** — `.stitch/SITE.md` (vision, projectId, sitemap,
   roadmap) and `.stitch/DESIGN.md` (visual style). Checks: don't recreate
   existing pages; pick from roadmap or creative-freedom section.
3. **Generate with Stitch** — discover MCP prefix via `list_tools`; get/create
   project; `generate_screen_from_text`; persist/update `metadata.json` via
   `get_project` after each screen.
4. **Integrate into site** — move HTML from `.stitch/designs/{page}.html` to
   `site/public/{page}.html`; fix asset paths; wire navigation (replace
   `href="#"` placeholders); ensure consistent headers/footers.
5. **Visual verification (optional)** — if Chrome DevTools MCP available:
   start local server, navigate, screenshot, compare against Stitch
   screenshot.
6. **Update site documentation** — mark sitemap page `[x]`, remove consumed
   creative-freedom idea, update roadmap.
7. **Prepare the next baton (CRITICAL)** — MUST update
   `.stitch/next-prompt.md` before completing or the loop breaks. Decide next
   page from roadmap/creative-freedom/new invention; write with proper YAML
   frontmatter and full design system block.

**Orchestration-agnostic:** CI/CD (GitHub Actions on next-prompt.md changes),
human-in-loop (developer reviews each iteration), agent chains (Jules API),
or manual (developer runs repeatedly).

**Common pitfalls enumerated:** forgetting to update next-prompt.md (breaks
loop), recreating existing pages, omitting design system block, leaving
placeholder links, not persisting metadata.json.

## 6. Full Plugin/Skill Taxonomy

### stitch-design plugin (v1.0.0) — Core design workflows
| Skill | Purpose |
|-------|---------|
| `stitch::code-to-design` | Convert frontend code (React/Vue) → Stitch Design via HTML extraction + design system + upload |
| `stitch::generate-design` | The unified router — generate from text/image, edit, variants (THIS is the 25.3K-installs skill) |
| `stitch::manage-design-system` | Upload DESIGN.md, create/apply design systems at project level |
| `stitch::extract-design-md` | Extract DESIGN.md directly from frontend source code |
| `stitch::extract-static-html` | Extract self-contained static HTML from running web apps (inline CSS/images) |
| `stitch::upload-to-stitch` | Upload local assets (images, mockups, HTML) to a Stitch project |

### stitch-build plugin — Code generation
| Skill | Purpose |
|-------|---------|
| `react-components` | Stitch screens → React component systems with validation + design token consistency |
| `react-native` | Stitch HTML → production React Native components (StyleSheet, platform-specific) |
| `remotion` | Walkthrough videos from Stitch projects (smooth transitions, zooming) |
| `shadcn-ui` | Expert guidance for shadcn/ui integration |

### stitch-utilities plugin — Helpers
| Skill | Purpose |
|-------|---------|
| `design-md` | Analyze Stitch projects → comprehensive DESIGN.md in semantic language |
| `enhance-prompt` | Vague UI ideas → polished Stitch-optimized prompts (standalone enhancer) |
| `stitch-loop` | Autonomous multi-page site builder via baton system |
| `taste-design` | Premium/anti-generic DESIGN.md with strict typography + calibrated colors |

## 7. Skill Standard Structure

Every skill follows the Agent Skills open standard (agentskills.io):
```
skills/<skill-name>/
├── SKILL.md           — Mission Control (YAML frontmatter: name, description, allowed-tools)
├── scripts/           — Executable enforcers (validation & networking)
├── resources/         — Knowledge base (checklists & style guides)
└── examples/          — Gold Standard syntactically valid references
```

YAML frontmatter declares `allowed-tools` — e.g. `stitch*:*`, `Bash`, `Read`,
`Write`, `web_fetch`, `chrome*:*` (for loop verification). MCP tool prefix
handling is delegated to the system prompt.

## 8. Installation & Compatibility

**Install command (skills.sh):**
```
npx skills add https://github.com/google-labs-code/stitch-skills --skill stitch-design
```

**Plugin install (recommended for full suite):**
- Codex: `codex plugin marketplace add google-labs-code/stitch-skills --ref main --sparse ...`
- Claude Code: `npx plugins add google-labs-code/stitch-skills --scope project --target claude-code`
- Cursor: `npx plugins add google-labs-code/stitch-skills --scope workspace --target cursor`

**Compatible agents:** Antigravity, Gemini CLI, Claude Code, Cursor, Codex,
GitHub Copilot, Windsurf, Cline, AMP, ClawdBot (per skills.sh agent list).

**Prerequisite:** Stitch MCP server must be configured and running
(stitch.withgoogle.com/docs/mcp/setup/).

**Security audits (skills.sh):** Gen Agent Trust Hub = Pass, Socket = Pass,
Snyk = Warn.

**Repo stats:** 6.1K stars, 739 forks, 54 watching, 66 commits, 2 releases,
TypeScript 89.6% / Shell 5.2% / Python 5.2%. First seen Mar 13 2026.

## 9. MCP Tool Surface (inferred from SKILL.md references)

Stitch MCP tools referenced across skills:
- `list_projects`, `create_project`, `get_project`
- `list_screens`, `get_screen`
- `list_design_systems`
- `generate_screen_from_text`
- `edit_screens`
- `generate_variants`
- `upload_design_md` (direct, <5KB)
- `create_design_system_from_design_md`
- `apply_design_system`
- `list_tools` (namespace discovery)
- `read_url_content`

Tool prefix is dynamic (discovered via `list_tools`); SKILL.md notes system
prompt handles MCP prefix mapping.

## 10. Key Design Principles Extracted

1. **Separation of concerns:** Content/structure in prompts; visual tokens at
   project level. Never mix.
2. **Iterative over regenerative:** Prefer targeted edits over full
   re-generation unless fundamental layout is wrong.
3. **One change at a time for edits:** Don't bundle unrelated changes.
4. **Persist everything:** metadata.json survives across iterations so edits/
   variants can reference prior screens.
5. **Human checkpoint at trust boundary:** Design system upload requires
   explicit user confirmation.
6. **Baton pattern for autonomy:** next-prompt.md is the single relay artifact
   that keeps loops alive.
7. **Content-first prompting:** Describe what each section contains, not how
   it looks.

---

# CONFIDENCE

**Overall: HIGH (0.88)**

| Aspect | Confidence | Basis |
|--------|-----------|-------|
| Four request modes (Generate Text/Image, Edit, Variants) | 0.98 | Full SKILL.md for generate-design fetched raw from GitHub — explicit "Determine the Mode" section with all four flows detailed |
| Prompt enhancement pipeline (4 steps) | 0.97 | Full SKILL.md content with CAUTION guardrails, templates, and references |
| Design-system is project-level | 0.97 | manage-design-system SKILL.md fetched raw — explicit two-step creation, project-level token storage, apply_design_system |
| Asset management (.stitch/designs/) | 0.96 | Confirmed in both generate-design and stitch-loop SKILL.md files; metadata.json schema fully documented |
| Autonomous loops (stitch-loop + next-prompt.md baton) | 0.97 | Full stitch-loop SKILL.md fetched raw — 6-step execution protocol, baton format, orchestration options, pitfalls |
| 25.3K installs / 6.1K stars | 1.00 | Direct from skills.sh page header |
| Full skill taxonomy (13 skills across 3 plugins) | 0.95 | GitHub README table + repo structure tree |
| MCP tool surface | 0.82 | Inferred from SKILL.md references, not from a formal tool schema doc (tool-schema.md referenced but not fetched) |
| Security audit details | 0.70 | Only pass/warn labels from skills.sh; no detail on Snyk Warn specifics |
| Inter-skill dependencies | 0.85 | README warns of dependencies but exact dependency graph not fully enumerated |

**Caveats:**
- skills.sh SKILL.md was truncated ("Show more"); compensated by fetching raw
  from GitHub raw.githubusercontent.com — which gave FULL content.
- Could not fetch references/design-mappings.md, references/prompt-keywords.md,
  or examples/enhanced-prompt.md (referenced but not retrieved) — content
  inferred from contextual descriptions in SKILL.md.
- metadata.json schema confirmed from stitch-loop SKILL.md (includes full JSON
  example with real-looking project ID and screen IDs).
- The `stitch-design` skills.sh page and the `stitch::generate-design` GitHub
  skill appear closely related — skills.sh presents a unified "Stitch Design
  Expert" system prompt that encompasses the routing logic detailed in
  generate-design/SKILL.md. The skills.sh page likely aggregates the plugin's
  top-level routing instructions.
