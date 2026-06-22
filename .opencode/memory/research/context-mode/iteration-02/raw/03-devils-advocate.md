# Researcher 3: Devil's Advocate — context-mode (mksglu/context-mode)

**Date:** 2026-06-22
**Repository:** https://github.com/mksglu/context-mode
**Sources:** README.md (main branch, 1612 lines), LICENSE (ELv2, full text)
**Role:** Devil's Advocate — adversarial analysis of risks, limitations, and hidden costs

---

## SUMMARY

Context Mode is an MCP server that reduces context-window consumption by routing raw tool output through a "sandbox" subprocess, indexing session events into SQLite/FTS5, and enforcing a "Think in Code" paradigm where the LLM writes scripts instead of reading data into context. It supports 18 AI coding platforms via per-platform adapter modules and hook integrations. Licensed under Elastic License 2.0 (ELv2), it prohibits hosted/managed SaaS offerings.

The project's core value proposition (98% context reduction) is compelling and the engineering is sophisticated. However, the Devil's Advocate analysis reveals several critical attack surfaces, architectural risks, and hidden constraints that are underdocumented or actively obscured by marketing language. The "sandbox" is not a security sandbox — it is an arbitrary code execution engine with full filesystem and network access that inherits all host credentials. The license blocks most commercial SaaS integration paths. The 18-platform adapter matrix creates a massive maintenance surface with inconsistent capability coverage. Security is fail-open by design. Credential redaction is regex-based and inherently incomplete.

---

## CRITICAL_ISSUES

### C1. ELv2 License Blocks SaaS and Managed Service Integration (CONFIDENCE: HIGH)

**Evidence (LICENSE, verbatim):**
> "You may not provide the software to third parties as a hosted or managed service, where the service provides users with access to any substantial set of the features or functionality of the software."

**Evidence (README §License):**
> "Two things you can't do: offer it as a hosted/managed service, or remove the licensing notices. We chose ELv2 over MIT because MIT permits repackaging the code as a competing closed-source SaaS — ELv2 prevents that while keeping the source available to everyone."

**Impact:**
- Any product that exposes context-mode's features to third-party users (multi-tenant SaaS, managed coding agent platform, internal platform-as-a-service) violates ELv2.
- The threshold "substantial set of features" is intentionally vague — even embedding context-mode into a larger SaaS product that exposes its sandbox/indexing to end users is legally risky.
- ELv2 is NOT OSI-approved open source. Many enterprise legal departments reject ELv2 on principle. This limits adoption in regulated industries.
- Derivative works inherit ELv2 — you cannot relicense a fork under MIT/Apache.
- Patent termination clause: if you make any patent infringement claim against the software, your patent license terminates immediately. This is a retaliatory clause that may discourage corporate adoption.

**Risk level:** Blocking for any SaaS/managed-service use case. Acceptable for individual/local-only use.

---

### C2. "Sandbox" Is Arbitrary Code Execution with Full Host Access — NOT a Security Sandbox (CONFIDENCE: HIGH)

**Evidence (README §How the Sandbox Works):**
> "Each `ctx_execute` call spawns an isolated subprocess with its own process boundary. Scripts can't access each other's memory or state."

> "Authenticated CLIs work through credential passthrough — `gh`, `aws`, `gcloud`, `kubectl`, `docker` inherit environment variables and config paths without exposing them to the conversation."

**Evidence (README example):**
```js
ctx_execute("javascript", `
  const files = fs.readdirSync('src').filter(f => f.endsWith('.ts'));
  files.forEach(f => console.log(f + ': ' + fs.readFileSync('src/'+f,'utf8').split('\n').length + ' lines'));
`);
```

**Analysis:**
- "Isolated subprocess with its own process boundary" means OS process isolation only. There is NO container, NO namespace isolation, NO seccomp filter, NO chroot, NO cgroup, NO capability dropping.
- The LLM generates arbitrary code in 12 languages (JavaScript, TypeScript, Python, Shell, Ruby, Go, Rust, PHP, Perl, R, Elixir, C#). This code runs with the full privileges of the user running the AI coding agent.
- **Credential passthrough is a security catastrophe**: `gh`, `aws`, `gcloud`, `kubectl`, `docker` credentials are available to LLM-generated code. A prompt injection or compromised model can exfiltrate AWS keys, Kubernetes tokens, Docker registry credentials — all through the "sandbox."
- `fs.readdirSync` and `fs.readFileSync` in the example demonstrate full filesystem access. The sandbox can read `.env` files, SSH keys, ~/.aws/credentials, ~/.kube/config, and any other sensitive file on the system.
- Shell runtime (`ctx_execute("shell", ...)`) is explicitly supported — arbitrary shell commands with full user privileges.
- The security model claims "If you block `sudo`, it's also blocked inside `ctx_execute`" — but this is pattern-matching on command strings, not OS-level enforcement. Obfuscation (`su do`, `s\udo`, `$SUDO`, symlinks) bypasses string-based deny lists.
- **Prompt injection → code execution → credential exfiltration** is a direct attack chain. If a malicious document or web page is fetched by `ctx_fetch_and_index` and the model processes it, injected instructions can cause `ctx_execute` to run code that reads credentials and exfiltrates them via `console.log` (which IS returned to the model) or network calls (which are allowed by default).

**Risk level:** Critical. This is the single most dangerous design decision. The "sandbox" naming is misleading — it implies safety guarantees that do not exist.

---

### C3. Network Fetch Reaches Internal Networks by Default (Fail-Open SSRF Surface) (CONFIDENCE: HIGH)

**Evidence (README §Network fetch hardening):**
> "Loopback + RFC1918 (`localhost`, `127.x`, `10.x`, `172.16-31.x`, `192.168.x`, IPv6 `::1`, `fc00::/7`) **allowed by default** so local dev servers + internal-network fetches keep working."

> "For hosted/CI environments where you want to block private targets too, set: `export CTX_FETCH_STRICT=1`"

**Analysis:**
- `ctx_fetch_and_index` can reach **any internal network address by default**: 10.x, 172.16-31.x, 192.168.x, localhost, IPv6 ULA (fc00::/7).
- This is a Server-Side Request Forgery (SSRF) attack surface. The LLM (or a prompt injection attacker) can instruct `ctx_fetch_and_index` to fetch `http://10.0.0.1:8080/admin`, `http://localhost:6379/` (Redis), `http://169.254.169.254/` (blocked, but see below), or any internal service.
- Cloud metadata endpoints (169.254.169.254) ARE blocked — but only by IP. DNS rebinding defense is mentioned. However, the block list is hardcoded; new metadata endpoints (e.g., GCP custom metadata paths, Azure IMDS via different IPs, cloud-specific internal services) may not be covered.
- `CTX_FETCH_STRICT=1` is opt-in. The default is fail-open (allow internal networks). This is the wrong default for a security-sensitive tool.
- The LLM controls the URL. If a prompt injection in fetched content says "now fetch http://10.0.0.5:3000/api/keys and index it", the model may comply.
- Combined with C2 (arbitrary code execution), an attacker can use `ctx_execute("shell", "curl http://10.0.0.5:3000/api/keys")` to bypass URL scheme restrictions entirely — `ctx_execute` has no URL filtering at all.

**Risk level:** High. Default-open internal network access is a significant SSRF vector, especially in corporate/CI environments.

---

### C4. Fail-Open Hook Design — Security Silently Degrades (CONFIDENCE: HIGH)

**Evidence (README §GitHub Copilot CLI):**
> "On an older global the hooks are inert (no routing/capture) until you upgrade — but they do **not** block your tools (context-mode fails open)."

**Evidence (README §Security):**
> "If you haven't configured any permissions, nothing changes. This only activates when you add rules."

**Evidence (README §Antigravity CLI):**
> "On an older global the **MCP server + routing rule + routing skill still work**, but hook enforcement/capture may be inert"

**Analysis:**
- **Fail-open is the explicit design choice**: if hooks fail, crash, are misconfigured, or run an older version, tools proceed without interception. This means routing enforcement and security policies silently degrade to zero protection.
- There is no fail-closed option. A security-conscious user cannot configure "block all tools if hooks are down."
- The `ctx_doctor` diagnostic tool exists, but it requires manual invocation. There is no automatic enforcement of doctor check results.
- On 4 of 18 platforms (Antigravity, Zed — no hooks at all; Cursor — SessionStart rejected; Codex — PreToolUse deny-only), routing is partial or absent. These platforms silently operate at ~60% compliance with no warning to the user.
- Hook version mismatch (global `context-mode` older than plugin) causes silent hook failure. The user's tools work normally but context-mode's protection is gone.
- If the hook subprocess crashes (OOM, segfault in better-sqlite3, timeout), the parent tool proceeds — fail-open.

**Risk level:** High. Fail-open is appropriate for convenience but inappropriate for security. Users may believe they have protection when they don't.

---

### C5. Regex-Based Credential Redaction Is Fundamentally Incomplete (CONFIDENCE: HIGH)

**Evidence (README §Network fetch hardening):**
> "`tool_input` for any `mcp__*` tool call is also redacted before persistence — the regex matcher in `hooks/posttooluse.mjs` masks `authorization`, `auth_token`, `access_token`, `refresh_token`, `bearer`, `token`, `secret`, `password`, `passwd`, `pwd`, `api_key` / `apikey` / `x_api_key`, `cookie` / `set-cookie`, `signature`, `private_key`, and `client_secret` (case-insensitive, hyphen/underscore-insensitive) to `[REDACTED]`"

**Analysis:**
- Regex-based credential redaction is a well-known security anti-pattern. It is a deny-list approach — it only catches credentials whose key names match the known patterns.
- **Missed credential formats:**
  - JWT tokens passed as raw strings (no key name)
  - Base64-encoded credentials in `Authorization: Basic <base64>` headers
  - OAuth tokens in non-standard parameter names (e.g., `X-Auth-Token`, `session`, `sid`, `hash`, `key`, `credential`, `oauth_token`, `access-key`)
  - SSH private keys in non-standard formats or pasted directly
  - TLS client certificates
  - Database connection strings (`postgres://user:pass@host:5432/db`)
  - AWS presigned URLs (credentials embedded in query parameters)
  - HMAC signatures in non-standard header names
  - API keys in URL query parameters (`?key=...`)
  - Credentials in nested JSON structures where the regex doesn't recurse
  - Credentials split across multiple lines or concatenated
  - Binary credentials (e.g., mTLS client certificate bytes)
- The redaction only applies to `mcp__*` tool call `tool_input`. Output from `ctx_execute` (stdout), `ctx_fetch_and_index` (fetched content), and non-MCP tool calls (raw Bash, Read, etc.) is NOT redacted before persistence to the session DB.
- The session DB (SQLite, stored in `~/.context-mode/` or `~/.claude/context-mode/`) contains: user prompts, tool calls, file paths, git operations, error messages, agent findings, and environment metadata. Any credentials in these fields are stored in plaintext.
- **The session DB is a credential treasure trove** for any malware or attacker with filesystem access.

**Risk level:** High. False sense of security. Regex redaction provides theater, not protection.

---

### C6. 18 Platform Adapters — Unsustainable Maintenance Burden (CONFIDENCE: HIGH)

**Evidence (README — platform count):**
The README documents install instructions for 18 platforms:
1. Claude Code
2. Gemini CLI
3. VS Code Copilot
4. JetBrains Copilot
5. GitHub Copilot CLI
6. Cursor
7. OpenCode
8. KiloCode
9. OpenClaw / Pi Agent
10. Codex CLI
11. Kimi Code
12. Qwen Code
13. Antigravity (IDE)
14. Antigravity CLI (agy)
15. Kiro
16. Zed
17. Pi Coding Agent
18. OMP (Oh My Pi)

**Evidence (README — capability gaps, from the compatibility matrix):**

| Platform | PreToolUse | PostToolUse | SessionStart | PreCompact | UserPromptSubmit | Stop |
|---|---|---|---|---|---|---|
| Antigravity (IDE) | -- | -- | -- | -- | -- | -- |
| Zed | -- | -- | -- | -- | -- | -- |
| Cursor | Yes | Yes | -- (rejected) | -- | -- | Yes |
| Codex CLI | Yes (deny-only) | Yes | Yes | runtime-gated | Yes | Yes |
| Antigravity CLI | Bounded | Capture-only | -- | -- | -- | Best-effort |
| Kiro | Yes | Yes | -- | -- | -- | -- |

**Analysis:**
- 18 adapters, each with different: hook event names, hook config formats (JSON/TOML/JSON-in-TOML), config file paths, MCP registration syntax, hook wire protocols, tool name conventions, permission systems, and capability gaps.
- 2 platforms (Antigravity IDE, Zed) have ZERO hooks — no routing enforcement, no session tracking, ~60% compliance only via manual file copy.
- Cursor's SessionStart hook is rejected by its validator — session restore after compaction is impossible.
- Codex PreToolUse supports deny-only (no input rewriting) — routing is incomplete.
- OpenCode/KiloCode lack a real SessionStart hook — they use `experimental.chat.system.transform` as a surrogate, which depends on an experimental API that may break.
- Each platform update (Claude Code, Cursor, Codex, etc.) may change hook semantics, config formats, or break adapters. The maintainer must track 18 upstream projects simultaneously.
- Single maintainer (Mert Koseoglu, per LICENSE copyright). Bus factor = 1.
- The README references 18+ tracking issues (#158, #164, #408, #473, #485, #489, #564, #567, #666, #774, #775) — indicating ongoing platform-specific breakage.
- **For a downstream user**: if your platform changes its hook API and context-mode hasn't released a fix yet, your routing enforcement silently degrades (fail-open, see C4).

**Risk level:** High for long-term maintenance. The adapter matrix is a N×M coupling problem where N=18 platforms and M=6 hook events = 108 integration points, each of which can break independently.

---

### C7. Model Compliance Dependence — Routing Is Non-Deterministic (CONFIDENCE: MEDIUM-HIGH)

**Evidence (README §Routing Enforcement):**
> "Instruction files guide the model via prompt instructions but cannot block anything."

> Without hooks: "~60% saved"

**Evidence (README §Think in Code):**
> "This is a mandatory paradigm across all 17 supported clients"

**Evidence (README §No prose-style enforcement):**
> "Aggressive brevity prompts have been shown to degrade coding/reasoning benchmarks"

**Analysis:**
- Even with hooks, the routing model depends on the LLM choosing to call `ctx_execute` instead of `Read` or `Bash`. Hooks can block tools (PreToolUse deny) but cannot force the model to use the sandbox alternative.
- The "mandatory paradigm" is enforced by prompt instructions, not by code. The model can ignore it, especially under prompt injection or when the model judges that direct tool use is more efficient.
- ~60% compliance without hooks means 40% of raw data still floods the context window. The "98% savings" claim requires both hooks AND model cooperation.
- Different models (Claude, GPT, Gemini, Qwen, Kimi) have different instruction-following capabilities. The same routing instructions may produce different compliance rates on different models.
- The `CONTEXT_MODE_EXTERNAL_MCP_NUDGE_EVERY` env var (default 10) re-injects guidance every 10 tool calls — an acknowledgment that the model forgets or ignores routing instructions.
- On platforms without hooks (Zed, Antigravity), compliance is entirely dependent on the model reading AGENTS.md/GEMINI.md and following instructions. This is non-deterministic and model-dependent.
- The README itself admits brevity prompts degrade benchmarks — implying that forcing the model into a specific paradigm (Think in Code) may also degrade performance on tasks where direct data processing is more appropriate.

**Risk level:** Medium-High. The tool's effectiveness is fundamentally limited by LLM compliance, which is non-deterministic and model-dependent.

---

### C8. Hosted Insight Dashboard Contradicts "Nothing Leaves Your Machine" (CONFIDENCE: MEDIUM)

**Evidence (README §Privacy & Architecture):**
> "Nothing leaves your machine. No telemetry, no cloud sync, no usage tracking, no account required."

**Evidence (README §ctx_insight tool):**
> "`/context-mode:ctx-insight` | Opens the hosted Insight dashboard ([context-mode.com/insight](https://context-mode.com/insight)) in your browser — org analytics for AI-assisted engineering teams."

**Evidence (README §Utility Commands):**
> "ctx insight → opens the hosted Insight dashboard in your browser"

**Analysis:**
- The privacy claim "Nothing leaves your machine" is contradicted by the existence of a hosted dashboard at `context-mode.com/insight` for "org analytics."
- The README does not explain what data the Insight dashboard receives, how it's transmitted, or what privacy protections apply.
- If the dashboard is read-only and pulls no data, the "org analytics" claim is hollow. If it does aggregate data, the privacy claim is false.
- This is a potential future monetization path that may involve data collection — the architecture for it is already shipped (the `ctx_insight` tool).
- The `stats.json` file served via jsDelivr CDN (`cdn.jsdelivr.net/gh/mksglu/context-mode@main/stats.json`) contains user count, npm version, and marketplace badges — this is public telemetry, contradicting "no usage tracking."

**Risk level:** Medium. The hosted dashboard is a contradiction that may become a data pipeline. Currently appears optional but the tool is shipped.

---

### C9. Session DB Stores Sensitive Data in Plaintext SQLite (CONFIDENCE: MEDIUM-HIGH)

**Evidence (README §Session Continuity):**
The session DB captures: user prompts, file paths, git operations, errors, environment metadata (cwd, venv, nvm, conda, worktree, package installs), agent findings, user decisions, constraints, blockers, rejected approaches, external references (URLs, GitHub issue numbers), and "large user-pasted data references."

**Evidence (README §Storage):**
> Sessions stored in `~/.context-mode/sessions/` or `~/.claude/context-mode/sessions/` or `~/.codex/context-mode/sessions/` etc.

**Analysis:**
- The session DB contains a complete audit trail of the user's coding session: what files they touched, what commands they ran, what errors they hit, what decisions they made, what they pasted.
- This data is stored in plaintext SQLite — no encryption at rest.
- The DB persists across sessions (unless `--continue` is not used, in which case previous data is deleted — but the default for many platforms is to persist).
- File path: `~/.context-mode/` or platform-specific — any process running as the user can read it.
- The 14-day cleanup for content DBs does not apply to session DBs — session data may persist indefinitely.
- Combined with C5 (incomplete credential redaction), credentials that appear in tool output, error messages, or user-pasted data are stored in plaintext in the session DB.
- `ctx_purge` deletes indexed content, but the README does not clarify whether it also purges session DBs.

**Risk level:** Medium-High. Plaintext session audit logs with potential credential leakage, accessible to any process with user privileges.

---

### C10. Single Maintainer + ELv2 = Supply Chain and Longevity Risk (CONFIDENCE: MEDIUM)

**Evidence:**
- LICENSE copyright: "Copyright 2026 Mert Koseoglu" — single author.
- 18 platform adapters, 12 language runtimes, 11 MCP tools, 6 hook types, SQLite/FTS5/BM25/RRF search engine.
- No mention of a team, organization, or contributing foundation.
- ELv2 prevents commercial forks from offering hosted services — reducing the incentive for community maintainers to contribute.
- The project reached Hacker News #1 (570+ points) — high visibility creates high dependency risk if the maintainer abandons the project.

**Risk level:** Medium. Bus factor of 1 for a tool that integrates deeply into 18 platforms' hook systems.

---

### C11. `better-sqlite3` Native Addon Fragility (CONFIDENCE: MEDIUM)

**Evidence (README §Build Prerequisites):**
> "sporadic SIGSEGV crashes caused by V8's `madvise(MADV_DONTNEED)` corrupting the addon's `.got.plt` section on Linux"

> "Linux + Node < 22.5 is unsupported"

> "if `better_sqlite3.node` ends up missing after install... the postinstall script and the runtime hook automatically re-fetch the prebuild and repair the binding"

**Analysis:**
- The project depends on `better-sqlite3` (native C++ addon) for Node < 22.5 and non-Linux platforms. Native addons are a well-known source of build failures, ABI incompatibilities, and segfaults.
- The README itself documents SIGSEGV crashes on Linux. The workaround (use `node:sqlite` on Node >= 22.5) means older Node versions are stuck with a crash-prone addon.
- The "self-heal" mechanism (auto re-fetch prebuild) means the install process may silently download and execute code from npm — a supply chain attack surface.
- CentOS 7/8, RHEL 7/8, and Alpine require special build toolchains — adding deployment complexity.
- Bun uses `bun:sqlite` — but Bun itself is a young runtime with its own stability issues.

**Risk level:** Medium. Native addon fragility affects reliability, especially on older systems.

---

### C12. Unverified "Used Across Teams At" Claims (CONFIDENCE: MEDIUM)

**Evidence (README lines 9-30):**
The README displays badges for: Microsoft, Google, Meta, Amazon, IBM, NVIDIA, ByteDance, Stripe, Datadog, Salesforce, GitHub, Red Hat, Supabase, Canva, Notion, Hasura, Framer, Cursor.

**Analysis:**
- These are static shields.io badge images (`img.shields.io/badge/Microsoft-141414`) — they are NOT verified endorsements. Anyone can generate a badge saying "Used at [Company]."
- There is no evidence that any of these companies officially use or endorse context-mode.
- This is misleading social proof. A developer evaluating the tool may assume enterprise adoption that does not exist.
- The shields.io badges link to `#` (no URL) — they are purely decorative.

**Risk level:** Low for security, but medium for trust/decision-making. Misleading adoption claims may cause teams to overestimate the project's maturity and institutional backing.

---

## CONFIDENCE

| Issue | Confidence | Rationale |
|---|---|---|
| C1: ELv2 blocks SaaS | **HIGH** | Direct quote from LICENSE and README. Legal text is unambiguous. |
| C2: Sandbox is not a sandbox | **HIGH** | Direct quote from README. "Isolated subprocess" + credential passthrough + 12 language runtimes + filesystem access = arbitrary code execution. |
| C3: Internal network fetch by default | **HIGH** | Direct quote: RFC1918 "allowed by default." `CTX_FETCH_STRICT=1` is opt-in. |
| C4: Fail-open hooks | **HIGH** | Direct quote: "context-mode fails open." Multiple corroborating passages. |
| C5: Regex redaction incomplete | **HIGH** | Direct quote listing redacted patterns. Regex-based credential detection is a known anti-pattern. Missing formats are enumerable. |
| C6: 18 adapter maintenance burden | **HIGH** | Counted from README. Compatibility matrix shows gaps. Single maintainer per LICENSE. |
| C7: Model compliance non-deterministic | **MEDIUM-HIGH** | README admits ~60% without hooks. Depends on LLM behavior which is inherently non-deterministic. |
| C8: Hosted dashboard contradiction | **MEDIUM** | Dashboard URL exists. "Org analytics" implies data. Privacy claim is "nothing leaves." Contradiction is clear but data flow is undocumented. |
| C9: Plaintext session DB | **MEDIUM-HIGH** | README lists captured events. No mention of encryption. SQLite stored in home directory. |
| C10: Single maintainer | **MEDIUM** | LICENSE shows one copyright holder. No team mentioned. ELv2 reduces fork incentive. |
| C11: Native addon fragility | **MEDIUM** | README documents SIGSEGV. Self-heal downloads code. Older systems need build toolchains. |
| C12: Unverified adoption claims | **MEDIUM** | Badges are shields.io static images with `href="#"`. No verification mechanism. |

**Overall confidence in findings: HIGH.** All critical issues (C1-C5) are backed by direct quotes from the project's own README and LICENSE. The analysis is adversarial but evidence-based — no claims are made without textual support from the project's primary sources.

**Key takeaway for downstream consumers:** Context Mode is a powerful context-optimization tool for individual developer use on trusted local machines. It is NOT safe for: (a) SaaS/managed service integration (ELv2), (b) environments with sensitive credentials (fake sandbox + credential passthrough), (c) hosted/CI environments without `CTX_FETCH_STRICT=1` (SSRF), (d) any context where hook failure must not silently degrade security (fail-open design), or (e) long-term dependency without a maintenance plan (18 adapters, single maintainer).
