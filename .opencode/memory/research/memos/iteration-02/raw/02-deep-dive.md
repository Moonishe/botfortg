# MemOS Deep Dive — Technical Architecture (Researcher 2)

**Repository:** https://github.com/hijzy/MemOS (fork of MemTensor/MemOS)
**Branch:** main | **Version:** MemOS 2.0 "Stardust"
**Date:** 2026-06-22
**Sources fetched:** core.py, main.py, general.py, factory.py, configs/mem_os.py, server_api.py, server_router.py, tree.py, searcher.py, recall.py, advanced_searcher.py, default_config.py, vec_dbs/factory.py, graph_dbs/factory.py, reranker/factory.py, simple_fastmcp_serve.py + directory listings

> **Note on data sources:** raw.githubusercontent.com and the GitHub REST API were
> unavailable (connection failures + 403 rate-limit). Files were fetched via
> `github.com/raw/refs/heads/main/` and `cdn.jsdelivr.net/gh/hijzy/MemOS@main/` CDN mirrors.
> The `warpgrep_github_search` tool was not available in this environment; code search was
> performed by fetching source files directly and reading directory listings.

---

## 1. MOSCore / MOS — Orchestration Layer

### 1.1 MOSCore (`src/memos/mem_os/core.py`, ~1203 lines)

**MOSCore** is the central orchestrator — an "operating system layer" that manages multiple
`GeneralMemCube` instances and routes memory operations across them with multi-user isolation.

**Constructor (`__init__`):**
- Accepts `MOSConfig` + optional `UserManager`.
- Creates `chat_llm` via `LLMFactory.from_config(config.chat_model)`.
- Creates `mem_reader` via `MemReaderFactory.from_config(config.mem_reader)`.
- `mem_cubes`: `OptimizedThreadSafeDict[str, GeneralMemCube]` when `user_manager` is provided
  (thread-safe for multi-user product-server scenarios), else plain `dict`.
- Validates user exists via `user_manager.validate_user(user_id)` — raises if user is
  inactive/missing.
- **MemScheduler**: lazily initialized if `enable_mem_scheduler=True`. Created via
  `SchedulerFactory.from_config(scheduler_config)`, then `initialize_modules()` injects
  `chat_llm`, `process_llm` (from mem_reader), and `db_engine` (from user_manager).
  `mem_scheduler.start()` launches the scheduler.

**Key methods:**

| Method | Behavior |
|--------|----------|
| `chat(query, user_id, base_prompt)` | Iterates accessible cubes → `text_mem.search()` per cube → builds system prompt with memories → `chat_llm.generate()`. Submits QUERY_TASK_LABEL + ANSWER_TASK_LABEL messages to scheduler. Supports activation memory (KV cache) for HuggingFace backend. |
| `search(query, user_id, install_cube_ids, top_k, mode, internet_search, moscube, session_id)` | Returns `MOSSearchResult` dict with `text_mem`, `act_mem`, `para_mem`, `pref_mem` lists. Runs textual + preference search in **parallel** via `ContextThreadPoolExecutor(max_workers=2)`. Mode: `"fast"` vs `"fine"`. |
| `add(messages, memory_content, doc_path, mem_cube_id, user_id, session_id, task_id)` | Three input paths: messages (chat), memory_content (single string), doc_path (files). For `tree_text` backend: routes through `mem_reader.get_memory()` → `text_mem.add()`. For non-tree: direct `TextualMemoryItem` add. Preference memory processed in parallel. Submits ADD_TASK_LABEL / MEM_READ_TASK_LABEL / PREF_ADD_TASK_LABEL to scheduler. Sync vs async mode determined by `text_mem.mode`. |
| `get / get_all / update / delete / delete_all` | Standard CRUD with cube-access validation (`_validate_cube_access`). |
| `dump / load` | Delegate to `mem_cube.dump()/load()` with `memory_types` filter. |
| `register_mem_cube(name_or_path, mem_cube_id, user_id)` | Accepts `GeneralMemCube` instance, local dir path (`init_from_dir`), or remote repo name (`init_from_remote_repo` from HuggingFace). Registers cube in DB via `user_manager.create_cube()` or `add_user_to_cube()`. Checks embedder consistency with MOSConfig. |
| `unregister_mem_cube(mem_cube_id)` | Removes from `mem_cubes` dict. |
| `create_user / list_users / create_cube_for_user / get_user_info` | User management via `UserManager`. |

**Validation pattern:** Every operation calls `_validate_user_exists()` then
`_validate_cube_access()` — defense-in-depth for multi-tenant isolation.

**Scheduler integration:** When `enable_mem_scheduler=True`, messages are submitted as
`ScheduleMessageItem` with labels: `QUERY_TASK_LABEL`, `ANSWER_TASK_LABEL`,
`ADD_TASK_LABEL`, `MEM_READ_TASK_LABEL`, `PREF_ADD_TASK_LABEL`. The scheduler handles
async memory ingestion (Redis Streams backend).

### 1.2 MOS (`src/memos/mem_os/main.py`, ~480 lines)

**MOS** extends `MOSCore` with:
- **Auto-configuration**: `MOS.simple()` classmethod → reads `OPENAI_API_KEY`,
  `OPENAI_API_BASE`, `MOS_TEXT_MEM_TYPE` from env → `get_default()` → auto-registers
  default cube.
- **PRO_MODE / CoT enhancement**: When `config.PRO_MODE=True`, `chat()` is overridden to
  use `_chat_with_cot_enhancement()`:
  1. `cot_decompose()` — LLM decomposes complex query into sub-questions (JSON output:
     `{is_complex, sub_questions}`).
  2. If not complex → falls back to `super().chat()`.
  3. `get_sub_answers()` — searches + answers each sub-question **in parallel**
     (`ContextThreadPoolExecutor`, up to 10 workers).
  4. `_generate_enhanced_response_with_context()` — synthesizes sub-answers + memories +
     chat history via `SYNTHESIS_PROMPT`.
  5. Graceful fallback to standard chat on any exception.

---

## 2. GeneralMemCube — Memory Container (`src/memos/mem_cube/general.py`)

**GeneralMemCube** is a container that loads/dumps **four memory types**:

| Slot | Type | Base Class | Backends (from MemoryFactory) |
|------|------|------------|-------------------------------|
| `text_mem` | Textual | `BaseTextMemory` | `naive_text`, `general_text`, `tree_text`, `simple_tree_text` |
| `act_mem` | Activation | `BaseActMemory` | `kv_cache`, `vllm_kv_cache` |
| `para_mem` | Parametric | `BaseParaMemory` | `lora` |
| `pref_mem` | Preference | `BaseTextMemory` | `pref_text`, `simple_pref_text` |

**Initialization:** Each slot is created via `MemoryFactory.from_config(config)` unless
`backend == "uninitialized"` (then `None`). Property getters log warnings and return `None`
for uninitialized slots; setters enforce type validation.

**Factory methods:**
- `init_from_dir(dir, memory_types, default_config)` — loads `config.json` →
  `GeneralMemCubeConfig.from_json_file()` → optional `merge_config_with_default()` →
  `GeneralMemCube(config)` → `load()`.
- `init_from_remote_repo(cube_id, base_url="https://huggingface.co/datasets")` —
  downloads repo via `download_repo()` then `init_from_dir()`.

**load/dump:** Both accept `memory_types` filter: `["text_mem", "act_mem", "para_mem", "pref_mem"]`.
`dump()` requires an **empty directory** (raises `MemCubeError` otherwise). Config is always
dumped to `config.json`. Schema version validated on load via `model_schema` comparison.

---

## 3. MemoryFactory & Config Factories

### 3.1 MemoryFactory (`src/memos/memories/factory.py`)

Registry pattern — `backend_to_class` ClassVar dict maps backend string → class:

```python
{
    "naive_text": NaiveTextMemory,
    "general_text": GeneralTextMemory,
    "tree_text": TreeTextMemory,
    "simple_tree_text": SimpleTreeTextMemory,
    "pref_text": PreferenceTextMemory,
    "simple_pref_text": SimplePreferenceTextMemory,
    "kv_cache": KVCacheMemory,
    "vllm_kv_cache": VLLMKVCacheMemory,
    "lora": LoRAMemory,
}
```

`from_config(config_factory)` reads `config_factory.backend`, looks up the class, and
instantiates with `config_factory.config`. Raises `ValueError` on unknown backend.

### 3.2 MOSConfig (`src/memos/configs/mem_os.py`)

Pydantic `BaseConfig` model:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `user_id` | str | `"root"` | Multi-user isolation key |
| `session_id` | str | `uuid4()` | Dialogue session tracking |
| `chat_model` | `LLMConfigFactory` | factory | Chat LLM config |
| `mem_reader` | `MemReaderConfigFactory` | factory | Memory extraction reader |
| `mem_scheduler` | `SchedulerConfigFactory\|None` | None | Async scheduler config |
| `user_manager` | `UserManagerConfigFactory` | sqlite | User DB backend |
| `max_turns_window` | int | 15 | Conversation history window |
| `top_k` | int | 5 | Memory retrieval count |
| `enable_textual_memory` | bool | True | Toggle text memory |
| `enable_activation_memory` | bool | False | Toggle KV-cache memory |
| `enable_parametric_memory` | bool | False | Toggle LoRA memory |
| `enable_preference_memory` | bool | False | Toggle preference memory |
| `enable_mem_scheduler` | bool | False | Toggle async scheduler |
| `PRO_MODE` | bool | False | Toggle CoT decomposition |

`MemOSConfigFactory` wraps MOSConfig with a `model_validator` that coerces a raw dict into
`MOSConfig(**self.config)`.

### 3.3 Default Config (`src/memos/mem_os/utils/default_config.py`)

`get_default()` produces `(MOSConfig, GeneralMemCube)` tuple with minimal input:
- **tree_text**: Neo4j config (bolt://localhost:7687, multi-DB or single-DB for Community
  Edition), `extractor_llm` + `dispatcher_llm` (OpenAI), embedder (universal_api/OpenAI),
  optional reorganize.
- **general_text**: Qdrant vector DB (collection_name, vector_dimension=3072, cosine),
  file-based storage (`textual_memory.json`).
- Scheduler (general_scheduler): Redis Streams, thread pool, parallel dispatch, activation
  memory update interval.

### 3.4 Supporting Factories

| Factory | Backends |
|---------|----------|
| `GraphStoreFactory` | `neo4j` (Neo4jGraphDB), `neo4j-community` (Neo4jCommunityGraphDB), `polardb` (PolarDBGraphDB), `postgres` (PostgresGraphDB) |
| `VecDBFactory` | `qdrant` (QdrantVecDB), `milvus` (MilvusVecDB) |
| `RerankerFactory` | `cosine_local`/`cosine` (CosineLocalReranker), `http_bge`/`bge` (HTTPBGEReranker, model bge-reranker-v2-m3), `http_bge_strategy` (HTTPBGERerankerStrategy), `noop` (NoopReranker). Singleton-cached. |
| `LLMFactory` | OpenAI, Azure, Ollama, HuggingFace, vLLM (inferred from imports) |

---

## 4. Tree Text Memory Retrieve Pipeline

This is the most complex subsystem. The pipeline spans four files:

```
TreeTextMemory (tree.py)
  └─ AdvancedSearcher (advanced_searcher.py) extends Searcher (searcher.py)
       ├─ TaskGoalParser (task_goal_parser.py)
       ├─ GraphMemoryRetriever (recall.py)
       │    ├─ _graph_recall   → Neo4j (key/tag metadata matching)
       │    ├─ _vector_recall  → embedding similarity (Neo4j vector index or Qdrant)
       │    ├─ _bm25_recall    → EnhancedBM25 (in-memory)
       │    └─ _fulltext_recall → graph_store.search_by_fulltext (jieba/PolarDB)
       ├─ Reranker (reranker/factory.py)
       └─ MemoryReasoner (reasoner.py)
```

### 4.1 TreeTextMemory (`src/memos/memories/textual/tree.py`)

**Constructor initializes:**
- `extractor_llm`, `dispatcher_llm` (via LLMFactory)
- `embedder` (via EmbedderFactory)
- `graph_store` (Neo4jGraphDB via GraphStoreFactory)
- `bm25_retriever` (EnhancedBM25, conditional on `search_strategy["bm25"]`)
- `reranker` (RerankerFactory, defaults to `cosine_local` with level_weights
  `{topic:1.0, concept:1.0, fact:1.0}`)
- `memory_manager` (MemoryManager — handles add/organize/reorganize with WorkingMemory=20,
  LongTermMemory=1500, UserMemory=480 defaults)
- `internet_retriever` (optional, via InternetRetrieverFactory)

**Memory scopes:** `WorkingMemory`, `LongTermMemory`, `UserMemory`, `RawFileMemory`,
`ToolSchemaMemory`, `ToolTrajectoryMemory`, `SkillMemory`, `PreferenceMemory`, `OuterMemory`.

**search()** creates an `AdvancedSearcher` (aliased as `Searcher`) per call and delegates.
**get_relevant_subgraph()** returns neighborhood subgraph: embeds query → top-k similar
nodes → `graph_store.get_subgraph(depth=N)` → merges nodes+edges. Supports both
`embedding` and `fulltext` search types (jieba tokenization for Chinese).

### 4.2 Searcher (`searcher.py`) — Base Retrieve/Rerank Pipeline

**search() flow:** `User query → TaskGoalParser → GraphMemoryRetriever → Reranker →
MemoryReasoner → Final output`

**`_parse_task()`:** Calls `task_goal_parser.parse()` which returns `ParsedTaskGoal`
(rephrased_query, keys, tags, memories, context, internet_search flag). Embeds query +
extra memories.

**`_retrieve_paths()` — Parallel multi-path retrieval** (ContextThreadPoolExecutor, max 5 workers):

| Path | Method | Memory Scope | Notes |
|------|--------|-------------|-------|
| A | `_retrieve_from_working_memory` | WorkingMemory | Graph recall + rerank |
| B | `_retrieve_from_long_term_and_user` | LongTermMemory + UserMemory | Optional CoT query expansion (`_cot_query`), parallel scope search, RawFile dedup |
| C | `_retrieve_from_internet` | OuterMemory | Conditional on `internet_retriever` + `parsed_goal.internet_search` |
| Keyword | `_retrieve_from_keyword` | LongTermMemory + UserMemory | Weighted keyword extraction (jieba for zh, FastTokenizer for en), fulltext search |
| D | `_retrieve_from_tool_memory` | ToolSchemaMemory + ToolTrajectoryMemory | Optional, parallel |
| E | `_retrieve_from_skill_memory` | SkillMemory | Optional |
| F | `_retrieve_from_preference_memory` | PreferenceMemory | Optional |

**CoT query expansion (`_cot_query`):** When `vec_cot=True`, LLM decomposes query into
sub-questions (COT_PROMPT for fine, SIMPLE_COT_PROMPT for fast), embeds each, concatenates
embeddings for multi-vector search.

**post_retrieve():** Deduplicates by memory text → sorts by score → trims to top_k per
memory type → updates usage history (async, fire-and-forget).

### 4.3 GraphMemoryRetriever (`recall.py`) — Hybrid Recall

**`retrieve()`** runs up to **four recall strategies in parallel** (ContextThreadPoolExecutor,
max 3 workers), then merges by ID (dedup keeping first occurrence):

1. **`_graph_recall`** — Structured Neo4j lookup:
   - Key-based: `key IN parsed_goal.keys` + `memory_type = scope`
   - Tag-based: `tags CONTAINS parsed_goal.tags` (overlap ≥ 2 tags)
   - Post-filters nodes by key/tag match. `use_fast_graph` adds `status="activated"` filter
     + parallel node processing.

2. **`_vector_recall`** — Embedding similarity:
   - Two parallel paths: Path A (no priority filter), Path B (with `search_priority`).
   - `graph_store.search_by_embedding(vector, top_k, status, scope, filter)` per embedding.
   - Deduplicates by ID keeping highest score. Injects `relativity` score into metadata.

3. **`_bm25_recall`** — EnhancedBM25 (in-memory):
   - Builds corpus from `graph_store.get_by_metadata()` + `get_nodes()`.
   - Corpus name = `user_name + search_filter values` (per-user isolation).
   - BM25 query = `query + parsed_goal.keys` (deduplicated).
   - `bm25_retriever.search(query, node_dicts, top_k, corpus_name)`.

4. **`_fulltext_recall`** — Graph DB fulltext:
   - `graph_store.search_by_fulltext(query_words, top_k, status, scope, filter)`.
   - Used when `use_fast_graph=True`. Query words from `parsed_goal.keys`.

**WorkingMemory special case:** Retrieved directly via `get_all_memory_items(scope,
status="activated")` — no vector/graph filtering, just top_k trim.

### 4.4 AdvancedSearcher (`advanced_searcher.py`) — Deep Search

Extends Searcher with **multi-stage iterative retrieval**:

**`deep_search()`** — up to `thinking_stages=3` + 1 final judgment stage:
1. Initial `retrieve()` + `post_retrieve()`.
2. Per stage: `stage_retrieve()` — LLM evaluates if current memories suffice
   (`can_answer`, `reason`, `retrieval_phrases`). If `can_answer` → return.
3. If not: extracts new `retrieval_phrases` → additional `retrieve()` per phrase →
   merges + `post_retrieve()` → `memory_recreate_enhancement()` (LLM rewrites/condenses
   memories).
4. Final stage: `judge_memories()` — LLM judges sufficiency. Returns top_k.
5. Graceful per-stage error handling (continues to next stage on exception).

**Key params:** `stage_retrieve_top=3`, `max_retry_times=2`, `deep_search_top_k_bar=2`.

**Prompt templates:** Loaded from `PROMPT_MAPPING` in `templates/advanced_search_prompts.py`
(stage1_expand_retrieve, stage2_expand_retrieve, ..., memory_judgement,
memory_recreate_enhancement).

---

## 5. FastAPI REST Endpoints

### 5.1 server_api.py — App Bootstrap

```python
app = FastAPI(title="MemOS Server REST APIs", version="1.0.1")
app.mount("/download", StaticFiles(directory=FILE_LOCAL_PATH))
app.add_middleware(RequestContextMiddleware, source="server_api")
app.include_router(server_router)  # /product prefix
```

- `GET /health` → `{status, service, version}` for load balancer checks.
- Exception handlers: `RequestValidationError`, `ValueError`, `HTTPException`, generic
  `Exception` — all via `APIExceptionHandler`.
- Plugin manager: `plugin_manager.discover()` → `plugin_manager.init_app(app)`.
- CLI: `uvicorn memos.api.server_api:app --host 0.0.0.0 --port 8001`.

### 5.2 server_router.py — `/product` Endpoints

**Architecture:** Class-based handlers with dependency injection.
`handlers.init_server()` returns components dict → `HandlerDependencies.from_init_server()`
→ handlers: `SearchHandler`, `AddHandler`, `ChatHandler` (conditional on
`ENABLE_CHAT_API` env), `FeedbackHandler`.

**Components:** `mem_scheduler`, `llm`, `naive_mem_cube`, `redis_client`,
`status_tracker` (TaskStatusTracker), `graph_db`.

| Endpoint | Method | Handler | Purpose |
|----------|--------|---------|---------|
| `/product/search` | POST | SearchHandler | Search memories |
| `/product/add` | POST | AddHandler | Add memories |
| `/product/scheduler/allstatus` | GET | scheduler_handler | Full scheduler status |
| `/product/scheduler/status` | GET | scheduler_handler | User/task status |
| `/product/scheduler/task_queue_status` | GET | scheduler_handler | Queue backlog |
| `/product/scheduler/wait` | POST | scheduler_handler | Wait for idle (timeout) |
| `/product/scheduler/wait/stream` | GET | scheduler_handler | SSE progress stream |
| `/product/chat/complete` | POST | ChatHandler | Non-streaming chat |
| `/product/chat/stream` | POST | ChatHandler | SSE streaming chat |
| `/product/chat/stream/playground` | POST | ChatHandler | Playground chat |
| `/product/chat/stream/business_user` | POST | ChatHandler | Business user chat |
| `/product/suggestions` | POST | suggestion_handler | Suggested queries |
| `/product/get_all` | POST | memory_handler | All memories or subgraph |
| `/product/get_memory` | POST | memory_handler | Paginated memories |
| `/product/get_memory/{id}` | GET | memory_handler | Single memory |
| `/product/get_memory_by_ids` | POST | memory_handler | Batch get |
| `/product/delete_memory` | POST | memory_handler | Delete memories |
| `/product/feedback` | POST | FeedbackHandler | Natural-language feedback |
| `/product/get_user_names_by_memory_ids` | POST | graph_db | User lookup |
| `/product/exist_mem_cube_id` | POST | graph_db | Cube existence check |
| `/product/delete_memory_by_record_id` | POST | graph_db | Hard/soft delete by record |
| `/product/recover_memory_by_record_id` | POST | graph_db | Recover deleted |
| `/product/get_memory_dashboard` | POST | memory_handler | Dashboard data |

**Pydantic models** (from `product_models.py`): `APISearchRequest`, `APIADDRequest`,
`APIChatCompleteRequest`, `ChatRequest`, `ChatPlaygroundRequest`, `ChatBusinessRequest`,
`APIFeedbackRequest`, `DeleteMemoryRequest`, `GetMemoryRequest`, `SuggestionRequest`, etc.

---

## 6. MCP Server

### 6.1 Pattern: FastMCP Proxy (`examples/mem_mcp/simple_fastmcp_serve.py`)

The MCP integration uses **FastMCP** as a thin proxy layer over the REST API:

```python
from fastmcp import FastMCP
mcp = FastMCP("MemOS MCP via Server API")
API_BASE_URL = os.getenv("MEMOS_API_BASE_URL", "http://localhost:8001/product")

@mcp.tool()
def add_memory(memory_content, user_id, cube_id=None):
    resp = requests.post(f"{API_BASE_URL}/add", json={...})
    return resp.json()["message"]

@mcp.tool()
def search_memories(query, user_id, cube_ids=None):
    resp = requests.post(f"{API_BASE_URL}/search", json={...})
    return json.dumps(resp.json()["data"])

@mcp.tool()
def chat(query, user_id):
    resp = requests.post(f"{API_BASE_URL}/chat/complete", json={...})
    return resp.json()["data"]["response"]
```

**Transports:** `stdio` (default), `http`, `sse` — selectable via CLI `--transport`.

**Client example:** `simple_fastmcp_client.py` (companion file).

### 6.2 MOSMCPServer — Not Found

A dedicated `MOSMCPServer` class was **not located** in this fork's source tree. The
`src/memos/mcp/` path returns 404. Directory exploration of `src/memos/` (24 subdirs),
`packages/`, `apps/`, `examples/`, and `src/memos/mem_agent/`, `src/memos/mem_chat/`,
`src/memos/search/` found no MCP server class. The MCP capability is delivered via the
FastMCP proxy pattern in `examples/mem_mcp/`. The `MOSMCPServer` name may originate from
the upstream `MemTensor/MemOS` (which also returned 404 for `src/memos/mcp/server.py`),
an older version, or the cloud-hosted variant. **Confidence: medium** — the pattern is
clear (FastMCP → REST), but a dedicated server class may exist in a path not enumerable
via CDN/blob fetching.

---

## 7. Data Flow Summary

```
User Request
    │
    ▼
┌─────────────────────────────────────────┐
│  MOS (main.py) — PRO_MODE CoT?          │
│   ├─ Yes: cot_decompose → sub_answers   │
│   │       → synthesis prompt            │
│   └─ No:  MOSCore.chat / search / add   │
└───────────────┬─────────────────────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌──────────┐
│UserMgr │ │Scheduler│ │MemReader │
│(SQLite)│ │(Redis) │ │(LLM+chunk)│
└────────┘ └────────┘ └──────────┘
                │
    ┌───────────┴───────────┐
    ▼                       ▼
┌──────────────┐   ┌───────────────┐
│GeneralMemCube│   │  per-cube     │
│ text_mem     │   │  operations   │
│ act_mem      │   └───────────────┘
│ para_mem     │
│ pref_mem     │
└──────┬───────┘
       │ (tree_text backend)
       ▼
┌──────────────────────────────────────┐
│  TreeTextMemory                       │
│   ├─ MemoryManager (add/organize)     │
│   └─ AdvancedSearcher                 │
│        ├─ TaskGoalParser (LLM)        │
│        ├─ GraphMemoryRetriever        │
│        │   ├─ _graph_recall (Neo4j)   │
│        │   ├─ _vector_recall (embed)  │
│        │   ├─ _bm25_recall (Enhanced) │
│        │   └─ _fulltext_recall        │
│        ├─ Reranker (cosine/bge/noop)  │
│        ├─ MemoryReasoner (LLM)        │
│        └─ [deep_search: multi-stage]  │
└──────────────────────────────────────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
┌────────┐ ┌────────┐ ┌──────────┐
│ Neo4j  │ │ Qdrant │ │ Enhanced │
│/PolarDB│ │/Milvus │ │  BM25    │
│/Postgres│ └────────┘ └──────────┘
└────────┘
```

---

## 8. Key Architectural Observations

1. **Multi-tenant by design**: UserManager + cube-access validation on every operation;
   `OptimizedThreadSafeDict` for concurrent multi-user access; per-user Neo4j DB names.

2. **Hybrid retrieval (4-way parallel)**: Graph (structured key/tag) + Vector (embedding
   similarity) + BM25 (lexical) + Fulltext (DB-native). All run concurrently, merged by ID
   dedup, then reranked. This is the "graph → BM25 → vector → reranker" pipeline, though
   the actual order is **parallel recall → merge → rerank**, not sequential.

3. **Two search modes**: `fast` (simpler CoT prompts, skips some paths) vs `fine` (full
   CoT decomposition, embedding-based context). `deep_search` adds iterative multi-stage
   expansion with LLM-judged sufficiency.

4. **Async ingestion**: MemScheduler (Redis Streams) decouples memory add/search from
   heavy LLM extraction. Sync mode = immediate; async mode = scheduler processes later.

5. **Pluggable backends everywhere**: Every component (LLM, embedder, graph DB, vector DB,
   reranker, chunker, scheduler, user manager) uses factory + config pattern. No hard
   dependency on a specific vendor.

6. **Activation memory**: KV-cache based, only for HuggingFace/vLLM backends — extracts
   internal attention tensors. Not applicable to API-based LLMs.

7. **Plugin system**: `plugin_manager.discover()` + `init_app(app)` — hooks defined in
   `plugins/hook_defs.py`, managed by `plugins/manager.py`.

---

## 9. Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| MOSCore/MOS orchestration | **High** | Full source of core.py + main.py read |
| GeneralMemCube | **High** | Full source read |
| MemoryFactory + configs | **High** | Full source read |
| Tree retrieve pipeline | **High** | tree.py + searcher.py + recall.py + advanced_searcher.py all read |
| FastAPI REST endpoints | **High** | server_api.py + server_router.py fully read |
| MCP server | **Medium** | FastMCP proxy pattern confirmed; dedicated MOSMCPServer class not found in fork |
| Infra factories (graph/vec/reranker) | **High** | All factory.py files read |
| Plugin system internals | **Low** | Only directory listing + manager imports seen; base.py/hook_defs.py not fetched |
