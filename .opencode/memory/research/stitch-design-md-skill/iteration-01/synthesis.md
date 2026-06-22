# Synthesis: stitch-design-md / design-md skill

## SUMMARY
The `design-md` skill (from Google Labs' `stitch-skills` repo) is a prompt-only Agent-Skills utility that instructs a coding agent to act as a Design Systems Lead and synthesize a semantic `DESIGN.md` from a Google Stitch project. It is popular (50.2K installs, 6.1K GitHub stars) but tightly coupled to the Stitch MCP server: it retrieves screen metadata, HTML/CSS, and screenshots via MCP calls, then translates Tailwind/CSS values into natural-language design tokens. The skill is recent (first seen Jan 22, 2026, plugin-architecture refactor in May 2026) and ships as a Markdown prompt with no validation scripts, relying entirely on the LLM for extraction quality.

## KEY FINDINGS
1. **Prompt-only artifact.** The skill is a `SKILL.md` plus one example output; there are no bundled scripts, tests, or validation tools.
2. **Hard Stitch dependency.** It cannot run without the Stitch MCP server, a project with at least one designed screen, and the user's ability to call `list_projects`, `list_screens`, `get_screen`, and `get_project`.
3. **Structured workflow.** It discovers the MCP prefix, resolves project/screen IDs, fetches `htmlCode.downloadUrl` and `screenshot.downloadUrl`, and extracts atmosphere, colors, typography, components, and layout.
4. **Semantic translation is the core value.** Its main job is converting technical CSS/Tailwind (e.g., `rounded-lg`, `#294056`) into evocative, design-friendly language (e.g., "subtly rounded corners", "Deep Muted Teal-Navy") while keeping exact hex codes in parentheses.
5. **Output format is fixed.** The generated `DESIGN.md` must contain: Visual Theme & Atmosphere, Color Palette & Roles, Typography Rules, Component Stylings, Layout Principles.
6. **Gold-standard example is rich.** The sample `Furniture Collections List` DESIGN.md adds responsive behavior, touch targets, and a "Design System Notes for Stitch Generation" section with reusable prompts.
7. **Alternative exists.** The repo also contains `extract-design-md`, which reads frontend source code (React, Vue, Tailwind configs, CSS vars) and is more deterministic, working without a live Stitch project.
8. **Quality is model-dependent.** There is no automated check that colors, roles, or component descriptions are correct; hallucinations and missed tokens are possible.
9. **Security/audit note.** skills.sh shows Snyk "Warn" (others Pass), and the repo states it is not an officially supported Google product.
10. **Not applicable to TelegramHelper as-is.** TelegramHelper is a backend Telegram bot with no HTML/CSS UI; the skill cannot be fed any Stitch screens. The *semantic documentation pattern* could inspire a `STYLE.md` for bot interactions, but the skill itself is not useful here unless a web UI (e.g., Telegram Mini App) is added later.

## WORKFLOW
1. **Discover MCP namespace** — call `list_tools` to find the Stitch prefix (e.g., `mcp_stitch:`).
2. **Lookup project** — `list_projects(filter="view=owned")`, extract numeric ID from `projects/{id}`.
3. **Lookup screen** — `list_screens(projectId)`, extract screen ID from the desired screen.
4. **Fetch screen metadata** — `get_screen(projectId, screenId)` → screenshot URL, HTML/CSS URL, dimensions, device type, `designTheme`.
5. **Download assets** — `web_fetch` or `read_url_content` on the HTML/CSS (and optionally screenshot).
6. **Fetch project metadata** — `get_project(name="projects/{id}")` for project-level `designTheme` and guidelines.
7. **Extract & synthesize** — parse HTML/CSS, identify colors, typography, spacing, components, layout; translate technical values into natural language; assign functional roles.
8. **Write `DESIGN.md`** — follow the prescribed sections; place in project directory (often `.stitch/DESIGN.md`).
9. **Optional integration** — hand off to `manage-design-system` to upload the `DESIGN.md` back into Stitch and apply it as a project-level design system.

## OUTPUT_FORMAT
```markdown
# Design System: [Project Title]
**Project ID:** [numeric ID]

## 1. Visual Theme & Atmosphere
(Rich, evocative description of mood, density, aesthetic philosophy, and key characteristics.)

## 2. Color Palette & Roles
### Primary Foundation
- **Descriptive Name** (#hex) — functional role
### Accent & Interactive
- **Descriptive Name** (#hex) — functional role
### Typography & Text Hierarchy
- **Descriptive Name** (#hex) — functional role
### Functional States
- **Descriptive Name** (#hex) — role (success, error, info, warning)

## 3. Typography Rules
(Font family, character, hierarchy/weights, sizes, letter-spacing, line-height, spacing principles.)

## 4. Component Stylings
- **Buttons:** shape, color, states, transitions
- **Cards/Containers:** corner radius, background, shadow, border, padding
- **Navigation:** layout, typography, active/hover states, mobile behavior
- **Inputs/Forms:** stroke, background, focus, padding
- **Domain-specific components:** as relevant

## 5. Layout Principles
(Grid, max-width, breakpoints, whitespace strategy, alignment, responsive behavior, touch targets.)

## 6. Design System Notes for Stitch Generation (optional but recommended)
- Atmosphere language
- Color references
- Example component prompts
- Incremental iteration tips
```

## RISKS
- **Stitch MCP dependency.** If the MCP server is unavailable, unauthenticated, or rate-limited, the skill fails entirely.
- **ID extraction brittleness.** Manual parsing of project/screen IDs from `name` fields is error-prone.
- **No validation.** No bundled script verifies the extracted tokens or the generated markdown.
- **Model hallucination.** Colors, roles, or component descriptions may be invented or mislabeled.
- **Upstream API drift.** Stitch API changes may break the MCP tool schema without notice.
- **Output size.** The generated `DESIGN.md` can exceed the ~5KB direct-upload limit; the skill does not warn about this.
- **Snyk warning.** skills.sh reports a Snyk "Warn" for the repo, though the skill itself contains no executable code.
- **Not a Google-supported product.** No SLA or official support.

## USAGE_PATTERNS
- **Stitch design audit:** Document the visual language of one or more Stitch screens.
- **Design-system source of truth:** Create a portable, human-readable spec to keep future prompts consistent.
- **Agent onboarding:** Give other agents (or humans) a shared vocabulary for a Stitch project.
- **Pre-build documentation:** Before generating more screens, capture the existing design language.
- **When to avoid:** Non-Stitch projects, offline environments, codebases without rendered Stitch screens, or any project requiring deterministic token extraction.

## RECOMMENDATIONS_FOR_TELEGRAMHELPER
- **Do not install or use `design-md` as-is.** TelegramHelper is a backend Telegram bot with no web UI or Stitch screens.
- **If a web UI (e.g., Telegram Mini App) is added later**, evaluate the source-code-based `extract-design-md` skill first; only use `design-md` if the UI is rendered as a Stitch project.
- **Borrow the semantic documentation pattern.** The structure (atmosphere, palette, typography, components, layout) can be adapted into a `STYLE.md` or `VOICE.md` for bot messages, commands, and replies.
- **Consider a custom skill.** If the project needs design-system documentation, a small custom `STYLE.md` generator reading bot copy/formatting conventions would be cheaper and more relevant than a Stitch-specific skill.
- **No code changes required.** No adaptation is recommended at this time.

## CONFIDENCE
**Medium.** The skill itself is fully documented and the example is concrete, but the Stitch MCP server and public API schemas were not directly exercised (only documented). The mapping to TelegramHelper is clear from the local workspace analysis.

## GAPS
- Did not call the live Stitch MCP server (no credentials in this environment).
- Did not retrieve the full Stitch Effective Prompting Guide (https://stitch.withgoogle.com/docs/learn/prompting/ returned a transport error).
- Could not inspect the exact `designTheme` JSON schema returned by `get_project`/`get_screen`.
- Could not verify the Snyk warning details or the exact security issue.
- Did not test the `npx skills add` install flow.
- The skill's own Git commit history is sparse; only one visible commit for the skill path was retrieved.

## OUTPUT CONTRACT
- **SUMMARY:** Provided above.
- **CHANGES:** No code changes recommended for TelegramHelper.
- **EVIDENCE:** Raw perspective files in `raw/`, downloaded skill files in `%LOCALAPPDATA%\Temp\opencode\stitch-design-md-research`, and webfetched sources cited per section.
- **RISKS:** Listed above.
- **BLOCKERS:** None for the research itself; adoption in TelegramHelper is blocked by the absence of a Stitch project and web UI.
