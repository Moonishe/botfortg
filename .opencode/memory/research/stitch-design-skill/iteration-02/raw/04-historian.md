# Researcher 4 — Historian
## stitch-skills monorepo evolution & stitch-design provenance

**Source URLs:**
- https://www.skills.sh/google-labs-code/stitch-skills/stitch-design
- https://github.com/google-labs-code/stitch-skills (repo, releases, full commit history pages 1-2)
- https://github.com/google-labs-code (org overview)

**Date of research:** 2026-06-22
**Method:** webfetch (markdown) on skills.sh page, GitHub repo README, releases page, commit history (2 pages, all 66 commits visible), org landing page.

---

## SUMMARY

The `google-labs-code/stitch-skills` repository is a monorepo of Agent Skills for the Google Stitch MCP server (stitch.withgoogle.com), following the Agent Skills open standard (agentskills.io). It is the #2 most-starred repo in the `google-labs-code` org (6,109 stars), behind `design.md` (16,055 stars) and ahead of `stitch-sdk` (1,715 stars). The repo lives within a coordinated ecosystem: `design.md` (the DESIGN.md format spec) + `stitch-sdk` (programmatic Stitch API) + `stitch-skills` (agent-facing skills consuming the Stitch MCP).

**Repo identity:** Despite living under the `google-labs-code` GitHub org, the README carries an explicit disclaimer: *"This is not an officially supported Google product. This project is not eligible for the Google Open Source Software Vulnerability Rewards Program."* So it is Google-Labs-originated, Google-employee-maintained, but NOT officially Google-supported. License: Apache-2.0.

**Monorepo evolution — three distinct eras:**

1. **Flat-skills era (Jan 16 – Mar 10, 2026):** Repo began as a flat collection of individual skills (react-components, DESIGN.md, stitch-loop, enhance-prompt, remotion, shadcn-ui). No plugin grouping. First release v0.1 (Mar 4, 2026) shipped in this shape. `design-md` and `enhance-prompt` were standalone skills.

2. **Unification attempt (Mar 11 – May 5, 2026):** On Mar 11, JLXIA committed *"create a new stitch-design skill, deprecate design-md and enhance-prompt"* — the first appearance of the `stitch-design` name as a unifying entry point. The next day (Mar 12) this was partially walked back: *"keep design-md and enhance-prompt skills"* (they survived as standalone skills alongside the new stitch-design). The `skills.sh` "First Seen: Mar 13, 2026" timestamp aligns with PR #36 merge on that date — the moment stitch-design was published/indexed on the skills registry. A community-contributed `taste-design` skill (#38) landed Mar 30.

3. **Plugin-architecture era (May 6 – present, 2026):** On May 6, JLXIA committed *"Stitch Skills V2"* (a1f77d5) — a major rewrite. On May 10, commit 5532ce0 *"refactor: restructure skills into plugin architecture"* split everything into three plugins: **stitch-design** (6 skills), **stitch-build** (4 skills), **stitch-utilities** (4 skills). On May 18, four rapid security-fix commits preceded the v1.0 release (tag 2c93fbc, PR #59). v1.0 release notes confirm: *"Reorg: structure all skills into three plugins"* + *"Refactor: merge several design related skills to generate-design"*. Post-v1.0: Codex plugin metadata (May 20), community hex-color fix (May 25-26), React Native skill added then moved into stitch-build (Jun 2-4), markdown-upload + provenance tracking for stitch-design (Jun 16-17).

**stitch-design vs stitch-build vs stitch-utilities (current v1.0 architecture):**

| Plugin | Role | Skills (count) | Key skills |
|--------|------|----------------|------------|
| **stitch-design** | Core design workflows: create, manage, optimize designs IN Stitch | 6 | code-to-design, generate-design, manage-design-system, extract-design-md, extract-static-html, upload-to-stitch |
| **stitch-build** | Code generation FROM Stitch designs: framework integration, asset compilation | 4 | react-components, react-native, remotion, shadcn-ui |
| **stitch-utilities** | Supporting tools: prompt enhancement, design-spec generation, standards enforcement | 4 | design-md, enhance-prompt, stitch-loop, taste-design |

The `stitch-design` skill specifically (as listed on skills.sh) is the **"unified design system entry point"** — it transforms rough ideas into structured prompts, synthesizes `.stitch/DESIGN.md` source-of-truth docs, routes between generation/editing/documentation workflows, and auto-downloads assets to `.stitch/designs`. Note the overlap: `design-md` and `enhance-prompt` still exist as standalone skills in stitch-utilities, but stitch-design supersedes them as the recommended entry point.

**Key contributors (from commit authorship):**
- `davideast` (uid 4570265) — primary early author (react-components, DESIGN.md, stitch-loop, enhance-prompt, shadcn-ui review). Google employee.
- `dalmaer` (uid 4770) — early PR merges, remotion skill. Google employee (Dalmaer / Aaron Boodman).
- `jedborovik` (uid 1231882) — authored the literal "Initial commit" (28cde21).
- `JLXIA` (uid 43076783) — primary maintainer from Mar 2026 onward. Drove V2 rewrite, plugin architecture, security fixes, stitch-design creation, all release tagging. The dominant force behind the current shape.
- `meeChn` (uid 1460397) — Codex plugin metadata + marketplace manifest (May 20).
- Community contributors: `vinothsubramanian` (shadcn-ui), `yuvrajangadsingh` (react-native), `Leonxlnx` (taste-design), `Michacallhome` (hex-color fix), `amourfrei` (Codex plugins PR), `AsadSumbul` (docs).
- `google-labs-jules[bot]` — AI-assisted performance optimization (regex-in-loop fix, Feb 11).

**Metrics (as observed 2026-06-22):**

| Metric | Value | Source |
|--------|-------|--------|
| skills.sh installs | 25.3K | skills.sh page |
| GitHub stars | 6,109 (displayed as 6.1k) | GitHub org page + repo |
| Forks | 739 | GitHub repo |
| Total commits | 66 | GitHub commit history (2 pages, fully visible) |
| Releases | 2 (v0.1, v1.0) | GitHub releases page |
| Open issues | 10 | GitHub repo header |
| Open PRs | 8 | GitHub repo header |
| Watchers | 54 | GitHub repo sidebar |
| First seen (skills.sh) | Mar 13, 2026 | skills.sh page |
| Languages | TypeScript 89.6%, Shell 5.2%, Python 5.2% | GitHub repo |
| License | Apache-2.0 | GitHub repo |
| Security audits (skills.sh) | Gen Agent Trust Hub: Pass; Socket: Pass; Snyk: Warn | skills.sh page |

**Cross-checks:**
- "First Seen Mar 13, 2026" on skills.sh ↔ PR #36 merge on Mar 13, 2026 in commit history. CONFIRMED match. This is when stitch-design was first indexed/published on the skills registry.
- "2 releases" ↔ v0.1 (Mar 4, 2026, tag 51d6d5a) + v1.0 (May 18, 2026, tag 2c93fbc). CONFIRMED.
- "66 commits" ↔ commit history shows 2 pages totaling 66 entries (page 1: May 6–Jun 17; page 2: Jan 16–May 6). CONFIRMED.
- "6.1K stars" ↔ org page shows 6,109. CONFIRMED (rounded).
- "Google Labs Code but not Google-supported" ↔ explicit README disclaimer. CONFIRMED.

---

## TIMELINE

### Era 1 — Flat-skills (Jan 16 – Mar 10, 2026)

| Date | Event | Commit / PR | Author |
|------|-------|-------------|--------|
| **2026-01-16** | Repo inception: "Initial commit" + "init" + react-components skill + seed-repo merge (PR #1) | 28cde21, cefcc7a, 581d7d0, 72cc442, 6083ef7 | jedborovik (initial), davideast (skills), dalmaer (merge) |
| **2026-01-22** | DESIGN.md skill (#3), fix skills directory (#4), add allowed_tools to DESIGN.md skill (#5) | f66e8c5, 9972895, a697ca5 | davideast |
| **2026-01-23** | stitch-loop skill (#6) | c7886fa | davideast |
| **2026-01-29** | enhance-prompt skill (feat/enhance, PR #10) | 00128d4, dbb6ff3 | davideast, dalmaer (merge) |
| **2026-02-03** | remotion skill (#11), CLI command add-skill→skills fix (#13), shadcn-ui skill incl. v4 features (#14) | 3ed28cf, 5bc8706, 29152aa | dalmaer, vinothsubramanian |
| **2026-02-11** | perf: avoid creating regex inside loop in validate.js (AI-assisted by google-labs-jules bot) | 982115d | davideast + jules bot |
| **2026-02-12** | SECURITY.md added, docs: normalize skills install command (#21) | d1390b5, 2a74df8 | davideast, AsadSumbul |
| **2026-02-17** | Merge PR #22 (performance-optimize-regex-validate) | 51d6d5a | dalmaer (merge) |
| **2026-03-04** | **Release v0.1** (tag at 51d6d5a) — first release, flat-skills shape | release v0.1 | JLXIA (tagged) |
| **2026-03-05** | .stitch directory convention + png download (#31), metadata.json update, PR #33 merge | aa363ee, d891a26, a92f689 | JLXIA |

### Era 2 — Unification attempt (Mar 11 – May 5, 2026)

| Date | Event | Commit | Author |
|------|-------|--------|--------|
| **2026-03-11** | **"create a new stitch-design skill, deprecate design-md and enhance-prompt"** — first appearance of stitch-design as unifying entry point | 01daeeb | JLXIA |
| **2026-03-12** | Walked back deprecation: "keep design-md and enhance-prompt skills" (×2 commits); delete .agents dir | 8b0e31d, 858f7e1, fdd0571 | JLXIA |
| **2026-03-13** | Merge PR #36 (jilin branch) — **matches skills.sh "First Seen" date**; stitch-design indexed on registry | ad0b5cc | JLXIA |
| **2026-03-30** | taste-design skill (#38) — community contribution | 6c0cbdb | Leonxlnx |

### Era 3 — Plugin architecture / V2 (May 6 – present, 2026)

| Date | Event | Commit / PR | Author |
|------|-------|-------------|--------|
| **2026-05-06** | **"Stitch Skills V2"** — major rewrite; update code-to-design to use DS v2 | a1f77d5, a791083 | JLXIA |
| **2026-05-07** | README MCP setup, prompt examples per skill, snapshot.ts improvements, timeout increase | b5a7fde, f039125, 4871339, d3ea25f, c961fb7, 2c6dd58, e5ddccb | JLXIA |
| **2026-05-10** | **"refactor: restructure skills into plugin architecture"** (5532ce0) — the three-plugin split (stitch-design / stitch-build / stitch-utilities); docs: consolidate prompt examples | 5532ce0, 8c85e1b | JLXIA |
| **2026-05-12** | install guide update, minor/path fixes, SSL certificate tip | adbc294, 27e0dc0, 681a283, be8147c, ea4e830 | JLXIA |
| **2026-05-17** | support uploading DESIGN.md through script | a2d69f7 | JLXIA |
| **2026-05-18** | 4× security-risk fixes; Merge PR #59 (stitch-skills-plugins); **Release v1.0** (tag 2c93fbc) — formalizes three-plugin architecture | 8e34bd0, 3bd73f2, cf11888, 66f6f5e, 2c93fbc | JLXIA |
| **2026-05-20** | Codex plugin metadata, marketplace manifest add/remove/restore, upstream URL fix; PR #60 (support-codex-plugins) | cbfb385, 4800041, 4b76f44, a2ca474, 21db6cd | meeChn, amourfrei, JLXIA (merge) |
| **2026-05-25** | fix(react-components): detect hex colors in JSX className | 241b53e | Michacallhome |
| **2026-05-26** | Merge PR #64 (fix/validate-hex-detection) | 53f15d8 | JLXIA (merge) |
| **2026-06-02** | Add React Native skill (#42) | c43ae1d | yuvrajangadsingh, davideast |
| **2026-06-04** | refactor: move react-native skill into stitch-build plugin; README updates (directory tree, quick-start formatting); Merge PR #67 (refactor) | 99a7d17, 922c4f7, 5140d0f, 1544aa4 | JLXIA, rustinb303 (merge) |
| **2026-06-16** | feat(stitch-design): add markdown upload support and generated-by provenance tracking | 22716f9 | JLXIA |
| **2026-06-17** | Merge PR #71 (jilin) — latest commit on main | fac7324 | JLXIA |

### Release summary

| Release | Date | Tag commit | Key changes |
|---------|------|------------|-------------|
| **v0.1** | 2026-03-04 | 51d6d5a (PR #22) | Flat-skills era; performance-optimize-regex-validate merge |
| **v1.0** (latest) | 2026-05-18 | 2c93fbc (PR #59) | Major launch: new skills (code-to-design, manage-design-system, extract-static-html, extract-design-md, upload-to-stitch); merged several design skills into generate-design; reorganized all skills into 3 plugins |

---

## CONFIDENCE

**Overall: HIGH (4.5/5)**

| Finding | Confidence | Basis |
|---------|------------|-------|
| Repo inception date (Jan 16, 2026) | **Very High** | Direct from commit history (Initial commit 28cde21, Jan 16) |
| Three-era evolution (flat → unification → plugins) | **Very High** | Commit messages explicitly narrate each transition (01daeeb, a1f77d5, 5532ce0, v1.0 notes) |
| stitch-design first seen = Mar 13, 2026 | **Very High** | skills.sh "First Seen" matches PR #36 merge date exactly |
| v0.1 = Mar 4, 2026; v1.0 = May 18, 2026 | **Very High** | Direct from GitHub releases page with dates + SHAs |
| 66 commits total | **High** | Two commit-history pages fully captured; count consistent with GitHub "66 Commits" header |
| 25.3K installs / 6.1K stars | **High** | skills.sh page + GitHub org page cross-confirmed (6,109 stars) |
| stitch-design vs build vs utilities skill composition | **Very High** | README tables list all 14 skills with descriptions + prompt examples |
| "Not officially supported Google product" | **Very High** | Verbatim disclaimer in README |
| Contributor roles (davideast=early, JLXIA=V2 maintainer) | **High** | Inferred from commit authorship across 66 commits; consistent pattern |
| Ecosystem relationship (design.md + stitch-sdk + stitch-skills) | **High** | Org landing page ranks + descriptions; stitch-skills README references Stitch MCP setup docs |
| Snyk "Warn" audit finding significance | **Medium** | skills.sh shows Snyk: Warn but no detail fetched on what triggered the warning |

**Gaps / not verified:**
- Exact commit dates for v0.1/v1.0 tagging vs. merge dates (release page shows "04 Mar 21:34" and "18 May 18:02" — assumed UTC, not independently confirmed).
- Contributor count and full contributor graph did not load ("Uh oh! There was an error while loading" on GitHub).
- Snyk warning specifics not fetched (audit detail page not retrieved).
- Whether `stitch-design` skill on skills.sh corresponds 1:1 to the `plugins/stitch-design/` plugin or to a specific SKILL.md within it — the skills.sh SKILL.md excerpt matches the "Stitch Design Expert" persona which appears to be the plugin-level entry, but the exact file mapping was not confirmed by fetching the raw SKILL.md from the repo.
- No fetch of issues/PRs content (10 issues, 8 PRs) — historical context from discussions not captured.
