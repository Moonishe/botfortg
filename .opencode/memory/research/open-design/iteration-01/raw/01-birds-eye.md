# 01 Bird's Eye — Open Design repository overview

## SUMMARY
Open Design (nexu-io/open-design) is a local-first, open-source alternative to Claude Design. It is a pnpm monorepo (Node ~24) that ships a Next.js 16 web app, a local Node/Express daemon, an Electron desktop shell, and a rich content layer of skills, design systems, design templates, and plugins. The repository is large (8,523 tracked files, 25 top-level directories, 155 skills, 151 design systems, 261+ plugins). Version is 0.11.0 on main, released under Apache-2.0, with 68.5k GitHub stars and 7.7k forks as of the README.

## CHANGES
No code changes; this is a read-only research pass.

## EVIDENCE
Tools used:
- `webfetch` on README.md, package.json, CHANGELOG.md (pre-flight).
- `glob` for top-level directory structure, skills, design-systems, design-templates, plugins.
- `read` of pnpm-workspace.yaml and local README.md.
- `bash` for repo metrics (git ls-files, directory counts).

Metrics computed from the shallow clone (`--depth 1`):
| Metric | Value |
|---|---|
| Tracked files | 8,523 |
| Top-level directories | 25 |
| Skills (`skills/*/SKILL.md`) | 155 |
| Design systems (`design-systems/*/DESIGN.md`) | 151 |
| Design templates (`design-templates/*/`) | 109 |
| Official plugins (`plugins/_official/`) | 261+ |
| Craft files | 13 |
| Version (package.json) | 0.11.0 |
| Package manager | pnpm 10.33.2 |
| Node engine | ~24 |
| License | Apache-2.0 |

Top-level directories: apps, assets, charts, craft, data, deploy, design-systems, design-templates, docs, e2e, mocks, nix, packages, plugins, prompt-templates, scripts, skills, specs, story, templates, tools, .github, .claude, .claude-plugin, .vaunt.

Key entry points:
- `package.json` root scripts: `tools-dev`, `tools-pack`, `tools-serve`, `guard`, `typecheck`, `postinstall`.
- `apps/web` — Next.js 16 App Router + React client.
- `apps/daemon` — Express daemon, `od` CLI, agent adapters, skills/design-systems resolution, MCP server.
- `apps/desktop` — Electron shell.
- `apps/packaged` — packaged Electron runtime.
- `packages/contracts`, `packages/sidecar-proto`, `packages/sidecar`, `packages/platform`.
- `tools/dev`, `tools/pack`, `tools/serve`.

## RISKS
- Repository size: 8,523 files means any change has a broad blast radius; strict AGENTS.md boundary rules are required to keep the project maintainable.
- Windows native support is documented as "best-effort" with known friction (better-sqlite3 must compile from source).
- The README metrics (stars/forks) come from GitHub UI and may be stale; the actual codebase version is 0.11.0 while README still advertises 0.10.0 in the banner.
- No root `pnpm build`/`pnpm test` aliases by design; contributors must use package-scoped commands.

## BLOCKERS
- Shallow clone gives only one commit; full history and contributor timeline require a deeper clone or GitHub API.
- The 0.11.0 version in package.json vs 0.10.0 banner in README suggests README may lag; confirm release notes at https://github.com/nexu-io/open-design/releases for exact latest stable.
- Some metrics are path-based counts; exact plugin/skill counts depend on registry indexing at runtime.
