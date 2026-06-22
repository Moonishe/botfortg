# MemOS Deep Research Synthesis — Iteration 01

## SUMMARY
MemOS is a Python-first, plugin-extensible memory framework for LLM agents, positioned as a "Memory Operating System." The hijzy/MemOS fork is a personal mirror of upstream MemTensor/MemOS at version 2.0.17, with a heavy TypeScript plugin layer (apps/) and a Python core (src/memos/) that unifies textual, activation, and parametric memory behind a FastAPI REST server, an MCP server, and a Python SDK. The recommended deployment combines Neo4j (graph), Qdrant (vector), and Redis/RabbitMQ (async scheduler), making it a multi-database system with notable operational complexity but also the most advanced retrieval pipeline among open-source LLM memory libraries.

## KEY_FINDINGS
1. **Unified but backend-heavy architecture**: `MOSCore` / `MOS` orchestrate `GeneralMemCube` instances; each cube can hold text, activation, parametric, and preference memory. The default recommended path is `tree_text` + `neo4j-community` + `qdrant`.
2. **Hybrid retrieval pipeline**: tree-text memory mixes graph traversal, BM25, vector search, reranking, and optional internet retrieval (Tavily/Bocha/Xinyu). This matches the README claim of "FTS5 + vector".
3. **Async ingestion via MemScheduler**: Redis Streams or RabbitMQ back a scheduler with task labels (add/query/answer/pref/read). Sync mode is still the default in README examples.
4. **MCP-first integration**: `MOSMCPServer` exposes memory tools via `fastmcp`, making MemOS consumable as an MCP resource by Hermes/OpenClaw/custom agents.
5. **Multi-modal claims, mostly text in core**: README advertises images/tool traces/personas; the Python core `tree_text_memory/retrieve/searcher.py` contains code paths for tool memory, skill memory, and persona memory, but image handling is more evident in the plugin/TS side and MemReader.
6. **Security posture is basic**: secrets via `.env`/`load_dotenv`, no visible auth middleware on REST endpoints, prompt-based safety guard, China-ecosystem-specific defaults (Bailian/DashScope, DingTalk, OSS).
7. **Code scale is large**: 377 Python files in `src/memos`; several files exceed 1000 lines, mixing extraction, retrieval, reranking, and scheduling logic.
8. **Plugin ecosystem dominates the surface**: 4 app packages (memos-local-plugin, memos-local-openclaw, MemOS-Cloud-OpenClaw-Plugin, openwork-memos-integration) show the project is as much a Hermes/OpenClaw plugin vendor as a library.
9. **Version 2.0.17 is current**: pyproject.toml reports `MemoryOS 2.0.17`, Python 3.10–3.13, Poetry + uv, Ruff linting.
10. **Apache-2.0 licensed**: permissive, clear contribution guidelines, PRs target `dev` branch.

## ARCHITECTURE
```
+-------------------------------------------------------------+
|                      Client / Agent                          |
|  (Hermes, OpenClaw, custom MCP, REST client)                 |
+-----------------------+-------------------------------------+
                        |
        +---------------+---------------+
        |        FastAPI REST            |
        |   /product/{add,search,chat,    |
        |   feedback,scheduler,delete}    |
        +---------------+---------------+
                        |
        +---------------+---------------+
        |   Handlers (Search/Add/Chat/  |
        |   Feedback/Scheduler/Memory)  |
        +---------------+---------------+
                        |
        +---------------+---------------+
        |          MOSCore / MOS         |
        |  multi-user, cube registry,    |
        |  chat history, scheduler hook  |
        +---------------+---------------+
                        |
        +---------------+---------------+
        |   GeneralMemCube(s)            |
        |   per user/cube                |
        +-------+-------+-------+-------+
                |       |       |
    +-----------+       |       +-----------+
    v                   v                   v
+--------+  +--------+  +--------+  +--------+
| text   |  | act    |  | param  |  | pref   |
| (tree) |  | (kv)   |  | (lora) |  | (pref) |
+---+----+  +--------+  +--------+  +--------+
    |
+---+-------------------+
|  Tree Text Memory retrieve pipeline        |
|  graph (Neo4j) -> BM25/keyword -> vector   |
|  (Qdrant/Milvus) -> reranker -> reasoner   |
+------------------------+
                        |
        +---------------+---------------+
        |  LLM (extractor/dispatcher)   |
        |  Embedder (universal_api/etc.)  |
        +-------------------------------+
```

## Data Flow
1. **Write**: `POST /product/add` -> `AddHandler` -> `MOSCore` -> `GeneralMemCube.text_mem.add()` -> LLM extraction -> Neo4j nodes + Qdrant vectors + optional scheduler queue.
2. **Read**: `POST /product/search` -> `SearchHandler` -> `text_mem.search()` -> retrieve/searcher -> graph + BM25 + vector + rerank -> `TextualMemoryItem` list.
3. **Chat**: `ChatHandler` first searches memory, then optionally adds the turn, then calls chat LLM.
4. **Feedback**: `FeedbackHandler` -> `MemFeedback` -> LLM-based correction/update -> graph_db update/delete.

## API_SURFACE
### REST endpoints (prefix `/product`)
- `POST /product/add` — add memories
- `POST /product/search` — search memories
- `POST /product/chat/complete` — non-streaming chat
- `POST /product/chat/stream` — SSE chat
- `POST /product/chat/stream/playground` — playground chat
- `POST /product/feedback` — natural-language memory correction
- `POST /product/get_all`, `POST /product/get_memory`, `GET /product/get_memory/{memory_id}` — memory retrieval
- `POST /product/delete_memory` — delete by IDs
- `GET /product/scheduler/*` — scheduler status/wait/queue
- `GET /health` — health check

### Python SDK classes
- `MOS` / `MOSCore` — orchestration
- `GeneralMemCube` — memory container
- `MemoryFactory` — memory backend factory
- `MOSMCPServer` — MCP server wrapper
- `UserManager` — user/cube access

### Configuration factories
- `MOSConfig`, `MemOSConfigFactory`
- `GeneralMemCubeConfig`, `MemoryConfigFactory`
- `TreeTextMemoryConfig`, `GeneralTextMemoryConfig`, etc.
- `EmbedderConfigFactory`, `VectorDBConfigFactory`, `GraphDBConfigFactory`, `LLMConfigFactory`

## RISKS
- **Operational**: requires Neo4j + Qdrant + Redis/RabbitMQ; multi-DB resilience is non-trivial.
- **Security**: no endpoint auth, secrets in env, prompt-level safety guard, OSS/DingTalk secrets in code path.
- **Maintainability**: 377 Python files, many very large; mixing sync/async; empty/temporary methods.
- **Correctness**: LLM-driven extraction/organization is non-deterministic; async mode breaks read-after-write unless explicit waits.
- **Vendor defaults**: hard-wired for Bailian/DashScope/Qwen ecosystem; switching to OpenAI/DeepSeek requires careful config override.
- **Ecosystem lock-in**: most end-user value is delivered through Hermes/OpenClaw plugins, not a standalone simple API.

## USAGE_PATTERNS
- **Self-hosted REST API**: Docker compose, then `POST /product/{add,search}`.
- **Python SDK**: `from memos.mem_os.main import MOS; mos = MOS(config); mos.register_mem_cube(cube); mos.chat(...)`.
- **MCP resource**: `MOSMCPServer.run()` exposes tools to any MCP client.
- **Agent plugin**: install `memos-local-plugin` (Hermes) or `memos-local-openclaw` (OpenClaw).
- **Async ingestion**: enable scheduler + Redis for high-throughput multi-agent scenarios.

## CONFIG_EXAMPLES
### Minimal self-hosted `.env`
```bash
OPENAI_API_KEY=sk-...
OPENAI_API_BASE=https://api.openai.com/v1
MOS_CHAT_MODEL=gpt-4o-mini
MEMRADER_API_KEY=sk-...
MEMRADER_API_BASE=https://api.openai.com/v1
MEMRADER_MODEL=gpt-4o-mini
MOS_EMBEDDER_BACKEND=universal_api
MOS_EMBEDDER_MODEL=text-embedding-3-small
MOS_EMBEDDER_API_BASE=https://api.openai.com/v1
MOS_EMBEDDER_API_KEY=sk-...
EMBEDDING_DIMENSION=1536
MOS_RERANKER_BACKEND=cosine_local
NEO4J_BACKEND=neo4j-community
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=12345678
NEO4J_DB_NAME=neo4j
MOS_NEO4J_SHARED_DB=false
DEFAULT_USE_REDIS_QUEUE=false
ENABLE_CHAT_API=true
```

### Python SDK one-liner
```python
from memos.mem_os.main import MOS
from memos.mem_os.utils.default_config import get_default
config, cube = get_default(openai_api_key="sk-...", text_mem_type="tree_text", user_id="u-1")
mos = MOS(config=config)
mos.register_mem_cube(cube)
print(mos.chat("What do I like?", user_id="u-1"))
```

## RECOMMENDATIONS_FOR_TELEGRAMHELPER
1. **Borrow the memory abstraction, not the whole stack**: TelegramHelper already uses SQLite + Qdrant embedded. Adopting the full MemOS would require Neo4j and Redis. Instead, port the `BaseTextMemory` interface and the `add/search/update/delete` contract to your existing `src/db/models/` + Qdrant layer.
2. **Adopt the retrieve pipeline design**: keyword/BM25 + vector + reranker hybrid is a proven pattern. Your project can replicate it using SQLite FTS5 (already planned) + Qdrant + a small cosine reranker.
3. **Use the feedback/correction pattern**: `MemFeedback` shows how to let users correct memories via natural language. This maps well to Telegram bot commands like `/memory_fix <id> <correction>`.
4. **MCP exposure**: `MOSMCPServer` is a good reference for how to expose memory tools via `fastmcp`. TelegramHelper could add an MCP layer for external agents without coupling to Telegram.
5. **Avoid the scheduler complexity unless needed**: `MemScheduler` is powerful but adds Redis/RabbitMQ. For a single-bot deployment, synchronous writes with SQLite + Qdrant embedded are sufficient.
6. **Keep pydantic-settings config**: MemOS uses pydantic factories everywhere. TelegramHelper already uses pydantic-settings; maintain that for consistency.
7. **Do not copy the security model**: TelegramHelper should add auth/rate-limiting per Telegram user_id and never store raw secrets in memory or logs. MemOS has gaps here.
8. **Skills / personas / tool traces**: the MemOS plugin concept of "L1 trace, L2 policy, L3 world model" can inspire TelegramHelper command/skill evolution layer, but implement it incrementally.

## CONFIDENCE
**Medium-High**. The repository was cloned and hundreds of source files were inspected; key files were read end-to-end. However, the full 1,788-commit history, test suite, and separate docs repo were not audited in depth, and many TS plugin files were only surface-scanned.

## GAPS
- No deep audit of the TypeScript plugin packages (`apps/memos-local-plugin`, `memos-local-openclaw`, etc.) beyond file listing.
- The full evaluation/benchmark scripts and reproducibility were not run.
- The separate `MemOS-Docs` repository was not fetched.
- Exact behavior of the scheduler under failure (retries, dead-letter) was not traced.
- Image multi-modal memory code paths were not fully validated.
- The `mem_agent` and `dream` modules were only briefly inspected.
- No runtime tests were executed; findings are static-analysis only.

## EVIDENCE
- GitHub repo page: https://github.com/hijzy/MemOS (stars/forks/commits)
- `README.md` (v2.0, feature list, quickstart)
- `pyproject.toml` (version 2.0.17, dependencies, dev tooling)
- `LICENSE` (Apache-2.0)
- `CONTRIBUTING.md` (branch model, commit style)
- `src/memos/api/server_api.py`, `src/memos/api/routers/server_router.py`, `src/memos/api/mcp_serve.py`
- `src/memos/mem_os/core.py`, `src/memos/mem_os/main.py`, `src/memos/configs/mem_os.py`
- `src/memos/mem_cube/general.py`, `src/memos/memories/factory.py`, `src/memos/memories/textual/base.py`
- `src/memos/configs/memory.py`, `src/memos/configs/embedder.py`, `src/memos/embedders/factory.py`
- `docker/.env.example`, `docker/docker-compose.yml`
- `examples/README.md`
- Local clone: `C:/Users/My/AppData/Local/Temp/opencode/memos-research`

## BLOCKERS
None for research. For adoption in TelegramHelper, blockers would be: (1) Neo4j dependency, (2) lack of auth/rate limiting in MemOS API, (3) large dependency footprint, (4) need to align async/sync patterns with TelegramHelper's aiogram/Telethon stack.
