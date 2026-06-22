# Deep Dive - Andrej Karpathy Skills (Technical Perspective)

**Researcher 2 - Deep Dive (Technical)**
**Iteration 02**
**Repository:** https://github.com/forrestchang/andrej-karpathy-skills (GitHub redirects/aliases to `multica-ai/andrej-karpathy-skills`)
**Date:** 2026-06-22

---

## 0. Sources Fetched & Provenance

| File | Path | Size | Status |
|------|------|------|--------|
| CLAUDE.md | repo root | 65 lines / 2.3 KB | Fetched (full) |
| EXAMPLES.md | repo root | 522 lines / 14.5 KB | Fetched (full) |
| SKILL.md | `skills/karpathy-guidelines/SKILL.md` | 67 lines / 2.46 KB | Fetched (full) |
| README.md | repo root | - | Fetched (context only) |
| CURSOR.md | repo root | 28 lines / 1.91 KB | Fetched (context only) |

**Important structural correction:** `SKILL.md` is NOT at the repository root. The repository's root listing is:
`.claude-plugin/`, `.cursor/rules/`, `skills/karpathy-guidelines/`, `CLAUDE.md`, `CURSOR.md`, `EXAMPLES.md`, `README.md`, `README.zh.md`.
The skill file lives at `skills/karpathy-guidelines/SKILL.md` and is the "reusable Agent Skill" form (with YAML front-matter). `CLAUDE.md` is the per-project instruction form. `SKILL.md`'s body text is **byte-identical** to `CLAUDE.md`'s four-principle body - the only differences are the YAML front-matter block:

```yaml
name: karpathy-guidelines
description: Behavioral guidelines to reduce common LLM coding mistakes. Use when writing, reviewing, or refactoring code to avoid overcomplication, make surgical changes, surface assumptions, and define verifiable success criteria.
license: MIT
```

Per `CURSOR.md`'s contributor note, the same principle text is maintained in three places that must stay in sync: `CLAUDE.md`, `.cursor/rules/karpathy-guidelines.mdc`, and `skills/karpathy-guidelines/SKILL.md`.

### Origin (from README.md)
The four principles are derived from Andrej Karpathy's public observations on LLM coding pitfalls. Three original quotes are cited in the README, each mapping to a principle:

1. "The models make wrong assumptions on your behalf and just run along with them without checking. They don't manage their confusion, don't seek clarifications, don't surface inconsistencies, don't present tradeoffs, don't push back when they should." -> **Principle 1 (Think Before Coding)**
2. "They really like to overcomplicate code and APIs, bloat abstractions, don't clean up dead code... implement a bloated construction over 1000 lines when 100 would do." -> **Principle 2 (Simplicity First)** (+ partially Principle 3)
3. "They still sometimes change/remove comments and code they don't sufficiently understand as side effects, even if orthogonal to the task." -> **Principle 3 (Surgical Changes)**

The README's headline insight, also attributed to Karpathy, grounds Principle 4:
"LLMs are exceptionally good at looping until they meet specific goals... Don't tell it what to do, give it success criteria and watch it go." -> **Principle 4 (Goal-Driven Execution)**

### Stated tradeoff (appears in both CLAUDE.md and SKILL.md)
> "These guidelines bias toward caution over speed. For trivial tasks, use judgment."

This is an explicit escape hatch so the rules do not slow down obvious one-liners / typo fixes.

---

## 1. PRINCIPLE 1 - Think Before Coding

### Canonical text (CLAUDE.md / SKILL.md)
> Don't assume. Don't hide confusion. Surface tradeoffs.
> Before implementing:
> - State your assumptions explicitly. If uncertain, ask.
> - If multiple interpretations exist, present them - don't pick silently.
> - Push back when warranted. If a simpler approach exists, say so.
> - If something is unclear, stop. Name what's confusing. Ask.

### Technical breakdown
This principle attacks the **silent-interpretation failure mode**: an LLM receives an underspecified request, picks one of several plausible readings, and commits to it without surfacing the choice. The cost is paid downstream when the chosen interpretation is wrong - the user only discovers the mismatch after code is already written.

The principle prescribes a **pre-implementation dialogue** with four distinct moves:

| Move | What it prevents | Mechanism |
|------|------------------|-----------|
| State assumptions explicitly | Hidden premises that skew the whole design | Enumerate assumptions as a list before code |
| Present multiple interpretations | Silent selection among ambiguous readings | Lay out 2-N interpretations, don't pre-pick |
| Push back / propose simpler approach | Over-engineered response to a simple ask | Name the simpler path explicitly |
| Stop & ask when confused | Fabricated progress on an unclear task | Name the confusion, halt, request clarification |

Crucially, "push back when warranted" is bidirectional: the LLM should not only ask but also *recommend*, including recommending *against* a complex approach the user implied. This is an active-skeptic posture, not passive compliance.

### Concrete example (from EXAMPLES.md)
**Request:** "Add a feature to export user data"
- Wrong: immediately writes `export_users(format='json')` that (a) exports ALL users, (b) writes to a hard-coded file path, (c) picks CSV fieldnames without checking the data shape, (d) assumes synchronous in-process file write.
- Right: before coding, surfaces scope (all vs filtered subset - privacy implications), format intent (browser download vs background job vs API endpoint), which fields (some may be sensitive), expected volume (affects approach). Then proposes the simplest viable path ("Add an API endpoint that returns paginated JSON") and asks for a preference.

A second example ("Make the search faster") shows the multi-interpretation move: "faster" could mean (1) lower response latency (<100ms vs 500ms, ~2h), (2) higher throughput/concurrency (~4h), or (3) faster perceived speed via progressive loading (~3h). Each interpretation has a different effort and a different solution. The wrong behavior is to silently build 200 lines of caching + indexing + async.

### Anti-pattern / fix (EXAMPLES.md summary table)
- Anti-pattern: "Silently assumes file format, fields, scope"
- Fix: "List assumptions explicitly, ask for clarification"

---

## 2. PRINCIPLE 2 - Simplicity First

### Canonical text
> Minimum code that solves the problem. Nothing speculative.
> - No features beyond what was asked.
> - No abstractions for single-use code.
> - No "flexibility" or "configurability" that wasn't requested.
> - No error handling for impossible scenarios.
> - If you write 200 lines and it could be 50, rewrite it.
> Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### Technical breakdown
This principle targets **premature complexity** - the LLM tendency to build for hypothetical future requirements. It enumerates five forbidden categories of speculative code:

1. **Unrequested features** - functionality beyond the ask.
2. **Single-use abstractions** - interfaces/ABCs/factories with one implementation.
3. **Unrequested flexibility/configurability** - knobs and options nobody asked to turn.
4. **Impossible-scenario error handling** - guards for states that cannot occur.
5. **Bloat** - the 200-line version of a 50-line solution.

The principle includes a **quantitative rewrite trigger** ("If you write 200 lines and it could be 50, rewrite it") and a **subjective senior-engineer heuristic** as a self-check. This dual check (ratio + gut) is notable: the ratio catches mechanical bloat, the gut-check catches "technically correct but absurdly over-structured."

Importantly, the principle does NOT say "never abstract" - it says "no abstractions for single-use code." The escape valve is explicit: add complexity *when the requirement actually arrives*, not in anticipation. EXAMPLES.md states this directly: "When to add complexity: Only when you actually need multiple discount types. If that requirement comes later, refactor then."

### Concrete examples (from EXAMPLES.md)
**Example A - Over-abstraction.** Request: "Add a function to calculate discount."
- Wrong: 30+ lines - `DiscountStrategy(ABC)` with `@abstractmethod`, `PercentageDiscount`, `FixedDiscount`, a `DiscountConfig` dataclass (min_purchase, max_discount), a `DiscountCalculator` class, and "30+ lines of setup for a simple calculation."
- Right:
  ```python
  def calculate_discount(amount: float, percent: float) -> float:
      """Calculate discount amount. percent should be 0-100."""
      return amount * (percent / 100)
  ```
  One function, one docstring, one return.

**Example B - Speculative features.** Request: "Save user preferences to database."
- Wrong: a `PreferenceManager` class with constructor-injected `cache`, `validator`, and a `save()` method carrying `merge`, `validate`, `notify` flags, plus a stub `notify_preference_change` ("Another 30 lines for a feature nobody asked for").
- Right:
  ```python
  def save_preferences(db, user_id: int, preferences: dict):
      """Save user preferences to database."""
      db.execute(
          "UPDATE users SET preferences = ? WHERE id = ?",
          (json.dumps(preferences), user_id)
      )
  ```
- Explicit "add later if needed" list: caching (when performance matters), validation (when bad data appears), merging (when requirement emerges). Each deferred feature is tied to the *signal* that would justify it.

### Anti-pattern / fix
- Anti-pattern: "Strategy pattern for single discount calculation"
- Fix: "One function until complexity is actually needed"

---

## 3. PRINCIPLE 3 - Surgical Changes

### Canonical text
> Touch only what you must. Clean up only your own mess.
> When editing existing code:
> - Don't "improve" adjacent code, comments, or formatting.
> - Don't refactor things that aren't broken.
> - Match existing style, even if you'd do it differently.
> - If you notice unrelated dead code, mention it - don't delete it.
> When your changes create orphans:
> - Remove imports/variables/functions that YOUR changes made unused.
> - Don't remove pre-existing dead code unless asked.
> The test: Every changed line should trace directly to the user's request.

### Technical breakdown
This principle addresses **collateral/diff blast radius**. It is the most rule-dense of the four because it must distinguish *your* mess from *pre-existing* mess. The rules split into two groups:

**A. Restraint on what you touch:**
- No "drive-by" improvements to adjacent code, comments, or formatting.
- No refactoring of working-but-ugly code.
- Match existing style even when you disagree (quotes, type hints, spacing, boolean patterns).
- Unrelated dead code: **mention, do not delete.**

**B. Ownership of your own orphans:**
- Remove imports/vars/functions that *your* change rendered unused.
- Do NOT remove pre-existing dead code unless explicitly asked.

The asymmetry is deliberate: you own the collateral damage of your own edit, but you do **not** own the codebase's pre-existing hygiene debt. The reason is reviewability - a PR where "every changed line traces directly to the user's request" is trivial to review; a PR that also "cleans up" unrelated things mixes concerns and hides risk.

The closing line - "**Every changed line should trace directly to the user's request**" - is the operational test for this principle. It is the single most quotable, auditable sentence in the whole document.

### Concrete examples (from EXAMPLES.md)
**Example A - Drive-by refactoring.** Request: "Fix the bug where empty emails crash the validator."
- Wrong (drive-by): besides the empty-email fix, also "improved" email validation (added `.`-in-domain check), added username length + alphanumeric validation that nobody asked for, changed comments, added a docstring.
- Right (surgical): only the two lines that fix empty-email handling changed - introduce `email = user_data.get('email', '')` and guard `not email or not email.strip()`, then use `email` in the `@` check. Everything else untouched.

**Example B - Style drift.** Request: "Add logging to the upload function."
- Wrong (reformat everything): changed quote style (`'` to `"`), added type hints (`file_path: str` ... `-> bool`), added a docstring, reformatted whitespace, rewrote the boolean return logic, replaced `print` with `logger.exception` AND restructured the success/failure branches.
- Right (match existing style): add `import logging` + module logger; insert `logger.info(...)` lines at the existing success/failure/except points; replace only the `print(...)` with `logger.exception(...)` - all while keeping single quotes, no type hints, the existing `if 200: return True / else: return False` structure, and original spacing.

### Anti-pattern / fix
- Anti-pattern: "Reformats quotes, adds type hints while fixing bug"
- Fix: "Only change lines that fix the reported issue"

---

## 4. PRINCIPLE 4 - Goal-Driven Execution

### Canonical text
> Define success criteria. Loop until verified.
> Transform tasks into verifiable goals:
> - "Add validation" -> "Write tests for invalid inputs, then make them pass"
> - "Fix the bug" -> "Write a test that reproduces it, then make it pass"
> - "Refactor X" -> "Ensure tests pass before and after"
> For multi-step tasks, state a brief plan:
> 1. [Step] -> verify: [check]
> 2. [Step] -> verify: [check]
> 3. [Step] -> verify: [check]
> Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

### Technical breakdown
This principle operationalizes Karpathy's headline insight ("LLMs are exceptionally good at looping until they meet specific goals"). The mechanism is a **declarative reformulation** of imperative tasks into verification-anchored goals:

| Imperative (weak) | Declarative/verifiable (strong) |
|-------------------|---------------------------------|
| "Add validation" | "Write tests for invalid inputs, then make them pass" |
| "Fix the bug" | "Write a test that reproduces it, then make it pass" |
| "Refactor X" | "Ensure tests pass before and after" |

The pattern is consistently **test-first**: the test is the success criterion, not an afterthought. For multi-step work, the prescribed plan format is rigid and valuable:

```
1. [Step] -> verify: [check]
2. [Step] -> verify: [check]
3. [Step] -> verify: [check]
```

Each step carries its own verification hook. This turns a vague request into a **self-driving loop**: with strong criteria the LLM can iterate independently until green; with weak criteria ("make it work") it must ping the user for clarification at every turn. The principle is essentially a **specification discipline** - the success criterion must be machine-checkable (ideally a test).

### Concrete examples (from EXAMPLES.md)
**Example A - Vague vs verifiable.** Request: "Fix the authentication system."
- Wrong: "I'll review the code, identify issues, make improvements, test the changes" - then proceeds with no success criteria.
- Right: pin the specific issue (e.g. "users stay logged in after password change"), then a 4-step plan: (1) write a test that reproduces the bug -> verify test fails; (2) implement session invalidation on password change -> verify test passes; (3) cover edge cases (multiple sessions, concurrent changes) -> verify; (4) run full auth suite -> verify no regression.

**Example B - Multi-step with verification.** Request: "Add rate limiting to the API."
- Wrong: one 300-line commit with Redis + multiple strategies + config + monitoring, no verification steps.
- Right: an incremental 4-step plan where each step is independently verifiable AND deployable:
  1. In-memory limiter on one endpoint -> verify (100 requests, first 10 succeed, rest 429; manual curl x11).
  2. Extract to middleware across endpoints -> verify (limits apply to /users and /posts; existing tests pass).
  3. Add Redis backend for multi-server -> verify (limit survives restart; two instances share counter).
  4. Per-endpoint configuration -> verify (/search 10/min, /users 100/min; config parsed correctly).

**Example C - Test-first verification.** Request: "The sorting breaks when there are duplicate scores."
- Wrong: immediately rewrites the sort key without confirming the bug.
- Right: (1) write a test that reproduces non-deterministic ordering for duplicate scores; run it 10x to confirm it fails inconsistently; (2) fix with a stable sort (`key=lambda x: (-x['score'], x['name'])`); (3) verify the test passes consistently. Reproduce-first, fix-second.

### Anti-pattern / fix
- Anti-pattern: "I'll review and improve the code"
- Fix: "Write test for bug X -> make it pass -> verify no regressions"

---

## 5. EXAMPLES.md - Consolidated Before/After Pattern Analysis

EXAMPLES.md contains **9 worked examples** (2 for Principles 1-3, 3 for Principle 4), each with a "what LLMs do wrong" block and a "what should happen" block, plus a closing anti-patterns summary table and a "Key Insight."

### Pattern matrix

| # | Principle | Request | Wrong move | Right move |
|---|-----------|---------|------------|------------|
| 1.1 | Think Before Coding | "Export user data" | Hard-codes scope/path/fields silently | Surfaces scope/format/fields/volume, proposes simplest path, asks |
| 1.2 | Think Before Coding | "Make search faster" | 200 lines of cache+index+async | Presents 3 interpretations (latency/throughput/UX) with effort estimates |
| 2.1 | Simplicity First | "Calculate discount" | ABC + 2 strategies + config + calculator (30+ lines) | One 2-line function |
| 2.2 | Simplicity First | "Save preferences" | Manager class w/ merge+validate+notify flags | One function, one UPDATE; defers cache/validate/merge w/ trigger signals |
| 3.1 | Surgical Changes | "Fix empty email crash" | Also adds username validation, changes comments, adds docstring | Only the 2 lines fixing empty-email handling |
| 3.2 | Surgical Changes | "Add logging to upload" | Reformats quotes, adds type hints, restructures returns | Inserts log lines; matches single-quote/no-hint style |
| 4.1 | Goal-Driven | "Fix auth system" | "Review and improve" w/ no criteria | Pins specific bug, 4-step verify-each plan |
| 4.2 | Goal-Driven | "Add rate limiting" | One 300-line commit | 4 incrementally-verifiable, deployable steps |
| 4.3 | Goal-Driven | "Sort breaks on dupes" | Rewrites sort key immediately | Reproduce w/ test x10 first, then fix, then verify |

### Recurring before/after structural patterns
Across all 9 examples the "wrong" and "right" versions follow consistent shapes:

**Wrong (recurring shape):**
1. **Silent scope expansion** - assumes the largest/most-general interpretation.
2. **Framework-first** - reaches for ABC/strategy/factory/config before a plain function.
3. **Flag proliferation** - boolean knobs (`merge`, `validate`, `notify`) that generalize a single ask.
4. **Drive-by editing** - touches adjacent code, style, comments, signatures while "in there."
5. **No verifiable exit** - "review and improve" with no test or check that defines done.

**Right (recurring shape):**
1. **Dialogue before code** - lists assumptions/interpretations, asks.
2. **One function first** - the smallest unit that satisfies the literal request.
3. **Deferred complexity w/ trigger** - names the feature AND the signal that would justify adding it later.
4. **Minimal diff, matched style** - only lines that trace to the request; existing conventions preserved.
5. **Test-as-definition-of-done** - reproduce -> fix -> verify, with a per-step verification hook.

### Anti-patterns summary table (verbatim from EXAMPLES.md)

| Principle | Anti-Pattern | Fix |
|-----------|--------------|-----|
| Think Before Coding | Silently assumes file format, fields, scope | List assumptions explicitly, ask for clarification |
| Simplicity First | Strategy pattern for single discount calculation | One function until complexity is actually needed |
| Surgical Changes | Reformats quotes, adds type hints while fixing bug | Only change lines that fix the reported issue |
| Goal-Driven | "I'll review and improve the code" | "Write test for bug X -> make it pass -> verify no regressions" |

### Key Insight (verbatim paraphrase from EXAMPLES.md)
The "overcomplicated" examples are **not obviously wrong** - they follow design patterns and best practices. The problem is **timing**: they add complexity *before it is needed*, which makes code harder to understand, introduces more bugs, takes longer, and is harder to test. The simple versions are easier to understand, faster to implement, easier to test, and can be refactored later when complexity is actually needed. "Good code is code that solves today's problem simply, not tomorrow's problem prematurely."

This reframes the four principles as a **timing discipline** rather than a complexity-as-bad discipline: patterns are fine, just not yet.

---

## 6. Success Metrics ("How to know it's working")
CLAUDE.md and README.md both state the operational success signals:
- Fewer unnecessary changes in diffs (only requested changes appear).
- Fewer rewrites due to overcomplication (simple the first time).
- Clarifying questions come *before* implementation, not after mistakes.
- Clean, minimal PRs (no drive-by refactoring or "improvements").

These are directly observable in a PR/diff - making the guidelines themselves evaluable via the same "verifiable criteria" ethos as Principle 4.

---

## 7. Cross-Cutting Technical Observations

1. **SKILL.md vs CLAUDE.md redundancy is intentional.** Same body, different delivery vehicles: CLAUDE.md = per-project instructions; SKILL.md = portable skill with front-matter for plugin/skill marketplaces. The `description` field in SKILL.md's front-matter is the trigger spec: "Use when writing, reviewing, or refactoring code...".

2. **The four principles form a chronological pipeline.** (1) Think -> (2) Simplify -> (3) Cut surgically -> (4) Verify by goal. They map onto the lifecycle of a single edit: before coding, while coding, while editing existing code, and while confirming done.

3. **Three of four principles end in an explicit self-check sentence.**
   - P2: "Would a senior engineer say this is overcomplicated? If yes, simplify."
   - P3: "Every changed line should trace directly to the user's request."
   - P4: "Strong success criteria let you loop independently. Weak criteria require constant clarification."
   P1 has no single closing aphorism; its check is procedural (did you list assumptions / interpretations / push back / ask?).

4. **Tradeoff is declared, not hidden.** Both CLAUDE.md and SKILL.md open with the same caveat: "These guidelines bias toward caution over speed. For trivial tasks, use judgment." This is itself an application of Principle 1 (surface the tradeoff rather than hiding it).

5. **No tooling/tech lock-in.** Examples are Python-flavored but the rules are language-agnostic. There is no dependency, no framework, no required test runner - "tests" in P4 are an abstract verification hook (could be a test, an assert, a manual curl).

6. **Tension points worth flagging for adoption:**
   - P3 ("match existing style") can conflict with P2 ("would a senior engineer call this overcomplicated") when the existing style *is* overcomplicated. The doc resolves this in favor of P3 for *edits* (match) and P2 for *new* code (simplify). Adopters should make this precedence explicit.
   - P4's test-first mandate assumes a testable surface; for exploratory/UI/infra work the "verify: [check]" may need to be a manual or scripted check rather than a unit test. The rate-limiting example (4.2) models this well by mixing automated tests with manual `curl` checks.
   - P1's "ask before coding" can stall in non-interactive/CI agent contexts. The doc's escape valve ("use judgment for trivial tasks") plus a "propose-then-default" policy (state assumptions, pick the simplest, proceed, note the assumption) is a practical reconciliation - and is exactly what Example 1.1 models ("Simplest approach: ... Would need more info for file-based exports. What's your preference?").

---

## 8. Confidence & Source Quality

- **Source fidelity: HIGH.** All three target files were fetched in full from the live GitHub repo (via the `github.com/.../blob/main/...` rendered pages, since `raw.githubusercontent.com` returned transport errors; the `multica-ai` alias is GitHub's canonical redirect for this repo). Content was extracted from the rendered file view, which reproduces the raw markdown body verbatim.
- **One fetch caveat:** `raw.githubusercontent.com` was unreachable (transport error) for all three files; I retrieved content via the GitHub blob HTML pages instead. The principle/example text extracted matches the README's own restatement, corroborating fidelity. SKILL.md's YAML front-matter and body were captured from its blob page.
- **Path correction confidence: HIGH.** Root `SKILL.md` returns 404; the directory listing of `skills/karpathy-guidelines/` shows `SKILL.md` as its only file. CURSOR.md confirms this location ("use `skills/karpathy-guidelines/SKILL.md`").
- **Interpretation confidence: HIGH** for principles/examples (the doc is self-explanatory and EXAMPLES.md is explicit). **MEDIUM** only for the cross-cutting observations in section 7, which are this researcher's synthesis, not verbatim repo claims.
