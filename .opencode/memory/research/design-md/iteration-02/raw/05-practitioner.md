# Researcher 5 — Practitioner
# Source: https://github.com/google-labs-code/design.md (main branch, v0.3.0, 16.1k stars, alpha spec)
# Fetched: README.md, PHILOSOPHY.md, docs/spec.md, 3 examples (atmospheric-glass, paws-and-paths, totality-festival),
#          .github/workflows/test.yml, packages/cli/package.json, .agents/skills/ink/SKILL.md, skills-lock.json

---

## SUMMARY

DESIGN.md is a plain-text, markdown-based format for handing a visual design system to coding agents. A single file lives in the repo root, combines YAML-front-matter design tokens (colors, typography, rounded, spacing, components) with human-readable prose rationale (Overview, Colors, Typography, Layout, Elevation, Shapes, Components, Do's and Don'ts). The format is explicitly built for agent consumption: "A format specification for describing a visual identity to coding agents. DESIGN.md gives agents a persistent, structured understanding of a design system."

The practitioner surface is a published npm CLI — `@google/design.md` v0.3.0 — with four commands: `lint` (structural + WCAG contrast validation → JSON findings), `diff` (before/after token-level comparison with regression flag + exit-code-1 gating), `export` (DESIGN.md → Tailwind v3 JSON / Tailwind v4 CSS / W3C DTCG tokens.json), and `spec` (emits the format spec text, documented as "useful for injecting spec context into agent prompts"). A programmatic `@google/design.md/linter` export lets teams embed linting into build pipelines and custom tooling. Three full examples (weather glassmorphism app, pet-walking service, eclipse festival) demonstrate complete, production-grade DESIGN.md files with ~50+ color tokens, 6-8 typography levels, component definitions with `{token.ref}` references, and rich prose.

The design philosophy (PHILOSOPHY.md) is the load-bearing insight for practitioners: prose matters more than tokens. A specific reference ("1970s graduate lecture handout") carries more usable signal than adjectives ("modern, clean, premium"). Negative constraints (Do's and Don'ts) arrive "for free" when the reference is specific enough. The format deliberately stays extensible — any custom key/section (motion, iconography) is accepted by the linter and read by agents without a spec change. Status is `alpha`; the repo ships its own CI, bun-based monorepo (turbo), and Windows-safe `designmd` bin alias.

The repository uses `.agents/skills/` with a `skills-lock.json` pinning skills from GitHub sources (jpoehnelt/skills, vercel-labs/json-render, msmps/opentui-skill) — indicating the project dogfoods agent-skill packaging. Google's Stitch tool (stitch.withgoogle.com) is the reference consumer and hosts the canonical spec page.

---

## USAGE_PATTERNS

### 1. Agent-driven UI generation (place DESIGN.md in repo root)

**Pattern:** Commit a `DESIGN.md` to the repository root. Any coding agent (Copilot, Claude, Cursor, Stitch) that reads repo context picks it up as persistent design ground truth. The file is the single source a code-generating agent needs to produce on-brand UI: exact token values (machine-readable YAML) + prose intent (why those values exist, how to apply them, what to avoid).

**Evidence:**
- README opening line: "A format specification for describing a visual identity to coding agents."
- README example conclusion: "An agent that reads this file will produce a UI with deep ink headlines in Public Sans, a warm limestone background, and Boston Clay call-to-action buttons."
- All 3 examples are complete, self-contained files demonstrating the full token+prose pattern (atmospheric-glass: 50+ surface color tokens + glassmorphism prose; paws-and-paths: Material-style semantic color roles + component refs; totality-festival: dark cosmic theme + dual-font strategy).
- PHILOSOPHY.md: "The quality of a generated design is determined less by the precision of its values than by how clearly the intent is described." → the prose is what makes agent output non-generic.
- Spec: "ensuring that these stylistic choices can be followed across design sessions and between different AI agents and tools" + "a living source of truth that both humans and AI can understand and refine."

**Practitioner steps:**
1. Write `DESIGN.md` in repo root (YAML frontmatter tokens + markdown prose sections).
2. Run `npx @google/design.md lint DESIGN.md` to catch broken refs, WCAG failures, missing primary color, orphaned tokens.
3. Point the coding agent at the repo; the agent reads DESIGN.md alongside code and generates conforming UI.
4. Re-lint after agent-generated code to verify token usage stays consistent.

**Confidence: HIGH** — the entire format is purpose-built for this; README + spec + examples all directly evidence it.

---

### 2. Design-system handoff (designers write, developers export)

**Pattern:** A designer authors the DESIGN.md (the prose + token decisions). A developer runs `export` to generate platform-specific artifacts — no manual translation of design values into code. The handoff artifact is the DESIGN.md file itself, not a Figma screenshot or a PDF spec sheet.

**Evidence:**
- `export` command with three targets:
  - `--format json-tailwind` → Tailwind v3 `theme.extend` JSON object for `tailwind.config.js`
  - `--format css-tailwind` → Tailwind v4 `@theme { ... }` CSS block with `--color-*`, `--font-*`, `--text-*`, `--leading-*`, `--tracking-*`, `--font-weight-*`, `--radius-*`, `--spacing-*` custom properties
  - `--format dtcg` → W3C Design Tokens Format Module `tokens.json`
- CLI package.json `bin`: both `design.md` and `designmd` (Windows-safe alias) → `./dist/index.js`
- Programmatic API: `import { lint } from '@google/design.md/linter'` → `report.findings`, `report.summary`, `report.designSystem` (parsed state) → enables custom export pipelines or IDE plugins.
- Component tokens use `{colors.tertiary}` reference syntax → export resolves references to concrete values automatically.
- Linting rules that support handoff quality: `broken-ref` (error), `contrast-ratio` (WCAG AA warning), `orphaned-tokens` (warning), `missing-primary` (warning — "agents will auto-generate one"), `missing-typography` (warning — "agents will use default fonts"), `unknown-key` (typo detection: `colours:` → `colors:`).

**Practitioner steps:**
1. Designer writes DESIGN.md (prose-first per PHILOSOPHY; tokens as context, not rendering instructions).
2. `npx @google/design.md lint DESIGN.md` → fix errors/warnings before handoff.
3. Developer: `npx @google/design.md export --format css-tailwind DESIGN.md > theme.css` → drop into the project.
4. (Optional) Developer embeds `lint` in a pre-commit hook or build step to catch drift.

**Confidence: HIGH** — export command + three formats + programmatic API are all directly documented and the CLI is published to npm.

---

### 3. CI regression gate (design.md diff in PRs)

**Pattern:** Run `design.md diff` in CI on PRs that modify DESIGN.md. The command compares before/after versions, reports token-level add/remove/modify per group, sets a `regression: true/false` flag, and exits with code 1 if the "after" file has more errors/warnings than the "before" — blocking merge on design-system regressions. Pair with `lint` for absolute validation.

**Evidence:**
- `diff` command output (README):
  ```json
  {
    "tokens": { "colors": { "added": ["accent"], "removed": [], "modified": ["tertiary"] }, ... },
    "regression": false
  }
  ```
- "Exit code `1` if regressions are detected (more errors or warnings in the 'after' file)."
- `lint` exit code: "Exit code `1` if errors are found, `0` otherwise." → combinable for CI gating.
- The repo's own `.github/workflows/test.yml` demonstrates the CI pattern (though for the CLI itself, not a consuming project): runs `bun run lint`, `bun run test`, `bun run build`, then a Node smoke test (`node packages/cli/dist/index.js lint examples/atmospheric-glass/DESIGN.md`), a tarball smoke test, and a Windows npm-registry smoke test. This is the template a consumer would adapt: install the CLI, run `lint`/`diff` on their own DESIGN.md.
- stdin support (`cat DESIGN.md | npx @google/design.md lint -`) enables piping in CI without temp files.

**Practitioner steps (consumer CI):**
1. Add a GitHub Actions step on PRs touching `DESIGN.md`:
   ```yaml
   - run: npx -p @google/design.md designmd diff ${{ github.event.pull_request.base.sha }}-DESIGN.md DESIGN.md
   ```
   (or fetch base-version DESIGN.md via `git show base:DESIGN.md`).
2. Add `designmd lint DESIGN.md` as a separate always-on gate.
3. Both exit 1 → PR check fails → blocks merge.
4. JSON output → parse for a PR comment bot showing token diff summary.

**Confidence: HIGH** — `diff` command with regression flag + exit code 1 is explicitly documented as the regression-detection mechanism. The repo's own CI shows the lint-in-CI pattern. (Note: no ready-made GitHub Action wrapper exists yet — teams compose it from the CLI; the Windows `designmd` alias is required for Windows runners.)

---

### 4. Prompt context injection (design.md spec)

**Pattern:** Use the `spec` command to emit the DESIGN.md format specification as markdown or JSON, then inject that text into an agent's system prompt so the agent knows the schema, section order, token types, and linting rules before generating or editing a DESIGN.md. Separately, inject the actual DESIGN.md file content into the agent's working context so it generates conforming code.

**Evidence:**
- `spec` command (README): "Output the DESIGN.md format specification (useful for injecting spec context into agent prompts)."
- Options: `--rules` (append linting rules table), `--rules-only` (rules only), `--format markdown|json`.
- This is an explicit, documented prompt-engineering primitive — the maintainers anticipate agents needing the spec as context.
- The `ink` agent skill (`.agents/skills/ink/SKILL.md`) demonstrates the broader pattern: a server-side `catalog.prompt({ system: ... })` API generates AI system prompts from a component catalog — the same architecture (spec-as-prompt-context) design.md's `spec` command enables.
- Spec.md itself is generated from `spec.mdx + spec-config.ts` via `bun run spec:gen` → the `spec` CLI command reads this generated content, ensuring the injected spec always matches the linter's active rules.

**Practitioner steps:**
1. `npx @google/design.md spec --rules --format markdown > design-md-spec.md`
2. Include `design-md-spec.md` in the agent's system prompt or context window (e.g., AGENTS.md, .cursorrules, or a system message).
3. Include the project's `DESIGN.md` in the agent's context (repo-root placement handles this automatically for repo-aware agents).
4. Agent now generates DESIGN.md edits that pass `lint` on first try, and generates code that conforms to the design system.

**Confidence: HIGH** — the `spec` command's purpose is explicitly documented as prompt injection; the skill architecture confirms the pattern.

---

### 5. Multi-platform token pipeline (DTCG → Figma)

**Pattern:** DESIGN.md is the authoring format; `export --format dtcg` emits W3C Design Tokens Format Module JSON (`tokens.json`), which is the interoperable exchange format that Figma plugins (and other design tools) consume. The pipeline: DESIGN.md (author) → `export dtcg` → `tokens.json` → Figma plugin (e.g., Tokens Studio, Figma Tokens) → Figma variables. Reverse direction is possible: Figma → DTCG → (manual or scripted) DESIGN.md YAML frontmatter.

**Evidence:**
- `export --format dtcg` → "W3C Design Tokens Format Module" (tr.designtokens.org/format/).
- README: "DESIGN.md tokens are inspired by the W3C Design Token Format." + "These tokens are easily converted from or to `tokens.json`, Figma variables, and Tailwind theme configs." (spec.md)
- Token reference syntax `{colors.primary}` mirrors DTCG's `{path.to.token}` reference syntax → near-lossless round-trip.
- Color values accept `oklch()`, `lab()`, `color-mix()` (wide-gamut) → compatible with modern Figma color spaces; internally converted to sRGB for WCAG checks, original format preserved for export.
- Typography tokens carry `fontFamily`, `fontSize`, `fontWeight`, `lineHeight`, `letterSpacing`, `fontFeature`, `fontVariation` → maps to Figma text styles.
- `rounded` + `spacing` as `Dimension` (px/em/rem) → maps to Figma corner-radius and spacing variables.
- Component tokens (`button-primary`, `card-profile`) with `backgroundColor`/`textColor`/`rounded`/`padding`/`height`/`width`/`typography` properties → map to Figma component properties/variants.

**Gap (not in this repo):** there is no built-in Figma plugin or Figma REST API integration in the design.md repo. The DTCG export is the handoff boundary; a separate Figma plugin (Tokens Studio for Figma, or a custom plugin reading `tokens.json`) completes the Figma side. The spec says tokens are "easily converted from or to" Figma variables, implying the DTCG intermediary, but no code in this repo does the Figma import.

**Practitioner steps:**
1. Author/edit DESIGN.md → `npx @google/design.md lint DESIGN.md`.
2. `npx @google/design.md export --format dtcg DESIGN.md > tokens.json`.
3. Import `tokens.json` into Figma via a DTCG-compatible plugin (Tokens Studio, W3C-tokens plugin).
4. (Web side) `export --format css-tailwind DESIGN.md > theme.css` for the same source of truth in code.
5. Single DESIGN.md → Figma variables + Tailwind theme + (json-tailwind for v3) all from one source.

**Confidence: MEDIUM-HIGH** — the DTCG export is directly documented and W3C-standard; the `{token}` reference syntax aligns with DTCG. The Figma import step is architecturally implied ("easily converted … to Figma variables") but no Figma-side code ships in this repo — a practitioner must supply the Figma plugin. Confidence is high that the DTCG boundary is correct; medium that the full Figma round-trip is frictionless without additional tooling.

---

## CONFIDENCE

| Focus area | Confidence | Basis |
|---|---|---|
| Agent-driven UI generation (DESIGN.md in repo root) | **HIGH** | Format purpose-built for agents; README + spec + 3 full examples directly evidence it. |
| Design-system handoff (designers write, developers export) | **HIGH** | `export` command with 3 formats + programmatic linter API; CLI published to npm v0.3.0. |
| CI regression gate (diff in PRs) | **HIGH** | `diff` command with `regression` flag + exit-code-1 is explicitly documented; repo's own test.yml shows the lint-in-CI template. No ready-made GH Action wrapper — composed from CLI. |
| Prompt context injection (spec command) | **HIGH** | `spec` command documented verbatim as "useful for injecting spec context into agent prompts"; skill architecture confirms the pattern. |
| Multi-platform token pipeline (DTCG → Figma) | **MEDIUM-HIGH** | DTCG export is W3C-standard and directly documented; `{token}` syntax aligns with DTCG. Figma import requires a separate DTCG-compatible plugin — no Figma-side code in this repo. The DTCG boundary is solid; the Figma leg is implied but not shipped. |

**Overall:** Format and CLI are real, published, and versioned (v0.3.0, alpha spec). The practitioner surface (lint/diff/export/spec + library API) is complete for patterns 1-4. Pattern 5 is half-shipped (export side yes, Figma import side no). The repo is 40 commits, 16.1k stars, actively maintained (latest release Jun 15 2026), and tied to Google's Stitch tool as a reference consumer.
