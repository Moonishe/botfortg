# 03 — Devil's Advocate: risks, security, and anti-patterns

## Output contract
- **SUMMARY**: Hermes's own security policy is admirably honest: the only real containment boundary is the operating system. In-process heuristics (approval gate, redaction, Skills Guard) are explicitly labeled as defense-in-depth, not boundaries. The project mitigates risk with exact dependency pinning, plugin sandbox expectations, and credential env stripping, but the sheer breadth of tools, platforms, plugins, and skills creates a large attack surface.
- **CHANGES**: None.
- **EVIDENCE**: `SECURITY.md`, `CONTRIBUTING.md`, `tools/approval.py`, `tools/skills_guard.py`, `tools/skill_manager_tool.py`, `pyproject.toml`, `AGENTS.md`, `gateway/run.py` (secret patterns), `hermes_cli/config.py` (env var model).
- **RISKS**: In-process approval is bypassable, plugins/skills execute arbitrary code, gateway auth relies on allowlists that can be misconfigured, supply-chain risk is real but mitigated, and the broad schema increases token cost and prompt-injection surface.
- **BLOCKERS**: None.

## Hermes's own trust model (from `SECURITY.md`)
> "The only security boundary against an adversarial LLM is the operating system. Nothing inside the agent process constitutes containment — not the approval gate, not output redaction, not any pattern scanner, not any tool allowlist."

This is the correct mental model. The project does not claim the agent is safe by default; it tells operators to choose OS-level isolation.

## In-process heuristics are explicitly not boundaries
| Heuristic | What it does | Why it is not a boundary |
|-----------|--------------|--------------------------|
| Approval gate (`tools/approval.py`) | Regex/static analysis over shell commands; prompts user or auto-approves via auxiliary LLM | Shell is Turing-complete; a motivated LLM can encode destructive commands to bypass any pattern set. |
| Output redaction (`agent/redact.py`) | Strips secret-like strings from display | An attacker-controlled output producer can defeat redaction by obfuscation/encoding. |
| Skills Guard (`tools/skills_guard.py`) | Regex scanner over skill files; trust-level policy | Regex is incomplete; a skill can still import malicious code at runtime. |
| Credential env stripping | Removes API keys from env of shell/MCP/cron/code-execution children | Any code running *inside* the agent process (plugin, hook, skill) can read the agent's memory/credentials. |

## Plugin / skill / MCP arbitrary code execution
- Plugins load into the agent process via `register(ctx)` and can register hooks, tools, and CLI commands. They run with full agent privileges.
- Skills are markdown instructions that reference scripts; those scripts are executed in the agent's environment via `terminal` or imported at skill load time.
- MCP servers run in separate subprocesses, but a malicious MCP server can return arbitrary data that is fed into the agent context as a tool result (prompt-injection vector).
- **Security.md explicitly says**: "A malicious or buggy plugin is not a vulnerability in Hermes Agent itself." The operator is the trust boundary.

## Gateway / external surface authorization
- Every messaging/network adapter must have an operator-configured caller allowlist before dispatching work or relaying output.
- `gateway/run.py` defines `_GATEWAY_SECRET_PATTERNS` for sk-*, gh[pousr]_*, xox*-*, hf_*, glpat-*, Bearer tokens.
- Binding a local-only HTTP surface to `0.0.0.0` is a documented "break-glass operator decision" — the operator is responsible for public-exposure hardening.
- Session IDs are routing handles, not auth boundaries.

## Supply-chain and dependency policy
- `pyproject.toml` uses **exact pins (`==`)** in core dependencies after the litellm compromise and the Mini Shai-Hulud worm (May 2026).
- Optional backends are lazy-installed via `tools/lazy_deps.py` so a compromised PyPI release cannot break a fresh install.
- Git dependencies must be pinned to SHA; GitHub Actions to SHA+comment.
- This is a strong, modern policy, but it depends on the team keeping pins updated and auditing PRs that touch dependencies.

## Anti-patterns / complexity concerns
1. **YOLO mode (`HERMES_YOLO_MODE`)** bypasses all approvals. It is frozen at module import time to prevent a skill from flipping it at runtime, but it is still an operator foot-gun.
2. **Dangerous command detection is regex-based.** `DANGEROUS_PATTERNS` in `tools/approval.py` is a heuristic. It will miss novel encodings (`$(command)`, unicode, base64, etc.).
3. **Skills Guard is off for agent-created skills by default.** `tools/skill_manager_tool.py` only scans when `skills.guard_agent_created` is true. The rationale is that the agent can already do the same things via `terminal`, but this means the skill path adds no extra friction.
4. **Skill deletion validation** (`tools/skill_manager_tool.py:_validate_delete_target`) is good defense-in-depth, but the comment references Kilo Code's incident — Hermes is aware of this class of bug.
5. **Gateway agent cache (128 LRU, 1h idle TTL)** shares `AIAgent` instances across concurrent sessions. A bug in cache eviction or state isolation could leak context between users.
6. **MCP dynamic tool registration** can mutate the registry while other threads are reading tool definitions. `ToolRegistry` uses an RLock, but `get_definitions` callers receive snapshots; still, dynamic MCP tool changes can cause non-deterministic schema size.
7. **Massive code volume in single files**: `gateway/run.py`, `hermes_cli/main.py`, `cli.py` still carry thousands of lines. High churn in large files increases regression risk.

## Windows-specific footguns
- The project explicitly audits `os.kill(pid, 0)` (silent console-group kill on Windows), `os.killpg`, `termios`, `fcntl`, POSIX signals, and CRLF generation.
- It uses `psutil` for cross-platform process management and `pywinpty`/`concurrent-log-handler` on Windows.
- Still, native Windows support is a major source of edge-case complexity (e.g., `pythonw.exe` for detached daemons, OneDrive path redirection, `.cmd` shims, `schtasks` double-quoting).

## What is *not* a vulnerability under their policy
- Bypasses of in-process heuristics.
- Prompt injection that only changes LLM output without a chained §3.1 outcome.
- Consequences of a chosen isolation posture (e.g., local backend reaching host state).
- Community-contributed skills/plugins doing something malicious.
- Public exposure without external controls.

## Bottom line
Hermes's security posture is **transparent and well-documented**, but the product's value proposition (execute code, browse the web, chat over many platforms, run arbitrary skills/plugins/MCP) is fundamentally at odds with strong containment. The only supported high-assurance path is **whole-process sandboxing** (Docker or NVIDIA OpenShell). Running the default local backend with untrusted input surfaces or exposing the gateway to the internet without VPN/firewall is explicitly outside the supported posture.

## Recommendations for consumers
- Run Hermes in a container or OpenShell sandbox for any non-personal, multi-user, or untrusted-input deployment.
- Configure allowlists for every gateway platform before enabling it.
- Review every third-party skill/plugin before install; read the Python/scripts, not just the SKILL.md.
- Keep credentials in `.env` with tight permissions, never in `config.yaml` or version control.
- Do not enable `HERMES_YOLO_MODE` on shared or production systems.
- Do not expose the dashboard/gateway to `0.0.0.0` without an external auth layer.
