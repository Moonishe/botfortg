# Practitioner — how to use the skill and what it produces

## Sources and tools used
- webfetch of https://www.skills.sh/anthropics/skills/frontend-design — installation command and summary.
- webfetch of https://support.claude.com/en/articles/12512198-creating-custom-skills — Claude skill mechanics and best practices.
- webfetch of https://www.skills.sh/docs — CLI usage and ranking.
- webfetch of https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md — exact workflow steps.
- glob of .opencode/skills/**/* — checked local skill format in this project.
- ead of .opencode/skills/deepresearch/SKILL.md — example of how this project stores skills locally.

## How to install
`ash
npx skills add https://github.com/anthropics/skills --skill frontend-design
`
In Claude Code you can also register the repo as a marketplace:
`ash
/plugin marketplace add anthropics/skills
`

## How to invoke
- The skill is triggered automatically when the user asks for a frontend component, page, application, or interface.
- Mentioning "frontend-design" or "use the frontend-design skill" can explicitly load it.
- It works in Claude.ai, Claude Code, and the Claude API (where skills are supported).

## Recommended prompt pattern
1. Provide the brief: component, page, app, or interface.
2. Include purpose, audience, technical constraints (framework, performance, accessibility).
3. Optionally request a specific aesthetic direction.
4. Ask the model to first propose a design plan (token system + signature) before writing code.

Example:
> "Using the frontend-design skill, design a landing page for a vintage synthesizer marketplace. Audience: electronic musicians. Framework: React + Tailwind. Start with a compact design plan, then build a single-page component."

## Expected workflow
1. **Ground in subject** — model names subject, audience, single job.
2. **Design plan** — model proposes color palette (4–6 hex), type pairing, layout (ASCII), and signature element.
3. **Self-critique** — model checks if the plan is generic and revises.
4. **Build** — model writes production-grade code in the chosen framework.
5. **Final critique** — model reviews against accessibility, responsiveness, and reduced-motion requirements.

## Code output
- The skill does not prescribe a stack; it says "HTML/CSS/JS, React, Vue, etc."
- Output is typically a single-file component or a small set of files.
- CSS variables are expected for color theming.
- Motion is expected via CSS transitions/animations, GSAP, or framework-specific libraries.

## Complementary skills and tools
- web-design-guidelines — for Vercel-style correctness and accessibility specifics.
- impeccable/polish or impeccable/critique — for final-pass refinement and structured review.
- extract-design-system — if the work must fit into an existing design system.
- ercel-composition-patterns — for React component architecture.
- External linting: 
px @google/design.md lint (if DESIGN.md is adopted), axe-core, Lighthouse, WCAG contrast checker.

## For TelegramHelper specifically
TelegramHelper is a Python/aiogram backend project (Python 3.13, aiogram 3.16, SQLAlchemy, SQLite). It does not have a frontend web UI in the current scope. Therefore:
- **Installing rontend-design directly is probably unnecessary.** The skill will only trigger if the user asks for frontend UI work.
- **Adapting the principles is more valuable.** If TelegramHelper ever adds an admin dashboard, landing page, or Telegram Mini App, the skill's principles (bold direction, token system, deliberate typography/motion) are useful.
- **Recommended action:** If a web UI is added, create a local rontend-design adaptation in .opencode/skills/frontend-design/SKILL.md that includes TelegramHelper-specific constraints (no raw secrets, async/await, pydantic-settings, etc.) and pairs with the project's impeccable skill already present.

## Key takeaways
- The skill is a workflow and taste protocol, not a drop-in component library.
- Best results come from combining it with prescriptive correctness skills and real linting tools.
- For a backend-first project like TelegramHelper, the principles matter more than the skill itself.
