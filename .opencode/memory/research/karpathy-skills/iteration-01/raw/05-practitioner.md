# 05 - Practitioner: How to Apply the Principles

## 1. Direct Application Patterns

### Pattern A: Before Writing Any Code
Apply **Think Before Coding**:
1. Read the user's request.
2. Identify implicit assumptions (file paths, formats, scope, volume, edge cases).
3. If ambiguous, ask up to 3 clarifying questions.
4. If multiple interpretations exist, present them with effort estimates.

### Pattern B: When Designing the Solution
Apply **Simplicity First**:
1. Start with the smallest code that satisfies the request.
2. If a feature is not explicitly requested, do not add it.
3. If a single-use abstraction appears, inline it.
4. Ask: *Would a senior engineer say this is overcomplicated?*

### Pattern C: When Editing Existing Code
Apply **Surgical Changes**:
1. Locate the exact lines relevant to the request.
2. Change only those lines.
3. Do not reformat, rename, or restructure adjacent code.
4. Remove only imports/variables/functions that your change made unused.
5. If you see unrelated dead code, mention it in the response but do not delete it.

### Pattern D: Before Declaring Done
Apply **Goal-Driven Execution**:
1. Define success criteria as tests or verifiable checks.
2. Write a failing test that reproduces the bug, then make it pass.
3. For multi-step tasks, use the format: `Step -> verify: check`.
4. Run the test suite and report results.

## 2. Integration with TelegramHelper's Workflow

### Before a Task
- Use the existing complexity classification (Tiny/Simple/Moderate/Complex/Critical) to decide how much rigor to apply.
- For Tiny/Simple tasks, apply the principles lightly (use judgment).
- For Moderate+, fully apply Think -> Simplify -> Surgical -> Goal-Driven.

### During Implementation
- Let **Ponytail** handle *Simplicity First* (it already enforces YAGNI).
- Let **Karpathy's Think Before Coding** handle ambiguity at the start.
- Let **Karpathy's Surgical Changes** prevent diff noise.
- Let **Karpathy's Goal-Driven Execution** define success criteria before invoking the heavy D5->R5 pipeline.

### After Implementation
- Run the **Zero-Risk Pipeline** (D5 debuggers + R5 reviewers).
- Use the **Goal Judge** as the final verification step.
- Report results in the **SUMMARY/CHANGES/EVIDENCE/RISKS/BLOCKERS** format.

## 3. Examples from EXAMPLES.md (Condensed)

### Example 1: Export User Data
- **Wrong:** Assumed all users, file path, fields, and CSV format.
- **Right:** Asked four questions before coding.
- **Lesson:** Ambiguity is not a feature to be silently resolved.

### Example 2: Discount Calculation
- **Wrong:** Strategy pattern + dataclass + 30+ lines.
- **Right:** One-line function.
- **Lesson:** One use case does not justify an abstraction.

### Example 3: Email Validator Bug
- **Wrong:** Added unrelated username validation and reformatted.
- **Right:** Only changed the empty-email handling.
- **Lesson:** Every changed line must trace to the request.

### Example 4: Rate Limiting
- **Wrong:** 300-line Redis + monitoring + config in one commit.
- **Right:** Four incremental steps, each with verification.
- **Lesson:** Decompose into independently verifiable milestones.

### Example 5: Duplicate Score Sorting
- **Wrong:** Changed sort logic immediately.
- **Right:** Wrote a failing test first, then fixed.
- **Lesson:** Reproduce before fixing.

## 4. Practical Prompt Template for TelegramHelper

When starting a non-trivial task:

```markdown
## Task Start Checklist
1. Think: What assumptions am I making? Are there multiple interpretations? If yes, ask.
2. Simplify: What is the minimum code that solves this? Avoid speculative features.
3. Surgical: Which files/lines must change? Avoid touching adjacent code.
4. Goal-Driven: What test or check proves success? State it before coding.

## After Implementation
- Run tests: `pytest tests/ -x -v`
- Run Zero-Risk Pipeline (D5 -> R5)
- Report in SUMMARY/CHANGES/EVIDENCE/RISKS/BLOCKERS format
```

## 5. Tools Used
- webfetch of EXAMPLES.md and CLAUDE.md.
- read of TelegramHelper-main rules.md and AGENTS.md to understand the local workflow.
- glob of `.opencode/skills/` and `skills/` to understand skill placement.

## 6. Final Practitioner Note
The Karpathy guidelines are not a replacement for TelegramHelper's constitution; they are a **behavioral layer** that sits above it. The constitution says what is forbidden; the Karpathy principles say how to approach the work within those constraints.
