# Practitioner — How to Use MemOS

## 1. Installation

### Poetry (development)
```bash
git clone https://github.com/MemTensor/MemOS.git
cd MemOS
poetry install
# or with extras
poetry install --extras "tree-mem mem-scheduler mem-user mem-reader"
```

### Pip (minimal tree-text backend)
```bash
pip install -r docker/requirements.txt
# or
pip install MemoryOS[tree-mem]
```

### Docker (recommended for Neo4j + Qdrant)
```bash
cd docker
cp .env.example ../.env  # edit keys
docker compose up
```

## 2. Minimal .env
```bash
# LLM
OPENAI_API_KEY=your_key
OPENAI_API_BASE=https://api.openai.com/v1
MOS_CHAT_MODEL=gpt-4o-mini

# Memory reader LLM
MEMRADER_API_KEY=your_key
MEMRADER_API_BASE=https://api.openai.com/v1
MEMRADER_MODEL=gpt-4o-mini

# Embedder
MOS_EMBEDDER_BACKEND=universal_api
MOS_EMBEDDER_MODEL=text-embedding-3-small
MOS_EMBEDDER_API_BASE=https://api.openai.com/v1
MOS_EMBEDDER_API_KEY=your_key
EMBEDDING_DIMENSION=1536

# Reranker
MOS_RERANKER_BACKEND=cosine_local

# Graph DB (Neo4j Community)
NEO4J_BACKEND=neo4j-community
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=12345678
NEO4J_DB_NAME=neo4j
MOS_NEO4J_SHARED_DB=false

# Scheduler
DEFAULT_USE_REDIS_QUEUE=false

# Enable chat endpoint
ENABLE_CHAT_API=true
```

## 3. Start REST Server
```bash
cd src
uvicorn memos.api.server_api:app --host 0.0.0.0 --port 8001 --workers 1
```

## 4. Add Memory
```python
import requests
import json

url = "http://localhost:8000/product/add"
data = {
    "user_id": "u-1",
    "mem_cube_id": "cube-1",
    "messages": [{"role": "user", "content": "I like strawberry"}],
    "async_mode": "sync"
}
resp = requests.post(url, json=data)
print(resp.json())
```

## 5. Search Memory
```python
import requests

url = "http://localhost:8000/product/search"
data = {
    "query": "What do I like",
    "user_id": "u-1",
    "mem_cube_id": "cube-1"
}
resp = requests.post(url, json=data)
print(resp.json())
```

## 6. Chat with Memory
Requires `ENABLE_CHAT_API=true` and `CHAT_MODEL_LIST` env.

```python
url = "http://localhost:8000/product/chat/complete"
data = {
    "user_id": "u-1",
    "mem_cube_id": "cube-1",
    "messages": [{"role": "user", "content": "Recommend a dessert"}]
}
resp = requests.post(url, json=data)
print(resp.json())
```

## 7. Programmatic Cube (Python SDK)
```python
from memos.mem_os.main import MOS
from memos.mem_os.utils.default_config import get_default

config, cube = get_default(
    openai_api_key="...",
    text_mem_type="tree_text",
    user_id="u-1"
)
mos = MOS(config=config)
mos.register_mem_cube(cube)
print(mos.chat("What do I like?", user_id="u-1"))
```

## 8. MCP Server
```python
from memos.api.mcp_serve import MOSMCPServer

server = MOSMCPServer()
server.run()  # exposes chat/create_user/create_cube/add_memory/search_memory/feedback/delete_memory
```

## 9. Integrations
- **Hermes Agent**: install `memos-local-plugin` 2.0
- **OpenClaw**: use `memos-local-openclaw` or `MemOS-Cloud-OpenClaw-Plugin`
- **Custom**: consume `/product/*` REST endpoints or embed `MOS` class

## Operational Tips
- For local dev, set `NEO4J_DB_NAME=neo4j` and disable multi-db for Neo4j Community.
- Use `"async_mode": "sync"` for read-after-write consistency; async mode routes through scheduler.
- Keep `MOS_NEO4J_SHARED_DB=false` unless you are running Neo4j Enterprise.
- The simplest backend is `tree_text` + `neo4j-community` + `qdrant` (or Qdrant local embedded mode).

## Tools Used
- read: docker/.env.example, README.md (Quick-start), examples/README.md, mem_chat/base.py, mcp_serve.py
- webfetch: README.md (usage snippets)
- bash: docker directory listing
