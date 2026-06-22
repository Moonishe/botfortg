# Practitioner (Researcher 5) — frontend-design skill, iteration-02

> Role focus: installation, best use cases, prompt pattern, pairing, adaptation
> for projects with existing constraints. Builds on iteration-01; this pass uses
> the CURRENT canonical SKILL.md (which is fuller than iteration-01 captured).

## Sources fetched (this iteration)
- https://www.skills.sh/anthropics/skills/frontend-design — install command, summary, stats.
- https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md — exact, current instructions (the authoritative body).
- https://github.com/anthropics/skills (repo README) — Claude Code plugin-marketplace path, skill spec links.
- https://www.skills.sh/vercel-labs/agent-skills/web-design-guidelines — primary pairing skill (audit/correctness).
- https://www.skills.sh/docs and /docs/cli — `skills` CLI mechanics + telemetry opt-out.
- Read iteration-01/synthesis.md + iteration-01/raw/05-practitioner.md for baseline.

## What changed in the canonical SKILL.md since iteration-01
iteration-01 paraphrased the skill; the current body is more concrete and worth
recording for practitioners:
- Persona is sharper: "design lead at a small studio known for giving every
  client a visual identity that could not be mistaken for anyone else's ...
  take one real aesthetic risk you can justify."
- Explicit two-pass process named: "brainstorm, explore, plan, critique, build,
  critique again."
- The "compact token system" pass now spells out four deliverables: Color
  (4-6 named hex), Type (2+ roles: restrained characterful display +
  complementary body + optional utility/data face), Layout (one-sentence prose
  + ASCII wireframes to ideate AND compare), Signature (the one memorable
  element).
- New "Restraint and self-critique" section: "Spend your boldness in one place."
  Chanel rule quoted ("before leaving the house, take a look in the mirror and
  remove one accessory"). Quality floor is non-negotiable but unannounced:
  responsive to mobile, visible keyboard focus, reduced-motion respected.
- New "More on writing in design" section: copy IS design material. Active
  voice default. Action names stay stable across the flow ("Publish" -> toast
  "Published"). Errors explain + direct, never apologize, never vague. Empty
  states are invitations to act.
- The anti-default calibration is now nuanced, not a blanket ban: the three
  AI-clique looks (warm cream + serif + terracotta; near-black + acid accent;
  broadsheet hairline columns) are "legitimate for some briefs" — "Where the
  brief pins down a visual direction, follow it exactly ... Where it leaves an
  axis free, don't spend that freedom on one of these defaults."
- CSS-specificity warning added: "easy to generate CSS classes that cancel each
  other out (especially with a type-based selector like .section and an
  element-based selector like .cta)" — watch padding/margin between sections.
- Iteration guidance: "Try to do a lot of this planning and iteration in your
  thinking, and only show ideas to the user when you have higher confidence."

## How to install (practitioner-accurate)
Primary (skills.sh CLI, no global install needed):
    npx skills add https://github.com/anthropics/skills --skill frontend-design
Alt (Claude Code plugin marketplace, registers the whole repo):
    /plugin marketplace add anthropics/skills
    /plugin install example-skills@anthropic-agent-skills
Telemetry: CLI sends anonymous skill-name + timestamp. Opt out with
    DISABLE_TELEMETRY=1
Availability: Claude.ai (paid, built in), Claude Code (plugin), Claude API
(Skills API). The skill is pure instructions (SKILL.md + LICENSE.txt only, no
scripts/assets), so there is nothing to compile and no runtime dependency.

## Best use cases (where it earns its keep)
- New frontend page/landing with a desired distinctive identity.
- Redesign / re-skin of an existing page where "templated" is the complaint.
- Single memorable component that needs a signature moment (hero, pricing,
  interactive demo).
- Generating a first design-plan + token system BEFORE code, to align with a
  stakeholder.
- Situations where copy/microcopy quality matters as much as visuals (the
  skill has real writing-in-design guidance).
Weak fit / skip:
- Strict design-system work where tokens are already fixed (use
  extract-design-system / your system instead; the skill deliberately takes
  aesthetic freedom).
- Backend-only projects with no web UI (it simply will not trigger).
- Pure accessibility/compliance audits (web-design-guidelines does that
  better).
- Performance/critical-path optimization (out of scope).

## Recommended prompt pattern (brief + audience + constraints + framework -> plan -> code)
1. State the subject + audience + the page's single job.
2. State constraints: framework, performance budget, accessibility needs.
3. Optionally pin an aesthetic direction OR explicitly leave it open.
4. Ask for the DESIGN PLAN FIRST: color tokens, type pairing, layout concept,
   signature element. Do NOT ask for code yet.
5. Review the plan; reject generic defaults; approve or redirect.
6. Then ask for production-grade code following the approved plan exactly.

Concrete prompt templates:

A) Open-direction landing page:
> "Using frontend-design: landing page for a vintage synth marketplace.
> Audience: electronic musicians and studio owners. Single job: get them to
> list a synth for sale. Framework: React + Tailwind. Accessibility: WCAG AA,
> reduced-motion respected. Propose a compact design plan first (4-6 named hex
> colors, display+body type pairing, one-sentence layout + ASCII wireframe, one
> signature element). Do not write code until I approve the plan."

B) Redesign with fixed brand constraints:
> "Redesign this dashboard header [paste current]. Brand constraints: must keep
> our existing accent #C2410C and Inter for body. Take ONE aesthetic risk on
> the hero treatment. Plan first, then code as a single React component. Keep
> keyboard focus and reduced-motion."

C) Reshaping existing UI (adaptation path):
> "Reshape our admin settings page. Keep all current functionality and our
> CSS-variable token names (--color-bg, --color-text, --accent). Improve
> hierarchy and add one signature moment. Plan first; show before/after ASCII
> wireframes; then patch the existing files."

## Pairing strategy (frontend-design + web-design-guidelines + impeccable/critique)
The skill itself is taste + process. It has NO correctness enforcement, NO
contrast checks, NO lint. Pair it:

Workflow (build -> audit -> polish):
1. frontend-design -> produce plan + code (taste + distinctiveness).
2. web-design-guidelines -> audit the output files in terse file:line format
   against Vercel's Web Interface Guidelines (spacing, type, interaction,
   accessibility). This is the correctness backstop frontend-design lacks.
3. impeccable/critique (or /impeccable polish) -> structured UX/design review
   on the rendered result; /impeccable bolder or /impeccable delight to push a
   timid design; /impeccable distill to strip over-engineering.
4. External objective checks the skill cannot do itself: axe-core or Lighthouse
   for WCAG/contrast, since frontend-design's accessibility bar is
   "non-negotiable but unannounced" and not mechanically verified.

Pairing notes:
- frontend-design owns AESTHETIC direction + copy voice.
- web-design-guidelines owns COMPLIANCE (it fetches fresh rules each run).
- impeccable owns ITERATIVE refinement (critique -> polish -> bolder/distill).
- vercel-composition-patterns owns React component ARCHITECTURE if the output
  needs to scale beyond one file.
Order matters: do correctness audit (2) before polish (3), otherwise polish
locks in violations.

## Adaptation for projects with existing constraints (TelegramHelper case)
TelegramHelper is backend-first (Python 3.13, aiogram 3.16, SQLAlchemy 2.0
async, SQLite, Qdrant). No web UI in scope today. Practitioner guidance:
- Do NOT install the upstream skill as a permanent project dependency. It only
  triggers on frontend asks, so it is harmless, but it adds noise and bypasses
  the project's constitution (pydantic-settings, no raw SQL, async/await,
  Alembic-only migrations, no hardcoded secrets).
- IF a web UI is added (admin dashboard, landing page, Telegram Mini App):
  create a LOCAL adaptation at .opencode/skills/frontend-design/SKILL.md that:
    * keeps the skill's core (bold direction, token system, two-pass plan,
      signature element, restraint, writing-in-design).
    * injects project constraints: config via pydantic-settings, no hardcoded
      secrets, async I/O, ruff lint (S/PT/TCH/PIE), type annotations strict.
    * fixes the framework choice (decide React vs plain HTML once; do not let
      the model re-decide each run).
    * mandates pairing with web-design-guidelines + a local impeccable/critique
      pass + Lighthouse/axe before merge (fold into the existing D5/R5
  pipeline).
    * replaces the unstructured "compact token system" with a DESIGN.md or
      tokens file that is checked in, so design decisions persist across
  sessions (iteration-01 gap #4).
- For one-off marketing/landing tasks: install upstream temporarily for that
  task, then remove. Cheaper than maintaining a fork.
- The skill's "take one real aesthetic risk" and Chanel "remove one accessory"
  rules pair naturally with the project's Ponytail "deletion over addition,
  boring over clever" — they are compatible philosophies, not conflicting.

## Practitioner risks / watch-outs
- "BOLD/distinctive" is not mechanically enforceable; the model can still emit
  a cliche. Mitigate by demanding the plan-first step and rejecting defaults.
- Accessibility vs maximalism tension is real; the skill asks for both but
  only enforces by instruction. Always run axe/Lighthouse.
- No examples in the skill dir (SKILL.md + LICENSE.txt only). Quality depends
  entirely on the model + your prompt. The plan-first pattern is the lever.
- CSS specificity footgun is called out by the skill itself; review paddings/
  margins between sections after generation.
- Copy quality: the skill gives good writing guidance but no validation; have a
  human or a critique pass review microcopy.

## SUMMARY
frontend-design is Anthropic's most-installed design skill (~574K installs,
154K-star repo): a pure-instruction skill that makes the agent act as a boutique
studio design lead, forcing a deliberate aesthetic direction and a plan-first
two-pass process (brainstorm plan -> self-critique vs generic defaults -> build
-> critique again) before any code. The current SKILL.md is richer than
iteration-01 recorded: it adds explicit token-system deliverables (4-6 hex,
2+ type roles, ASCII wireframes, one signature), a restraint/self-critique
section (Chanel rule, quiet-around-the-signature, non-negotiable a11y floor),
and real writing-in-design guidance (active voice, stable action names,
directing error/empty states). It is taste+process only — no correctness
enforcement — so practitioners MUST pair it with web-design-guidelines
(audit), impeccable/critique (refinement), and axe/Lighthouse (objective a11y).
For TelegramHelper (backend-first, no web UI), do not install upstream
permanently; adapt the principles into a local skill IF a web UI is added,
injecting project constraints and mandating the audit/polish pairing.

## USAGE_PATTERNS
- Install: `npx skills add https://github.com/anthropics/skills --skill
  frontend-design` (or Claude Code plugin marketplace). DISABLE_TELEMETRY=1 to
  opt out.
- Trigger: any frontend component/page/app/redesign request; auto-loads.
- Prompt pattern: subject+audience+single-job, constraints (framework, perf,
  a11y), optional direction pin, then "design plan FIRST, no code yet" ->
  approve -> "now code following the plan exactly".
- Plan deliverables: Color (4-6 named hex), Type (display+body+optional
  utility), Layout (prose + ASCII wireframes to compare), Signature (one
  memorable element). Self-critique against three known AI-clique defaults.
- Pairing order: frontend-design (build) -> web-design-guidelines (audit
  file:line) -> impeccable/critique+polish (refine) -> axe/Lighthouse
  (objective a11y). Audit BEFORE polish.
- Adaptation for constrained projects: local SKILL.md fork that injects
  project constitution + fixed framework + checked-in tokens/DESIGN.md +
  mandatory D5/R5 + audit pairing; install upstream only temporarily for
  one-off tasks.

## RECOMMENDATIONS
1. Adopt the plan-first prompt pattern as the default way to invoke this skill;
   never accept code without an approved token-system plan.
2. Always pair: frontend-design alone has no correctness enforcement. Minimum
   viable pairing = web-design-guidelines (audit) + axe-core/Lighthouse (a11y).
   Add impeccable/critique for refinement, vercel-composition-patterns for
   multi-file React.
3. Run the audit pass BEFORE polish, so refinement does not lock in violations.
4. For TelegramHelper: do NOT install upstream permanently (no web UI in
   scope). If a web UI is added, create a LOCAL
   .opencode/skills/frontend-design/SKILL.md that injects the project
   constitution (pydantic-settings, no raw SQL, async, ruff, strict types),
   fixes the framework once, checks in a tokens/DESIGN.md file, and mandates
   the audit+polish pairing inside the existing D5/R5 pipeline.
5. Reconcile the skill's "one aesthetic risk" with Ponytail's "deletion over
   addition": take the risk on the signature element only, keep the rest
   disciplined — the two philosophies compose.
6. Close iteration-01 gaps with this iteration: the current SKILL.md body is
   now captured (token deliverables, restraint section, writing section,
  CSS-specificity warning, nuanced anti-default stance).

## CONFIDENCE
- high: skill purpose, install commands, trigger conditions, canonical
  SKILL.md body, CLI/telemetry mechanics, web-design-guidelines pairing
  behavior (all fetched from primary sources this iteration).
- high: prompt pattern and plan deliverables (taken verbatim from the current
  SKILL.md process section).
- medium: exact output quality in practice (no examples in-repo; depends on
  model + prompt; no benchmark).
- medium: long-term roadmap (open PRs/issues from iteration-01 unresolved;
  cannot predict merges).
- medium: TelegramHelper adaptation specifics (sound for the documented
  constitution, but hypothetical until a web UI is actually scoped).
