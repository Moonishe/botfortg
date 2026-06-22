# 04 — Historian: evolution, releases, and recent trajectory

## Output contract
- **SUMMARY**: Hermes is a very fast-moving project. Since the fork/rewrite from OpenClaw, it has accumulated ~12,638 commits across all refs, released 18 versioned tags in 2026, and shipped v0.17.0 (“The Reach Release”) on 2026-06-19. The recent ~50 days saw ~5,000 commits. The code is actively refactoring toward pluginization (providers, platforms, terminal backends) and has closed in-tree memory providers to keep core coupling low.
- **CHANGES**: None.
- **EVIDENCE**: `git log`, `git tag`, GitHub release page HTML for v0.17.0, `CONTRIBUTING.md`, `AGENTS.md`, `pyproject.toml` dependency-pinning comments, `SECURITY.md` references to past incidents.
- **RISKS**: Velocity this high creates regression risk, documentation drift, and makes it hard for downstream forks to keep up. The move to pluginize core surfaces is good but still in progress.
- **BLOCKERS**: GitHub API rate-limited; detailed contributor graphs and PR/issue counts were not fetched live.

## Release timeline (version tags from `git tag`)
| Tag | Release |
|-----|---------|
| v2026.3.12 | v0.11.0 (early 2026) |
| v2026.3.17 | v0.12.0 |
| v2026.3.23 | v0.13.0 |
| v2026.3.28 | v0.13.1 |
| v2026.3.30 | v0.13.2 |
| v2026.4.3  | v0.14.0 |
| v2026.4.8  | v0.14.1 |
| v2026.4.13 | v0.14.2 |
| v2026.4.16 | v0.14.3 |
| v2026.4.23 | v0.14.4 |
| v2026.4.30 | v0.14.5 |
| v2026.5.7  | v0.15.0 |
| v2026.5.28 | v0.15.1 |
| v2026.5.29 | v0.15.2 |
| v2026.5.29.2 | v0.15.3 |
| v2026.6.5  | v0.16.0 |
| v2026.6.19 | v0.17.0 (latest) |

## v0.17.0 — "The Reach Release" (2026-06-19)
From the GitHub release page:
- ~1,475 commits since v0.16.0
- ~800 merged PRs
- 1,693 files changed
- 235,390 insertions / 50,730 deletions
- 300+ issues closed
- 245 community contributors

Highlights:
- **iMessage via Photon Spectrum** — managed iMessage line pool, no Mac relay.
- **Raft agent network** — new gateway adapter; wake payloads carry only metadata, not message bodies.
- **Desktop app v0.17** — rebindable shortcuts, OS notifications, live subagent watch-windows, composer model selector, RTL/bidi, VS Code terminal pane, per-thread drafts, VS Code Marketplace theme install.
- **Background/async subagents** — delegate work and keep going.
- **Image generation editing** — in-painting/variant editing.
- **xAI Grok → Cursor Composer model** — via xAI subscription.
- **Dashboard profile builder + secure login**.
- **Skills Hub browser overhaul**.
- **Memory tool major upgrade**.
- **Curator** — reduced aux-model budget on routine runs.
- Security round included.

## Recent commit velocity
- `git log --since=2026-05-01 --oneline` returned ~5,093 commits in ~50 days.
- Recent commits at the time of research:
  - `fix(cli): detect containerd/CRI cgroup-v2 containers in is_container()`
  - `feat(gateway): gate/inject stable human-readable message timestamps`
  - `fix(desktop): honor pre-session model pick + restore global reasoning/speed defaults`
  - `fix(models): pass model.base_url to fetch_models in /model picker`
  - `fix(skills): ignore support docs in skill discovery`
  - `feat(xai): default to grok-build-0.1`
  - `feat(hooks): session:compress event_callback for MemPalace sync`

## Architectural evolution
1. **From OpenClaw**: Hermes includes `hermes claw migrate` to import settings, memories, skills, API keys, and messaging configs from OpenClaw.
2. **Core extraction**: Historically `run_agent.py` and `cli.py` were ~12k and ~11k LOC god-files. The team is actively extracting modules into `agent/`, `hermes_cli/`, and `tools/`.
3. **Pluginization**: The recent commit `0fce82164 Pluginify provider/platform/terminal backends` indicates a major refactor to move providers, platforms, and terminal backends into the plugin system. This aligns with `AGENTS.md` rule: "New memory backends must ship as standalone plugin repos."
4. **Memory provider closure**: `CONTRIBUTING.md` states "We are no longer accepting new memory providers into this repo" — the in-tree set is closed to reduce coupling.
5. **Dependency hardening**: After March 2026 (litellm) and May 2026 (Mini Shai-Hulud worm), exact pins and upper-bound policies were adopted. `pyproject.toml` contains lengthy comments explaining the rationale.
6. **Security policy maturation**: `SECURITY.md` is a detailed trust-model document, not a generic "report bugs here" page. It defines scope, boundaries, and what is out of scope.

## Contribution model
- Conventional Commits (`fix(cli):`, `feat(gateway):`, etc.).
- Branch naming: `fix/description`, `feat/description`, `docs/description`, `test/description`, `refactor/description`.
- PR priorities: bug fixes > cross-platform > security > performance > new skills > new tools > docs.
- Automated triage sweeper can close PRs as `implemented_on_main`, `cannot_reproduce`, `incoherent`.
- Strong code-review intent: verify premise against the codebase before merging; many closed PRs are due to wrong premise rather than bad code.

## Historical risks
- **God-files and large refactors**: The project routinely merges large mechanical refactors (e.g., extracting clusters out of `cli.py` / `run_agent.py`). This is good long-term but risky short-term.
- **Windows compatibility**: A dedicated `scripts/check-windows-footguns.py` exists because the project has been bitten repeatedly by POSIX-only idioms (`os.kill(pid,0)`, `termios`, etc.).
- **Supply chain**: The exact-pin policy is a direct response to real incidents.
- **Community skills/plugins**: The project intentionally pushes third-party capability to the edges; the core team is not responsible for what a community skill does.

## Migration path
- For OpenClaw users: `hermes claw migrate` (with `--dry-run`, `--preset`, `--overwrite`).
- For new users: one-line install (`curl | bash` or PowerShell), then `hermes setup` or `hermes setup --portal` for Nous Portal.

## Bottom line
Hermes is not a stable, slow-moving framework; it is a product iterating at startup velocity. The latest release is a major expansion of reach (channels, desktop, subagents). The codebase is healthy in the sense that it documents its own intent and hardens against past incidents, but it is also large and rapidly changing.
