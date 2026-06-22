# 01 — Bird's Eye: design-md skill overview

## Perspective
High-level, ecosystem-level view of the `design-md` skill from the Google `stitch-skills` repo.

## Tools used
- `webfetch` on https://www.skills.sh/google-labs-code/stitch-skills/design-md (skill page, metrics, SKILL.md preview)
- `webfetch` on https://github.com/google-labs-code/stitch-skills (repo README, plugin layout, stars/forks)
- `webfetch` on https://agentskills.io (Agent Skills open standard, compatible clients)
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/plugin.json (plugin metadata)
- `grep` on downloaded raw files in `%LOCALAPPDATA%\Temp\opencode\stitch-design-md-research` for `DESIGN.md`, `Stitch MCP`, `allowed-tools`

## What it is
`design-md` is a skill that tells a coding agent to act as an **expert Design Systems Lead**. Its single job is to **analyze a Google Stitch project and synthesize a semantic `DESIGN.md` file** that later prompts Stitch to generate new screens in the same visual language.

## Key metadata
| Metric | Value |
|--------|-------|
| Installs | 50.2K |
| GitHub stars | 6.1K |
| Forks | 739 |
| First seen | Jan 22, 2026 |
| Latest release | v1.0 — May 18, 2026 |
| License | Apache-2.0 |
| Maintainer | google-labs-code |
| Skill path | `plugins/stitch-utilities/skills/design-md/` |
| Plugin | `stitch-utilities` (design utilities & assistants) |

## Where it lives in the ecosystem
The `stitch-skills` repo is split into three plugins:
- `stitch-design` — core design workflows (generate, extract, manage, upload)
- `stitch-build` — code generation from Stitch designs (React, React Native, shadcn/ui, Remotion)
- `stitch-utilities` — helpers and assistants, including `design-md`, `enhance-prompt`, `stitch-loop`, `taste-design`

`design-md` is explicitly a **utility**: it consumes existing Stitch screens and emits a design-system document; it does not create new screens or code directly.

## Standard & compatibility
- Follows the **Agent Skills** open standard (`SKILL.md` metadata, `allowed-tools`, `examples/`)
- Compatible clients listed on agentskills.io include OpenCode, Claude Code, Cursor, Gemini CLI, Codex, Antigravity, etc.
- The repo README lists `npx skills add google-labs-code/stitch-skills --skill design-md` and Codex/Claude/Cursor plugin install paths.

## Hard dependency
- **Requires the Stitch MCP Server** (Google Stitch).
- Cannot work offline; it relies on `list_projects`, `list_screens`, `get_screen`, `get_project`, and `web_fetch` to pull screen metadata, HTML/CSS, and screenshots.

## Bottom line
A popular, well-documented utility skill for **Google Stitch users only**. It is not a generic design-system extractor; it is tightly coupled to Stitch's MCP server and its "Visual Descriptions" prompting model.

## Sources
- https://www.skills.sh/google-labs-code/stitch-skills/design-md
- https://github.com/google-labs-code/stitch-skills
- https://agentskills.io
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/plugin.json
- Downloaded skill files in `%LOCALAPPDATA%\Temp\opencode\stitch-design-md-research`
