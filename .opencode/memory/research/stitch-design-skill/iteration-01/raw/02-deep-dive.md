# Deep Dive: how stitch-design works end-to-end

## 1. The generation pipeline (`generate-design` SKILL.md)

### Mode detection
The skill first decides which flow to run:
- Text description → **Generate from Text**
- Image / screenshot / mockup → **Generate from Image**
- Modify existing screen → **Edit**
- Layout/color/content variations → **Generate Variants**

### Prompt enhancement pipeline
Before any Stitch call, the prompt is enriched:

1. **Analyze context**
   - `list_projects` → find/create `projectId`.
   - `list_design_systems` → if a design system exists, tokens are already applied project-wide; do NOT include colors/fonts/roundness in the generation prompt.
2. **Refine UI/UX terminology**
   - Use `references/design-mappings.md` to translate vague terms ("nice header" → "sticky navigation bar with glassmorphism effect and centered logo").
   - Use `references/prompt-keywords.md` for component names, adjectives, color roles, shape descriptions.
3. **Structure the final prompt**
   - Output format: `[Overall purpose]`, `**PLATFORM:**`, numbered `**PAGE STRUCTURE:**` sections.
   - For edits: focus on Location, Visuals, Structure.
4. **Present AI feedback**
   - After every tool call, surface `outputComponents` (Text Description + Suggestions) to the user.

### Generate from Text
```json
{
  "projectId": "...",
  "prompt": "[Enhanced Prompt]",
  "designSystem": "assets/...",
  "deviceType": "DESKTOP" // MOBILE, DESKTOP, TABLET
}
```
After generation:
- Download HTML and screenshot to `.stitch/designs/{slug}.html` and `.png`.
- If not perfect, enter the **Edit** flow rather than regenerating.

### Generate from Image
1. Delegate to `upload-to-stitch` to upload the image as a new screen.
2. Use `list_screens` to get the new `screenId`.
3. Call `edit_screens` with a descriptive intent prompt (not just "make it look like this").
4. Download assets.

### Edit flow
```json
{
  "projectId": "...",
  "selectedScreenIds": ["..."],
  "prompt": "[Targeted edit prompt]"
}
```
- After editing, overwrite local assets.
- Update `.stitch/metadata.json` with new screen IDs/titles.
- Iterate one focused change at a time.

### Variants flow
```json
{
  "projectId": "...",
  "selectedScreenIds": ["..."],
  "prompt": "[Direction for variants]",
  "variantOptions": {
    "variantCount": 3,
    "creativeRange": "EXPLORE", // REFINE, EXPLORE, REIMAGINE
    "aspects": ["LAYOUT", "COLOR_SCHEME"] // LAYOUT, COLOR_SCHEME, IMAGES, TEXT_FONT, TEXT_CONTENT
  }
}
```

## 2. Design-system synthesis (`manage-design-system` + `extract-design-md` + `design-md`)

### When no design system exists
- Extract from existing screens via `design-md` skill (reads rendered HTML/screenshot).
- Or extract from source code via `extract-design-md` (reads source files without building).
- Output: `.stitch/DESIGN.md` with YAML frontmatter (`name`, `colors`, `typography`, `rounded`, `spacing`).

### Creating a design system in Stitch
Two-step process:
1. Upload `DESIGN.md` via the `upload_to_stitch.py` script (base64 in-process, bypasses token limits).
2. Call `create_design_system_from_design_md` with the resulting `sourceScreen` and `screenInstance` IDs.

Important: user confirmation is required before uploading.

### Applying a design system
```json
{
  "projectId": "...",
  "assetId": "...",
  "selectedScreenInstances": [
    { "id": "...", "sourceScreen": "projects/.../screens/..." }
  ]
}
```
- Only pass `id` and `sourceScreen`; no dimensions.
- Filter out `type: "DESIGN_SYSTEM_INSTANCE"` entries.

## 3. Code-to-design migration (`code-to-design`)
Sequential orchestration:
1. `extract-static-html` → self-contained HTML file.
2. `extract-design-md` → `.stitch/DESIGN.md` from source.
3. `manage-design-system` → upload DESIGN.md and create design system.
4. `upload-to-stitch` → upload the standalone HTML.

This is the skill to use for "save", "migrate", or "upload" requests.

## 4. Static HTML extraction (`extract-static-html`)
Two strategies:
- **Strategy A (Puppeteer, recommended)**: headless Chrome snapshot, inlines CSS, base64 images, removes scripts/dev overlays. Supports full-height, dark mode, click interactions, fixed-element removal.
- **Strategy B (Browser subagent)**: manual interaction + DOM extraction; may truncate.
- **Fallback (MockPage.jsx)**: flatten React components to a single JSX file and inline with Babel; used when the app cannot run locally.

Key implementation details in `snapshot.ts`:
- Materializes CSS-in-JS (Emotion, styled-components, MUI) into DOM before serialization.
- Inlines stylesheets, images, srcset, CSS url(), SVG images, video posters, favicons.
- Removes scripts, Vite/Next/CRA dev overlays.
- Handles same-origin iframes recursively.
- Global timeout + guaranteed browser cleanup.

## 5. Upload mechanics (`upload-to-stitch`)
- Python script reads the file, base64-encodes it, and calls `POST /v1/projects/{projectId}/screens:batchCreate`.
- Supports `.png`, `.jpg`, `.jpeg`, `.webp`, `.html`, `.htm`, `.md`.
- Auto-detects MIME type.
- HTML/markdown get `screenType: "DOCUMENT"` and `generatedBy` field.
- Images get `screenType: "IMAGE"`.
- User confirmation checkpoint before running.

## 6. File outputs
| File | Purpose | Skill that owns it |
|------|---------|--------------------|
| `.stitch/DESIGN.md` | Design system source of truth | `manage-design-system`, `extract-design-md`, `design-md` |
| `.stitch/metadata.json` | Project IDs, screen IDs, design theme summary | `generate-design`, `manage-design-system`, `stitch-loop` |
| `.stitch/designs/*.html` | Downloaded generated HTML | `generate-design`, `stitch-loop` |
| `.stitch/designs/*.png` | Downloaded screenshots | `generate-design`, `stitch-loop` |
| `.stitch/SITE.md` | Site vision, sitemap, roadmap | `stitch-loop` |
| `.stitch/next-prompt.md` | Baton for autonomous loop | `stitch-loop` |

## 7. Stitch MCP data model observed
- `list_projects` → `name` = `projects/{id}`.
- `list_screens` / `get_screen` → returns `screenshot.downloadUrl`, `htmlCode.downloadUrl`, `width`, `height`, `deviceType`.
- `get_project` → `screenInstances` with `id`, `sourceScreen`, `x`, `y`, `width`, `height`, plus `designTheme`.
- `list_design_systems` → `name` = `assets/{assetId}`.
- Design theme enums: `colorMode` (LIGHT/DARK), `headlineFont`/`bodyFont`/`labelFont` (INTER, ROBOTO, OPEN_SANS, etc.), `roundness` (ROUND_FOUR, ROUND_EIGHT, ROUND_TWELVE, ROUND_FULL), `colorVariant` (FIDELITY, TONAL, VIBRANT, ...).
