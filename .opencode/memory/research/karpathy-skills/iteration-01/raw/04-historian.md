# 04 - Historian: Evolution, Community, and Adoption

## 1. Timeline of the Repository

The repository is young but highly viral. The commit history shows the following phases:

### Phase 1: Genesis (January 2026)
- **Jan 27, 2026** — `Add Karpathy-inspired Claude Code guidelines` (8462496). First commit by forrestchang and claude.
- **Jan 27, 2026** — `Add install instructions for new and existing projects` (c488bed).
- **Jan 27, 2026** — `Add skills version for .claude/skills/ directory` (0b53cbc).
- **Jan 28, 2026** — `Update Karpathy tweet link in README` (bf5837f) via PR #1 from TomBener.

### Phase 2: Expansion (January 2026)
- **Jan 28, 2026** — `refactor: restructure repo for skills.sh compatibility` (64723a4) by szkocot.
- **Jan 29, 2026** — `Add examples of coding principles and common mistakes` (4f6e050) by HOLYKEYZ. This is when `EXAMPLES.md` was added.
- **Jan 30, 2026** — `Add Claude Code plugin support` (82467fa) by back1ply and claude.
- Multiple plugin-structure fixes by back1ply and claude.

### Phase 3: Stabilization and Distribution (February-April 2026)
- **Feb 16, 2026** — `Merge pull request #18 from back1ply/fix/plugin-skill-path` (aa4467f).
- **Apr 13, 2026** — `Add Multica project link at the top of README` (fb8fdb0) by forrestchang.
- **Apr 18, 2026** — `add cursor support` (fb7a22c) by azakharko. This added `.cursor/rules/karpathy-guidelines.mdc` and `CURSOR.md`.
- **Apr 20, 2026** — `Sync Chinese README with English version (add Cursor section)` (2c60614) by herobrine19.

### Phase 4: Current State (June 2026)
- **28 commits** on `main`.
- **93 open PRs**, **40 closed PRs**.
- PRs are mostly translations (Hindi, etc.), minor fixes, and additions (e.g., planning and approval process).
- Canonical repo moved to `multica-ai` organization.

## 2. Community Signals
- **180k stars** in ~5 months is exceptional for a markdown-only repository.
- **18.4k forks** suggests widespread copying and adaptation.
- **1k watchers** indicates ongoing interest.
- **No releases** — the project is not versioned as a library.
- **9 labels** — likely issue/PR triage labels.

## 3. Fork History
The original repo was `forrestchang/andrej-karpathy-skills`. The current canonical location is `multica-ai/andrej-karpathy-skills`. The README still points to the old URLs in install instructions:
```
/plugin marketplace add forrestchang/andrej-karpathy-skills
curl -o CLAUDE.md https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md
```
This is a minor inconsistency but not a functional issue.

## 4. Relation to Andrej Karpathy's Tweet
The README cites a tweet from Karpathy (https://x.com/karpathy/status/2015883857489522876). The content of the tweet is paraphrased in the README:
- Models make wrong assumptions and run with them.
- Models overcomplicate code and APIs.
- Models change/remove code they do not understand.

The four principles are a direct response to these three failure modes:
- Wrong assumptions -> Think Before Coding.
- Overcomplication -> Simplicity First.
- Orthogonal changes -> Surgical Changes.
- General trust issue -> Goal-Driven Execution.

## 5. The Multica Connection
The README prominently promotes **Multica** (https://github.com/multica-ai/multica), described as an open-source platform for running and managing coding agents with reusable skills. The repository was moved to the `multica-ai` organization, suggesting the author is building a commercial/open ecosystem around agent skills. The repo itself is a high-quality example of a reusable skill.

## 6. Tools Used
- webfetch of the GitHub commits page, PR page, and README.md.
- webfetch of the original repo landing page and current repo landing page.
- glob of the local TelegramHelper project to understand how skills are stored (`.opencode/skills/`, `skills/`).

## 7. Historical Pattern
This is a classic **viral prompt engineering repo**: a single, well-structured behavioral prompt gets distributed through multiple channels (plugin, skill, rule, markdown). The rapid star growth reflects frustration with LLM coding behavior and a desire for a standardized antidote.
