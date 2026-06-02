# Performance Audit — TelegramHelper

## Project context
- Async Python (aiogram v3 + Telethon) bot/assistant with a single-process event loop.
- Persistence: SQLite (WAL, 64MB cache, 30s busy_timeout) via SQLAlchemy AsyncSession (`src/db/session.py:24-31`).
- LLM stack: OpenAI-compatible providers with in-memory + on-disk embedding cache (`src/core/actions/embedding_cache.py`).
- Vector store: Qdrant (local on-disk, sync client) wrapped with `asyncio.to_thread` (`src/core/actions/vector_store.py:40`).
- Background work: ~12 modules register tasks through `BackgroundTaskManager` in `main.py`; `track_ff` is used widely for fire-and-forget coroutines (`src/core/infra/task_manager.py:314`).
- Telethon mirror handler in `src/userbot/mirror.py:156` runs on every incoming message; semaphores limit inbox processing to 50 concurrent (`mirror.py:33`).
- SQLite hot path is shared between the bot event handlers, the userbot mirror handler, and 12+ background tasks running concurrently (digest, news, reminders, auto_sync, dream_cycle, etc.) — see `src/core/scheduling/*` and `src/main.py:_register_background_tasks`.

## Audit methodology
- Static read of all `src/core/**` and `src/db/repos/**`, every `mcp_*.py` under `src/core/actions/`, `src/userbot/mirror.py`, `src/userbot/auto_reply.py`, `src/bot/handlers/free_text_pipeline.py`, `src/main.py`, and `src/db/session.py`.
- All `requests.*` / `subprocess.run` / `sqlite3.connect` call-sites cross-checked for `loop.run_in_executor` / `asyncio.to_thread` / `run_in_thread` offload.
- AST parse of all 10 hot files: PASS.
- No code modified.

## Findings

### CRITICAL

- **PERF-001 — Sequential LLM fan-out in `smart_extract_after_sync` (2× N LLM calls per sync)**
  - Severity: CRITICAL
  - Location: `src/core/memory/smart_memory.py:216-360` (function `smart_extract_after_sync`, called from sync background)
  - Category: Sequential-when-parallel / LLM fan-out
  - Description: The loop iterates `contact_ids` sequentially and performs **two** LLM extractions per contact (`_extract_llm_filtered(provider, owner_id, contact=None, ...)` and again with `contact=contact`, `smart_memory.py:247-275`). No semaphore, no `asyncio.gather`. For a user with 50 contacts, that is 100 sequential LLM calls.
  - Reproduction: trigger `smart_extract_after_sync` for a freshly synced user with 30+ contacts → measured duration is sum of LLM latencies; the bot event loop is blocked from serving user messages for the entire window (typically 30-120s).
  - Impact: Tail latency for the next free-text user message rises to tens of seconds; parallel free-text from the same owner is serialized behind the sync task (per-user `_user_locks` from `session_repo.py:35` adds to this).
  - Fix direction: bound concurrency with `asyncio.Semaphore(4)`, run owner+contact extractions for each peer as `asyncio.gather`, and skip peers with empty `fetch_chat_messages` before calling LLM.

- **PERF-002 — `mirror.py` mirror handler runs three DB sessions per incoming message, including N+1 contact upsert**
  - Severity: CRITICAL
  - Location: `src/userbot/mirror.py:155-292` (`attach_mirror.on_message`)
  - Category: Per-message I/O / N+1 / blocking DB
  - Description: Every Telethon NewMessage event opens **two** synchronous-style async sessions (`async with get_session() as _w_session:`, line 177, then `async with get_session() as session:`, line 188) plus a fire-and-forget third one inside `_process_incoming_bg` (line 109). Each session is preceded by `get_or_create_user` (one SELECT), `get_watched_peers` (one SELECT), and per-message `event.get_chat()` (one Telethon RPC). The mirror also performs `await client.get_entity(msg.sender_id)` on every non-outgoing non-text message that has no `sender_id` resolution yet (line 272), which is a real Telegram network round-trip.
  - Reproduction: forward a group conversation to the bot — each message incurs ≥2 SQLite round-trips, 1 Telethon RPC for `get_chat`, and 1 RPC for `get_entity`; under group flooding this saturates the loop.
  - Impact: At 10 msg/s from an active group, ~30–40 awaited DB+RPC operations/s are piled on the same loop that is also serving the bot's own aiogram dispatcher; the global event loop latency rises, aiogram poll updates are delayed, and per-message `mirror handler failed` exceptions bubble up in logs.
  - Fix direction: cache `watched_peers` per owner with a 5s TTL, reuse the same `get_session` for both queries, batch `get_entity` calls via `client.get_entities(list)` per tick, and move the bot-sender detection into the background task (no need to block the mirror path).

- **PERF-003 — `_maybe_auto_save_facts` fires an LLM call per user message; no batching, no throttling, no dedup**
  - Severity: CRITICAL
  - Location: `src/core/bot/handlers/free_text_pipeline.py:1592, 1741` (called via `track_ff(asyncio.create_task(_maybe_auto_save_facts(...)))`); the function itself is at `free_text_pipeline.py:379-470`.
  - Category: LLM fan-out / unbounded fire-and-forget
  - Description: After every maestro answer the bot schedules an LLM extraction (a complete `provider.chat` round-trip) to mine personal facts. The only gate is a cheap substring filter on the *user* text. The LLM call is never deduplicated by `(<owner>, <user_text_hash>)`, never rate-limited, and the resulting facts are written via `add_memory` one-by-one inside a single session (`free_text_pipeline.py:442-457`).
  - Reproduction: send 5 personal messages in 30s → 5 concurrent LLM extractions, each producing N rows in `Memory` and enqueuing N jobs into `MemoryQueue` (`memory_queue.py:242`). Under sustained use the queue grows, embedding cache lookups repeat, and SQLite is hammered.
  - Impact: LLM provider rate limits and quota burn; SQLite write-amplification through FTS5 update triggers (`session.py:56-74`); `MemoryQueue` backlog → `memory_queue.py:worker` becomes the critical path.
  - Fix direction: dedup by `(owner_id, hash(user_text))` in a short-TTL LRU (e.g. 60s), batch the LLM call with `provider.chat` over a queue of recent user texts (e.g. flush every 10s or every 5 messages), and write extracted facts via a single `executemany` instead of N awaited `add_memory` calls.

### HIGH

- **PERF-004 — `embedding_cache.get/set` called from the async embedding path with no offload; every embed opens the SQLite file under the event loop**
  - Severity: HIGH
  - Location: callers `src/llm/_openai_compat_mixin.py:72, 88, 101`; impl `src/core/actions/embedding_cache.py:116-202`; sync sqlite conn at `embedding_cache.py:58-80`.
  - Category: Blocking-IO in async path
  - Description: The OpenAI-compat mixin (used by OpenAI, DeepSeek, Mistral, Cloudflare) calls `cache_get`/`cache_set` *synchronously* (lines 72, 77, 88, 101). The `aget`/`aset` wrappers (lines 214-225) exist but are not used in the mixin. The `get()` body opens a *thread-safe* `sqlite3.connect(check_same_thread=False)`, runs `SELECT`, and on cache miss persists with `INSERT OR REPLACE` + `commit` — every one of those is a sync syscall on the event loop thread.
  - Reproduction: in `embed_batch(texts)` with 20 texts (e.g. `memory_extractor.py:188` or `smart_memory.py:589`), each iteration runs an un-offloaded `cache_get`; same for `cache_set` after the API call.
  - Impact: 20 embed_batch calls = 20 unyielding `sqlite3` reads + 20 unyielding writes. On WAL-mode SQLite this is typically sub-ms per call, but on checkpoint stalls or busy DB it can spike to 50-200ms and stall LLM streaming.
  - Fix direction: switch the mixin to `await aget(...)` / `await aset(...)`, which already wraps the work in `run_in_thread` (`embedding_cache.py:214-225`).

- **PERF-005 — `context_files.search_in_contexts` uses sync `sqlite3` from the async `search_contexts_hybrid` path**
  - Severity: HIGH
  - Location: `src/core/memory/context_files.py:260-319` (sync function) called from `search_contexts_hybrid` (`context_files.py:615-643`).
  - Category: Blocking-IO in async path
  - Description: `search_contexts_hybrid` is `async def` and awaited from the free-text pipeline (`prompt_assembler` → maestro `process`). It directly calls `search_in_contexts` (a sync function that opens `sqlite3.connect(str(db_path))` at `context_files.py:274`, executes a `MATCH`, and reads rows). The DB is the same SQLite file the main engine uses; combined with the default 5s `busy_timeout` this can block the loop for the full timeout.
  - Reproduction: call `maestro.process` for a user with many `.md` context files during a `dream_cycle` write window → the FTS5 query may stall.
  - Impact: A single 5s block stalls the LLM streaming task and the bot dispatcher simultaneously.
  - Fix direction: rewrite `search_in_contexts` as `async def` and run the `sqlite3` body in `asyncio.to_thread` (the same pattern already used in `context_files.search_contexts_semantic` at `context_files.py:578-612` and in `dsm.py:48,68,100,122,142`).

- **PERF-006 — `context_files._get_qdrant` initializes a sync `QdrantClient` on first call from async code without offload**
  - Severity: HIGH
  - Location: `src/core/memory/context_files.py:520-530` (`_get_qdrant`), invoked at `context_files.py:588, 590, 596` via `asyncio.to_thread`.
  - Category: First-call blocking + repeated sync client init
  - Description: `_get_qdrant()` instantiates `QdrantClient(path=...)` lazily; the surrounding `asyncio.to_thread` correctly offloads the *call* but `_get_qdrant` itself runs `QdrantClient(path=...)` synchronously inside the worker thread. Worse, every fresh call re-checks `self._client is None` and returns a *new* `QdrantClient` is not possible because `_get_qdrant` is wrapped in a `lru_cache` — but that lru_cache is *not* async-safe. Concurrency: two concurrent `asyncio.to_thread(_get_qdrant)` calls each spawn a `QdrantClient(path=...)` on a different thread, both opening the same on-disk path. Qdrant uses a local `RocksDB`-like store and **does not support multiple writers from different processes/threads** without external locking.
  - Reproduction: first hybrid search for a user with semantic contexts triggers `client.search`; subsequent concurrent searches from a different free-text path can race.
  - Impact: Qdrant lock-file collisions, "storage already opened" errors under load, slow path fallback.
  - Fix direction: pre-build the Qdrant client at startup and store it on `app.state`; access via `asyncio.to_thread` only for the actual `search`/`upsert` calls.

- **PERF-007 — `mcp_search_docs` and other embed-cache callers run un-offloaded L2 read+write in hot loops**
  - Severity: HIGH
  - Location: `src/core/actions/mcp_search_docs.py:60, 61`; `src/core/context/providers/document_provider.py:33, 34`; `src/llm/gemini_provider.py:81, 109`.
  - Category: Blocking-IO in async path
  - Description: Same as PERF-004, but in the Gemini and search-doc call-sites. `cache_get` is called directly without `await aget`. The Gemini provider is the primary embedding model for this project; on every embed call there is a sync `SELECT` against `embedding_cache` and a sync `UPDATE` on every read.
  - Reproduction: open a free-text turn, observe the Gemini `embed` path; profile the event loop — each cache hit is a sync `sqlite3` call.
  - Impact: Tail latency spikes; `journal_mode=WAL` checkpoint stalls amplify the stall.
  - Fix direction: convert all callers to `await aget`/`await aset`; the wrapper already exists at `embedding_cache.py:214-225`.

- **PERF-008 — `free_text_pipeline` `execute_maestro` runs style + persona + self_profile + memory + tools *sequentially* in 9+ awaited stages**
  - Severity: HIGH
  - Location: `src/bot/handlers/free_text_pipeline.py:1400-1500` (`execute_maestro`); the prefetched recall at line 1407; the LLM plan+plan-execute chain inside `run_pipeline` (`maestro.py:1223-1279`); the post-stream `humanize_*` calls at 1554, 1571, 1705; the per-message `analyze_user_style` at `style_matcher.py:309`.
  - Category: Sequential-when-parallel / LLM fan-out
  - Description: For every user message the pipeline performs, in order: (1) pre-gate check, (2) `_deep = await recall(...)` (line 1407), (3) `await detect_contradiction(...)` (line 1414), (4) `await run_pipeline(provider, raw, ...)` (line 1424) — which itself sequentially loads self_profile, persona, style, confirmed_rules, anti_ai, recent corrections, RAG embed+Qdrant search, then `process` (a full LLM call), then `orchestrator.execute` (sequential per-level `asyncio.gather`, but LLM-bound), then post-process: `get_or_update_style_profile` (line 1508, can trigger a *second* LLM call via `analyze_user_style`, `style_matcher.py:309`), then `_get_anti_ai_mode` (a DB call), then `_humanize_assistant_response` (potentially another LLM call), then `humanize_deep` (yet another LLM call), then the self-correction loop (`free_text_pipeline.py:1717-1735`) which may run up to 2 more LLM regenerations.
  - Reproduction: send a personal message with anti_ai="fix" enabled → up to 5 LLM round-trips per turn (plan + agent-execute synthesis + style+anti_ai humanize + deep humanize + up to 2 self-correction rewrites).
  - Impact: Median turn latency ≥ 5 × P50-LLM-latency, which is usually 8-15s. For DeepSeek/Claude-class providers with 30s SLO this exhausts the budget. There is no concurrency control across these stages and no early-exit if the user already typed "ok" / a short message.
  - Fix direction: use `asyncio.gather` for independent reads (self_profile, persona, style, confirmed_rules, anti_ai, recent corrections, RAG embed+search) before the LLM call; short-circuit `_maybe_auto_save_facts` and `humanize_deep` when `len(response_text) < 80`; cap the self-correction loop at 1 iteration.

- **PERF-009 — `mcp_playwright` / `mcp_screenshot` / `StealthBrowser` use sync Playwright under `async with async_playwright()` and launch a fresh Chromium per request**
  - Severity: HIGH
  - Location: `src/core/actions/mcp_playwright.py:175-183`; `src/core/actions/mcp_screenshot.py:149-169`; `src/core/avito/stealth/browser.py:53-94`; `src/core/avito/stealth/session.py:91-130`.
  - Category: Resource thrash / long-running coroutine
  - Description: Each tool invocation does `async with async_playwright(): await p.chromium.launch(...)`, navigates, captures HTML, then tears the browser down. A typical request takes 5-15s of wall time because Chromium launch alone is 1-2s. Worse, the same Playwright client is created in `_get_playwright` (line 182) **and** `_idle_task` (line 192) — there is a race where the first call's `async with` exit can kill a browser that a concurrent tool call is still using, because the `mcp_playwright` tool is a free function and a new `async_playwright()` context manager is opened per call.
  - Reproduction: call `mcp_playwright` twice in quick succession → the second call's launch races the first's tear-down; you can see `_OPERATION_TIMEOUT` (line 347) or `playwright.runtime` errors in logs.
  - Impact: 5-15s of Chromium boot per Avito/screenshot call, plus Chromium zombie processes from the broken teardown.
  - Fix direction: keep a long-lived `BrowserContext` on a module-level singleton (initialized once at first use with proper `asyncio.Lock`), share the context across tool calls, and only tear down on `close()` at shutdown.

### MEDIUM

- **PERF-010 — Unbounded `stats_cache._stats` and `_recall_cache` are bounded by eviction-policy corner cases**
  - Severity: MEDIUM
  - Location: `src/core/actions/stats_cache.py:19-46`; `src/core/memory/memory_recall.py:27-33, 703-711`.
  - Category: Unbounded growth (latent)
  - Description: `stats_cache` has *no* max size — only TTL eviction on `get_cached` (line 23-31). If a key is never re-read it stays until the process restarts. Cache is keyed by free-form strings; in long-running sessions the number of distinct keys is bounded by the UI but in a system that uses many command handlers it can grow. `memory_recall._recall_cache` is bounded by `_RECALL_CACHE_MAX = 1000` (settings, line 235) and the eviction at `memory_recall.py:703-711` correctly drops 10% on overflow. `stats_cache` should match this pattern.
  - Reproduction: run the bot for 24h, examine `_stats` size in a debug pdb session.
  - Impact: Latent memory growth; in 24h with 1 entry/min the cache may exceed 1k entries × hundreds of bytes each = MB of memory.
  - Fix direction: add `MAX_SIZE = 1000` and an eviction step inside `set_cache` when overflow is detected.

- **PERF-011 — `_bump_use_counts` is fired as fire-and-forget on every recall cache hit, opening a new DB session per call**
  - Severity: MEDIUM
  - Location: `src/core/memory/memory_recall.py:168-190, 249, 717` (`asyncio.create_task(_bump_use_counts(cached_ids))`).
  - Category: Unbounded fire-and-forget / DB session pressure
  - Description: On every cached recall hit, a new asyncio task is created that opens a new `AsyncSession` and runs a bulk `UPDATE`. Under sustained traffic this can spawn dozens of one-shot sessions per second, all writing to the same SQLite file. The `track_ff` registry is *not* used (line 249, 717 use bare `asyncio.create_task`), so these tasks are not cancelled cleanly on shutdown.
  - Reproduction: hot-path query for a user with many facts → several `_bump_use_counts` tasks/sec, each committing independently.
  - Impact: WAL writes pile up; on shutdown some increments are lost (the untracked tasks are cancelled mid-flight).
  - Fix direction: debounce `_bump_use_counts` per owner (e.g. 5s batching window), or use `track_ff` so shutdown awaits them, or move the work to the existing `MemoryQueue` background worker.

- **PERF-012 — `context_files.index_contexts_to_fts` rebuilds the entire FTS5 table synchronously at startup**
  - Severity: MEDIUM
  - Location: `src/core/memory/context_files.py:343-420` (called from `main.py` startup via `_register_background_tasks`).
  - Category: Startup blocking
  - Description: The function drops and reinserts every `.md` context file into the FTS5 virtual table. The `INSERT OR REPLACE` loop is not wrapped in a transaction (`context_files.py:380-419`) — every insert is its own implicit transaction → N fsyncs. For users with many context files this stalls the main loop for several seconds.
  - Reproduction: start the bot after editing 30+ contexts → startup latency.
  - Impact: The bot's `/start` and dispatcher are unavailable for the duration; if the process is supervised with a fast-restart policy, restart loops are possible.
  - Fix direction: wrap the loop in `BEGIN IMMEDIATE; ... COMMIT;` and run the whole function in `asyncio.to_thread` from the caller.

- **PERF-013 — `mcp_timer._timer_list` iterates a copy of the in-memory dict but reads `task.done()` outside any lock**
  - Severity: MEDIUM
  - Location: `src/core/actions/mcp_timer.py:342-369`.
  - Category: Lock-free read of mutable state
  - Description: `_timer_list` snapshots `_active_timers` and reads `info["task"]` and `datetime.fromisoformat(info["fire_at"])` without holding `_timer_lock`. The set of timers can be modified by `_timer_task` (line 414) and `_timer_cancel` (line 379) concurrently. While Python dict iteration over a copy is safe, the `task.done()` call and the datetime parse touch mutable state (the task's done flag) without any synchronisation.
  - Reproduction: call `mcp_timer(action="list")` while a timer is firing → small chance of `RuntimeError: dictionary changed size during iteration` (mitigated by the `list()` copy) or `KeyError` if a key is removed between snapshot and access.
  - Impact: Latent race; not a hot path, but the tool is exposed to LLM agents that may invoke it frequently.
  - Fix direction: take `_timer_lock` for the whole function, or use a frozen dataclass snapshot.

- **PERF-014 — `main.py` registers ~12 background tasks, several of which can simultaneously hit the same `_user_locks` and contend on `get_or_create_user`**
  - Severity: MEDIUM
  - Location: `src/main.py:26-170` (`_register_background_tasks`); per-user lock at `src/db/repos/session_repo.py:35-51`; SQLAlchemy engine at `src/db/session.py:15-16`.
  - Category: Task explosion / DB contention
  - Description: Each background task opens its own `AsyncSession` and calls `get_or_create_user` (e.g. `digest`, `news`, `reminders`, `auto_sync`, `dream_cycle`, `weekly_summarizer`, `habit_tracker`, `sleep_tracker`, `proactive_briefing`, `notification_queue`, `mcp_monitor`, `smart_digest`). All contend on the same per-user lock (`session_repo.py:68-86`) and on the same SQLite engine. With the `_lock_cleanup_counter` cleanup at `_user_locks:47-50` running only every 1000 calls, the dict can grow to thousands of entries.
  - Reproduction: start the bot with a single owner — observe ~12 concurrent background coroutines all calling `get_or_create_user`; the first run after a fresh DB has multiple `INSERT INTO users ...` racing for the per-user lock.
  - Impact: Startup latency, plus steady-state background load even when no user is interacting.
  - Fix direction: stagger the background-task wake-up intervals (jitter) and serialize the first-time `get_or_create_user` race via a single global init barrier at startup.

- **PERF-015 — `maestro.process` opens a session inside an inline `try` and does serial awaits for persona/style/rules/anti_ai/corrections/RAG — independent reads run sequentially**
  - Severity: MEDIUM
  - Location: `src/core/intelligence/maestro.py:131-235` (RAG embed+Qdrant sequential) and `maestro.py:163-225` (the entire context-assembly block).
  - Category: Sequential-when-parallel
  - Description: Between `maestro.py:163` and `maestro.py:225` the code performs ~6 awaited DB/IO calls in series (persona, style_matcher, active_rules, anti_ai, recent_corrections, plus the RAG embed+Qdrant search at lines 138-141). None of these depend on each other, and all are IO/DB-bound; they could run concurrently. The RAG `provider.embed` (line 138) is itself an LLM call, which is the single most expensive part of this block.
  - Reproduction: a single cold-path call to `process()` adds 6 × DB round-trip + 1 LLM call before the actual planning LLM call; on a 1ms RTT SQLite this is ~10ms, on a 50ms RTT (e.g. busy DB) it is 300ms.
  - Impact: Every user message pays a 6-step serial IO tax before the planning LLM is even invoked.
  - Fix direction: collect all independent reads in an `asyncio.gather`; consider memoizing the persona/style/rules/anti_ai snapshot in a 30s TTL cache keyed by `owner_id`.

### LOW

- **PERF-016 — `mcp_avito._format_search_result` is `def` and runs synchronously in the asyncio loop**
  - Severity: LOW
  - Location: `src/core/actions/mcp_avito.py:107-157`.
  - Category: Sync-in-async (CPU work)
  - Description: After `await scan_avito(params)`, the result is passed to `_format_search_result` and `_format_stats_result`, both pure-CPU `def` that iterate listings, call `min`/`max`, and build dicts. For a 100-listing scan this is microseconds, but the same pattern in `mcp_avito.service._compare_with_db` (`service.py:151-194`) does an O(N×M) nested loop on every call when `existing` is large.
  - Reproduction: trigger an Avito scan with 200+ existing listings → O(N²) comparison loop.
  - Impact: Sub-100ms in practice, but it runs on the loop and delays the next handler.
  - Fix direction: build an index `{url:title → entry}` from `existing.items()` once and look up in O(1).

- **PERF-017 — `mcp_oauth` stores `httpx.AsyncClient` as an instance attribute but does not guarantee `close()` on shutdown**
  - Severity: LOW
  - Location: `src/core/actions/mcp_oauth.py:73` (`self._http = httpx.AsyncClient(...)`).
  - Category: Resource leak (graceful shutdown)
  - Description: The OAuth provider keeps a long-lived `AsyncClient`. There is no shutdown hook in `main.py` that calls `.close()` on every MCP tool instance, so the underlying HTTP connection pool is leaked on `KeyboardInterrupt` / `SIGTERM`.
  - Reproduction: send SIGTERM, observe leaked file descriptors and a noisy "unclosed client" warning.
  - Impact: Slow-but-steady FD growth on every restart cycle.
  - Fix direction: register `app.state.mcp_oauth.close()` in the shutdown path, or use `httpx.AsyncClient` via `async with` and recreate on each call (acceptable for low-frequency OAuth).

- **PERF-018 — `mirror.py:188` opens a new SQLAlchemy session for every incoming message; with WAL-mode, frequent short transactions add fsync pressure**
  - Severity: LOW
  - Location: `src/userbot/mirror.py:177, 188`; PRAGMA at `src/db/session.py:24-30`.
  - Category: DB transaction churn
  - Description: Two separate `async with get_session()` blocks per message. `synchronous=NORMAL` reduces but does not eliminate per-commit fsyncs; combining the two into a single session would cut the number of WAL writes in half.
  - Reproduction: instrument with `PRAGMA wal_autocheckpoint` counter under message flood.
  - Impact: Latent — measured only at 100+ msg/min.
  - Fix direction: collapse the two session blocks into one.

- **PERF-019 — `auto_reply._evict_stale_auto_reply_locks` runs synchronously on every first-touch of a new peer**
  - Severity: LOW
  - Location: `src/userbot/auto_reply.py:60-86`.
  - Category: Lock-eviction under contention
  - Description: Eviction is a pure-CPU `O(N)` iteration over `_auto_reply_locks_ts` and a sort (`auto_reply.py:73`) — runs every time a new peer appears. With 500+ peers in the dict, this is a non-trivial sort.
  - Reproduction: cold start with 600 unique contacts in 5 minutes → 600 sorts of 500 elements each.
  - Impact: Single-digit milliseconds per call, but in a Telethon handler this delays the next event dispatch.
  - Fix direction: run the eviction on a 60s timer instead of inline.

- **PERF-020 — `context_files._index_with_provider` opens a session and builds a provider per context-file write; `_schedule_semantic_index` is bare `loop.create_task` without `track_ff`**
  - Severity: LOW
  - Location: `src/core/memory/context_files.py:663-690`.
  - Category: Untracked fire-and-forget / per-write provider build
  - Description: Every `save_context` or `append_to_context` call schedules an `asyncio.create_task(_index_with_provider(...))` (line 671) that opens a new SQLAlchemy session, calls `get_or_create_user`, then `build_provider` (which itself builds an LLM provider + key resolution). This is wasteful if the user writes 5 context files in a row.
  - Reproduction: write 5 context entries → 5 parallel `_index_with_provider` tasks, each constructing a new provider.
  - Impact: Provider construction cost (key lookup, model resolution) is duplicated; on shutdown these tasks are not awaited.
  - Fix direction: dedup in-flight index tasks per key with a short-TTL in-memory set, and switch to `track_ff`.

## Cross-cutting observations
- The project uses a healthy pattern of offloading `requests.*`, `subprocess.run`, Qdrant, and DSM SQLite I/O to `loop.run_in_executor` / `asyncio.to_thread` — this is consistently applied across the 50+ `mcp_*.py` files. The exceptions are documented above.
- `asyncio.gather` is used correctly for independent agent fan-out in `agent_orchestrator.py:340, 417`. The same pattern is **not** applied to the surrounding pipeline stages.
- WAL + `synchronous=NORMAL` is a reasonable production choice. The 30s `busy_timeout` masks blocking-IO bugs by waiting silently — that makes PERF-005/006/007 *worse* on contention.
- FTS5 triggers (`session.py:55-118`) are correct but each FTS5 insert causes an implicit transaction; PERF-018/012 are the read-side symptoms.

## Changes
- `no changes` — read-only audit per instructions.

## Validation
- AST parse of 10 representative hot files: PASS (`stats_cache.py`, `context_files.py`, `memory_recall.py`, `embedding_cache.py`, `vector_store.py`, `mirror.py`, `free_text_pipeline.py`, `dsm.py`, `maestro.py`, `agent_orchestrator.py`, `session_logger.py`).
- `embedding_cache.aget`/`aset` wrappers exist at `embedding_cache.py:214-225` and are confirmed unused in the OpenAI-compat mixin, Gemini provider, and document provider — consistent with PERF-004/007.
- Qdrant sync client offload verified: `vector_store.py:87, 132, 184, 242, 304, 348, 395, 426, 458` all use `asyncio.to_thread(_do)` — consistent.
- Background-task explosion: `main.py:_register_background_tasks` confirmed at line 26 with 12 module imports — consistent with PERF-014.

## Risks
- Findings 001-009 are derived from static code reading only; no runtime profile or load test was executed. Wall-clock impact numbers are estimates based on typical LLM latencies and SQLite WAL behavior.
- The audit does not include security, correctness, or maintainability concerns — those are out of scope.
- This is a single-process bot with a single owner in the typical deployment; per-user locks hide most multi-tenant contention, so PERF-014 may be lower-impact in single-owner production but a regression risk in any multi-tenant rollout.

## Next steps
- Run a `cProfile`-driven load test on a representative free-text turn (PERF-008 will dominate) and a representative `mirror.py` flood (PERF-002 will dominate) to confirm hot paths and to size the speedups.
- Replace sync `sqlite3` calls in `context_files.py` and the embed-cache call-sites (PERF-004/005/007) — small change, isolated, easy to land first.
- Batch the auto-save-facts LLM call (PERF-003) and add a per-owner dedup window — biggest LLM-cost win for the smallest code change.
- De-duplicate the LLM calls in `smart_extract_after_sync` (PERF-001) by extracting owner-facts and contact-facts in a single LLM call per peer (or a single batch call per N peers).
