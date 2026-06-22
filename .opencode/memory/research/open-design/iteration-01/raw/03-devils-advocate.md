# 03 Devil's Advocate — Risks, license, security, and limitations

## SUMMARY
Open Design is Apache-2.0, local-first, and privacy-respecting, but it carries meaningful risks: it runs arbitrary user-installed agent CLIs on the user's machine, executes agent-generated code in a sandboxed iframe with no additional OD-level sandbox, and depends on a large matrix of third-party CLI stream formats. The "Open Design AMR" official model service and the optional telemetry opt-in create a commercial/telemetry boundary that must be carefully reviewed. Windows native support is best-effort, and the repo's huge file count (8,523) makes regressions likely.

## CHANGES
No code changes; read-only risk assessment.

## EVIDENCE
Tools used:
- `read` of `LICENSE`, `PRIVACY.md`, `AGENTS.md`, `docs/architecture.md` §9 (Security model), `docs/roadmap.md` Risk register.
- `grep` across markdown for `TODO|FIXME|SECURITY|license|Apache|MIT|GPL` and across TypeScript for `sandbox|iframe|SSRF|CORS|auth|token|secret`.
- `webfetch` pre-flight of README/AGENTS/CHANGELOG for risk context.
- `glob` of plugin manifests to verify license diversity.

### License
- Root license: Apache-2.0 (`LICENSE`, `package.json`).
- Skills directory: Apache-2.0 unless individual `LICENSE` says otherwise. Notable exception: `skills/guizang-ppt/` is bundled verbatim from `op7418/guizang-ppt-skill` under MIT.
- Plugins: mostly Apache-2.0, some MIT (e.g., `frame-product-promo-30s`), some derived from third-party projects (huashu-design, Hyperframes, frontend-slides) with attribution preserved.
- `tools/pack/resources/win/7zip/README.md` explicitly states those binaries remain under upstream 7-Zip license (LGPL/BSD/unRAR notices) and are not relicensed.

### Security model
- Daemon binds to `127.0.0.1` by default; LAN exposure requires `OD_BIND_HOST` + `OD_ALLOWED_ORIGINS`.
- BYOK API keys stored in daemon config or browser localStorage; never sent to Open Design servers.
- Preview iframe is `sandbox="allow-scripts"` without `allow-same-origin`, isolating from host DOM/cookies.
- Agent adapter sets CWD to artifact directory; relies on agent's own permission system (Claude Code `--permission-mode`, Codex sandbox, etc.).
- BYOK proxy at `/api/proxy/{anthropic,openai,azure,google,ollama,senseaudio}/stream` has per-target SSRF protection blocking internal IPs / link-local / CGNAT.
- Desktop folder import uses HMAC token gate with 32-byte secret, single-use nonce, 60s TTL, and sticky in-process flag (docs/architecture.md §Folder import).

### Privacy
- Telemetry is **off by default**. Opt-in categories: anonymous metrics, conversation/tool content (truncated, redacted), artifact manifest (filenames/types/sizes only, never contents).
- Cloudflare Worker relay → Langfuse. Public relay URL only; no secret keys in client.
- Anonymous random installation ID; no name/email/account linkage.
- Open Design AMR (official model service) may share data between the two products because they are the same product family/team.

### Risks and limitations found
- **Agent CLI matrix**: 24+ adapters. Any upstream CLI change (Claude Code JSON stream format, ACP protocol, etc.) can break an adapter.
- **No OD-level sandbox**: OD intentionally inherits the agent's permission model. A malicious skill or plugin can ask the agent to write/delete files anywhere the agent is allowed.
- **Skill install from arbitrary URLs**: `od skill add <url>` can pull from any git repo. The roadmap risk register calls this out as a user-machine compromise risk.
- **Windows native friction**: `better-sqlite3` has no prebuilt win32/Node 24 binary; compiles from source via node-gyp requiring VS Build Tools.
- **README vs package.json version mismatch**: README banner says 0.10.0, package.json says 0.11.0. This suggests documentation may lag releases.
- **Large codebase blast radius**: 8,523 files, strict AGENTS.md boundary rules; the project is not trivial to fork or audit.
- **Commercial model service**: AMR is a paid, first-party service. While BYOK is supported, the default onboarding in 0.9.0+ leads users toward AMR sign-in.
- **Telemetry redaction**: Keys/JWTs/emails/credit cards are stripped, but "conversation and tool content" is still truncated and sent; sensitive business prompts could leak.
- **No RBAC/multi-user**: Explicitly out of MVP scope; not suitable for shared server deployments without additional work.

## RISKS
- **High**: arbitrary code execution via agent-generated artifacts and skills; relies entirely on agent's own permissions.
- **High**: adapter breakage from upstream CLI format changes; 24 adapters to maintain.
- **Medium**: telemetry opt-in still sends prompts/tool content; redaction is not proof against leakage.
- **Medium**: Windows native build is painful and may drive users to Docker or WSL.
- **Medium**: AMR creates a commercial dependency/paid path inside an open-source project.
- **Low**: license mix (Apache-2.0, MIT, LGPL for 7zip binaries) is well documented but requires care when redistributing packaged builds.

## BLOCKERS
- No dynamic security audit (no static analysis, no dependency vulnerability scan, no live pen-test).
- No review of individual agent adapter definitions (`apps/daemon/src/runtimes/defs/*.ts`) for per-CLI injection risks.
- No review of `apps/daemon/src/mcp*.ts` for MCP tool authorization beyond what the docs state.
- No verification of whether AMR terms and telemetry implementation match the PRIVACY.md text.
