# Researcher 1 — Bird's Eye (Overview Perspective)

**Repository:** https://github.com/forrestchang/andrej-karpathy-skills
**Canonical URL (redirected):** https://github.com/multica-ai/andrej-karpathy-skills
**Date:** 2026-06-22
**Files fetched:** README.md, CLAUDE.md, SKILL.md (skills/karpathy-guidelines/), EXAMPLES.md, CURSOR.md, .claude-plugin/plugin.json

---

## 1. Repository Identity & Metrics

| Metric          | Value                              |
|-----------------|------------------------------------|
| Stars           | 180k                               |
| Forks           | 18.4k                              |
| Watchers        | 1k                                 |
| Commits         | 28                                 |
| Open PRs        | 93                                 |
| License         | MIT                                |
| Releases        | None published                     |
| Default branch  | main                               |
| Original owner  | forrestchang (personal)            |
| Current owner   | multica-ai (org — repository moved/renamed) |
| Language        | Markdown only — zero source code  |
| Plugin version  | 1.0.0 (from plugin.json)           |

**Key observation:** The repository URL in the task (`forrestchang/andrej-karpathy-skills`) now redirects to `multica-ai/andrej-karpathy-skills`. The author (forrestchang / @jiayuan_jy on X) moved the repo to the `multica-ai` organization, which is also the org behind "Multica" — an open-source platform for running and managing coding agents with reusable skills (promoted in the README header).

---

## 2. What This Repository Actually Is

**This is NOT a code library.** It contains zero executable source code, zero dependencies, zero tests, and zero build configuration. The entire repository is **pure Markdown** plus two tiny JSON manifest files.

The core artifact is a single `CLAUDE.md` file — **65 lines, 2.3 KB** — that encodes four behavioral principles as a system-prompt supplement for LLM coding assistants (primarily Claude Code, also Cursor).

It is best described as a **risk-reduction framework / behavioral prompt** rather than a software project. Its purpose is to reshape how LLMs behave when writing code, not to provide reusable code itself.

### Complete file inventory

```
.claude-plugin/
    marketplace.json       # Claude Code plugin marketplace manifest
    plugin.json            # Plugin metadata (name, version, skills pointer)
.cursor/rules/
    karpathy-guidelines.mdc  # Cursor project rule (alwaysApply: true)
skills/karpathy-guidelines/
    SKILL.md               # Reusable skill definition (67 lines, 2.46 KB)
CLAUDE.md                  # The core artifact (65 lines, 2.3 KB)
CURSOR.md                  # Cursor integration guide (28 lines, 1.91 KB)
EXAMPLES.md                # Before/after code examples (522 lines, 14.5 KB)
README.md                  # Project documentation (171 lines, 6.05 KB)
README.zh.md               # Chinese translation of README
```

---

## 3. The Problem Being Solved — Karpathy's Critique

The repository is directly derived from [Andrej Karpathy's X/Twitter post](https://x.com/karpathy/status/2015883857489522876) criticizing LLM coding behavior. The README quotes three specific critiques:

### Critique 1 — Silent assumptions
> "The models make wrong assumptions on your behalf and just run along with them without checking. They don't manage their confusion, don't seek clarifications, don't surface inconsistencies, don't present tradeoffs, don't push back when they should."

### Critique 2 — Overcomplication
> "They really like to overcomplicate code and APIs, bloat abstractions, don't clean up dead code... implement a bloated construction over 1000 lines when 100 would do."

### Critique 3 — Collateral damage
> "They still sometimes change/remove comments and code they don't sufficiently understand as side effects, even if orthogonal to the task."

### The positive insight (leveraging LLM strength)
> "LLMs are exceptionally good at looping until they meet specific goals... Don't tell it what to do, give it success criteria and watch it go."

This last quote is the foundation of the fourth principle (Goal-Driven Execution) — it reframes a weakness (vague task execution) into a strength (autonomous goal-seeking loops).

---

## 4. The Four Principles — Detailed

Each principle maps directly to one of Karpathy's critiques (or his positive insight):

### Principle 1: Think Before Coding
**Addresses:** Wrong assumptions, hidden confusion, missing tradeoffs (Critique 1)

Core directive: "Don't assume. Don't hide confusion. Surface tradeoffs."

Concrete rules in CLAUDE.md:
- State assumptions explicitly — if uncertain, ask
- If multiple interpretations exist, present them — don't pick silently
- If a simpler approach exists, say so — push back when warranted
- If something is unclear, stop — name what's confusing and ask

### Principle 2: Simplicity First
**Addresses:** Overcomplication, bloated abstractions (Critique 2)

Core directive: "Minimum code that solves the problem. Nothing speculative."

Concrete rules:
- No features beyond what was asked
- No abstractions for single-use code
- No "flexibility" or "configurability" that wasn't requested
- No error handling for impossible scenarios
- If 200 lines could be 50, rewrite it
- Self-test: "Would a senior engineer say this is overcomplicated?" If yes, simplify

### Principle 3: Surgical Changes
**Addresses:** Orthogonal edits, touching code you shouldn't (Critique 3)

Core directive: "Touch only what you must. Clean up only your own mess."

Concrete rules:
- Don't "improve" adjacent code, comments, or formatting
- Don't refactor things that aren't broken
- Match existing style, even if you'd do it differently
- If you notice unrelated dead code, mention it — don't delete it
- Remove orphans YOUR changes created; don't remove pre-existing dead code
- Self-test: "Every changed line should trace directly to the user's request"

### Principle 4: Goal-Driven Execution
**Addresses:** Leverages Karpathy's positive insight about LLM goal-seeking loops

Core directive: "Define success criteria. Loop until verified."

Transformation patterns:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

Multi-step tasks require a plan with per-step verification:
```
1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]
```

Key claim: "Strong success criteria let you loop independently. Weak criteria ('make it work') require constant clarification."

---

## 5. Distribution — Four Channels

The same four principles are packaged for four different consumption channels:

### Channel A: Claude Code Plugin (recommended per README)
- Install: `/plugin marketplace add forrestchang/andrej-karpathy-skills`
- Then: `/plugin install andrej-karpathy-skills@karpathy-skills`
- Manifest: `.claude-plugin/plugin.json` (name, version 1.0.0, author "forrestchang", skills pointer to `./skills/karpathy-guidelines`)
- Marketplace manifest: `.claude-plugin/marketplace.json`
- Makes the skill available across ALL projects

### Channel B: CLAUDE.md (per-project)
- New project: `curl -o CLAUDE.md https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md`
- Existing project: append via curl
- Claude Code reads CLAUDE.md automatically from project root
- Designed to merge with project-specific instructions

### Channel C: Cursor Project Rule
- File: `.cursor/rules/karpathy-guidelines.mdc` with `alwaysApply: true`
- Committed in-repo so it activates automatically when the folder is opened in Cursor
- For other projects: copy the .mdc file into target project's `.cursor/rules/`
- Documented in CURSOR.md (28 lines)
- Note: Cursor does NOT read `.claude-plugin/` or `CLAUDE.md` by default

### Channel D: Reusable Skill
- File: `skills/karpathy-guidelines/SKILL.md` (67 lines, 2.46 KB)
- Contains YAML frontmatter: name, description, license
- Can be copied/symlinked into `~/.cursor/skills` or used in other skill systems
- The SKILL.md body is essentially identical to CLAUDE.md (same four principles, same text)

### Contributor sync requirement
CURSOR.md explicitly states: when changing the four principles, keep CLAUDE.md, `.cursor/rules/karpathy-guidelines.mdc`, and `skills/karpathy-guidelines/SKILL.md` in sync. This means the same content is triplicated across three files with different wrappers.

---

## 6. EXAMPLES.md — The Teaching Component

EXAMPLES.md (522 lines, 14.5 KB) is the largest file and serves as the practical teaching companion. It contains real-world before/after code examples for each principle:

- **Think Before Coding (2 examples):** Hidden assumptions in "export user data" request; multiple interpretations of "make search faster" (response time vs throughput vs perceived speed)
- **Simplicity First (2 examples):** Strategy pattern overengineered for a single discount calculation (30+ lines → 2 lines); speculative PreferenceManager with caching/validation/notifications nobody asked for
- **Surgical Changes (2 examples):** Drive-by refactoring that adds username validation while fixing an email bug; style drift that changes quotes, adds type hints, reformats while adding logging
- **Goal-Driven Execution (3 examples):** Vague "fix authentication" vs verifiable test-first plan; incremental rate-limiting with per-step verification; test-first bug reproduction for sorting duplicates

### Anti-patterns summary table (from EXAMPLES.md)

| Principle           | Anti-Pattern                                    | Fix                                              |
|---------------------|-------------------------------------------------|--------------------------------------------------|
| Think Before Coding | Silently assumes file format, fields, scope     | List assumptions explicitly, ask for clarification |
| Simplicity First    | Strategy pattern for single discount calculation | One function until complexity is actually needed |
| Surgical Changes    | Reformats quotes, adds type hints while fixing bug | Only change lines that fix the reported issue   |
| Goal-Driven         | "I'll review and improve the code"              | "Write test for bug X -> make it pass -> verify no regressions" |

### Key meta-insight from EXAMPLES.md
> "The 'overcomplicated' examples aren't obviously wrong — they follow design patterns and best practices. The problem is **timing**: they add complexity before it's needed."
> "Good code is code that solves today's problem simply, not tomorrow's problem prematurely."

---

## 7. Design Philosophy & Tradeoffs

### Explicit tradeoff acknowledgment
The README and CLAUDE.md both state: "These guidelines bias toward **caution over speed**. For trivial tasks (simple typo fixes, obvious one-liners), use judgment — not every change needs the full rigor."

The goal is "reducing costly mistakes on non-trivial work, not slowing down simple tasks."

### Success metrics (from README "How to Know It's Working")
- Fewer unnecessary changes in diffs — only requested changes appear
- Fewer rewrites due to overcomplication — code is simple the first time
- Clarifying questions come before implementation — not after mistakes
- Clean, minimal PRs — no drive-by refactoring or "improvements"

### Customization model
The guidelines are designed to be **merged**, not replace, project-specific instructions. The README shows an example of adding project-specific sections (TypeScript strict mode, API endpoint tests, existing error handling patterns).

---

## 8. Relationship to Existing Frameworks

This repository is conceptually aligned with several existing philosophies:

- **YAGNI (Extreme Programming):** "No features beyond what was asked" / "Nothing speculative" — identical to Principle 2
- **Boy Scout Rule (inverted):** "Clean up only your own mess" — a more conservative variant; don't even clean up others' mess unless asked
- **Test-First / TDD:** Principle 4 transforms all tasks into test-first workflows
- **Minimal Diff Principle:** Principle 3's "every changed line should trace to the user's request"
- **Rubber Duck Debugging (adapted):** Principle 1 forces the LLM to externalize its reasoning before acting

The repository does NOT reference these frameworks by name — it presents the principles as derived directly from Karpathy's observations.

---

## 9. Notable Observations

1. **Extreme minimalism:** The core deliverable is 65 lines of Markdown. The entire repository is ~1000 lines of Markdown + 2 small JSON files. This is itself an embodiment of Principle 2 (Simplicity First).

2. **Content triplication:** The same four principles appear in CLAUDE.md, .cursor/rules/karpathy-guidelines.mdc, and skills/karpathy-guidelines/SKILL.md. Contributors must keep them in sync manually.

3. **Viral distribution model:** 180k stars for 65 lines of Markdown suggests the value proposition (Karpathy's name + a ready-to-use behavioral prompt) resonated massively. The star-to-commit ratio (180k stars / 28 commits) is extraordinarily high — this is a "drop-in config" repo, not a development project.

4. **Commercial angle:** The README header promotes "Multica" — the author's open-source platform for running coding agents with reusable skills. This repo serves as a funnel/marketing artifact for that platform.

5. **93 open PRs vs 28 commits:** The high PR count relative to commits suggests active community engagement but slow merge velocity — possibly because the content is so simple that there's little to merge, or the maintainer is selective.

6. **No tests, no CI, no code:** There is nothing to test, lint, or build. The "verification" the framework advocates (Principle 4) applies to the code that LLMs produce under its guidance, not to the framework itself.

7. **SKILL.md not at root:** The task requested fetching `/SKILL.md` at the repository root, but this file does not exist there. It lives at `skills/karpathy-guidelines/SKILL.md`. This is the Claude Code / Multica skill format (YAML frontmatter + markdown body).

---

## 10. Raw Content Captured

### CLAUDE.md (full text — the core artifact)

```markdown
# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" -> "Write tests for invalid inputs, then make them pass"
- "Fix the bug" -> "Write a test that reproduces it, then make it pass"
- "Refactor X" -> "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
```

### SKILL.md frontmatter (skills/karpathy-guidelines/)

```yaml
name: karpathy-guidelines
description: Behavioral guidelines to reduce common LLM coding mistakes. Use when writing, reviewing, or refactoring code to avoid overcomplication, make surgical changes, surface assumptions, and define verifiable success criteria.
license: MIT
```
Body text is identical to CLAUDE.md (same four principles, same wording).

### plugin.json (.claude-plugin/)

```json
{
  "name": "andrej-karpathy-skills",
  "description": "Behavioral guidelines to reduce common LLM coding mistakes, derived from Andrej Karpathy's observations on LLM coding pitfalls",
  "version": "1.0.0",
  "author": { "name": "forrestchang" },
  "license": "MIT",
  "keywords": ["guidelines", "best-practices", "coding", "karpathy"],
  "skills": ["./skills/karpathy-guidelines"]
}
```

---

## Researcher 1 — Summary Assessment

This repository is a **behavioral prompt framework**, not a software library. Its entire value is contained in 65 lines of Markdown encoding four principles that map 1:1 to Karpathy's public critiques of LLM coding behavior. The framework's innovation is not novelty — YAGNI, minimal diffs, and TDD predate it — but rather **packaging**: it distills established wisdom into a drop-in CLAUDE.md / Cursor rule / Claude Code plugin that requires zero configuration. The 180k stars reflect the market demand for ready-made LLM behavioral guardrails, amplified by Karpathy's name authority. The framework explicitly trades speed for caution and is designed to be merged with (not replace) project-specific instructions.
