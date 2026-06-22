# Researcher 1 — Bird's Eye: design.md (google-labs-code/design.md)

Sources fetched (raw.githubusercontent.com/main/):
- README.md (full)
- package.json (root monorepo)
- LICENSE (Apache 2.0)

Fetched: 2026-06-22

---

## SUMMARY

google-labs-code/design.md defines **DESIGN.md** — a format specification that gives AI coding agents a persistent, structured understanding of a visual design system. The format is a single file combining **YAML front matter** (machine-readable design tokens) with a **markdown body** (human-readable design rationale). The repo ships a CLI published as `@google/design.md` on npm with four commands (lint, diff, export, spec), nine linting rules, and four export targets (Tailwind v3 JSON, Tailwind v4 CSS, W3C DTCG JSON, plus a tailwind alias). The format status is **alpha** and under active development. The project is an Apache-2.0 Google Labs experiment, explicitly not eligible for the Google OSS VRP. The root repo is a bun+turbo monorepo with workspaces under `packages/*`; the CLI lives in `packages/cli`. Note: the specific version "0.3.0" and the "16k stars" figure could not be verified from the fetched files (root package.json is a private monorepo shell without a version field; stars are GitHub metadata not present in repo files).

---

## KEY_FINDINGS

### 1. DESIGN.md Format — Two-Layer Structure
- **Layer 1 — YAML front matter**: machine-readable design tokens, delimited by `---` fences at top of file. These are the normative values.
- **Layer 2 — Markdown body**: human-readable design rationale organized into `##` sections. Tells agents *why* values exist and how to apply them.
- Design intent: an agent reading the file produces a UI matching the tokens (e.g. deep ink headlines, warm limestone background, accent CTAs).

### 2. Token Schema (YAML)
Top-level keys:
- `version` (optional, current value: `"alpha"`)
- `name` (required)
- `description` (optional)
- `colors`: map of `<token-name>: <Color>`
- `typography`: map of `<token-name>: <Typography>`
- `rounded`: map of `<scale-level>: <Dimension>`
- `spacing`: map of `<scale-level>: <Dimension | number>`
- `components`: map of `<component-name>` → sub-token properties

### 3. Token Types
| Type | Format | Example |
|------|--------|---------|
| Color | Any CSS color (hex, rgb(), oklch(), named) | `"#1A1C1E"`, `"oklch(62% 0.18 250)"` |
| Dimension | number + unit (px, em, rem) | `48px`, `-0.02em` |
| Token Reference | `{path.to.token}` | `{colors.primary}` |
| Typography | object: fontFamily, fontSize, fontWeight, lineHeight, letterSpacing, fontFeature, fontVariation | — |

### 4. Component Tokens
- Components map a name to a group of sub-token properties.
- Valid component properties: `backgroundColor`, `textColor`, `typography`, `rounded`, `padding`, `size`, `height`, `width`.
- Variants (hover, active, pressed) are expressed as **separate component entries** with related key names (e.g. `button-primary-hover`).
- Token references supported inside component props: `backgroundColor: "{colors.tertiary}"`.

### 5. Canonical Section Order (8 sections, can be omitted but order enforced)
1. Overview (alias: Brand & Style)
2. Colors
3. Typography
4. Layout (alias: Layout & Spacing)
5. Elevation & Depth (alias: Elevation)
6. Shapes
7. Components
8. Do's and Don'ts

### 6. Consumer Behavior for Unknown Content
- Unknown section heading → preserve, no error.
- Unknown color token name → accept if value valid.
- Unknown typography token name → accept as valid typography.
- Unknown component property → accept with warning.
- Duplicate section heading → error, reject file.

### 7. CLI — Package & Installation
- npm package: `@google/design.md` (scoped under `@google`).
- Install: `npm install @google/design.md` (quote on Windows PowerShell: `"@google/design.md"`).
- Run directly: `npx @google/design.md <command>`.
- **Windows quirk**: the `design.md` bin name collides with Windows Markdown file association (`.md` suffix). Use the dot-free `designmd` alias instead: `npx -p @google/design.md designmd lint DESIGN.md`. Same entrypoint, cross-platform.
- All commands accept a file path or `-` for stdin. Output defaults to JSON.
- `ENOVERSIONS` error → npm not querying public registry (corporate mirror / `.npmrc` misconfig). Fix: `npm config get registry` should be `https://registry.npmjs.org/`.

### 8. CLI Commands
| Command | Purpose | Exit code |
|---------|---------|-----------|
| `lint` | Validate structural correctness; structured JSON findings | 1 if errors, 0 otherwise |
| `diff` | Compare two DESIGN.md files; token-level + prose regressions | 1 if regressions (more errors/warnings in "after") |
| `export` | Convert tokens to other formats (4 formats) | — |
| `spec` | Output the DESIGN.md format spec (for injecting into agent prompts) | — |

- `lint` options: `file` (positional, required), `--format json` (default).
- `diff` options: `before`, `after` (positional, required), `--format json`.
- `export` options: `file` (positional, required), `--format` (required: `json-tailwind` | `css-tailwind` | `tailwind` | `dtcg`).
- `spec` options: `--rules` (append linting rules table), `--rules-only` (only rules table), `--format` (`markdown` default | `json`).

### 9. Export Targets (4 formats)
| Format flag | Output | Target |
|-------------|--------|--------|
| `json-tailwind` | JSON | Tailwind v3 `theme.extend` config object for `tailwind.config.js` |
| `css-tailwind` | CSS | Tailwind v4 `@theme { ... }` block with CSS custom properties (`--color-*`, `--font-*`, `--text-*`, `--leading-*`, `--tracking-*`, `--font-weight-*`, `--radius-*`, `--spacing-*`) |
| `tailwind` | JSON | Alias for `json-tailwind` (backwards-compatible) |
| `dtcg` | JSON | W3C Design Tokens Format Module (tokens.json) |

- DESIGN.md tokens are **inspired by** the W3C Design Token Format (designtokens.org). The `export` command bridges to that and to Tailwind.

### 10. Linting Rules (9 total)
| Rule | Severity | Checks |
|------|----------|--------|
| `broken-ref` | error | Token references that don't resolve to any defined token |
| `missing-primary` | warning | Colors defined but no `primary` color (agents auto-generate) |
| `contrast-ratio` | warning | Component bg/text pairs below WCAG AA (4.5:1) |
| `orphaned-tokens` | warning | Color tokens never referenced by any component |
| `token-summary` | info | Summary of token counts per section |
| `missing-sections` | info | Optional sections (spacing, rounded) absent |
| `missing-typography` | warning | Colors defined but no typography tokens (agents use default fonts) |
| `section-order` | warning | Sections out of canonical order |
| `unknown-key` | warning | Top-level YAML key looks like a typo of a known schema key (e.g. `colours` → `colors`); custom extension keys stay silent |

### 11. Programmatic API
- Linter available as a library:
  ```typescript
  import { lint } from '@google/design.md/linter';
  const report = lint(markdownString);
  // report.findings: Finding[]
  // report.summary: { errors, warnings, info }
  // report.designSystem: Parsed DesignSystemState
  ```

### 12. Repo Structure & Tooling
- Root `package.json`: name `design-monorepo`, private, workspaces `packages/*`.
- Package manager: **bun@1.3.9** (pinned).
- Build/test/lint orchestration: **turbo** (`turbo build`, `turbo test`, `turbo lint`).
- CLI entrypoint: `packages/cli/src/index.ts` (run via `bun run packages/cli/src/index.ts`).
- Dev deps: `@types/node ^25.6.0`, `bun-types ^1.3.12`, `turbo latest`, `typescript latest`.
- Full spec lives at `docs/spec.md`.

### 13. License & Status
- **License**: Apache License 2.0.
- **Status**: format version `alpha`; spec, token schema, and CLI under active development; expect breaking changes.
- **Disclaimer**: not eligible for Google Open Source Software Vulnerability Rewards Program (signals this is a Google Labs experiment, not a hardened Google product).

### 14. Gaps / Unverified Claims
- **"16k stars"**: NOT verifiable from fetched files (GitHub star count is UI metadata, absent from README/package.json/LICENSE). Flag for a researcher with GitHub API access.
- **"version 0.3.0"**: NOT found in root package.json (which is a versionless private monorepo shell). The CLI version would be in `packages/cli/package.json`, which was NOT fetched. README states format version is `"alpha"`. Flag for follow-up fetch of `packages/cli/package.json`.
- **Google Labs experiment**: confirmed by org name `google-labs-code` and the VRP disclaimer; no other "Google Labs" branding in README.

---

## CONFIDENCE

**High** for: format structure (YAML front matter + markdown body), token schema/types, component model, section order, CLI commands/options, export formats/targets, linting rules, programmatic API, license (Apache 2.0), monorepo tooling (bun+turbo), alpha status.

**Medium** for: the claim that this is specifically a "Google Labs experiment" — strongly implied by the `google-labs-code` GitHub org and the VRP exclusion disclaimer, but README does not literally say "Google Labs".

**Low / Unverified** for: the "16k stars" figure (GitHub metadata, not in repo files) and the "0.3.0" version (root package.json is versionless; CLI package.json not fetched). Both require additional sources (GitHub API or `packages/cli/package.json`).
