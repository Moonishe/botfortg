# DESIGN.md Deep Research Synthesis

Research date: 2026-06-22
Source repository: `https://github.com/google-labs-code/design.md`

## SUMMARY

DESIGN.md is a Google Labs open-source format that couples a YAML front matter of design tokens with a markdown body of design rationale, so AI agents and humans share a single source of truth for a visual identity. The `@google/design.md` CLI provides `lint`, `diff`, `export`, and `spec` commands; exports target Tailwind v3 JSON, Tailwind v4 CSS `@theme`, and W3C DTCG tokens.json. The project is currently at version `alpha` (latest release `0.3.0` on 2026-06-15) with 16k stars and 1.5k forks, but it is still a small, fast-moving experiment with documented Windows CLI friction and extensibility/export gaps. The format is most valuable for teams that generate UI with agents or maintain a living design system document; for a pure backend Telegram bot like TelegramHelper, it only becomes relevant if the project adds a custom web UI surface.

## KEY_FINDINGS

1. **Format is two-layer**: YAML front matter tokens + markdown body rationale. Tokens are normative; prose carries intent and negative constraints.
2. **Token groups**: `colors`, `typography`, `rounded`, `spacing`, `components` are first-class. Unknown top-level keys and custom markdown sections are accepted, but export emitters silently ignore them unless they are in the known schema.
3. **Sections are ordered**: Overview/Brand & Style, Colors, Typography, Layout/Layout & Spacing, Elevation & Depth/Elevation, Shapes, Components, Do's and Don'ts.
4. **CLI is Bun-based**: `citty` commands, `unified`/`remark` parsing, `zod` validation, `yaml` parsing. It also runs in `npx` / `npm install` environments.
5. **10 lint rules** (README still says 9): broken-ref (error), missing-primary, contrast-ratio, orphaned-tokens, missing-typography, section-order, unknown-key, token-like-ignored (warnings), and token-summary, missing-sections (info).
6. **Export targets**: Tailwind v3 `theme.extend` JSON, Tailwind v4 `@theme` CSS with CSS-variable namespaces, and W3C DTCG tokens.json.
7. **Component tokens are limited**: 8 recognized sub-tokens (`backgroundColor`, `textColor`, `typography`, `rounded`, `padding`, `size`, `height`, `width`). Variants are separate entries.
8. **Windows CLI gotcha**: the `design.md` bin collides with the `.md` file association. Use the `designmd` alias or `npx -p @google/design.md designmd ...`.
9. **Extensibility/export gap**: custom token groups like `motion`, `shadow`, or `icon` are allowed but dropped by `export`, caught only by the new `token-like-ignored` warning.
10. **DTCG and Tailwind v4 fidelity issues**: lineHeight is emitted unitless; v4 export rejects token names not matching `/^[a-zA-Z0-9][a-zA-Z0-9-]*$/`.

## SPECIFICATION

### Document structure
```markdown
---
version: alpha                 # optional
name: <string>
description: <string>          # optional
# tokens...
---

## Overview
## Colors
## Typography
## Layout
## Elevation & Depth
## Shapes
## Components
## Do's and Don'ts
```

### Token schema
```yaml
version: alpha
name: <string>
description: <string>
colors:
  <token-name>: <Color>
typography:
  <token-name>:
    fontFamily: <string>
    fontSize: <Dimension>
    fontWeight: <number>
    lineHeight: <Dimension|number>
    letterSpacing: <Dimension>
    fontFeature: <string>
    fontVariation: <string>
rounded:
  <scale>: <Dimension>
spacing:
  <scale>: <Dimension|number>
components:
  <component-name>:
    backgroundColor: <Color|TokenRef>
    textColor: <Color|TokenRef>
    typography: <Typography|TokenRef>
    rounded: <Dimension|TokenRef>
    padding: <Dimension|TokenRef>
    size: <Dimension|TokenRef>
    height: <Dimension|TokenRef>
    width: <Dimension|TokenRef>
```

### Component token model
- Valid sub-tokens: `backgroundColor`, `textColor`, `typography`, `rounded`, `padding`, `size`, `height`, `width`.
- Variants are expressed as separate component entries: `button-primary`, `button-primary-hover`, `button-primary-active`.
- References use `{path.to.token}` syntax.

### Section order
1. Overview (Brand & Style)
2. Colors
3. Typography
4. Layout (Layout & Spacing)
5. Elevation & Depth (Elevation)
6. Shapes
7. Components
8. Do's and Don'ts

## CLI_CAPABILITIES

### `lint`
- Validates a DESIGN.md file.
- Accepts file path or `-` for stdin.
- Default output JSON; `--format text` supported.
- Exit code 1 on errors.
- Output: `{ findings: [...], summary: { errors, warnings, info } }`.

### `diff`
- Compares two DESIGN.md files.
- Reports added/removed/modified tokens per group (colors, typography, rounded, spacing, components).
- Reports error/warning delta.
- Exit code 1 if the after file has more errors or warnings.

### `export`
- Formats: `json-tailwind` (alias `tailwind`), `css-tailwind`, `dtcg`.
- `json-tailwind`: Tailwind v3 `theme.extend` JSON.
- `css-tailwind`: Tailwind v4 CSS `@theme { ... }` block.
- `dtcg`: W3C Design Tokens Format Module JSON.
- Exit code 1 on errors or invalid format.

### `spec`
- Prints the DESIGN.md specification.
- `--rules` appends the active linting rules table.
- `--rules-only` outputs only the rules.
- `--format json` or `markdown` (default).

## RISKS

- **Alpha format**: the spec and schema may change. Any adoption requires version pinning and a migration plan.
- **Windows CLI friction**: the `design.md` bin name is problematic on Windows; use `designmd`.
- **Corporate npm registry**: published via `wombat-dressing-room.appspot.com`; misconfigured mirrors can cause `ENOVERSIONS`.
- **Export silently drops custom tokens**: extensibility is a feature, but custom groups like `motion`, `shadow`, `icon` are ignored by `export` unless you transform them yourself.
- **Tailwind v4 naming strictness**: token names with underscores may pass lint but fail v4 export.
- **DTCG lineHeight fidelity**: dimension lineHeight is converted to a raw number, not a multiplier.
- **Limited contrast coverage**: only checks `backgroundColor`/`textColor` inside `components`; prose color usage and non-component tokens are not checked.
- **Governance**: Google Labs experiment, small commit history (40 commits), requires CLA, not eligible for VRP.

## USAGE_PATTERNS

1. **Agent-driven UI generation**: place a `DESIGN.md` in the repo root; agents read it before generating HTML/CSS/Tailwind components.
2. **Design-system handoff**: designers write prose + tokens; developers run `export` to get Tailwind/DTCG artifacts.
3. **CI regression gate**: run `design.md diff` in PRs to detect token drift and lint to block broken references.
4. **Prompt context injection**: use `design.md spec` to feed the format specification into agent prompts.
5. **Multi-platform token pipeline**: use the DTCG export to feed Figma, Style Dictionary, or other token-management tools.

## EXAMPLES

### Minimal Heritage example
```markdown
---
name: Heritage
colors:
  primary: "#1A1C1E"
  secondary: "#6C7278"
  tertiary: "#B8422E"
  neutral: "#F7F5F2"
typography:
  h1:
    fontFamily: Public Sans
    fontSize: 3rem
  body-md:
    fontFamily: Public Sans
    fontSize: 1rem
  label-caps:
    fontFamily: Space Grotesk
    fontSize: 0.75rem
rounded:
  sm: 4px
  md: 8px
spacing:
  sm: 8px
  md: 16px
components:
  button-primary:
    backgroundColor: "{colors.tertiary}"
    textColor: "{colors.neutral}"
    rounded: "{rounded.sm}"
    padding: 12px
---

## Overview
Architectural Minimalism meets Journalistic Gravitas.

## Colors
...
```

### Real example: Totality Festival
- 40+ Material-like color tokens.
- 6 typography tokens.
- 6 rounded tokens, 5 spacing tokens.
- 10 component tokens including hover states.
- Accompanied by `tailwind.config.js` (primitives only) and `design_tokens.json` (DTCG full export).

## RECOMMENDATIONS_FOR_TELEGRAMHELPER

TelegramHelper is a Python aiogram/Telethon bot. Its user interface is largely determined by the Telegram client itself (messages, inline keyboards, reply markup). Therefore, DESIGN.md is **not a must-have today**. It becomes relevant only when the project adds:

- A **Telegram Web App / Mini App** (custom HTML/CSS/JS frontend).
- A **public landing page or documentation site** that needs a consistent visual identity.
- **Branded rendered messages** (HTML-to-image, reports, or media cards).

**Recommendation**: do not add a DESIGN.md file now. Instead, add a short note or task to the project backlog: "If we build a Telegram Web App or branded docs, evaluate `google-labs-code/design.md` as a lightweight design-system format." If a UI surface is added, start with one of the examples (e.g., `atmospheric-glass` or `heritage`) and a pinned `@google/design.md` dev dependency.

## CONFIDENCE

**High** for the format, CLI commands, and export capabilities — we have read the source code, examples, spec, and README. **Medium** for exact metrics and historical commit details — we relied on the GitHub repo page and a shallow clone, not full API access. **Medium** for the future trajectory — the project is an alpha Google Labs experiment.

## GAPS

- Full git history beyond the most recent commit was not fetched (shallow clone).
- GitHub API was blocked (403), so we could not query issues, PRs, releases, or contributors programmatically.
- The npm package page was inaccessible (403), so we could not confirm exact download counts or latest version metadata.
- We did not run the CLI against the examples locally (Bun was not executed in the environment), so runtime behavior and exact lint output are inferred from source and README.
- No direct information about roadmap, governance, or how the spec will evolve beyond the `alpha` label.

---

## OUTPUT CONTRACT

- **SUMMARY**: See SUMMARY section above.
- **CHANGES**: No code changes made in the local repo; only research artifacts were written to `.opencode/memory/research/design-md/iteration-01/`.
- **EVIDENCE**: Web-fetched README/spec/PHILOSOPHY/CONTRIBUTING/package.json; cloned repo to `%TEMP%\opencode\design-md-clone`; read CLI source, examples, and rules; used `glob`, `grep`, and `git log` to verify structure and rule set.
- **RISKS**: See RISKS section above.
- **BLOCKERS**: None for completing the research; no external action needed.
