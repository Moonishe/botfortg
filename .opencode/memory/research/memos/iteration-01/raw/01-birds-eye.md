# Bird’s Eye — MemOS v2.0 (hijzy/MemOS fork)

## Identity & Metrics
- Repository: https://github.com/hijzy/MemOS
- Upstream: MemTensor/MemOS
- Fork metadata: stars=2, forks=0, commits=1,788 (per GitHub UI), latest commit `721568a merge v2.0.17 into main (#1768)`
- Package version: `MemoryOS 2.0.17`
- License: Apache-2.0
- Stack: TypeScript 57.6%, Python 35.2%, HTML 4.3%, JS 1.5%, CSS 0.6%

## Purpose
MemOS brands itself as a "Memory Operating System for LLMs and AI agents" — a long-term memory framework that unifies store / retrieve / manage across multiple memory backends, modalities, and knowledge-base cubes.

## High-level Structure
```
apps/          — TypeScript plugins (memos-local-plugin, memos-local-openclaw, MemOS-Cloud-OpenClaw-Plugin, openwork-memos-integration)
deploy/helm/   — Kubernetes Helm charts
docker/        — Docker compose, Dockerfile, requirements, .env examples
docs/          — Markdown documentation (English + Chinese)
evaluation/    — Benchmarks (LongMemEval, LoCoMo, PersonaMem, etc.)
examples/      — Runnable examples for every module
packages/      — Additional package assets
scripts/       — Helper scripts
src/memos/     — Python core (377 .py files)
tests/         — pytest test suite
```

## Core Modules (Python)
- `mem_os/` — MOS / MOSCore orchestration
- `mem_cube/` — GeneralMemCube (text / activation / parametric / preference memory container)
- `memories/` — memory backends: textual (naive, general, tree, preference), activation (KV cache), parametric (LoRA)
- `mem_chat/` — chat wrapper with memory-aware retrieval
- `mem_agent/` — agent-oriented memory usage
- `mem_reader/` — ingestion pipeline for documents, URLs, images, messages
- `mem_scheduler/` — async Redis/RabbitMQ task scheduler
- `mem_feedback/` — natural-language memory correction
- `mem_user/` — user/cube access management (SQLite, MySQL, Redis)
- `api/` — FastAPI server + MCP server + handlers
- `configs/` — pydantic-based config factories
- `llms/`, `embedders/`, `vec_dbs/`, `graph_dbs/`, `chunkers/`, `reranker/`, `parsers/` — pluggable providers

## Ecosystem Surface
- Cloud API: https://memos.openmem.net / https://memos-dashboard.openmem.net
- Self-hosted: Docker or `uvicorn memos.api.server_api:app`
- Plugins: Hermes Agent local plugin, OpenClaw local/cloud plugin, memos-local-plugin 2.0
- Docs: https://memos-docs.openmem.net
- Paper: arXiv 2507.03724

## Key Claims (from README)
- +43.70% accuracy vs. OpenAI Memory
- 35.24% memory-token savings
- Multi-modal: text, images, tool traces, personas
- Hybrid retrieval: FTS5 + vector
- Multi-cube KB isolation and sharing

## Tools Used
- webfetch: GitHub repo page, README.md, pyproject.toml, LICENSE
- bash: shallow clone, top-level directory listing, recent commit log
- glob: src/**/*.py, apps/**/* structure
