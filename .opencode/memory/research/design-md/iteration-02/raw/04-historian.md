# Researcher 4 (Historian) — design.md Repository History

**Repository:** https://github.com/google-labs-code/design.md
**Data sources:** GitHub main page, /releases, /commits/main, PHILOSOPHY.md (raw), VoltAgent/awesome-design-md, Stitch docs link
**Fetched:** 2026-06-22

---

## SUMMARY

`google-labs-code/design.md` is a young, fast-moving Google Labs experiment that defines **DESIGN.md** — a plain-text format (YAML front-matter tokens + markdown prose) for handing a visual design system to AI coding agents. It is the spec + CLI tooling counterpart to **Google Stitch** (stitch.withgoogle.com), which is the consumer-facing AI design product that introduced the DESIGN.md concept.

**Vital statistics (as of 2026-06-22):**
- Stars: **16.1k** · Forks: **1.5k** · Watchers: **101**
- Commits: **40** (on `main`)
- Releases: **4** — 0.1.0, 0.1.1, 0.2.0, 0.3.0
- License: Apache-2.0
- Languages: TypeScript 95.7%, MDX 3.9%, JavaScript 0.4%
- npm package: `@google/design.md`
- Official spec home: `stitch.withgoogle.com/docs/design-md/specification`

**Lifecycle arc:** First commit Apr 11, 2026 → latest release 0.3.0 Jun 15, 2026. The entire public history spans ~9 weeks. In that window it went from zero to 16.1k stars — roughly 1,600 stars/week — an exceptionally viral trajectory for a format-specification repo, driven by the broader "vibe coding" / AI-agent movement and the Google Stitch launch.

**Two distinct "version" axes (important distinction):**
1. **Format/spec version = `alpha`.** The token schema's optional `version` field is currently `"alpha"`. README Status states: *"The DESIGN.md format is at version alpha. The spec, token schema, and CLI are under active development. Expect changes to the format as it matures."* This labels the **specification itself** as not-yet-stabilized.
2. **CLI/software version = semver 0.1.0 → 0.3.0.** Pre-1.0 semver, independently tracking the npm-published tooling. Both axes signal immaturity, but they are separate: the format is "alpha" while the CLI marches through 0.x.y.

**Alpha label meaning (decoded):** "alpha" is a deliberate, philosophical choice — not a placeholder. The PHILOSOPHY.md (added Jun 3, 2026, PR #99) makes the intent explicit: *"The format grows through its users, not its spec."* The spec defines only a structural minimum (name + a few universal token categories); everything beyond is user-defined and accepted by the linter without error. The alpha label warns consumers that the **structural minimum itself** may still change. This pairs with two other signals of "experiment, not product" status: (a) the repo lives under the `google-labs-code` org (Google Labs experiments), and (b) the disclaimer — *"This project is not eligible for the Google Open Source Software Vulnerability Rewards Program"* — the standard marker Google attaches to Labs/experimental OSS to distance it from fully-supported Google products.

**Relationship to VoltAgent/awesome-design-md:** This is a **third-party ecosystem consumer**, NOT a contributor to or fork of the official repo. VoltAgent (an AI agent framework company, voltagent.dev) maintains `awesome-design-md` (92.1k stars, 10.9k forks, 60 commits, MIT) as a curated collection of ready-to-use DESIGN.md files reverse-engineered from 73+ real brand websites (Apple, Stripe, Nike, Linear, Vercel, Spotify, etc.). It explicitly credits Google Stitch as the originator (*"DESIGN.md is a new concept introduced by Google Stitch"*), links to the official Stitch spec, and conforms to the design.md format while extending it with extra sections (Visual Theme & Atmosphere, Component Stylings, Responsive Behavior, Agent Prompt Guide). The awesome-list is hosted at `getdesign.md`. Notably the community collection (92.1k stars) is ~5.7x more starred than the official spec repo (16.1k) — the ecosystem around the format is outpacing the format tooling itself, a strong product-market-fit signal for the concept even as the spec remains alpha.

**Contributor profile:** The project is led by **davideast** (David East — well-known Google developer advocate, formerly Firebase). He authored all 4 releases and the majority of core commits. Other Google-affiliated contributors: xkxx, samelhusseini, chelseayerong. A healthy stream of **external community PRs** was merged (vikks, SyedaQurratAI, ryo-manba, Bortlesboat, mvanhorn, sbrsubuvga, dalmaer, voidborne-d, Saatvik-GT, tejas100, tototofu123, coliff, camilojheans, friendglak) — high external-contribution ratio for a 9-week-old repo, indicating strong open-source engagement rather than a closed Google-only dev model.

---

## TIMELINE

### Pre-release development phase (Apr 11 – Apr 20, 2026)

| Date | Event |
|------|-------|
| 2026-04-11 | First commits: example DESIGN.md directories (xkxx) + `feat(linter): refactor parser to use remark for AST parsing` (#3, davideast). Remark/MDX chosen as the markdown AST foundation. |
| 2026-04-13 | `feat(cli): ship @google/design.md CLI toolkit` (#5, davideast) — the CLI appears. Merge of examples PR #4 (xkxx). |
| 2026-04-14 | `Update package.json with metadata fields` (#7, samelhusseini) + `feat: add missing-typography lint rule` (#6, chelseayerong). |
| 2026-04-15 | `docs: update some spec phrasing` (#8, xkxx). |
| 2026-04-20 | `docs: update recommended tokens in spec` (#9, davideast). |

### v0.1.x — Initial public release (Apr 21, 2026)

| Date | Event |
|------|-------|
| 2026-04-21 | `docs: set up licensing and headers` (#10) → `chore: packaging for release` (#11) → **RELEASE 0.1.0** (commit 7eec9e0, davideast). Initial open-source release: design-system linter, spec generator, agent-first deterministic JSON output, Windows `designmd` bin alias, self-contained package. |
| 2026-04-21 | `docs: update README and spec generation` (#12) → **RELEASE 0.1.1** (commit 6589f05, davideast). Same-day patch: replaced outdated `tailwind` command docs with the correct `export` command; documented Tailwind + DTCG export. |
| 2026-04-22 | `fix: resolve token references in rounded and spacing sections` (#26, tejas100) + `fix: support transparent hex colors` (#24, tototofu123). First external-contributor fixes. |

### v0.2.0 — Feature expansion (May 1 – May 26, 2026)

| Date | Event |
|------|-------|
| 2026-05-01 | Windows-friendly `designmd` bin alias (#62), checkout v4→v6 (#58, coliff), orphaned-tokens MD3 paired-token fix (#59, mvanhorn). |
| 2026-05-02 | Tailwind export format rename (#64), **Tailwind CSS v4 export** (#45, sbrsubuvga), Windows npm docs + registry smoke test (#57, camilojheans/friendglak), component token diff (#51, Saatvik-GT), numeric component prop crash fix (#43, tejas100). |
| 2026-05-08 | `feat: support standard and CSS Color Module formats in validator and linter` (#73, **dalmaer** — notable: Dalmaer is a respected web-platform figure). |
| 2026-05-26 | **RELEASE 0.2.0** (commit 814134e / release commit ad4a492, davideast). Changelog: Tailwind v4 `css-tailwind` export, CSS Color Module support, `designmd` Windows alias, component token diff, renamed export formats (`css-tailwind`/`json-tailwind`/`tailwind` alias), orphaned-token false-positive fixes, numeric-prop crash fix, transparent hex colors, rounded/spacing token-ref resolution. 8 new external contributors acknowledged. |

### v0.3.0 — Stabilization + philosophy (Jun 2 – Jun 15, 2026)

| Date | Event |
|------|-------|
| 2026-06-02 | Burst of 7 commits: stale-deps removal + lint script + CI bun pin (#98), color spec docs for all CSS color formats (#96), **unknown-key lint rule** (#84, ryo-manba), `lint --format markdown` rendering fix (#95), boolean YAML scalar handling (#94), non-string YAML property crash fix (#79, SyedaQurratAI), Tailwind v4 digit-prefixed token names (#72, Bortlesboat). |
| 2026-06-03 | `docs: add PHILOSOPHY.md` (#99, davideast) — establishes the "prose over tokens," "specific reference over adjectives," "format grows through users" doctrine. Key philosophical foundation for the alpha label. |
| 2026-06-11 | `fix: support nested token declarations in frontmatter` (#103, vikks) + `docs: document Windows/PowerShell npx invocation` (#104, mvanhorn). |
| 2026-06-15 | `feat: add token-like-ignored lint rule` (#105, mvanhorn) → **RELEASE 0.3.0** (commit 2a19f5d, davideast). Bumps from 0.2.0. Bug fixes: nested token declarations, non-string YAML values, boolean YAML scalars, markdown lint rendering, Tailwind v4 digit tokens, stale-dep removal. Features: unknown top-level key warnings, nested token support. Docs: PHILOSOPHY.md, Windows/PowerShell npx, CSS color formats. 5 external contributors credited. |

### Ecosystem parallel — VoltAgent/awesome-design-md

| Marker | Detail |
|--------|--------|
| Identity | Third-party curated collection (NOT a Google repo). 92.1k stars / 10.9k forks / 60 commits / MIT. |
| Origin credit | README: *"DESIGN.md is a new concept introduced by Google Stitch."* Links to `stitch.withgoogle.com/docs/design-md/specification/`. |
| Content | 73+ ready-made DESIGN.md files extracted from real brand sites (Apple, Stripe, Nike, Linear, Vercel, Spotify, BMW, Ferrari, etc.) + retro-web nostalgia series (Dell 1996, Nintendo 2001). Hosted at `getdesign.md`. |
| Format relationship | Conforms to the Stitch DESIGN.md format; **extends** it with extra sections (Visual Theme & Atmosphere, Component Stylings, Layout Principles, Depth & Elevation, Do's and Don'ts, Responsive Behavior, Agent Prompt Guide). Each entry ships `DESIGN.md` + `preview.html` + `preview-dark.html`. |
| Role | Consumer/ecosystem-builder around the spec, not a contributor to the spec repo. Demonstrates the format's adoption surface. |

---

## CONFIDENCE

| Claim | Confidence | Basis |
|-------|------------|-------|
| Repo stats (16.1k stars, 1.5k forks, 40 commits, 4 releases) | **High** | Directly observed on GitHub main + releases + commits pages (fetched 2026-06-22). |
| Release dates & version sequence (0.1.0→0.1.1→0.2.0→0.3.0) | **High** | Directly observed on /releases page with exact dates and commit SHAs. |
| Commit-by-commit timeline (Apr 11 – Jun 15, 2026) | **High** | Directly observed on /commits/main page; 40 commits enumerated with dates, authors, PR numbers. |
| "alpha" = format-spec version, separate from CLI semver | **High** | README Status section + token schema doc (`version: <string> # optional, current: "alpha"`) explicitly state this. |
| Alpha = deliberate experimental signal (Labs + VRP disclaimer) | **High** | `google-labs-code` org + explicit VRP disclaimer in README. |
| PHILOSOPHY.md rationale for alpha ("grows through users") | **High** | Fetched raw PHILOSOPHY.md content directly. |
| davideast = lead maintainer (David East, Google) | **High** | All 4 releases authored by davideast; he authored the majority of core commits. David East's identity as Google dev advocate is well-established public knowledge. |
| VoltAgent/awesome-design-md is third-party, not Google | **High** | Different org (VoltAgent), MIT vs Apache-2.0, explicitly credits Google Stitch as originator, no shared contributors visible. |
| awesome-design-md stats (92.1k stars, 73+ brands) | **High** | Directly observed on its GitHub page. |
| Star-growth rate (~1,600/week) | **Medium** | Computed from first-commit date (Apr 11) to fetch date (Jun 22) vs 16.1k stars; assumes roughly linear growth, which is an approximation. Actual growth was likely front-loaded around the Stitch launch and viral spikes. |
| External-contributor ratio indicates healthy OSS engagement | **Medium** | Inferred from PR author attribution in commits/releases; some "Google-affiliated" classifications (xkxx, samelhusseini, chelseayerong) are inferred from context, not verified employment. |
| Stitch is the consumer product; design.md repo is the spec/CLI | **High** | Repo About links to stitch.withgoogle.com/docs/design-md/specification; README frames design.md as "a format specification"; awesome-design.md describes DESIGN.md as "introduced by Google Stitch." |

**Overall confidence: High.** All core factual claims (stats, dates, versions, alpha meaning, VoltAgent relationship) are grounded in directly-fetched primary sources. The only medium-confidence items are growth-rate linearity and contributor-affiliation inferences, which are interpretive rather than factual.
