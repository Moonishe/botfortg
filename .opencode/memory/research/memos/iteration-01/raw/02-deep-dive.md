# Deep Dive — MemOS Architecture & Data Flow

## Foundational Abstractions

### Memory Taxonomy
MemOS divides memory into three conceptual layers:
1. **Plaintext / Textual Memory** — explicit facts, user preferences, KB documents
2. **Activation Memory** — KV-cache compression for inference speed-ups
3. **Parametric Memory** — LoRA adapters / model-layer personalization

### Core Class Hierarchy
```
BaseMemory (load/dump)
  ├─ BaseTextMemory   ── NaiveTextMemory, GeneralTextMemory, TreeTextMemory, PreferenceTextMemory, Simple*
  ├─ BaseActMemory    ── KVCacheMemory, VLLMKVCacheMemory
  └─ BaseParaMemory   ── LoRAMemory

MemoryFactory.from_config(config_factory) -> concrete memory instance

BaseMemCube
  └─ GeneralMemCube
       ├─ text_mem : BaseTextMemory
       ├─ act_mem  : BaseActMemory
       ├─ para_mem : BaseParaMemory
       └─ pref_mem : BaseTextMemory
```

### Orchestration Layer
- `MOSCore` (src/memos/mem_os/core.py) — multi-user cube manager, scheduler, chat history, user validation
- `MOS` (src/memos/mem_os/main.py) — extends MOSCore with public API-facing methods
- `MemOSConfigFactory` (src/memos/configs/mem_os.py) — pydantic config with switches for each memory type

### Storage Pluggability
| Component | Backends |
|-----------|----------|
| Vector DB | Qdrant, Milvus |
| Graph DB  | Neo4j, Neo4j Community, PolarDB, PostgreSQL |
| Embedder  | Ollama, universal_api (OpenAI-compatible), sentence_transformers, Ark (Volcano) |
| LLM       | OpenAI, Azure, Qwen/DashScope, DeepSeek, MiniMax, Ollama, HuggingFace, vLLM |
| Reranker  | cosine_local, http_bge |
| User DB   | SQLite, MySQL, Redis |

## Data Flow (Self-Hosted API)

### 1. Add memory
```
POST /product/add
  APIADDRequest {user_id, mem_cube_id, messages, async_mode}
  -> AddHandler
  -> multi_mem_cube (single/composite cube view)
  -> text_mem.add() / mem_scheduler.submit_messages()
  -> graph_db (Neo4j) + vec_db (Qdrant) + optional reorganizer
  -> MemoryResponse
```

### 2. Search memory
```
POST /product/search
  APISearchRequest {query, user_id, mem_cube_id, ...}
  -> SearchHandler
  -> text_mem.search(query, top_k, info={chat_history, user_id, session_id})
  -> retrieve pipeline: keyword/bm25 -> vector -> reranker -> reasoner
  -> SearchResponse
```

### 3. Chat
```
POST /product/chat/complete
  APIChatCompleteRequest
  -> ChatHandler
  -> search_handler.search + add_handler.add (feedback loop)
  -> LLM completion (non-stream / SSE stream / playground)
  -> MemOSChatResponse
```

### 4. Feedback / Correction
```
POST /product/feedback
  APIFeedbackRequest
  -> FeedbackHandler
  -> MemFeedback (graph_db + embedder + reranker)
  -> updates or deletes existing memory nodes
```

## Tree Text Memory (Recommended Backend)
- Stores short-term and long-term memories in a tree graph in Neo4j
- `MemoryManager` / `Reorganizer` periodically merges / rewrites nodes
- `Retrieve` pipeline mixes graph traversal, BM25, vector search, internet search (Tavily/Bocha/Xinyu), reranking
- `Searcher` supports tool-memory, skill-memory, persona-memory, and preference-memory retrieval

## MemScheduler
- Async ingestion using Redis Streams or RabbitMQ (pika)
- Task labels: ADD_TASK_LABEL, QUERY_TASK_LABEL, ANSWER_TASK_LABEL, MEM_READ_TASK_LABEL, PREF_ADD_TASK_LABEL
- Handlers: memory_update_handler, etc.
- Status tracker with task queue status and wait endpoints

## MCP Server
- `MOSMCPServer` exposes FastMCP tools: chat, create_user, create_cube, add_memory, search_memory, feedback, delete_memory
- Reads env vars for one-shot config; can also wrap an existing MOS instance

## Tools Used
- read: server_api.py, server_router.py, mem_os/core.py, configs/mem_os.py, memories/factory.py, configs/memory.py, mem_cube/general.py, mcp_serve.py
- grep: class definitions for Memory/MemOS/MemCube/Scheduler/Reader/Feedback
- bash: directory listings, count of Python files
