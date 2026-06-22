# Bird's Eye — frontend-design skill (anthropics/skills)

## Overview
rontend-design is an Agent Skill published by Anthropic in the public nthropics/skills repository. Its purpose is to guide an AI agent toward distinctive, production-grade frontend interfaces that deliberately avoid the homogenized look of generic AI-generated design.

## Sources and tools used
- webfetch of https://www.skills.sh/anthropics/skills/frontend-design — skill page, summary, metrics.
- webfetch of https://www.skills.sh/anthropics/skills — full skill set ranking within the repo.
- webfetch of https://www.skills.sh/topic/design — Design & UI category context and related skills.
- webfetch of https://github.com/anthropics/skills — repository metadata and skill format description.

## Metrics (as of fetch, 2026-06-22)
- Installs: 572.9K–573.0K on skills.sh (top skill in the nthropics/skills repo).
- GitHub stars: 153.2K for the whole nthropics/skills repository.
- First seen on skills.sh: Jan 19, 2026.
- Total installs for the nthropics/skills repo: 2.1M.
- Security audits: Agent Trust Hub Pass, Socket Pass, Snyk Pass.

## Position in the skills ecosystem
- Category: Design & UI on skills.sh.
- Installation command: 
px skills add https://github.com/anthropics/skills --skill frontend-design.
- Related skills in the same category:
  - web-design-guidelines (vercel-labs/agent-skills) — prescriptive Vercel standards.
  - ercel-composition-patterns — React composition patterns.
  - ui-ux-pro-max — advanced UI/UX patterns.
  - canvas-design (anthropics/skills) — canvas-based design generation.
  - impeccable family (polish, critique, older, delight, distill, quieter) — stylistic operators.
  - extract-design-system — reads existing codebases and extracts tokens/components.
  - design-taste-frontend, high-end-visual-design, emil-design-eng — taste-oriented skills.
- The skills.sh Design & UI page explicitly recommends rontend-design as the starting point for new projects, alongside web-design-guidelines.

## What the skill does at a high level
- Triggers when the user asks for a frontend component, page, application, or interface.
- Forces a BOLD aesthetic direction before any code is written.
- Produces working code in HTML/CSS/JS, React, Vue, etc.
- Treats typography, color theming with CSS variables, motion, spatial composition, and textural details as core design pillars.
- Explicitly avoids overused fonts, cliched color schemes, and predictable layouts.

## File layout in the repository
- skills/frontend-design/SKILL.md — the entire skill (only file besides LICENSE.txt).
- No bundled code, examples, or reference files.
- The skill is therefore a pure prompt/instruction skill, not a script-based skill.

## Key takeaways
- This is the most popular design skill from Anthropic's official repo.
- It is intentionally opinionated and aesthetic rather than neutral or prescriptive.
- It competes/complements with impeccable, web-design-guidelines, and extract-design-system.
- The skill is small and portable, but leaves concrete implementation entirely to the model.
