# Synthesis: effective-html skill repository

## SUMMARY
`plannotator/effective-html` is a set of three agent skills (`html`, `html-diagram`, `html-plan`) that instruct models to generate self-contained, visually polished HTML files instead of markdown walls. The repository is a prompt-and-reference package, not a runtime library: it bundles three `SKILL.md` files and a 20-file reference corpus (`html-effectiveness`) so the model can imitate a warm, editorial, low-contrast design system. The repo is published to skills.sh, Claude Code, Codex, and a generic agents marketplace, and it is tightly linked to the Plannotator annotation surface and `tot` sharing tool.

## KEY_FINDINGS
1. The skill is purely prompt-driven: `SKILL.md` uses YAML front-matter with `disable-model-invocation: true` and a short markdown directive telling the model to study the reference corpus.
2. There are three narrowly scoped skills: generic HTML artifacts, full-screen SVG architecture diagrams, and pragmatic HTML plan pages.
3. Every skill mandates the same dark-mode pattern: hand-rolled CSS variables on `:root` / `html.dark`, a theme toggle, `localStorage` persistence, and an apply-before-paint script that defaults to `prefers-color-scheme`.
4. The reference corpus is a verbatim copy of Thariq Shihipar's `html-effectiveness` examples (20 standalone HTML files), licensed Apache-2.0 and explicitly marked as unmaintained sample code.
5. The visual style is warm, low-contrast, editorial: ivory background (`#FAF9F5`), slate ink (`#141413`), clay accent (`#D97757`), olive secondary (`#788C5D`), and a serif/sans/mono font stack.
6. Diagrams are inline SVG with CSS-styled nodes, edges, and zones; the `html-diagram` skill forbids hard-coded hex values inside SVG.
7. The repo has no tests, no validation, no CI, no releases, and no changelog; it depends entirely on the model's ability to imitate the examples.
8. Distribution is fragmented across four plugin manifests: skills.sh, Claude Code, Codex, and `.agents/plugins`; each manifest has a slightly different schema.
9. The ecosystem is anchored by `backnotprop/plannotator` (review surface, 6.4k stars) and `plannotator/tot` (git-backed HTML sharing, 11 stars).
10. The skill is best for one-off communication artifacts, not for production UI components or multi-modal deliverables.

## ARCHITECTURE
```
effective-html/
├── README.md
├── LICENSE (MIT)
├── skills.sh.json                 # skills.sh registry grouping
├── .claude-plugin/
│   ├── plugin.json                # Claude plugin skill paths
│   └── marketplace.json           # marketplace listing
├── .codex-plugin/
│   └── plugin.json                # Codex plugin metadata
├── .agents/plugins/
│   └── marketplace.json           # generic agents marketplace
└── skills/
    ├── html/
    │   ├── SKILL.md               # prompt + front-matter
    │   ├── agents/openai.yaml     # OpenAI-agent interface
    │   └── references/html-effectiveness/  # 20 example HTML files
    ├── html-diagram/
    │   ├── SKILL.md
    │   ├── agents/openai.yaml
    │   ├── references/html-effectiveness/
    │   └── references/architecture-example.html  # finished diagram
    └── html-plan/
        ├── SKILL.md
        ├── agents/openai.yaml
        └── references/html-effectiveness/
```

Each skill is a self-contained package: prompt + reference corpus + agent metadata. The references are duplicated under each skill, which keeps examples local to the prompt but increases package size.

## SKILL_FORMAT
`SKILL.md` template:
```yaml
---
name: html-diagram
description: Create a self-contained HTML file for visualizing architecture and understanding the stack with a high-quality SVG diagram. Use when the user wants a full-screen diagram, wants the output to be light on prose, or wants an HTML artifact that is mostly there to make the architecture click fast.
disable-model-invocation: true
---

# HTML Diagram

Review the SVG diagrams used throughout `references/html-effectiveness/`.
...
Always include dark mode: hand-rolled CSS variables on `:root` / `html.dark`, a small theme toggle button, `localStorage` persistence, and an apply-before-paint script in `<head>` (default to `prefers-color-scheme`).
```

`agents/openai.yaml` template:
```yaml
interface:
  display_name: "HTML"
  short_description: "Create effective standalone HTML artifacts"
  default_prompt: "Use $html to create a polished standalone HTML artifact."

policy:
  allow_implicit_invocation: false
```

Reference examples include:
- `16-implementation-plan.html` — milestones, data-flow SVG, mockups, code blocks, risk table.
- `11-status-report.html` — summary band, shipped table, velocity bar chart, carryover cards.
- `architecture-example.html` — full-screen interactive SVG with zones, nodes, edges, flow chips, and detail cards.

## RISKS
- The reference corpus is explicitly unmaintained, so the style may drift over time.
- No automated validation: generated HTML may be invalid, inaccessible, or miss required dark-mode/theme behavior.
- Generated HTML contains inline JS/CSS; if user input is rendered, XSS is possible unless the model sanitizes.
- Accessibility (ARIA landmarks, contrast, keyboard navigation) is not enforced by the prompts.
- Four plugin manifests with no shared schema tests can drift and break installation.
- No versioning or changelog makes upgrades opaque.
- Heavy model-dependence: smaller or weaker models may miss the visual conventions or generate verbose prose.
- Duplicated corpus across skills increases token usage and package size.
- The skill is not designed for multi-modal output (image, PDF, email) or production UI components.

## USAGE_PATTERNS
1. Generate a report, plan, or explainer in Telegram and send it as an HTML file or a link via `tot`.
2. Create an interactive architecture diagram for a userbot / LLM-router architecture review.
3. Turn rough notes into a clean plan page before handing it to a developer.
4. Use the HTML artifact as a review surface in Plannotator (`/plannotator-annotate report.html --render-html`).
5. Share the HTML via `tot page.html` to get an instant `tot.page/<id>` link.

## CONFIG_EXAMPLES
### skills.sh
```bash
npx skills add plannotator/effective-html
npx skills add plannotator/effective-html --list
npx skills add plannotator/effective-html --skill html-diagram
```

### Claude Code
```
/plugin marketplace add plannotator/effective-html
/plugin install plannotator-effective-html@effective-html
```

### Codex
```bash
codex plugin marketplace add plannotator/effective-html
codex plugin add plannotator-effective-html@effective-html
```

### OpenCode (manual/local)
Copy the relevant skill folder into `.opencode/skills/` and reference it in `opencode.json`. The key files are `SKILL.md`, `references/html-effectiveness/`, and optionally `agents/openai.yaml`.

### Sharing
```bash
npm i -g @plannotator/tot
tot page.html
# returns https://tot.page/<id>
```

## RECOMMENDATIONS_FOR_TELEGRAMHELPER
Based on the local structure (`.opencode/skills/` already exists with `deepresearch`, `ponytail`, etc.), the project already supports skills. The following HTML/visual skills would be the smallest useful additions:

1. **`html-report`** (or `html`) — generate self-contained status reports for research jobs, security audits, or memory summaries. Fits the existing research pipeline (`data/research/<job_id>/SUMMARY.md` could be upgraded to HTML). Use the warm palette and dark-mode script from `effective-html`.
2. **`html-diagram`** — generate architecture diagrams for the TelegramHelper stack (aiogram + Telethon + SQLAlchemy + Qdrant) as full-screen SVG files. Useful for onboarding and architecture reviews.
3. **`html-plan`** — generate plan pages for new features or refactors, keeping the user's rough notes intact but cleaning them into a visual timeline with risks.
4. **`html-telegram-card`** — a Telegram-specific variant: single-file HTML that renders a Telegram message card, poll, or media gallery. Could be used for previewing bot responses before sending.
5. **`html-dashboard`** — small self-contained dashboard for bot metrics (memory stats, tool usage, provider fallback history) using the same bar chart / summary card conventions.

Implementation path: mirror the `effective-html` structure (`SKILL.md` + `references/` + `agents/openai.yaml`) under `.opencode/skills/`, adapt the prompt to mention TelegramHelper constraints (async/await, SQLite, no raw SQL, no hardcoded secrets), and seed the reference corpus with 3-5 example HTML files generated for the project's own domains.

## CONFIDENCE
**Medium-high.** The repository structure, skill format, and style patterns are well documented and directly observable from the fetched files. Confidence is lower for the exact inner semantics of the skills.sh / Claude / Codex installation flows because those are host-specific and cannot be exercised without installing the plugins. The reference corpus content is fully fetched and inspected, but the exact rendering of all 20 examples was not tested in a browser.

## GAPS
- Could not fetch the GitHub API tree due to 403, so the directory structure was reconstructed from the GitHub web UI and individual raw URLs.
- Could not verify the `.claude-plugin` and `.codex-plugin` installation in a live IDE.
- The Twitter/X demo link was unreachable.
- Did not render all 20 reference HTML files in a browser; only inspected source.
- Did not measure token usage or compare output quality across different models.
- Did not find a CI/CD pipeline, tests, or contribution guidelines in the repo.

## OUTPUT CONTRACT
- **SUMMARY:** Self-contained HTML skill package with three prompts and a warm, low-contrast reference corpus.
- **CHANGES:** No code changes made to TelegramHelper; only research files saved.
- **EVIDENCE:** `README.md`, `LICENSE`, `skills.sh.json`, three `SKILL.md` files, three `agents/openai.yaml`, plugin manifests, and multiple reference HTML files fetched and read.
- **RISKS:** Unmaintained corpus, no validation, accessibility/security not enforced, fragmented manifests, heavy model dependence.
- **BLOCKERS:** None for research; the only blocker for adoption is a user decision on which HTML skills to add to `.opencode/skills/`.
