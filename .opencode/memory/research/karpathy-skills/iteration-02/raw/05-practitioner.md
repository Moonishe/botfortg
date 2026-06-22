# Researcher 5 - Practitioner (Applied Perspective)

Repository: https://github.com/forrestchang/andrej-karpathy-skills
Files analyzed: CLAUDE.md (4 principles, 65 lines), EXAMPLES.md (concrete before/after, 522 lines)
Date: 2026-06-22

---

## 1. Source Material Summary

CLAUDE.md defines four behavioral principles to reduce common LLM coding mistakes:
1. Think Before Coding - Don't assume. Don't hide confusion. Surface tradeoffs.
2. Simplicity First - Minimum code that solves the problem. Nothing speculative.
3. Surgical Changes - Touch only what you must. Clean up only your own mess.
4. Goal-Driven Execution - Define success criteria. Loop until verified.

EXAMPLES.md provides concrete before/after for each principle across 11 examples plus an anti-patterns summary table. The key meta-insight: the wrong examples are not obviously wrong - they follow standard design patterns and best practices. The defect is TIMING (premature complexity), not technique.

Tradeoff stated explicitly in CLAUDE.md: These guidelines bias toward caution over speed. For trivial tasks, use judgment. This is the bypass clause.

---

## 2. Per-Task Checklist (Practitioner Application)

A consolidated checklist derived from all four principles, designed to run at the START of any non-trivial task.

### Think (before coding)
- [ ] State assumptions explicitly. If uncertain, ask - do not silently pick.
- [ ] If multiple interpretations exist, enumerate them with effort estimates, let user choose.
- [ ] If a simpler approach exists than the one being considered, name it and push back.
- [ ] If something is unclear, STOP. Name what is confusing. Ask before proceeding.

### Simplify (during design)
- [ ] No features beyond what was asked.
- [ ] No abstractions for single-use code (no ABC/Protocol/Strategy for one discount type).
- [ ] No flexibility or configurability that was not requested.
- [ ] No error handling for impossible scenarios.
- [ ] Self-check: Would a senior engineer say this is overcomplicated? If yes, rewrite.
- [ ] If you wrote 200 lines and it could be 50, rewrite it.

### Surgical (when editing existing code)
- [ ] Every changed line traces directly to the user request.
- [ ] Do not improve adjacent code, comments, or formatting.
- [ ] Do not refactor things that are not broken.
- [ ] Match existing style, even if you would do it differently (quotes, type hints, spacing, docstrings).
- [ ] Unrelated dead code noticed - MENTION it, do not delete it.
- [ ] Remove only the orphans YOUR changes created (imports/vars/funcs made unused by your edit).
- [ ] Do not remove pre-existing dead code unless explicitly asked.

### Goal-Driven (define success + loop)
- [ ] Transform vague task into verifiable goal:
      - Add validation -> Write tests for invalid inputs, then make them pass
      - Fix the bug -> Write a test that reproduces it, then make it pass
      - Refactor X -> Ensure tests pass before and after
- [ ] For multi-step tasks, state a brief plan with per-step verification check.
- [ ] Strong success criteria allow looping independently; weak criteria (make it work) force clarification loops.

---

## 3. Diff Self-Review (before done)

Derived from the Surgical Changes examples (drive-by refactoring, style drift) and the working-if success statement at the end of CLAUDE.md.

### Pre-done review procedure
1. Generate the diff.
2. For each changed line, ask: Does this line trace directly to the user request?
   - If NO and it is a drive-by improvement - REVERT it.
   - If NO and it is an orphan your change created - keep the removal (that IS your mess).
3. Check for style drift:
   - Quote style changed (single to double)? - revert to match existing.
   - Type hints added that were not requested? - revert.
   - Docstring added that was not requested? - revert.
   - Whitespace reformatted? - revert.
   - Boolean/return logic restructured without need? - revert.
4. Check for scope creep:
   - Validation added beyond the reported bug? - revert.
   - Username validation added while fixing email bug? - revert.
5. Check for dead code encountered:
   - Pre-existing dead code touched? - revert the touch, add a note in RISKS instead.
6. Success signal (from CLAUDE.md): fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

### Concrete diff-review checklist
- [ ] Every plus/minus line maps to a request line item.
- [ ] No quote-style changes.
- [ ] No unrequested type hints or docstrings.
- [ ] No reformatting of untouched logic.
- [ ] Only MY orphans removed, not pre-existing dead code.
- [ ] Dead code noticed is flagged in RISKS, not deleted.

---

## 4. Trivial Task Bypass

CLAUDE.md explicitly states: These guidelines bias toward caution over speed. For trivial tasks, use judgment.

Mapping to the TelegramHelper task-size taxonomy (AGENTS.md / rules.md):

| Task size | Full checklist? | What to skip | What to keep |
|-----------|-----------------|--------------|--------------|
| Tiny (1 file, couple lines, obvious fix, no contract/arch/security change) | NO | Think multi-interpretation enumeration; multi-step plan with verify per step | Surgical (always - even tiny tasks must not drive-by); Goal (one-line success criterion) |
| Simple (1-3 files, clear requirements, low-med risk) | Light | Full assumption enumeration if requirements are unambiguous | Surgical + Goal + a targeted test |
| Moderate+ | YES | Nothing | All four principles fully |

Bypass rule of thumb: if the diff touches 5 lines or fewer, has a single obvious interpretation, and changes no public contract - apply Surgical + Goal only, skip Think-enumeration and Simplify-rewrite loops. Still do the diff self-review (Surgical is the one principle that must NEVER be bypassed - drive-by edits in a tiny task are still drive-by edits).

---

## 5. Dead-Code Flagging Policy

From CLAUDE.md section 3 Surgical Changes:
- If you notice unrelated dead code, mention it - do not delete it.
- Do not remove pre-existing dead code unless asked.

From EXAMPLES.md anti-patterns table: the fix for touching adjacent code is: Only change lines that fix the reported issue.

### Operational rule
- Dead code ENCOUNTERED during an edit - do NOT delete, do NOT refactor.
- Report it in the RISKS section of the sub-agent output contract (SUMMARY/CHANGES/EVIDENCE/RISKS/BLOCKERS per AGENTS.md).
- Format suggestion: RISKS: Dead code found in <file>:<line> - <symbol> appears unused. Not deleted per Surgical Changes policy. Recommend separate cleanup task if desired.
- Only remove dead code that YOUR change orphaned (e.g., an import your edit made unused).

This aligns with and reinforces the TelegramHelper AGENTS.md Files-NEVER-to-modify-without-explicit-permission list - flagging respects protected files.

---

## 6. Test-First for Bugs

From CLAUDE.md section 4 and EXAMPLES.md Example 3 (Test-First Verification):

The canonical bug-fix workflow:
1. Reproduce first - write a test that reproduces the reported bug.
2. Verify the test fails (and fails for the RIGHT reason - run it multiple times if non-deterministic, per the duplicate-scores example).
3. Fix with the minimal surgical change.
4. Verify the test passes consistently.
5. Verify no regression - existing tests still green.

Anti-pattern (explicitly called out): Immediately changes sort logic without confirming the bug. The fix-without-reproducing approach means you cannot tell whether you fixed the actual bug, a different bug, or nothing.

### TelegramHelper alignment
AGENTS.md already states: New feature = new test in tests/. Use pytest-asyncio with in-memory SQLite. The test-first principle EXTENDS this to bugs: a bug fix must be preceded by a failing test. This is stronger than add a test for the fix - the test must exist and FAIL before the fix is applied, proving reproduction.

---

## 7. Anti-Patterns Summary (from EXAMPLES.md)

| Principle | Anti-Pattern | Fix |
|-----------|--------------|-----|
| Think Before Coding | Silently assumes file format, fields, scope | List assumptions explicitly, ask for clarification |
| Simplicity First | Strategy pattern for single discount calculation | One function until complexity is actually needed |
| Surgical Changes | Reformats quotes, adds type hints while fixing bug | Only change lines that fix the reported issue |
| Goal-Driven | I will review and improve the code | Write test for bug X, make it pass, verify no regressions |

Key meta-insight: the overcomplicated examples follow legitimate design patterns. The defect is TIMING - adding complexity before it is needed. Good code is code that solves today problem simply, not tomorrow problem prematurely.

---

## 8. Recommendations for TelegramHelper Adaptation

The TelegramHelper AGENTS.md already has Ponytail (Simplicity) and Zero-Risk Pipeline (D5/R5 goal verification). The Karpathy skills add two missing primitives: Think (explicit assumption surfacing) and Surgical (diff-line-to-request traceability), plus a concrete test-first discipline for bugs.

### 8.1 Add Think + Surgical to AGENTS.md
Insert a new section Karpathy Coding Principles with:
- Think: Before any Moderate+ task, the Worker must output an ASSUMPTIONS block listing every unstated assumption. If 2+ interpretations exist, enumerate with effort estimates and STOP for user choice. Cross-reference existing Planner agent (planner.md) which already does decomposition - Think adds the assumption-surfacing layer the Planner lacks.
- Surgical: Add to the Worker agent prompt and to Lead Reviewer checklist: Every changed line must trace directly to the user request. No drive-by refactors, no style drift, no unrequested type hints/docstrings. Flag dead code in RISKS, never delete.

### 8.2 Cross-reference Ponytail for Simplicity
Ponytail ladder (rung 1: YAGNI, rung 5: one-liner) is the operational form of Karpathy Simplicity First. They are fully compatible:
- Karpathy No abstractions for single-use code == Ponytail no interface with one implementation.
- Karpathy If you wrote 200 lines and it could be 50, rewrite == Ponytail shortest working diff wins.
- Recommendation: in AGENTS.md under Ponytail, add one line: Simplicity First also enforced by Karpathy section 2 - see research/karpathy-skills. This makes the dual-source explicit so reviewers can cite either.

### 8.3 Cross-reference Zero-Risk Pipeline for Goal-Driven
Karpathy Goal-Driven (define success criteria, loop until verified) maps to the Zero-Risk Pipeline loop-until-zero-problems and the Goal Judge JSON {ok, impossible, reason}:
- Karpathy Write test that reproduces bug then make it pass == Zero-Risk D5 debuggers find root cause, R5 reviewers verify.
- Karpathy per-step verify-check == Zero-Risk per-iteration problem count to 0.
- Recommendation: add to Goal Judge prompt a check: Did the task have a verifiable success criterion (test or check) stated BEFORE implementation? If not, flag as incomplete. This closes the gap where the pipeline verifies output quality but not whether the goal was pre-defined.

### 8.4 Add Diff Self-Review as a mandatory R5 reviewer step
The R5 Maintainability reviewer (review-maintainability.md) should include the Surgical diff-review checklist from section 3 above. Specifically add to its prompt:
Review the diff line-by-line. Any changed line that does NOT trace to the user request = flag as drive-by. Any style drift (quotes, type hints, docstrings not matching existing) = flag. Any pre-existing dead code deleted = flag. Require revert of all drive-by changes before approving.

### 8.5 Add Trivial Task Bypass to the task router
The Progressive Complexity Router (main.md section 0) already has Tiny/Simple/Moderate/Complex/Critical tiers. Add an explicit bypass note to Tiny: Tiny tasks skip full Karpathy checklist; apply Surgical + Goal only. Diff self-review is STILL mandatory - drive-by edits in a tiny task are still drive-by edits.

### 8.6 Add Test-First for Bugs to Test Engineer prompt
test-engineer.md should require: for any bug-fix task, the first artifact is a failing test that reproduces the bug (in-memory SQLite, pytest-asyncio per AGENTS.md). The fix is not accepted until (a) the test fails before the fix, (b) passes after, (c) full suite green. This is already partially implied by New feature = new test but must be explicit for bugs.

### 8.7 Dead-code flagging in Sub-agent Output Contract
The existing RISKS field in SUMMARY/CHANGES/EVIDENCE/RISKS/BLOCKERS should gain a standard dead-code flag format:
RISKS: Dead code: <file>:<line> <symbol> - unused, not deleted (Surgical policy). Separate cleanup recommended.

---

## 9. Confidence Assessment

- Source authority: HIGH - repo is the canonical Karpathy skills doc (180k stars, 18.4k forks, multi-file structure).
- Content retrieval: MEDIUM - raw.githubusercontent.com returned transport errors; fetched via github.com blob view which includes GitHub chrome but the markdown body was fully rendered and readable. All four principles and all 11 examples were captured intact.
- Application to TelegramHelper: HIGH - the mapping between Karpathy principles and existing AGENTS.md structures (Ponytail, Zero-Risk, R5 reviewers, Sub-agent Output Contract) is direct and non-overlapping. Think and Surgical are genuinely additive; Simplicity and Goal-Driven reinforce existing layers.
