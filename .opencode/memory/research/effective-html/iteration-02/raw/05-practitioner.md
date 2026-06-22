# Researcher 5 — Practitioner (Applied Perspective)
# Repository: plannotator/effective-html
# Iteration: 02
# Date: 2026-06-22

> Perspective: practical usage — how to install, invoke, share, and adapt these
> skills for real projects. Sources fetched live from GitHub (API + jsdelivr CDN
> + github.com tree views). See "Sources" at the bottom.

---

## 1. What this is, in one paragraph

`plannotator/effective-html` is a MIT-licensed (1.1k stars, 79 forks, 26 commits)
collection of three **agent skills** that teach a coding agent to produce
**self-contained `.html` files** instead of walls of markdown. The three skills
are `html`, `html-diagram`, and `html-plan`. Each skill is a tiny `SKILL.md`
front-matter prompt (~750 bytes) that points the agent at a bundled **local
reference corpus** (`references/html-effectiveness/`) — 20 example HTML files by
Thariq Shihipar — and tells it to match that style. The repo ships install
manifests for **four** distribution channels: skills.sh (`npx skills add`),
Claude Code plugin marketplace, Codex plugin marketplace, and a generic
`.agents/plugins/marketplace.json`. An optional companion tool, `tot.page`,
publishes any generated HTML to an instant git-backed share link.

---

## 2. Repository shape (verified by tree fetch)

```
plannotator/effective-html/
  README.md
  LICENSE                          (MIT)
  skills.sh.json                   # skills.sh grouping manifest
  star-plannotator.svg
  use-tot.svg
  .claude-plugin/
    marketplace.json               # Claude Code plugin marketplace
    plugin.json                    # Claude Code plugin definition
  .codex-plugin/
    plugin.json                    # Codex plugin definition (no marketplace.json)
  .agents/plugins/
    marketplace.json               # generic agents marketplace
  skills/
    html/
      SKILL.md
      agents/openai.yaml           # OpenAI agent interface spec
      references/html-effectiveness/   # bundled example corpus
    html-diagram/
      SKILL.md
      agents/                      # (openai.yaml, same pattern)
      references/
        html-effectiveness/        # bundled example corpus
        architecture-example.html  # finished diagram example (full-screen SVG)
    html-plan/
      SKILL.md
      references/html-effectiveness/   # bundled example corpus
```

Key structural facts:
- **Skills live at `skills/<name>/SKILL.md`.** This is the canonical skills.sh
  convention. The front-matter has `name`, `description`, and
  `disable-model-invocation: true` (all three skills set this — meaning they
  must be explicitly invoked by name, not auto-triggered by the model).
- **Each skill bundles its own copy** of the `html-effectiveness` example corpus
  under `references/` "so the examples stay local to the skill." This means the
  reference HTML travels with the skill regardless of install channel — no
  network fetch at generation time.
- `html-diagram` additionally ships `architecture-example.html` — a finished,
  full-screen SVG example with clickable nodes and animated request paths.
- **Language stats: HTML 100%.** No build step, no JS framework, no bundler.

---

## 3. The three skills — full SKILL.md text (decoded from API)

### 3.1 `html` (skills/html/SKILL.md, 783 bytes)

```yaml
---
name: html
description: Create a self-contained HTML file for whatever the user is
  describing, in the effective HTML style. Use when the user wants an HTML
  artifact that isn't specifically a diagram or a plan — a report, explainer,
  comparison, deck, prototype, or anything else best delivered as one HTML file.
disable-model-invocation: true
---
```
Body: "Review the files throughout `references/html-effectiveness/`. Create an
HTML file for whatever the user is describing. Use the references as best you can
to match alignment — style, density, and tone. **Always include dark mode:**
hand-rolled CSS variables on `:root` / `html.dark`, a small theme toggle button,
`localStorage` persistence, and an apply-before-paint script in `<head>` (default
to `prefers-color-scheme`)."

**Practitioner read:** This is the catch-all / general-purpose skill. Use it for
reports, explainers, comparisons, decks, prototypes — anything that is NOT
specifically a diagram or a plan. It is the broadest of the three.

### 3.2 `html-diagram` (skills/html-diagram/SKILL.md)

```yaml
---
name: html-diagram
description: Create a self-contained HTML file for visualizing architecture and
  understanding the stack with a high-quality SVG diagram. Use when the user
  wants a full-screen diagram, wants the output to be light on prose, or wants
  an HTML artifact that is mostly there to make the architecture click fast.
disable-model-invocation: true
---
```
Body: "Review the SVG diagrams used throughout `references/html-effectiveness/`.
... create an HTML file that is strictly for visualizing the architecture and
understanding the stack. It should not be prose-heavy. It should simplify more
into a full-screen diagram and whatnot. Build a high-quality diagram in SVG.
Take your time iterating on the diagram more than anything. If it makes sense,
make the diagram interactive and able to visualize and animate different
sequences of system behavior. Also review `references/architecture-example.html`
— a finished example of this skill done well (full-screen SVG stage, clickable
nodes, flow chips that light up and animate request paths). **Always include dark
mode** ... Style the SVG through CSS classes using those variables — **never
hard-coded hex inside the SVG** — so the diagram follows the theme."

**Practitioner read:** Architecture / stack / systems diagrams. SVG-first,
minimal prose, full-screen. The only skill that explicitly forbids hard-coded
colors in the SVG (must use CSS variables so the diagram follows the dark/light
theme). Encourages interactivity and animation of system-behavior sequences.
Has a finished gold-standard example bundled.

### 3.3 `html-plan` (skills/html-plan/SKILL.md, 749 bytes)

```yaml
---
name: html-plan
description: Create a self-contained HTML plan that is pragmatic, simple, and
  visually organized. Use when the user wants a plan page in the effective HTML
  style, wants the writing kept close to what they gave you, or wants the
  grammar cleaned up without turning it into a whole bigger thing.
disable-model-invocation: true
---
```
Body: "Review the files throughout `references/html-effectiveness/`. After
reviewing them, create an HTML file for the plan in a similar style. **Keep it
pragmatic and simple.** Always include dark mode: [same dark-mode recipe]."

**Practitioner read:** Plan pages. Deliberately restrained — "keep the writing
close to what they gave you," "clean up the grammar without turning it into a
whole bigger thing." This is the anti-bloat skill: it formats and organizes
existing plan content rather than expanding it. Good for milestone timelines,
roadmaps, sprint plans.

### 3.4 Common contract across all three

Every skill mandates the **same dark-mode recipe**:
1. Hand-rolled CSS variables on `:root` and `html.dark`.
2. A small theme toggle button.
3. `localStorage` persistence of the user's choice.
4. An **apply-before-paint script** in `<head>` that defaults to
   `prefers-color-scheme` (avoids flash of wrong theme).

All three set `disable-model-invocation: true` — they are **opt-in**, invoked
explicitly by the user/agent by name, never auto-triggered.

---

## 4. Install — four distribution channels (all verified)

### 4.1 skills.sh (the canonical / primary channel)

```bash
# Install ALL skills from the repo:
npx skills add plannotator/effective-html

# List available skills before installing:
npx skills add plannotator/effective-html --list

# Install ONE specific skill:
npx skills add plannotator/effective-html --skill html-diagram
npx skills add plannotator/effective-html --skill html-plan
npx skills add plannotator/effective-html --skill html
```

The `skills.sh.json` manifest at repo root defines the grouping:
```json
{
  "$schema": "https://skills.sh/schemas/skills.sh.schema.json",
  "notGrouped": "bottom",
  "groupings": [
    {
      "title": "Effective HTML",
      "description": "Skills for generating pragmatic HTML diagrams and plans.",
      "skills": ["html", "html-diagram", "html-plan"]
    }
  ]
}
```
So `npx skills add` with no `--skill` flag installs the whole "Effective HTML"
group (all three). With `--skill <name>` you get just one.

### 4.2 Claude Code (plugin marketplace)

```
/plugin marketplace add plannotator/effective-html
/plugin install plannotator-effective-html@effective-html
```

Backed by `.claude-plugin/marketplace.json`:
```json
{
  "name": "effective-html",
  "owner": { "name": "plannotator" },
  "description": "Agent skills for elegant, self-contained HTML plans, diagrams, and artifacts.",
  "plugins": [
    {
      "name": "plannotator-effective-html",
      "source": { "source": "github", "repo": "plannotator/effective-html" },
      "description": "HTML skills for pragmatic visual artifacts — html, html-diagram, and html-plan.",
      "homepage": "https://github.com/plannotator/effective-html",
      "license": "MIT",
      "keywords": ["html", "diagram", "plan", "svg", "artifacts", "skills"],
      "category": "productivity"
    }
  ]
}
```
And `.claude-plugin/plugin.json` points the plugin at all three skill folders:
```json
{
  "name": "plannotator-effective-html",
  "skills": ["./skills/html", "./skills/html-diagram", "./skills/html-plan"]
}
```

### 4.3 Codex (plugin marketplace)

```bash
codex plugin marketplace add plannotator/effective-html
codex plugin add plannotator-effective-html@effective-html
```

Backed by `.codex-plugin/plugin.json` (note: Codex has NO `marketplace.json`,
just `plugin.json` with a richer interface block):
```json
{
  "name": "plannotator-effective-html",
  "version": "0.1.0",
  "description": "HTML skills for pragmatic visual artifacts — html, html-diagram, and html-plan.",
  "author": { "name": "plannotator", "url": "https://github.com/plannotator" },
  "license": "MIT",
  "keywords": ["html", "diagram", "plan", "svg", "artifacts", "skills"],
  "skills": "./skills/",
  "interface": {
    "displayName": "Effective HTML",
    "shortDescription": "Elegant, self-contained HTML plans, diagrams, and artifacts",
    "longDescription": "Create polished, self-contained HTML artifacts, full-screen architecture diagrams, and pragmatic plan pages from local reference examples.",
    "developerName": "plannotator",
    "category": "Productivity",
    "capabilities": ["Write"],
    "defaultPrompt": [
      "Create an effective HTML plan.",
      "Create an effective HTML diagram.",
      "Create an effective HTML artifact."
    ]
  }
}
```

### 4.4 Generic agents marketplace (.agents/plugins/)

`.agents/plugins/marketplace.json` — a platform-neutral marketplace manifest:
```json
{
  "name": "effective-html",
  "interface": { "displayName": "Effective HTML" },
  "plugins": [
    {
      "name": "plannotator-effective-html",
      "source": { "source": "url", "url": "https://github.com/plannotator/effective-html.git" },
      "policy": { "installation": "AVAILABLE", "authentication": "ON_INSTALL" },
      "category": "Productivity"
    }
  ]
}
```

### 4.5 OpenCode (manual — no native marketplace command)

OpenCode has no `npx skills add` or plugin-marketplace command. The install is
**manual**: copy the skill folder(s) into `.opencode/skills/`. Concretely:

```powershell
# From the TelegramHelper project root, clone the repo to a temp location,
# then copy only the skill folders you want:
git clone --depth 1 https://github.com/plannotator/effective-html.git "$env:TEMP\opencode\effective-html-src"

# Copy all three skills (each is self-contained: SKILL.md + references/):
Copy-Item -Recurse "$env:TEMP\opencode\effective-html-src\skills\html" `
          ".opencode\skills\html"
Copy-Item -Recurse "$env:TEMP\opencode\effective-html-src\skills\html-diagram" `
          ".opencode\skills\html-diagram"
Copy-Item -Recurse "$env:TEMP\opencode\effective-html-src\skills\html-plan" `
          ".opencode\skills\html-plan"
```

Because each skill folder is fully self-contained (SKILL.md + its own
`references/html-effectiveness/` copy), you can copy just one skill and it works
with zero external dependencies. OpenCode's `skill("html")` / `skill("html-plan")`
/ `skill("html-diagram")` loader will pick them up from `.opencode/skills/`.

**Caveat:** OpenCode skills are typically single `SKILL.md` files, not folders
with bundled references. Verify that OpenCode's skill loader resolves a folder
with a `SKILL.md` inside (it should — it follows the skills.sh folder
convention). If it only loads flat `.md` files, point `skill()` at the
`SKILL.md` path directly and ensure the `references/` relative path resolves.

---

## 5. The reference corpus (html-effectiveness) — what the skills learn from

All three skills point at `references/html-effectiveness/` — a bundled copy of
**Thariq Shihipar's "The unreasonable effectiveness of HTML"** example set
(thariqs.github.io/html-effectiveness). It is **20 self-contained `.html` files**
grouped into 9 categories. This is the entire "training data" for the skills —
the agent reads these examples and matches their style/density/tone.

| Category | Example files | What it replaces |
|---|---|---|
| Exploration & Planning | `01-exploration-code-approaches.html`, `02-exploration-visual-designs.html`, `16-implementation-plan.html` | Side-by-side option comparison, implementation plan handoff |
| Code Review & Understanding | `03-code-review-pr.html`, `17-pr-writeup.html`, `04-code-understanding.html` | Annotated diffs, PR writeups, module maps (boxes & arrows) |
| Design | `05-design-system.html`, `06-component-variants.html` | Living design tokens, component contact sheets |
| Prototyping | `07-prototype-animation.html`, `08-prototype-interaction.html` | Animation sandboxes, clickable flows |
| Illustrations & Diagrams | `10-svg-illustrations.html`, `13-flowchart-diagram.html` | Inline SVG figures, annotated flowcharts |
| Decks | `09-slide-deck.html` | Arrow-key slide deck from one HTML file |
| Research & Learning | `14-research-feature-explainer.html`, `15-research-concept-explainer.html` | Collapsible explainers, tabbed samples, glossaries |
| Reports | `11-status-report.html`, `12-incident-report.html` | Weekly status with charts, incident timelines |
| Custom Editing Interfaces | `18-editor-triage-board.html`, `19-editor-feature-flags.html`, `20-editor-prompt-tuner.html` | Throwaway editors with export-to-markdown buttons |

**Practitioner takeaway:** These 20 files ARE the skill's intelligence. The
SKILL.md is just a routing prompt that says "look at these, match them." This is
a **distillation-by-example** pattern — cheap, portable, and model-agnostic.

---

## 6. The OpenAI agent spec (skills/html/agents/openai.yaml)

Each skill folder contains an `agents/openai.yaml` — a minimal OpenAI-agent
interface definition:
```yaml
interface:
  display_name: "HTML"
  short_description: "Create effective standalone HTML artifacts"
  default_prompt: "Use $html to create a polished standalone HTML artifact."
policy:
  allow_implicit_invocation: false
```
`allow_implicit_invocation: false` mirrors `disable-model-invocation: true` in
the SKILL.md front-matter: the skill is **explicit-invocation only**. The
`$html` token is the invocation trigger in OpenAI's agent framework.

---

## 7. Sharing — tot.page (optional companion)

The repo README and badges point at `plannotator/tot` — a separate npm tool that
publishes any HTML or markdown file to an instant, git-backed share link.

```bash
npm i -g @plannotator/tot

tot page.html
  #> https://tot.page/aB3xK9q
  #> commit  e5f6c1a
  #> frozen  https://tot.page/aB3xK9q/index.md@e5f6c1a
```

How it works (from the tot README, verified):
- `tot notes.md` / `tot page.html` → publishes to a live `tot.page/<id>` link.
- For HTML, it **also uploads local support files** the page directly references
  (images, CSS, JS, video, srcset, posters) — skips external URLs. No bundler.
- `tot update <link>` → pushes new content; **the same URL updates** (living
  link, like a git branch).
- Every publish also creates a **frozen `@hash` URL** (like a commit) that never
  changes — for fixed snapshots.
- `tot list`, `tot remove <link>`, `tot login --key <key>` (optional ownership).
- State in `~/.tot`. Override API origin with `--endpoint <url>`.
- Built on **Cloudflare Artifacts**; every version is a real git commit.
- **Security model: the link IS the key.** Anyone with the link can view, update,
  or delete. No private mode. "Treat them as you would excalidraw."

**Practitioner workflow:** generate HTML with a skill → `tot page.html` → paste
the `tot.page/<id>` link in a chat/PR/issue. Iterate with `tot update`. Pin a
review with the `@hash` frozen link.

---

## 8. Usage patterns — how a practitioner actually drives this

### 8.1 Triggering (by platform)

| Platform | How to invoke |
|---|---|
| skills.sh-compatible agent | User says "create an HTML report/diagram/plan" → agent loads the matched skill via `skill("html")` etc. |
| Claude Code (plugin) | After `/plugin install`, the skills are available; invoke by describing the artifact. |
| Codex (plugin) | After `codex plugin add`, use the `defaultPrompt` phrases: "Create an effective HTML plan/diagram/artifact." |
| OpenAI agents | `$html` / `$html-diagram` / `$html-plan` invocation tokens (per openai.yaml). |
| OpenCode (manual copy) | `skill("html")` / `skill("html-diagram")` / `skill("html-plan")` after copying folders to `.opencode/skills/`. |

Because all three skills set `disable-model-invocation: true`, **they never fire
on their own** — the user or orchestrating agent must explicitly request them.
This prevents accidental HTML generation when the user just wants markdown.

### 8.2 Typical generation flow

1. User describes what they want ("make an architecture diagram of our stack",
   "turn this roadmap into a plan page", "make a weekly status report").
2. Agent loads the matching skill (reads SKILL.md + scans the bundled
   `references/html-effectiveness/` examples).
3. Agent emits **one self-contained `.html` file** — all CSS/JS inline, dark-mode
   recipe included, SVG (for diagrams) styled via CSS variables.
4. Practitioner opens the file in a browser to review.
5. (Optional) `tot page.html` to get a shareable link.
6. (Optional) iterate → `tot update <link>` to push a new version to the same URL.

### 8.3 What each skill is best for (decision guide)

| You want... | Use skill | Why |
|---|---|---|
| Architecture / stack / system diagram | `html-diagram` | SVG-first, full-screen, interactive, animated sequences. Has gold-standard `architecture-example.html`. |
| A plan / roadmap / milestone timeline | `html-plan` | Restrained, "keep writing close to what they gave you," anti-bloat. |
| A report / explainer / comparison / deck / prototype / anything else | `html` | The catch-all. Broadest applicability. |
| A status report with charts | `html` (see example `11-status-report.html`) | Reports fall under the general `html` skill. |
| An annotated PR writeup | `html` (see example `17-pr-writeup.html`) | Code-review artifacts fall under `html`. |
| A flowchart / process diagram | `html-diagram` (see `13-flowchart-diagram.html`) | Diagrams route to `html-diagram`. |

---

## 9. Adaptation for TelegramHelper — concrete plan

The TelegramHelper project (Python 3.13, aiogram 3.16, Telethon, SQLAlchemy,
SQLite + Qdrant) uses OpenCode with `.opencode/skills/`. The Footprint Ladder
(AGENTS.md) says "Add a skill or slash command" is rung 2 — the right level for
this. Here is how to adapt.

### 9.1 Install the three skills into OpenCode

Copy `skills/html`, `skills/html-diagram`, `skills/html-plan` from the repo into
`C:\Users\My\Desktop\asist\TelegramHelper-main\.opencode\skills\`. Each is
self-contained. Rename to project-specific names if desired (see 9.3).

### 9.2 Map to TelegramHelper use cases

| TelegramHelper need | Skill to use | Adaptation |
|---|---|---|
| **Architecture report** of the bot (handlers, services, DB, Qdrant, MCP) | `html` or `html-diagram` | `html-diagram` for the component/stack picture; `html` for a written architecture report. The repo's `architecture-example.html` is a direct template. |
| **Architecture diagram** (aiogram routers → handlers → services → repositories → SQLite/Qdrant) | `html-diagram` | Full-screen SVG, clickable nodes, animated request path (update flow, voice flow, search flow). Style SVG via CSS vars — no hard-coded hex. |
| **Sprint / milestone plan** (Zero-Risk Pipeline phases, D5→R5, memory schedule) | `html-plan` | Keep it pragmatic; feed it the existing plan text from `.opencode/memory/`. |
| **Weekly status report** (what shipped, what slipped, metrics from `.metrics.json`) | `html` | Mirror example `11-status-report.html`; small chart from metrics. |
| **Incident / post-mortem** (if a prod issue occurs) | `html` | Mirror example `12-incident-report.html`; minute-by-minute timeline. |
| **PR writeup** for reviewers | `html` | Mirror example `17-pr-writeup.html`; file-by-file tour with the "why." |
| **Feature explainer** ("how does the voice pipeline work?") | `html` | Mirror example `14-research-feature-explainer.html`; collapsible steps, tabbed snippets. |

### 9.3 Project-specific skill variants (optional, recommended)

Instead of the generic names, create TelegramHelper-tailored skills by copying
and editing the SKILL.md front-matter + body. Suggested names:

- **`.opencode/skills/html-report/`** — derived from `html`. Description tuned:
  "Create a self-contained HTML status/architecture/incident report for the
  TelegramHelper project." Add a project-specific reference (e.g. a saved good
  report) under `references/`.
- **`.opencode/skills/html-diagram/`** — keep as-is (already architecture-focused)
  but optionally add a `references/telegramhelper-archetype.html` showing the
  bot's own stack as the gold-standard example.
- **`.opencode/skills/html-plan/`** — keep as-is, or rename `html-plan-th` and
  point references at a saved plan page from a past sprint.

The adaptation cost is **near zero** — the skills are ~750 bytes of prompt each.
The value is in the bundled 20-file reference corpus, which already covers
reports, diagrams, plans, PR writeups, and status reports.

### 9.4 Integration with existing OpenCode workflow

- The AGENTS.md `skill("impeccable")` pattern is the precedent: a skill loaded on
  trigger. Add `skill("html-report")` / `skill("html-diagram")` /
  `skill("html-plan")` to the trigger table in AGENTS.md §Skill policy.
- Trigger: any user request for "report / diagram / plan / architecture picture
  / status / PR writeup" → load the matching skill.
- Output: the agent writes a `.html` file to `data/reports/` or a temp path.
- Sharing: if `tot` is installed globally, the agent can run `tot page.html` via
  bash to produce a `tot.page/<id>` link and include it in its response.
- **No new dependencies in `requirements.txt`** — these are prompt-only skills.
  `tot` is an optional global npm tool, not a Python dep.

### 9.5 Risks / caveats for adaptation

1. **OpenCode skill-folder resolution:** verify `skill("html")` resolves a folder
   containing `SKILL.md` + `references/`. OpenCode typically loads flat
   `.opencode/skills/<name>.md`. If folder-loading is unsupported, flatten:
   copy `SKILL.md` to `.opencode/skills/html.md` and adjust the `references/`
   path in the body to point at an absolute or known-relative location.
2. **Reference corpus size:** 20 HTML files bundled per skill = ~3x the corpus
   (each skill has its own copy). For disk-conscious setups, symlink or share a
   single `references/html-effectiveness/` and edit the SKILL.md paths.
3. **`disable-model-invocation: true`:** the skills will NOT auto-trigger. The
   orchestrating agent (per rules.md) must explicitly route report/diagram/plan
   requests to them.
4. **tot.page security model:** links are public + mutable by anyone with the
   link. Never `tot publish` HTML containing secrets, tokens, or internal
   architecture with sensitive endpoints. For TelegramHelper, redact before
   sharing.
5. **No tests / no CI in the repo:** the skills are pure prompt artifacts. There
   is nothing to run. Quality is governed by the reference corpus, not by
   automated checks.

---

## 10. Confidence assessment

- **Install commands (all 4 channels):** HIGH — fetched verbatim from README.md
  (decoded from GitHub API base64) and cross-checked against the actual
  `marketplace.json` / `plugin.json` files via jsdelivr CDN.
- **SKILL.md content (all 3):** HIGH — decoded directly from the GitHub Contents
  API base64 payloads.
- **Reference corpus structure:** HIGH — fetched the live
  thariqs.github.io/html-effectiveness index page; 20 files across 9 categories,
  matches the README's description.
- **tot.page behavior:** HIGH — fetched the plannotator/tot README directly.
- **OpenCode adaptation:** MEDIUM — the manual-copy approach is sound, but
  OpenCode's exact skill-folder resolution behavior is inferred from the
  skills.sh convention, not verified against OpenCode source. The flatten
  fallback in 9.5.1 mitigates this.
- **Repo stats (1.1k stars, 79 forks, 26 commits, MIT):** HIGH — from the live
  GitHub repo page.

---

## Sources

- https://github.com/plannotator/effective-html (repo page, live)
- https://api.github.com/repos/plannotator/effective-html/contents/README.md (base64 → decoded)
- https://api.github.com/repos/plannotator/effective-html/contents/skills/html/SKILL.md (base64 → decoded)
- https://api.github.com/repos/plannotator/effective-html/contents/skills/html-plan/SKILL.md (base64 → decoded)
- https://cdn.jsdelivr.net/gh/plannotator/effective-html@main/skills/html-diagram/SKILL.md
- https://cdn.jsdelivr.net/gh/plannotator/effective-html@main/skills.sh.json
- https://cdn.jsdelivr.net/gh/plannotator/effective-html@main/.claude-plugin/marketplace.json
- https://cdn.jsdelivr.net/gh/plannotator/effective-html@main/.claude-plugin/plugin.json
- https://cdn.jsdelivr.net/gh/plannotator/effective-html@main/.codex-plugin/plugin.json
- https://cdn.jsdelivr.net/gh/plannotator/effective-html@main/.agents/plugins/marketplace.json
- https://cdn.jsdelivr.net/gh/plannotator/effective-html@main/skills/html/agents/openai.yaml
- https://github.com/plannotator/effective-html/tree/main/skills (tree view: html, html-diagram, html-plan)
- https://github.com/plannotator/effective-html/tree/main/skills/html (tree: SKILL.md, agents/, references/html-effectiveness/)
- https://github.com/plannotator/effective-html/tree/main/skills/html-diagram (tree: SKILL.md, agents/, references/ [html-effectiveness/ + architecture-example.html])
- https://github.com/plannotator/effective-html/tree/main/.claude-plugin (tree: marketplace.json, plugin.json)
- https://github.com/plannotator/effective-html/tree/main/.codex-plugin (tree: plugin.json only — no marketplace.json)
- https://github.com/plannotator/effective-html/tree/main/.agents/plugins (tree: marketplace.json)
- https://thariqs.github.io/html-effectiveness (live index of the 20-file reference corpus)
- https://github.com/plannotator/tot (tot.page README — sharing tool)

Note: `raw.githubusercontent.com` returned transport errors for all files; the
GitHub REST API (base64) and jsdelivr CDN were used as fallbacks and succeeded.
The GitHub API rate-limited (403) after a few calls; jsdelivr CDN covered the
rest.
