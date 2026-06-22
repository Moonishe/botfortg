# 04 — Historian: evolution and context of the design-md skill

## Perspective
Timeline of the skill, its repo, and the design-md-adjacent issues that shaped it.

## Tools used
- `webfetch` on https://github.com/google-labs-code/stitch-skills/commits/main/plugins/stitch-utilities/skills/design-md (commit history)
- `webfetch` on https://github.com/google-labs-code/stitch-skills/releases/tag/v1.0 (release notes)
- `webfetch` on https://github.com/google-labs-code/stitch-skills/issues?q=is%3Aissue+design-md (issue list mentioning design-md)
- `webfetch` on https://www.skills.sh/google-labs-code/stitch-skills/design-md (first-seen date, install metrics)
- `grep` on downloaded files for `version`, `release`, `plugin`, `refactor`

## Timeline
| Date | Event | Evidence |
|------|-------|----------|
| Jan 22, 2026 | `design-md` first seen on skills.sh | skills.sh page |
| Feb 3, 2026 | Issue #12 closed: docs updated to use `npx skills add` instead of deprecated `npx add-skill` | GitHub issues |
| Feb 18, 2026 | Issue #24 opened: "Stitch Build Loop skill" (still open) | GitHub issues |
| Mar 2026 | Issue #40: feature request to update skills for March 2026 Stitch redesign — DESIGN.md, Voice Canvas, 4-mode AI, Direct Edits | GitHub issues |
| May 10, 2026 | Commit `5532ce0` — "refactor: restructure skills into plugin architecture" | GitHub commits page |
| May 18, 2026 | Release v1.0 — "Stitch Skills Plugins Release" | GitHub releases page |
| Jun 2, 2026 | Issues #37 and #40 closed after the refactor | GitHub issues |
| Jun 4, 2026 | Issue #66 closed: `npx skills` broken after v1.0 | GitHub issues |

## What the history tells us
1. **Young, fast-moving project.** The skill is only ~5 months old in the public registry. The repo has 66 commits total; `design-md` is a small leaf in the `stitch-utilities` plugin.
2. **Driven by Stitch redesigns.** Issue #40 explicitly tied DESIGN.md updates to the March 2026 Stitch redesign, showing the skill is sensitive to upstream UI changes.
3. **Plugin architecture is recent.** The refactor on May 10, 2026 restructured all skills into three plugins (`stitch-design`, `stitch-build`, `stitch-utilities`). `design-md` landed in `stitch-utilities` as a non-core utility.
4. **Install count is high but not a quality signal.** 50.2K installs likely reflect the popularity of the overall `stitch-skills` repo and Google Stitch, not necessarily this single skill.
5. **No code changes recorded in the skill's own history.** The commit history page shows only one visible commit for the `design-md` path (`refactor: restructure skills into plugin architecture`). The skill is likely a prompt-only artifact.

## Bottom line
`design-md` is a recent, prompt-only utility skill that emerged alongside the broader Stitch skills ecosystem. Its history is short and tied to Google Stitch's product redesigns, not a long-evolved design-engineering tool.

## Sources
- https://github.com/google-labs-code/stitch-skills/commits/main/plugins/stitch-utilities/skills/design-md
- https://github.com/google-labs-code/stitch-skills/releases/tag/v1.0
- https://github.com/google-labs-code/stitch-skills/issues?q=is%3Aissue+design-md
- https://www.skills.sh/google-labs-code/stitch-skills/design-md
- Downloaded skill files in `%LOCALAPPDATA%\Temp\opencode\stitch-design-md-research`
