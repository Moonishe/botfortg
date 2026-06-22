# Researcher 5: Practitioner - Applied Perspective

> Repository: https://github.com/hijzy/MemOS (fork of MemTensor/MemOS)
> Branch: main
> Date: 2026-06-22
> Focus: Practical usage, self-hosted deployment, SDK patterns, MCP integration

---

## 1. Self-Hosted REST API via Docker Compose

### 1.1 Docker Compose Architecture

File: `docker/docker-compose.yml`

Three services in a bridge network (`memos_network`):

| Service  | Image                  | Ports              | Purpose                        |
|----------|------------------------|--------------------|--------------------------------|
| `memos`  | Built from `docker/Dockerfile` | `8000:8000`   | FastAPI REST API server        |
| `neo4j`  | `neo4j:5.26.6`         | `7474`, `7687`     | Graph DB (memory nodes+edges)  |
| `qdrant` | `qdrant/qdrant:v1.15.3` | `6333`, `6334`    | Vector DB (embeddings)         |

Key details:
- `memos` service builds from repo root (`context: ..`), mounts `../src` and `./docker` as volumes for hot-reload during dev.
- `env_file: ../.env` -- all config flows from a single `.env` at repo root.
- Environment overrides in compose: `QDRANT_HOST=qdrant-docker`, `NEO4J_URI=bolt://neo4j-docker:7687`, `PYTHONPATH=/app/src`, `HF_ENDPOINT=https://hf-mirror.com` (China mirror).
- Neo4j healthcheck: `wget http://localhost:7474` with 20 retries, 1s interval.
- Qdrant restarts `unless-stopped`.
- Named volumes: `neo4j_data`, `neo4j_logs`, `qdrant_data` for persistence.

### 1.2 Deployment Steps (from README)

```bash
git clone https://github.com/MemTensor/MemOS.git
cd MemOS
pip install -r ./docker/requirements.txt
# Configure: cp docker/.env.example .env  then edit
cd docker
docker compose up
```

Alternative: CLI without Docker (requires Neo4j + Qdrant running externally):
```bash
cd src
uvicorn memos.api.server_api:app --host 0.0.0.0 --port 8001 --workers 1
```

### 1.3 REST API Endpoints

File: `src/memos/api/server_api.py` + `src/memos/api/routers/server_router.py`

FastAPI app, all routes under `/product` prefix:

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health` | Health check (status, service, version) |
| POST | `/product/add` | Add memories (messages, text, or doc path) |
| POST | `/product/search` | Semantic search across user cubes |
| POST | `/product/chat/complete` | Non-streaming chat with memory |
| POST | `/product/chat/stream` | Streaming chat (SSE) |
| POST | `/product/chat/stream/playground` | Playground streaming chat |
| POST | `/product/chat/stream/business_user` | Business user chat |
| POST | `/product/feedback` | Memory feedback/correction |
| POST | `/product/get_all` | Get all memories or subgraph |
| POST | `/product/get_memory` | Get memories by criteria |
| GET  | `/product/get_memory/{memory_id}` | Get single memory by ID |
| POST | `/product/get_memory_by_ids` | Get memories by ID list |
| POST | `/product/delete_memory` | Delete memories |
| POST | `/product/delete_memory_by_record_id` | Hard/soft delete by record ID |
| POST | `/product/recover_memory_by_record_id` | Recover soft-deleted memory |
| POST | `/product/get_memory_dashboard` | Dashboard view |
| POST | `/product/suggestions` | Suggestion queries |
| GET  | `/product/scheduler/allstatus` | Full scheduler status |
| GET  | `/product/scheduler/status` | Scheduler status by user_id |
| GET  | `/product/scheduler/task_queue_status` | Queue backlog per user |
| POST | `/product/scheduler/wait` | Wait until scheduler idle |
| GET  | `/product/scheduler/wait/stream` | SSE stream of scheduler progress |
| POST | `/product/exist_mem_cube_id` | Check cube existence |
| POST | `/product/get_user_names_by_memory_ids` | Resolve memory IDs to users |

### 1.4 REST API Usage Examples (from README)

**Add memory:**
```python
import requests, json

data = {
    "user_id": "8736b16e-1d20-4163-980b-a5063c3facdc",
    "mem_cube_id": "b32d0977-435d-4828-a86f-4f47f8b55bca",
    "messages": [{"role": "user", "content": "I like strawberry"}],
    "async_mode": "sync"
}
res = requests.post("http://localhost:8000/product/add",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(data))
print(res.json())
```

**Search memory:**
```python
data = {
    "query": "What do I like",
    "user_id": "8736b16e-1d20-4163-980b-a5063c3facdc",
    "mem_cube_id": "b32d0977-435d-4828-a86f-4f47f8b55bca"
}
res = requests.post("http://localhost:8000/product/search",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(data))
print(res.json())
```

### 1.5 Server Initialization Architecture

`server_api.py` uses a plugin system:
```python
from memos.plugins.manager import plugin_manager
plugin_manager.discover()  # at module load
plugin_manager.init_app(app)  # after FastAPI app created
```

Router initialization (`server_router.py`):
```python
components = handlers.init_server()  # builds all components (LLM, embedder, graph_db, scheduler, etc.)
dependencies = HandlerDependencies.from_init_server(components)
search_handler = SearchHandler(dependencies)
add_handler = AddHandler(dependencies)
chat_handler = ChatHandler(...) if os.getenv("ENABLE_CHAT_API") == "true" else None
feedback_handler = FeedbackHandler(dependencies)
```

Class-based handlers with dependency injection -- clean separation of routing and business logic.

---

## 2. Python SDK: MOS Class

### 2.1 Core API

File: `src/memos/mem_os/main.py`

`MOS` inherits from `MOSCore` and is the primary programmatic interface.

**Initialization patterns:**

```python
# Pattern 1: Simplest -- auto-configure from env vars
from memos.mem_os.main import MOS
memory = MOS.simple()  # classmethod, reads OPENAI_API_KEY, MOS_TEXT_MEM_TYPE, etc.

# Pattern 2: Explicit config
from memos.configs.mem_os import MOSConfig
config = MOSConfig(...)
memory = MOS(config=config)

# Pattern 3: No args -- same as simple()
memory = MOS()  # auto-configure, auto-registers default cube
```

When `config=None`, `_auto_configure()` calls `get_default()` which builds a `MOSConfig` and a default MemCube from environment variables. The default cube is auto-registered.

**Key MOS methods:**

| Method | Signature | Description |
|--------|-----------|-------------|
| `chat` | `chat(query, user_id=None, base_prompt=None) -> str` | Memory-enhanced chat with optional CoT decomposition (PRO_MODE) |
| `search` | `search(query, user_id, cube_ids) -> dict` | Semantic search across cubes; returns `{text_mem, act_mem, para_mem}` |
| `add` | `add(messages=None, memory_content=None, doc_path=None, mem_cube_id=None, user_id=None)` | Add memories from messages, raw text, or document file |
| `get` | `get(cube_id, memory_id, user_id) -> memory` | Retrieve single memory by ID |
| `update` | `update(cube_id, memory_id, memory_item, user_id)` | Update memory content (may not work with tree_text backend) |
| `delete` | `delete(cube_id, memory_id, user_id)` | Delete single memory |
| `delete_all` | `delete_all(cube_id, user_id)` | Clear all memories in a cube |
| `register_mem_cube` | `register_mem_cube(cube, mem_cube_id=None, user_id=None)` | Register a MemCube (path or object) |
| `unregister_mem_cube` | `unregister_mem_cube(cube_id, user_id)` | Unregister cube (data stays on disk) |
| `create_user` | `create_user(user_id, role, user_name)` | Create user (UserRole.USER or UserRole.ADMIN) |
| `create_cube_for_user` | `create_cube_for_user(cube_name, owner_id, cube_path, cube_id)` | Create a new MemCube |
| `share_cube_with_user` | `share_cube_with_user(cube_id, target_user_id) -> bool` | Share cube access |
| `get_user_info` | `get_user_info() -> dict` | User profile + accessible cubes |
| `clear_messages` | `clear_messages(user_id)` | Clear chat history (memories stay) |
| `dump` | `dump(dump_dir, user_id, cube_id)` | Export cube to directory |
| `mem_scheduler_on` | `mem_scheduler_on() -> bool` | Start async memory scheduler |
| `mem_scheduler_off` | `mem_scheduler_off() -> bool` | Stop scheduler |

### 2.2 Chat with CoT Enhancement (PRO_MODE)

When `config.PRO_MODE` is enabled, `chat()` uses Chain-of-Thought decomposition:

1. `cot_decompose(query)` -- LLM analyzes query complexity, returns `{is_complex, sub_questions}`
2. If complex: decomposes into sub-questions
3. `get_sub_answers()` -- searches and answers each sub-question in parallel (`ContextThreadPoolExecutor`, max 10 workers)
4. `_generate_enhanced_response_with_context()` -- synthesizes sub-answers + chat history + memories
5. Falls back to standard `super().chat()` on any error

Search modes during CoT:
- `mode="fine"` when CoT enabled (slower, more thorough)
- `mode="fast"` when CoT disabled (quick keyword+vector)

### 2.3 MemCube Management

MemCubes are composable knowledge containers:
- `register_mem_cube()` accepts either a file path (string) or a cube object
- If path doesn't exist, a default cube config is generated via `load_default_config()`
- Multiple cubes can be registered per user
- Cubes can be shared across users via `share_cube_with_user()`
- `SingleCubeView` provides a single-cube interface (used in examples)

### 2.4 Low-Level Component Access (from load_cube.py example)

```python
from memos.api.handlers import init_server
from memos.multi_mem_cube.single_cube import SingleCubeView

components = init_server()
# components dict contains: naive_mem_cube, mem_reader, mem_scheduler,
#   searcher, feedback_server, llm, embedder, graph_db, redis_client, etc.

view = SingleCubeView(
    cube_id="my_cube",
    naive_mem_cube=components["naive_mem_cube"],
    mem_reader=components["mem_reader"],
    mem_scheduler=components["mem_scheduler"],
    searcher=components["searcher"],
    feedback_server=components["feedback_server"],
    logger=logger,
)
```

Direct graph import:
```python
text_mem = naive.text_mem
text_mem.graph_store.import_graph(json_data, user_name="cube_id")
```

---

## 3. MCP Server: MOSMCPServer

### 3.1 Architecture

File: `src/memos/api/mcp_serve.py`

Uses **FastMCP** library. `MOSMCPServer` wraps a `MOS` instance and exposes 16 tools.

```python
from memos.mem_os.main import MOS
from fastmcp import FastMCP

class MOSMCPServer:
    def __init__(self, mos_instance: MOS | None = None):
        self.mcp = FastMCP("MOS Memory System")
        if mos_instance is None:
            config, cube = load_default_config()
            self.mos_core = MOS(config=config)
            self.mos_core.register_mem_cube(cube)
        else:
            self.mos_core = mos_instance
        self._setup_tools()
```

### 3.2 Exposed MCP Tools (16 total)

| Tool | Args | Returns | Description |
|------|------|---------|-------------|
| `chat` | `query, user_id?` | `str` | Memory-enhanced chat response |
| `create_user` | `user_id, role?, user_name?` | `str` | Create user (USER/ADMIN) |
| `create_cube` | `cube_name, owner_id, cube_path?, cube_id?` | `str` | Create new MemCube |
| `register_cube` | `cube_name_or_path, cube_id?, user_id?` | `str` | Register existing cube |
| `unregister_cube` | `cube_id, user_id?` | `str` | Unregister cube |
| `search_memories` | `query, user_id?, cube_ids?` | `dict` | Semantic search across cubes |
| `add_memory` | `memory_content?, doc_path?, messages?, cube_id?, user_id?` | `str` | Add memories from text/doc/messages |
| `get_memory` | `cube_id, memory_id, user_id?` | `dict` | Get single memory |
| `update_memory` | `cube_id, memory_id, memory_content, user_id?` | `str` | Update memory (not all backends) |
| `delete_memory` | `cube_id, memory_id, user_id?` | `str` | Delete single memory |
| `delete_all_memories` | `cube_id, user_id?` | `str` | Clear all memories in cube |
| `clear_chat_history` | `user_id?` | `str` | Reset conversation history |
| `dump_cube` | `dump_dir, user_id?, cube_id?` | `str` | Export cube to directory |
| `share_cube` | `cube_id, target_user_id` | `str` | Share cube with another user |
| `get_user_info` | `user_id?` | `dict` | User profile + accessible cubes |
| `control_memory_scheduler` | `action` ("start"/"stop") | `str` | Start/stop async scheduler |

### 3.3 Transport Modes

```python
# Monkey-patched onto MOSMCPServer
def _run_mcp(self, transport: str = "stdio", **kwargs):
    if transport == "stdio":
        self.mcp.run(transport="stdio")
    elif transport == "http":
        asyncio.run(self.mcp.run_http_async(host=host, port=port))
    elif transport == "sse":
        self.mcp.run(transport="sse", host=host, port=port)
```

CLI usage:
```bash
python mcp_serve.py --transport stdio                    # default, for MCP clients
python mcp_serve.py --transport http --host 0.0.0.0 --port 8000
python mcp_serve.py --transport sse --host localhost --port 8000
```

### 3.4 Configuration via Environment

`load_default_config()` maps env vars to MOS config. Key mappings:

| Env Var | Config Key | Type |
|---------|-----------|------|
| `OPENAI_API_KEY` | `openai_api_key` | string |
| `OPENAI_API_BASE` | `openai_api_base` | string |
| `MOS_TEXT_MEM_TYPE` | `text_mem_type` | string (general_text/tree_text) |
| `NEO4J_URI` | `neo4j_uri` | string |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j_user` / `neo4j_password` | string |
| `NEO4J_DB_NAME` | `neo4j_db_name` | string |
| `NEO4J_AUTO_CREATE` | `neo4j_auto_create` | bool |
| `MOS_NEO4J_SHARED_DB` | `mos_shared_db` -> `use_multi_db` (inverted) | bool |
| `MOS_CHAT_MODEL` / `MODEL_NAME` | `model_name` | string |
| `MOS_EMBEDDER_MODEL` / `EMBEDDER_MODEL` | `embedder_model` | string |
| `ENABLE_MEM_SCHEDULER` / `MOS_ENABLE_SCHEDULER` | `enable_mem_scheduler` | bool |
| `TEMPERATURE` / `MOS_CHAT_TEMPERATURE` | `temperature` | float |
| `MAX_TOKENS` / `MOS_MAX_TOKENS` | `max_tokens` | int |
| `TOP_P` / `MOS_TOP_P` | `top_p` | float |
| `TOP_K` / `MOS_TOP_K` | `top_k` | int |
| `SCHEDULER_TOP_K` | `scheduler_top_k` | int |

Important Neo4j Community Edition note (from source comments):
- `NEO4J_DB_NAME=neo4j` (must use default database)
- `NEO4J_AUTO_CREATE=false` (disable auto DB creation)
- `NEO4J_USE_MULTI_DB=false` (disable multi-tenant DB mode)

---

## 4. Async Ingestion via MemScheduler

### 4.1 Overview

MemScheduler provides asynchronous memory processing with millisecond-level latency. Two queue backends:
- **Redis Streams** (primary, production)
- **RabbitMQ** (alternative, for message-log pipeline)

### 4.2 Redis Streams Pattern

File: `examples/mem_scheduler/redis_example.py`

```python
from memos.configs.mem_scheduler import SchedulerConfigFactory
from memos.mem_scheduler.scheduler_factory import SchedulerFactory
from memos.mem_scheduler.schemas.message_schemas import ScheduleMessageItem
from memos.mem_scheduler.schemas.task_schemas import QUERY_TASK_LABEL

# 1. Load scheduler config from YAML
scheduler_config = SchedulerConfigFactory.from_yaml_file(
    yaml_path="examples/data/config/mem_scheduler/general_scheduler_config.yaml"
)
mem_scheduler = SchedulerFactory.from_config(scheduler_config)

# 2. Initialize Redis
mem_scheduler.initialize_redis()
mem_scheduler.redis_start_listening()

# 3. Submit messages to Redis stream
message_item = ScheduleMessageItem(
    user_id=user_id,
    mem_cube_id="mem_cube_2",
    label=QUERY_TASK_LABEL,
    mem_cube=mem_cube,
    content=query,
    timestamp=datetime.now(),
)
res = mem_scheduler.redis_add_message_stream(message=message_item.to_dict())

# 4. Stop
mem_scheduler.redis_stop_listening()
mem_scheduler.redis_close()
```

### 4.3 Custom Handler Registration

File: `examples/mem_scheduler/memos_w_scheduler.py`

```python
from memos.mem_scheduler.schemas.task_schemas import MEM_UPDATE_TASK_LABEL, QUERY_TASK_LABEL

# Custom query handler -- receives queries and triggers memory updates
def custom_query_handler(messages: list[ScheduleMessageItem]):
    for msg in messages:
        new_msg = msg.model_copy(update={"label": MEM_UPDATE_TASK_LABEL})
        mem_scheduler.submit_messages([new_msg])

# Custom memory update handler -- delegates to built-in handler
def custom_mem_update_handler(messages: list[ScheduleMessageItem]):
    default_mem_update_handler(messages)  # built-in handler

# Register custom handlers
mem_scheduler.register_handlers({
    QUERY_TASK_LABEL: custom_query_handler,
    MEM_UPDATE_TASK_LABEL: custom_mem_update_handler,
})
```

Task labels:
- `QUERY_TASK_LABEL` -- query/search tasks
- `MEM_UPDATE_TASK_LABEL` -- memory update tasks
- `ANSWER_LABEL` -- answer messages (submitted after chat responses)

### 4.4 Scheduler via REST API

Scheduler control endpoints (from `server_router.py`):
- `GET /product/scheduler/allstatus` -- full status (running tasks, queue metrics)
- `GET /product/scheduler/status?user_id=X&task_id=Y` -- per-user/task status
- `GET /product/scheduler/task_queue_status?user_id=X` -- queue backlog
- `POST /product/scheduler/wait` -- block until idle (with timeout)
- `GET /product/scheduler/wait/stream` -- SSE progress stream

`TaskStatusTracker` uses Redis for distributed task tracking.

### 4.5 Scheduler Configuration (from .env.example-full)

| Env Var | Default | Description |
|---------|---------|-------------|
| `MOS_ENABLE_SCHEDULER` | `false` | Master switch for MOS class init |
| `API_SCHEDULER_ON` | `true` | Start scheduler background loop at server init |
| `MOS_SCHEDULER_TOP_K` | `10` | Top-K memories for scheduler processing |
| `MOS_SCHEDULER_ACT_MEM_UPDATE_INTERVAL` | `300` | Activation memory update interval (seconds) |
| `MOS_SCHEDULER_CONTEXT_WINDOW_SIZE` | `5` | Context window (recent messages) |
| `MOS_SCHEDULER_THREAD_POOL_MAX_WORKERS` | `10000` | Thread pool size |
| `MOS_SCHEDULER_CONSUME_INTERVAL_SECONDS` | `0.01` | Queue poll interval |
| `MOS_SCHEDULER_ENABLE_PARALLEL_DISPATCH` | `true` | Parallel task dispatch |
| `MOS_SCHEDULER_ENABLE_ACTIVATION_MEMORY` | `false` | Activation memory feature |
| `MEMSCHEDULER_USE_REDIS_QUEUE` | `false` | Use Redis queue (vs in-memory) |
| `API_SEARCH_WINDOW_SIZE` | `5` | API search context window |
| `API_SEARCH_HISTORY_TURNS` | `5` | History turns for hybrid search |

Redis-specific:
| Env Var | Description |
|---------|-------------|
| `MEMSCHEDULER_REDIS_HOST` | Redis host |
| `MEMSCHEDULER_REDIS_PORT` | Redis port |
| `MEMSCHEDULER_REDIS_DB` | Redis DB number |
| `MEMSCHEDULER_REDIS_PASSWORD` | Redis password |
| `MEMSCHEDULER_REDIS_TIMEOUT` | Operation timeout |
| `MEMSCHEDULER_REDIS_CONNECT_TIMEOUT` | Connection timeout |

RabbitMQ-specific:
| Env Var | Description |
|---------|-------------|
| `MEMSCHEDULER_RABBITMQ_HOST_NAME` | RabbitMQ host |
| `MEMSCHEDULER_RABBITMQ_USER_NAME` | Username |
| `MEMSCHEDULER_RABBITMQ_PASSWORD` | Password |
| `MEMSCHEDULER_RABBITMQ_VIRTUAL_HOST` | vhost (default: `memos`) |
| `MEMSCHEDULER_RABBITMQ_PORT` | Port (default: 5672) |
| `MEMSCHEDULER_RABBITMQ_ERASE_ON_CONNECT` | Clear connection state on connect |

---

## 5. Memory Feedback/Correction Pattern (MemFeedback)

### 5.1 SimpleMemFeedback Class

File: `examples/mem_feedback/example_feedback.py`

`SimpleMemFeedback` is the feedback processing engine. It:
1. Receives natural-language feedback about incorrect/incomplete memories
2. Retrieves relevant existing memories
3. Analyzes feedback against chat history
4. Generates update operations (add, modify, or archive old memories)

### 5.2 Component Assembly

```python
from memos.mem_feedback.simple_feedback import SimpleMemFeedback

feedback_server = SimpleMemFeedback(
    llm=llm,                    # LLM for understanding feedback
    embedder=embedder,          # For similarity matching
    graph_store=graph_db,       # Neo4j for memory storage
    memory_manager=memory_manager,  # Core memory CRUD
    mem_reader=mem_reader,      # Parse input
    searcher=searcher,          # Hybrid retrieval
    reranker=mem_reranker,      # Refine results
    pref_feedback=True,         # Enable preference feedback
)
```

### 5.3 Feedback Workflow

```python
# 1. Existing (possibly wrong) memory in the graph
mem_text = "You like apples, dislike bananas"
memory_manager.add(
    [make_mem_item(mem_text, user_id="user_001", user_name="cube_001",
     tags=["fact"], key="food_preference", sources=[{"type": "chat"}],
     background="init from chat history",
     embedding=embedder.embed([mem_text])[0])],
    user_name="cube_001", mode="sync"
)

# 2. User provides correction
feedback_content = "Wrong, I actually like mangosteen"

# 3. Process feedback
res = feedback_server.process_feedback(
    user_id="user_001",
    user_name="cube_001",
    session_id="session_id",
    chat_history=[
        {"role": "user", "content": "What fruits do I like/dislike?"},
        {"role": "assistant", "content": "You like apples, dislike bananas"},
    ],
    feedback_content=feedback_content,
    feedback_time="",
    corrected_answer="",
    task_id="task_id",
    info={},
    async_mode="sync",  # or "async"
)

# 4. Result contains add/update operations
print(json.dumps(res, indent=4, default=str))
```

### 5.4 REST API Feedback

```python
POST /product/feedback
# Body: APIFeedbackRequest -- uses FeedbackHandler class
# Internally calls SimpleMemFeedback.process_feedback()
```

### 5.5 make_mem_item Helper

```python
from memos.mem_feedback.utils import make_mem_item

mem_item = make_mem_item(
    "memory text",
    user_id="user_id",
    user_name="cube_id",       # acts as graph partition key
    session_id="session_id",
    tags=["fact"],             # custom tags for filtering
    key="food_preference",     # semantic key
    sources=[{"type": "chat"}],
    background="init from chat history",
    embedding=embedder.embed(["memory text"])[0],
    info={"user_id": "...", "user_name": "...", "session_id": "..."},
)
```

---

## 6. Hybrid Retrieval: Keyword/BM25 + Vector + Reranker

### 6.1 Searcher Architecture

File: `examples/mem_feedback/example_feedback.py` (component assembly)

```python
from memos.memories.textual.tree_text_memory.retrieve.searcher import Searcher

searcher = Searcher(llm, graph_db, embedder, mem_reranker)
```

The `Searcher` integrates:
1. **Graph DB (Neo4j)** -- stores memory nodes with metadata, relationships (topic/concept/fact hierarchy)
2. **Embedder** -- converts queries to vectors for semantic search
3. **Reranker** -- refines ranking of retrieved results
4. **LLM** -- used for "fine" strategy (query rewrite/recreate/deep_search)

### 6.2 Search Modes

| Mode | Description | When to Use |
|------|-------------|-------------|
| `fast` | Quick hybrid search (keyword + vector), async fine search in background | Real-time chat, low latency |
| `fine` | Thorough search with query rewriting/recreation | Complex queries, high accuracy needed |
| `mixture` | Combination of fast and fine | Balanced |

Fine strategies (`FINE_STRATEGY` env var):
- `rewrite` -- LLM rewrites query for better retrieval
- `recreate` -- LLM recreates query from different angle
- `deep_search` -- multi-step deep search

### 6.3 Reranker Configuration

| Backend | Description | Config |
|---------|-------------|--------|
| `http_bge` | Remote BGE reranker service | `MOS_RERANKER_URL`, `MOS_RERANKER_MODEL` |
| `http_bge_strategy` | BGE with strategy support | `MOS_RERANKER_STRATEGY=single_turn` |
| `cosine_local` | Local cosine similarity (no external service) | `level_weights`, `level_field` |

Example reranker config:
```python
mem_reranker = RerankerFactory.from_config(
    RerankerConfigFactory.model_validate({
        "backend": "cosine_local",
        "config": {
            "level_weights": {"topic": 1.0, "concept": 1.0, "fact": 1.0},
            "level_field": "background",
        },
    })
)
```

For remote BGE reranker:
```env
MOS_RERANKER_BACKEND=http_bge
MOS_RERANKER_URL=http://localhost:8001
MOS_RERANKER_MODEL=bge-reranker-v2-m3
MOS_RERANKER_HEADERS_EXTRA={"Authorization":"Bearer your_token"}
```

### 6.4 Embedder Configuration

| Backend | Description | Config |
|---------|-------------|--------|
| `universal_api` | OpenAI-compatible API | `MOS_EMBEDDER_PROVIDER`, `MOS_EMBEDDER_API_BASE`, `MOS_EMBEDDER_API_KEY` |
| `ollama` | Local Ollama models | `OLLAMA_API_BASE` |

Common embedding models:
- `bge-m3` (BAAI/bge-m3) -- 1024 dimensions (default in examples)
- `text-embedding-v4` (Alibaba/DashScope)
- `nomic-embed-text:latest` (Ollama)

**Critical**: Sample data uses specific embedding model + dimensions. Semantic search requires matching embedder config. Mismatched models produce inaccurate results.

### 6.5 Graph Database Hierarchy

Tree-text memory organizes memories in a hierarchy:
- **topic** level -- broad categories
- **concept** level -- specific concepts
- **fact** level -- individual facts

The reranker uses `level_weights` to weight results by their position in this hierarchy.

### 6.6 Search Result Format

```python
{
    "text_mem": [
        {
            "cube_id": "cube_uuid",
            "memories": [
                {"memory": "fact text", "metadata": {...}},
                ...
            ]
        }
    ],
    "act_mem": [...],    # activation memory (KV cache)
    "para_mem": [...]    # parametric memory
}
```

### 6.7 Internet Search Integration

Optional web search augments memory retrieval:
```env
ENABLE_INTERNET=false                    # master switch
INTERNET_SEARCH_BACKEND=bocha            # bocha | tavily
BOCHA_API_KEY=                           # required if bocha
TAVILY_API_KEY=                          # required if tavily
SEARCH_MODE=fast                         # fast | fine | mixture
```

---

## 7. Memory Types

| Type | Env Value | Description |
|------|-----------|-------------|
| General text | `general_text` | Flat text memories, simpler storage |
| Tree text | `tree_text` | Hierarchical topic->concept->fact graph, richer retrieval |

Activation memory (KV cache):
- `MOS_SCHEDULER_ENABLE_ACTIVATION_MEMORY=false` -- disabled by default
- Only works with `huggingface` / `huggingface_singleton` LLM backends
- Stores past key values for context reuse

Preference memory:
- `ENABLE_PREFERENCE_MEMORY=true` -- enabled by default
- `PREFERENCE_ADDER_MODE=fast` -- fast or safe mode
- Uses Milvus for vector storage when enabled
- `DEDUP_PREF_EXP_BY_TEXTUAL=false` -- dedup preferences against factual memory

---

## 8. LLM Provider Support

| Provider | `MOS_CHAT_MODEL_PROVIDER` | Notes |
|----------|--------------------------|-------|
| OpenAI | `openai` | Default, requires `OPENAI_API_KEY` |
| Azure OpenAI | `azure` | Azure-specific config |
| Qwen (DashScope) | `qwen` | Alibaba Cloud, BaiLian platform |
| DeepSeek | `deepseek` | DeepSeek API |
| MiniMax | `minimax` | Requires `MINIMAX_API_KEY` |
| Ollama | `ollama` | Local models, `OLLAMA_API_BASE` |
| HuggingFace | `huggingface` | Local HF models, supports activation memory |
| vLLM | `vllm` | vLLM inference server |

`CHAT_MODEL_LIST` env var defines multiple chat backends (JSON array):
```json
[{
  "backend": "deepseek",
  "api_base": "http://localhost:1234",
  "api_key": "your-api-key",
  "model_name_or_path": "deepseek-r1",
  "support_models": ["deepseek-r1"]
}]
```

---

## 9. Graph/Vector Store Options

### Neo4j
| Backend | Value | Notes |
|---------|-------|-------|
| Community | `neo4j-community` | No multi-DB, use default `neo4j` DB |
| Enterprise | `neo4j` | Supports multi-DB, auto-create |
| PolarDB | `polardb` | PostgreSQL-compatible, multi-DB mode |
| PostgreSQL | `postgres` | Standard Postgres |

Multi-DB isolation:
- `MOS_NEO4J_SHARED_DB=false` -> each user gets own database (requires Enterprise)
- `MOS_NEO4J_SHARED_DB=true` -> shared database, logical isolation by username

### Vector DB
| Store | Config | Notes |
|-------|--------|-------|
| Qdrant | `QDRANT_HOST`, `QDRANT_PORT` or `QDRANT_URL`, `QDRANT_API_KEY` | Default in docker-compose |
| Milvus | `MILVUS_URI`, `MILVUS_USER_NAME`, `MILVUS_PASSWORD` | Required for preference memory |

---

## 10. Practical Integration Patterns

### 10.1 Minimal Self-Hosted Setup

```bash
# 1. Clone and configure
git clone https://github.com/MemTensor/MemOS.git && cd MemOS
cp docker/.env.example .env

# 2. Edit .env -- minimum required:
# OPENAI_API_KEY=sk-xxx
# OPENAI_API_BASE=https://api.openai.com/v1
# MOS_CHAT_MODEL=gpt-4o-mini
# MOS_EMBEDDER_MODEL=bge-m3
# MOS_EMBEDDER_BACKEND=universal_api
# MOS_EMBEDDER_API_BASE=http://localhost:8000/v1
# MOS_EMBEDDER_API_KEY=EMPTY

# 3. Start
cd docker && docker compose up
```

Services available:
- REST API: `http://localhost:8000`
- Neo4j Browser: `http://localhost:7474`
- Qdrant Dashboard: `http://localhost:6333/dashboard`

### 10.2 MCP Server for AI Agent Integration

```bash
# stdio mode (for Claude Desktop, Cursor, etc.)
python src/memos/api/mcp_serve.py --transport stdio

# HTTP mode (for remote MCP clients)
python src/memos/api/mcp_serve.py --transport http --host 0.0.0.0 --port 9000
```

MCP client config (e.g., for Claude Desktop):
```json
{
  "mcpServers": {
    "memos": {
      "command": "python",
      "args": ["src/memos/api/mcp_serve.py", "--transport", "stdio"],
      "env": {
        "OPENAI_API_KEY": "sk-xxx",
        "MOS_TEXT_MEM_TYPE": "general_text"
      }
    }
  }
}
```

### 10.3 Programmatic SDK Usage

```python
from memos.mem_os.main import MOS

# Simplest
memory = MOS.simple()
memory.add(messages=[{"role": "user", "content": "I love Python"}])
response = memory.chat("What do I love?")
print(response)  # "You love Python"

# With cube management
memory.create_user("alice", "USER", "Alice")
cube_id = memory.create_cube_for_user("alice_kb", "alice")
memory.register_mem_cube("/path/to/cube", mem_cube_id=cube_id, user_id="alice")
results = memory.search("Python preferences", user_id="alice", cube_ids=[cube_id])
```

### 10.4 Async Production Pipeline

```python
# Enable scheduler in .env
# MOS_ENABLE_SCHEDULER=true
# MEMSCHEDULER_USE_REDIS_QUEUE=true
# MEMSCHEDULER_REDIS_HOST=localhost

memory = MOS.simple()  # scheduler auto-starts if enabled
memory.add(messages=conversation, async_mode="async")  # non-blocking
# Memory processing happens in background via Redis Streams
# Check status via REST: GET /product/scheduler/status?user_id=alice
```

---

## 11. Examples Directory Structure

| Directory | Purpose | Key Files |
|-----------|---------|-----------|
| `api` | Server router and product API examples | -- |
| `basic_modules` | Embedders, LLMs, chunkers, rerankers, graph DBs | -- |
| `core_memories` | Memory backends: general, naive, preference, tree, KV cache | -- |
| `data` | Shared sample configs, cube data, input assets | `config/mem_scheduler/general_scheduler_config.yaml` |
| `dream` | End-to-end dream pipeline | -- |
| `extras` | Standalone demos | -- |
| `mem_agent` | Agent-oriented examples, deep search | -- |
| `mem_chat` | Chat with generated cubes + explicit memory | -- |
| `mem_cube` | Load, dump, legacy remote/lazy loading | `load_cube.py`, `dump_cube.py` |
| `mem_feedback` | Memory feedback workflows | `example_feedback.py` |
| `mem_mcp` | FastMCP server and client examples | -- |
| `mem_reader` | Parser, builder, sample, runner for text/files/images/messages | -- |
| `mem_scheduler` | Redis-backed async memory workflows | `memos_w_scheduler.py`, `redis_example.py`, `api_w_scheduler.py` |

---

## 12. Key Observations for Practitioners

1. **Two deployment modes**: Quick (docker compose with 3 services) and Full (adds Redis, Milvus, RabbitMQ for production features).

2. **Neo4j Community Edition is supported** but with constraints: no multi-DB, no auto-create. Must use default `neo4j` database and set `MOS_NEO4J_SHARED_DB=true`.

3. **Scheduler is opt-in**: `MOS_ENABLE_SCHEDULER=false` by default. For production async ingestion, must explicitly enable + configure Redis.

4. **Embedding model lock-in**: Sample data and cubes are tied to specific embedding models (bge-m3, 1024 dim). Changing embedder requires re-embedding all data.

5. **MCP server is standalone**: Can run independently of REST API. Just needs env vars for LLM + Neo4j + Qdrant.

6. **Feedback is natural language**: Users describe corrections in plain text ("Wrong, I actually like mangosteen"). The LLM interprets and generates memory mutations.

7. **CoT mode (PRO_MODE)**: Decomposes complex queries into sub-questions, searches and answers each in parallel, then synthesizes. Falls back to standard chat on errors.

8. **Plugin system**: `server_api.py` uses `plugin_manager.discover()` and `plugin_manager.init_app(app)` for extensibility.

9. **Multi-cube architecture**: Each user can have multiple MemCubes. Cubes can be shared across users. Isolation is by `user_name` in the graph DB.

10. **Chinese ecosystem defaults**: Default examples use Alibaba Cloud BaiLian (DashScope) for LLM/embedding. Qwen models are first-class citizens. Bocha search is a Chinese search engine. HF mirror (`hf-mirror.com`) is preconfigured in docker-compose.

---

## 13. Complete .env Example (Production)

```env
## Base
TZ=Asia/Shanghai
MOS_CUBE_PATH=/tmp/data_test
MEMOS_BASE_PATH=.
MOS_ENABLE_DEFAULT_CUBE_CONFIG=true
MOS_ENABLE_REORGANIZE=false
MOS_TEXT_MEM_TYPE=general_text
ASYNC_MODE=sync

## Chat LLM
MOS_CHAT_MODEL=gpt-4o-mini
MOS_CHAT_TEMPERATURE=0.8
MOS_MAX_TOKENS=2048
MOS_TOP_P=0.9
MOS_CHAT_MODEL_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
OPENAI_API_BASE=https://api.openai.com/v1

## MemReader / retrieval LLM
MEMRADER_MODEL=gpt-4o-mini
MEMRADER_API_KEY=sk-xxx
MEMRADER_API_BASE=https://api.openai.com/v1
MEMRADER_MAX_TOKENS=5000

## Embedding
EMBEDDING_DIMENSION=1024
MOS_EMBEDDER_BACKEND=universal_api
MOS_EMBEDDER_PROVIDER=openai
MOS_EMBEDDER_MODEL=bge-m3
MOS_EMBEDDER_API_BASE=http://localhost:8000/v1
MOS_EMBEDDER_API_KEY=EMPTY

## Reranker
MOS_RERANKER_BACKEND=cosine_local

## Scheduler
MOS_ENABLE_SCHEDULER=true
MEMSCHEDULER_USE_REDIS_QUEUE=true
MEMSCHEDULER_REDIS_HOST=localhost
MEMSCHEDULER_REDIS_PORT=6379
API_SCHEDULER_ON=true

## Neo4j
NEO4J_BACKEND=neo4j-community
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=12345678
NEO4J_DB_NAME=neo4j
MOS_NEO4J_SHARED_DB=true

## Qdrant
QDRANT_HOST=localhost
QDRANT_PORT=6333

## Chat API
ENABLE_CHAT_API=true
CHAT_MODEL_LIST=[{"backend":"openai","api_base":"https://api.openai.com/v1","api_key":"sk-xxx","model_name_or_path":"gpt-4o-mini","support_models":["gpt-4o-mini"]}]

## Preference Memory
ENABLE_PREFERENCE_MEMORY=true
PREFERENCE_ADDER_MODE=fast

## Internet Search (optional)
ENABLE_INTERNET=false
SEARCH_MODE=fast
```
