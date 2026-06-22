# Synthesis: Karpathy-Inspired Claude Code Guidelines

## 1. SUMMARY (3-5 sentences)
The `andrej-karpathy-skills` repository (now canonical at `multica-ai`) is a viral, markdown-only behavioral prompt that encodes Andrej Karpathy's critique of LLM coding into four principles: Think Before Coding, Simplicity First, Surgical Changes, and Goal-Driven Execution. It has 180k stars, 18.4k forks, and 28 commits, distributed as a Claude Code plugin, Cursor rule, reusable skill, and per-project CLAUDE.md. The repository is not a code library but a risk-reduction framework: it prevents wrong assumptions, overengineering, orthogonal diff noise, and vague success criteria. TelegramHelper already covers the *Simplicity First* intent through its Ponytail plugin and the *Goal-Driven* intent through the Zero-Risk Pipeline, but it can strengthen the *Think Before Coding* and *Surgical Changes* dimensions by adding explicit, concise rules to AGENTS.md or CONSTITUTION.md. The principles must be adapted so they do not override TelegramHelper's protected invariants (async I/O, pydantic-settings, Alembic, no raw SQL, type annotations, test coverage).

## 2. KEY_FINDINGS (5-10 points)
1. **Single-purpose prompt artifact.** The repository's value is one file (`CLAUDE.md`) with four tightly defined principles. The rest is distribution plumbing.
2. **Direct response to three failure modes.** The principles map directly to Karpathy's tweet: wrong assumptions (Think), overcomplication (Simplify), and orthogonal changes (Surgical). Goal-Driven is the verification layer that makes the other three self-correcting.
3. **Strong community validation.** 180k stars and 18.4k forks in ~5 months suggest broad agreement that LLM coding needs behavioral guardrails, not just capability prompts.
4. **Excellent examples.** `EXAMPLES.md` is not optional fluff; it contains concrete before/after Python snippets that make the abstract principles actionable.
5. **Distribution is multi-channel.** The same content is packaged as Claude plugin, Cursor rule, skill file, and raw markdown. This increases adoption but also risks version drift.
6. **No quantitative evidence.** The repo relies on anecdote and authority (Karpathy's tweet). There are no A/B tests or bug-reduction metrics.
7. **LICENSE ambiguity.** The README and `SKILL.md` state MIT, but no `LICENSE` file exists in the repository root.
8. **No trivial-task definition.** The guidelines say "use judgment for trivial tasks" but do not define what trivial means. TelegramHelper's complexity classification can fill this gap.
9. **Overlap with existing TelegramHelper rules.** Ponytail already enforces YAGNI; the Zero-Risk Pipeline already enforces verification. The new value is in *Think Before Coding* and *Surgical Changes*.
10. **Conflict potential with strict invariants.** Simplicity First could be misread to justify skipping type annotations, error handling, or tests. The constitution must remain the hard floor.

## 3. PRINCIPLES (detailed)

### 3.1 Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**
- State assumptions explicitly before implementing.
- If multiple interpretations exist, present them and ask the user to choose.
- If a simpler approach exists, push back and propose it.
- If something is unclear, stop and name the confusion.
- **Why it matters:** LLMs silently pick the most convenient interpretation and run with it, often solving the wrong problem or using the wrong format.
- **TelegramHelper application:** Add a rule to AGENTS.md that before any Moderate+ task, the agent must list up to 3 assumptions or clarifying questions. If none, state "No ambiguity detected."

### 3.2 Simplicity First
**Minimum code that solves the problem. Nothing speculative.**
- No features beyond what was asked.
- No abstractions for single-use code.
- No flexibility or configurability that was not requested.
- No error handling for logically impossible scenarios.
- If 200 lines could be 50, rewrite it.
- **Why it matters:** LLMs generate elaborate class hierarchies, config systems, and caches for problems that need a single function.
- **TelegramHelper application:** This is already covered by Ponytail. Add a cross-reference note: "Simplicity First: see Ponytail ladder." Do not duplicate; instead reinforce with the senior-engineer heuristic.

### 3.3 Surgical Changes
**Touch only what you must. Clean up only your own mess.**
- Do not improve adjacent code, comments, or formatting.
- Do not refactor things that are not broken.
- Match existing style, even if you would do it differently.
- If you notice unrelated dead code, mention it but do not delete it.
- Remove only imports/variables/functions that your changes made unused.
- **Why it matters:** LLMs produce noisy diffs that make reviews harder and introduce unrelated regressions.
- **TelegramHelper application:** Add a rule to AGENTS.md that every changed line must trace to the user's request. Before finalizing, the agent must review the diff and remove drive-by changes (unless explicitly requested).

### 3.4 Goal-Driven Execution
**Define success criteria. Loop until verified.**
- Transform vague instructions into verifiable goals.
- Write tests before/while fixing bugs.
- For multi-step tasks, use `Step -> verify: check` format.
- Strong criteria let the LLM loop autonomously; weak criteria require constant clarification.
- **Why it matters:** LLMs are good at looping until a specific criterion is met; giving them vague instructions wastes that strength.
- **TelegramHelper application:** This is already covered by the Goal Judge and Zero-Risk Pipeline. Add an explicit pre-coding step: state the success criteria that will be used by the Goal Judge.

## 4. RISKS (contradictions and limitations)
1. **Rigidity on trivial tasks.** The guidelines can slow down one-line fixes. TelegramHelper's complexity classification should gate the level of rigor.
2. **Conflict with protected invariants.** Simplicity First must not override async I/O, pydantic-settings, Alembic, no raw SQL, type annotations, or test requirements.
3. **Dead-code accumulation.** Surgical Changes discourages deleting pre-existing dead code. TelegramHelper should route dead-code findings to a separate task or an explicit approval step.
4. **Redundancy with Ponytail and Zero-Risk Pipeline.** Adding all four principles verbatim would duplicate existing instructions. Instead, add the missing dimensions (Think, Surgical) and cross-reference the existing ones.
5. **Prompt dilution.** The constitution is already long. Adding too much Karpathy text could lower signal-to-noise. Keep additions concise and link to the full research files for detail.
6. **No quantitative proof.** The principles are heuristics. Monitor whether they actually reduce diff noise and review iterations in TelegramHelper.

## 5. USAGE_PATTERNS (how to apply)
1. **Per-task checklist.** At the start of a Moderate+ task, the agent must:
   - List assumptions (Think).
   - Propose the minimal solution (Simplify).
   - Identify the exact files/lines to touch (Surgical).
   - State the success criteria (Goal-Driven).
2. **Diff self-review.** Before saying "done," the agent must review the diff and revert any changes not directly traceable to the request.
3. **Trivial task bypass.** For Tiny/Simple tasks, skip the explicit checklist and use judgment.
4. **Dead-code flagging.** When unrelated dead code is spotted, mention it in `RISKS` or `BLOCKERS`, not delete it.
5. **Test-first for bugs.** Always reproduce the bug with a test before applying the fix, then run the full test suite.

## 6. EXAMPLES (from EXAMPLES.md)
- **Export user data:** Wrong = silent assumptions about all users, file path, fields. Right = four clarifying questions before implementation.
- **Discount calculation:** Wrong = 30-line strategy-pattern hierarchy. Right = one-line function.
- **Preference manager:** Wrong = cache, validator, merge, notify. Right = direct SQL update.
- **Email validator bug:** Wrong = added username validation and reformatted. Right = only changed empty-email handling.
- **Upload logging:** Wrong = changed quotes, added types, docstrings. Right = added logging only, matching existing style.
- **Rate limiting:** Wrong = 300-line Redis + monitoring + config in one commit. Right = four incremental verifiable steps.
- **Duplicate score sorting:** Wrong = changed sort immediately. Right = wrote failing test first, then stable sort.

## 7. RECOMMENDATIONS_FOR_TELEGRAMHELPER
### Add to AGENTS.md (or CONSTITUTION.md)
1. **Think Before Coding rule:**
   > Before implementing a Moderate+ task, state your assumptions. If the request is ambiguous, present up to 3 interpretations or clarifying questions and wait for user choice. Do not silently guess.
2. **Surgical Changes rule:**
   > Every changed line must trace directly to the user's request. Do not improve adjacent code, comments, or formatting. Do not refactor unrelated code. If you find unrelated dead code, mention it but do not delete it.
3. **Cross-reference Simplicity First to Ponytail:**
   > For YAGNI and minimal-code decisions, follow the Ponytail decision ladder. Do not override protected invariants (async, types, tests, config, migrations) in the name of simplicity.
4. **Cross-reference Goal-Driven to Zero-Risk Pipeline:**
   > Define success criteria before coding. Use them as input to the Goal Judge. For multi-step tasks, list `Step -> verify: check`.
5. **Trivial task exception:**
   > For Tiny/Simple tasks (one file, obvious fix), the full Think/Surgical/Goal-Driven checklist is optional; use judgment.

### Add a skill (optional)
Create `.opencode/skills/karpathy/SKILL.md` as a lightweight, reusable skill that summarizes the four principles with TelegramHelper-specific constraints. This avoids bloating the system prompt for every request.

## 8. CONFIDENCE
**High** for the content of the four principles and the repository structure (directly fetched from the canonical repo). **Medium** for community-impact claims (stars/forks are visible, but causal impact is anecdotal). **Medium** for the exact best insertion point in TelegramHelper's prompt hierarchy; the recommendation is conservative and preserves the constitution's supremacy.

## 9. GAPS (what was not determined)
1. **LICENSE file absence.** The README says MIT, but no `LICENSE` file was found. Not a blocker, but a hygiene gap.
2. **Plugin manifest contents.** `.claude-plugin/plugin.json` and `marketplace.json` were listed but not fetched; their exact schema and version pinning are unknown.
3. **Closed PR themes.** The PR page loaded partially but truncated; detailed themes of accepted vs rejected contributions are not fully analyzed.
4. **Empirical effectiveness.** No data exists on whether these prompts reduce bugs or review cycles in real projects.
5. **Old vs new URL consistency.** The README still references `forrestchang` URLs after the move to `multica-ai`; the maintainer's intent regarding backward compatibility is not confirmed.
6. **Cursor rule precedence.** How Cursor handles `alwaysApply: true` rules and whether they stack with user rules was not tested.

---

**Output contract format:**
- SUMMARY: see section 1.
- CHANGES: Recommended additions to AGENTS.md/CONSTITUTION.md; optional new skill file.
- EVIDENCE: Raw files fetched and analyzed in `raw/01-birds-eye.md` through `raw/05-practitioner.md`.
- RISKS: See section 4.
- BLOCKERS: None. The repository is readable and the recommendations are non-destructive.
