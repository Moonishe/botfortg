# 01 — Bird's Eye View: andrej-karpathy-skills

## Repository Snapshot
- **Current canonical repo:** https://github.com/multica-ai/andrej-karpathy-skills
- **Original repo:** https://github.com/forrestchang/andrej-karpathy-skills
- **Purpose:** A single-file behavioral prompt (CLAUDE.md) that encodes Andrej Karpathy's observed LLM coding pitfalls into four actionable principles.
- **Stack:** Pure Markdown (no runtime code). Distribution is via copy-paste, Claude Code plugin, and Cursor rule.
- **License:** MIT (stated in README.md and SKILL.md, but no LICENSE file exists in either the multica-ai or orrestchang fork).

## Metrics (fetched from GitHub UI, 2026-06-22)
- **Stars:** 180k
- **Forks:** 18.4k
- **Watchers:** 1k
- **Commits on main:** 28
- **Open PRs:** 93
- **Closed PRs:** 40
- **Labels:** 9
- **Releases:** 0
- **Contributors:** small core (forrestchang, back1ply, HOLYKEYZ, szkocot, TomBener, herobrine19, azakharko)

## File Layout
`
.claude-plugin/
  marketplace.json
  plugin.json
.cursor/
  rules/
    karpathy-guidelines.mdc
skills/karpathy-guidelines/
  SKILL.md
CLAUDE.md
CURSOR.md
EXAMPLES.md
README.md
README.zh.md
`

## Content Distribution
| File | Role | Audience |
|------|------|----------|
| CLAUDE.md | Core behavior prompt | Any LLM coding assistant / per-project drop-in |
| README.md | User-facing landing page with install instructions | Developers evaluating the idea |
| EXAMPLES.md | Annotated anti-patterns and fixes | Developers who learn by example |
| CURSOR.md | Cursor-specific setup instructions | Cursor IDE users |
| .cursor/rules/karpathy-guidelines.mdc | Cursor project rule (lwaysApply: true) | Cursor IDE users |
| .claude-plugin/{marketplace.json,plugin.json} | Claude Code plugin manifest | Claude Code plugin marketplace |
| skills/karpathy-guidelines/SKILL.md | Reusable skill for Claude Code / skills.sh | Skill users |
| README.zh.md | Chinese translation | Chinese-speaking users |

## The Four Principles at a Glance
1. **Think Before Coding** — surface assumptions, present interpretations, push back, stop when confused.
2. **Simplicity First** — minimum code, no speculative features, no single-use abstractions.
3. **Surgical Changes** — touch only what the request demands, clean up only your own orphans.
4. **Goal-Driven Execution** — turn vague instructions into testable success criteria with verification loops.

## Tools Used
- webfetch of raw README.md, CLAUDE.md, CURSOR.md, EXAMPLES.md, README.zh.md, SKILL.md, .cursor/rules/karpathy-guidelines.mdc, GitHub repo landing page, and commit history.
- glob of local TelegramHelper-main markdown files to map overlap with the project's existing rules.

## Key Observation
The repository is a **prompt engineering artifact**, not a code library. Its engineering is in the distribution layer (Claude plugin, Cursor rule, skill file) rather than in runtime behavior. The 180k star count signals that the community is voting for *constraint* (what not to do) over *capability* (what to do).

