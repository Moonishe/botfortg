# Historian — MemOS Evolution & Development Context

## Version Timeline

### 2024
- **2024-07-04** — Memory3 model unveiled at WAIC 2024 (pre-MemOS academic foundation)

### 2025
- **2025-05-28** — Short paper "MemOS: An Operating System for Memory-Augmented Generation" released on arXiv (2505.22101)
- **2025-07-04** — MemOS paper on arXiv: 2507.03724
- **2025-07-07** — MemOS v1.0: Stellar (星河) Preview Release
- **2025-08-07** — MemOS v1.0.0 (MemCube) Release: first MemCube, LongMemEval, Bocha search, Playground
- **2025-12-24** — MemOS v2.0: Stardust (星尘) Release: KB docs/URLs, feedback & deletion, multi-modal, tool memory, Redis Streams scheduler, MCP, lightweight deployment

### 2026
- **2026-03-08** — MemOS OpenClaw Plugin Cloud & Local
- **2026-04-10** — MemOS Hermes Agent Local Plugin
- **2026-05-09** — memos-local-plugin 2.0 (L1 trace / L2 policy / L3 world model / crystallized skills)
- **Current package version** — `2.0.17` (per pyproject.toml)

## Repository Signals
- **Upstream**: MemTensor/MemOS (organization account of 记忆张量 / MemTensor Research)
- **Fork**: hijzy/MemOS — personal fork with no open PRs, 2 stars, 0 forks, 1,788 commits (mirrors upstream)
- **Latest local commit**: `721568a merge v2.0.17 into main (#1768)` — this is a single merge commit in the shallow clone; the full graph is not available without a deep clone.
- **Contribution model**: PRs target `dev` branch, not `main`; Conventional Commits; docs live in separate MemTensor/MemOS-Docs repo.

## Development Tooling
- **Python**: 3.10–3.13
- **Dependency managers**: Poetry (primary) + uv (lock file present)
- **Lint/Format**: Ruff with bugbear, comprehensions, eradicate, isort, naming, pie, pygrep, ruff, simplify, type-checking, tidy-imports, pyupgrade
- **Tests**: pytest + pytest-asyncio + pytest-cov + pytest-html
- **CI**: pre-commit hooks
- **Docker**: multi-service compose (memos + neo4j + qdrant); slim/full image variants referenced in docs

## Architecture Evolution
- v1.0 focused on **MemCube** (textual memory + activation KV cache + parametric LoRA)
- v2.0 shifted to **product/server mode** with:
  - REST API (`/product/*` endpoints)
  - multi-user / multi-cube access control
  - Redis-backed scheduler
  - MCP server integration
  - Knowledge-base ingestion (MemReader)
  - feedback/correction loop
  - TypeScript plugins (Hermes/OpenClaw)

## Community & Governance
- Issues/PRs/Discussions on upstream MemTensor/MemOS
- Discord server, WeChat group, X account @MemOS_dev
- Apache-2.0 license with clear contributor guidelines
- Maintainers emphasize: branch off `dev`, add tests, update docs in separate repo

## Tools Used
- webfetch: README.md (news/changelog), GitHub repo page (commits, stars, forks)
- read: pyproject.toml (version, classifiers, dependencies, dev tooling), CONTRIBUTING.md (workflow, commit style, branch rules)
- bash: git log in cloned repo
