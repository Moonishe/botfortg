# TelegramHelper Performance Audit — Detailed Findings

**Generated:** 2026-06-10  
**Scope:** Full codebase analysis (src/ directory, ~200+ Python files)

---

## Executive Summary

The TelegramHelper codebase is a **single-user Telegram bot** with sophisticated memory, LLM routing, and scheduling systems. Key architectural patterns:

- **SQLite (WAL mode) + SQLAlchemy async** for persistence
- **Embedded Qdrant** for vector search (memory facts, messages)
- **Multi-provider LLM routing** with circuit breakers, key rotation, fallback chains
- **Managed TTL caches** (recall, LLM response, contact, stats) with single-writer patterns
- **Background task manager** with exponential backoff, 11-phase nightly dream cycle

---

## 1. Database Performance

### 1.1 Query Patterns & Indexes

| File | Pattern | Impact |
|------|---------|--------|
| `src/db/session.py` (lines 48-124) | FTS5 virtual tables: `messages_fts`, `memories_fts`, `agent_session_messages_fts` with triggers | ✅ Good: FTS5 for full-text search, triggers keep sync |
| `src/db/repos/memory_repo.py` (lines 147-214) | Russian morphological expansion for FTS5 (`_RU_MORPH_EXPANSIONS`) | ✅ Good: Boosts recall ~30-40% |
| `src/db/repos/memory_repo.py` (lines 1049-1192) | `search_memories_fts_with_scores()` → BM25 + reciprocal rank fusion | ✅ Good: RRF combines vector + keyword |
| `src/db/repos/memory_repo.py` (lines 1511-1582) | `get_memory_graph()` — BFS with 2-query optimization (load all links + batch load memories) | ✅ Good: Avoids N+1 |
| `src/db/repos/memory_repo.py` (lines 1585-1724) | `get_graph_stats()` — loads ALL edges for flood-fill | ⚠️ **Risk**: O(E) memory, loads 5000 edges max (line 1537) |
| `src/db/repos/memory_repo.py` (lines 1727-1896) | `contact_impact()` — multiple queries, batch-loads contact names | ✅ Acceptable for single-user |

### 1.2 Connection Pooling

| File | Finding |
|------|---------|
| `src/db/session.py` (lines 16-21) | **Single global engine**, no pool sizing config. Uses `create_async_engine` with `connect_args={"check_same_thread": False}` only. |
| `src/db/session.py` (lines 24-36) | PRAGMAs set per-connection: `WAL`, `cache_size=64MB`, `mmap=128MB`, `busy_timeout=30s`, `temp_store=MEMORY` |
| **Risk** | No connection pool limits (`pool_size`, `max_overflow`). Under load, could exhaust file descriptors or SQLite locks. |

### 1.3 Schema & Migrations

| File | Finding |
|------|---------|
| `src/db/session.py` (lines 184-290) | `init_db()` runs Alembic upgrade + `create_all` fallback + FTS5 setup + data migration |
| **Risk** | `Base.metadata.create_all` runs if no ORM tables found — masks missing migrations. **Mitigation**: Alembic runs first in `main.py:run()` (120s timeout). |

---

## 2. Memory Usage & Leak Patterns

### 2.1 In-Memory Caches (Global State)

| Cache | Location | Max Size | TTL | Eviction | Leak Risk |
|-------|----------|----------|-----|----------|-----------|
| `TTLCache` (recall) | `src/core/memory/memory_recall.py:35-41` | `recall_cache_max_size` (default 1000) | 30s/60s | LRU + TTL | Low |
| `ManagedCache` (LLM response) | `src/core/intelligence/llm_response_cache.py:288-294` | 5000 | 5m-1h | LRU + TTL | Low |
| `_CIRCUIT_BREAKERS` | `src/llm/provider_manager.py:262` | Unbounded | Cleanup every 5m | Time-based (1h closed, 30m open) | **Medium** |
| `_PROVIDER_METRICS` | `src/llm/provider_manager.py:178` | Unbounded | Cleanup every 5m | Time-based (1h) | **Medium** |
| `_rec_version` | `src/core/memory/memory_recall.py:64` | Unbounded (per-user) | Reset on restart | Version bump on mutation | **Low** (single-user) |
| `_ff_tasks` | `src/core/infra/task_manager.py` | Unbounded | Tracked, cleaned on completion | Auto-discard on done | **Low** |

### 2.2 Large Object Retention

| File | Pattern | Risk |
|------|---------|------|
| `src/core/memory/memory_recall.py` (lines 482-485) | Loads `_pf_cap` memories (default 500, up to `limit*40`) into `all_facts` list | **Medium**: 500 ORM objects per recall |
| `src/core/memory/memory_recall.py` (lines 680-748) | `embedding_map` dict stores vectors for hybrid results | **Medium**: Vectors held in memory during recall |
| `src/llm/router.py` (lines 414-565) | `chat_stream` accumulates `total_text` string (line 469) | **Low**: Stream yields tokens |
| `src/db/repos/memory_repo.py` (lines 1654-1709) | `get_graph_stats()` builds `adj` dict with ALL edges | **High** if graph grows large |

### 2.3 Asyncio Event/Task Leaks

| File | Pattern |
|------|---------|
| `src/core/cache/manager.py` (lines 69-72, 136-141) | `_write_events: dict[K, asyncio.Event]` — created per-key, cleaned in `_evict()` and `clear()` |
| `src/llm/provider_manager.py` (lines 262, 372-403) | `_CIRCUIT_BREAKERS_LOCK`, `_PROVIDER_METRICS_LOCK`, `_PURPOSE_SEMAPHORES` — global locks, initialized once |
| `src/main.py` (lines 295, 296) | `_cleanup_task`, `_update_check_task`, `_prefetch_task` — created, cancelled on shutdown |

---

## 3. Async Patterns & Blocking Calls

### 3.1 `asyncio.to_thread` / `run_in_executor` Usage (152 occurrences)

| Category | Files | Count | Assessment |
|----------|-------|-------|------------|
| **Qdrant (vector_store)** | `vector_store.py` | 12 | ✅ Appropriate — embedded Qdrant is sync |
| **Crypto (Fernet)** | `crypto.py` | 2 | ✅ Appropriate — CPU-bound |
| **Transcription (faster-whisper)** | `transcription.py` | 4 | ✅ Appropriate — CPU-bound model inference |
| **File I/O / parsing** | `ingest.py`, `documents.py`, `mcp_*.py` | 20+ | ✅ Appropriate — blocking I/O |
| **Gemini Provider** | `gemini_provider.py` | 6 | ⚠️ **Risk**: Uses `asyncio.to_thread` for HTTP calls — should use native async client |
| **SQLite PRAGMA checks** | `vector_store.py` | 4 | ✅ Appropriate — sync client calls |

### 3.2 ThreadPoolExecutor Misuse

| File | Lines | Issue |
|------|-------|-------|
| `src/main.py` | 555-572 | `ThreadPoolExecutor(max_workers=1)` for Alembic migrations — **blocks event loop for 120s**. Acceptable at startup only. |

### 3.3 Semaphore Contention

| File | Semaphores | Concurrency |
|------|------------|-------------|
| `src/llm/provider_manager.py` (lines 393-402) | Purpose semaphores: main=2, draft=1, memory=1, background=3, analysis=1, urgent=2, search=2, summarize=2, fallback=2 | **Low** for single-user |
| `src/llm/router.py` (lines 166, 370, 422) | `MultiKeyProvider._semaphore` (per provider, N=keys) + purpose semaphore | Nested acquisition — could deadlock if timeout |

---

## 4. Caching Efficiency

### 4.1 Recall Cache (`src/core/memory/memory_recall.py`)

| Metric | Value |
|--------|-------|
| Max size | `recall_cache_max_size` (default 1000) |
| TTL (with facts) | 30s (`recall_cache_result_ttl`) |
| TTL (empty) | 60s (`recall_cache_empty_ttl`) |
| Key includes | user_id, query, contact_id, mode, limit, offset, all flags, **version** |
| Invalidation | Version bump on mutation (`bump_recall_version`) — **O(1)** |
| Hit rate tracking | `ManagedCache.metrics.hit_rate` |

**Risk**: Short TTL (30s) for hits → frequent recomputation. Consider longer TTL for stable queries.

### 4.2 LLM Response Cache (`src/core/intelligence/llm_response_cache.py`)

| Feature | Implementation |
|---------|----------------|
| Tier 1 (exact) | SHA256 of first 200 chars |
| Tier 2 (semantic) | Category + normalized hash (names→{NAME}, dates→{DATE}, numbers→{NUM}) |
| Blanket categories | greeting, farewell, agreement, disagreement, gratitude, emotion — single key per category |
| TTL by category | 1h (greeting), 10m (question), 30m (trivial), 5m (default) |
| Context-dependent blocking | Detects "ты говорил", dates, @mentions, URLs, commands |

**Risk**: Normalization uses regex per call — CPU cost. Consider caching normalized forms.

### 4.3 Stats Cache (`src/db/repos/memory_repo.py:1195-1263`)

- 5-minute TTL, keyed by `mem_stats:{user_id}`
- Single query with scalar aggregates — **efficient**

### 4.4 Cache Cleanup

| Mechanism | Interval | Scope |
|-----------|----------|-------|
| `CacheManager.start_background_cleanup()` | 60s (`_CACHE_CLEANUP_INTERVAL`) | All `ManagedCache` instances |
| `cleanup_circuit_breakers()` | 300s (every 5th tick of 60s loop) | `_CIRCUIT_BREAKERS`, `_PROVIDER_METRICS` |
| `WorkingMemory` cleanup | 300s | Expired rows |
| `PendingAction` cleanup | 300s | Expired rows |

---

## 5. LLM API Efficiency

### 5.1 Provider Routing & Fallback

| Component | File | Optimization |
|-----------|------|--------------|
| `ProviderFallback` | `src/llm/router.py:650-859` | Adaptive sorting by success rate + latency (60%/40%) + recency penalty |
| `MultiKeyProvider` | `src/llm/router.py:125-602` | Round-robin key rotation, per-key circuit breaker, 3 retries with exponential backoff |
| `build_provider()` | `src/llm/provider_manager.py:814-1000` | Caches provider chain for 300s per user/purpose/task_type |

### 5.2 Token Usage & Redundant Calls

| Pattern | File | Assessment |
|---------|------|------------|
| `auto_select_model()` | `provider_manager.py:616-746` | Scores providers by tier, priority, success rate, explicit model — **avoids default model ambiguity** |
| `chat_stream` fallback | `router.py:733-767` | Tries streaming on each provider, falls back to regular chat — **good** |
| Embedding fallback | `router.py:769-836` | **Critical**: Validates dimension match to prevent Qdrant corruption |
| `validate_key()` | `router.py:591-598` | Tries all keys — could be cached |

### 5.3 Streaming Optimization

| File | Pattern |
|------|---------|
| `src/llm/router.py` (lines 414-565) | `chat_stream` holds purpose semaphore + provider semaphore, 180s overall timeout, yields tokens |
| **Risk** | Accumulates `total_text` in memory (line 469) — could grow large for long responses |

### 5.4 Auto-Batching

| Feature | File | Config |
|---------|------|--------|
| Auto-save batch | `src/core/memory/auto_save_batch.py` | Batch size 5, timeout 10s, max wait 60s |
| Settings | `config.py:548-561` | `auto_save_batch_enabled`, `auto_save_batch_size`, `auto_save_batch_timeout`, `auto_save_batch_max_wait` |

---

## 6. Background Task Overhead

### 6.1 Task Manager (`src/core/infra/task_manager.py`)

- **28 registered tasks** (see `main.py:_register_background_tasks()` lines 64-96)
- Exponential backoff: `base * 2^(consecutive-1)` capped at 300s
- Max restarts: 10, then escalation notification
- Fire-and-forget tracking: `_ff_tasks` set with `add_done_callback(discard)`

### 6.2 Dream Cycle (`src/core/scheduling/dream_cycle.py`)

| Phase | Operation | Frequency |
|-------|-----------|-----------|
| 1 | Decay + tier promotion/demotion | Daily 03:00 |
| 2 | Duplicate consolidation | Daily |
| 3 | Contradiction detection (batch scan 200 memories) | Daily |
| 3.5 | Dreaming V3 re-evaluation (up to 50 facts) | Daily |
| 4 | Digest rebuild (top 20 contacts) | Daily |
| 5 | Memory Wiki generation | Daily |
| 6 | DSM cleanup (30 days) | Daily |
| 7 | Auto-forget sweep | Daily |
| 8 | Close stale sessions (24h) | Daily |
| 9 | Episode reflection | Daily |
| 10 | Meta-Memory importance recalc | Daily |
| 11 | Dreaming Consolidator | Daily |

**Overlap guard**: `_overlap_guard` lock prevents concurrent runs.

### 6.3 Interval Tuning (from `config.py`)

| Task | Interval | Tunable? |
|------|----------|----------|
| Memory checker | 600s | `memory_check_interval_sec` |
| Memory consolidation | 21600s (6h) | `memory_consolidation_interval_sec` |
| Memory clusterer | 600s | `memory_clusterer_interval_sec` |
| Habit tracker | 3600s | `habit_tracker_interval_sec` |
| Proactive briefing | 300s | `proactive_briefing_check_sec` |
| Knowledge distiller | 600s | `knowledge_distiller_interval_sec` |
| Weekly digest | 3600s | `weekly_digest_check_sec` |
| Skill optimizer | 86400s | `skill_optimizer_interval_sec` |

**Risk**: Many 5-10 min interval tasks — could overlap under load. Consider coalescing or using single scheduler.

---

## 7. Startup/Shutdown Performance

### 7.1 Cold Start Sequence (`src/main.py`)

| Step | Time Estimate | Blocking? |
|------|---------------|-----------|
| Alembic migration | Up to 120s | **Yes** (ThreadPoolExecutor) |
| `init_db()` PRAGMAs + FTS5 | ~100-500ms | Yes (async) |
| LLM router locks init | ~1ms | Yes |
| DI container init | ~10-50ms | Yes |
| Skill seeding | ~1-5s | Yes |
| Context engine registration | ~10ms | Yes |
| FTS5 context indexing | ~100-2000ms | Yes |
| Vector store health check | ~100-500ms | Yes |
| Userbot restore | ~1-10s | Yes |
| Key rotation init | ~100ms | Yes |
| Task manager start_all | ~50-200ms | Yes |
| MCP tool registration | ~10-50ms | Yes |
| Startup prefetch | Background | No (async task) |

**Total cold start: ~2-5s (excluding Alembic)**. Alembic adds up to 120s.

### 7.2 Shutdown Sequence

| Step | Timeout | Forced After |
|------|---------|--------------|
| Background tasks cancel | 5s | Yes |
| Userbot shutdown | 15s | Yes |
| Task manager stop | 15s | Yes |
| Memory worker stop | 15s | Yes |
| Voice worker stop | 15s | Yes |
| Notification queue | 15s | Yes |
| Cache manager cleanup | 15s | Yes |
| Fire-and-forget tasks | 10s | Yes |
| Vector store shutdown | 10s | Yes |
| Playwright browser | 5s | Yes |

**Graceful shutdown: ~60-120s worst case** with forced timeouts.

---

## 8. Critical Findings & Recommendations

### 🔴 HIGH PRIORITY

| # | Issue | File | Impact | Fix |
|---|-------|------|--------|-----|
| 1 | **Unbounded `_CIRCUIT_BREAKERS` / `_PROVIDER_METRICS` dicts** | `provider_manager.py:262, 178` | Memory leak over weeks if cleanup fails | Add max-size guard + periodic size logging |
| 2 | **`get_graph_stats()` loads ALL edges into memory** | `memory_repo.py:1656-1662` | OOM if graph >10k edges | Add pagination / sampling / SQL-only aggregation |
| 3 | **Short recall cache TTL (30s) causes recomputation** | `memory_recall.py:33, 905-906` | High CPU for repeated queries | Increase to 60-120s; add cache warming |
| 4 | **No connection pool limits** | `session.py:16-21` | FD exhaustion under load | Add `pool_size=5, max_overflow=10` |
| 5 | **Gemini provider uses `to_thread` for HTTP** | `gemini_provider.py:72, 94, 171` | Blocks thread pool, higher latency | Migrate to native `httpx.AsyncClient` |

### 🟡 MEDIUM PRIORITY

| # | Issue | File | Impact | Fix |
|---|-------|------|--------|-----|
| 6 | **`chat_stream` accumulates full response in `total_text`** | `router.py:469` | Memory for long streams | Remove accumulation; log length only |
| 7 | **Recall loads 500 ORM objects per call** | `memory_recall.py:482-485` | GC pressure | Stream results / use scalar subqueries |
| 8 | **`_rec_version` dict unbounded (per-user)** | `memory_recall.py:64` | Leak if users added/removed | Periodic cleanup of inactive users |
| 9 | **Nested semaphore acquisition** | `router.py:370, 422` | Deadlock risk | Flatten or add timeout logging |
| 10 | **Many 5-min background tasks may overlap** | `main.py:67-96` | Contention | Coalesce into single scheduler with offsets |

### 🟢 LOW PRIORITY / OPTIMIZATIONS

| # | Opportunity | File | Effort |
|---|-------------|------|--------|
| 11 | Cache normalized forms in LLM response cache | `llm_response_cache.py` | Low |
| 12 | Add cache hit-rate metrics export | `cache/manager.py` | Low |
| 13 | Batch `mark_key_used` / `mark_key_failure` DB calls | `router.py:278-286, 315-323` | Medium |
| 14 | Use `asyncio.TaskGroup` (3.11+) for cleaner task management | `task_manager.py` | Medium |
| 15 | Add query plan logging for slow SQL (>100ms) | `session.py` / repo | Low |

---

## 9. Impact Boundary Assessment

| Change Area | Affected Modules | Risk Level |
|-------------|------------------|------------|
| Database connection pooling | `session.py`, all repos | Medium (test under load) |
| Cache TTL/size tuning | `config.py`, `memory_recall.py`, `llm_response_cache.py` | Low |
| Circuit breaker cleanup | `provider_manager.py`, `main.py` cleanup loop | Low |
| Graph stats optimization | `memory_repo.py`, `dream_cycle.py` | Medium |
| LLM provider async migration | `gemini_provider.py`, `router.py` | High (test all providers) |
| Background task consolidation | `main.py`, `dream_cycle.py`, `task_manager.py` | Medium |

---

## 10. Suggested Agent Assignments

```json
{
  "suggested_agents": [
    {
      "agent": "backend-dev",
      "effort": "high",
      "reason": "Fix unbounded circuit breaker dicts, connection pool config, graph stats memory"
    },
    {
      "agent": "backend-dev",
      "effort": "medium",
      "reason": "Migrate Gemini provider to native async HTTP client"
    },
    {
      "agent": "backend-dev",
      "effort": "medium",
      "reason": "Optimize recall cache TTL, reduce ORM object loading, add cache metrics"
    },
    {
      "agent": "backend-dev",
      "effort": "low",
      "reason": "Consolidate background task intervals, add overlap offsets"
    },
    {
      "agent": "test-engineer",
      "effort": "high",
      "reason": "Load test database pool, cache eviction, LLM fallback chains under concurrency"
    }
  ]
}
```

---

## Classification

**Type:** Performance audit / optimization research (not a bug/feature)  
**Relevant Files:** 15+ core files listed above  
**Impact Boundary:** Database layer, cache layer, LLM routing, background scheduler  
**Risks:** Memory leaks in global dicts, OOM on large graphs, thread pool saturation, short cache TTLs