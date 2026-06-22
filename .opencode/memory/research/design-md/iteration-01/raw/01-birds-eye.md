# Bird's Eye Perspective: design.md at a glance

## Sources / Tools
- `webfetch` of `README.md` and GitHub repo page.
- `git clone` into `%TEMP%\opencode\design-md-clone` + `glob` to enumerate structure.
- `read` of `package.json`, `turbo.json`, `packages/cli/package.json`.

## Project identity
- **Repo**: `https://github.com/google-labs-code/design.md`
- **Purpose**: A plain-text format for describing a visual identity to coding agents (AI + humans).
- **Premise**: Combine machine-readable YAML design tokens (front matter) with human-readable markdown rationale (body). Tokens are normative; prose carries intent.
- **Status**: Version `alpha` (actively changing).
- **License**: Apache-2.0.

## Community / metrics (from GitHub page, 2026-06-22)
- Stars: ~16k
- Forks: ~1.5k
- Watchers: ~100
- Commits: 40 (main branch)
- Releases: 4 (latest `0.3.0` dated 2026-06-15)
- Open issues: ~15, open PRs: ~2
- Languages: TypeScript 95.7%, MDX 3.9%, JavaScript 0.4%

## Repository structure
```
design.md/
  .github/workflows/        # CI (presumed)
  docs/
    spec.md                 # Generated from spec.mdx + spec-config.ts
  examples/
    totality-festival/       # DESIGN.md + tailwind.config.js + design_tokens.json
    atmospheric-glass/       # DESIGN.md + tailwind.config.js + design_tokens.json
  packages/
    cli/                     # The @google/design.md npm package
      src/
        commands/            # lint, diff, export, spec
        linter/
          parser/            # YAML frontmatter / fenced-yaml extraction
          model/             # color/dimension/typography resolution, symbols
          linter/            # rule runner + 10 rules
            rules/           # broken-ref, contrast-ratio, section-order, etc.
          tailwind/          # v3 theme.extend emitter
          tailwind/v4/       # v4 @theme emitter
          dtcg/              # W3C DTCG tokens.json emitter
          spec-gen/          # docs/spec.md generator
        version.ts
        index.ts
      package.json
  README.md, PHILOSOPHY.md, CONTRIBUTING.md
  package.json (bun monorepo root)
  turbo.json
  tsconfig.base.json
  bun.lock
  skills-lock.json
```

## Stack / tooling
- **Runtime**: Bun (1.3.9+). `packageManager: "bun@1.3.9"`.
- **Build**: `bun build` + `tsc --emitDeclarationOnly`; `turbo` for monorepo orchestration.
- **Test**: `bun test`.
- **CLI framework**: `citty`.
- **Markdown / YAML**: `remark-parse`, `remark-frontmatter`, `remark-mdx`, `unified`, `yaml`.
- **Validation**: `zod`.
- **Export targets**: Tailwind v3, Tailwind v4, W3C DTCG tokens.json.

## What it does (functions)
1. Parses a `DESIGN.md` file into YAML front matter + markdown sections.
2. Resolves colors, dimensions, typography, and component references.
3. Lints for broken references, missing tokens, section order, WCAG contrast, etc.
4. Diffs two design systems.
5. Exports tokens to Tailwind v3/v4 and DTCG JSON.
6. Prints the living spec for agent context injection.

## Core design philosophy
- Prose > tokens: "The quality of a generated design is determined less by the precision of its values than by how clearly the intent is described."
- Negative constraints emerge naturally from a specific reference (e.g., "1970s graduate handout" implies no glows, no gradients).
- Extensible by default: unknown sections, keys, and token groups are accepted rather than rejected, so teams can add motion, iconography, etc.

## Evidence for this perspective
- `README.md` lines 1-6 and the whole PHILOSOPHY.md section "Prose, not Tokens, is the focus of the specification".
- `packages/cli/package.json` shows the `@google/design.md` npm package with `design.md` and `designmd` bin shims.
- `turbo.json` shows a small monorepo pipeline: build -> test -> lint.
- GitHub repo page shows metrics and language breakdown.
