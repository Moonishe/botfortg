# 03 - Devil's Advocate: Risks, Conflicts, and Critical Analysis (Iteration 02)

> Researcher 3 - Critical perspective on andrej-karpathy-skills
> Sources: CLAUDE.md (65 lines / 2.3KB), README.md (171 lines / 6.05KB), EXAMPLES.md (522 lines / 14.5KB)
> Repo: https://github.com/multica-ai/andrej-karpathy-skills (redirected from forrestchang/)
> Date: 2026-06-22

---

## SUMMARY

The karpathy-skills CLAUDE.md is a well-written 65-line behavioral prompt with 4 principles (Think Before Coding, Simplicity First, Surgical Changes, Goal-Driven Execution). However, the EXAMPLES.md file that accompanies it contains concrete examples that directly conflict with TelegramHelper protected invariants - most critically, it presents removing type hints as "correct" behavior. The guidelines are approximately 50-75% redundant with the project existing Ponytail plugin and Zero-Risk Pipeline. The "trivial task" escape hatch is vaguely defined and split across two files. No LICENSE file exists despite claiming MIT, creating a legal risk for incorporation. No quantitative evidence supports efficacy; 180k stars reflect celebrity endorsement, not proven results.

---

## CRITICAL ISSUES (Severity-Ranked)

### CRITICAL-1: EXAMPLES.md Explicitly Removes Type Hints as "Correct" Behavior

**Severity: CRITICAL - Direct violation of protected invariant**

In EXAMPLES.md, Section 3 (Surgical Changes), Example 2 ("Style Drift"), the scenario is: user asks to "Add logging to the upload function." The "wrong" (LLM) version adds type hints (def upload_file with str/bool annotations). The "correct" (surgical) version explicitly keeps the original signature without type hints and states:

> **Matched:** Single quotes, **no type hints**, existing boolean pattern, spacing style.

This directly conflicts with TelegramHelper AGENTS.md protected invariant:

> Type annotations on ALL public functions/methods (pyright strict mode).

If a model internalizes this example, it will treat adding type annotations as a "drive-by refactoring" to be avoided - the exact opposite of the project requirement. This is not a theoretical risk; it is a concrete, demonstrated conflict in the repo own examples.

**Mitigation:** Never import EXAMPLES.md into the project. If the CLAUDE.md is adopted, add an explicit override: "Type annotations are mandatory per project constitution; Surgical Changes does not apply to type hint additions."
---

### CRITICAL-2: No LICENSE File Despite Claiming MIT

**Severity: CRITICAL - Legal risk for incorporation**

The README.md states under "License": MIT. However, the repository file tree shows:

    .claude-plugin/
    .cursor/rules/
    skills/karpathy-guidelines/
    CLAUDE.md
    CURSOR.md
    EXAMPLES.md
    README.md
    README.zh.md

No LICENSE file exists. Under standard copyright law (Berne Convention), code without a proper license file defaults to "all rights reserved." The MIT license requires inclusion of the copyright notice and permission text to be legally valid. A README statement alone does not constitute a valid license grant.

The install instructions actively encourage copying:
- curl -o CLAUDE.md https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md
- echo "" >> CLAUDE.md
- curl https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md >> CLAUDE.md

This means every user who follows the install instructions is copying copyrighted material without a properly granted license. For TelegramHelper, incorporating this text into AGENTS.md or CONSTITUTION.md carries a (low-probability but real) legal risk.

**Mitigation:** If incorporating, add a LICENSE file to the karpathy-skills repo first (via PR), or treat the content as public domain given Karpathy tweet context, or paraphrase the principles rather than copying verbatim.

---

### HIGH-1: Surgical Changes "Do Not Delete Dead Code" Contradicts Ponytail "Deletion Over Addition"

**Severity: HIGH - Direct operational conflict between two active rule sets**

Karpathy Surgical Changes states:
- "If you notice unrelated dead code, mention it - do not delete it."
- "Do not remove pre-existing dead code unless asked."

Ponytail (active in full mode in this project) states:
- "Deletion over addition. Boring over clever, clever is what someone decodes at 3am."
- "Fewest files possible. Shortest working diff wins."

These are directly contradictory. When a developer encounters dead code during an unrelated change:
- Karpathy says: leave it, just mention it.
- Ponytail says: delete it (deletion is preferred over addition).

If both rules are active simultaneously, the model faces an unresolvable conflict. The project authority hierarchy (user > code > AGENTS.md > rules.md > memory > handoffs) does not include Karpathy CLAUDE.md, so there is no defined precedence.

**Dead-code accumulation risk:** Over a project lifetime, the "mention but do not delete" rule creates a stream of dead-code mentions that are logged in reviews but never acted upon. This is the "broken windows" anti-pattern - visible decay that signals neglect and encourages further mess. In a long-running project like TelegramHelper, this leads to gradual codebase rot.

**Mitigation:** Ponytail "deletion over addition" should take precedence for dead code that is unambiguously unused (verified via serena_find_referencing_symbols). Karpathy "mention it" applies only to code whose usage is uncertain.
---

### HIGH-2: "No Error Handling for Impossible Scenarios" Is Dangerously Vague

**Severity: HIGH - Could justify skipping mandated error handling**

Karpathy Simplicity First states:
> No error handling for impossible scenarios.

TelegramHelper AGENTS.md mandates:
> Error handling: log every exception. NO bare except: pass.
> Graceful shutdown: every component must have cleanup with timeouts.

TelegramHelper is a distributed system (Telegram Bot API + SQLite + Qdrant + asyncio). In such systems, "impossible" scenarios happen routinely:
- Race conditions between concurrent handlers
- Partial database writes during crash
- Network timeouts on Telegram API calls
- SQLite "database is locked" errors under concurrency
- Qdrant embedded mode corruption
- asyncio task cancellation during shutdown

A model following "no error handling for impossible scenarios" could interpret any of these as "impossible" and skip defensive code. The project explicitly requires logging every exception and graceful shutdown with timeouts - both of which are "error handling for scenarios that should not happen but do."

The word "impossible" is subjective and context-dependent. Without a precise definition, this rule is a loaded gun in a distributed system.

**Mitigation:** Define "impossible scenarios" as "logically unreachable code paths proven by type system or control flow analysis" - NOT "unlikely runtime conditions." The project existing rule ("log every exception") overrides Karpathy simplification.

---

### HIGH-3: EXAMPLES.md Treats Additional Input Validation as "Wrong" Drive-By

**Severity: HIGH - Security implication for trust-boundary code**

In EXAMPLES.md, Section 3 (Surgical Changes), Example 1 ("Drive-by Refactoring"), the "wrong" version adds username validation (length check, alphanumeric check) while fixing an email bug. The "correct" version only fixes the email bug and leaves username validation unchanged.

The principle: "Every changed line should trace directly to the user request."

However, TelegramHelper is a Telegram bot that processes user input - a trust boundary. The project Ponytail explicitly protects:
> Never simplify away: input validation at trust boundaries

Additional validation at a trust boundary is not a "drive-by improvement" - it is a security fix. Karpathy example blurs the line between cosmetic drive-by changes and security-relevant additions. A model following this example literally would refuse to add input validation unless explicitly asked, even when it identifies a security hole.

**Mitigation:** Surgical Changes should not apply to security-critical code paths. The project security review swarm (R5) should have authority to flag and fix validation gaps regardless of whether they were explicitly requested.
---

### MEDIUM-1: No Trivial-Task Definition

**Severity: MEDIUM - Over-application risk**

The CLAUDE.md header says:
> **Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

The README "Tradeoff Note" elaborates:
> For trivial tasks (simple typo fixes, obvious one-liners), use judgment - not every change needs the full rigor.

Problems:
1. "Trivial" is never formally defined - no line count, no complexity threshold, no risk classification.
2. The concrete examples ("simple typo fixes, obvious one-liners") appear ONLY in the README, not in the CLAUDE.md. The install instructions tell users to curl the CLAUDE.md - so the model gets the vague "use judgment" caveat without the concrete examples.
3. "Use judgment" is itself subjective - different models (or the same model in different contexts) will interpret it differently.
4. The project rules.md already has a precise "Tiny" classification: "One file, pair of lines, obvious fix (typo, CSS, naming), no contract/architecture/security changes, low regression risk." Karpathy guidelines do not reference any such taxonomy.

**Mitigation:** Map "trivial" to the project existing "Tiny" classification. Add to the CLAUDE.md (if adopted): "Trivial = Tiny per rules.md classification (1 file, less than 5 lines, no contract/security/architecture change)."

---

### MEDIUM-2: Goal-Driven Execution Mandates Test-First Even for Trivial Fixes

**Severity: MEDIUM - Process overhead for simple changes**

Goal-Driven Execution prescribes:
- "Add validation" then "Write tests for invalid inputs, then make them pass"
- "Fix the bug" then "Write a test that reproduces it, then make it pass"
- "Refactor X" then "Ensure tests pass before and after"

This is excellent for bugs and features but pure overhead for:
- Typo fixes (write a test that reproduces a typo?)
- Config value changes (write a test for a changed timeout value?)
- One-line CSS/style tweaks
- Comment corrections
- Import reordering

The project rules.md says Tiny tasks need only targeted test and typecheck for touched area - not full test-first reproduction. Karpathy principle is stricter than the project own process for small tasks.

**Mitigation:** Goal-Driven Execution applies to Simple+ tasks. Tiny tasks follow rules.md lighter validation (targeted test + typecheck only).

---

### MEDIUM-3: No Quantitative Evidence - Celebrity Endorsement, Not Engineering Proof

**Severity: MEDIUM - Adoption justification risk**

The repo has:
- 180k stars
- 18.4k forks
- 28 commits
- 0 releases
- 0 published tests
- 0 benchmarks
- 0 A/B comparisons

Star-to-commit ratio: approximately 6,429 stars per commit. This is extreme and indicates viral popularity driven by Karpathy name and tweet (linked in README), not by proven efficacy.

The "How to Know It Working" section is entirely subjective:
- "Fewer unnecessary changes in diffs" - no measurement method
- "Fewer rewrites due to overcomplication" - no baseline, no metric
- "Clarifying questions come before implementation" - no tracking
- "Clean, minimal PRs" - no definition of "clean" or "minimal"

No controlled experiment compares: (a) projects with the CLAUDE.md vs. (b) projects without. No diff-size metrics, no bug-rate comparisons, no time-to-completion measurements. The evidence base is: one Karpathy tweet + community vibes.

**Risk:** Treating these guidelines as proven best practices rather than untested hypotheses could lead to over-confident adoption that displaces working processes.

**Mitigation:** Treat as "plausible but unproven." If adopted, measure: diff size, clarification frequency, rework rate. Compare against baseline before full commitment.
---

### MEDIUM-4: No Versioning - Unpinned Content via curl

**Severity: MEDIUM - Supply chain risk**

The repo has 28 commits but:
- 0 published releases
- 0 tags
- No changelog
- No version number in CLAUDE.md

The install instructions use curl from main:
- curl -o CLAUDE.md https://raw.githubusercontent.com/forrestchang/andrej-karpathy-skills/main/CLAUDE.md

This means:
1. Content can change at any time without notice.
2. No way to pin a specific version.
3. No way to audit what changed between installations.
4. The project own dependency policy says "Git dependencies: pin to SHA or release tag" - this repo supports neither (no tags, and pinning to a SHA of a prompt file is unusual).

**Mitigation:** If adopted, copy the content once, commit it to the project repo, and treat it as a frozen snapshot. Never auto-fetch from main.

---

### MEDIUM-5: Repo Transfer and Commercial Promotion

**Severity: MEDIUM - Maintenance independence concern**

The repo has moved from forrestchang/andrej-karpathy-skills to multica-ai/andrej-karpathy-skills. Observations:
1. Install instructions still reference forrestchang URLs (they redirect, but this is fragile).
2. multica-ai is the author company. The README now promotes "Multica" - "an open-source platform for running and managing coding agents with reusable skills."
3. The repo is now associated with a commercial product, raising questions about whether future changes will bias toward Multica interests.
4. 28 commits over the repo lifetime suggest low active maintenance.

**Mitigation:** If adopted, fork to a project-controlled repo to ensure independence from commercial entity decisions.

---

### LOW-1: Prompt Dilution Risk

**Severity: LOW - Aggregate effect concern**

The project system prompt is already large:
- AGENTS.md: 206 lines
- rules.md: extensive (MCP servers, agents, effort matrix, routing)
- shell-strategy.md: extensive (non-interactive shell patterns)
- Ponytail: active plugin (~100 lines of behavioral rules)
- CONSTITUTION.md: prose version of law layer
- constitution.json: machine-readable law layer

Adding CLAUDE.md (65 lines / 2.3KB) or CLAUDE.md + EXAMPLES.md (587 lines / 16.8KB) increases prompt token consumption. More critically, it adds competing authority:

The project defines: Authority: user > code > AGENTS.md > rules.md > memory > handoffs

Karpathy CLAUDE.md is not in this hierarchy. If appended to AGENTS.md, it becomes part of AGENTS.md authority. If placed elsewhere, its priority is undefined. Either way, more rules = more dilution = lower compliance per rule.

**Mitigation:** Do not append verbatim. Extract only the 2 principles not already covered by Ponytail/ZRP (Think Before Coding, Surgical Changes - see CONFLICTS section below). This adds approximately 20 lines instead of 65.

---

### LOW-2: Internal Contradiction - Ask vs. Loop Autonomously

**Severity: LOW - Resolvable but worth noting**

- **Think Before Coding** says: "If uncertain, ask." "If something is unclear, stop. Name what is confusing. Ask."
- **Goal-Driven Execution** says: "Strong success criteria let you loop independently." "LLMs are exceptionally good at looping until they meet specific goals."

These create a tension: when should the model stop and ask, vs. when should it define success criteria and loop autonomously? The boundary is implied (ask when ambiguous, loop when clear) but never stated explicitly.

The project rules.md resolves this with task classification: Tiny/Simple = just do it; Moderate+ = Explorer first, then execute. Karpathy guidelines lack this explicit routing.

**Mitigation:** Use rules.md task classification as the router. Think Before Coding applies to Moderate+ tasks. Tiny/Simple tasks proceed without clarification overhead.
---

## CONFLICTS WITH EXISTING PROJECT RULES

### Conflict-1: Type Annotations (DIRECT CONFLICT)

| Karpathy (EXAMPLES.md) | TelegramHelper (AGENTS.md) |
|---|---|
| Adding type hints is a "wrong" drive-by refactoring; "no type hints" is the correct surgical approach | "Type annotations on ALL public functions/methods (pyright strict mode)" |

**Resolution:** AGENTS.md wins. Never import EXAMPLES.md. Add explicit override if CLAUDE.md is adopted.

### Conflict-2: Dead Code Deletion (DIRECT CONFLICT)

| Karpathy (Surgical Changes) | Ponytail (active, full mode) |
|---|---|
| "If you notice unrelated dead code, mention it - do not delete it." "Do not remove pre-existing dead code unless asked." | "Deletion over addition. Boring over clever." "Shortest working diff wins." |

**Resolution:** Ponytail wins for unambiguously dead code (verified via serena_find_referencing_symbols). Karpathy "mention only" applies to code with uncertain usage.

### Conflict-3: Error Handling Scope (PARTIAL CONFLICT)

| Karpathy (Simplicity First) | TelegramHelper (AGENTS.md) |
|---|---|
| "No error handling for impossible scenarios." | "Error handling: log every exception. NO bare except: pass." "Graceful shutdown: every component must have cleanup with timeouts." |

**Resolution:** Define "impossible" as "proven unreachable by type system / control flow analysis." All runtime error paths (even unlikely ones) must be logged per AGENTS.md.

### Conflict-4: Test-First Mandate Scope (PARTIAL CONFLICT)

| Karpathy (Goal-Driven Execution) | TelegramHelper (rules.md) |
|---|---|
| "Write a test that reproduces it, then make it pass" - always test-first | Tiny: targeted test + typecheck for touched area only |

**Resolution:** Goal-Driven Execution applies to Simple+ tasks. Tiny tasks follow rules.md lighter validation.

### Conflict-5: Input Validation as "Drive-By" (PARTIAL CONFLICT)

| Karpathy (EXAMPLES.md) | Ponytail + Security Requirements |
|---|---|
| Adding username validation (length, alphanumeric) while fixing an email bug = "wrong" drive-by | "Never simplify away: input validation at trust boundaries" (Ponytail) |

**Resolution:** Security-relevant validation at trust boundaries is never a "drive-by." R5 security reviewer has authority to flag and fix validation gaps.

### Conflict-6: Authority Hierarchy (STRUCTURAL CONFLICT)

| Karpathy (CLAUDE.md) | TelegramHelper (AGENTS.md) |
|---|---|
| No defined priority - designed as standalone or "merged with project-specific instructions" | Explicit hierarchy: user > code > AGENTS.md > rules.md > memory > handoffs |

**Resolution:** If adopted, integrate into AGENTS.md as a subsection with explicit subordination to protected invariants. Never equal authority to constitution.
### Conflict-7: Redundancy with Ponytail (REDUNDANCY - approximately 50% overlap)

| Karpathy Principle | Ponytail Equivalent | Overlap |
|---|---|---|
| Simplicity First: "Minimum code that solves the problem. Nothing speculative." | YAGNI ladder: "Does this need to exist at all?" + "No unrequested abstractions" + "Shortest working diff wins" | ~90% overlap |
| Goal-Driven Execution: "Define success criteria. Loop until verified." | Zero-Risk Pipeline: loop until 0 problems + Goal Judge: JSON {ok, impossible, reason} | ~70% overlap (ZRP is more rigorous) |
| Think Before Coding: "State assumptions. Present interpretations. Ask when confused." | rules.md: use sub-agents for non-trivial work + Explorer agent | ~40% overlap (different mechanism, similar intent) |
| Surgical Changes: "Touch only what you must. Do not improve adjacent code." | Ponytail: "Shortest working diff wins" + "No boilerplate, no scaffolding" | ~30% overlap (Surgical Changes is more specific about not touching adjacent code) |

**Net new content from Karpathy:** approximately 40% (Think Before Coding "surface tradeoffs" + Surgical Changes "do not touch adjacent code" + Goal-Driven "transform tasks to verifiable goals")

### Conflict-8: Redundancy with Zero-Risk Pipeline (REDUNDANCY)

| Karpathy (Goal-Driven Execution) | ZRP (AGENTS.md) |
|---|---|
| "Transform tasks into verifiable goals" | D5: 5 parallel debuggers + R5: 5 parallel reviewers |
| "Loop until verified" | "Loop until 0 problems. Maximum 10 iterations" |
| "Strong success criteria let you loop independently" | Goal Judge: independent model, JSON {ok, impossible, reason} |

**Assessment:** ZRP is strictly more rigorous. Goal-Driven Execution is a lighter subset. For Tiny/Simple tasks where ZRP is skipped, Goal-Driven adds value. For Moderate+ tasks, it is fully redundant.

---

## ADDITIONAL OBSERVATIONS

### Observation-1: The Escape Hatch Is Fragmented Across Files

The "trivial task" escape hatch appears in three places with varying specificity:
1. CLAUDE.md header: "For trivial tasks, use judgment." (vague, no definition)
2. README "Tradeoff Note": "For trivial tasks (simple typo fixes, obvious one-liners), use judgment." (concrete examples, but in README, not CLAUDE.md)
3. EXAMPLES.md: No mention of trivial tasks at all - all examples are non-trivial.

If a user installs only CLAUDE.md (as the install instructions suggest), they get the vague caveat. If they also read the README, they get concrete examples. If they internalize EXAMPLES.md, they see no escape hatch at all - every example applies full rigor.

### Observation-2: EXAMPLES.md Is 522 Lines - Too Large for Prompt Inclusion

If the full EXAMPLES.md were included in the system prompt, it would add approximately 14.5KB (approximately 3,600 tokens). Combined with the existing system prompt, this risks context window pressure and instruction dilution. The examples are valuable for human understanding but impractical as persistent model instructions.

### Observation-3: The "200 lines could be 50" Rule Is Arbitrary

Simplicity First states: "If you write 200 lines and it could be 50, rewrite it." The numbers 200 and 50 are arbitrary heuristics. For some tasks, 200 lines is the minimum viable implementation (e.g., a SQLAlchemy model with relationships, validators, and repository methods). For others, 50 lines is already over-engineered. The rule spirit (rewrite if overcomplicated) is sound, but the specific numbers could mislead.

### Observation-4: No Mechanism for Measuring "Working"

The README says "These guidelines are working if you see: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, clarifying questions come before implementation, clean, minimal PRs." But there is:
- No tool to measure diff noise
- No baseline to compare against
- No definition of "unnecessary" changes
- No tracking mechanism for clarification timing
- No "minimal PR" size threshold

This is aspiration, not measurement. The project .metrics.json could be extended to track these, but Karpathy guidelines provide no methodology.
---

## CONFIDENCE ASSESSMENT

**Overall confidence: HIGH**

- **CLAUDE.md content:** Verified directly from GitHub blob page (65 lines, 4 principles). HIGH confidence.
- **EXAMPLES.md content:** Verified directly from GitHub blob page (522 lines, 8 examples + anti-patterns summary). HIGH confidence. The type-hint removal example is verbatim.
- **No LICENSE file:** Verified from repository file tree (no LICENSE file in listing). HIGH confidence.
- **Repo transfer (forrestchang to multica-ai):** Verified - all URLs redirect from forrestchang to multica-ai. HIGH confidence.
- **Star/commit counts:** Verified from repo page (180k stars, 18.4k forks, 28 commits, 0 releases). HIGH confidence.
- **Conflicts with project rules:** Verified against AGENTS.md content (lines 49-60 for coding constraints, line 21 for authority hierarchy). HIGH confidence.
- **Ponytail rules:** Verified from system prompt Ponytail section (deletion over addition, YAGNI ladder, trust-boundary validation protection). HIGH confidence.
- **Efficacy assessment (no evidence):** Inferred from absence of tests, benchmarks, or studies in the repo. MEDIUM-HIGH confidence (absence of evidence is not absolute proof of no evidence, but extensive search of repo content found none).

---

## COMPARISON WITH ITERATION-01 FINDINGS

Iteration-01 identified 12 risks. Iteration-02 deepens and adds:

**New findings in iteration-02 (not in iteration-01):**
1. EXAMPLES.md explicitly removes type hints as "correct" (CRITICAL-1) - iteration-01 mentioned the type annotation conflict theoretically; iteration-02 found the concrete example in EXAMPLES.md.
2. EXAMPLES.md treats input validation as "wrong" drive-by (HIGH-3) - security implication not raised in iteration-01.
3. No versioning / no tags / no releases (MEDIUM-4) - supply chain risk not raised in iteration-01.
4. Repo transfer to commercial entity multica-ai (MEDIUM-5) - iteration-01 noted the transfer but not the commercial promotion aspect.
5. The escape hatch fragmentation across files (Observation-1) - iteration-01 noted the lack of definition but not the file fragmentation.
6. The "200 lines could be 50" arbitrary heuristic (Observation-3) - not raised in iteration-01.
7. No measurement mechanism (Observation-4) - iteration-01 noted no quantitative evidence but did not analyze the "How to Know It Working" section specifically.
8. Prompt dilution quantified (LOW-1) - iteration-01 noted the risk; iteration-02 quantifies the token cost.
9. Internal contradiction analysis (LOW-2) - iteration-01 raised this; iteration-02 provides the resolution mechanism.
10. Redundancy quantified per-principle (Conflict-7) - iteration-01 noted overlap; iteration-02 quantifies it per principle (90%, 70%, 40%, 30%).

**Deepened findings (present in both but expanded in iteration-02):**
- LICENSE issue: iteration-01 called it "minor legal hygiene"; iteration-02 escalates to CRITICAL with Berne Convention analysis.
- Dead code: iteration-01 called it "accumulation risk"; iteration-02 identifies the direct Ponytail conflict and "broken windows" pattern.
- Error handling: iteration-01 noted the tension; iteration-02 lists 6 specific distributed-system scenarios that could be misclassified as "impossible."

---

## RECOMMENDATION (Devil's Advocate Position)

**Do NOT adopt karpathy-skills verbatim.** The EXAMPLES.md directly conflicts with protected invariants (type hints), and the full CLAUDE.md is approximately 60% redundant with existing Ponytail + ZRP rules.

**If anything is adopted, extract only:**
1. **Think Before Coding** (the "surface assumptions and tradeoffs" principle) - approximately 15 lines, not covered by Ponytail or ZRP.
2. **Surgical Changes** (the "do not touch adjacent code" rule) - approximately 10 lines, partially covered by Ponytail but more specific.

**Skip entirely:**
- Simplicity First - redundant with Ponytail YAGNI ladder
- Goal-Driven Execution - redundant with Zero-Risk Pipeline
- EXAMPLES.md - contains type-hint removal conflict, too large for prompt
- The curl-based install - no versioning, no LICENSE file

**Net addition: approximately 25 lines to AGENTS.md, explicitly subordinated to protected invariants.**

---

## SOURCES

1. CLAUDE.md - fetched from https://github.com/multica-ai/andrej-karpathy-skills/blob/main/CLAUDE.md (65 lines, 44 loc, 2.3KB)
2. README.md - fetched from https://github.com/multica-ai/andrej-karpathy-skills/blob/main/README.md (171 lines, 109 loc, 6.05KB)
3. EXAMPLES.md - fetched from https://github.com/multica-ai/andrej-karpathy-skills/blob/main/EXAMPLES.md (522 lines, 392 loc, 14.5KB)
4. Repository file tree - verified from https://github.com/multica-ai/andrej-karpathy-skills (no LICENSE file present)
5. TelegramHelper AGENTS.md - read locally (lines 1-80 verified, 206 total lines)
6. Ponytail rules - from system prompt configuration (full mode active)
7. Iteration-01 devil's advocate - read from .opencode/memory/research/karpathy-skills/iteration-01/raw/03-devils-advocate.md (66 lines)
