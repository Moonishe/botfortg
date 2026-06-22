# Researcher 4 — Historian (Context/Evolution Perspective)

**Repository:** https://github.com/openclaw/openclaw
**Date:** 2026-06-22
**Sources fetched:** GitHub main page, CONTRIBUTING.md, Releases pages 1 and 22
**Sources failed:** CHANGELOG.md and package.json (raw.githubusercontent.com transport errors — raw host unreachable from this environment)

---

## 1. Repository Metrics (from GitHub main page)

| Metric | Value |
|--------|-------|
| Stars | 380k |
| Forks | 79.5k |
| Commits | 61,441 |
| Open issues | 3.3k |
| Open pull requests | 3.2k |
| Security alerts | 602 |
| Release pages | 22 (paginated) |
| License | MIT |
| Primary language | TypeScript (pnpm workspace monorepo) |

Tagline: "Your own personal AI assistant. Any OS. Any Platform. The lobster way."

Created by **Peter Steinberger** (@steipete) and the community. Built for "Molty," a space lobster AI assistant.

---

## 2. Release Train Versioning (YYYY.M.PATCH / CalVer)

### Versioning scheme

The project uses **calendar versioning (CalVer)** in the format `vYYYY.M.D`:

- **Stable channel:** tagged releases `vYYYY.M.D` or `vYYYY.M.D-<patch>`, npm dist-tag `latest`
- **Beta channel:** prerelease tags `vYYYY.M.D-beta.N`, npm dist-tag `beta` (macOS app may be missing)
- **Dev channel:** moving head of `main`, npm dist-tag `dev` (when published)

Switch channels: `openclaw update --channel stable|beta|dev`

This is explicitly documented in the README under "Development channels" and confirmed by the releases page. The scheme encodes the release date directly into the version, making it immediately clear when a release was cut.

### Origin: semver → CalVer evolution

The earliest releases (page 22 of 22) reveal the project was **originally named "warelay"** and used **semantic versioning (0.1.x)**:

- **warelay 0.1.1** — 25 Nov (by @steipete) — first public release, executable shim, CLI help banner
- **warelay 0.1.2** — 25 Nov (by @steipete) — commander help config fix for TypeScript build
- **warelay 0.1.3** — 25 Nov (by @steipete) — added `cwd` option, configurable file-based logging

All three were released on the same day (25 Nov), suggesting a rapid initial MVP launch. The project was a "web relay" tool at this stage.

At some point between Nov 2025 and the current CalVer releases, the project:
1. Was renamed from "warelay" to "openclaw"
2. Shifted from semver to CalVer (`vYYYY.M.D`)
3. Expanded from a web relay into a full personal AI assistant platform
4. Grew from a single-author project to 25+ named maintainers

---

## 3. Release Timeline (June 2026 — most recent)

Extracted from releases page 1. All dates in June 2026:

| Version | Date | Type | PRs in window | Window |
|---------|------|------|---------------|--------|
| v2026.6.10-beta.1 | 21 Jun 09:12 | Pre-release | 109 | v2026.6.9-beta.1..HEAD |
| v2026.6.9 | 21 Jun 01:44 | **Latest stable** | 422 | v2026.6.8..HEAD |
| v2026.6.9-beta.1 | 19 Jun 05:52 | Pre-release | 375 | v2026.6.8..HEAD~1 |
| v2026.6.8 | 16 Jun 16:32 | Stable | 192 | v2026.6.6..v2026.6.8 |
| v2026.6.8-beta.2 | 16 Jun 01:50 | Pre-release | 192 | v2026.6.6..v2026.6.8 |
| v2026.6.8-beta.1 | 14 Jun 22:45 | Pre-release | 192 | v2026.6.6..v2026.6.8 |
| v2026.6.7-beta.1 | 13 Jun 09:42 | Pre-release | 59 | v2026.6.6..v2026.6.7-beta.1 |
| v2026.6.6 | 12 Jun 11:04 | Stable | 198 | v2026.6.5..v2026.6.6 |
| v2026.6.6-beta.2 | 12 Jun 03:32 | Pre-release | 198 | v2026.6.5..v2026.6.6 |
| (v2026.6.5) | referenced | prior stable | — | — |

### Release cadence observations

- **Multiple releases per week**: 9+ releases in a 10-day window (12 Jun – 21 Jun)
- **Beta → stable pipeline**: each stable release is preceded by one or more beta releases
- **Same-day stable + next beta**: v2026.6.9 stable and v2026.6.10-beta.1 both on 21 Jun
- **Automated release tooling**: most releases published by `github-actions` bot, some by @vincentkoc (release engineer)
- **Signed tags**: beta releases by @vincentkoc carry verified SSH signatures

---

## 4. PR Velocity

### Per-release PR counts (from audited release records)

| Release | Merged PRs | Note |
|---------|-----------|------|
| v2026.6.9 (latest stable) | **422** | The "423 PRs" referenced in the research brief — actual count is 422 |
| v2026.6.9-beta.1 | 375 | |
| v2026.6.10-beta.1 | 109 | Incremental from beta.1 |
| v2026.6.8 (all betas + stable) | 192 | |
| v2026.6.7-beta.1 | 59 | |
| v2026.6.6 | 198 | |

### Analysis

- **Extremely high throughput**: 422 PRs in a single stable release cycle (v2026.6.8 → v2026.6.9) is exceptional
- The release notes state: "This audited record covers the complete v2026.6.8..HEAD history: 422 merged PRs. The generation manifest also supplies direct commits as editorial input"
- Each release includes a **"Complete contribution record"** — an audited, per-PR breakdown with PR numbers, titles, related issues, and contributor attribution
- The v2026.6.10-beta.1 release alone credits "vincentkoc, nxmxbbd, and 92 other contributors" — **95 contributors in a single beta**
- PR numbering is in the 90,000+ range (e.g., #95250), confirming 61,441 commits across the project lifetime

### Release note generation

Release notes are grouped by theme (Highlights → Changes → Fixes → Complete contribution record), suggesting automated or semi-automated generation from PR metadata with editorial grouping. The "generation manifest" reference implies a tool (likely ClawSweeper or a custom release tool) assembles these from git history.

---

## 5. AI-Assisted PR Transparency Policy

### From CONTRIBUTING.md

The project has an explicit, welcoming, and structured policy for AI-assisted PRs:

**Section: "AI/Vibe-Coded PRs Welcome! 🤖"**

> Built with Codex, Claude, or other AI tools? **Awesome - just mark it!**

Required for AI-assisted PRs:
- [ ] Mark as AI-assisted in the PR title or description
- [ ] Include a concise **Evidence** section with the most useful validation
- [ ] Include prompts or session logs if possible (described as "super helpful!")
- [ ] Confirm you understand what the code does
- [ ] Run `codex review --base origin/main` locally and address findings before review
- [ ] Resolve or reply to bot review conversations after addressing them

> "AI PRs are first-class citizens here. We just want transparency so reviewers know what to look for."

### Evidence of AI-assisted PRs in the wild

Release notes explicitly tag AI-assisted PRs:
- PR #81696: "fix: guard tool event callbacks **(AI-assisted)**" — by @enjoylife1243
- PR #92564: "fix(agents): isolate invalid plugin model catalogs **[AI-assisted]**" — by @tangtaizong666 and @fxstein

### AI-powered review tooling

- **Codex review**: `codex review --base origin/main` is described as "the current highest standard of AI review" — even higher than GitHub's built-in Codex review
- **ClawSweeper**: a bot that asks for context/evidence, adds changelog entries when landing changes, and manages PR triage
- **Barnacle**: another bot referenced in the review process
- Contributors are expected to handle AI bot review conversations themselves ("Review Conversations Are Author-Owned")

---

## 6. Engineering Discipline

### 6.1 PR Limits

> "We cap at **20 open PRs per author**. If you exceed this, the `r: too-many-prs` label is added and your PR is auto-closed. This is a hard limit."

For coordinated change sets needing more than 20 PRs, contributors must join the #clawtributors Discord channel and coordinate with maintainers first.

### 6.2 PR Quality Requirements

- **"What Problem This Solves"** section required for external PRs
- **"Evidence"** section required (tests, CI results, screenshots, recordings, terminal output, redacted logs, artifact links)
- **"Why This Change Was Made"**, **"User Impact"**, and **"Evidence"** must stay current in the PR body
- PRs must be **takeover-ready**: branch maintainers can push to, or fork PRs with "Allow edits by maintainers" enabled
- One concern per PR ("Keep PRs focused")
- Screenshots required for UI/visual changes (before + after)
- American English in all code, comments, docs, UI strings

### 6.3 Docs-First / Changelog Discipline

- **Contributors must NOT edit `CHANGELOG.md`** — maintainers or ClawSweeper add changelog entries when landing user-facing changes
- This prevents merge conflicts and keeps the changelog authoritative
- Release notes are generated from the audited PR record, not hand-edited

### 6.4 Prohibited PR Types

- No **refactor-only PRs** unless a maintainer explicitly requests them for an active fix
- No **test/CI-only PRs** for known `main` failures (maintainer team tracks these)
- No test-only PRs that just make known CI failures pass

### 6.5 Security Scanning

The repository contains multiple security tooling configurations:

| File | Purpose |
|------|---------|
| `.semgrepignore` | Semgrep static analysis (security scanning) — ignore rules |
| `.pre-commit-config.yaml` | Pre-commit hooks for local quality gates |
| `.oxlintrc.json` | OxLint (Rust-based linter) configuration |
| `.oxfmtrc.jsonc` | OxFmt (Rust-based formatter) configuration |
| `.crabbox.yaml` | Custom tooling (likely project-specific quality/config) |
| `security/` | Dedicated security directory |
| `git-hooks/` | Git hooks directory |
| `SECURITY.md` | Security policy |
| `CODEOWNERS` | Security ownership (referenced in CONTRIBUTING.md) |

**Note on "OpenGrep":** The research brief mentions "OpenGrep security scanning." The repository does not contain an explicit "OpenGrep" reference in the fetched sources. The actual security scanning tool identified is **Semgrep** (`.semgrepignore` file present). OpenGrep may be an internal name, a planned tool, or a reference to the grep-like code search capabilities. The project does have 602 security alerts active on GitHub.

### 6.6 CODEOWNERS Security Ownership

From CONTRIBUTING.md:
> "Do not edit files covered by `CODEOWNERS` security ownership unless a listed owner explicitly asked for the change or is already reviewing it with you. Treat those paths as restricted review surfaces, not opportunistic cleanup targets."

This enforces privileged review for security-sensitive code paths.

### 6.7 Testing Infrastructure

| Tool | Role |
|------|------|
| vitest | Test runner (`vitest.config.ts`) |
| tsdown | Build tool (`tsdown.config.ts`) |
| pnpm | Package manager (workspace monorepo) |
| `pnpm test:extension` | Per-extension fast test lane |
| `pnpm test:contracts` | Contract tests for shared surfaces |
| `pnpm test:contracts:channels` | Channel contract tests |
| `pnpm test:contracts:plugins` | Plugin contract tests |
| `qa/` | QA scenario directory (YAML-based) |
| Extension import boundary checks | `scripts/check-src-extension-import-boundary.mjs` etc. |

The testing strategy is layered: fast local lanes for extensions → contract tests for shared surfaces → full test suite. QA scenarios migrated to YAML (noted in v2026.6.9 release notes).

### 6.8 Vulnerability Reporting

Structured security report requirements:
1. Title
2. Severity Assessment
3. Impact
4. Affected Component
5. Technical Reproduction
6. Demonstrated Impact
7. Environment
8. Remediation Advice

> "Reports without reproduction steps, demonstrated impact, and remediation advice will be deprioritized. Given the volume of AI-generated scanner findings, we must ensure we're receiving vetted reports from researchers who understand the issues."

This is a direct response to AI-generated security noise — requiring human vetting.

---

## 7. Maintainer Team Evolution

### Current team (25+ named maintainers)

Led by **Peter Steinberger** (@steipete) as "Benevolent Dictator." The team has specialized roles:

- Frank Yang — PR triage, Agents, Gateway, Channels
- Shadow — Discord subsystem, ClawHub, community moderation
- Vignesh — Memory (QMD), formal modeling, TUI, IRC
- Jos — Telegram, API, Nix mode
- Ayaan Zaidi — Telegram, Android app
- Tyler Yust — Agents/subagents, cron, iMessage, macOS app
- Mariano Belinky — iOS app, Security
- Nimrod Gutman — iOS app, macOS app
- Vincent Koc — Agents, Telemetry, Hooks, Security (also release engineer)
- Val Alexander — UI/UX, Docs, SDK, Agent DevX
- Seb Slight — Docs, Agent Reliability, Runtime Hardening
- Christoph Nakazawa — JS Infra (former Jest maintainer)
- + 13 more maintainers covering Chinese channels, CLI, performance, etc.

### Maintainer expansion policy

> "Being a maintainer is a responsibility, not an honorary title. We expect active, consistent involvement."
> "We review every human-only-written application carefully and add maintainers slowly and deliberately."

---

## 8. Evolution Trajectory

### Phase 1: warelay (Nov 2025)
- Single-author project by Peter Steinberger
- Semver 0.1.x versioning
- Web relay tool (command auto-replies, file-based logging)
- npm package, CLI tool

### Phase 2: Transition to openclaw (late 2025 / early 2026)
- Rename to "openclaw" (lobster branding)
- Shift to CalVer (YYYY.M.D) release train
- Expansion to multi-channel AI assistant (WhatsApp, Telegram, Slack, Discord, etc.)
- Local-first Gateway architecture

### Phase 3: Rapid scaling (2026)
- 22 pages of releases → hundreds of releases
- 61,441 commits, 380k stars
- 25+ maintainers, 95+ contributors per beta
- 422 PRs in a single stable release
- Plugin SDK, ClawHub skill marketplace
- Companion apps (macOS, iOS, Android, Windows Hub)
- Voice Wake, Live Canvas, multi-agent routing
- Codex integration, OpenTelemetry, sandboxing

### Key architectural evolution signals (from release notes)
- v2026.6.9: "Standalone official provider plugins" — externalization of providers to npm packages
- v2026.6.9: "Codex Hosted Search" — hosted AI search integration
- v2026.6.10-beta.1: "Broader plugin and skill coverage" — Zalo channel, Trello skills
- Progressive hardening: SSH tunnel loopback checks, bounded input lengths, volatile SQLite state detection
- QA moved to YAML-based scenarios

---

## 9. Trends

1. **Accelerating release cadence**: Multiple releases per week, beta→stable pipeline within days
2. **Extreme PR throughput**: 422 PRs per stable release, 95+ contributors per beta — community-driven at massive scale
3. **AI-native engineering workflow**: Codex review as standard, AI-assisted PRs as first-class citizens, ClawSweeper bot automation, transparent AI labeling
4. **Progressive externalization**: providers becoming standalone npm packages, plugins loading at Gateway startup, ClawHub for skills
5. **Defense-in-depth security**: semgrep, CODEOWNERS, sandbox modes, DM pairing, bounded inputs, structured vuln reporting
6. **Disciplined contributor governance**: 20-PR hard limit, no refactor-only PRs, evidence requirements, author-owned review conversations
7. **Platform expansion**: from web relay → multi-channel AI assistant → voice/canvas/mobile companion ecosystem
8. **Monorepo maturation**: pnpm workspace, layered test lanes, contract tests, import boundary enforcement, Rust-based tooling (oxlint/oxfmt)

---

## 10. Data Gaps / Limitations

- **CHANGELOG.md**: could not fetch (raw.githubusercontent.com transport error). Would contain the full version history narrative.
- **package.json**: could not fetch (same transport error). Would confirm exact current version string and dependencies.
- **Releases pages 2-21**: not fetched. Would show the full evolution from warelay 0.1.x through to v2026.6.5, including the rename point and CalVer transition date.
- **OpenGrep**: no explicit reference found in fetched sources. The project uses Semgrep for static analysis. OpenGrep may be internal/unreleased or referenced in unfetched files.
- **Exact transition date**: when warelay was renamed to openclaw and switched to CalVer is not pinned — would require fetching release pages 18-21.
