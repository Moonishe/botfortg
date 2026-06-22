# Deep Dive — stitch-design skill (Researcher 2)

- Source: https://www.skills.sh/google-labs-code/stitch-skills/stitch-design
- Repo: https://github.com/google-labs-code/stitch-skills (branch `main`)
- Plugin path: `plugins/stitch-design/` (Agent Skills open standard; also packaged as Codex plugin under `.codex-plugin/plugin.json`)
- Retrieval date: 2026-06-22
- Retrieval method: webfetch on skills.sh (HTML+markdown) + raw.githubusercontent.com for all sub-skill SKILL.md files and reference files.

## 0. Structural note (important for routing analysis)

The skills.sh page renders a unified entry SKILL.md titled **"Stitch Design Expert"** with a `## Core Responsibilities` list and a `## Workflows` section ("Based on the user's request, follow one of these workflows:"). That section is **server-truncated** behind a client-side "Show more" toggle (gradient overlay `pointer-events-none absolute inset-x-0 bottom-0 h-24`); the full text is NOT in the static HTML and is loaded on click. The GitHub plugin directory `plugins/stitch-design/` contains **no standalone SKILL.md** — only `.codex-plugin/plugin.json`, `plugin.json`, and `skills/`. The `.codex-plugin/plugin.json` declares `"skills": "./skills/"`. Conclusion: the skills.sh "stitch-design" entry point is a **synthesized aggregate** produced by the skills.sh registry from the plugin's `interface` metadata + its 6 sub-skills. The routing matrix that the truncated "Workflows" section encodes is therefore reconstructed below from (a) the skills.sh summary bullet ("Routes requests intelligently between text-to-design generation, screen editing, and design system documentation workflows"), (b) the README "Available Plugins" prompt-example table, and (c) the **full text of every sub-skill SKILL.md** retrieved from raw GitHub. Confidence in the reconstruction is high because the sub-skills are self-describing and the user-supplied routing mapping matches the sub-skill boundaries exactly.

The plugin exposes 6 skills (`plugins/stitch-design/skills/`):
1. `generate-design` — new screens from text/image, edit, variants (the routing hub)
2. `code-to-design` — migrate existing frontend code into Stitch (orchestrator)
3. `manage-design-system` — DESIGN.md → Stitch design system + apply to screens
4. `extract-design-md` — reverse-engineer DESIGN.md from source code
5. `extract-static-html` — self-contained HTML from a running app
6. `upload-to-stitch` — Python uploader script (bypasses MCP base64 limits)

Cross-plugin dependencies (stitch-utilities): `enhance-prompt`, `stitch-loop`, `design-md`, `taste-design`.

---

## 1. ROUTING LOGIC

### 1.1 Top-level intent → skill mapping (the truncated "Workflows" section, reconstructed)

| User intent signal | Routed skill | Flow within skill |
|---|---|---|
| "Make / create / design a new screen" (text) | `generate-design` | Generate from Text flow |
| User provides image / screenshot / mockup | `generate-design` | Generate from Image flow |
| "Edit / change / add / tweak existing screen" | `generate-design` | Edit flow |
| "Variants / variations / explore alternatives / different layouts" | `generate-design` | Generate Variants flow |
| "Design system / theme / tokens / apply style / DESIGN.md" | `manage-design-system` | (synthesis + create/update + apply) |
| "Migrate / save / upload my app / React code into Stitch" | `code-to-design` | orchestrator (chains 3 skills) |
| "Multi-page site / build a 5-page website / whole site" | `stitch-loop` (utilities) | baton loop |

The `code-to-design` SKILL.md front-matter enforces routing with an explicit directive: **"ALWAYS use this skill when the user's intent is to move existing web apps or React components into Stitch (e.g., requests to 'save', 'migrate', or 'upload'). You must use this skill even for simple 'save' operations."** Similarly `upload-to-stitch` front-matter: **"ALWAYS use this skill when you need to upload visual assets, HTML pages, or design docs to Stitch, particularly when direct MCP tool calls fail or truncate due to base64 token limits."** And `extract-static-html`: **"Use this skill whenever you need to capture a specific UI state… even if the user just asks to 'save the HTML' or 'mock the view'."** These `ALWAYS`/`whenever` cues are the routing triggers.

### 1.2 Intra-skill routing inside `generate-design` (the "Determine the Mode" switch)

```
Decide which flow to use based on the user's request:
- create from a text description        -> Generate from Text flow (NEW SCREEN)
- provides an image/screenshot/mockup   -> Generate from Image flow
- modify an existing screen             -> Edit flow
- layout/color/content variations       -> Generate Variants flow (EXPLORE)
```

Mode → MCP tool mapping:
- Generate from Text → `generate_screen_from_text` (params: `projectId`, `prompt`, `designSystem`?, `deviceType` = MOBILE|DESKTOP|TABLET)
- Generate from Image → `upload-to-stitch` (upload) → `list_screens` (get screenId) → `edit_screens` (refine)
- Edit → `list_screens`/`get_screen` → `edit_screens` (params: `projectId`, `selectedScreenIds[]`, `prompt`)
- Variants → `generate_variants` (params: `projectId`, `selectedScreenIds[]`, `prompt`, `variantOptions{variantCount 1-5, creativeRange REFINE|EXPLORE|REIMAGINE, aspects[] LAYOUT|COLOR_SCHEME|IMAGES|TEXT_FONT|TEXT_CONTENT}`)

### 1.3 Routing guardrails (the "no theme leakage" rule)

A critical routing-adjacent invariant: **generation prompts must NEVER contain hex codes, font names, color palettes, or roundness values** — those are project-level design-system tokens applied by `manage-design-system`. The `generate-design` SKILL.md marks this `[!CAUTION]`. Exception: **edit** prompts MAY use hex codes for precise color adjustments. If `list_design_systems` shows no design system exists, the agent must **delegate to `manage-design-system` first** before generating screens. This makes `manage-design-system` a soft prerequisite for the Generate flow.

### 1.4 `code-to-design` routing = a fixed sequential chain (orchestrator)

`code-to-design` does not branch — it always runs the same 5-step pipeline (see WORKFLOWS §3). It delegates to 3 sibling skills in order: `extract-static-html` → `extract-design-md` → `manage-design-system` → `upload-to-stitch`.

---

## 2. PROMPT ENHANCEMENT PIPELINE

Two implementations exist; `generate-design` embeds the canonical 4-step pipeline inline; the `enhance-prompt` skill (stitch-utilities) is a standalone, lighter version. Both consume the same two knowledge-base files.

### 2.1 Canonical pipeline (`generate-design` SKILL.md, "Prompt Enhancement Pipeline")

> "Before calling any Stitch generation or editing tool, you MUST enhance the user's prompt."

**Step 1 — Analyze Context**
- Project: `list_projects` → find `projectId`; if none, `create_project`.
- Design System: `list_design_systems`. If one exists → tokens already applied at project level → do NOT include color/font/theme in prompt. If none → delegate to `manage-design-system` first.

**Step 2 — Refine UI/UX Terminology**
- Consult `references/design-mappings.md` to replace vague terms.
  - Vague "Make a nice header" → Pro "Sticky navigation bar with glassmorphism effect and centered logo"
- Consult `references/prompt-keywords.md` for component names, adjective palettes, color roles, shape descriptions.

**Step 3 — Structure the Final Prompt** (layout/content/structure only; NO theme tokens)
- New-screen template:
  ```
  [Overall purpose and user intent of the page]
  PLATFORM: [Web/Mobile], [Desktop/Mobile]-first
  PAGE STRUCTURE:
  1. Header: [...]
  2. Hero Section: [...]
  3. Primary Content Area: [...]
  4. Footer: [...]
  ```
- Edit template: Location → Visuals → Structure (hex codes allowed here).

**Step 4 — Present AI Insights**: always surface `outputComponents` (Text Description + Suggestions) to the user after every tool call.

Example artifact: `generate-design/examples/enhanced-prompt.md` shows "Make a landing page for my fitness app 'Pulse'…" → a 5-section structured prompt with ZERO color/font tokens (verified: the note explicitly says "This prompt contains NO colors, fonts, or theme instructions — those are handled by the project's design system").

### 2.2 Standalone `enhance-prompt` skill (stitch-utilities)

4 steps: **Assess Input** (table of missing elements: Platform, Page type, Structure, Visual style, Colors, Components) → **Check for DESIGN.md** (if exists, inject as `DESIGN SYSTEM (REQUIRED)` block; if not, append tip to create one via `design-md` skill) → **Apply Enhancements** (A: UI/UX keyword swap table; B: vibe adjectives; C: numbered page structure; D: color formatting `Descriptive Name (#hex) for role`) → **Format Output**. Output optionally written to `next-prompt.md` (the baton file for `stitch-loop`).

### 2.3 Knowledge base: `design-mappings.md` (full content retrieved)

Located at `plugins/stitch-design/skills/generate-design/references/design-mappings.md`. Four tables:
- **UI/UX Keyword Refinement**: "menu at the top"→"sticky navigation bar with logo and list items"; "big photo"→"high-impact hero section with full-width imagery"; "list of things"→"responsive card grid with hover states and subtle elevations"; "button"→"primary call-to-action button with micro-interactions"; "form"→"clean form with labeled input fields, validation states, and submit button"; "picture area"→"hero section with focal-point image or video background"; "sidebar"→"collapsible side navigation with icon-label pairings"; "popup"→"modal dialog with overlay and smooth entry animation".
- **Atmosphere / Vibe Descriptors**: Modern→"Clean, minimal, generous whitespace, high-contrast typography"; Professional→"Sophisticated, trustworthy, subtle shadows, restricted premium palette"; Fun/Playful→"Vibrant, organic, rounded corners, bold accents, bouncy micro-animations"; Dark Mode→"Electric, high-contrast accents on deep slate or near-black"; Luxury→"Elegant, spacious, fine lines, serif headers, high-fidelity photography"; Tech/Cyber→"Futuristic, neon accents, glassmorphism, monospaced typography".
- **Geometry & Shape Translation**: pill-shaped (`rounded-full`), softly rounded (`rounded-xl/2xl`), sharp/precise (`rounded-none/sm`), glassmorphism (semi-transparent + blur + thin borders).
- **Depth & Elevation**: Flat / Whisper-soft / Floating / Inset.

### 2.4 Knowledge base: `prompt-keywords.md` (full content retrieved)

Located at `plugins/stitch-design/skills/generate-design/references/prompt-keywords.md`. Progressive-disclosure reference:
- **Component Keywords**: Navigation (nav bar, breadcrumbs, tabs, sidebar, hamburger…); Content Containers (hero, card grid, modal, accordion, carousel); Forms (input, dropdown, checkbox, toggle, date picker, search bar, submit); CTAs (primary/secondary/ghost/FAB/icon button); Feedback (toast, snackbar, alert, spinner, skeleton, progress, step indicator); Layout (grid, flexbox, split view, sticky header, max-width container).
- **Adjective Palettes**: Minimal/Clean; Professional/Corporate; Playful/Fun; Premium/Luxury; Dark Mode; Organic/Natural.
- **Interaction & Behavior Keywords**: Animations (slide-in, fade, micro-interaction, shimmer); Responsiveness (mobile-first, breakpoint, stacked); User Flows (progressive disclosure, infinite scroll, drag-and-drop, swipe).
- **Shape Descriptions** table: `rounded-none`→"sharp, squared-off edges" … `rounded-full`→"pill-shaped, circular".

### 2.5 DESIGN.md producers (the "source of truth" the enhancer injects)

Three skills can produce `.stitch/DESIGN.md`:
- `design-md` (utilities) — synthesizes from a rendered Stitch screen (via MCP retrieval: `list_projects`→`list_screens`→`get_screen`→download HTML/screenshot→parse). Output structure: 1.Visual Theme & Atmosphere / 2.Color Palette & Roles / 3.Typography Rules / 4.Component Stylings / 5.Layout Principles.
- `extract-design-md` (design plugin) — extracts DESIGN.md **from source code** (no build needed). 4 phases: Project Discovery (detect framework via package.json/tailwind.config, map source tree, read framework-specific reference) → Deep Extraction (6 dimensions: Visual Theme, Color Palette & Roles, Typography, Component Stylings, Layout Principles, Stitch Generation Notes) → Write DESIGN.md (MUST include YAML frontmatter with `name` + `colors` mapping; placed at `.stitch/DESIGN.md`) → optional Integration handoff to `manage-design-system`.
- `taste-design` (utilities) — opinionated premium/anti-generic DESIGN.md generator. Enforces bans: no `Inter`, no emojis, no pure black, no neon glows, no 3-col equal card grids, no AI copywriting clichés ("Elevate", "Seamless"), no fabricated metrics. Encodes spring-physics motion, asymmetric heroes, max 1 accent color (<80% saturation). 7-section output adds "Motion & Interaction" + "Anti-Patterns (Banned)".

---

## 3. WORKFLOWS

### 3.1 `generate-design` — 4 flows (full steps)

**Generate from Text (New Screen)**
1. Enhance prompt (pipeline above). 2. `list_projects` → projectId. 3. `generate_screen_from_text{projectId, prompt, designSystem?, deviceType}`. 4. Surface `outputComponents`. 5. Download HTML + screenshot → `.stitch/designs/` (curl -o; filename = screen ID or slug). 6. Review; if minor issues use Edit flow, do NOT re-generate unless layout is fundamentally wrong.

**Generate from Image (Mockup → Design)**
1. `list_projects` → projectId (create if none). 2. Delegate to `upload-to-stitch` to upload image (creates a screen). 3. `list_screens` → new screenId → `edit_screens{projectId, selectedScreenIds[], prompt}` to refine (describe intent, not "make it look like this"). 4. Surface outputComponents. 5. Download assets to `.stitch/designs/`.

**Edit (Modify Existing Screen)**
1. `list_screens`/`get_screen` → projectId + screenId. 2. Enhance prompt with specificity (Location/Visuals/Structure; hex OK). 3. `edit_screens{projectId, selectedScreenIds[], prompt}`. 4. Surface outputComponents. 5. Download updated assets, **overwriting** previous versions. 6. Update `.stitch/metadata.json` (titles, new screen IDs). 7. Verify; repeat edit if more polish needed.

**Generate Variants (Explore)**
1. `list_screens`/`get_screen` → IDs. 2. `generate_variants{projectId, selectedScreenIds[], prompt, variantOptions}` (variantCount 1-5 default 3; creativeRange REFINE/EXPLORE/REIMAGINE; aspects LAYOUT/COLOR_SCHEME/IMAGES/TEXT_FONT/TEXT_CONTENT or empty=all). 3. Surface outputComponents. 4. Download to `.stitch/designs/`.

### 3.2 `code-to-design` — the migrate pipeline (orchestrator chain)

The exact chain the research brief asked about: **extract-static-html → extract-design-md → manage-design-system → upload**.

1. **Extract Self-Contained HTML** — delegate to `extract-static-html` → produces e.g. `/path/to/extracted/standalone.html`.
2. **Verify HTML (optional, user-driven)** — inform user of path; do NOT block; proceed to step 3.
3. **Extract Design System (file)** — delegate to `extract-design-md` → writes `.stitch/DESIGN.md` from source.
4. **Upload DESIGN.md + Create Design System in Stitch** — delegate to `manage-design-system` (runs `upload_to_stitch.py` with `--generated-by 'stitch::code-to-design'`, then `create_design_system_from_design_md`). **User-confirmation checkpoint required before upload.**
5. **Upload HTML to Stitch** — use `upload-to-stitch` script with the standalone HTML, `--generated-by 'stitch::extract-static-html'`.

Prerequisites: built web app dir with `index.html` + assets; target projectId.

### 3.3 `extract-static-html` — 2 strategies (+ fallback)

User MUST choose (checkpoint with pros/cons table; recommend Strategy A):
- **Strategy A — Puppeteer Snapshot (recommended)**: `npx tsx <SKILL_DIR>/scripts/snapshot.ts --url http://localhost:5173 --output .stitch/home.html --wait 2000`. Flags: `--url`, `--output`, `--wait` (default 1000), `--viewport 1280x800`, `--html-class dark`, `--remove-fixed`, `--full-height`, `--title`. Auto: inlines `<link>` CSS→`<style>`, converts `<img>` src+srcset→base64 (skips fonts), inlines `<source srcset>`, removes dead srcset entries, removes `<script>`/Vite overlay/Next dev indicators, resolves relative CSS `url()`. Framework notes: React+Vite `--wait 1000`; Next.js `--wait 3000`; Storybook use story URL. Checkpoint before running snapshot.
- **Strategy B — Browser Subagent Capture**: for pages needing interaction (click/fill/nav) before capture; `document.documentElement.outerHTML`; large pages may truncate (remove `<style>` first, re-add statically).
- **Appendix — Static Fallback (MockPage.jsx)**: last resort when app can't run (broken deps, auth walls). `npx tsx <SKILL_DIR>/scripts/extract_inline_html.ts --index-css … --outdir .stitch --page src/MockPage.jsx:Page.html:"Title"`. Rules: include full layout, flatten conditionals, hardcode all data, preserve logos via local `<img>`, remove floating elements. Post-process inlines local images via `post_process.ts`.

### 3.4 `manage-design-system` — synthesis + create + apply

**Retrieval**: `list_projects`→`list_screens`→`get_screen` (get `screenshot.downloadUrl` + `htmlCode.downloadUrl`)→`read_url_content` to fetch HTML.
**Synthesis from description** (new project): map vague→precise via design mappings → pick hex/fonts/roundness → generate DESIGN.md (refer to `design-md` skill structure).
**Create/Update in Stitch — 2 steps** (user-confirmation checkpoint before upload):
  - Step 1 Upload DESIGN.md: Option A (recommended) `upload_to_stitch.py --project-id … --file-path …/DESIGN.md --api-key … --generated-by …` → returns `sourceScreen` + `screenInstance` IDs; Option B (only if <~5KB) `upload_design_md` MCP tool with base64 `designMdBase64`.
  - Step 2 `create_design_system_from_design_md{projectId, selectedScreenInstance{id, sourceScreen}}` immediately after upload.
**Apply to screens**: `apply_design_system{projectId, assetId, selectedScreenInstances[{id, sourceScreen}]}`. Get IDs via `get_project` (screenInstances) + `list_design_systems` (name `assets/{assetId}`); filter out `type: "DESIGN_SYSTEM_INSTANCE"`. CAUTION: instances must contain ONLY `id`+`sourceScreen` — no x/y/width/height or request fails "invalid argument".
**Update metadata**: write/update `.stitch/metadata.json` (projectId, title, screens, designSystem summary) per `examples/metadata.json`.

### 3.5 `stitch-loop` — multi-page baton loop (stitch-utilities)

Autonomous iterative site builder. Baton file = `.stitch/next-prompt.md` (YAML frontmatter `page: <name>` + prompt body). Per iteration:
1. Read baton (page name + prompt). 2. Consult `.stitch/SITE.md` (vision, projectId, sitemap, roadmap) + `.stitch/DESIGN.md` (copy Section 6 "Design System Notes for Stitch Generation" into prompt). 3. Discover MCP prefix via `list_tools`; get/create project; persist `.stitch/metadata.json` (call `get_project` after each screen); `generate_screen_from_text`; download `htmlCode.downloadUrl`→`.stitch/designs/{page}.html` and `screenshot.downloadUrl` (append `=w{width}` for hi-res)→`.stitch/designs/{page}.png` (if files exist, ask user before re-downloading). 4. Integrate: move HTML to `site/public/{page}.html`, fix asset paths, wire placeholder `href="#"` links, add to nav, consistent header/footer. 4.5 Optional Chrome DevTools MCP visual verification. 5. Update SITE.md (sitemap `[x]`, consume roadmap/creative-freedom item). 6. **Write next baton** (pick from roadmap/creative-freedom; MUST update or loop breaks).
Orchestration-agnostic: CI/CD (GH Actions on baton change), human-in-loop, agent chains, or manual. Pitfalls: forgetting to update baton, recreating existing pages, omitting design-system block, leaving placeholder links, not persisting metadata.json.

---

## 4. ASSET MANAGEMENT

### 4.1 The `.stitch/` directory contract (the persistent state layer)

```
project/
└── .stitch/
    ├── DESIGN.md        # source-of-truth design system (YAML frontmatter: name, colors)
    ├── metadata.json    # Stitch project + screen IDs (persist across iterations)
    ├── SITE.md          # (stitch-loop) vision, sitemap, roadmap
    ├── next-prompt.md   # (stitch-loop) the baton — current task
    └── designs/         # downloaded Stitch output (HTML + screenshots)
        ├── {page}.html
        └── {page}.png
```

### 4.2 `.stitch/DESIGN.md` — the "source of truth"

- Produced by `design-md` / `extract-design-md` / `taste-design`.
- `extract-design-md` REQUIRES YAML frontmatter with `name` and `colors` mapping (structured data other skills parse). Failure to include = incorrect skill use.
- Consumed by: `enhance-prompt` (injects as `DESIGN SYSTEM (REQUIRED)` block), `stitch-loop` (copies Section 6 into every baton prompt), `manage-design-system` (uploads + creates project-level design system so generation prompts stay theme-free).
- Standard sections: 1.Visual Theme & Atmosphere / 2.Color Palette & Roles / 3.Typography Rules / 4.Component Stylings / 5.Layout Principles / 6.Stitch Generation Notes (taste-design adds 6.Motion & Interaction, 7.Anti-Patterns).
- Colors encoded as `Descriptive Name (#hex) — functional role` (e.g., "Deep Muted Teal-Navy (#294056) — Primary CTA, active navigation"); near-duplicates consolidated; intent described, not just raw values.

### 4.3 `.stitch/metadata.json` — the ID persistence layer

Two schemas coexist (compatible):
- `manage-design-system/examples/metadata.json` (lighter):
  ```json
  {"projectId":"…","title":"…","screens":[{"screenId":"…","title":"…","deviceType":"DESKTOP"}],"designSystem":{"assetId":"…","primaryColor":"#1a365d","secondaryColor":"#718096","backgroundColor":"#f7fafc","fontFamily":"INTER"}}
  ```
- `stitch-loop` schema (fuller, populated via `get_project`):
  ```json
  {"name":"projects/{id}","projectId":"{id}","title":"…","visibility":"PRIVATE","createTime":"…","updateTime":"…","projectType":"PROJECT_DESIGN","origin":"STITCH","deviceType":"MOBILE","designTheme":{"colorMode":"DARK","font":"INTER","roundness":"ROUND_EIGHT","customColor":"#40baf7","saturation":3},"screens":{"index":{"id":"…","sourceScreen":"projects/…/screens/…","x":0,"y":0,"width":390,"height":1249},"about":{…}},"metadata":{"userRole":"OWNER"}}
  ```
Purpose: persist Stitch identifiers so future iterations can reference screens for edits/variants without re-listing. Updated after every `create_project` / screen generation / edit (call `get_project` then update the `screens` map).

### 4.4 `upload_to_stitch.py` — the asset uploader (bypasses MCP base64 token limits)

Located at `plugins/stitch-design/skills/upload-to-stitch/scripts/upload_to_stitch.py`. Rationale (from SKILL.md): the AI model cannot upload files via MCP directly because base64 of even a small file exceeds the model's output token limit (~16K tokens); the script reads the file and sends it directly over HTTP.

**CLI**:
```
python3 <SKILL_DIR>/scripts/upload_to_stitch.py \
  --project-id <PROJECT_ID> --file-path <PATH> --api-key <API_KEY> \
  [--api-url <STITCH_API_URL>] [--title <SCREEN_TITLE>] [--generated-by <GENERATED_BY>]
```
- Endpoint: `POST /v1/projects/{projectId}/screens:batchCreate` (base64-encodes file in-process).
- Supported types (MIME auto-detected from extension): `.png` image/png, `.jpg/.jpeg` image/jpeg, `.webp` image/webp, `.html/.htm` text/html, `.md` text/markdown.
- `--api-url` defaults to `https://stitch.googleapis.com`.
- `--generated-by` defaults to `UserUploadedDesignMd` if omitted; set to calling skill name (`stitch::code-to-design`, `stitch::extract-static-html`) or agent (`Gemini`, `Claude Code`, `Codex`).
- **API key discovery** (agent config files): Antigravity `.gemini/antigravity/mcp_config.json` or `.gemini/jetski/mcp_config.json`; Gemini CLI `~/.gemini/settings.json` or `~/.gemini/extensions/Stitch/gemini-extension.json`; Claude Code `~/.claude.json`. Extract from `X-Goog-Api-Key` header or auth arg. If not found → MUST ask user (do not proceed).
- SSL troubleshooting (macOS): script auto-uses `certifi` if installed; else set `SSL_CERT_FILE=$(python3 -c "import certifi; print(certifi.where())")`.
- **User-confirmation checkpoint required** before running (present file paths/sizes/types, wait for approval).

### 4.5 Asset download behavior (post-generation)

Every generation/edit/variant flow ends by downloading `outputComponents` URLs to `.stitch/designs/` via `curl -o` (or `run_command`); filename = screen ID or descriptive slug; overwrite on edit to keep local files current. `stitch-loop` appends `=w{width}` to screenshot downloadUrl (Google CDN serves low-res thumbnails by default) and asks before re-downloading existing files.

---

## 5. CROSS-SKILL DEPENDENCY GRAPH

```
code-to-design (orchestrator)
 ├─ extract-static-html   (snapshot.ts / extract_inline_html.ts / post_process.ts)
 ├─ extract-design-md     (writes .stitch/DESIGN.md from source; needs YAML frontmatter)
 ├─ manage-design-system  (upload_to_stitch.py + create_design_system_from_design_md + apply_design_system)
 └─ upload-to-stitch      (upload_to_stitch.py — shared script)

generate-design (hub)
 ├─ uses design-mappings.md + prompt-keywords.md (references/)
 ├─ delegates image upload → upload-to-stitch
 └─ soft-prereq: manage-design-system (if no design system exists)

stitch-loop (utilities)
 ├─ reads .stitch/DESIGN.md (from design-md / extract-design-md / taste-design)
 ├─ reads/writes .stitch/SITE.md, .stitch/next-prompt.md, .stitch/metadata.json
 └─ optional chrome-devtools MCP for visual verification

enhance-prompt (utilities) — standalone prompt polisher; reads DESIGN.md; writes next-prompt.md
```

---

## 6. CONFIDENCE

- **Sub-skill SKILL.md content (generate-design, code-to-design, manage-design-system, upload-to-stitch, extract-static-html, extract-design-md, enhance-prompt, stitch-loop, design-md, taste-design)**: retrieved in FULL from raw.githubusercontent.com (HTTP 200, complete bodies). Confidence: **HIGH (verbatim)**.
- **design-mappings.md + prompt-keywords.md**: retrieved in FULL (HTTP 200). Confidence: **HIGH (verbatim)**.
- **plugin.json + .codex-plugin/plugin.json**: retrieved in FULL. Confidence: **HIGH**.
- **enhanced-prompt.md example + metadata.json example**: retrieved in FULL. Confidence: **HIGH**.
- **Unified skills.sh "Stitch Design Expert" SKILL.md workflows section**: NOT directly retrievable — server-truncated behind "Show more" (content loaded client-side on click; not in static HTML). The GitHub repo has NO standalone entry SKILL.md (confirmed via tree view: plugin dir = `.codex-plugin/`, `skills/`, `plugin.json` only). Routing matrix in §1.1 is **reconstructed** from the skills.sh summary bullet, the README prompt-example table, the sub-skill front-matter `ALWAYS`/`whenever` directives, and the generate-design "Determine the Mode" switch. The reconstruction matches the user-supplied routing mapping exactly. Confidence: **HIGH for the mapping itself, MEDIUM for verbatim phrasing of the truncated workflows section** (the exact bullet wording of the unified SKILL.md is unverified; the semantic routing is confirmed by 3 independent sources).
- **Not retrieved (out of scope or unfetchable)**: `upload_to_stitch.py` source code, `snapshot.ts` source, framework-specific `extract-design-md` references (react-tailwind.md, vue.md, svelte.md, angular.md, plain-css.md), `manage-design-system/reference/tool-schema.md`, `extract-design-md/examples/DESIGN.md`. These were not needed to answer the routing/prompt-enhancement/asset/code-to-design questions.
