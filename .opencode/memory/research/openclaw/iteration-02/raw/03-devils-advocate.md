# Researcher 3 — Devil's Advocate (Critical/Security Perspective)

**Repository:** https://github.com/openclaw/openclaw
**Date:** 2026-06-22
**Role:** Researcher 3 — Critical/Security analysis
**Sources fetched:**
- `SECURITY.md` (root)
- `src/security/dangerous-config-flags-core.ts`
- `src/security/core-dangerous-config-flags.ts`
- `docs/gateway/configuration.md`

---

## 1. Security Model: One Trusted Operator Per Gateway

### What the docs say
OpenClaw explicitly does **not** model a gateway as a multi-tenant, adversarial-user boundary. The stated model is "personal assistant" — one trusted operator, potentially many agents. Authenticated Gateway callers are treated as **trusted operators** with full access. Session identifiers (`sessionKey`, session IDs, labels) are explicitly called out as **routing controls, not per-user authorization boundaries**.

### Critical analysis
- **No per-user isolation exists by design.** If two humans share one gateway, they can see each other's sessions, chat history, and `sessions.list`/`sessions.preview`/`chat.history` output. The docs call this "expected in this trust model."
- **The burden of isolation is entirely on the operator** to provision separate hosts/VPS/OS-users per trust boundary. There is no in-product enforcement. A misconfigured multi-user deployment leaks data silently — no warning, no audit, no block.
- **Company-shared agent setups are acknowledged** but rely purely on operational discipline ("use a dedicated machine/VM/container and dedicated accounts"). If the host browser profile is logged into personal accounts, the boundary collapses — but OpenClaw provides no detection or guardrail for this collapse.
- **Risk:** The trust model is coherent for a single user but creates a sharp footgun for any collaborative/team deployment. The "not a vulnerability" stance means reports of cross-user data exposure on a shared gateway are dismissed by default.

### Verdict
The model is internally consistent for its stated use case (single operator). The danger is that the product's surface area (Slack, Discord, Teams, WhatsApp integrations; OpenAI-compatible HTTP endpoints) naturally invites multi-user/team usage, while the security model actively refuses to defend that scenario. This is an architectural mismatch between product surface and threat model.

---

## 2. Default Host Execution Risk (sandbox.mode: off)

### What the docs say
- `agents.defaults.sandbox.mode` defaults to **`off`**.
- `tools.exec.host` defaults to **`auto`**: sandbox when a sandbox runtime is active for the session, otherwise **gateway** (i.e., the host).
- Implicit exec calls (no explicit host in the tool call) follow the same behavior.
- This is described as "expected in OpenClaw's one-user trusted-operator model."

### Critical analysis
- **Out-of-the-box, any tool-enabled agent executes arbitrary commands directly on the host OS** with the full privileges of the OpenClaw process. There is no sandbox by default.
- **Combined with the prompt-injection policy (see §6), this creates a compounding risk:** prompt injection is out of scope as a vulnerability, AND sandboxing is off by default. An attacker who can inject content into a channel the agent reads (via reply, quote, thread, forwarded message, or webhook payload) can potentially steer the agent to run host commands — and neither layer is on by default.
- **Sandbox requires explicit opt-in AND a Docker image build.** The configuration docs note you must "build the image first" (`scripts/sandbox-setup.sh` or inline `docker build`). This is a non-trivial setup step that most users will skip, especially for a "personal assistant" that "just works."
- **`tools.exec.host: auto` is a silent downgrade.** If a sandbox runtime is unavailable or fails to initialize, exec silently falls back to the gateway host. There's no indication in the docs that this fallback produces a warning or blocks execution. An operator who thinks sandboxing is active but whose Docker runtime is down gets host execution with no explicit signal.
- **Docker security is optional hardening, not a boundary.** The Docker section recommends `--read-only` and `--cap-drop=ALL` but these are operator choices, not defaults enforced by OpenClaw.

### Verdict
This is the single most dangerous default in the system. Host code execution is the default, sandboxing requires manual setup, and the fallback from sandbox to host is silent. The "trusted operator" justification is reasonable in theory but fragile in practice given the multi-channel input surface.

---

## 3. Shared-Secret Auth Risks

### What the docs say
- `gateway.auth.mode="token"` or `"password"` authenticates **possession of the gateway operator secret**.
- Shared-secret bearer callers receive the **full default operator scope set**: `operator.admin`, `operator.read`, `operator.write`, `operator.approvals`, `operator.pairing`.
- Chat-turn endpoints (`/v1/chat/completions`, `/v1/responses`) and `POST /tools/invoke` treat shared-secret callers as **owner senders** for owner-only tool policy.
- **Narrower `x-openclaw-scopes` headers are explicitly IGNORED** for shared-secret paths. Only identity-bearing HTTP modes (trusted proxy auth or `auth.mode="none"` on private ingress) honor declared per-request scopes.
- The OpenAI-compatible HTTP endpoints and `/tools/invoke` are documented as "full operator-access surfaces, not per-user/per-scope boundaries."

### Critical analysis
- **No least-privilege HTTP client is possible under shared-secret auth.** A script, integration, or CI pipeline that needs only read access cannot be scoped down. It gets full admin. One leaked token = full gateway compromise including tool invocation, config writes, pairing, and approvals.
- **The `x-openclaw-scopes` header is a false sense of security.** An operator might set narrower scopes on an HTTP client expecting reduced access, but the gateway silently ignores them and grants full operator scope. The docs disclose this, but the header's existence invites misuse. A security-conscious operator who configures `x-openclaw-scopes: operator.read` on an integration believes they've achieved least privilege — they have not.
- **Owner-sender semantics on shared-secret paths** mean HTTP API callers bypass owner-only tool restrictions. Any tool gated to "owner only" is accessible to any bearer of the shared secret, including external integrations.
- **No token rotation or scoping guidance** is provided in the fetched docs. The shared secret is a single high-value credential with no documented mechanism for scoped, rotating, or revocable API keys.
- **Control UI device auth can be disabled** via `dangerouslyDisableDeviceAuth` (see §5), which collapses the only secondary auth layer on the local UI path.

### Verdict
The shared-secret model is all-or-nothing. It is adequate for a single-loopback deployment but creates a high-impact credential that cannot be scoped, only fully trusted or fully absent. The silent ignoring of `x-openclaw-scopes` on the most common auth mode is a usability-trap that could mislead security-conscious operators.

---

## 4. Plugin In-Process Trust Model

### What the docs say
- Plugins/extensions are loaded **in-process** with the Gateway and treated as **trusted code**.
- Plugins execute with the **same OS privileges as the OpenClaw process**.
- Runtime helpers (e.g., `runtime.system.runCommandWithTimeout`) are **convenience APIs, not a sandbox boundary**.
- Installing or enabling a plugin grants it the same trust level as local code.
- Malicious behavior from a trusted-installed plugin is explicitly **not a vulnerability**.
- Recommended mitigation: `plugins.allow` to pin explicit trusted plugin IDs.

### Critical analysis
- **No plugin sandbox exists.** Unlike a browser extension model or even an MCP server (which runs as a separate process), OpenClaw plugins run in the same Node.js process with full host access. A plugin can read env vars, read/write files, spawn processes, and exfiltrate data with zero isolation.
- **Supply-chain attack surface is maximal.** A compromised or malicious plugin update has full host access. There is no permission model, no capability restriction, no IPC boundary. `plugins.allow` is an allowlist of IDs — it does not restrict what an allowed plugin can do.
- **The "only install plugins you trust" guidance** is the entire defense. This is the same trust model as `npm install` running postinstall scripts — widely recognized as a supply-chain risk. OpenClaw adds no mitigation beyond this advice.
- **Plugin config contracts extend dangerous-flag detection** (see `dangerous-config-flags-core.ts`), which is a positive — it means the security audit can surface risky plugin config. But this is detection, not prevention. The plugin still runs in-process regardless of what flags it sets.
- **No documented plugin signing or verification.** The fetched docs do not mention package signatures, integrity verification, or pinned versions for plugins. `plugins.allow` pins IDs, not content hashes or versions.

### Verdict
The in-process plugin model is the highest supply-chain risk in the architecture. It is consistent with the "trusted operator" model but offers zero defense-in-depth against plugin compromise. An operator who installs a popular plugin is implicitly trusting its entire dependency tree and update pipeline with full host access.

---

## 5. Dangerous Break-Glass Flags

### From `core-dangerous-config-flags.ts`
| Flag | Effect |
|------|--------|
| `gateway.controlUi.allowInsecureAuth=true` | Weakens Control UI authentication |
| `gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback=true` | Bypasses Host header origin validation (CSRF surface) |
| `gateway.controlUi.dangerouslyDisableDeviceAuth=true` | Disables device pairing auth on local UI (break-glass) |
| `hooks.gmail.allowUnsafeExternalContent=true` | Treats Gmail hook payloads as trusted (prompt injection vector) |
| `hooks.mappings[].allowUnsafeExternalContent=true` | Treats webhook payloads as trusted (prompt injection vector) |
| `tools.exec.applyPatch.workspaceOnly=false` | Allows `apply_patch` writes outside workspace |
| `security.audit.suppressions configured (N)` | Hides audit findings (visibility risk, not direct vuln) |

### From `dangerous-config-flags-core.ts`
| Flag | Effect |
|------|--------|
| `hooks.allowRequestSessionKey=true` | Caller-controlled session routing (session hijacking/confusion risk) |
| `browser.ssrfPolicy.dangerouslyAllowPrivateNetwork=true` | Browser tool can reach internal/private/metadata IPs (SSRF) |
| `tools.fs.workspaceOnly=false` | Filesystem reads/writes escape workspace (host FS access) |
| `agents.defaults.sandbox.docker.*` dangerous keys | Per-agent sandbox Docker escape flags |

### Critical analysis
- **All `dangerous*`/`dangerously*` flags are explicitly out of scope as vulnerabilities.** The SECURITY.md states: "Any report whose only claim is that an operator-enabled `dangerous*`/`dangerously*` config option weakens defaults (these are explicit break-glass tradeoffs by design)." This means the project will not track, alert, or treat these as security issues even when enabled in production.
- **The `security audit` tool surfaces these flags** (positive), and `dangerouslyDisableDeviceAuth` non-local setups are flagged as dangerous findings. But the audit is opt-in (`openclaw security audit --deep`), not continuous.
- **`allowUnsafeExternalContent` on hooks is a direct prompt-injection enablement.** The docs warn: "Hook/webhook-driven payloads should be treated as untrusted content; keep unsafe bypass flags disabled." But the flag exists, is named `allowUnsafeExternalContent`, and when enabled, untrusted webhook content is treated as trusted model input. Combined with sandbox-off default and host exec, this is a remote-to-host-execution chain waiting to happen.
- **`dangerouslyAllowPrivateNetwork`** disables SSRF protection in the browser tool, allowing the agent to fetch internal/metadata endpoints (e.g., cloud metadata services at `169.254.169.254`). This is a credential-theft vector in cloud deployments.
- **`hooks.allowRequestSessionKey`** lets webhook callers choose the session key, enabling session confusion or targeting if `allowedSessionKeyPrefixes` is not set (the docs recommend setting it, but it's a separate config).
- **`security.audit.suppressions`** can hide audit findings. While not a direct vulnerability, an operator who suppresses dangerous-flag warnings has removed the only detection layer for the other flags. Suppression count is surfaced in the dangerous-flag snapshot, which is good, but the suppressed findings themselves are hidden.

### Verdict
The break-glass flags are clearly labeled and detected by the audit tool — good engineering practice. The risk is that they are all out of scope as vulnerabilities, meaning an operator who enables several of them simultaneously (e.g., `allowUnsafeExternalContent` + `sandbox.mode: off` + `dangerouslyAllowPrivateNetwork`) has assembled a full attack chain, and the project's security policy will not treat the resulting compromise as a vulnerability.

---

## 6. Prompt Injection Handling Policy

### What the docs say
- "The model/agent is **not** a trusted principal. Assume prompt/content injection can manipulate behavior."
- "Prompt injection by itself is **not** a vulnerability report unless it crosses one of those boundaries [policy, auth, approval, sandbox, or tool-boundary]."
- "Prompt-injection-only attacks (without a policy/auth/sandbox boundary bypass)" are **Out of Scope**.
- Hook/webhook-driven payloads should be treated as untrusted content.
- Weak model tiers are easier to prompt-inject; prefer strong models + strict tool policy + sandboxing.
- Allowlists primarily gate **triggering**, not **context visibility**. Supplemental context (reply body, quoted text, thread history, forwarded metadata) may still reach the model from non-allowlisted senders depending on channel.

### Critical analysis
- **Prompt injection is the primary attack vector for LLM agents, and OpenClaw explicitly declines to treat it as a vulnerability.** The rationale ("it's only a vuln if it crosses a boundary") is defensible, but the practical effect is that the most common real-world attack against agent systems is not in scope unless the operator has already configured all the boundary layers correctly.
- **Context visibility is not uniformly filtered by allowlist.** The docs admit: "some channels already filter parts of supplemental context by sender allowlist; other channels still pass supplemental context as received." This means on some channels, a non-allowlisted user can inject content into the model's context via reply/quote/thread/forward — and this is treated as a "hardening/consistency finding," not a vulnerability.
- **The defense-in-depth chain requires every layer to be manually configured:** allowlists (triggering), strict tool policy (`tools.profile: "messaging"`), sandboxing (`sandbox.mode: non-main` or `all`), exec approvals, and disabling `allowUnsafeExternalContent`. If any layer is missing or misconfigured, prompt injection can reach host execution. Given that sandbox is off by default and tool policy requires explicit configuration, the default state is **vulnerable to prompt-injection-driven host execution**.
- **The "use strong models" guidance** is a tacit admission that weaker models are exploitable, but it pushes the burden onto the operator's model choice rather than providing structural protection.

### Verdict
The prompt-injection policy is the most philosophically debatable position in the security model. It is logically consistent with the boundary-based approach, but it creates a situation where the default configuration (sandbox off, no strict tool policy, context visibility not uniformly filtered) is effectively open to prompt-injection-driven host execution, and the project will not treat that as a security issue. The operator must actively harden every layer; the defaults do not protect against the primary attack vector.

---

## 7. Config Strictness Lockout Risk

### What the docs say
- OpenClaw accepts only configurations that **fully match the schema**. Unknown keys, malformed types, or invalid values cause the Gateway to **refuse to start**.
- Only `$schema` (string) is allowed as a root-level exception.
- When validation fails: Gateway does not boot; only diagnostic commands work (`doctor`, `logs`, `health`, `status`).
- The Gateway keeps a **last-known-good copy** after each successful startup, but **startup and hot reload do not restore it automatically**.
- `openclaw doctor --fix` (or `--yes`) is required to restore the last-known-good copy.
- Hot reload: invalid external edits are rejected without rewriting `openclaw.json`; the current runtime keeps the last accepted config.
- Promotion to last-known-good is **skipped when a candidate contains redacted secret placeholders** such as `***`.
- Destructive clobbers (dropping `gateway.mode`, shrinking file by >50%) are rejected and saved as `.rejected.*`.

### Critical analysis
- **A single typo or unknown key bricks the gateway.** Strict validation is good for correctness but creates a self-inflicted denial-of-service vector. An operator who adds an undocumented field, misspells a key, or copies a config from a newer version into an older instance will find the gateway refuses to start with no auto-recovery.
- **No auto-restore on startup is a deliberate but risky choice.** The last-known-good copy exists but is not used automatically. Rationale is likely to avoid silently reverting intentional changes, but the cost is that a bad edit during a remote maintenance session can lock the operator out until they run `doctor --fix` — which requires shell access to the host.
- **Hot-reload failure is safer than startup failure** (runtime keeps last accepted config), but a restart-required change that also fails validation leaves the operator in a state where the running config diverges from the file with no clear resolution path.
- **Redacted-secret-placeholder skip is a subtle trap.** If the last-known-good candidate contains `***` placeholders (e.g., from a config snapshot that redacted secrets), it will not be promoted. An operator relying on auto-restore after a `doctor --fix` may find it silently skipped.
- **`$include` adds complexity.** Includes are confined to the config directory (with `OPENCLAW_INCLUDE_ROOTS` for shared trees), support 10 levels of nesting, and symlink resolution is re-checked. A broken include (missing file, parse error, circular reference) fails validation. While errors are reported clearly, the multi-file config layout increases the surface for accidental lockout.
- **Not a security vulnerability per the project's model** (it requires trusted-operator config access, which is in the trusted boundary), but it is an **availability risk** that can mimic a security incident (gateway down, no service).

### Verdict
Config strictness is a double-edged sword. It prevents silent misconfiguration but creates a hard lockout on any validation failure with no automatic recovery. The last-known-good mechanism is present but deliberately inert on startup. For remote/headless deployments, a bad config edit can require physical or SSH access to recover, which may not be available in all scenarios.

---

## Cross-Cutting Risk Summary

### Attack Chain: Remote Prompt Injection to Host RCE (Default Config)
1. Agent is connected to a channel (Slack/Discord/Teams/WhatsApp) with `dmPolicy: "open"` or in a shared group.
2. `sandbox.mode: off` (default) → agent exec runs on host.
3. No strict tool policy configured → agent has broad tool access.
4. A non-allowlisted user sends a reply/quote/forward with injected instructions.
5. On channels that don't filter supplemental context by allowlist, the injection reaches the model.
6. Model is steered to invoke a tool that runs a host command.
7. Host command executes with OpenClaw process privileges.

**Project stance:** Each individual step is either "by design" or "out of scope" or "hardening, not vulnerability." The chain as a whole is not treated as a security issue unless one specific step crosses a documented boundary. The operator is expected to configure all defenses manually.

### Attack Chain: Shared-Secret Theft to Full Gateway Compromise
1. Operator uses `gateway.auth.mode="token"` for an HTTP integration (e.g., a CI pipeline or external script).
2. Token is stored in the integration's config/env and leaks (common occurrence).
3. Attacker uses the token on `/v1/chat/completions`, `/v1/responses`, or `/tools/invoke`.
4. Attacker receives full operator scope (admin/read/write/approvals/pairing) and owner-sender semantics.
5. Attacker invokes tools, writes config, pairs rogue nodes.

**Project stance:** This is "documented full operator-access surface" and not a vulnerability. The `x-openclaw-scopes` header cannot reduce this. Mitigation is operational (protect the secret, use loopback-only).

### Attack Chain: Plugin Supply-Chain to Host Compromise
1. Operator installs a popular community plugin (via `plugins.allow`).
2. Plugin update is compromised (maintainer account takeover or dependency compromise).
3. Updated plugin runs in-process with full OS privileges.
4. Plugin exfiltrates `~/.openclaw` state, env vars, API keys, or installs persistence.

**Project stance:** "Malicious plugin after a trusted operator installs or enables it" is explicitly out of scope. Defense is "only install plugins you trust."

---

## Positive Security Observations (for balance)

1. **`security audit --deep` and `--fix`** — Active detection tool for dangerous flags and deployment risks. This is above average for open-source agent frameworks.
2. **Dangerous flags are explicitly named and detected** — The `dangerous-config-flags-core.ts` and `core-dangerous-config-flags.ts` modules systematically collect all enabled break-glass flags for audit visibility.
3. **Loopback-only default** — `gateway.bind="loopback"` is the default, reducing public exposure risk.
4. **Hot-reload validation gate** — Invalid external edits are rejected without rewriting the file, and destructive clobbers are saved as `.rejected.*` for inspection.
5. **Hook auth is header-only** — Query-string tokens are rejected, reducing token-in-URL leakage.
6. **Dedicated temp root** — `/tmp/openclaw` with sandbox media validation scoped to the OpenClaw-managed temp root, not arbitrary `os.tmpdir()`.
7. **Sub-agent delegation hardening** — `sessions_spawn` denied by default; `sandbox: "require"` prevents unsandboxed child delegation.
8. **CodeQL + OpenGrep + secret detection** — Multi-layer static analysis in CI.
9. **Node.js version requirement** — Pins to v22.19.0+ including specific CVE patches (CVE-2025-59466, CVE-2026-21636).
10. **Clear, detailed SECURITY.md** — The trust model is explicitly documented rather than implicit. This is genuinely better than most projects that leave the threat model unstated.

---

## Confidence Assessment

**Confidence: HIGH** on the following:
- The trust model is accurately described (single-operator, no multi-tenant boundary).
- Sandbox defaults to off; host exec is the default path.
- Shared-secret auth grants full operator scope; `x-openclaw-scopes` is ignored on shared-secret paths.
- Plugins run in-process with full OS privileges.
- Prompt injection alone is out of scope as a vulnerability.
- Config validation is strict with no auto-restore on startup.

**Confidence: MEDIUM** on the following:
- The silent sandbox-to-host fallback behavior (the docs say `tools.exec.host: auto` falls back to gateway, but whether this produces a runtime warning is not confirmed from the fetched docs).
- The exact set of `DANGEROUS_SANDBOX_DOCKER_BOOLEAN_KEYS` (imported from `../agents/sandbox/config.js`, not fetched).
- Plugin signing/verification mechanisms (not mentioned in fetched docs, but may exist elsewhere in the repo).

**Confidence: LOW** on:
- Whether any of these risks have been mitigated in code not fetched (e.g., runtime warnings for sandbox fallback, additional auth layers in unreferenced modules).
