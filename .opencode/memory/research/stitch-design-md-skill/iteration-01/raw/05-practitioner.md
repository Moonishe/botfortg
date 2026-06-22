# 05 — Practitioner: how to use it, and how it maps to TelegramHelper

## Perspective
Practical, hands-on reading: what does an adopter need, and what would adaptation look like?

## Tools used
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/README.md (install & example prompt)
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/SKILL.md (workflow prerequisites)
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-design/skills/manage-design-system/SKILL.md (downstream upload/apply workflow)
- `glob` on TelegramHelper workspace: `**/*.html`, `**/*.css`, `**/*.js`, `**/*.ts`
- `grep` on TelegramHelper for `design|DESIGN|stitch|Tailwind|tailwind`

## How to use the skill as intended
1. Install:
   ```bash
   npx skills add google-labs-code/stitch-skills --skill design-md --global
   ```
2. Ensure the **Stitch MCP server** is configured and authenticated.
3. Prompt the agent with something like:
   ```text
   Analyze my Furniture Collection project's Home screen and generate a comprehensive DESIGN.md file documenting the design system.
   ```
4. The agent will:
   - discover the Stitch MCP prefix
   - list projects, find the target, extract project/screen IDs
   - fetch screen metadata, HTML/CSS, and screenshot
   - write `DESIGN.md` in the project directory
5. (Optional) hand off to `manage-design-system` to upload the `DESIGN.md` back into Stitch and apply it as a design system.

## Typical use cases
- You have an existing Stitch design and want a **portable, human-readable source of truth** for prompting future screens.
- You want to reduce prompt drift by centralizing colors, typography, and component language.
- You are onboarding a teammate or another agent and need a shareable design spec.
- You want to audit the visual language of one screen before scaling to many screens.

## When NOT to use it
- You do not have Stitch access or the Stitch MCP server.
- You have a frontend codebase but no Stitch project — use `stitch-design/skills/extract-design-md` instead.
- You need a design system for a non-web platform (e.g., native iOS/Android) without a web-rendered equivalent.
- You need automated, deterministic token extraction — the skill is prompt-based and model-dependent.

## Mapping to TelegramHelper
TelegramHelper is a Python/aiogram/Telethon backend bot (no `.html`, `.css`, `.ts`, or `.js` UI files; checked via `glob`). It is primarily a **chat-bot** with no rendered web UI.

### Direct applicability
- **Low.** `design-md` is built to consume Stitch screen HTML/CSS and emit a visual design system. TelegramHelper has no Stitch screens and no web UI.
- The only Stitch-related content in the workspace is the term "design" in generic memory/notes, not actual UI code.

### Adaptation opportunities
1. **Telegram Mini App.** If TelegramHelper later adds a Telegram Mini App (web UI), the rendered HTML/CSS of that Mini App could be analyzed using the same semantic extraction approach — though still not through the Stitch MCP server.
2. **Bot personality/style guide.** The *semantic documentation idea* (descriptive names, color roles, typography rules, component vocabulary) can be reused for a **bot interaction style guide** — e.g., a `STYLE.md` for bot messages, commands, replies.
3. **Extract from source code.** TelegramHelper has no CSS, but if it had HTML templates (e.g., for web admin panel), the `extract-design-md` pattern could be adapted to generate a `DESIGN.md` directly from source.
4. **Use as a prompt template.** The DESIGN.md structure (atmosphere, palette, typography, components, layout) can be repurposed as a template for documenting **any UI-like surface**, even if the source is not Stitch.

## Recommended adoption path for TelegramHelper
- **Do not install or use `design-md` in its current form.** There is no Stitch project to feed it.
- **If a web UI (Mini App) is added later**, evaluate `extract-design-md` first, then consider a custom adaptation of the `design-md` semantic format.
- **If you want the same idea for the bot**, create a `STYLE.md` for tone, command names, reply formatting, and emoji/spacing conventions — inspired by the structure of `design-md`, but not the skill itself.

## Sources
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/README.md
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/SKILL.md
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-design/skills/manage-design-system/SKILL.md
- TelegramHelper workspace `glob` results (no HTML/CSS/JS UI files)
- Downloaded skill files in `%LOCALAPPDATA%\Temp\opencode\stitch-design-md-research`
