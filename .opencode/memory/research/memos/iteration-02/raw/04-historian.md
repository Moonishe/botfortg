# Researcher 4 — Historian (Context Perspective)

**Repository:** https://github.com/hijzy/MemOS (fork of MemTensor/MemOS)
**Date of research:** 2026-06-22
**Sources fetched:**
- https://github.com/hijzy/MemOS (main page, metrics, README, news)
- https://github.com/hijzy/MemOS/blob/main/pyproject.toml (version + build config)
- https://github.com/hijzy/MemOS/commits/main (recent commit history)
- https://github.com/hijzy/MemOS/tags (release tags on the fork)
- https://raw.githubusercontent.com/hijzy/MemOS/main/CONTRIBUTING.md (workflow)
- https://github.com/MemTensor/MemOS (upstream for comparison)

> Note: raw.githubusercontent.com fetch for pyproject.toml failed twice with transport errors; the file was successfully retrieved via the GitHub blob view instead. Content is identical.

---

## 1. Repository Identity & Fork Relationship

| Attribute | hijzy/MemOS (fork) | MemTensor/MemOS (upstream) |
|---|---|---|
| Stars | 2 | 9.9k |
| Forks | 0 | 906 |
| Watchers | 0 | 47 |
| Commits (main) | 1,788 | 1,828 |
| Tags | 12 | 31 releases |
| Latest tag (fork) | v2.0.0 (Dec 24, 2025) | v2.0.20 (Jun 18, 2026) |
| pyproject version | 2.0.17 | 2.0.20 (upstream latest) |
| Open PRs | 0 | 94 |
| Open Issues | n/a | 145 |
| License | Apache-2.0 | Apache-2.0 |

**Key observations:**
- The fork is ~40 commits behind upstream (1,788 vs 1,828), indicating it is actively synced but not perfectly current.
- The fork has **no tags after v2.0.0** (Dec 24, 2025), yet `pyproject.toml` declares version `2.0.17`. This means the fork tracks upstream's version bumps in code without cutting its own release tags post-v2.0.0.
- The fork's README uniquely points the `memos-local-plugin` link to `hijzy/MemOS` (not `MemTensor/MemOS`), and the NPM package `@memtensor/memos-local-plugin` is referenced — suggesting **hijzy is the primary maintainer/author of the local plugin layer** that lives in `apps/`.
- Commit history shows `hijzy` (user ID 33757498) is an active committer with PR numbers in the 1700–1800 range, the same range as upstream PRs. Commits by `bittergreen`, `wustzdy`, `CaralHsi`, `endxxxx` also appear — these are upstream MemTensor team members. **This strongly indicates hijzy is a MemTensor team member or close collaborator**, not an external community fork.

---

## 2. Project Naming & Codename Evolution

MemOS uses astronomical codenames for major releases:
- **v1.0 — "Stellar (星河)"** — the preview/open-source release (Jul 7, 2025)
- **v2.0 — "Stardust (星尘)"** — the major 2.0 release (Dec 24, 2025)

The package name on PyPI is **`MemoryOS`** (not `MemOS`): `pip install MemoryOS[<feature>]`.
The import path is `memos` (package lives in `src/memos/`).

---

## 3. Full Version Timeline

### Pre-release / Research Origins

| Date | Event |
|---|---|
| 2024-07-04 | **Memory3 model** unveiled at WAIC 2024 — memory-layered architecture (conceptual ancestor) |
| 2025-05-28 | **Short version paper** released (arXiv 2505.22101) — "earliest work to propose Memory OS for LLMs" |
| 2025-07-04 | **Long paper** on arXiv (2507.03724) — "MemOS: A Memory OS for AI System" |

### Tagged Releases (from fork tags page)

| Tag | Date | Upstream PR | Highlights |
|---|---|---|---|
| **v0.2.0** | Jul 11, 2025 | #66 | Structure reorganizer, conflict resolver, OpenAI memory integration, memory reader |
| **v0.2.1** | Jul 21, 2025 | #131 | Multiple embedders (Ark/Volcengine, Universal API), Neo4j integration, MCP server support + client demo, longmemeval evaluation pipeline, multi-language support |
| **v0.2.2** | Jul 29, 2025 | #191 | Internet search (memreader), memos API mode, user manager factory, Nebula features, chat history |
| **v1.0.0** | Aug 7, 2025 | #228 | **"MemCube" release** — word-game demo, LongMemEval, BochaAISearchRetriever, Playground launch |
| **v1.0.1** | Sep 10, 2025 | #275 | LoCoMo RAG + LongMemEval scripts, request context/middleware with trace IDs, NebulaGraph session pool, multilingual templates, vLLM adapter |
| **v1.1.0** | Sep 24, 2025 | #340 | **OpenTelemetry** support, chatbot API + reranker filter, Nebula 5.1.1, parallelized recall/search, API client |
| **v1.1.1** | Sep 24, 2025 | #346 | (Patch release, same day as v1.1.0) |
| **v1.1.2** | Oct 11, 2025 | — | (Patch release) |
| **v1.1.3** | Nov 7, 2025 | #471 | **Major**: async add (plain & preference), Preference Memory pipeline, Reranker strategy suite, BM25 for TreeTextMemory, MemReader structural parsing, API scheduler modularization, Redis ORM, Nacos dynamic config, PolarDB graph backend, unified graph factory (Nebula/Neo4j/PolarDB), Milvus, PrefEval standardization |
| **v2.0.0** | Dec 24, 2025 | #716 | **"Stardust (星尘)"** — Comprehensive KB (doc/URL parsing + cross-project sharing), memory feedback & precise deletion, multi-modal memory (images/charts), tool memory, Redis Streams scheduler, streaming/non-streaming chat, MCP upgrade, lightweight quick/full deployment |

### Post-v2.0.0 (version bumps in pyproject.toml, no fork tags)

Based on commit history and upstream releases, versions 2.0.1 through 2.0.20 were released on upstream. The fork's pyproject.toml is at **2.0.17**, with the version bump commit dated **May 25, 2026** (`chore: change version number to 2.0.17 (#1800)` by wustzdy).

Upstream's latest is **v2.0.20** (Jun 18, 2026), meaning the fork is ~3 patch versions behind upstream.

### Plugin Ecosystem Timeline (from README News section)

| Date | Event |
|---|---|
| 2026-03-08 | **OpenClaw Plugin** — Cloud & Local launched. Cloud: 72% token reduction, multi-agent sharing. Local v1.0.0: on-device SQLite, hybrid search (FTS5 + vector), Memory Viewer dashboard |
| 2026-04-10 | **Hermes Agent Local Plugin** — hybrid retrieval (FTS5 + vector), smart dedup, tiered skill evolution, multi-agent collaboration, 100% local |
| 2026-05-09 | **memos-local-plugin 2.0** — one core for Hermes Agent + OpenClaw; L1 traces, L2 policies, L3 world model, crystallized Skills |

---

## 4. Technology Stack Evolution

### Build & Dependency Management

| Aspect | Detail |
|---|---|
| Build system | `poetry-core` (poetry.core.masonry.api) |
| Poetry version | `>=2.0` |
| Also present | `uv.lock` (both Poetry and uv are used — dual lockfile strategy) |
| Python requirement | `>=3.10` (classifiers: 3.10, 3.11, 3.12, 3.13) |
| Linting | Ruff `^0.11.8` with extended rules: B, C4, ERA, I, N, PIE, PGH, RUF, SIM, TC, TID, UP; ignores RUF001, PGH003 |
| Ruff target | py310 |
| Line length | 100 |
| Testing | pytest `^8.3.5`, pytest-asyncio `^0.23.5`, pytest-cov, pytest-html |
| asyncio_mode | auto |
| Pre-commit | `^4.2.0` |
| PyPI mirror | Tsinghua mirror (mirrors.tuna.tsinghua.edu.cn) as supplemental source |

> **Discrepancy note:** CONTRIBUTING.md says "Python 3.9+" as prerequisite, but pyproject.toml requires `>=3.10`. The contributing doc is slightly outdated — the actual floor is Python 3.10.

### Core Dependencies (always installed)

openai (>=1.77,<2.0), ollama (>=0.5.0,<0.5.1), transformers (>=4.51.3,<5.0), tenacity, fastapi[all], sqlalchemy (>=2.0.41,<3.0), pymysql, scikit-learn, fastmcp (>=2.10.5,<3.0), python-dateutil, prometheus-client, concurrent-log-handler

### Optional Dependency Groups (feature-gated)

| Extra | Key deps | Purpose |
|---|---|---|
| `tree-mem` | neo4j, schedule | TreeTextualMemory (graph-based) |
| `mem-scheduler` | redis, pika (RabbitMQ) | Async task scheduling |
| `mem-user` | pymysql | MySQL user management |
| `mem-reader` | chonkie, markitdown, langchain-text-splitters | Document parsing/chunking |
| `pref-mem` | pymilvus, datasketch | Preference memory (vector + MinHash) |
| `skill-mem` | alibabacloud-oss-v2 | Skill memory (OSS storage) |
| `tavily` | tavily-python | Tavily web search |
| `all` | All above + torch, sentence-transformers, qdrant-client, volcengine-sdk, nltk, rake-nltk, jieba, rank-bm25, cachetools | Everything |

### Entry Points

```
[project.scripts]
memos = "memos.cli:main"

[project.entry-points."memos.plugins"]
dream = "memos.dream:CommunityDreamPlugin"
```

The `dream` plugin entry point is notable — it's a community dream plugin loaded via Python entry points, showing the project has a **plugin discovery mechanism** built into the package metadata.

---

## 5. Architecture Evolution — Python Core vs TypeScript Plugin Layer

### Language Distribution

| Language | Fork % | Upstream % |
|---|---|---|
| TypeScript | 57.6% | 57.7% |
| Python | 35.2% | 35.2% |
| HTML | 4.3% | 4.2% |
| JavaScript | 1.5% | 1.5% |
| CSS | 0.6% | 0.6% |
| Shell | 0.6% | 0.6% |

**TypeScript dominates the codebase by line count** (57.6%), which is surprising for a "Python" memory OS. This is explained by the `apps/` directory containing the TypeScript plugin layer (`memos-local-plugin`) — the NPM package `@memtensor/memos-local-plugin` that provides the Hermes Agent / OpenClaw integration.

### Two-Layer Architecture

```
apps/                          ← TypeScript plugin layer (NPM: @memtensor/memos-local-plugin)
  memos-local-plugin/          ← Local-first memory core for Hermes Agent & OpenClaw
                                (FTS5 + vector hybrid search, SQLite, skill evolution)

src/memos/                     ← Python core (PyPI: MemoryOS)
  cli.py                       ← CLI entry point
  api/server_api.py            ← FastAPI REST server
  dream.py                     ← Community dream plugin
  ...                          ← Memory backends, schedulers, readers, etc.

packages/                      ← Shared packages (likely TypeScript)
deploy/helm/                   ← Kubernetes Helm charts
docker/                        ← Docker deployment
evaluation/                    ← Benchmark scripts (LoCoMo, LongMemEval, PrefEval, PersonaMem)
examples/                      ← Usage examples
tests/                         ← Python tests (pytest, mirrors src/ structure)
```

### Memory Backend Evolution

The project evolved from a single naive text backend to a multi-backend architecture:

1. **v0.2.0** (Jul 2025): Basic plaintext memory, structure reorganizer
2. **v0.2.1** (Jul 2025): Neo4j graph DB added, MCP support
3. **v1.0.1** (Sep 2025): NebulaGraph session pool, trace ID middleware
4. **v1.1.0** (Sep 2025): OpenTelemetry observability, parallelized recall
5. **v1.1.3** (Nov 2025): PolarDB graph backend, unified graph factory (Nebula/Neo4j/PolarDB), Redis ORM, Milvus for preference memory, BM25 for TreeTextMemory
6. **v2.0.0** (Dec 2025): Redis Streams scheduler (replacing older scheduler), multi-modal (images), tool memory, knowledge base
7. **Post-v2.0**: Local plugin layer (TypeScript) with FTS5+vector hybrid search, skill evolution (L1/L2/L3 + crystallized skills)

### Self-Evolving Memory Model (latest architecture)

| Layer | Purpose |
|---|---|
| L1 Traces | Raw interaction history |
| L2 Policies | Learned preferences/behaviors |
| L3 World Model | User understanding |
| Crystallized Skills | Reusable patterns (cross-task reuse) |

---

## 6. Development Workflow (from CONTRIBUTING.md)

### Branch Strategy

- **PRs target `dev` branch, NOT `main`** — PRs against `main` will be asked to retarget
- Feature branches: `feat/...`, `fix/...`, `docs/...`
- Workflow: fork → branch off `dev` → `make install` → `make test` → rebase onto `upstream/dev` → commit → push → PR against `dev`
- Merge strategy: **squash and merge** by default
- Review timeline: "within a few business days"

### Commit Convention

Conventional Commits format: `<type>: <description>`
Types: feat, fix, docs, style, refactor, test, chore, ci
Imperative mood ("add", not "added")

### Memory Backend Setup for Contributors

| Backend | Identifier | DB needed |
|---|---|---|
| Tree (recommended) | `tree_text` | Neo4j (graph) |
| General | `general_text` | Qdrant (vector) |
| Naive (testing only) | `naive_text` | None |
| Preference | `pref` | Milvus |

### Testing

- `make test` runs all tests
- Tests mirror `src/` structure under `tests/`
- Unit tests should mock external services
- Bug fixes require regression tests

### Documentation

Lives in a **separate repo**: `MemTensor/MemOS-Docs`

---

## 7. Recent Commit Activity on the Fork (May 2026)

The fork's recent commits (May 18–26, 2026) reveal the active development focus:

| Date | Commit | Author | Focus |
|---|---|---|---|
| May 26 | merge v2.0.17 into main (#1768) | bittergreen | Version merge |
| May 25 | change version to 2.0.17 (#1800) | wustzdy | Version bump |
| May 21 | preserve image_url sources (#1779) | bittergreen | Multimodal fix |
| May 20 | Mem agent 0514 (#1774) | hijzy | Memory agent work |
| May 20 | serialize Hermes viewer daemon (#1773) | hijzy | Local plugin fix |
| May 20 | pluginize context recall / dream plugin (#1772) | bittergreen | Dream plugin refactor |
| May 20 | release @memtensor/memos-local-plugin v2.0.5 | github-actions | NPM release |
| May 20 | AddPhaseEnricher & context & search (#1767) | CaralHsi | Dream plugin features |
| May 20 | skill trigger bug (#1770) | Wang-Daoji | Skill memory fix |
| May 20 | fix redis serialization (#1766) | wustzdy | Scheduler fix |
| May 19 | merge service from mq (#1764) | leslie1992-dqp | MQ integration |
| May 19 | prevent process leak via PID file (#1765) | syzsunshine219 | Bridge fix |
| May 19 | Fix Hermes bridge packaged runtime (#1761) | hijzy | Local plugin fix |
| May 19 | speed imports and stabilize overview (#1760) | hijzy | Local plugin perf |
| May 19 | V2.0.16 (#1753) | CaralHsi | Version merge |
| May 18 | repair hub team sharing (#1754) | hijzy | Local plugin feature |
| May 18 | refine reflection (#1752) | whipser030 | Reflection logic |
| May 18 | start stdio before host LLM fallback (#1750) | hijzy | Local plugin fix |

**Pattern:** hijzy's commits focus almost exclusively on `memos-local-plugin` (the TypeScript layer in `apps/`), while bittergreen, wustzdy, CaralHsi, and others work on the Python core. This confirms the fork owner (hijzy) is the **local plugin maintainer** within the MemTensor team.

---

## 8. Benchmark Evolution

The project maintains a strong evaluation story:

| Benchmark | MemOS Result | Improvement |
|---|---|---|
| LoCoMo | 75.80 | — |
| LongMemEval | — | +40.43% vs baseline |
| PrefEval-10 | — | +2568% |
| PersonaMem | — | +40.75% |
| vs OpenAI Memory | — | +43.70% accuracy |
| Token savings | — | 35.24% |

Benchmark scripts live in `evaluation/` and evolved from:
- v1.0.0: LongMemEval scripts added
- v1.0.1: LoCoMo RAG scripts added
- v1.1.3: PrefEval standardization, PersonaMem pipeline upgrades

---

## 9. Upstream vs Fork Divergence Analysis

### What the fork has that's notable
- The README explicitly points `memos-local-plugin` to `hijzy/MemOS/tree/main/apps/memos-local-plugin` — this is the canonical home for the local plugin
- NPM releases of `@memtensor/memos-local-plugin` are triggered from this fork (github-actions bot commits releases here)

### What upstream has that the fork lacks
- Upstream has `.claude/agents/`, `.codex/agents/`, `AGENTS.md`, `CLAUDE.md` — AI coding agent configurations. The fork does NOT show these in its file listing, suggesting either they were added on upstream after the fork's last sync, or the fork excludes them.
- Upstream has 31 releases (v2.0.20 latest); fork has 12 tags (v2.0.0 latest)
- Upstream has 40 more commits (1,828 vs 1,788)
- Upstream has Discussions enabled; fork does not
- Upstream description: "Self-evolving memory OS for LLM & AI Agents: ultra-persistent memory, hybrid-retrieval, and cross-task skill reuse, with 35.24% token savings"
- Fork description: "Build memory-native AI agents with Memory OS — an open-source framework for long-term memory, retrieval, and adaptive learning in large language models."

### Fork sync behavior
The fork appears to be a **working fork** — hijzy develops the local plugin here and syncs from upstream regularly. The 40-commit gap is likely just the lag between upstream's latest pushes and the fork's last merge. The commit "Merge remote-tracking branch 'upstream/main'" (May 20, cadd397) confirms active upstream tracking.

---

## 10. License & Citation

**License:** Apache-2.0 (both fork and upstream)

**Key papers:**
1. arXiv 2507.03724 (Jul 2025) — "MemOS: A Memory OS for AI System" (long version, 37 authors)
2. arXiv 2505.22101 (May 2025) — "MemOS: An Operating System for Memory-Augmented Generation (MAG) in LLMs" (short version)
3. Memory3 (2024) — "Memory3: Language Modeling with Explicit Memory" (Journal of Machine Learning, ancestor work)

The project claims: "We publicly released the Short Version on May 28, 2025, making it the earliest work to propose the concept of a Memory Operating System for LLMs."

---

## 11. Confidence Assessment

| Finding | Confidence | Reason |
|---|---|---|
| Version 2.0.17 confirmed | **High** | Directly read from pyproject.toml |
| Fork relationship & metrics | **High** | GitHub pages directly observed |
| Tagged release timeline | **High** | Tags page with dates and PR numbers |
| hijzy = local plugin maintainer | **High** | Commit patterns + README links point to hijzy/MemOS for plugin |
| Post-v2.0.0 version history (2.0.1–2.0.17) | **Medium** | Inferred from commit messages + upstream release count; individual patch notes not all fetched |
| Python 3.10 floor (not 3.9) | **High** | pyproject.toml says >=3.10 despite CONTRIBUTING saying 3.9+ |
| Dual Poetry+uv strategy | **High** | Both poetry.lock and uv.lock present |
| Fork is ~40 commits behind upstream | **High** | Direct count comparison (1,788 vs 1,828) |
| hijzy is MemTensor team member | **Medium-High** | Inferred from shared PR numbering, co-committers, direct-to-main commits; not explicitly confirmed |
