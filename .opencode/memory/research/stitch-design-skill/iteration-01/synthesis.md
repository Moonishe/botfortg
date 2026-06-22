# Synthesis: stitch-design skill deep research

## SUMMARY
stitch-design is a unified router skill for Google Stitch that sits between a user's rough UI idea and the Stitch MCP server. It transforms vague prompts into structured, design-system-aware generation instructions, synthesizes existing work into `.stitch/DESIGN.md`, routes requests to generation/edit/variant/documentation workflows, and manages local assets in `.stitch/designs`. It is part of a larger `stitch-skills` monorepo (plugins: `stitch-design`, `stitch-build`, `stitch-utilities`) published by `google-labs-code` under Apache-2.0. The skill is agent-facing, not human-facing: it instructs Claude Code, Cursor, Codex, Gemini CLI, or Antigravity how to call Stitch tools. The workflow is sound for marketing pages, dashboards, and mobile screens, but it is tightly coupled to Stitch cloud availability, requires a configured MCP server, and uses local helper scripts to bypass the model's output-token limits.

## KEY_FINDINGS
1. **Prompt enhancement is the core value**: vague input ("make a nice header") is mapped to professional UI terms ("sticky navigation bar with glassmorphism effect and centered logo") using `design-mappings.md` and `prompt-keywords.md`.
2. **Design system is project-level, not prompt-level**: once a `DESIGN.md` is uploaded and a design system created, generation prompts must describe only layout/content/structure; colors/fonts/roundness are applied automatically by Stitch.
3. **Four main request modes**: Generate from Text, Generate from Image, Edit, Generate Variants — all converge on the same asset-download and feedback steps.
4. **Code-to-design is a migration pipeline**: `extract-static-html` → `extract-design-md` → `manage-design-system` → `upload-to-stitch`. It is lossy (scripts removed, interactivity gone) but useful for bringing existing apps into Stitch.
5. **Asset upload uses a local Python script**: `upload_to_stitch.py` base64-encodes files in-process and calls the Stitch REST API directly, because MCP arguments would exceed model output-token limits.
6. **Static extraction tooling is mature**: `snapshot.ts` is a 600+ line Puppeteer script that materializes CSS-in-JS, inlines stylesheets/images/srcset/CSS url()/SVG/video posters/favicons, removes scripts/dev overlays, and handles iframes and full-height capture.
7. **Autonomous loops are supported via `stitch-loop`**: it uses `.stitch/next-prompt.md` as a baton, `.stitch/SITE.md` as a sitemap, and `.stitch/metadata.json` as persistent screen IDs.
8. **Security is mixed**: SSRF protection in snapshot scripts; Snyk shows "Warn" on skills.sh; the skill reads MCP config files to extract API keys, which is a sensitive trust boundary.
9. **Metrics show popularity, not maturity**: 25.3K installs, 6.1K stars, but only 66 commits, 2 releases, Mar 2026 first seen, 10 issues, 8 PRs.
10. **Not a Google-supported product**: explicitly stated in the repository.

## WORKFLOWS

### Generation
1. Detect mode (text / image / variant / edit).
2. Check `list_projects` and `list_design_systems`.
3. Enhance prompt with mappings and keywords; structure as `[Purpose] + PLATFORM + PAGE STRUCTURE`.
4. Call `generate_screen_from_text` / `edit_screens` / `generate_variants`.
5. Present `outputComponents` Text Description + Suggestions.
6. Download HTML/screenshot to `.stitch/designs/`.
7. Update `.stitch/metadata.json`.

### Editing
1. Identify screen via `list_screens` / `get_screen`.
2. Build targeted prompt: Location + Visuals + Structure; hex codes allowed for precise edits.
3. Call `edit_screens`.
4. Present feedback and re-download assets, overwriting local copies.

### Design system synthesis
1. Source from existing Stitch screens (`design-md`), source code (`extract-design-md`), or user description.
2. Write `.stitch/DESIGN.md` with YAML frontmatter (`name`, `colors`, `typography`, `rounded`, `spacing`).
3. Ask user for confirmation.
4. Upload `DESIGN.md` via `upload_to_stitch.py`.
5. Call `create_design_system_from_design_md`.
6. Apply to screens via `apply_design_system`.

## ROUTING_LOGIC
- **New screen from text** → `generate-design` Generate from Text.
- **New screen from image/mockup** → `upload-to-stitch` → `generate-design` Edit flow.
- **Modify existing screen** → `generate-design` Edit flow.
- **Explore variations** → `generate-design` Variants flow.
- **Create or update design system** → `manage-design-system` (and possibly `extract-design-md` or `design-md`).
- **Migrate existing frontend** → `code-to-design` orchestration.
- **Multi-page autonomous site** → `stitch-loop` baton pattern.
- If no design system exists for generation, delegate to `manage-design-system` first.

## ASSET_MANAGEMENT
- **Local staging directory**: `.stitch/designs/`.
- **Downloaded assets**: `{slug}.html` (generated HTML) and `{slug}.png` (screenshot).
- **Source of truth**: `.stitch/DESIGN.md`.
- **Project metadata**: `.stitch/metadata.json` tracks projectId, screen IDs, dimensions, design theme.
- **Upload helper**: `upload_to_stitch.py` handles PNG/JPG/WebP/HTML/MD, auto-detects MIME, sets `generatedBy` for documents.
- **No bidirectional sync**: local assets are refreshed only when the agent explicitly re-downloads after edits.

## RISKS
- **External dependency**: Stitch MCP server + Google API availability.
- **Token-limit workaround**: requires Python + local script; path and SSL issues can break the flow.
- **User confirmation checkpoints**: interrupt automation and CI/CD.
- **Lossy code-to-design**: scripts removed, dynamic behavior lost.
- **API key exposure**: skill reads sensitive MCP config files.
- **Snyk Warn**: security scan is not fully clean.
- **Young project**: limited commit/release history, unresolved issues/PRs.
- **No offline mode**: every meaningful operation requires cloud access.

## USAGE_PATTERNS
- **Best for**: marketing landing pages, mobile app screens, dashboard mockups, design-system migration, rapid visual prototyping.
- **Good for**: teams that already use Google Stitch and want agent-assisted generation.
- **Avoid for**: fully functional apps, heavy interactivity, production code without human review, environments where Stitch API or Puppeteer cannot run.
- **Sweet spot**: early-stage design exploration where consistency and speed matter more than final implementation fidelity.

## RECOMMENDATIONS_FOR_TELEGRAMHELPER
TelegramHelper is a Python aiogram/Telethon bot with SQLite/Qdrant. Direct adaptation of stitch-design is limited because:

1. **Different medium**: Stitch generates web/mobile screens; Telegram bots use inline keyboards, messages, photos, and web apps (Telegram WebApp/Mini Apps). Stitch's output format (HTML/screenshot) is not native to Telegram.
2. **No Stitch integration**: TelegramHelper has no Stitch MCP server or Google API dependency.
3. **What can be adapted**:
   - **Prompt enhancement pipeline**: the same `design-mappings.md` + `prompt-keywords.md` idea can be used to translate vague user requests ("make the bot look nicer") into structured Telegram UI descriptions ("inline keyboard with primary CTA button, compact card message, high-contrast accent").
   - **`.stitch/DESIGN.md` → `.opencode/DESIGN.md`**: a local design-system document for the bot's visual language (message templates, colors, typography, button styles) would improve consistency across bot messages.
   - **Asset management pattern**: download generated images/screenshots to a local directory (e.g., `.opencode/designs/` or `data/designs/`) for reuse in Telegram messages.
   - **Workflow routing**: classify user requests into "new message template", "edit existing template", "generate variant", "update bot design system".
   - **Code-to-design inversion**: for TelegramHelper, the relevant flow is more likely "design-to-code" — generate message templates/HTML from a description, then render them to PNG for Telegram.
4. **Concrete minimal adaptation**:
   - Create a lightweight `design-system.md` in the project root documenting Telegram-specific tokens (accent color, message bubble style, inline keyboard layout, font).
   - Add a small prompt-enhancement helper that converts vague bot-UI requests into structured Telegram message specs.
   - Store generated message images/screenshots in `data/designs/` instead of `.stitch/designs/`.
   - Do NOT add a Stitch MCP server dependency; use local rendering (e.g., HTML → PNG via Playwright/Chromium) if visual messages are needed.
5. **Risk**: avoid importing the heavy `stitch-skills` tooling (Puppeteer, Python upload scripts) into TelegramHelper unless the bot explicitly needs to interact with Stitch. The *workflow patterns* are portable; the *implementation* is not.

## CONFIDENCE
**Medium-High** for the workflow description and file structure (directly from SKILL.md and scripts). **Medium** for ecosystem maturity and production readiness (metrics are popularity signals, not quality signals). **High** for the limitations and risks (explicitly documented in skills and observed in code).

## GAPS
- No access to the exact Stitch MCP schema beyond the reference snippets; the full list of tools and their arguments is not published in the repo.
- No visibility into the 10 open GitHub issues or 8 PRs — could contain critical bugs or blockers.
- No runtime evaluation data: how much does prompt enhancement improve output quality? Not measured.
- No documented cost or rate limits for the Stitch API.
- No clear migration path from other design tools (Figma, Framer, etc.) into this workflow.
- The `stitch-design` skill page on skills.sh contains a "Show more" collapsed section; the full SKILL.md text was fetched from GitHub, but the exact rendering differences are not fully verified.
- No information about whether the skill can be extended with custom reference files or whether the Agent Skills standard supports private skill registries.
