# 03-devils-advocate.md — context-mode (Devil's Advocate Perspective)

## Research Methods
- grep for "TODO", "FIXME", "WARN", "FAIL", "issue", "risk", "security", "limitation"
- read of LICENSE (ELv2), README "Security" and "Routing Enforcement" sections
- read of tests/core/routing.test.ts, tests/security.test.ts, tests/core/server.test.ts
- review of package.json dependencies and engine requirements

## License Risk: ELv2
- License is **Elastic License 2.0**, not MIT/Apache.
- Prohibits offering the software as a hosted or managed service to third parties.
- Prohibits removing or obscuring license/copyright notices.
- For TelegramHelper this means: we can use it internally, modify it, fork it, but cannot expose it as a SaaS feature without relicensing risk.
- If we borrow implementation ideas (not code) the license risk is lower; if we copy code, ELv2 applies.

## Security / Sandbox Risks
- `ctx_execute` runs arbitrary code in a subprocess with network access. The README calls it "destructiveHint" and "openWorldHint".
- The executor is only isolated by process boundary; a malicious prompt can still exfiltrate data via `fetch()` inside the sandbox.
- Security policies are opt-in and depend on `.claude/settings.json` format. If a project has no rules, no enforcement occurs.
- `ctx_fetch_and_index` allows loopback/RFC1918 by default (can reach internal services). Strict mode (`CTX_FETCH_STRICT=1`) blocks them but is opt-in.
- Hook failures are designed to fail-open (allow the tool). A bug in a hook could silently disable routing.
- Credentials in MCP tool arguments are redacted by regex, but regex-based redaction is not foolproof and may miss custom auth field names.

## Operational Risks
- Requires Node.js >= 22.5 or Bun. TelegramHelper is Python 3.13; integrating a Node/Bun runtime adds deployment complexity.
- Native dependency `better-sqlite3` can fail on Windows/Linux without build tools. Self-healing scripts exist but are extra surface area.
- Per-platform adapter matrix creates maintenance burden: 17+ platforms with different hook names, config formats, and limitations.
- Storage paths are scattered across `~/.claude/`, `~/.cursor/`, `~/.codex/`, etc. Multi-platform users can end up with duplicated state.

## Architectural Risks
- Heavy reliance on hooks that intercept tool calls. Many platforms have incomplete hook support (e.g. Cursor no SessionStart, Antigravity/Zed no hooks). Effectiveness drops from ~98% to ~60% on those platforms.
- Routing relies on prompt injection + model compliance. The model can ignore instructions or hallucinate tool calls.
- "Think in Code" requires the LLM to write code; weaker models may not do it correctly.
- Auto-indexed session events can grow unbounded until `ctx_purge` or 14-day cleanup.
- Snapshot is injected into system prompt; if it is too large or malformed, it can pollute context instead of saving it.

## Complexity / Maintainability Risks
- `src/server.ts` is 4893 lines; `src/store.ts` 2071; `src/session/db.ts` 1687; `tests/core/server.test.ts` 6718.
- Bundled output is required for distribution; source changes are not loaded unless `server.bundle.mjs` is deleted or rebuilt.
- Version sync touches 7 files during release.
- Many environment variables and edge-case handling (e.g. Windows non-ASCII paths, WSL bash detection, npm shims).

## What It Means for TelegramHelper
- We cannot directly embed the npm package as a dependency if we want to avoid Node in our Python stack.
- We should adopt the **ideas** (sandbox execution, FTS5 memory, routing rules, session snapshots) rather than the code when possible.
- If we do copy/adapt code, we must respect ELv2 and not offer it as a managed service.

## Sources
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\LICENSE`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\README.md` (Security, Routing Enforcement, Platform Compatibility sections)
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\package.json`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\tests\core\routing.test.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\tests\security.test.ts`
- `C:\Users\My\AppData\Local\Temp\opencode\context-mode\tests\core\server.test.ts`
