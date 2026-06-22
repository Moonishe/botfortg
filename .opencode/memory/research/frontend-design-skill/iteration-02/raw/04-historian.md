# Researcher 4 — Historian
## Anthropic frontend-design skill: evolution, repo, community

Source: https://www.skills.sh/anthropics/skills/frontend-design
Repo: https://github.com/anthropics/skills
Fetched: 2026-06-22

---

# SUMMARY

The `frontend-design` skill is one of ~16 example skills in Anthropic's official
`anthropics/skills` repo (154k stars, 18.1k forks, 269 open issues, 705 open PRs,
41 commits on main). The repo is the canonical public home for Agent Skills
(Apache 2.0 for example skills; docx/pdf/pptx/xlsx are source-available).
Structure: `skills/`, `spec/` (agent-skills spec), `template/`, `.claude-plugin/`
(marketplace.json). Languages: Python 84.4%, HTML 12.4%, Shell 1.9%, JS 1.3%.

The skill itself has had exactly **3 commits** touching its current path
(`skills/frontend-design`) plus the original creation — i.e. a short, high-signal
history. The headline finding: **the SKILL.md was comprehensively rewritten on
Jun 9, 2026 (PR #1293, "v1.6")**, transforming it from a motivational
"be bold, avoid AI slop" prompt into a mature, diagnostic design-process document.
This rewrite was done by an outside Contributor (williamqian12) and merged by an
Anthropic maintainer (rlancemartin) with two approving reviews.

Two notable community initiatives the task flagged are both **still open and
unanswered by maintainers**:
- **#978** (PR, emoji-ban / icon-system) — open since Apr 19, 2026; author pinged
  @chrislloyd on Apr 27 with no reply; needs 1 approving review.
- **#1008** (Issue, DESIGN.md integration per Google Labs `design.md` open spec)
  — open since Apr 22, 2026; no maintainer response.

The Jun 9 rewrite did NOT adopt either proposal; instead it took a different
philosophical direction (restraint + diagnostic self-critique over hard rules
and structured contracts). skills.sh (Vercel-built directory) reports **574.5K
installs**, first seen Jan 19, 2026; security audits all Pass
(Gen Agent Trust Hub, Socket, Snyk).

Complementary skills cluster: `web-design-guidelines` (Vercel, 407K installs —
the *audit* counterpart to frontend-design's *create* role), `canvas-design`
(same repo), plus community skills `ui-ux-pro-max`, `sleek-design-mobile-apps`,
`vercel-composition-patterns`. `impeccable` is NOT an Agent Skill in this
ecosystem — it is a separate npm design-tooling package (npx impeccable) used
in the TelegramHelper project's own config. `extract-design-system` returned no
listing on skills.sh (low confidence; likely a community/community-fork concept
or not indexed).

---

# TIMELINE

## 2025-11-12 — PR #98: Creation (original "AI slop" version)
- Author: **klazuka** (Collaborator); approved by **maheshmurag**; merged same day.
- Added `frontend-design/SKILL.md` (42 lines) + Apache 2.0 LICENSE + marketplace.json entry.
- frontmatter description: *"Create distinctive, production-grade frontend
  interfaces with high design quality. Use this skill when the user asks to build
  web components, pages, or applications. Generates creative, polished code that
  avoids generic AI aesthetics."*
- Content character: **motivational / prescriptive**. "Commit to a BOLD
  aesthetic direction" (brutalist, maximalist, retro-futuristic, luxury, etc.).
  "Frontend Aesthetics Guidelines": Typography (avoid Arial/Inter/Roboto),
  Color & Theme (CSS vars, dominant + sharp accents), Motion, Spatial
  Composition, Backgrounds & Visual Details. "NEVER use generic AI-generated
  aesthetics… NEVER converge on common choices (Space Grotesk)". Closing line:
  *"Claude is capable of extraordinary creative work. Don't hold back."*
- Review note: munir131 commented "Is LLM need motivation speech? :)" (Nov 26) —
  early community signal that the enthusiastic tone was noticed.

## 2025-12-01 — PR #129: Structural reorganization
- Author: **ant-andi**. "Move example skills into dedicated folder and create
  minimal top-level folder structure." 296 files changed.
- `frontend-design/` → `skills/frontend-design/`. marketplace.json paths updated
  (`./frontend-design` → `./skills/frontend-design`). Pure move, no content change.

## 2025-12-04 — PR #134: doc-coauthoring + example-skill updates
- Authors: **klazuka + claude** (co-authored-by Claude noreply@anthropic.com —
  i.e. Claude Code itself co-authored the commit). Touched `skills/frontend-design`
  among other example-skill updates. Minor update to the skill folder.

## 2026-04-19 — Issue #977 + PR #978: Emoji-ban / icon-system proposals
- **#977** (Issue, Juan-Severiano): "Add new rule of use of emojis into
  frontend-design skill" — propose avoiding emojis, prefer icon library
  (Bootstrap/Material/Font Awesome). Open, no maintainer reply.
- **#978** (PR, Juan-Severiano): "ban emojis, enforce icon system". Branch
  `feat/frontend-design/antiemoji-guideline`. Adds an "Icons, not Emojis" bullet:
  never emojis in UI/comments; reuse existing icon lib or add tree-shakeable one
  (Lucide, Heroicons, Font Awesome, Bootstrap Icons, Material Icons, Phosphor);
  treat icons as first-class (sized/colored/spaced). **Copilot AI reviewed**
  (Apr 19). Author pinged **@chrislloyd** on Apr 27 — **no reply**. Still Open,
  requires ≥1 approving review.

## 2026-04-22 — Issue #1008: DESIGN.md integration proposal
- Author: **marcusjezweb**. Proposes the skill optionally consume/produce a
  `DESIGN.md` per **Google Labs `design.md` open spec** (shipped 2026-04-22,
  ~1.6k stars, Apache 2.0, alpha) — YAML frontmatter tokens (colours, type,
  spacing) + prose "why".
- Two-phase model: (1) distil brief into DESIGN.md; (2) every downstream
  generation reads it + verifies via `npx @google/design.md lint` (WCAG contrast).
- Export paths: `--format tailwind` and W3C DTCG tokens.
- Explicitly **"not prescriptive"** — accepts "we evaluated and decided not to
  adopt" as valid; goal is deliberate consideration.
- **Open, no maintainer response.**

## 2026-04-26 → 2026-06-01 — Adjacent community skill proposals (all Open)
- #1036 (Apr 26) "Add: designlang skill" (Manavarya09) — a competing
  design-system-contract approach. Open.
- #1046 "add new skill definition files for frontend-design, ai-experien…"
  (ALMMECHANICAL) — **closed/abandoned Jun 13, 2026**.
- #1087 (May 4) "document-skills plugin loads all skills from repo instead of
  only declared ones" (JiangHe12) — Open.
- #1109 (May 8) "Add saas-motion-ideation skill" (harishwtf) — Open.
- #1146 (May 16) "skill-auto-installer meta-skill" (maimai-dot) — Open.
- #1228 (Jun 1) "Add Vibe UI skill" (Liuwei1125) — Open.

## 2026-06-08/09 — PR #1293: THE REWRITE ("v1.6")
- Author: **williamqian12** (Contributor); branch `williamq/frontend-design-v1.6`.
  PR opened Jun 8, merged Jun 9 by **rlancemartin**; approved by **tobinsouth**
  + rlancemartin.
- frontmatter description changed to: *"Guidance for distinctive, intentional
  visual design when building new UI or reshaping an existing one. Helps with
  aesthetic direction, typography, and making choices that don't read as
  templated defaults."* (less "build components", more "design choices").
- **Content is a near-total rewrite** (~from 42 motivational lines to a long,
  diagnostic design-process doc). New structure:
  1. **"Design lead at a small studio"** framing — "take one real aesthetic risk
     you can justify."
  2. **Ground it in the subject** — pin down subject/audience/page's single job;
     build from the subject's own world/materials/vernacular.
  3. **Design principles** — "the hero is a thesis"; "typography carries the
     personality"; "structure is information" (question numbered 01/02/03 markers
     unless order carries meaning); "leverage motion deliberately"; "match
     complexity to the vision".
  4. **Process: brainstorm, explore, plan, critique, build, critique again** —
     includes the now-famous **calibration of "three AI-generated looks"**:
     (a) warm cream ~#F4F1EA + high-contrast serif + terracotta accent;
     (b) near-black + single acid-green/vermilion accent;
     (c) broadsheet hairline-rules zero-radius dense columns. "All three are
     legitimate for some briefs, but they are defaults rather than choices."
     Two-pass workflow: compact token system (4–6 named hex, 2+ type roles, ASCII
     wireframes, one signature) → review against brief for uniqueness → only then
     code. CSS selector-specificity caution.
  5. **Restraint and self-critique** — "spend your boldness in one place";
     Chanel's "remove one accessory" advice; quality floor without announcing it
     (responsive, keyboard focus, reduced-motion).
  6. **More on writing in design** — write from end-user's side; active voice;
     "Save changes" not "Submit"; errors don't apologize; empty screen is an
     invitation to act.
- The rewrite **did not adopt #978 (emoji ban) or #1008 (DESIGN.md)**. It instead
  addresses the same underlying "AI convergence" problem the original flagged,
  but via **diagnostic specificity** (naming the exact 3 default looks + hex)
  rather than rules/contracts. No emoji rule was added; no structured-contract
  layer was added.
- Community fork signal: **norbert-pap** referenced #1293 in a weekly-sync PR
  (claudebert#8) on Jun 15, 2026 — forks actively track this skill.

## skills.sh metadata (independent directory, Vercel-built)
- frontend-design: **574.5K installs**, repo 153.5K stars, first seen **Jan 19,
  2026**, security audits Pass (Gen Agent Trust Hub, Socket, Snyk).
- NOTE: skills.sh still renders the **OLD** SKILL.md body ("avoid generic 'AI
  slop' aesthetics"…) — i.e. its indexed content predates/lags the Jun 9 #1293
  rewrite. The live GitHub raw file reflects the new version. Caching gap.

---

# COMMUNITY

## Repo health & governance
- anthropics/skills: 154k stars / 18.1k forks / 269 open issues / 705 open PRs.
  Very high inbound contribution, low maintainer throughput on community items
  (issue creation is now restricted). Active Anthropic maintainers seen:
  klazuka (Collaborator, created the skill), maheshmurag (approved #98),
  ant-andi (reorg #129), rlancemartin (merged #1293). @chrislloyd was pinged on
  #978 but did not respond in the fetched thread.
- Claude itself is a co-author on repo commits (PR #134 co-authored-by Claude),
  confirming Anthropic dogfoods Claude Code on this repo.

## Community pressure points (Apr–Jun 2026)
A clear wave of community proposals in April 2026 tried to harden the skill:
1. **Icon discipline** (#977/#978) — ban emojis, enforce icon libraries.
2. **Structured design contracts** (#1008 DESIGN.md; #1036 designlang) — make the
   skill consume/produce a machine-checkable design-system file (WCAG lint,
   token export).
3. **New adjacent skills** (#1109 saas-motion-ideation, #1228 Vibe UI, #1146
   skill-auto-installer, #1046 abandoned).
None of the hardening proposals were merged; the official response was the
**#1293 rewrite**, which went a different direction (softer, diagnostic,
process-oriented). This suggests Anthropic's philosophy for the skill favors
*judgment coaching* over *enforceable rules* — a meaningful signal for anyone
considering whether to submit rule-based PRs.

## Complementary skills (skills.sh "Design & UI" cluster)
- **web-design-guidelines** (vercel-labs/agent-skills): 407K installs, 28.2k
  repo stars. **Audit/lint** role — fetches Vercel's Web Interface Guidelines
  (spacing, typography, interaction, accessibility) and outputs `file:line`
  findings. Natural complement: frontend-design *creates*, web-design-guidelines
  *reviews*. Security: Snyk Warn (one advisory) vs frontend-design's clean Pass.
- **canvas-design** (anthropics/skills, same repo): design generation in
  canvas-based environments. Shares the repo's design lineage and bundled fonts.
- **vercel-composition-patterns** (vercel-labs): React component architecture.
- **ui-ux-pro-max** (nextlevelbuilder): advanced UI/UX interaction patterns.
- **sleek-design-mobile-apps** (sleekdotdesign): mobile-first iOS/Android.

## "impeccable" and "extract-design-system" (task-named complements)
- **impeccable**: NOT an Agent Skill on skills.sh (search returned empty
  directory shell). It is a separate **npm design tooling package**
  (`npx impeccable …`, 23 sub-commands per TelegramHelper's own AGENTS.md:
  teach/polish/critique/bolder/distill/typeset/colorize/layout/animate/detect…).
  Different ecosystem from anthropics/skills — no install-count/audit data on
  skills.sh. High confidence it is outside the skills.sh index.
- **extract-design-system**: NOT found on skills.sh (search returned empty
  shell). Low confidence. Possibly a community concept / fork-only skill / not
  yet indexed. Could not confirm existence as a published Agent Skill from
  skills.sh or the anthropics/skills issue search. Flag for follow-up via
  GitHub code search or npm.

## Fork / propagation signals
- PR #98 was cherry-picked into `btli/skills` (Dec 4, 2025, Claude-Code
  generated) and `Peleke/skills` (Mar 14, 2026). #1293 referenced by
  norbert-pap/claudebert weekly sync (Jun 15, 2026). The skill propagates into
  many personal/company forks.

---

# CONFIDENCE

| Claim | Confidence | Basis |
|---|---|---|
| Repo stats (154k stars, 18.1k forks, 269 issues, 705 PRs, 41 commits) | High | Live GitHub repo page + commit/issue pages fetched 2026-06-22 |
| Creation = PR #98 by klazuka, Nov 12 2025, original 42-line "AI slop" SKILL.md | High | PR #98 page + PR #98 files diff (exact original body captured) |
| Reorg = PR #129 by ant-andi, Dec 1 2025, pure move to skills/ | High | Commit ef74077 page + marketplace.json diff |
| PR #134 (Dec 4 2025) touched the skill, co-authored by Claude | High | commit history for skills/frontend-design lists it; PR #98 thread shows co-author pattern repo-wide |
| **Rewrite = PR #1293 by williamqian12, merged Jun 9 2026** | High | PR #1293 page (merged by rlancemartin, 2 approvals) + current raw SKILL.md matches new description/content + commit history shows 2235be7 |
| New SKILL.md content ("design lead at a small studio", three AI looks #F4F1EA, two-pass process, writing guidance) | High | Fetched raw `skills/frontend-design/SKILL.md` from main (full body) |
| #978 emoji-ban PR open, unanswered, pings @chrislloyd | High | PR #978 page (Open, comment Apr 27, no reply) |
| #1008 DESIGN.md issue open, unanswered | High | Issue #1008 page (Open, no comments) |
| skills.sh shows 574.5K installs, first seen Jan 19 2026, audits Pass | High | skills.sh frontend-design page |
| skills.sh renders STALE (old) SKILL.md body vs live GitHub | Medium-High | skills.sh body text matches PR #98 original; live raw = new. Likely indexing lag; not 100% confirmed whether skills.sh will refresh |
| web-design-guidelines = 407K installs, Vercel, audit-role complement | High | skills.sh web-design-guidelines page |
| impeccable is NOT a skills.sh Agent Skill (separate npm pkg) | High | skills.sh search empty; referenced in TelegramHelper AGENTS.md as npx tooling |
| extract-design-system existence/identity | Low | skills.sh search empty; could not confirm. Follow-up needed (GitHub/npm code search) |
| Anthropic philosophy = judgment-coaching over enforceable rules | Medium | Inference from #1293 direction vs rejected #978/#1008 — well-supported but interpretive |
| Full list of adjacent community PRs/issues (#1036,#1046,#1087,#1109,#1146,#1228) | High | GitHub issues search `q=frontend-design` result list |

Overall: **High confidence** on the core evolution narrative (3-commit history +
Jun 9 rewrite) and on the two flagged proposals (#978, #1008) being open/unanswered.
**Medium** on the skills.sh staleness interpretation. **Low** only on
extract-design-system identity. No fabricated data; all dates/numbers/quotes
sourced from fetched pages.
