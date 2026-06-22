# Synthesis — frontend-design skill (anthropics/skills)

## SUMMARY
rontend-design is the most installed design skill in Anthropic's official nthropics/skills repository (?573K installs, 153.2K repo stars). It is a pure prompt skill that instructs an AI agent to act like a design lead at a boutique studio, forcing a deliberate, BOLD aesthetic direction before writing any frontend code. It produces working HTML/CSS/JS, React, or Vue output and emphasizes typography, color tokens, motion, composition, and texture as the pillars of distinctive design. Its core value is the structured design-thinking protocol; its main weakness is the lack of examples, validation tools, and formal design-token contracts.

## KEY_FINDINGS
1. **Top-of-category popularity.** It is the #1 skill in the nthropics/skills repo and the top skill in the skills.sh Design & UI category.
2. **Pure instruction skill.** The directory contains only SKILL.md and LICENSE.txt; no code examples, scripts, or reference files.
3. **BOLD direction before code.** The model must choose an aesthetic direction (brutalist, maximalist, retro-futuristic, luxury, organic, etc.) and a single memorable signature element before implementation.
4. **Five design pillars.** Typography, color (4–6 named hex values via CSS variables), motion, spatial composition, and texture/structural details.
5. **Two-pass process.** Brainstorm a compact token system, self-critique for generic defaults, then build and critique again.
6. **Explicit anti-"AI slop" stance.** Names three common AI-generated default looks (warm cream + serif, dark + acid accent, broadsheet newspaper) and asks the model to avoid them unless the brief explicitly requests them.
7. **Quality floor without fanfare.** Requires responsive, keyboard focus, and reduced-motion support.
8. **Active community evolution.** Recent PRs/issues include emoji/icon-system enforcement and Google Labs DESIGN.md integration.
9. **Complementary skill ecosystem.** Works best with web-design-guidelines, impeccable (polish/critique/bolder/delight/distill), extract-design-system, and ercel-composition-patterns.
10. **Strong writing-in-design guidance.** Treats copy as design material, demands active voice, consistent vocabulary, and useful error/empty states.

## AESTHETIC_DIRECTIONS
The skill explicitly mentions the following directions as options:
- Brutally minimal
- Maximalist chaos
- Retro-futuristic
- Organic / natural
- Luxury / refined
- Playful / toy-like
- Editorial / magazine
- Brutalist / raw
- Art deco / geometric
- Soft / pastel
- Industrial / utilitarian

The instruction is not to pick any of these blindly, but to choose one direction that is true to the brief and execute it with precision.

## DESIGN_PILLARS
- **Typography:** Pair a characterful display face with a complementary body face; set a clear scale with intentional weights, widths, and spacing.
- **Color:** Define a palette as 4–6 named hex values and use CSS variables for theming.
- **Motion:** Use animation only where it serves the subject (load sequence, scroll reveal, hover, ambient); avoid scattered effects that feel AI-generated.
- **Composition / Spatial:** Layout concept expressed in prose + ASCII wireframes; structural elements (numbering, dividers, labels) must encode real information.
- **Texture / Signature:** One memorable element that embodies the brief; keep everything else quiet and disciplined.

## CODE_OUTPUT
- The skill does not enforce a specific framework; it lists "HTML/CSS/JS, React, Vue, etc."
- Output is production-grade, functional, responsive, and visually cohesive.
- Expected to use CSS variables for color theming.
- Motion may be implemented via CSS, JS, or a framework-specific animation library.

## RISKS
1. **Subjective interpretation.** "BOLD" and "distinctive" are not mechanically enforceable; the model may still produce another cliche.
2. **No examples or reference files.** The skill is entirely abstract; execution quality depends on the model's design knowledge.
3. **Accessibility vs. maximalism tension.** Elaborate animation and visual risk can conflict with reduced-motion, focus visibility, and contrast requirements.
4. **No validation tooling.** No built-in contrast checks, Lighthouse, axe-core, or HTML/CSS linting.
5. **Unresolved emoji/icon stance.** PR #978 proposes banning emojis and enforcing icon systems, but it is still open.
6. **No DESIGN.md contract.** Issue #1008 suggests a structured design-token contract, but it is not implemented.
7. **No framework-specific conventions.** The model must decide architecture, which can lead to inconsistent code.

## USAGE_PATTERNS
- **Installation:** 
px skills add https://github.com/anthropics/skills --skill frontend-design
- **Best for:** New frontend pages, components, or redesigns where a distinctive aesthetic is desired.
- **Prompt pattern:** Give brief + audience + constraints + framework; ask for a design plan first, then code.
- **Pairing:** Combine with web-design-guidelines for correctness, impeccable/critique for review, and extract-design-system for existing codebases.

## RECOMMENDATIONS_FOR_TELEGRAMHELPER
- **Do not add the skill as-is** unless TelegramHelper explicitly starts building a web UI (admin dashboard, landing page, Telegram Mini App).
- **Adapt the principles instead.** If a frontend is added, fold the skill's core ideas into a local .opencode/skills/frontend-design/SKILL.md that respects TelegramHelper's constraints (async/await, pydantic-settings, no raw SQL, no hardcoded secrets, Alembic migrations, etc.).
- **Leverage the existing impeccable skill.** The project already has ponytail, impeccable, and related skills; the design principles of rontend-design can be merged with the project's lazy-senior-dev and zero-risk pipeline.
- **Use for one-off tasks.** If a landing page or marketing page is needed, install the skill temporarily for that task rather than making it a permanent project dependency.
- **Consider DESIGN.md later.** If the project grows a multi-page web UI, evaluate the Google Labs DESIGN.md spec as a shared contract before adopting the skill's unstructured planning format.

## CONFIDENCE
- **high** for the skill's purpose, popularity, and core instructions (we fetched the canonical SKILL.md and the skills.sh page directly).
- **medium** for roadmap and community intent (we see open PRs/issues but cannot predict merge outcomes).
- **low** for exact code-output quality (no examples, no reproducible benchmark, and results depend on the model and prompt).

## GAPS
1. Exact content of the June 2026 update (PR #1293 diff is not publicly detailed beyond the merge event).
2. Whether the emoji/icon PR #978 will be merged and what its final wording will be.
3. Whether Anthropic will adopt the Google Labs DESIGN.md spec.
4. No first-party benchmark or example gallery of what the skill produces.
5. No telemetry on which aesthetic directions users actually choose most often.
6. No clarity on how the skill interacts with the impeccable family in a single session.

## OUTPUT_CONTRACT
- SUMMARY: see above.
- CHANGES: None made to the codebase; this is a research artifact.
- EVIDENCE: Sources include the canonical SKILL.md, skills.sh page, GitHub commit history, open issues/PRs, Claude support docs, and the skills.sh Design & UI topic page.
- RISKS: Research is based on public web content; skill behavior may differ in practice across Claude versions, clients, and API configurations.
- BLOCKERS: None for this research phase.
