# Researcher 3: Devil's Advocate — Critical/Security Perspective
# Repository: https://github.com/nousresearch/hermes-agent
# Date: 2026-06-22
# Iteration: 02

## Executive Summary

Hermes Agent is a 199k-star, 12.5k-commit, single-tenant AI agent framework with 40+ tools
and 20+ messaging platform integrations. From a critical/security perspective, the project
exhibits several systemic risk patterns: extreme code centralization (gateway/run.py is 850KB),
a complex multi-platform authorization surface with documented fail-open history, known
credential-exfiltration vectors (GHSA-rhgp-j443-p4rf), race conditions in cron and MCP OAuth
flows, and a trust model that explicitly disclaims in-process heuristics as non-boundaries.
While the SECURITY.md is unusually honest about limitations, this honesty masks the reality
that the default deployment posture is insecure for untrusted input.

---

## 1. CRITICAL ISSUES (Severity-Ranked)

### C1. God-File Architecture: gateway/run.py is 850,982 bytes (~850KB)
**Severity: CRITICAL (architectural)**
**Evidence**: GitHub API contents listing shows `gateway/run.py` at 850,982 bytes.
The authz_mixin.py header confirms: "Extracted from gateway/run.py as part of the
god-file decomposition campaign."
**Impact**: 
- Unauditable: No human can effectively review 850KB of Python in one file
- Merge conflicts are inevitable in a 1,524-contributor repo
- Security bugs hide in the noise — the authorization logic alone was 27KB when extracted
- Testing coverage is structurally limited for a monolith
- The "decomposition campaign" is ongoing, meaning the codebase is mid-refactor
**Anti-pattern**: Single-responsibility violation at extreme scale. This is the #1
maintainability and security risk in the project.

### C2. Credential Exfiltration via Skill-declared Environment Variables (GHSA-rhgp-j443-p4rf)
**Severity: CRITICAL (security)**
**Evidence**: `tools/env_passthrough.py` lines reference GHSA-rhgp-j443-p4rf:
"A malicious skill registered ANTHROPIC_TOKEN / OPENAI_API_KEY as passthrough and
received the credential in the execute_code child process, defeating the sandbox's
scrubbing guarantee."
**Status**: Fixed with `_HERMES_PROVIDER_ENV_BLOCKLIST` denylist, but:
- The fix is a DENYLIST, not an allowlist — new provider credentials could be missing
- The blocklist is imported from `tools.environments.local` at runtime with bare `except`
- If the import fails, `_is_hermes_provider_credential()` returns `False` for EVERYTHING
- This means a failed import silently disables the entire credential scrubbing protection
**Code evidence** (env_passthrough.py):
```python
def _is_hermes_provider_credential(name: str) -> bool:
    try:
        from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST
    except Exception:
        return False  # <-- SILENT FAIL-OPEN: all credentials pass through
    return name in _HERMES_PROVIDER_ENV_BLOCKLIST
```

### C3. Authorization Fail-Open in Gateway (Historical + Structural)
**Severity: HIGH (security)**
**Evidence**: `gateway/authz_mixin.py` contains extensive comments about fail-open bugs:
- "#34515 follow-up: trusting 'open' was a fail-open" — a past security bug where
  adapters with dm_policy="open" were trusted as authorized, admitting the entire network
- The fix is complex: 27KB of authorization logic with 20+ platform-specific env vars
- Backward-compat shims add attack surface: "#15027: TELEGRAM_GROUP_ALLOWED_USERS was
  (mis)used as a chat-ID allowlist" — legacy behavior preserved
- WhatsApp alias expansion (`_expand_whatsapp_auth_aliases`) adds another auth bypass vector
- SimpleX allows matching by display name (user-controllable, spoofable)
**Risk**: The sheer complexity of the authorization code makes future fail-open bugs likely.
Each new platform integration adds another env var, another policy dimension, another
chance for a logic gap.

### C4. No Published Security Advisories Despite Known GHSA
**Severity: HIGH (transparency)**
**Evidence**: The GitHub security advisories page shows "There aren't any published
security advisories." Yet the code references GHSA-rhgp-j443-p4rf, indicating at least
one advisory was filed. Either:
- The advisory was withdrawn/kept private (reducing community awareness)
- The advisory ID in code refers to an internal tracking number, not a published GHSA
**Impact**: Users cannot assess historical vulnerability exposure. The SECURITY.md
mentions a 90-day disclosure window, but with no published advisories, there's no
public record of what was fixed or when.

### C5. OAuth MCP Lock Corruption — RuntimeError: "The current task is not holding this lock"
**Severity: HIGH (reliability + potential security)**
**Evidence**: Issue #49543 — OAuth-authenticated MCP servers drop mid-session due to
`yield-under-lock` pattern in `mcp/client/auth/oauth2.py`:
```python
async def async_auth_flow(self, request):
    async with self.context.lock:
        ...
        response = yield request  # <-- yields while holding the lock
```
When httpx closes the auth-flow from a different task, `anyio.Lock.__aexit__` raises
RuntimeError, corrupting the OAuth session.
**Additional concerns**:
- 120s default timeout means a dead connection stalls the session for 2 minutes
- Version pin drift: deployed venv had mcp 1.27.2 despite lockfile pinning 1.26.0
- Manual patches (`local-patches/`) are silently reverted by `uv sync`
- This is a supply-chain integrity issue: the running code may not match the pinned code

---

## 2. SECURITY RISKS (Detailed)

### S1. Path Traversal — Defense-in-Depth Concerns
**File**: `tools/path_security.py` (1,322 bytes)
**Analysis**:
- `validate_within_dir()` uses `Path.resolve()` + `relative_to()` — this is correct
  for symlink resolution and `..` normalization
- `has_traversal_component()` is a quick pre-check that only looks for `..` in path parts
- **Gap**: The function catches `(ValueError, OSError)` but not `RuntimeError` — on
  some platforms, `resolve()` can raise RuntimeError for non-existent paths with
  certain locale settings
- **Gap**: No TOCTOU protection — between `validate_within_dir()` returning and the
  caller acting, a symlink could be created (classic TOCTOU race)
- The docstring says "previously duplicated across skill_manager_tool, skills_tool,
  skills_hub, cronjob_tools, and credential_files" — the consolidation is good but
  indicates the codebase had (and may still have elsewhere) unvalidated path handling

### S2. Skills Execute Arbitrary Python at Import Time
**Evidence**: SECURITY.md §2.4: "skills execute arbitrary Python at import time"
The Skills Guard is explicitly called "a review aid; not a boundary."
**Risk**: A malicious skill from the community skills repository (agentskills.io)
can execute arbitrary code when imported. The only defense is operator review.
**Attack vector**: Social engineering — publish a useful-looking skill, wait for
installation, the skill runs with full agent privileges at import.

### S3. Plugin Trust Model — Full Agent Privileges
**Evidence**: SECURITY.md §2.5: "Plugins load into the agent process and run with
full agent privileges: they can read the same credentials, call the same tools,
register the same hooks, and import the same modules."
**Risk**: Plugins can:
- Read in-memory credentials (API keys, tokens)
- Register network listeners (the dashboard, kanban plugins bind HTTP sockets)
- Add new attack surface without operator knowledge
- Persist background services that survive session resets

### S4. Environment Variable Passthrough — Global Cache Staleness
**File**: `tools/env_passthrough.py`
**Issue**: `_config_passthrough` is a module-level global cached on first load:
```python
_config_passthrough: frozenset[str] | None = None
```
If the operator updates `terminal.env_passthrough` in config.yaml at runtime, the
cache is stale until process restart. This means:
- A revoked env var continues to pass through to sandboxes
- An added env var doesn't take effect until restart
- In a long-running gateway (the intended deployment), this could be days/weeks

### S5. Session IDs Are Not Authorization Boundaries
**Evidence**: SECURITY.md §2.6 rule 3: "Session identifiers are routing handles,
not authorization boundaries. Knowing another caller's session ID does not grant
access to their approvals or output."
**Risk**: If any code path fails to re-check the allowlist and relies on session ID
for authorization, it's a cross-session access bug. The 63KB `gateway/session.py`
and 10KB `gateway/session_context.py` represent a large surface for such bugs.

### S6. In-Process Heuristics Are Not Boundaries (by Design)
**Evidence**: SECURITY.md §2.2: "Nothing inside the agent process constitutes
containment — not the approval gate, not output redaction, not any pattern scanner,
not any tool allowlist."
**Implications**:
- The approval gate is a shell denylist — Turing-complete shell makes this structurally
  incomplete (explicitly acknowledged)
- Output redaction can be defeated by a motivated LLM output producer
- Skills Guard pattern matching can be bypassed
- **The default local backend provides NO isolation against adversarial LLM output**
- Operators must explicitly configure Docker/SSH/Modal/Daytona backends for isolation

### S7. WhatsApp Identity Resolution — Expanded Attack Surface
**File**: `gateway/authz_mixin.py` + `gateway/whatsapp_identity.py` (8,460 bytes)
**Risk**: WhatsApp authorization uses phone-to-LID alias expansion from "bridge session
mapping files." This means:
- The authorization check consults external files (bridge mapping) that could be
  manipulated if an attacker has filesystem access
- The `_normalize_whatsapp_identifier()` and `_expand_whatsapp_auth_aliases()` functions
  add complexity to the auth check that could harbor bypass logic
- WhatsApp phone numbers are spoofable via SIM swap attacks

### S8. Multiple Network-Exposed HTTP Surfaces
**Evidence**: SECURITY.md §2.6 lists: API server adapter, dashboard plugin, kanban
plugin HTTP endpoints, plus any plugin that binds a listening socket.
**Risk**: Each HTTP surface is a potential unauthorized-access vector. The SECURITY.md
requires allowlists for each, but:
- The dashboard defaults to loopback (good) but `--host 0.0.0.0` is a "break-glass"
  operator decision that shifts responsibility
- Plugin HTTP endpoints may not all enforce the allowlist requirement consistently
- The kanban plugin (61KB `tools/kanban_tools.py`) has its own HTTP surface

---

## 3. PERFORMANCE BOTTLENECKS

### P1. Session Bloat from Tool-Output Retention
**Evidence**: Issue #49673 — "Gateway/front-door sessions become multi-minute slow
from retained tool-output bloat" (P2 severity)
**Impact**: Long-running gateway sessions accumulate tool output in context, causing
multi-minute latency. This is a denial-of-service vector: a malicious authorized user
(or prompt-injected agent) could trigger tool calls that bloat the session until
the gateway becomes unresponsive.

### P2. Massive File Sizes → Slow Imports, High Memory
**Evidence**:
- `gateway/run.py`: 850KB
- `tools/mcp_tool.py`: 202KB
- `gateway/slash_commands.py`: 185KB
- `tools/browser_tool.py`: 170KB
- `tools/delegate_tool.py`: 139KB
- `tools/file_operations.py`: 106KB
- `gateway/config.py`: 97KB
- `tools/approval.py`: 89KB
- `gateway/stream_consumer.py`: 82KB
- `tools/file_tools.py`: 80KB
**Impact**: Python imports these as single modules. Memory footprint is high.
IDE tooling (type checkers, linters) will be slow. Code navigation is impaired.

### P3. MCP 120s Default Timeout — Session Stall
**Evidence**: Issue #49543 — 120s default timeout for MCP calls means a dead
connection blocks the session for 2 full minutes before failing.
**Impact**: In a multi-user gateway, one user's dead MCP call stalls the event loop
(if not properly isolated), affecting all users.

### P4. saveImageBuffer IPC — No Size Cap (Memory Exhaustion)
**Evidence**: Issue #49457 (type/security) — `ipcMain.handle('hermes:saveImageBuffer')`
accepts renderer-provided image data with no max-size check. Other IPC paths have
caps (`DATA_URL_READ_MAX_BYTES`, `TEXT_PREVIEW_SOURCE_MAX_BYTES`) but this one doesn't.
**Impact**: A compromised renderer or buggy path can cause memory pressure / disk
exhaustion via unbounded image buffers.

---

## 4. ERROR HANDLING GAPS

### E1. Silent Fail-Open in Credential Blocklist Import
**File**: `tools/env_passthrough.py`
```python
def _is_hermes_provider_credential(name: str) -> bool:
    try:
        from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST
    except Exception:
        return False  # ALL credentials pass through if import fails
```
This is the most dangerous error handling gap: a simple import failure (missing module,
circular import, syntax error in the module) silently disables ALL credential protection.

### E2. Bare Exception Swallows in Authorization
**File**: `gateway/authz_mixin.py`
```python
try:
    from gateway.platform_registry import platform_registry
    entry = platform_registry.get(source.platform.value)
    ...
except Exception:
    pass  # <-- silently ignores all errors in plugin platform auth lookup
```
If the platform registry has a bug, the exception is swallowed and the platform
falls through to the default-deny path. While fail-closed is safer than fail-open
here, the silent swallow means the bug is invisible.

### E3. Cron Race Condition — Title Generation vs Session Close
**Evidence**: Issue #50536 — "race condition between title generation and session
close in cron jobs" (P3, Open)
**Impact**: Cron jobs may produce inconsistent session metadata or lose title
information. Low severity but indicates insufficient locking in session lifecycle.

### E4. Windows Bootstrap Kills Gateway Silently
**Evidence**: Issue #50090 (P1, Closed) — "bootstrap-installer kills Gateway without
respawning — Telegram bot silently stops responding"
**Impact**: On Windows, the update process can kill the gateway without restart,
causing the bot to go silent. This was P1 and is marked closed, but the pattern
suggests Windows process lifecycle management is fragile.

### E5. Desktop WebSocket Disconnect — No Graceful Degradation
**Evidence**: Issue #50005 (P2, Open) — "Desktop becomes non-functional when gateway
WebSocket disconnects — no offline mode or graceful degradation"
**Impact**: A network hiccup makes the desktop app unusable. No retry, no offline
mode, no user feedback.

---

## 5. ANTI-PATTERNS (What NOT to Do)

### A1. 850KB Single File
**Anti-pattern**: `gateway/run.py` is 850KB. No file should be this large.
**Lesson**: Decompose by responsibility. The team is already doing this
("god-file decomposition campaign") but the file is still 850KB.

### A2. Denylist for Security-Critical Checks
**Anti-pattern**: `_HERMES_PROVIDER_ENV_BLOCKLIST` is a denylist of credential names.
**Lesson**: Use an allowlist. New credentials added by the project won't be in the
denylist until someone remembers to update it. An allowlist of non-sensitive vars
is safer.

### A3. Silent Fail-Open on Security Critical Path
**Anti-pattern**: `except Exception: return False` in credential check.
**Lesson**: Security-critical checks should fail CLOSED. If the blocklist can't load,
ALL env vars should be blocked from passthrough, not allowed.

### A4. Yield-Under-Lock in Async Code
**Anti-pattern**: `async with lock: ... yield ...` in OAuth auth flow.
**Lesson**: Never hold a lock across a yield point in async code. The task that
acquires the lock may not be the task that releases it.

### A5. 20+ Platform-Specific Env Vars for Authorization
**Anti-pattern**: The authz_mixin.py hardcodes env var names for 20+ platforms in
multiple dictionaries.
**Lesson**: This doesn't scale. A plugin-based auth model where each platform
registers its own auth handler would be more maintainable and less error-prone.

### A6. Backward-Compat Shims in Security Code
**Anti-pattern**: The legacy Telegram chat-ID-in-_USERS shim preserves a
misconfiguration for backward compatibility.
**Lesson**: Security code should not preserve known-broken behavior. Emit a
deprecation warning with a sunset date and eventually remove the shim.

### A7. Trusting Adapter "Open" Policy as Authorization
**Anti-pattern**: Issue #34515 showed that `dm_policy: "open"` was trusted as
authorization, admitting the entire network.
**Lesson**: "Reached the gateway" is never authorization. The default must always
be deny.

---

## 6. ATTACK SURFACE ANALYSIS

### Surface Area: 40+ Tools, 20+ Platforms

**Tools** (from directory listing):
- terminal_tool (shell execution — LLM-emitted commands)
- code_execution_tool (75KB — host subprocess code execution)
- mcp_tool (202KB — MCP server management, OAuth, subprocess spawning)
- browser_tool (170KB — browser automation)
- browser_camofox (29KB — anti-detect browser)
- browser_cdp_tool (22KB — Chrome DevTools Protocol)
- computer_use_tool (desktop control via UIA/AX)
- file_operations (106KB — read/write/patch files)
- file_tools (80KB — file management)
- delegate_tool (139KB — subagent spawning)
- discord_tool (34KB — Discord integration)
- memory_tool (45KB — persistent memory)
- image_generation_tool (61KB)
- kanban_tools (61KB — includes HTTP endpoints)
- cronjob_tools (45KB — scheduled task execution)
- credential_files (17KB — credential file management)
- mcp_oauth (31KB) + mcp_oauth_manager (27KB)
- microsoft_graph_auth + microsoft_graph_client
- homeassistant_tool (18KB — IoT control)
- feishu_doc_tool + feishu_drive_tool
- mixture_of_agents_tool (22KB)
- patch_parser (23KB — code patching)

**Platforms** (from authz_mixin.py env var map):
Telegram, Discord, WhatsApp, WhatsApp Cloud, Slack, Signal, Email, SMS,
Mattermost, Matrix, DingTalk, Feishu, WeCom, WeCom Callback, Weixin,
BlueBubbles, QQBot, Yuanbao, SimpleX, HomeAssistant, Webhook

**Network-exposed surfaces**:
- API server adapter
- Dashboard plugin (HTTP)
- Kanban plugin (HTTP endpoints)
- TUI gateway (local IPC, JSON-RPC)
- ACP adapter (local IPC)
- Each messaging platform adapter (inbound message surface)

**Subprocess spawning surfaces**:
- Terminal backend (shell commands)
- Code execution tool (Python subprocess)
- MCP server subprocesses
- Cron job scripts
- Plugin background services

### Key Attack Vectors:
1. **Prompt injection → shell execution**: LLM emits malicious shell, approval gate
   is a denylist (structurally incomplete)
2. **Prompt injection → file write**: File tools run through shell backend; path
   validation exists but has TOCTOU gaps
3. **Malicious skill → credential exfiltration**: Skill declares env vars, imports
   at load time with full agent privileges
4. **Malicious MCP server → code execution**: MCP servers are subprocesses spawned
   from the agent's environment; a compromised MCP server has process-level access
5. **Message platform spoofing**: WhatsApp (SIM swap), SimpleX (display name match),
   Telegram (anonymous admin posts with no user_id)
6. **Session ID leakage**: If session IDs leak (e.g., in logs), and any code path
   trusts them for authorization, cross-session access is possible
7. **Desktop IPC abuse**: saveImageBuffer has no size cap; other IPC paths may
   have similar gaps
8. **Supply chain**: mcp SDK version drift, manual patches silently reverted,
   1,524 contributors with varying security awareness

---

## 7. CONFIDENCE ASSESSMENT

**Overall confidence: MEDIUM-HIGH**

**High confidence findings**:
- C1 (850KB god file): Verified via GitHub API file size
- C2 (credential exfiltration fix with fail-open): Verified in source code
- C4 (no published advisories): Verified on GitHub security page
- C5 (OAuth lock corruption): Verified in issue #49543 with detailed root cause
- S2/S3 (skills/plugins execute with full privileges): Stated in SECURITY.md
- S6 (in-process heuristics are not boundaries): Stated in SECURITY.md

**Medium confidence findings**:
- C3 (authorization fail-open structural risk): Based on code analysis and
  historical bug references, but current code appears to have fixes in place
- S1 (path traversal TOCTOU): Theoretical — no evidence of exploitation
- S4 (env passthrough cache staleness): Code analysis shows global cache,
  but runtime behavior in gateway mode not verified
- P1 (session bloat DoS): Issue #49673 confirms the problem exists

**Lower confidence (inferred)**:
- S5 (session ID cross-session access): No specific bug found, but the surface
  is large (63KB session.py + 10KB session_context.py)
- S7 (WhatsApp identity spoofing): The alias expansion adds complexity but
  no concrete bypass was identified

**Limitations**:
- terminal_tool.py, mcp_tool.py (202KB), and gateway/run.py (850KB) could not
  be fetched directly due to size; analysis is based on file listings, extracted
  mixins, smaller related files, and issue reports
- No access to test suite to assess coverage
- No access to CI/CD pipeline to assess security gates
- GitHub issues search requires login for full results — some issues may be missed
- The warpgrep_github_search tool was not available; GitHub code search was used
  via webfetch instead

---

## 8. RECOMMENDATIONS FOR THE RESEARCH TEAM

1. **Prioritize C2**: The fail-open in `_is_hermes_provider_credential()` is a
   live security issue. If the import fails for any reason, ALL credentials
   pass through to sandboxes. This should be fail-closed.

2. **Examine the full mcp_tool.py** (202KB): This file manages MCP server
   lifecycle, OAuth, and subprocess spawning. It's a critical security surface
   that couldn't be fully analyzed due to size.

3. **Audit session.py** (63KB): Cross-session authorization is the highest-risk
   unexplored surface. Every code path that uses session_id for routing must
   be verified to also check the allowlist.

4. **Monitor the god-file decomposition**: Until run.py is decomposed, new
   security bugs will continue to hide in it. Track the decomposition campaign
   progress.

5. **Check supply-chain integrity**: The mcp SDK version drift (1.26.0 pinned,
   1.27.2 installed) and manual patches being silently reverted are supply-chain
   risks. Verify uv.lock enforcement.

6. **Test the approval gate bypass**: The SECURITY.md admits it's a denylist
   over Turing-complete shell. A demonstration bypass would validate the
   "not a boundary" claim and inform deployment decisions.
