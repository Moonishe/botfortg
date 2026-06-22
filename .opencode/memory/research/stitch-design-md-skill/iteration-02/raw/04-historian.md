# 04-historian.md - Researcher 4 (Historian)
# Subject: google-labs-code/stitch-skills - design-md skill lineage and repository history
# Deep Research iteration-02 - raw findings
# Date: 2026-06-22
# Sources: skills.sh (Vercel), github.com/google-labs-code/stitch-skills (commits, releases, README, repo metadata), stitch.withgoogle.com

---

## SUMMARY

**google-labs-code/stitch-skills** is an open-source (Apache-2.0) library of Agent Skills that wrap the **Google Stitch** AI design product (cloud service at stitch.withgoogle.com, tagline "Stitch - Design with AI"). The skills communicate with Stitch projects through the **Stitch MCP server** (setup docs at stitch.withgoogle.com/docs/mcp/setup), bridging the closed Stitch cloud product and open coding agents (Codex, Antigravity, Gemini CLI, Claude Code, Cursor). The repository lives under the **google-labs-code** GitHub organization (Google Labs) and carries an explicit disclaimer "This is not an officially supported Google product" and is not eligible for the Google Open Source Software Vulnerability Rewards Program.

The repository was seeded on **Jan 16, 2026** (Initial commit 28cde21 by jedborovik, then "init" + react-components skills by davideast, merged via PR #1 seed-repo by dalmaer). The **design-md** skill (the subject of this research) was born on **Jan 22, 2026** via three commits by davideast: PR #3 "feat: add DESIGN.md skill" (f66e8c5), PR #4 "fix: actually use skills directory" (9972895), and PR #5 "feat: add allowed_tools to DESIGN.md skill" (a697ca5). This Jan 22, 2026 date matches the skills.sh registry "First Seen" field for the design-md skill (note: "First Seen" refers to the design-md skill specifically, not the whole repo, which was seeded 6 days earlier).

The repo evolved from a flat collection of skills into a **plugin-architecture monorepo** in **May 2026**. The pivotal refactor commit is **"refactor: restructure skills into plugin architecture" (5532ce0) on May 10, 2026** by JLXIA (Jilin Xia), which created the three plugins: **stitch-design** (core design workflows), **stitch-build** (code generation / framework integration), and **stitch-utilities** (utility / assistant skills). This was formalized and shipped as the **v1.0 release "Stitch Skills Plugins Release" on May 18, 2026 18:02** (tag v1.0, commit 2c93fbc, GPG-verified), whose release notes explicitly state: (1) new skills added (code-to-design, manage-design-system, extract-static-html, extract-design-md, upload-to-stitch), (2) refactor merging several design-related skills into generate-design, (3) reorg structuring all skills into the three plugins. After the refactor, the design-md skill resides at `plugins/stitch-utilities/skills/design-md`.

There was an earlier **v0.1 release on Mar 4, 2026** (tag v0.1, commit 51d6d5a "Merge PR #22 performance-optimize-regex-validate"). Notably, design-md was nearly killed in **March 2026**: on Mar 11 commit "create a new stitch-design skill, deprecate design-md and enhance-prompt" (01daeeb) marked them deprecated, but on Mar 12 two commits "keep design-md and enhance-prompt skills" (858f7e1, 8b0e31d) reverted the deprecation, and "delete the .agents" (fdd0571) cleaned up - so design-md survived and was later re-homed under stitch-utilities in May.

Current scale (as observed Jun 2026): 6.1k stars, 739 forks, 54 watching, 66 commits, 15 skills with 318.5K total installs on skills.sh. design-md is the most-installed skill at 50.3K installs. Languages: TypeScript 89.6%, Shell 5.2%, Python 5.2%. Security audits on skills.sh: Gen Agent Trust Hub Pass, Socket Pass, Snyk Warn. The skills follow the **Agent Skills open standard** (agentskills.io) and are distributed both as individual skills (`npx skills add`) and as plugins (`codex plugin marketplace add`, `npx plugins add`).

Key maintainers: davideast (David East, Google DevRel - original seed and most early skills), dalmaer (Dion Almaer, Google - merged seed PR and early releases), JLXIA / Jilin Xia (Google - drove the V2 / plugin refactor and v1.0 release), jedborovik (initial commit). Community contributors added shadcn-ui (vinothsubramanian), taste-design (Leonxlnx), react-native (yuvrajangadsingh), Codex plugin support (amourfrei/meeChn), and hex-color fixes (Michacallhome). A "google-labs-jules" bot co-authored a perf commit, indicating Jules AI assistance in the repo.

Relationship to the Stitch cloud product: Stitch is a standalone Google AI design tool (closed cloud service). stitch-skills is an open agent-skill layer that calls Stitch via its MCP server - it does NOT contain Stitch itself and is explicitly not an officially supported Google product. The dependency direction is: coding agent -> stitch-skills (skills/plugins) -> Stitch MCP server -> Stitch cloud (stitch.withgoogle.com). design-md specifically retrieves screen metadata, HTML/CSS source, and design assets from Stitch projects via MCP tools, then synthesizes a "Semantic Design System" DESIGN.md file that serves as the "source of truth" for prompting Stitch to generate new screens matching an existing design language.

---

## TIMELINE

All dates are commit / release timestamps as shown on GitHub (UTC, machine-stamped). PR numbers in parentheses.

### 2026-01-16 - Repository seed (origin)
- 28cde21 "Initial commit" - jedborovik (earliest commit in history)
- cefcc7a "init" - davideast
- 72cc442 "feat: add stitch-to-react-components skill" - davideast
- 581d7d0 "feat: react-components skill" - davideast
- 33b8645 "repo: cleanup" - davideast
- 6061528 "Merge remote-tracking branch 'labs/main' into seed-repo" - davideast
- 6083ef7 Merge PR #1 from seed-repo - dalmaer (Dion Almaer)
- NOTE: repo created under google-labs-code org; no design-md skill yet.

### 2026-01-22 - design-md skill born (matches skills.sh "First Seen")
- f66e8c5 "feat: add DESIGN.md skill (#3)" - davideast
- 9972895 "fix: actually use skills directory (#4)" - davideast
- a697ca5 "feat: add allowed_tools to DESIGN.md skill (#5)" - davideast

### 2026-01-23
- c7886fa "feat: stitch-loop skill (#6)" - davideast

### 2026-01-29
- 00128d4 "feat: enhance prompt skill" - davideast
- dbb6ff3 Merge PR #10 feat/enhance - dalmaer (enhance-prompt skill)

### 2026-02-03
- 3ed28cf "initial remotion stitch skill (#11)" - dalmaer
- 5bc8706 "fix: update CLI command from add-skill to skills for consistency (AI Assisted) (#13)" - vinothsubramanian
- 29152aa "Add shadcn-ui skill including v4 features (Base UI, RTL, Visual Styles) (#14)" - vinothsubramanian + davideast

### 2026-02-11
- 982115d "perf: avoid creating regex inside loop in validate.js" - davideast + google-labs-jules[bot] (Jules AI co-author)

### 2026-02-12
- 2a74df8 "docs: normalize skills install command (#21)" - AsadSumbul
- d1390b5 "chore: Add security.md" - davideast
- e8df144 Merge PR #23 chore/security-md - dalmaer (SECURITY.md added)

### 2026-02-17
- 51d6d5a Merge PR #22 performance-optimize-regex-validate - dalmaer (this commit becomes the v0.1 tag)

### 2026-03-04 - v0.1 release
- Release "v0.1 release" tagged v0.1 at commit 51d6d5a, 04 Mar 21:34 - JLXIA (first tagged release; pre-plugin-architecture)

### 2026-03-05
- aa363ee "Update two skills: 1. change all stitch related files into .stitch, 2. download png as well (#31)" - JLXIA
- d891a26 "update metadata.json content" - JLXIA
- a92f689 Merge PR #33 (jilin) - JLXIA

### 2026-03-11 - design-md deprecation attempt
- 01daeeb "create a new stitch-design skill, deprecate design-md and enhance-prompt" - JLXIA (design-md marked deprecated)

### 2026-03-12 - deprecation reverted, design-md survives
- 8b0e31d "keep design-md and enhance-prompt skills" - JLXIA
- 858f7e1 "keep design-md and enhance-prompt skills" - JLXIA (deprecation walked back)
- fdd0571 "delete the .agents" - JLXIA

### 2026-03-13
- ad0b5cc Merge PR #36 (jilin) - JLXIA

### 2026-03-30
- 6c0cbdb "feat: add taste-design skill (#38)" - Leonxlnx

### 2026-05-06 - Stitch Skills V2 begins
- a1f77d5 "Stitch Skills V2" - JLXIA (start of the V2 / plugin-architecture effort)
- a791083 "update the code-to-design to use DS v2" - JLXIA

### 2026-05-07
- 4871339 "update snapshot.ts" - JLXIA
- f039125 "add prompt examples for each Skill" - JLXIA
- b5a7fde "update README.md for MCP setup" - JLXIA
- e5ddccb "improve the format a bit" - JLXIA
- c961fb7 "increase timeout" - JLXIA
- 2c6dd58 merge stitch-code-skills branch - JLXIA

### 2026-05-08
- d3ea25f "improve snapshot.ts for interactive elements support" - JLXIA

### 2026-05-10 - PLUGIN-ARCHITECTURE REFACTOR (key event)
- 5532ce0 "refactor: restructure skills into plugin architecture" - JLXIA (creates stitch-design, stitch-build, stitch-utilities plugins)
- 8c85e1b "docs: consolidate prompt examples into single table in README" - JLXIA

### 2026-05-12
- adbc294 "update install guide" - JLXIA
- 27e0dc0 "fix some inconsitent issues" - JLXIA
- 681a283 "fix minor issues" - JLXIA
- be8147c "fix a path error" - JLXIA
- ea4e830 "add a tip for SSL Certificate" - JLXIA

### 2026-05-17
- a2d69f7 "support uploading DESIGN.md through script" - JLXIA

### 2026-05-18 - v1.0 release "Stitch Skills Plugins Release"
- 8e34bd0 "fix security risk" - JLXIA
- 3bd73f2 "fix security risk" - JLXIA
- cf11888 "fix security risk further" - JLXIA
- 66f6f5e "fix the last security risk" - JLXIA
- 2c93fbc Merge PR #59 stitch-skills-plugins - JLXIA
- Release "Stitch Skills Plugins Release" tagged v1.0 at 2c93fbc, 18 May 18:02, GPG-verified (B5690EEEBB952194). Latest release. Notes:
  1. Features: new skills (code-to-design, manage-design-system, extract-static-html, extract-design-md, upload-to-stitch)
  2. Refactor: merge several design-related skills to generate-design
  3. Reorg: structure all skills into three plugins (stitch-design, stitch-build, stitch-utilities)

### 2026-05-20 - Codex plugin marketplace support
- cbfb385 "Add Codex plugin metadata" - meeChn
- 4800041 "Remove local Codex marketplace entry" - meeChn
- 4b76f44 "Restore Codex marketplace manifest" - meeChn
- a2ca474 "Update Codex instructions in README to use upstream URL" - meeChn
- 21db6cd Merge PR #60 support-codex-plugins - JLXIA

### 2026-05-25 to 2026-05-26
- 241b53e "fix(react-components): detect hex colors in JSX className" - Michacallhome (May 25)
- 53f15d8 Merge PR #64 fix/validate-hex-detection - JLXIA (May 26)

### 2026-06-02
- c43ae1d "Add React Native skill (#42)" - yuvrajangadsingh + davideast

### 2026-06-04 - post-v1.0 reorg
- 99a7d17 "refactor: move react-native skill into stitch-build plugin" - JLXIA
- 5140d0f "docs: improve README quick-start formatting and add inline comments" - JLXIA
- 922c4f7 "docs: add react-native skill to README table and directory tree" - JLXIA
- 1544aa4 Merge PR #67 refactor - rustinb303

### 2026-06-16 to 2026-06-17 - latest activity
- 22716f9 "feat(stitch-design): add markdown upload support and generated-by provenance tracking" - JLXIA (Jun 16)
- fac7324 Merge PR #71 from jilin - JLXIA (Jun 17, most recent commit observed)

### Observed repo metadata (Jun 2026)
- Stars 6.1k, Forks 739, Watchers 54, Commits 66, Releases 2 (v0.1, v1.0)
- Languages: TypeScript 89.6%, Shell 5.2%, Python 5.2%
- License: Apache-2.0
- 15 skills, 318.5K total installs (skills.sh); design-md 50.3K (most-installed)
- skills.sh security audits: Gen Agent Trust Hub Pass, Socket Pass, Snyk Warn
- skills.sh First Seen (design-md): Jan 22, 2026

---

## CONFIDENCE

**Overall confidence: HIGH** on origin, lineage, and the May 2026 plugin refactor; **MEDIUM-HIGH** on the Stitch-product relationship framing.

High-confidence facts (primary-source, machine-stamped GitHub commit timestamps + release tags + skills.sh registry fields, cross-checked across two independent views of the same repo):
- Repo seed date Jan 16, 2026 (Initial commit 28cde21; PR #1 seed-repo merged by dalmaer). DIRECTLY OBSERVED in commit history.
- design-md skill birth Jan 22, 2026 (PRs #3/#4/#5 by davideast). Matches skills.sh "First Seen: Jan 22, 2026". The Jan 22 "First Seen" is the skill's first registry indexing, NOT the repo creation (repo is 6 days older) - this nuance is confirmed.
- Plugin-architecture refactor dated May 10, 2026, commit 5532ce0 "refactor: restructure skills into plugin architecture" by JLXIA. DIRECTLY OBSERVED.
- v1.0 release "Stitch Skills Plugins Release" dated May 18, 2026 18:02, tag v1.0 at commit 2c93fbc, GPG-verified. Release notes explicitly name the three plugins and the merge-into-generate-design refactor. DIRECTLY OBSERVED on the releases page.
- v0.1 release dated Mar 4, 2026, tag v0.1 at commit 51d6d5a. DIRECTLY OBSERVED.
- The March 2026 design-md deprecation + reversal (01daeeb on Mar 11 -> 858f7e1/8b0e31d on Mar 12) is DIRECTLY OBSERVED in commit messages.
- License Apache-2.0, 6.1k stars / 739 forks / 66 commits / 2 releases, language breakdown, plugin/skill directory structure, README content: all DIRECTLY OBSERVED on the repo page.
- Maintainer identities (davideast = David East, dalmaer = Dion Almaer, JLXIA = Jilin Xia) inferred from GitHub usernames + avatars + authorship pattern; HIGH confidence but not biographically verified beyond the GitHub handles.

Medium-high / inferred (consistent with evidence but not a direct primary statement):
- "Google Labs Code" as the organizational home: the org is literally named google-labs-code and the product is Google Stitch (stitch.withgoogle.com); the "This is not an officially supported Google product" disclaimer confirms it is Google-affiliated but not a formally supported Google product. The relationship framing (closed Stitch cloud <- Stitch MCP server <- open stitch-skills -> coding agents) is inferred from the README ("skills require the Stitch MCP server to be configured and running"), the design-md SKILL.md ("Retrieves ... via MCP Server tools"), and the MCP setup docs URL. Direction of dependency is unambiguous; the only inferred part is characterizing Stitch as a "closed cloud product" (it is a Google web service at stitch.withgoogle.com, not open-sourced here).
- The stitch.withgoogle.com landing page returned only the title "Stitch - Design with AI" (JS-rendered SPA, minimal fetchable text), so product-feature claims about Stitch itself beyond "AI design tool" are NOT independently verified here; they rely on the README/SKILL.md descriptions.

Lower-confidence / not verified in this pass:
- Exact install counts (50.3K etc.) are skills.sh-reported figures as of the fetch and may drift; treated as point-in-time snapshots, not durable.
- Contributor real-world affiliations beyond the GitHub-handle-to-known-Google-names mapping are not verified.
- The GitHub REST API (api.github.com/repos/...) returned HTTP 403 (unauthenticated rate limit), so machine-readable created_at/pushed_at fields were NOT fetched; all dates come from the rendered GitHub UI commit/release timestamps, which are equally authoritative for dating but mean I could not cross-check the API JSON. This does not reduce confidence in the dates themselves.
- Whether v1.0 is truly the "Latest" release: the releases page labels it "Latest" and it is the most recent tag; no release newer than May 18, 2026 was observed (latest commit is Jun 17, 2026 with no subsequent tagged release), so v1.0 remains latest as of Jun 22, 2026. MEDIUM-HIGH (a newer release could exist that was not rendered, but the releases page showed exactly 2 releases total).

Unverified claim from the task brief: the brief states "plugin-architecture refactor May 2026" - CONFIRMED precisely (May 10, 2026 refactor commit, May 18, 2026 v1.0 release formalizing it). The brief states "first seen Jan 22, 2026" - CONFIRMED (matches skills.sh and the design-md PR #3 date). The brief states "Apache-2.0" - CONFIRMED. The brief states "Google Labs Code" - CONFIRMED (org name). The brief's framing of "stitch-skills monorepo (plugins: stitch-design, stitch-build, stitch-utilities)" - CONFIRMED (exact plugin names and structure observed in README repository-structure section).
