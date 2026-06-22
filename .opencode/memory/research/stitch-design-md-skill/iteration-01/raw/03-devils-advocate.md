# 03 — Devil's Advocate: risks, gaps, and limitations

## Perspective
Skeptical, critical reading of the skill and its real-world viability.

## Tools used
- `webfetch` on https://www.skills.sh/google-labs-code/stitch-skills/design-md (security audit badges, install count)
- `webfetch` on https://github.com/google-labs-code/stitch-skills/issues?q=is%3Aissue+design-md (issues mentioning design-md)
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/SKILL.md (limitations baked into the prompt)
- `webfetch` on https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-design/skills/extract-design-md/SKILL.md (the alternative, source-code-based skill)
- `grep` on downloaded files for `allowed-tools`, `scripts`, `MUST`, `requires`, `Stitch MCP`

## 1. Hard external dependency
The skill cannot function without a live, configured **Stitch MCP server** and at least one designed screen. This makes it unusable for:
- offline codebases
- non-Stitch projects
- users without Google Stitch access
- CI/automated pipelines that lack the MCP server

## 2. No bundled validation
The skill directory contains only:
- `SKILL.md` (instructions)
- `README.md` (usage summary)
- `examples/DESIGN.md` (one sample output)
There is no `scripts/` or validation layer. The agent is expected to self-check the output. A hallucinated hex code or a missed functional role is not caught automatically.

## 3. Manual, error-prone ID extraction
The workflow requires the agent to:
- call `list_projects`
- find the right project by title
- parse the numeric ID from `projects/{id}`
- call `list_screens`
- parse screen IDs
This is brittle; project titles may be ambiguous, and there is no "search by exact name" shortcut in the skill instructions.

## 4. No version lock or schema enforcement
The skill does not specify a required version of the Stitch MCP API or a schema for the returned `designTheme`. If the MCP server changes, the skill may fail silently or produce outdated tokens.

## 5. Vague about asset size limits
The related `manage-design-system` skill warns that `DESIGN.md` under ~5KB can be uploaded directly, but larger files require a script. `design-md` itself does not mention size limits or output truncation.

## 6. Limited to one screen (or "screens")
The prompt asks for a project ID and a screen ID. It does not specify how to aggregate multiple screens, handle variants, or resolve conflicts when screens diverge. A real design system often spans many screens.

## 7. Security / audit note
skills.sh shows:
- Gen Agent Trust Hub: Pass
- Socket: Pass
- Snyk: Warn
A Snyk "Warn" on the repo is a yellow flag for supply-chain risk, though the skill itself is pure Markdown and has no executable code.

## 8. Alternative exists that may be strictly better
The repo also contains `stitch-design/skills/extract-design-md`, which reads frontend **source code** (React, Vue, Tailwind configs, CSS vars) instead of rendered Stitch screens. It is more deterministic and works without a live Stitch project. For codebases, that skill may be preferred.

## 9. Output quality is entirely model-dependent
The skill is a prompt. The actual extraction depends on the LLM's ability to parse HTML/CSS, identify colors, deduplicate near-hex values, and write evocative prose. A weaker model may dump CSS classes verbatim.

## 10. Not officially supported
README explicitly says: "This is not an officially supported Google product."

## Bottom line
`design-md` is convenient for Stitch users, but it is **opinionated, unvalidated, and tightly coupled to a live MCP service**. For production use, wrap it with a verification step (color validation, diff against previous DESIGN.md, human review) and consider `extract-design-md` for source-based projects.

## Sources
- https://www.skills.sh/google-labs-code/stitch-skills/design-md
- https://github.com/google-labs-code/stitch-skills/issues?q=is%3Aissue+design-md
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-utilities/skills/design-md/SKILL.md
- https://raw.githubusercontent.com/google-labs-code/stitch-skills/main/plugins/stitch-design/skills/extract-design-md/SKILL.md
- Downloaded skill files in `%LOCALAPPDATA%\Temp\opencode\stitch-design-md-research`
