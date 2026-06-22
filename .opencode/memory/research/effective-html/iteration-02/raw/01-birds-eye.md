# Researcher 1: Bird's Eye View -- Overview Perspective

**Repository:** https://github.com/plannotator/effective-html
**Branch:** main
**Research Date:** 2026-06-22
**Researcher:** Researcher 1 (Bird's Eye -- Overview)
**Iteration:** 02

---

## 1. Repository Identity

| Field | Value |
|-------|-------|
| Name | plannotator/effective-html |
| Description | Agent skill for elegant and simple html plans, architecture diagrams, or whatever else you can think of. |
| Stars | ~1,100 (1.1k) |
| Forks | 79 |
| Commits | 26 |
| License | MIT (Copyright 2026 plannotator) |
| Language | HTML 100% |
| Topics | skills, agent-skills |
| Website | plannotator.ai |
| Open Issues | 0 |
| Pull Requests | 2 |
| Releases | 0 published |

The repository is a curated collection of **agent skills** -- not application code. It contains no build system, no package.json, no Python files, no CI configuration. The entire content is markdown skill definitions, HTML reference examples, and JSON/YAML plugin manifests. This is a **pure prompt-engineering artifact** distributed across multiple agent ecosystems.

---

## 2. Complete File Tree

```
plannotator/effective-html/
|
|-- .agents/plugins/
|   `-- marketplace.json                 # Generic agents marketplace manifest
|
|-- .claude-plugin/
|   |-- marketplace.json                 # Claude Code plugin marketplace entry
|   `-- plugin.json                      # Claude Code plugin definition (3 skills)
|
|-- .codex-plugin/
|   `-- plugin.json                      # Codex plugin definition (v0.1.0)
|
|-- skills/
|   |-- html/
|   |   |-- agents/
|   |   |   `-- openai.yaml              # OpenAI agent interface definition
|   |   |-- references/
|   |   |   `-- html-effectiveness/      # 20 HTML reference files + index + meta
|   |   `-- SKILL.md                     # 13 lines, 783 bytes
|   |
|   |-- html-diagram/
|   |   |-- agents/
|   |   |   `-- openai.yaml              # OpenAI agent interface definition
|   |   |-- references/
|   |   |   |-- html-effectiveness/      # Same 20-file corpus (bundled copy)
|   |   |   `-- architecture-example.html # Extra: finished diagram exemplar
|   |   `-- SKILL.md                     # 23 lines, 1.49 KB (most detailed)
|   |
|   `-- html-plan/
|       |-- agents/
|       |   `-- openai.yaml              # OpenAI agent interface (presumed)
|       |-- references/
|       |   `-- html-effectiveness/      # Same 20-file corpus (bundled copy)
|       `-- SKILL.md                     # 15 lines, 749 bytes
|
|-- LICENSE                              # MIT License
|-- README.md                            # 2,751 bytes
|-- skills.sh.json                       # skills.sh distribution config
|-- star-plannotator.svg                 # Badge: "like this? star Plannotator"
`-- use-tot.svg                          # Badge: "share your HTML? use tot"
```

### Key Structural Observations

- **No code at all.** The repo is 100% HTML (reference examples) plus config/manifest files. GitHub reports "HTML 100.0%" as the language.
- **Triple-bundled reference corpus.** The `html-effectiveness/` directory (20 HTML files + index + metadata) is copied verbatim into each skill's `references/` subdirectory. This is intentional: each skill is self-contained when installed independently via `npx skills add --skill <name>`.
- **`html-diagram` has an extra reference.** Beyond the shared corpus, it includes `architecture-example.html` -- a finished exemplar of a full-screen SVG diagram with clickable nodes and animated flow chips.
- **Each skill has an `agents/` directory** containing `openai.yaml` -- a minimal OpenAI agent interface definition with display name, short description, default prompt, and invocation policy.

---

## 3. The Three Skills

### 3.1 `html` -- General HTML Artifacts

**SKILL.md:** 13 lines, 783 bytes

**Frontmatter:**
```yaml
name: html
description: Create a self-contained HTML file for whatever the user is describing,
  in the effective HTML style. Use when the user wants an HTML artifact that isn't
  specifically a diagram or a plan -- a report, explainer, comparison, deck, prototype,
  or anything else best delivered as one HTML file.
disable-model-invocation: true
```

**Instructions (body):**
- Review files in `references/html-effectiveness/`
- Create an HTML file matching the references' alignment -- style, density, and tone
- Always include dark mode: hand-rolled CSS variables on `:root` / `html.dark`, theme toggle button, `localStorage` persistence, apply-before-paint script in `<head>` (default to `prefers-color-scheme`)

**OpenAI agent (openai.yaml):**
```yaml
interface:
  display_name: "HTML"
  short_description: "Create effective standalone HTML artifacts"
  default_prompt: "Use $html to create a polished standalone HTML artifact."
policy:
  allow_implicit_invocation: false
```

**Role:** The catch-all skill. Any HTML deliverable that isn't a diagram or a plan falls here. Reports, explainers, comparisons, slide decks, prototypes.

---

### 3.2 `html-diagram` -- Architecture Diagrams

**SKILL.md:** 23 lines, 1.49 KB (largest, most prescriptive)

**Frontmatter:**
```yaml
name: html-diagram
description: Create a self-contained HTML file for visualizing architecture and
  understanding the stack with a high-quality SVG diagram. Use when the user wants
  a full-screen diagram, wants the output to be light on prose, or wants an HTML
  artifact that is mostly there to make the architecture click fast.
disable-model-invocation: true
```

**Instructions (body):**
- Review SVG diagrams throughout `references/html-effectiveness/` (notes there are "a bunch" focused on architecture)
- Create an HTML file strictly for visualizing architecture and understanding the stack
- Should NOT be prose-heavy; simplify into a full-screen diagram
- Build a high-quality diagram in SVG; iterate on the diagram more than anything
- If it makes sense, make the diagram interactive -- visualize and animate different sequences of system behavior
- Also review `references/architecture-example.html` -- a finished example (full-screen SVG stage, clickable nodes, flow chips that light up and animate request paths)
- Always include dark mode (same pattern as `html`)
- **Critical SVG rule:** Style the SVG through CSS classes using the CSS variables -- never hard-coded hex inside the SVG -- so the diagram follows the theme

**OpenAI agent (openai.yaml):**
```yaml
interface:
  display_name: "HTML Diagram"
  short_description: "Create effective standalone HTML diagrams"
  default_prompt: "Use $html-diagram to create a polished architecture diagram."
policy:
  allow_implicit_invocation: false
```

**Role:** The most opinionated skill. Demands SVG-first, minimal prose, interactive/animated diagrams. The only skill with an extra reference file (`architecture-example.html`). The only skill that gives a specific technical constraint about SVG styling (CSS classes + variables, no hard-coded hex).

---

### 3.3 `html-plan` -- Plan Pages

**SKILL.md:** 15 lines, 749 bytes

**Frontmatter:**
```yaml
name: html-plan
description: Create a self-contained HTML plan that is pragmatic, simple, and visually
  organized. Use when the user wants a plan page in the effective HTML style, wants
  the writing kept close to what they gave you, or wants the grammar cleaned up
  without turning it into a whole bigger thing.
disable-model-invocation: true
```

**Instructions (body):**
- Review files in `references/html-effectiveness/`
- Create an HTML file for the plan in a similar style
- Keep it pragmatic and simple
- Always include dark mode (same pattern as `html`)

**Role:** The most minimal skill. No SVG, no interactivity requirements, no extra reference. Just: review the corpus, match the style, keep it simple. The description emphasizes fidelity to the user's input -- "wants the writing kept close to what they gave you" and "without turning it into a whole bigger thing." This is a light-touch formatting skill, not a generative one.

---

### 3.4 Cross-Skill Patterns

| Aspect | html | html-diagram | html-plan |
|--------|------|-------------|-----------|
| SKILL.md size | 13 lines / 783 B | 23 lines / 1.49 KB | 15 lines / 749 B |
| disable-model-invocation | true | true | true |
| Dark mode required | Yes | Yes | Yes |
| SVG-specific constraints | No | Yes (CSS classes, no hex) | No |
| Extra references | No | Yes (architecture-example.html) | No |
| Interactivity guidance | No | Yes (animate sequences) | No |
| Corpus reference | html-effectiveness/ | html-effectiveness/ + arch example | html-effectiveness/ |
| Tone | "match alignment" | "not prose-heavy", "iterate on diagram" | "pragmatic and simple" |

**All three skills share:**
- `disable-model-invocation: true` -- the model cannot auto-invoke; the user must explicitly request the skill
- The same dark mode implementation pattern (CSS variables, localStorage, apply-before-paint, prefers-color-scheme)
- The same reference corpus (html-effectiveness)
- The same self-contained HTML output philosophy (no build step, no dependencies)
- `allow_implicit_invocation: false` in OpenAI agent configs

---

## 4. Reference Corpus: html-effectiveness

### 4.1 Origin

The corpus is by **Thariq Shihipar**, originally published at https://thariqs.github.io/html-effectiveness. The README in the corpus directory states:

> "A gallery of standalone HTML examples that accompany the blog post on using HTML as a flexible output format. Each file is a self-contained .html page (no build step, no dependencies) demonstrating a different use case."

The original corpus is licensed under **Apache License 2.0** (not MIT like the wrapper repo). The README also notes: "Sample code. Not maintained and not accepting contributions."

### 4.2 The 20 HTML Files

| # | File | Category |
|---|------|----------|
| 01 | exploration-code-approaches.html | Exploration |
| 02 | exploration-visual-designs.html | Exploration |
| 03 | code-review-pr.html | Code |
| 04 | code-understanding.html | Code |
| 05 | design-system.html | Code |
| 06 | component-variants.html | Code |
| 07 | prototype-animation.html | Prototyping |
| 08 | prototype-interaction.html | Prototyping |
| 09 | slide-deck.html | Communication |
| 10 | svg-illustrations.html | Diagrams & research |
| 11 | status-report.html | Communication |
| 12 | incident-report.html | Communication |
| 13 | flowchart-diagram.html | Diagrams & research |
| 14 | research-feature-explainer.html | Diagrams & research |
| 15 | research-concept-explainer.html | Diagrams & research |
| 16 | implementation-plan.html | Communication |
| 17 | pr-writeup.html | Communication |
| 18 | editor-triage-board.html | Custom editing UIs |
| 19 | editor-feature-flags.html | Custom editing UIs |
| 20 | editor-prompt-tuner.html | Custom editing UIs |

### 4.3 Corpus Metadata Files

Beyond the 20 HTML examples, the corpus directory includes:
- `index.html` -- categorized index/gallery page
- `README.md` -- description and category table
- `LICENSE` -- Apache License 2.0
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`

### 4.4 Categories (from corpus README)

| Category | Examples |
|----------|----------|
| Exploration | code approaches, visual designs |
| Code | review, understanding, design systems, component variants |
| Prototyping | animation, interaction |
| Communication | slide deck, status report, incident report, PR write-up |
| Diagrams & research | flowchart, feature/concept explainers |
| Custom editing UIs | triage board, feature flags, prompt tuner |

### 4.5 Design System Inference

The corpus demonstrates what the README calls "the effective HTML style." Based on the SKILL.md instructions and the corpus description, the design system has these characteristics:
- **Warm, low-contrast, editorial** -- the visual style is understated, not flashy
- **Self-contained** -- each file is a complete HTML page, no build step, no dependencies
- **Dark mode native** -- CSS variables on `:root` / `html.dark`, theme toggle, localStorage persistence
- **Apply-before-paint** -- inline script in `<head>` to prevent flash of wrong theme
- **SVG for diagrams** -- styled through CSS classes using variables, not hard-coded colors
- **Fictional sample data** -- all product names, data, and scenarios are fictional (placeholder brand "Acme")

---

## 5. Distribution Channels

The repository is distributed through **four distinct channels**, each with its own manifest format:

### 5.1 skills.sh (Primary)

**Config file:** `skills.sh.json` (root)
**Install command:** `npx skills add plannotator/effective-html`

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

**Options:**
- `npx skills add plannotator/effective-html --list` -- list available skills
- `npx skills add plannotator/effective-html --skill html-diagram` -- install specific skill
- `npx skills add plannotator/effective-html --skill html-plan` -- install specific skill

All three skills are grouped under a single "Effective HTML" grouping. The `notGrouped: "bottom"` setting controls display ordering for ungrouped skills (none exist here).

### 5.2 Claude Code Plugin

**Config files:** `.claude-plugin/marketplace.json` + `.claude-plugin/plugin.json`
**Install commands:**
```
/plugin marketplace add plannotator/effective-html
/plugin install plannotator-effective-html@effective-html
```

**marketplace.json:**
```json
{
  "name": "effective-html",
  "owner": { "name": "plannotator" },
  "description": "Agent skills for elegant, self-contained HTML plans, diagrams, and artifacts.",
  "plugins": [{
    "name": "plannotator-effective-html",
    "source": { "source": "github", "repo": "plannotator/effective-html" },
    "description": "HTML skills for pragmatic visual artifacts -- html, html-diagram, and html-plan.",
    "homepage": "https://github.com/plannotator/effective-html",
    "license": "MIT",
    "keywords": ["html", "diagram", "plan", "svg", "artifacts", "skills"],
    "category": "productivity"
  }]
}
```

**plugin.json:**
```json
{
  "name": "plannotator-effective-html",
  "skills": ["./skills/html", "./skills/html-diagram", "./skills/html-plan"]
}
```

The Claude Code plugin points directly at the `skills/` subdirectories. Each skill directory's `SKILL.md` is the entry point.

### 5.3 Codex Plugin

**Config file:** `.codex-plugin/plugin.json`
**Install commands:**
```bash
codex plugin marketplace add plannotator/effective-html
codex plugin add plannotator-effective-html@effective-html
```

**plugin.json:**
```json
{
  "name": "plannotator-effective-html",
  "version": "0.1.0",
  "description": "HTML skills for pragmatic visual artifacts -- html, html-diagram, and html-plan.",
  "author": { "name": "plannotator", "url": "https://github.com/plannotator" },
  "homepage": "https://github.com/plannotator/effective-html",
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

The Codex plugin is the richest manifest: version 0.1.0, full interface metadata, capabilities limited to "Write" (read-only access not needed -- these skills produce HTML files), and three default prompts. It points to `./skills/` as a whole directory (vs. Claude's explicit per-skill paths).

### 5.4 .agents/plugins (Generic Agents Marketplace)

**Config file:** `.agents/plugins/marketplace.json`

```json
{
  "name": "effective-html",
  "interface": { "displayName": "Effective HTML" },
  "plugins": [{
    "name": "plannotator-effective-html",
    "source": {
      "source": "url",
      "url": "https://github.com/plannotator/effective-html.git"
    },
    "policy": {
      "installation": "AVAILABLE",
      "authentication": "ON_INSTALL"
    },
    "category": "Productivity"
  }]
}
```

This is a more generic format -- uses URL source (git clone) rather than GitHub-specific source. Policy declares the plugin as "AVAILABLE" (not auto-installed) with authentication "ON_INSTALL".

### 5.5 OpenAI Agent Definitions

Each skill contains `agents/openai.yaml` -- a minimal OpenAI-compatible agent interface:

**html/agents/openai.yaml:**
```yaml
interface:
  display_name: "HTML"
  short_description: "Create effective standalone HTML artifacts"
  default_prompt: "Use $html to create a polished standalone HTML artifact."
policy:
  allow_implicit_invocation: false
```

**html-diagram/agents/openai.yaml:**
```yaml
interface:
  display_name: "HTML Diagram"
  short_description: "Create effective standalone HTML diagrams"
  default_prompt: "Use $html-diagram to create a polished architecture diagram."
policy:
  allow_implicit_invocation: false
```

All skills use `allow_implicit_invocation: false` -- consistent with `disable-model-invocation: true` in the SKILL.md frontmatter. The agent must be explicitly invoked by name.

---

## 6. Ecosystem

The README promotes two companion projects, each linked via SVG badges in the repo root:

### 6.1 Plannotator (Review Surface)

- **Repo:** https://github.com/backnotprop/plannotator
- **Badge:** `star-plannotator.svg` ("like this? star Plannotator")
- **Description:** "Render and annotate your HTML with Plannotator (optional)"
- **Website:** plannotator.ai
- **Role:** A review/annotation surface for HTML files produced by the skills. The user generates HTML with a skill, then uses Plannotator to render, review, and annotate it.

### 6.2 tot (Git-Backed HTML Sharing)

- **Repo:** https://github.com/plannotator/tot
- **Badge:** `use-tot.svg` ("share your HTML? use tot")
- **Description:** "Create instant share links for your HTML files (optional)"
- **Website:** tot.page
- **Example:** https://tot.page/0CW7xV96XMxnalrzwRl4eQ (HTML from the demo video)
- **Role:** A sharing service for HTML files. The user generates HTML with a skill, then uses tot to create an instant shareable link. The "git-backed" nature suggests tot stores HTML in git repositories and serves them via short URLs.

### 6.3 Ecosystem Flow

```
User request
    |
    v
[effective-html skill] -- generates --> self-contained HTML file
    |
    |-- (optional) --> [Plannotator] -- render + annotate + review
    |
    `-- (optional) --> [tot] -- instant share link (git-backed)
```

The skills are the production tool; Plannotator is the review surface; tot is the distribution channel. All three are independent but complementary.

---

## 7. The Fable 5 Note

The README includes a notable annotation:

> "The diagram was made by `Fable 5`, I will create more fable 5 artifacts and add them to the skill folder for smaller models to distill."

This suggests:
- The demo video diagram was generated by **Fable 5** (an AI model, likely a front-end generation model)
- The maintainer plans to add more Fable 5-generated artifacts to the skill reference folders
- The purpose is **distillation** -- smaller models can learn from high-quality Fable 5 outputs as reference examples
- This implies the reference corpus is not static; it may grow with AI-generated exemplars

---

## 8. Design Philosophy Summary

The repository embodies a specific philosophy about AI-generated HTML:

1. **HTML as the universal artifact format** -- not code, not markdown, not JSON. Self-contained HTML files that open in any browser.
2. **Reference-driven generation** -- skills don't contain templates or code generators. They contain *examples* (the 20-file corpus) and brief instructions to "review" them and "match" their style.
3. **Pure prompt-driven** -- SKILL.md files are 13-23 lines. No functions, no APIs, no tools. The skill IS the prompt.
4. **Self-contained output** -- no build step, no dependencies, no external resources. Every generated HTML file must work standalone.
5. **Dark mode as a first-class requirement** -- not optional, not "if you have time." Every skill mandates the same dark mode pattern.
6. **SVG for diagrams, CSS variables for theming** -- the html-diagram skill explicitly forbids hard-coded hex in SVG, requiring CSS-class-based styling that follows the theme.
7. **Explicit invocation only** -- `disable-model-invocation: true` and `allow_implicit_invocation: false` across all skills. The user must ask for the skill by name.
8. **Pragmatic over pretty** -- the html-plan skill emphasizes "pragmatic and simple" and "without turning it into a whole bigger thing." The html-diagram skill says "not prose-heavy." The philosophy values density and clarity over elaboration.

---

## 9. Technical Details

### 9.1 SKILL.md Format

Each SKILL.md uses YAML frontmatter with three fields:
- `name` -- the skill identifier (matches the directory name)
- `description` -- a detailed trigger description explaining when to use the skill
- `disable-model-invocation` -- boolean, always `true`

The body is brief markdown prose. No structured sections, no templates, no code blocks. Just: "review the references, create the HTML, include dark mode."

### 9.2 Corpus Licensing Complexity

- **Wrapper repo (effective-html):** MIT License (Copyright 2026 plannotator)
- **Bundled corpus (html-effectiveness):** Apache License 2.0 (by Thariq Shihipar)
- The corpus is bundled (copied) not linked. Each skill gets its own copy. This means consumers of the skill get both the MIT-licensed skill definitions and the Apache-2.0-licensed reference examples.

### 9.3 Plugin Capability Model

The Codex plugin declares `capabilities: ["Write"]` -- these skills only need write access (to produce HTML files). No read, no execute, no network. This is a minimal capability surface.

---

## 10. Open Questions for Other Researchers

1. **What do the actual HTML reference files look like?** This researcher examined the file listing and metadata but did not read the content of the 20 HTML files. The design system details (color palette, typography, layout patterns) require reading the files.
2. **What is the `architecture-example.html` content?** This is the only skill-specific extra reference. Its structure (full-screen SVG stage, clickable nodes, flow chips) is described in SKILL.md but the actual implementation was not examined.
3. **What does the `html-plan/agents/openai.yaml` contain?** Two of three openai.yaml files were fetched; the third (html-plan) was inferred to follow the same pattern but not directly verified.
4. **How does Plannotator integrate?** The README says "optional" but doesn't describe any integration mechanism (API, webhook, CLI). Is it purely manual (user opens HTML in Plannotator) or is there an automated pipeline?
5. **How does tot work technically?** The "git-backed" sharing model is mentioned but the mechanism (git push to a remote? GitHub Pages? custom server?) is not documented in this repo.
6. **What is the skills.sh ecosystem?** The `npx skills` CLI and skills.sh schema are referenced but not explained. How does skill installation work? Where do skills get installed locally?
7. **What are the 2 open pull requests?** They were not examined. They may contain new skills, reference additions, or plugin config changes.

---

## 11. Confidence Assessment

| Area | Confidence | Notes |
|------|-----------|-------|
| Repository structure | High | Complete file tree verified via GitHub web interface |
| SKILL.md content (all 3) | High | Full content extracted from GitHub blob pages |
| Plugin configs (all 4) | High | Full JSON content extracted from GitHub blob pages |
| OpenAI agent configs (2 of 3) | High | 2 verified, 1 inferred from pattern |
| Reference corpus file list | High | All 20 files + 5 metadata files enumerated |
| Design system description | Medium | Inferred from SKILL.md instructions + corpus README, not from reading HTML file contents |
| Ecosystem relationships | Medium | Described in README but integration mechanisms not documented |
| Fable 5 plans | Low | Single README note, no further details |
| Corpus licensing | High | Both licenses directly verified |

**Overall confidence: HIGH** for structural and content findings. **MEDIUM** for design system and ecosystem integration details (require deeper content analysis by other researchers).
