# Practitioner (Researcher 5) — stitch-design skill, iteration-02

Focus: best use cases, prompt enhancement adaptation for Telegram UI,
DESIGN.md to local design-system document, asset management pattern,
workflow routing for bot UI, design-to-code inversion for Telegram.

Sources: SKILL.md files fetched from
github.com/google-labs-code/stitch-skills (plugins/stitch-design/skills/*,
plugins/stitch-utilities/skills/*). Cross-referenced with TelegramHelper
bot UI components: src/bot/visual_tokens.py, src/bot/rich_messages.py,
src/bot/handlers/smart_keyboard.py, src/bot/ambient.py, skills/*.

---

## SUMMARY

The stitch-design skill family is a set of agent-facing instructions that
teach a coding agent (Claude Code, Cursor, Codex, Gemini CLI) how to drive
the Google Stitch MCP server to produce high-fidelity UI screens. The
practitioner-level value is not the Stitch cloud itself but the reusable
workflow patterns: (1) a prompt-enhancement pipeline that converts vague
intent into structured UI/UX specs, (2) a design-system-as-document
pattern where a single DESIGN.md becomes the project-level source of truth,
(3) a four-mode request router (generate/edit/variant/design-system), and
(4) an asset-staging convention where generated HTML + screenshots are
downloaded to a local directory for reuse.

For TelegramHelper these patterns are directly portable as workflow
methodology, even though the output medium differs (Telegram inline
keyboards + markdown messages vs. web/mobile HTML). The bot already has
the seeds of a design system -- visual_tokens.py (emoji vocabulary),
rich_messages.py (GFM formatting rules), smart_keyboard.py (keyboard
layout conventions) -- but lacks a consolidated design-system document and
a prompt-enhancement layer that translates vague user requests like "make
the menu prettier" into structured Telegram UI specs. The design-to-code
inversion (generate template then render to PNG then send as Telegram
photo) is the most novel adaptation: it lets the bot produce visual
previews of message layouts without Stitch, using local HTML-to-PNG
rendering via Playwright (already a configured MCP server in this project).

---

## USAGE_PATTERNS

### 1. Best use cases for the stitch-design workflow (as documented)

The skill docs and example prompts name three sweet spots:

| Use case | Skill flow | Why it fits | TelegramHelper relevance |
|----------|-----------|-------------|--------------------------|
| Marketing landing pages | generate-design (text to screen) | Single-purpose, visually rich, structure-heavy prompts (hero + features + CTA + footer) map cleanly to Stitch page-structure template | Low -- Telegram has no landing pages, but the structured-section-template idea applies to bot onboarding/welcome messages |
| Mobile app screens | generate-design (text to screen, deviceType: MOBILE) | Compact layouts, clear hierarchy, touch-target sizing | Medium -- Telegram mobile WebApp / Mini App screens could use this directly if the bot ever ships a WebApp |
| Dashboard mockups | generate-design (text to screen) + edit flow for refinement | Dense data tables, sidebar nav, chart widgets; iterative editing for polish | Medium-High -- bot status dashboards (/stats, /health, /audit) rendered as PNG images sent to chat |
| Design-system migration | code-to-design (extract HTML, extract DESIGN.md, upload) | Bringing existing React/Tailwind apps into Stitch for iteration | Low for code migration, but the extraction pattern (scan existing code, produce design-system doc) applies to TelegramHelper |
| Multi-page autonomous sites | stitch-loop (baton pattern) | Site-level sitemap + per-page generation loop | Low -- Telegram bots are conversational, not multi-page |

Practitioner verdict: the marketing-page and mobile-screen flows are
Stitch strongest use cases. For TelegramHelper the methodology (prompt
enhancement + design-system doc + asset staging) is more valuable than
the tool (Stitch cloud generation).

### 2. Prompt enhancement adaptation (vague to structured for Telegram UI)

The stitch enhance-prompt skill defines a 4-step pipeline:
1. Assess input (platform, page type, structure, visual style, components)
2. Check for DESIGN.md (inject design system block if exists)
3. Apply enhancements (UI/UX keywords, vibe adjectives, page structure, color format)
4. Format output (one-line description + DESIGN SYSTEM block + Page Structure)

Adapted for Telegram UI, the keyword mappings change from web terms to
Telegram-native terms:

| Vague user input | Web keyword (stitch) | Telegram keyword (adapted) |
|------------------|---------------------|---------------------------|
| "menu at the top" | "navigation bar with logo" | "main menu inline keyboard with emoji-prefixed buttons" |
| "button" | "primary call-to-action button" | "inline keyboard button with callback_data" |
| "list of items" | "card grid layout" | "numbered list with emoji bullets in markdown" |
| "form" | "form with labeled input fields" | "FSM state-based input flow with prompt message + validation reply" |
| "picture area" | "hero section with full-width image" | "photo message with caption and inline keyboard below" |
| "make it look nice" | "clean, minimal, generous whitespace" | "compact card with section headers (##), emoji tokens from visual_tokens.py, 2-column keyboard" |
| "show me stats" | "dashboard with chart widgets" | "Rich Message with markdown table + summary header + inline keyboard actions" |

Concrete adaptation for TelegramHelper -- a prompt-enhancement helper
function that takes a vague bot-UI request and returns a structured spec:

Input: "make the /stats command output prettier"
Output spec:
- Message type: Rich Message (GFM markdown, supports tables + headers)
- Structure:
  1. Header: "## Stats" with date range
  2. Summary row: 3 key metrics as emoji-prefixed inline values
  3. Table: markdown table with aligned columns
  4. Footer: inline keyboard with [Details] [Menu]
- Visual tokens: TIER_EMOJI for model tiers, SENTIMENT_EMOJI for sentiment
- Keyboard: 2-column layout, emoji prefix per button (smart_keyboard.py convention)
- Constraints: max 8000 chars (rich_messages.py RICH_MESSAGE_LIMIT), fallback to sendMessage if unsupported

The key rule from stitch (no theme leakage): never put hex codes or font
names in a generation prompt when a design system exists. Adapted: never
specify emoji choices or keyboard layout in a message-generation prompt
when DESIGN.md exists -- the design system handles all visual vocabulary.
This keeps message generation prompts focused on content and structure,
not styling.

### 3. .stitch/DESIGN.md to local design-system document

The stitch design-md skill produces a DESIGN.md with this structure:
1. Visual Theme and Atmosphere
2. Color Palette and Roles (descriptive name + hex + functional role)
3. Typography Rules
4. Component Stylings (buttons, cards, inputs)
5. Layout Principles

For TelegramHelper, the equivalent document would capture the bot
conversational design system -- not colors and fonts (Telegram controls
those), but the vocabulary and structural conventions that make bot
output consistent:

Proposed: docs/DESIGN.md (or src/bot/DESIGN.md) with sections:

1. Tone and Atmosphere -- conversational register (Russian, concise,
   emoji-augmented but not overwhelming), address formality, when to use
   markdown vs. plain text
2. Emoji Vocabulary -- canonical mapping from visual_tokens.py:
   sentiment (green/red/white circles), risk (red/yellow/green), task
   status (check/cross/clipboard/clock), entity kind (person/group/
   channel/bot), conversation status, relation status. Rule: one emoji
   per semantic concept, never repurpose.
3. Message Structure Patterns -- templates for recurring message types:
   - Status report: section header + summary line + table + keyboard
   - List/digest: numbered items with emoji bullets + footer keyboard
   - Error/confirmation: single-line emoji-prefixed + optional keyboard
   - Morning briefing (ambient.py): greeting + agenda + actionable keyboard
4. Keyboard Conventions -- from smart_keyboard.py:
   - 2 buttons per row for action pairs (primary + secondary)
   - Full-width single button for "Menu" navigation
   - Emoji prefix on every button label
   - callback_data format: {namespace}:{action}:{id}
5. Formatting Rules -- from rich_messages.py:
   - GFM markdown for Rich Messages (headers, tables, checklists)
   - 8000-char threshold to switch to Rich Message
   - 32768-char hard limit with truncation at paragraph boundary
   - Fallback to sendMessage if sendRichMessage returns 404/405
6. Section Layout Principles -- max 2 levels of headers, tables capped
   at ~10 rows for readability, summary-first (TL;DR before detail)

This document becomes the source of truth that a prompt-enhancement
layer references, exactly like Stitch DESIGN.md is referenced by
generate-design.

### 4. Asset management pattern (download to data/designs/)

Stitch downloads generated HTML + PNG screenshots to .stitch/designs/
with slug-based naming, and tracks project state in .stitch/metadata.json.

For TelegramHelper, the equivalent staging area is data/designs/:

    data/designs/
    +-- templates/          # Generated message template HTML (for PNG rendering)
    |   +-- stats-report.html
    |   +-- morning-briefing.html
    |   +-- digest-summary.html
    +-- renders/            # PNG renders of templates (sent as Telegram photos)
    |   +-- stats-report.png
    |   +-- morning-briefing.png
    |   +-- digest-summary.png
    +-- metadata.json       # Tracks template to render mapping, version, last-used

metadata.json structure (adapted from .stitch/metadata.json):

    {
      "templates": [
        {
          "id": "stats-report",
          "title": "Statistics Report",
          "htmlPath": "data/designs/templates/stats-report.html",
          "renderPath": "data/designs/renders/stats-report.png",
          "version": 3,
          "lastUsed": "2026-06-22T10:00:00Z",
          "renderEngine": "playwright"
        }
      ]
    }

Key difference from Stitch: no cloud dependency. Templates are generated
locally (LLM produces HTML from a structured spec), rendered locally
(Playwright/Chromium HTML-to-PNG), and sent as Telegram photos. No upload
script, no API key, no token-limit workaround needed.

### 5. Workflow routing for bot UI

Stitch generate-design skill routes requests to 4 flows based on intent
detection. Adapted for TelegramHelper bot UI requests:

| User intent | Stitch flow | TelegramHelper flow | Output |
|-------------|------------|---------------------|--------|
| "Create a new stats display" | Generate from Text | New template: enhance prompt, generate HTML from spec, render PNG, send photo + store in data/designs/templates/ | PNG photo + metadata.json entry |
| "Change the stats table to show 2 columns" | Edit flow (targeted) | Edit template: load existing HTML, apply edit (regex or LLM), re-render PNG, overwrite | Updated PNG |
| "Show me 3 layout options for the digest" | Generate Variants (variantCount: 3, creativeRange: EXPLORE) | Generate variants: produce N HTML variants with different layouts, render all, send as media group | Media group of N PNGs |
| "Update the bot visual style guide" | manage-design-system | Design system update: edit docs/DESIGN.md, re-render affected templates | Updated DESIGN.md + re-rendered templates |
| "Make the welcome message look like this screenshot" | Generate from Image | Image-to-template: analyze screenshot, generate matching HTML, render, send | PNG photo |

Routing logic (pseudo-code, no abstraction needed):

    if "design system" or "style guide" in request: design-system-update
    elif "variant" or "option" or "alternative" in request: generate-variants
    elif existing template referenced: edit-template
    elif image/screenshot provided: image-to-template
    else: new-template

### 6. Design-to-code inversion for Telegram

Stitch primary flow is text-to-design (generate HTML screens). The
code-to-design skill is the inversion (existing code to Stitch design).

For TelegramHelper the useful inversion is different: design-to-render.
The bot generates a message template (structured spec to HTML), then
renders it to PNG for display in Telegram. This is design-to-image,
not code-to-design:

    User request ("make a nice stats report")
        |
        v
    [Prompt enhancement] -- vague to structured spec (sections, tokens, keyboard)
        |
        v
    [Template generation] -- LLM produces self-contained HTML (inline CSS,
                             Telegram-like card styling, 400px wide
                             for mobile readability)
        |
        v
    [HTML-to-PNG render] -- Playwright headless: load HTML, screenshot element,
                            save to data/designs/renders/{slug}.png
        |
        v
    [Telegram send] -- bot.send_photo(chat_id, photo=png_path,
                      caption=short_summary, reply_markup=keyboard)
        |
        v
    [Asset tracking] -- update data/designs/metadata.json

Why this works for Telegram:
- Telegram photos render identically across all clients (unlike markdown
  which varies by client)
- Complex layouts (tables, multi-column, charts) that exceed markdown
  limits become feasible as images
- The HTML template is editable and re-renderable (edit flow)
- No Stitch cloud dependency -- Playwright is already a configured MCP
  server in this project

Constraints to document:
- HTML width: 400-600px (mobile-first, matches Telegram photo display)
- Font: system sans-serif, 14-16px base
- No external resources (inline everything, like stitch extract-static-html)
- No JavaScript (static render only)
- Max height: ~2000px (Telegram photo limit, taller = split into multiple)

---

## RECOMMENDATIONS

### R1: Create docs/DESIGN.md as the bot design-system source of truth
Priority: Medium. Effort: ~1 hour (one-time documentation).
Consolidate what already exists in visual_tokens.py, rich_messages.py,
smart_keyboard.py into a single reference document. This is the direct
adaptation of stitch DESIGN.md pattern. A prompt-enhancement layer can
then reference it the same way generate-design references .stitch/DESIGN.md.

### R2: Add a prompt-enhancement helper for bot UI requests
Priority: Medium-Low. Effort: ~50 lines.
A function enhance_bot_ui_prompt(vague_request: str) -> str that maps
vague terms to Telegram-native UI keywords (table in section 2 above)
and structures output as message-type + structure + tokens + keyboard +
constraints. This is the highest-value pattern from the stitch skill
family -- it makes "make it prettier" actionable.

### R3: Set up data/designs/ as a template + render staging area
Priority: Low (only if visual message rendering is wanted). Effort: ~2 hours.
Create data/designs/templates/, data/designs/renders/, metadata.json.
Use Playwright (already available as MCP server) for HTML-to-PNG rendering.
No Stitch dependency needed. The pattern (staging dir + slug naming +
metadata tracking) is lifted directly from .stitch/designs/.

### R4: Do NOT add Stitch MCP server as a dependency
Priority: High (negative recommendation).
Stitch is a cloud design tool for web/mobile screens. TelegramHelper is
a Telegram bot. Adding Stitch would introduce: external API dependency,
API key management, token-limit workarounds (Python upload scripts),
user-confirmation checkpoints that break bot automation, and output
(HTML screens) that does not map to Telegram message/keyboard model.
The workflow patterns are portable; the tool is not.

### R5: If visual dashboards are wanted, use the design-to-render pipeline
Priority: Low. Effort: ~3-4 hours for a first template.
The most valuable adaptation: generate self-contained HTML templates for
complex bot outputs (/stats, /health, /audit, morning briefing), render
to PNG via Playwright, send as Telegram photos. This solves the problem
of markdown tables looking inconsistent across Telegram clients. The HTML
templates live in data/designs/templates/ and are editable and re-renderable.

### R6: Steal the "no theme leakage" rule for bot prompts
Priority: Low. Effort: 0 (just a convention).
Stitch rule: never put hex codes or font names in a generation prompt
when a design system exists -- the design system handles all visual
styling. Adapted: never specify emoji choices or keyboard layout in a
message-generation prompt when DESIGN.md exists -- the design system
handles all visual vocabulary. This keeps message generation prompts
focused on content and structure, not styling.

---

## CONFIDENCE

High for the workflow pattern descriptions and the adaptation mapping
-- directly sourced from 6 SKILL.md files (generate-design, enhance-prompt,
manage-design-system, code-to-design, design-md, upload-to-stitch) fetched
from the canonical GitHub repository, and cross-referenced with
TelegramHelper actual bot UI source files (visual_tokens.py,
rich_messages.py, smart_keyboard.py, ambient.py).

High for the "do not add Stitch dependency" recommendation -- the
medium mismatch (web/mobile HTML screens vs. Telegram messages/keyboards)
is structural, not incidental.

Medium for the design-to-render (HTML-to-PNG) pipeline viability --
Playwright is available as an MCP server, but the actual rendering
quality, Telegram photo size limits, and mobile readability at 400-600px
width need empirical validation before committing to the approach.

Medium-Low for the prompt-enhancement keyword mappings (section 2) --
the web-to-Telegram term mappings are reasonable by analogy but untested
against actual LLM generation quality for Telegram message templates.
