# Deep Dive: design.md — Token Schema, Lint Rules, Export Formats, Component Model

> Researcher 2 (Deep Dive) | Source: https://github.com/google-labs-code/design.md
> Files fetched: README.md, docs/spec.md, packages/cli/package.json, packages/cli/src/index.ts,
> packages/cli/src/linter/index.ts, lint.ts, commands/{lint,diff,export,spec}.ts,
> linter/linter/rules/{index,types,broken-ref,contrast-ratio,orphaned-tokens,section-order,
> unknown-key,token-like-ignored,missing-primary,missing-typography,missing-sections,token-summary}.ts,
> linter/linter/runner.ts, linter/model/{spec,handler}.ts, linter/parser/spec.ts,
> linter/spec-config.{ts,yaml}, linter/tailwind/handler.ts, linter/tailwind/v4/{handler,serialize}.ts,
> linter/dtcg/handler.ts, linter/fixer/handler.ts

---

## 1. YAML Front Matter Token Schema

### 1.1 Top-Level Keys (SCHEMA_KEYS)

Defined in `parser/spec.ts` as a const tuple — the canonical 8 keys:

```typescript
export const SCHEMA_KEYS = [
  'version', 'name', 'description', 'colors',
  'typography', 'rounded', 'spacing', 'components',
] as const;
```

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `version` | optional | string | Current: `"alpha"` |
| `name` | required | string | Design system name |
| `description` | optional | string | Free-text description |
| `colors` | optional | `Map<string, Color>` | Named color tokens |
| `typography` | optional | `Map<string, Typography>` | Named typography levels |
| `rounded` | optional | `Map<string, Dimension>` | Corner-radius scale |
| `spacing` | optional | `Map<string, Dimension \| number>` | Spacing scale (supports unitless numbers) |
| `components` | optional | `Map<string, Map<string, string\|ref>>` | Component definitions |

Any top-level key not in this list is classified as `unknownKeys` and stored in `unknownKeyValues` for lint rules to inspect.

### 1.2 Token Types

**Color** — any valid CSS color string. Internally converted to sRGB for WCAG checking; original format preserved for display/export.
- Hex: `#RGB`, `#RGBA`, `#RRGGBB`, `#RRGGBBAA`
- Named: `red`, `cornflowerblue`, `transparent`
- Functional: `rgb()`, `rgba()`, `hsl()`, `hsla()`, `hwb()`
- Wide-gamut: `oklch()`, `oklab()`, `lch()`, `lab()`
- Mixing: `color-mix(in srgb, ...)`

Resolved to `ResolvedColor`:
```typescript
interface ResolvedColor {
  type: 'color';
  hex: string;
  r: number; g: number; b: number;
  a?: number;        // alpha 0..1
  luminance: number; // WCAG relative luminance
}
```

**Dimension** — number + unit. Standard units (from `spec-config.yaml`): `px`, `em`, `rem`.
```typescript
interface ResolvedDimension {
  type: 'dimension';
  value: number;
  unit: string; // 'px' | 'em' | 'rem' (others flagged by linter)
}
```
Parsing also recognizes a broader set of CSS units (vh, vw, %, dvh, cqw, etc.) for generous acceptance, but only px/em/rem are spec-standard.

**Token Reference** — `{path.to.token}` syntax. Regex: `/^\{[a-zA-Z0-9._-]+\}$/`.
- Must point to a primitive value (not a group), except in `components` where references to composite typography values (`{typography.label-md}`) are permitted.
- Chained resolution supported with cycle detection (visited set) and depth limit.
- Limits from `spec-config.yaml`: `max_reference_depth: 10`, `max_token_nesting_depth: 20`.

**Typography** — object with 7 properties:
```typescript
interface ResolvedTypography {
  type: 'typography';
  fontFamily?: string;
  fontSize?: ResolvedDimension;
  fontWeight?: number;        // numeric (400, 700); YAML bare number or quoted string
  lineHeight?: ResolvedDimension; // also accepts unitless number (multiplier)
  letterSpacing?: ResolvedDimension;
  fontFeature?: string;       // font-feature-settings
  fontVariation?: string;     // font-variation-settings
}
```

### 1.3 DesignSystemState (resolved model)

The linter's internal state after parsing + model resolution:

```typescript
interface DesignSystemState {
  name?: string;
  description?: string;
  colors: Map<string, ResolvedColor>;
  typography: Map<string, ResolvedTypography>;
  rounded: Map<string, ResolvedDimension>;
  spacing: Map<string, ResolvedDimension>;
  components: Map<string, ComponentDef>;
  symbolTable: Map<string, ResolvedValue>;  // flat "colors.primary" → value
  sections?: string[];                        // markdown H2 headings
  unknownKeys?: string[];                     // non-schema top-level YAML keys
  unknownKeyValues?: Record<string, unknown>; // raw values for unknown keys
}

interface ComponentDef {
  properties: Map<string, ResolvedValue>;
  unresolvedRefs: string[];   // references that failed to resolve
}
```

### 1.4 Model Resolution Pipeline (ModelHandler.execute)

3-phase pipeline in `model/handler.ts`:

1. **Phase 1 — Resolve primitive tokens**: iterate colors, typography, rounded, spacing. Parse each leaf value. If it's a token reference, store raw string in symbolTable for later. If it's a valid color/dimension, parse and store. Invalid values produce error findings.

2. **Phase 2 — Resolve chained references**: re-iterate entries that are still raw references. Follow chains via `resolveReference()` with cycle detection (visited Set) and depth limit (MAX_REFERENCE_DEPTH=10). Resolve colors → colors, rounded → rounded, spacing → spacing.

3. **Phase 3 — Build components**: for each component, iterate properties. Non-string scalars (numbers, booleans) stored as-is. Token references resolved via symbolTable (failures tracked in `unresolvedRefs`). Valid colors/dimensions parsed inline. Everything else stored as raw string.

Key safety: `forEachLeaf()` enforces MAX_TOKEN_NESTING_DEPTH=20. `resolveReference()` returns null on circular or too-deep chains.

---

## 2. Component Token Model — 8 Sub-Tokens

Defined in `spec-config.yaml` under `component_sub_tokens`:

| # | Sub-token | Type | Description |
|---|-----------|------|-------------|
| 1 | `backgroundColor` | Color | Background color of the component |
| 2 | `textColor` | Color | Text/foreground color |
| 3 | `typography` | Typography | Reference to a typography token |
| 4 | `rounded` | Dimension | Corner radius |
| 5 | `padding` | Dimension | Internal padding |
| 6 | `size` | Dimension | General size |
| 7 | `height` | Dimension | Explicit height |
| 8 | `width` | Dimension | Explicit width |

These are the only recognized component property names. Any other property triggers a `broken-ref` finding at warning severity (overriding the rule's default error severity):

```typescript
// From broken-ref.ts
if (!(VALID_COMPONENT_SUB_TOKENS as readonly string[]).includes(propName)) {
  findings.push({
    severity: 'warning',  // overrides rule's default 'error'
    path: `components.${compName}.${propName}`,
    message: `'${propName}' is not a recognized component sub-token. Valid sub-tokens: ${VALID_COMPONENT_SUB_TOKENS.join(', ')}.`,
  });
}
```

**Variants** (hover, active, pressed) are expressed as separate component entries with related key names (e.g., `button-primary`, `button-primary-hover`, `button-primary-active`). The agent infers the relationship from naming.

Example:
```yaml
components:
  button-primary:
    backgroundColor: "{colors.tertiary}"
    textColor: "{colors.on-tertiary}"
    rounded: "{rounded.sm}"
    padding: 12px
  button-primary-hover:
    backgroundColor: "{colors.tertiary-container}"
```

---

## 3. Section Ordering — 8 Canonical Sections

Defined in `spec-config.yaml`, exported as `CANONICAL_ORDER`:

| # | Canonical Name | Aliases |
|---|----------------|---------|
| 1 | Overview | Brand & Style |
| 2 | Colors | — |
| 3 | Typography | — |
| 4 | Layout | Layout & Spacing |
| 5 | Elevation & Depth | Elevation |
| 6 | Shapes | — |
| 7 | Components | — |
| 8 | Do's and Don'ts | — |

- Sections use `##` (H2) headings. An optional `<h1>` may appear for titling but is not parsed as a section.
- Sections can be omitted, but those present must appear in canonical order.
- Aliases resolved via `resolveAlias()` function (builds a Map of alias→canonical).
- Unknown section headings (e.g., `## Iconography`) are preserved and do NOT error.
- Duplicate section headings → error, file rejected (handled by parser, `DUPLICATE_SECTION` error code).

### Fixer (fixSectionOrder)

`fixer/handler.ts` provides a `fixSectionOrder()` function that:
1. Separates prelude (content before first H2), known sections, and unknown sections.
2. Sorts known sections by `CANONICAL_ORDER.indexOf(resolveAlias(heading))`.
3. Concatenates: prelude → sorted known → unknown (preserved in original order).
4. Returns `FixerResult` with `fixedContent`, `beforeOrder`, `afterOrder`.

---

## 4. Lint Rules — 10 Rules (NOT 9)

> **IMPORTANT DISCREPANCY**: The README says "nine rules" and lists 9 in its table.
> The actual code in `linter/linter/rules/index.ts` defines **10** `DEFAULT_RULE_DESCRIPTORS`.
> The 10th rule, `token-like-ignored`, is implemented and active but missing from the README table.

### Rule Architecture

Each rule is a `RuleDescriptor`:

```typescript
interface RuleDescriptor {
  name: string;
  severity: Severity;   // 'error' | 'warning' | 'info'
  description: string;
  run: (state: DesignSystemState) => RuleFinding[];
}

interface RuleFinding {
  path?: string;
  message: string;
  severity?: Severity;  // optional override of descriptor's default
}
```

Rules are pure functions: `(state) → findings[]`. No side effects.
`toLintRule()` wraps a descriptor, injecting `descriptor.severity` as default when finding doesn't override.

`runLinter()` executes all rules via `flatMap` and aggregates counts by severity.

### Complete Rule Table (from source code)

| # | Rule Name | Severity | What It Checks | Source |
|---|-----------|----------|----------------|--------|
| 1 | `broken-ref` | error | Unresolved token references (`{colors.foo}` that don't exist) AND unknown component sub-tokens (overrides to warning for unknown props) | broken-ref.ts |
| 2 | `missing-primary` | warning | Colors defined but no `primary` key exists — agents will auto-generate one | missing-primary.ts |
| 3 | `contrast-ratio` | warning | Component `backgroundColor`/`textColor` pairs below WCAG AA minimum (4.5:1). Uses `contrastRatio()` = (L1+0.05)/(L2+0.05) | contrast-ratio.ts |
| 4 | `orphaned-tokens` | warning | Color tokens defined but never referenced by any component. Uses MD3 family-aware logic: if one token in a family is referenced, siblings are not flagged. MD3 standard families (primary, secondary, tertiary, error, surface, background, outline) are never flagged. | orphaned-tokens.ts |
| 5 | `token-summary` | info | Summary: "Design system defines N colors, M typography scales, ..." | token-summary.ts |
| 6 | `missing-sections` | info | Optional sections (spacing, rounded) absent when colors exist. Warns about fallback to agent defaults. | missing-sections.ts |
| 7 | `missing-typography` | warning | Colors defined but no typography tokens — agents will use default fonts | missing-typography.ts |
| 8 | `section-order` | warning | Sections appear out of canonical order. Resolves aliases, filters to known sections, checks sequential ordering. Reports first out-of-order pair, then breaks. | section-order.ts |
| 9 | `unknown-key` | warning | Top-level YAML key looks like a typo of a known schema key. Uses Levenshtein distance ≤ 2 (case-insensitive). Custom extension keys that aren't close matches stay silent. | unknown-key.ts |
| 10 | `token-like-ignored` | warning | Unknown top-level key whose value "looks like" a design-token map (contains hex colors, CSS dimensions, or typography property names like fontFamily). Warns it will be silently dropped by export. **NOT in README's rule table.** | token-like-ignored.ts |

### Key Implementation Details

**broken-ref** (error): Dual-purpose rule. Checks (a) unresolved references in components (from `comp.unresolvedRefs`), and (b) unknown component sub-token property names. The unknown-sub-token check overrides severity to `warning` even though the rule's default is `error`.

**contrast-ratio** (warning): Only checks components that have BOTH `backgroundColor` and `textColor` resolved to `ResolvedColor`. WCAG AA minimum = 4.5. Formula: `(max(L1,L2) + 0.05) / (min(L1,L2) + 0.05)`. Luminance comes from the color parser's sRGB conversion.

**orphaned-tokens** (warning): Sophisticated MD3-aware logic. `colorFamily()` strips MD3 prefixes (`on-`, `inverse-`) and suffixes (`-container*`, `-fixed*`, `-dim`, `-bright`, `-tint`, `-variant`). If any token in a family is referenced, siblings are considered in-use. MD3 standard families (`primary`, `secondary`, `tertiary`, `error`, `surface`, `background`, `outline`) are never flagged. Only runs when `components.size > 0`.

**section-order** (warning): Resolves aliases → filters to known sections → checks adjacent pairs. Reports the FIRST out-of-order pair and breaks (doesn't enumerate all violations).

**unknown-key** (warning): Levenshtein distance with max typo distance = 2. Case-insensitive comparison. Only flags if an unknown key is CLOSE to a known key. Pure extension keys (e.g., `myCustomSection`) stay silent.

**token-like-ignored** (warning): Heuristic detection — a value is "token-like" if it's an object containing: hex color strings (`#RRGGBB`), CSS dimension strings (`42px`), or typography property keys (`fontFamily`, `fontSize`, `fontWeight`, `lineHeight`, `letterSpacing`). Recurses one level for nested token maps. This is the rule NOT documented in the README.

### preEvaluate() — Graded Edit Menu

`runner.ts` also exports `preEvaluate()` which groups findings into:
- `fixes`: error-severity findings
- `improvements`: warning-severity findings  
- `suggestions`: info-severity findings

Returns `GradedTokenEdits` with `TokenEditEntry[]` per grade.

---

## 5. Export Formats

### 5.1 json-tailwind (Tailwind v3)

Handler: `TailwindEmitterHandler` in `tailwind/handler.ts`
Output: JSON object with `theme.extend` structure for `tailwind.config.js`

```typescript
{
  theme: {
    extend: {
      colors: { primary: "#1A1C1E", ... },
      fontFamily: { h1: ["Public Sans"], ... },
      fontSize: { h1: ["48px", { lineHeight: "1.1", letterSpacing: "-0.02em", fontWeight: "600" }], ... },
      borderRadius: { sm: "4px", md: "8px", ... },
      spacing: { sm: "8px", md: "16px", ... }
    }
  }
}
```

Mapping details:
- Colors → `colors` (hex string)
- Typography fontFamily → `fontFamily` (wrapped in array: `[fontFamily]`)
- Typography fontSize → `fontSize` (tuple: `[size, { lineHeight, letterSpacing, fontWeight }]`)
- Rounded → `borderRadius`
- Spacing → `spacing`
- Components are NOT exported

### 5.2 css-tailwind (Tailwind v4)

Handler: `TailwindV4EmitterHandler` in `tailwind/v4/handler.ts`
Serializer: `serializeToCss()` in `tailwind/v4/serialize.ts`
Output: CSS `@theme { ... }` block with CSS custom properties

```css
@theme {
  --color-primary: #1A1C1E;
  --font-h1: "Public Sans";
  --text-h1: 48px;
  --leading-h1: 1.1;
  --tracking-h1: -0.02em;
  --font-weight-h1: 600;
  --radius-sm: 4px;
  --spacing-sm: 8px;
}
```

CSS variable prefix mapping (output order):
| Category | Prefix |
|----------|--------|
| colors | `--color-` |
| fontFamily | `--font-` |
| fontSize | `--text-` |
| lineHeight | `--leading-` |
| letterSpacing | `--tracking-` |
| fontWeight | `--font-weight-` |
| borderRadius | `--radius-` |
| spacing | `--spacing-` |

Validation: token names must match `/^[a-zA-Z0-9][a-zA-Z0-9-]*$/` (valid CSS identifier). Invalid names return `INVALID_TOKEN_NAME` error. Font family values are wrapped in double quotes with `\` and `"` escaped.

### 5.3 tailwind (alias)

Alias for `json-tailwind`. Handled in the same branch:
```typescript
} else if (format === 'json-tailwind' || format === 'tailwind') {
```

### 5.4 dtcg (W3C Design Tokens Format Module)

Handler: `DtcgEmitterHandler` in `dtcg/handler.ts`
Output: W3C DTCG tokens.json (schema: `https://www.designtokens.org/schemas/2025.10/format.json`)

```json
{
  "$schema": "https://www.designtokens.org/schemas/2025.10/format.json",
  "$description": "Heritage",
  "color": {
    "$type": "color",
    "primary": { "$value": { "colorSpace": "srgb", "components": [0.102, 0.110, 0.118], "hex": "#1a1c1e" } }
  },
  "spacing": {
    "$type": "dimension",
    "sm": { "$value": { "value": 8, "unit": "px" } }
  },
  "rounded": {
    "$type": "dimension",
    "sm": { "$value": { "value": 4, "unit": "px" } }
  },
  "typography": {
    "h1": { "$type": "typography", "$value": { "fontFamily": "Public Sans", "fontSize": { "value": 48, "unit": "px" }, "fontWeight": 600, "lineHeight": 1.1, "letterSpacing": { "value": -0.02, "unit": "em" } } }
  }
}
```

Mapping details:
- Colors → `color` group with `$type: "color"`, values as `{ colorSpace: "srgb", components: [r,g,b], hex: "#..." }` (components normalized 0..1, rounded to 3 decimals)
- Spacing/Rounded → dimension groups with `$type: "dimension"`, values as `{ value: number, unit: string }`
- Typography → `typography` group, each token has `$type: "typography"` and `$value` with fontFamily, fontSize, fontWeight, lineHeight (unitless multiplier), letterSpacing
- Components are NOT exported
- Only non-empty groups are emitted

---

## 6. CLI Capabilities

### 6.1 Package

- **npm**: `@google/design.md` v0.3.0
- **Entry**: `dist/index.js` (ESM, `"type": "module"`)
- **Bins**: `design.md` and `designmd` (both → `./dist/index.js`)
- **Runtime**: Bun (build) / Node (consume)
- **Framework**: citty (CLI), zod (validation), yaml (YAML parsing), unified/remark (markdown parsing)
- **Exports**: `.` (main) and `./linter` (programmatic API)

### 6.2 Commands

**`lint <file>`** — Validate a DESIGN.md file.
- Input: file path or `-` (stdin)
- Output: JSON `{ findings: Finding[], summary: { errors, warnings, infos } }`
- Exit code: 1 if errors > 0, else 0
- Pipeline: parse → model resolve → runLinter → output

**`diff <before> <after>`** — Compare two DESIGN.md files.
- Output: JSON with token diffs (added/removed/modified per category), finding summary delta, regression boolean
- Regression = after has more errors OR more warnings than before
- Exit code: 1 if regression detected
- Diffs computed for: colors, typography, rounded, spacing, components (serialized to plain objects)
- Uses `diffMaps()` utility

**`export --format <fmt> <file>`** — Export tokens to other formats.
- Formats: `json-tailwind`, `css-tailwind`, `tailwind` (alias), `dtcg`
- Invalid format → JSON error, exit 1
- Pipeline: lint (to get designSystem) → handler.execute → output
- Exit code: 1 if lint errors OR handler failure

**`spec [--rules] [--rules-only] [--format markdown|json]`** — Output the format specification.
- `--rules`: append linting rules table
- `--rules-only`: output only the rules table
- `--format`: `markdown` (default) or `json`
- Uses `DEFAULT_RULE_DESCRIPTORS` for rule metadata
- Spec content from `getSpecContent()` (reads generated docs/spec.md)

### 6.3 Programmatic API

```typescript
import { lint } from '@google/design.md/linter';

const report = lint(markdownString);
// report.findings: Finding[]
// report.summary: { errors, warnings, infos }
// report.designSystem: DesignSystemState
// report.tailwindConfig: TailwindEmitterResult
// report.sections: string[]
// report.documentSections: Array<{ heading, content }>
```

Also exported:
- `runLinter(state, rules?)` — run rules against pre-parsed state
- `preEvaluate(state, rules?)` — graded edit menu (fixes/improvements/suggestions)
- `DEFAULT_RULES` — array of 10 LintRule functions
- `DEFAULT_RULE_DESCRIPTORS` — array of 10 RuleDescriptor objects
- Individual rule functions: `brokenRef`, `missingPrimary`, `contrastCheck`, `orphanedTokens`, `tokenSummary`, `missingSections`, `missingTypography`, `unknownKey`, `sectionOrder`, `tokenLikeIgnored`
- `contrastRatio(a, b)` — WCAG contrast ratio between two ResolvedColors
- `TailwindEmitterHandler`, `TailwindV4EmitterHandler`, `serializeTailwindV4`
- `DtcgEmitterHandler`
- `fixSectionOrder(input)` — auto-fix section ordering

### 6.4 LintReport Interface

```typescript
interface LintReport {
  designSystem: DesignSystemState;
  findings: Finding[];
  summary: { errors: number; warnings: number; infos: number };
  tailwindConfig: TailwindEmitterResult;
  sections: string[];
  documentSections: Array<{ heading: string; content: string }>;
}
```

Note: `summary.infos` in the interface vs `summary.info` in some code paths — the lint command outputs `summary` directly from the report.

### 6.5 Windows Quirks

- `design.md` bin name collides with Windows `.md` file association.
- `designmd` alias provided as workaround (identical entrypoint).
- PowerShell: use `npx -p @google/design.md designmd lint DESIGN.md`
- package.json scripts: use `"design:lint": "designmd lint DESIGN.md"`

### 6.6 Parser Error Codes

```typescript
ParserErrorCode = ['EMPTY_CONTENT', 'NO_YAML_FOUND', 'YAML_PARSE_ERROR', 'DUPLICATE_SECTION', 'UNKNOWN_ERROR']
```

Recoverable errors (e.g., `NO_YAML_FOUND`) return an empty design system with a warning finding instead of throwing. Non-recoverable errors throw.

### 6.7 Model Error Codes

```typescript
ModelErrorCode = ['INVALID_COLOR', 'INVALID_DIMENSION', 'INVALID_TYPOGRAPHY_PROP',
  'UNRESOLVED_REFERENCE', 'CIRCULAR_REFERENCE', 'REFERENCE_TO_NON_PRIMITIVE',
  'NESTING_DEPTH_EXCEEDED', 'UNKNOWN_ERROR']
```

---

## 7. Consumer Behavior for Unknown Content

| Scenario | Behavior | Implementation |
|----------|----------|----------------|
| Unknown section heading | Preserve; do not error | Parser keeps it; section-order rule ignores unknowns |
| Unknown color token name | Accept if value is valid | Model handler validates value, not name |
| Unknown typography token name | Accept as valid typography | Model handler parses any name |
| Unknown spacing value | Accept; store as string if not valid dimension | `forEachLeaf` stores non-dimensions as raw in symbolTable |
| Unknown component property | Accept with warning | `broken-ref` rule flags at warning severity |
| Duplicate section heading | Error; reject the file | Parser returns `DUPLICATE_SECTION` error |

---

## 8. Recommended Token Names (Non-Normative)

From `spec-config.yaml` → `recommended_tokens`:

- **Colors**: `primary`, `secondary`, `tertiary`, `neutral`, `surface`, `on-surface`, `error`
- **Typography**: `headline-display`, `headline-lg`, `headline-md`, `body-lg`, `body-md`, `body-sm`, `label-lg`, `label-md`, `label-sm`
- **Rounded**: `none`, `sm`, `md`, `lg`, `xl`, `full`

Core color roles (from `color_roles`): `primary`, `secondary`, `tertiary`, `neutral`

---

## 9. Architecture Notes

### 9.1 Spec Configuration as Single Source of Truth

`spec-config.yaml` is the canonical source. `spec-config.ts` loads it (lazy singleton), validates with Zod, and exports derived constants. `docs/spec.md` is generated from this via `bun run spec:gen`.

### 9.2 Pipeline Architecture

```
Raw content
  → ParserHandler.execute()     [remark-parse + yaml]
  → ParsedDesignSystem
  → ModelHandler.execute()      [resolve tokens, build symbolTable]
  → DesignSystemState
  → runLinter(state, rules)     [10 rules, pure functions]
  → Finding[]
  → (optional) TailwindEmitterHandler / DtcgEmitterHandler
  → LintReport
```

### 9.3 Handler Pattern

All major components follow a `Spec` interface pattern:
- `ParserSpec.execute(input) → ParserResult`
- `ModelSpec.execute(input) → ModelResult`
- `TailwindEmitterSpec.execute(state) → TailwindEmitterResult`
- `DtcgEmitterSpec.execute(state) → DtcgEmitterResult`

Results are discriminated unions: `{ success: true, data } | { success: false, error }`.

---

## 10. Discrepancies and Observations

1. **README says "nine rules", code has 10**: `token-like-ignored` is the 10th rule, active in `DEFAULT_RULE_DESCRIPTORS`, but absent from the README's linting rules table.

2. **README mentions `contrast-ratio` in its table but the export name in code is `contrastCheck`**: The rule descriptor name is `contrast-ratio` (matches README), but the exported function is `contrastCheck`.

3. **Components not exported**: Neither Tailwind nor DTCG exporters emit component definitions. Only primitive tokens (colors, typography, rounded, spacing) are exported.

4. **`broken-ref` dual severity**: The rule's default severity is `error`, but unknown component sub-tokens within it override to `warning`. This means the same rule produces both errors and warnings.

5. **Spec version is `alpha`**: The format is under active development. Expect breaking changes.

6. **DTCG lineHeight**: Converted to unitless number regardless of original unit. This may lose information for dimension-based line heights (e.g., `24px` becomes `24` instead of preserving the unit).

7. **Tailwind v3 fontSize format**: Uses tuple `[size, { meta }]` where meta includes lineHeight, letterSpacing, fontWeight — but NOT fontFamily or fontFeature/fontVariation.

8. **Nested linter directory**: The codebase has `src/linter/linter/` (linter within linter), which is unusual. The inner `linter/` contains rules, runner, and spec types.
