# 02 - Deep Dive: Principles, Mechanics, and Distribution

## 1. Principle-by-Principle Breakdown

### 1.1 Think Before Coding
**Core mechanic:** Force the LLM to externalize its reasoning before touching the codebase.

- **State assumptions explicitly** - ask if the user did not specify file location, scope, fields, or format.
- **Present multiple interpretations** - when a request is ambiguous (e.g., "make the search faster"), enumerate the possible meanings (latency, throughput, perceived speed) and ask which one matters.
- **Push back when warranted** - if a simpler approach exists, say so.
- **Stop when confused** - name the confusion and ask for clarification rather than guessing.

**Evidence from CLAUDE.md:**
> "If something is unclear, stop. Name what is confusing. Ask."

**Evidence from EXAMPLES.md - export user data:**
The wrong version silently assumes all users, a file path, field list, and CSV format. The right version lists four clarifying questions (scope, format, fields, volume) before writing code.

### 1.2 Simplicity First
**Core mechanic:** Treat complexity as a cost that must be justified by an explicit requirement.

- No features beyond what was asked.
- No abstractions for single-use code.
- No flexibility or configurability that was not requested.
- No error handling for impossible scenarios.
- If 200 lines could be 50, rewrite it.

**Evidence from EXAMPLES.md - discount calculation:**
The wrong version uses ABC, Enum, Protocol, dataclass, and a DiscountCalculator class for a single percentage calculation. The right version is a one-line function.

**Evidence from EXAMPLES.md - preference manager:**
The wrong version adds cache, validator, merge, and notify features. The right version is a direct SQL update.

### 1.3 Surgical Changes
**Core mechanic:** Minimize the diff footprint and preserve the existing codebase.

- Do not improve adjacent code, comments, or formatting.
- Do not refactor things that are not broken.
- Match existing style, even if you would do it differently.
- If you notice unrelated dead code, mention it - do not delete it.
- Remove imports/variables/functions that your changes made unused.
- Do not remove pre-existing dead code unless asked.

**Evidence from EXAMPLES.md - email validator bug:**
The wrong diff adds email complexity, username validation, docstring, and changed comments. The surgical diff only adds a local email variable and a .strip() check on the specific line that fixes the bug.

**Evidence from EXAMPLES.md - upload logging:**
The wrong diff changes quote style, adds type hints, docstrings, and reformats. The surgical diff adds only import logging, a logger, and logging calls while matching existing quote style and boolean return pattern.

### 1.4 Goal-Driven Execution
**Core mechanic:** Convert imperative instructions into declarative, verifiable goals so the LLM can loop autonomously.

- Add validation -> Write tests for invalid inputs, then make them pass.
- Fix the bug -> Write a test that reproduces it, then make it pass.
- Refactor X -> Ensure tests pass before and after.
- Multi-step tasks must have a brief plan: Step -> verify: check.

**Evidence from EXAMPLES.md - rate limiting:**
Wrong version: one 300-line commit with Redis, strategies, config, and monitoring. Right version: four incremental steps, each independently verifiable.

**Evidence from EXAMPLES.md - duplicate score sorting:**
Wrong version: immediately changes sort logic. Right version: first writes a failing test that reproduces the non-deterministic ordering, then fixes it with a stable sort.

## 2. Distribution Mechanics

### 2.1 Claude Code Plugin
- .claude-plugin/marketplace.json and .claude-plugin/plugin.json define the plugin.
- Install via /plugin marketplace add forrestchang/andrej-karpathy-skills then /plugin install andrej-karpathy-skills@karpathy-skills.
- This makes the skill globally available across all Claude Code projects.

### 2.2 Cursor Rule
- .cursor/rules/karpathy-guidelines.mdc has alwaysApply: true and description: Behavioral guidelines....
- When the project is opened in Cursor, the rule applies automatically.
- For other projects, copy the .mdc file into .cursor/rules/.

### 2.3 Skill File
- skills/karpathy-guidelines/SKILL.md is a reusable skill format.
- Can be symlinked or copied into ~/.cursor/skills or similar.

### 2.4 Per-Project Drop-In
- curl -o CLAUDE.md https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md
- Or append to an existing CLAUDE.md.

## 3. Rhetorical and Structural Choices

- **Tradeoff note:** The guidelines explicitly bias toward caution over speed and allow judgment for trivial tasks.
- **Senior-engineer heuristic:** Simplicity First uses the test: Would a senior engineer say this is overcomplicated?
- **Traceability test:** Surgical Changes uses the test: Every changed line should trace directly to the user's request.
- **Loop leverage:** Goal-Driven Execution cites Karpathy: LLMs are exceptionally good at looping until they meet specific goals... Do not tell it what to do, give it success criteria and watch it go.

## 4. Tools Used
- webfetch of CLAUDE.md, EXAMPLES.md, .cursor/rules/karpathy-guidelines.mdc, and skills/karpathy-guidelines/SKILL.md.
- read of TelegramHelper-main/CLAUDE.md and AGENTS.md to compare principle overlap.
- grep of the local project for the four principle names (none found).

## 5. Deep Insight
The four principles are not independent; they form a pipeline:
1. **Think** -> prevent the wrong problem from being solved.
2. **Simplify** -> prevent the right problem from being over-solved.
3. **Surgical** -> prevent the solution from spilling into unrelated code.
4. **Goal-Driven** -> make the solution self-verifying.

This is a **risk-reduction framework** disguised as a coding-style guide.
