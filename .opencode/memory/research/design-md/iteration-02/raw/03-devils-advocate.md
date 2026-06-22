# Researcher 3 — Devil's Advocate

**Repository:** https://github.com/google-labs-code/design.md
**Role:** Devil's Advocate (adversarial risk assessment)
**Date:** 2026-06-22
**Sources:** README.md, docs/spec.md, PHILOSOPHY.md, CONTRIBUTING.md, package.json,
  CLI source (export.ts, handler.ts, v4/handler.ts, dtcg/handler.ts, lint.ts),
  releases page, open issues (#47, #48, #55, #92, #101), npm package metadata

---

## SUMMARY

DESIGN.md is a 2-month-old (Apr 21 – Jun 15, 2026) alpha-stage format from Google
Labs for feeding design systems to coding agents. It pairs YAML front-matter
tokens with markdown prose. The CLI (`@google/design.md` v0.3.0) provides lint,
diff, export, and spec subcommands. 16.1k stars / 1.5k forks signal strong
interest, but the underlying artefact is thin: 40 commits, 4 releases, a single
primary maintainer (@davideast), and an explicit `version: "alpha"` tag with the
warning "Expect changes to the format as it matures."

This assessment surfaces seven risk vectors. The most severe are: (1) the export
pipeline **silently drops** every token category the PHILOSOPHY.md actively
encourages users to define (motion, shadows, iconography, elevation) — a
data-loss footgun with no warning or error; (2) the DTCG lineHeight export
**discards the unit**, converting `24px` into `24` (a 24x multiplier) — a
semantic corruption; (3) the Windows CLI bin-name collision makes the canonical
`npx @google/design.md` invocation fail silently on the most common developer OS,
requiring a non-obvious workaround; and (4) the project carries explicit
governance risk — Google CLA required, VRP-ineligible, issue creation restricted,
Labs-org experiment with no stability or deprecation policy.

Adoption for production design-system pipelines is premature. The format is
usable today for **agent context injection** (its stated primary purpose), but
the export/interop path is not safe for round-trip or CI-gated workflows.

---

## CRITICAL_ISSUES

### C1. Export silently drops custom token categories (DATA LOSS)

**Severity: Critical — silent data loss**

PHILOSOPHY.md explicitly encourages users to define custom token categories the
spec leaves open: "motion, iconography, elevation, text casing, paragraph
measure." It even provides a full `motion:` block example with durations and
easing curves.

The linter accepts these unknown keys (since 0.3.0, with a warning) and the
parser preserves them in the parsed state. But **every export emitter ignores
them entirely**:

- **TailwindEmitterHandler** (v3, `tailwind/handler.ts`): maps `colors`,
  `fontFamily`, `fontSize`, `borderRadius`, `spacing` only.
- **TailwindV4EmitterHandler** (v4, `tailwind/v4/handler.ts`): maps `colors`,
  `fontFamily`, `fontSize`, `lineHeight`, `letterSpacing`, `fontWeight`,
  `borderRadius`, `spacing` only.
- **DtcgEmitterHandler** (`dtcg/handler.ts`): maps `color`, `spacing`,
  `rounded`, `typography` only.

No emitter reads `state` for motion, shadows, iconography, or any unknown
top-level key. The export emits **no warning, no error, no log** — the tokens
simply vanish from the output. A user who follows the PHILOSOPHY.md guidance,
defines `motion:`, `shadows:`, or `icon:` tokens, then runs `export --format
dtcg` will get a `tokens.json` that is silently incomplete.

Open issues confirm these are recognised gaps, not oversights:
- #47 — "Feature: motion tokens (duration, easing) with prefers-reduced-motion
  fallback" (opened Apr 24, 2026, still open)
- #92 — "Feature: structured shadows / elevation token category" (opened May 26,
  2026, still open)
- #101 — "spec: Iconography section" (opened Jun 4, 2026, still open)

**Risk:** Round-trip fidelity is broken. DESIGN.md → DTCG → Figma/Tokens Studio
→ DESIGN.md loses all custom tokens. CI pipelines that diff exported output will
not catch the loss because the export succeeds with exit code 0.

**Mitigation unavailable:** There is no `--strict` flag, no "unknown tokens
dropped" warning in export output, and no programmatic way to enumerate what was
dropped without diffing the input state against the output yourself.

### C2. DTCG lineHeight export discards the unit (SEMANTIC CORRUPTION)

**Severity: Critical — silent semantic error**

In `dtcg/handler.ts`, the `typographyToValue` method handles lineHeight:

```typescript
if (typo.lineHeight) {
  // DTCG lineHeight is a unitless multiplier of fontSize.
  // Our model stores it as a ResolvedDimension. Convert if possible.
  // If unit is a relative unit, just use the numeric value as a multiplier.
  value.lineHeight = typo.lineHeight.value;
}
```

It unconditionally takes `typo.lineHeight.value` (the numeric component) and
**discards `typo.lineHeight.unit`**. The spec (`docs/spec.md`) says lineHeight
"Accepts either a Dimension (e.g., `24px`, `1.5rem`) or a unitless number (e.g.,
`1.6`). A unitless number represents a multiplier of the element's `fontSize`."

Consequences:
- `lineHeight: 24px` → exported as `24` → DTCG consumer interprets this as a
  **24x multiplier** of fontSize, not 24px. A 16px heading would get a
  384px line height. This is a **24x semantic error**.
- `lineHeight: 1.5rem` → exported as `1.5` → happens to be numerically correct
  only if the consumer treats it as a multiplier, but the original intent (a
  dimension relative to root font size) is lost.
- `lineHeight: 1.6` (unitless, the recommended form) → exported as `1.6` →
  correct by coincidence.

The W3C DTCG format (2025.10) supports both a unitless number and a dimension
value for lineHeight. The exporter could preserve the unit by emitting a
dimension token when a unit is present. It does not. The comment in the code
acknowledges the model stores a `ResolvedDimension`, but the implementation
throws away the unit for all cases rather than only for unitless inputs.

**Risk:** Any design system using absolute or rem-based lineHeight values will
produce broken DTCG output. The error is silent — no warning, no validation.

### C3. Windows CLI bin collision — canonical invocation is broken by default

**Severity: High — platform-specific silent failure**

`packages/cli/package.json` defines:
```json
"bin": {
  "design.md": "./dist/index.js",
  "designmd": "./dist/index.js"
}
```

Both bins point to the same entrypoint. The primary bin name `design.md` has a
`.md` suffix that collides with the Windows Markdown file association. The
README documents the failure mode: "this direct form can produce no output (or
open `DESIGN.md` in your Markdown editor) because the `.md` suffix in the
`design.md` bin name collides with the Windows Markdown file association during
command resolution."

The workaround is verbose and non-obvious:
```
npx -p @google/design.md designmd lint DESIGN.md
```

The `designmd` alias was added in 0.1.0 (#62 in 0.2.0 credited to
@voidborne-d) specifically to work around this, but the **primary bin name
remains `design.md`** — meaning the first command every new user tries (the one
in the README's "Getting Started") silently fails on Windows. The README now
documents the workaround, but the default experience is broken.

Additional Windows friction:
- `npm install @google/design.md` may need quoting in PowerShell (`"@google/design.md"`)
- Issue #55 ("npm install @google/design.md failing") is still open
- `ENOVERSIONS` errors are common with corporate npm mirrors that haven't synced
  the `@google` scope

**Risk:** Windows is the most common developer OS. First-run experience fails
silently (no output, or editor opens instead of CLI running). Users may conclude
the tool is broken and abandon it before reaching the workaround documentation.

### C4. Alpha format instability — no stability guarantees

**Severity: High — format churn risk**

Direct evidence from the README "Status" section: "The DESIGN.md format is at
version `alpha`. The spec, token schema, and CLI are under active development.
Expect changes to the format as it matures."

Additional signals:
- **40 commits total** across the entire repository lifetime (~2 months).
- **4 releases**: 0.1.0 (Apr 21) → 0.1.1 (Apr 21, same day, docs fix) → 0.2.0
  (May 26) → 0.3.0 (Jun 15). All pre-1.0.
- **Breaking changes already happened**: 0.2.0 renamed the Tailwind export
  formats (`tailwind` → `json-tailwind` / `css-tailwind`), with `tailwind`
  retained only as a backwards-compatible alias. The `diff` command silently
  skipped component tokens until #51 fixed it in 0.2.0.
- **Spec is self-generating**: `docs/spec.md` is generated from `spec.mdx` +
  `spec-config.ts` ("Do not edit directly. Run `bun run spec:gen` to
  regenerate."). The schema is code-driven and can change at any commit.
- **Components spec explicitly unstable**: "the components specification is
  actively evolving. The current structure provides intentional flexibility for
  domain-specific component definitions while the spec matures."
- **`version` field is optional**: the schema lists `version: <string>` as
  optional with current value "alpha." There is no validation that a DESIGN.md
  file declares its version, and no consumer behaviour defined for
  version-mismatch.

**Risk:** Any DESIGN.md file written today may not lint correctly under the next
release. CI pipelines pinning `@google/design.md` versions will need to track
format changes manually. There is no changelog for the **format** itself (only
for the CLI package), so schema-level breaking changes are not explicitly
documented.

### C5. Tailwind v4 export — all-or-nothing naming strictness

**Severity: Medium-High — hard failure on common token names**

`tailwind/v4/handler.ts` enforces:
```typescript
const VALID_TOKEN_NAME = /^[a-zA-Z0-9][a-zA-Z0-9-]*$/;
```

The handler validates **every** token name (colors, typography, rounded,
spacing) against this regex before emitting anything. If **any single** token
name fails, the entire export aborts with:
```
{ "error": "Token name \"X\" is not a valid CSS identifier for Tailwind v4
  export (must match /^[a-zA-Z0-9][a-zA-Z0-9-]*$/)." }
```

Names that will fail:
- Underscored names: `on_primary`, `primary_container` (MD3 snake_case convention)
- Dotted names: `primary.60` (used in spec examples: `colors.primary-60`)
- Names with spaces or special characters
- Names starting with a hyphen: `-sm`

The 0.3.0 release notes mention "Tailwind v4 export handles digit-prefixed
token names" (#72) — confirming this has been an ongoing friction point that
required patches. The fix allowed names starting with digits, but underscore and
dot-separated names remain unsupported.

**Risk:** Material Design 3 token systems commonly use hyphens (`on-primary`),
which work, but systems using underscores or dots will hit a hard wall. The
failure is all-or-nothing — one non-conforming token aborts the entire export
with no partial output and no per-token skip option.

### C6. Limited contrast coverage — narrow accessibility checking

**Severity: Medium — false sense of accessibility compliance**

The `contrast-ratio` lint rule (severity: warning) checks only component
`backgroundColor`/`textColor` pairs against WCAG AA (4.5:1). Gaps:

1. **Only component-level pairs**: Colors defined outside components (surface,
   background, on-surface) are never checked. A design system with no
   `components:` block gets zero contrast checks.
2. **Only 4.5:1 threshold**: No distinction between normal text (4.5:1) and
   large text (3:1). No AAA (7:1) check. No option to select the target level.
3. **No non-text contrast**: WCAG SC 1.4.11 (non-text contrast, 3:1 for UI
   component boundaries and focus indicators) is not checked at all.
4. **No colour-blind simulation**: Issue #48 ("color-blind contrast lint rule
   (opt-in, Brettel-Viénot-Mollon simulation)") is open and unresolved.
5. **Warning even on pass**: The README example shows a "warning" finding for a
   pair that *passes* WCAG AA (15.42:1) — the severity model is confusing. A
   pass produces a warning, which inflates warning counts and trains users to
   ignore warnings.
6. **sRGB conversion only**: The spec says "All color values are internally
   converted to sRGB for WCAG contrast checking." Wide-gamut colours
   (`oklch()`, `oklab()`) are converted, which may lose perceptual accuracy
   for out-of-sRGB-gamut colours. The contrast formula used is not documented
   (WCAG 2.x relative luminance vs. APCA/WCAG 3.0 contrast).

**Risk:** A DESIGN.md that lints clean (0 errors, 0 warnings on contrast) is
**not** WCAG-compliant. Teams may treat a clean lint as an accessibility
sign-off, creating false confidence. The linter checks the easiest 10% of
accessibility and ignores the rest.

### C7. Google Labs governance risk — experimental, CLA-gated, VRP-ineligible

**Severity: Medium-High — long-term viability and contribution risk**

| Signal | Evidence |
|--------|----------|
| **40 commits** | Extremely small history. Entire repo is ~2 months old. |
| **Single primary maintainer** | All 4 releases published by @davideast. Bus factor = 1. |
| **Google CLA required** | CONTRIBUTING.md: "Contributions to this project must be accompanied by a Contributor License Agreement." External contributors must sign Google's CLA, which grants Google a broad patent license. This is a friction point for corporate contributors whose legal teams review CLAs. |
| **VRP-ineligible** | README disclaimer: "This project is not eligible for the Google Open Source Software Vulnerability Rewards Program." No bug bounty. Security researchers have no financial incentive to audit the CLI (which parses untrusted YAML and markdown input). |
| **Issue creation restricted** | Issues page: "Issue creation is restricted in this repository." Not all users can file bug reports. This limits community feedback and bug surfacing. |
| **`google-labs-code` org** | This is a Google Labs experiment, not a core Google product (like Angular, TensorFlow). Labs projects can be archived or abandoned without notice. No deprecation policy is published. |
| **Publishing via internal proxy** | `publishConfig.registry` = `wombat-dressing-room.appspot.com` — a Google-internal npm publishing proxy. External contributors cannot publish; all releases flow through Google's infrastructure. |
| **No security policy** | The repo's "Security and quality" tab shows 0 advisories, no SECURITY.md, no coordinated-disclosure process. Combined with VRP ineligibility, there is no formal path to report or fix security issues. |
| **No stability/deprecation policy** | No documented commitment to semver, no LTS window, no EOL notice period. The alpha tag is the only signal. |
| **`bun` + `turbo` toolchain** | Monorepo uses Bun 1.3.9 as package manager (not npm/yarn/pnpm). Contributors must install Bun. This is a non-standard toolchain that limits contribution accessibility. |

**Risk:** Adopting DESIGN.md as a critical dependency in a production pipeline
means betting on a 2-month-old Labs experiment with one maintainer, a
CLA-gated contribution process, no security reporting path, and no commitment
to continued development. If Google Labs archives the project, the npm package
remains but the spec and CLI stop evolving.

---

## CONFIDENCE

| Finding | Confidence | Basis |
|---------|------------|-------|
| C1 — Export silently drops custom tokens | **Very High** | Source code of all three emitters read directly; only colors/typography/rounded/spacing are mapped. PHILOSOPHY.md explicitly encourages motion/iconography/elevation. Open issues #47, #92, #101 confirm gaps. |
| C2 — DTCG lineHeight unit discarded | **Very High** | Source code of `dtcg/handler.ts` read directly: `value.lineHeight = typo.lineHeight.value` unconditionally drops `.unit`. Spec confirms lineHeight accepts Dimension with units. |
| C3 — Windows bin collision | **Very High** | `package.json` bin field read directly. README documents the failure and workaround. Release 0.1.0/0.2.0 changelog confirms the `designmd` alias was added as a fix. Issue #55 confirms install failures. |
| C4 — Alpha instability | **High** | README "Status" section is explicit. 40 commits / 4 releases verified on GitHub. Breaking format rename in 0.2.0 verified in release notes. Spec is self-generated from code. |
| C5 — Tailwind v4 naming strictness | **Very High** | `VALID_TOKEN_NAME` regex read in source. All-or-nothing validation loop confirmed. 0.3.0 changelog confirms prior naming fixes (#72). |
| C6 — Limited contrast coverage | **High** | Lint rules table in README confirms only `contrast-ratio` rule checks component bg/text pairs. Spec confirms sRGB conversion. Issue #48 confirms no colour-blind check. No large-text/AAA/non-text checks found anywhere. |
| C7 — Governance risk | **High** | CONTRIBUTING.md confirms CLA. README confirms VRP ineligibility. GitHub confirms 40 commits, restricted issues, Labs org. package.json confirms internal publishing proxy. No SECURITY.md or stability policy found. |

**Overall confidence: High.** All seven findings are grounded in primary
sources (source code, spec, README, CONTRIBUTING, release notes, issues). No
finding relies on speculation. The highest-impact risks (C1, C2) are confirmed
by reading the actual emitter implementation. The governance risks (C7) are
documented facts, not predictions.

**Caveats:**
- The project is 2 months old and actively evolving; some issues (especially C1,
  C5, C6) may be resolved in future releases. Open issues show the maintainers
  are aware of these gaps.
- 16.1k stars indicate strong community interest, which may pressure Google to
  stabilise the format. However, star count is not a commitment.
- The CLI is one consumer of the format. The format itself (YAML + markdown) is
  human-readable and usable without the CLI. The risks above primarily affect
  the automated export/lint pipeline, not manual authoring.
- I could not access the `linter/rules/` directory contents to confirm the exact
  contrast formula implementation (WCAG 2.x vs. APCA). The contrast rule's
  internal algorithm is inferred from the spec's sRGB-conversion note and the
  README's 4.5:1 threshold, not from reading the rule source directly.
