# MemOS — Bird's Eye View (Researcher 1, Iteration 02)

> Repository: https://github.com/hijzy/MemOS/tree/main (mirror of https://github.com/MemTensor/MemOS)
> Perspective: Overview / Bird's Eye
> Date: 2026-06-22
> Sources: README.md, pyproject.toml, LICENSE, CONTRIBUTING.md (raw from main branch)

---

## 1. What is MemOS?

MemOS (Memory Operating System) is an open-source **memory infrastructure layer for LLMs and AI agents**, developed by **MemTensor** (记忆张量, memtensor.com.cn). The current release is **v2.0.17 "Stardust" (星尘)**, published on PyPI as the package `MemoryOS`.

The project positions itself as a unified system for **store / retrieve / manage** of long-term memory, enabling context-aware and personalized LLM interactions. It is not a chatbot or agent framework — it is the *memory subsystem* that sits underneath them.

### Academic Foundation

MemOS is backed by peer-reviewed research:

| Paper | arXiv | Role |
|-------|-------|------|
| **MemOS: A Memory OS for AI System** | 2507.03724 | Long version — full architecture, evaluation |
| **MemOS: An Operating System for Memory-Augmented Generation (MAG) in LLMs** | 2505.22101 | Short version — concept proposal (released May 28, 2025, claimed as earliest "Memory OS for LLMs" concept) |
| **Memory3: Language Modeling with Explicit Memory** | JML 2024, vol 3(3), pp.300–346 | Predecessor — memory-layered language model unveiled at WAIC 2024 |

The author list is extensive (35+ contributors), spanning institutions including Shanghai Jiao Tong University, Tsinghua, and industry labs.

### Performance Claims (from README)

| Metric | Value | Benchmark |
|--------|-------|-----------|
| Accuracy vs OpenAI Memory | **+43.70%** | — |
| Memory token savings | **35.24%** | — |
| LoCoMo score | **75.80** | Long-term conversation |
| LongMemEval | **+40.43%** | Long-term memory eval |
| PrefEval-10 | **+2568%** | Preference/personalization |
| PersonaMem | **+40.75%** | Persona consistency |

These are self-reported; the evaluation group in pyproject.toml includes `mem0ai` and `zep-cloud` as comparison baselines, plus `rouge-score`, `bert-score`, `nltk` for automated scoring.

---

## 2. Unified Memory Architecture

MemOS defines a taxonomy of memory types. The CONTRIBUTING.md explicitly distinguishes between **Plaintext, Activation, and Parametric** memory as core concepts. The pyproject.toml optional-dependency groups reveal the implemented backends:

### 2.1 Textual (Plaintext) Memory

Three backend variants, each requiring different infrastructure:

| Backend | Identifier | Database | Status |
|---------|-----------|----------|--------|
| **Tree** (recommended) | `tree_text` | **Neo4j** (graph) + Qdrant (vector, embedded OK) | Primary |
| **General** | `general_text` | **Qdrant** (vector) or compatible | Alternative |
| **Naive** | `naive_text` | None | Testing only |

- Tree memory uses a graph-structured, inspectable, editable representation — explicitly contrasted with "black-box embedding store."
- Neo4j Community lacks native vector retrieval, so it is paired with Qdrant for hybrid graph + vector search.

### 2.2 Preference Memory

| Backend | Identifier | Database |
|---------|-----------|----------|
| **Pref** | `pref` | **Milvus** (vector DB) + datasketch (MinHash for dedup) |

Captures user preferences and persona for personalization. Drives the dramatic PrefEval-10 (+2568%) score.

### 2.3 Activation Memory

Referenced in CONTRIBUTING.md core concepts. In the Memory3 lineage, this corresponds to KV-cache-level memory. The pyproject.toml keywords include `"kv cache"`, confirming activation-level memory is part of the conceptual model.

### 2.4 Parametric Memory

Referenced in CONTRIBUTING.md core concepts. In the Memory3 lineage, this corresponds to LoRA-style weight-level memory. The pyproject.toml keywords include `"lora"`, confirming parametric memory is part of the conceptual model.

### 2.5 Skill Memory

Optional dependency group `skill-mem` requires `alibabacloud-oss-v2` — suggesting skill crystallization storage on Alibaba Cloud OSS. The local plugin (memos-local-plugin 2.0) describes "tiered skill evolution" with L1/L2/L3 layers:

- **L1**: traces (raw interaction logs)
- **L2**: policies (extracted behavioral rules)
- **L3**: world model (abstracted understanding)
- **Crystallized Skills**: reusable procedural knowledge

### 2.6 Tool Memory

Added in v2.0 (2025-12-24). Stores tool usage history for agent planning — agents can recall how tools were used previously.

### 2.7 Multimodal Memory

Added in v2.0. Supports images and charts alongside text, retrieved and reasoned together in one memory system.

---

## 3. Backend Infrastructure Stack

The pyproject.toml optional-dependency groups provide a precise map of the infrastructure:

```
┌─────────────────────────────────────────────────────────────┐
│                      MemOS 2.0 Stack                         │
├──────────────┬──────────────────┬───────────────────────────┤
│ Layer        │ Technology       │ Role                       │
├──────────────┼──────────────────┼───────────────────────────┤
│ Graph DB     │ Neo4j 5.28+      │ Tree-textual memory graph  │
│ Vector DB    │ Qdrant 1.16+     │ General-textual + hybrid   │
│ Vector DB    │ Milvus 2.5+      │ Preference memory          │
│ KV Store     │ Redis 6.2+       │ MemScheduler async queues  │
│ Message Queue│ RabbitMQ (pika)  │ Async task distribution    │
│ Relational   │ MySQL (pymysql)  │ MemUser (multi-user data)  │
│ API Server   │ FastAPI 0.115+   │ REST API + MCP endpoints   │
│ ORM          │ SQLAlchemy 2.0+  │ Relational data access     │
│ Embeddings   │ sentence-trans.  │ Local embedding (optional) │
│ LLM Clients  │ openai, ollama   │ Multi-provider LLM access  │
│ Monitoring   │ prometheus-client│ Metrics/observability      │
│ Chunking     │ chonkie, langchain│ Document ingestion        │
│ File Parsing │ markitdown       │ docx/pdf/pptx/xlsx parsing │
│ MCP          │ fastmcp 2.10+    │ MCP-first integration      │
└──────────────┴──────────────────┴───────────────────────────┘
```

### Async Ingestion: MemScheduler

The v2.0 release rebuilt the task scheduler with **Redis Streams** and queue isolation. Features:
- Task priority levels
- Auto-recovery
- Quota-based scheduling
- Millisecond-level latency for production stability under high concurrency

This replaces the older `schedule` library (still listed in `tree-mem` optional deps for backward compatibility).

### Deployment Modes

v2.0 introduced lightweight deployment with **quick** and **full** modes:
- **Quick**: minimal services (no Redis/RabbitMQ), synchronous operations
- **Full**: all services including MemScheduler for async production workloads

Docker Compose is the recommended deployment path, with slim/full image variants for ARM/x86.

---

## 4. MCP-First Integration

MemOS is explicitly **MCP-first** (Model Context Protocol). Evidence:

1. **`fastmcp` (>=2.10.5,<3.0.0)** is a **core** dependency (not optional) — every installation includes MCP server capability.

2. **MCP operations** (from v2.0 release notes):
   - Memory addition (add memories via MCP)
   - Memory search (retrieve memories via MCP)
   - Memory deletion by memory ID (added v2.0)
   - Memory feedback / correction (added v2.0)

3. The REST API server (`memos.api.server_api:app` via FastAPI/uvicorn) and MCP server are co-deployed — MCP is a first-class protocol, not an add-on.

4. The plugin ecosystem (local plugin for Hermes Agent / OpenClaw) uses MCP-style memory operations as the integration contract.

This means any MCP-compatible client (Claude Desktop, OpenCode, custom agents) can connect to a MemOS instance and use it as the persistent memory backend.

---

## 5. Multi-User Support

Multi-user is a first-class feature, not an afterthought:

1. **`user_id` is a required field** in all API examples (add, search). Every memory operation is scoped to a user.

2. **MemUser subsystem** — dedicated optional dependency group `mem-user` with `pymysql` for MySQL-backed user management. This is separate from the memory storage backends.

3. **Multi-agent memory sharing by `user_id`** — the OpenClaw Cloud Plugin explicitly advertises this. Multiple agents serving the same user share a unified memory store.

4. **MemCube isolation** — memory cubes (`mem_cube_id`) provide composable, isolated memory spaces that can be:
   - Per-user (isolation)
   - Per-project (project-scoped memory)
   - Per-agent (agent-specific memory)
   - Shared (controlled cross-user/cross-project sharing)

5. **Custom tags** — v2.0 added memory filtering with custom tags for fine-grained access control.

---

## 6. Plugin Ecosystem

MemOS has a structured plugin system at two levels:

### 6.1 Python Plugin Entry Points

pyproject.toml defines:
```toml
[project.entry-points."memos.plugins"]
dream = "memos.dream:CommunityDreamPlugin"
```

This is a Python entry-point-based plugin system. The `dream` plugin (Community Dream Plugin) is the first example — likely a community-contributed memory consolidation/optimization plugin.

### 6.2 External Agent Plugins

| Plugin | Target | Type | Highlights |
|--------|--------|------|------------|
| **memos-local-plugin 2.0** | Hermes Agent, OpenClaw | Local-first (NPM: `@memtensor/memos-local-plugin`) | L1/L2/L3 + Skills, 100% local, SQLite + FTS5 + vector hybrid, Memory Viewer dashboard |
| **OpenClaw Cloud Plugin** | OpenClaw | Cloud-hosted | 72% token reduction, multi-agent memory sharing by user_id |
| **MemOS-Cloud-OpenClaw-Plugin** | OpenClaw | Cloud (separate repo) | Auto-recall before agent starts, auto-save after agent finishes |

### 6.3 Local Plugin Architecture (memos-local-plugin 2.0)

Key characteristics from README + News:
- **One core** powers both Hermes Agent and OpenClaw
- **Self-evolving memory**: L1 traces → L2 policies → L3 world model → crystallized Skills, driven by feedback
- **Local-first storage**: persistent SQLite
- **Hybrid search**: FTS5 (full-text) + vector similarity
- **Smart dedup**: avoids redundant memories
- **Tiered skill evolution**: progressive crystallization
- **Multi-agent collaboration**: multiple agents share the local memory core
- **Zero cloud dependency**: 100% on-device
- **Memory Viewer dashboard**: inspect/edit memories via UI
- Distributed via **NPM** (not pip) — targets the JS/TS agent ecosystem

---

## 7. Project Metadata & Engineering

### 7.1 Package Identity

| Field | Value |
|-------|-------|
| PyPI name | `MemoryOS` |
| Version | 2.0.17 |
| Tagline | "Intelligence Begins with Memory" |
| Python | >=3.10 (supports 3.10–3.13) |
| Build system | Poetry (poetry-core) |
| License | Apache 2.0 |
| Copyright | 2025–Present MemTensor Research |

### 7.2 Core Dependencies (always installed)

| Package | Version | Purpose |
|---------|---------|---------|
| openai | >=1.77,<2.0 | LLM API client |
| ollama | >=0.5.0,<0.5.1 | Local LLM client |
| transformers | >=4.51.3,<5.0 | HF model utilities |
| tenacity | >=9.1.2,<10.0 | Retry logic |
| fastapi[all] | >=0.115.12,<0.116 | REST API server |
| sqlalchemy | >=2.0.41,<3.0 | ORM |
| pymysql | >=1.1.0,<2.0 | MySQL driver |
| scikit-learn | >=1.7.0,<2.0 | ML utilities |
| **fastmcp** | >=2.10.5,<3.0 | **MCP server** |
| python-dateutil | >=2.9,<3.0 | Date handling |
| prometheus-client | >=0.23.1,<0.24 | Metrics |
| concurrent-log-handler | >=0.9.28,<1.0 | Process-safe logging |

### 7.3 LLM Provider Support

Configurable via `MOS_CHAT_MODEL_PROVIDER`:
- OpenAI
- Azure OpenAI
- Qwen (DashScope / Alibaba Cloud)
- DeepSeek
- MiniMax
- Ollama (local)
- HuggingFace
- vLLM

### 7.4 Development Workflow

| Aspect | Standard |
|--------|----------|
| VCS | Git (fork → branch off `dev` → PR to `dev`) |
| Dependency mgmt | Poetry >=2.0 |
| Linting | Ruff (B, C4, ERA, I, N, PIE, PGH, RUF, SIM, TC, TID, UP) |
| Testing | pytest + pytest-asyncio (asyncio_mode=auto) |
| Pre-commit | Yes (via `make install`) |
| Commits | Conventional Commits (`feat:`, `fix:`, `docs:`, etc.) |
| Merge strategy | Squash and merge |
| CI | Required green before merge |
| Docs | Separate repo: MemTensor/MemOS-Docs |

### 7.5 Entry Points

- **CLI**: `memos` command → `memos.cli:main`
- **API server**: `uvicorn memos.api.server_api:app --host 0.0.0.0 --port 8001`
- **Plugin discovery**: `memos.plugins` entry-point group

---

## 8. Timeline & Maturity

| Date | Milestone |
|------|-----------|
| 2024-07-04 | Memory3 model unveiled at WAIC 2024 |
| 2025-05-28 | MemOS short paper released (first "Memory OS for LLMs" concept) |
| 2025-07-04 | MemOS long paper on arXiv |
| 2025-07-07 | MemOS v1.0 "Stellar (星河)" preview open-sourced |
| 2025-08-07 | MemOS v1.0.0 "MemCube" release — first stable, with LongMemEval eval |
| 2025-12-24 | **MemOS v2.0 "Stardust (星尘)"** — KB, feedback, multimodal, tool memory, Redis Streams, MCP upgrade, lightweight deploy |
| 2026-03-08 | OpenClaw plugins (Cloud + Local v1.0.0) launched |
| 2026-04-10 | Hermes Agent local plugin launched |
| 2026-05-09 | memos-local-plugin 2.0 — unified core for Hermes + OpenClaw |

The project has been in active development for ~2 years, with v2.0 representing a major architectural milestone 6 months ago and the plugin ecosystem maturing over the last 3 months.

---

## 9. Repository URLs

| Resource | URL |
|----------|-----|
| Main repo (official) | https://github.com/MemTensor/MemOS |
| Mirror (researched) | https://github.com/hijzy/MemOS |
| Docs | https://memos-docs.openmem.net/ |
| Dashboard | https://memos-dashboard.openmem.net/ |
| Website | https://memos.openmem.net/ |
| PyPI | https://pypi.org/project/MemoryOS/ |
| ArXiv (long) | https://arxiv.org/abs/2507.03724 |
| ArXiv (short) | https://arxiv.org/abs/2505.22101 |
| Discord | https://discord.gg/Txbx3gebZR |
| OpenClaw Cloud Plugin | https://github.com/MemTensor/MemOS-Cloud-OpenClaw-Plugin |
| Awesome-AI-Memory | https://github.com/IAAR-Shanghai/Awesome-AI-Memory |
| Local plugin (NPM) | https://www.npmjs.com/package/@memtensor/memos-local-plugin |

---

## 10. Relevance to TelegramHelper Project

For context, this research is being conducted within the TelegramHelper project (Python 3.13, aiogram 3.16, Telethon, SQLAlchemy 2.0 asyncio, SQLite + Qdrant embedded). Potential relevance:

1. **Qdrant overlap** — both projects use Qdrant. MemOS uses Qdrant for vector search; TelegramHelper uses Qdrant embedded. Could share infrastructure.
2. **MCP integration** — TelegramHelper's OpenCode setup uses MCP servers extensively. MemOS is MCP-first, making it a natural fit as a memory backend.
3. **Multi-user** — TelegramHelper is a Telegram bot serving multiple users. MemOS's user_id-scoped memory maps directly to Telegram user IDs.
4. **SQLAlchemy 2.0** — both use SQLAlchemy 2.0+ (MemOS sync, TelegramHelper asyncio). Different async patterns but same ORM generation.
5. **Python 3.10+** — MemOS requires 3.10+, TelegramHelper uses 3.13. Compatible.
6. **Skill evolution (L1/L2/L3)** — the local plugin's tiered skill evolution could inspire similar patterns in TelegramHelper's agent memory.

---

## Researcher Notes

- The `hijzy/MemOS` repository appears to be a fork/mirror of the official `MemTensor/MemOS`. All URLs in pyproject.toml point to `MemTensor/MemOS`. The research findings are based on the main branch content which should be identical.
- The README references dates up to 2026-05-09, confirming active development.
- Performance claims are self-reported and should be verified against the arXiv paper (2507.03724) for methodology.
- The `eval` dependency group includes `mem0ai` and `zep-cloud` as baselines, suggesting competitive benchmarking was done.
- The local plugin is distributed via NPM (not pip), indicating the plugin ecosystem targets JavaScript/TypeScript agent frameworks (Hermes, OpenClaw) rather than Python.
