# Security & Reliability Findings

## 1. Race Conditions

### 1.1 userbot manager `restore_all()` + bot start (CRITICAL)
**File**: `src/userbot/manager.py:41-124`, `src/main.py:254-255`
- `restore_all()` iterates ALL users and creates Telethon clients sequentially
- No protection against concurrent `restore_all()` calls (e.g., if called twice)
- `register_client()` (line 128-136) attaches handlers every time it's called — can cause **duplicate event handlers** on same client if `restore_all()` runs twice or if `register_client()` is called manually after `restore_all()`
- **Reproduction**: Call `restore_all()` twice quickly, or start bot while `restore_all()` is in progress from another process

### 1.2 Background tasks + shutdown (HIGH)
**File**: `src/main.py:188-246`, `src/userbot/manager.py:179-206`
- `_cleanup_global_state` task runs every 60s with DB operations
- On shutdown: tasks cancelled with 5s timeout, but DB operations inside may not complete
- `task_manager.stop_all()` (line 419) cancels tasks but `stop_ff_tasks()` (line 436) waits 10s for fire-and-forget
- **Race**: `_cleanup_global_state` may be mid-transaction when cancelled → potential partial commits/rollbacks
- No `asyncio.shield` for critical DB writes during shutdown

### 1.3 Cache invalidation — `ReplyDedup` not thread-safe (MEDIUM)
**File**: `src/bot/reply_dedup.py:11-36`
- `OrderedDict` operations not atomic: eviction loop (lines 28-29) + check (line 30) + insert (line 35)
- Multiple concurrent `is_duplicate()` calls can cause: double-insert, missed eviction, or KeyError
- **Reproduction**: Burst of messages to same chat from multiple handlers simultaneously

### 1.4 VectorStore concurrent upserts (MEDIUM)
**File**: `src/core/actions/vector_store.py:37-41`, `265-306`, `308-351`
- `_lock` protects upsert operations but `_ensure_collection` has its own lock (line 55)
- Double-lock pattern: `upsert` → `_ensure_collection` (lock) → upsert body (lock)
- But `reindex_collection` (line 412-442) also takes lock — potential deadlock if reindex runs during upsert
- **Missing**: No lock in `search()` (line 353-410) — read during write can see inconsistent state

---

## 2. Memory Leaks

### 2.1 Unclosed Telethon clients on error paths (HIGH)
**File**: `src/userbot/manager.py:58-123`
- `restore_all()`: If `client.connect()` succeeds but `is_user_authorized()` fails (line 60), client disconnected (line 75)
- But if exception occurs BETWEEN connect and is_user_authorized (line 59-60), client NOT disconnected
- FloodWaitError handling (lines 89-117): On retry success, client registered; on failure, disconnected (line 113)
- **Missing**: `finally` block to ensure disconnect on ANY exception

### 2.2 Pending login clients not cleaned up on FSM timeout (HIGH)
**File**: `src/bot/handlers/login.py:109-162`, `src/userbot/manager.py:152-177`
- `step_phone` creates `PendingLogin` with Telethon client (line 124-125)
- If user never sends code → FSM stays in `LoginStates.code` indefinitely
- `cancel_pending` only called on explicit `/cancel` or errors
- **No timeout** for pending login — client stays connected forever
- **Reproduction**: Start `/login`, enter phone, then walk away — client leaks

### 2.3 Background tasks not tracked for cancellation (MEDIUM)
**File**: `src/userbot/mirror.py:277-288`, `src/main.py:433-438`
- `track_ff()` creates fire-and-forget tasks for `_process_incoming_bg`
- `stop_ff_tasks()` waits 10s but no guarantee all tasks finish
- `_bg_semaphore` (line 33) limits concurrency but tasks not stored anywhere
- On rapid shutdown: in-flight `_process_incoming_bg` tasks may have open DB sessions

### 2.4 Qdrant client not closed on init failure (LOW)
**File**: `src/core/actions/vector_store.py:36-50`, `544-550`
- `VectorStore.__init__` creates `QdrantClient` (line 40)
- If `_ensure_collection` fails later, client stays open
- `shutdown()` exists but only called from `main.py:445-449`

### 2.5 No weakref usage for caches — potential reference cycles (LOW)
**File**: `src/bot/reply_dedup.py:11-36`, various caches in `src/core/cache/`
- `ReplyDedup._cache` holds strong references to strings
- No `weakref` for userbot clients in `UserbotManager._clients` — if user logs out but reference held elsewhere, client not GC'd

---

## 3. FSM Edge Cases (login.py)

### 3.1 User sends non-digit text in code state (MEDIUM)
**File**: `src/bot/handlers/login.py:165-173`
- `step_code`: extracts digits with `join(ch for ch in raw if ch.isdigit())`
- If user sends "abc" → `code = ""` → "Не вижу цифр" error (line 172)
- **But**: No rate limiting — user can spam non-digits indefinitely
- State never advances, no lockout

### 3.2 No FSM state timeout (HIGH)
**File**: `src/bot/handlers/login.py`, `src/bot/states.py`
- `LoginStates.phone`, `LoginStates.code`, `LoginStates.password_2fa` — **no timeout**
- User can start `/login`, enter phone, then abandon — FSM stuck forever
- Blocks new `/login` (line 84-88 checks existing client but not pending FSM)
- **Reproduction**: `/login` → enter phone → wait 1 hour → `/login` again → "Аккаунт уже подключён" but FSM still in code state

### 3.3 2FA password not cleared from memory on exception (MEDIUM)
**File**: `src/bot/handlers/login.py:213-249`
- `password = None; del password` in `finally` (lines 248-249)
- But if exception occurs BEFORE `finally` (e.g., `message.delete()` fails line 241-243), password stays in local variable
- Python GC will eventually collect but not deterministic

### 3.4 Concurrent login attempts (MEDIUM)
**File**: `src/bot/handlers/login.py:74-98`, `src/userbot/manager.py:152-163`
- Two `/login` commands quickly: second one sees `userbot_manager.get_client(tg_id) is not None` (line 84)
- But if first login in progress (pending), `get_client` returns None, second login creates NEW pending client
- First pending client leaked

---

## 4. Transaction Management

### 4.1 `get_session()` commits on yield, rolls back on exception (MEDIUM)
**File**: `src/db/session.py:293-301`
```python
async with SessionLocal() as session:
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
```
- **Problem**: If caller does `session.flush()` then exception, rollback happens
- But if caller does multiple `flush()` calls, partial data may be visible to other transactions (SQLite WAL)
- No explicit transaction demarcation — entire function = one transaction

### 4.2 Nested `get_session()` calls create separate transactions (HIGH)
**File**: `src/userbot/mirror.py:188-250`, `109-152`, `src/userbot/auto_reply.py:137-215`, `343-511`
- `mirror.py` `on_message`: Session 1 (lines 188-250) for message upsert
- Then `_process_incoming_bg` creates NEW session (line 109)
- `auto_reply.py` `_build_reply_text`: session at line 137, ANOTHER session at line 258-266 (load_chat), ANOTHER at line 493-502
- **Each session = separate transaction** — no atomicity across operations
- If message upserted but inbox processing fails → message in DB but no inbox action

### 4.3 Missing savepoints for nested operations (MEDIUM)
- No use of `session.begin_nested()` / savepoints
- Complex operations (e.g., auto-reply: decide → generate → send → log) span multiple sessions
- Partial failure = inconsistent state

### 4.4 `init_db()` runs migrations OUTSIDE transaction (CRITICAL for bootstrap)
**File**: `src/db/session.py:184-291`
- `init_db()` uses `engine.begin()` (line 207) — auto-commits each statement
- If `Base.metadata.create_all` (line 246) partially fails → schema in inconsistent state
- Alembic stamp (lines 258-275) separate from create_all
- **No rollback possible** for bootstrap

---

## 5. Offline/Reconnection Logic

### 5.1 Telethon auto-reconnect not configured (CRITICAL)
**File**: `src/userbot/manager.py:52-57`, `155-160`
- `TelegramClient` created with NO `connection_retries`, `retry_delay`, `auto_reconnect` settings
- Default Telethon behavior: retry 5 times with exponential backoff, then raise
- **No custom reconnection logic** — if connection drops, client stays disconnected
- `restore_all()` only runs at startup (line 255 in main.py)

### 5.2 No periodic health check for userbot clients (HIGH)
- No background task to verify `client.is_connected()` and `client.is_user_authorized()`
- If Telegram pushes update (session revoked, password changed), client becomes zombie
- `auto_reply.py` `_check_and_track_offline` (line 74-119) calls `client.get_me()` but only on incoming message

### 5.3 Auto-reply on connection loss (MEDIUM)
**File**: `src/userbot/auto_reply.py:343-515`
- Handler registered once at `attach_auto_reply` (line 542-552)
- If client disconnects, handler never fires — no auto-reply
- No fallback to send notification to owner via bot when userbot down

### 5.4 FloodWait handling only in `restore_all` (MEDIUM)
**File**: `src/userbot/manager.py:89-117`
- FloodWait caught and retried ONCE during restore
- No FloodWait handling in message handlers (mirror, auto-reply)
- If FloodWait occurs during operation → exception logged, operation lost

---

## 6. Schema Migration Risks

### 6.1 Alembic upgrade with 120s timeout but no retry (CRITICAL)
**File**: `src/main.py:482-516`
- `alembic.command.upgrade` runs in ThreadPoolExecutor with 120s timeout
- On timeout: `SystemExit(1)` — **hard crash, no recovery**
- On Railway: volume may be slow, 120s may be insufficient for large migrations
- **No**: retry logic, migration status check, or "repair" mode

### 6.2 `init_db()` bootstrap can mask missing migrations (HIGH)
**File**: `src/db/session.py:223-279`
- If ORM tables exist but `alembic_version` missing → stamps head revision (lines 258-279)
- **Danger**: Developer adds column to model, forgets `alembic revision --autogenerate`
- `create_all` creates column silently, alembic stamped → migration gap hidden
- Next deploy: alembic sees column exists, doesn't generate migration → drift

### 6.3 FTS5 tables created in `init_db()` not tracked by Alembic (MEDIUM)
**File**: `src/db/session.py:48-124`, `281-287`
- `_FTS_SETUP`, `_SESSION_FTS_SETUP`, `_MEMORY_FTS_SETUP` raw SQL
- Not in Alembic → not versioned, not reviewed
- If FTS schema changes → manual sync required

### 6.4 Data migration `_migrate_related_memory_to_links` runs every startup (LOW)
**File**: `src/db/session.py:127-182`, `290`
- Runs on every `init_db()` call
- Has broad exception handling that swallows errors (lines 160-181)
- If migration partially applied → re-runs and may hit "duplicate" errors

---

## 7. Vector Store Health (Qdrant)

### 7.1 Embedded Qdrant lock — single process only (CRITICAL)
**File**: `src/core/actions/vector_store.py:36-41`, `553-562`
- `QdrantClient(path=...)` uses file-based storage with **internal lock**
- **Cannot run multiple processes** (e.g., bot + worker + CLI) simultaneously
- `get_vector_store()` singleton (lines 557-562) but no cross-process coordination
- **Reproduction**: Run `python -m src.main` twice → second fails with lock error

### 7.2 Corruption recovery DESTROYS all data (HIGH)
**File**: `src/core/actions/vector_store.py:476-542`
- `check_health_and_recover()`: on persistent corruption → `shutil.rmtree(qdrant_dir)` (line 522)
- **ALL vector data lost** — no backup, no partial recovery
- Only notifies via notification_queue (may not deliver if bot down)

### 7.3 Dimension mismatch handling — silent skip (HIGH)
**File**: `src/core/actions/vector_store.py:276-284`, `154-162`, `368-374`
- `upsert()` and `search()` silently return/skip if `_reindex_required`
- **No alert to user** — semantic search silently stops working
- Only logs warning (easy to miss in production)
- `reindex_collection()` must be called MANUALLY via `/index` command

### 7.4 No health check scheduling (MEDIUM)
- `check_health_and_recover()` only called at startup (main.py:252)
- No periodic health check → corruption undetected until search fails

### 7.5 Qdrant client not thread-safe for concurrent access (MEDIUM)
- `asyncio.Lock` protects upserts but `QdrantClient` itself not async-safe
- `asyncio.to_thread` used but multiple threads can call client concurrently
- Qdrant's embedded mode uses SQLite internally — potential contention

---

## 8. LLM Provider Failover

### 8.1 Circuit breaker state not persisted (HIGH)
**File**: `src/llm/router.py:19-46`, `209-216`, `298-301`
- `_CIRCUIT_BREAKERS` in-memory dict (line 46 in provider_manager)
- On restart: all circuit breakers reset → hammer previously failing keys
- No persistence to DB — cooldown only in memory

### 8.2 Fallback order static, not adaptive enough (MEDIUM)
**File**: `src/llm/router.py:673-700`
- `ProviderFallback.chat()` sorts by `_score_provider` (success rate + latency)
- But sort happens **per request** — no caching of sort order
- Under high load: repeated sorting overhead
- Fallback tries ALL providers sequentially — no parallel attempt with timeout

### 8.3 Embedding fallback dimension check but no auto-reindex (HIGH)
**File**: `src/llm/router.py:738-771`, `773-805`
- `embed()` / `embed_batch()` check dimension match (lines 753-758, 787-792)
- Mismatch → `ValueError` → try next provider
- **But**: If fallback provider has DIFFERENT dimension → vectors in Qdrant now mixed dimensions
- Next search with primary provider's dimension fails silently (see 7.3)

### 8.4 No "all keys exhausted" alerting (MEDIUM)
- `ExhaustedError` raised but caught upstream?
- No notification to owner when all LLM keys failing
- Silent degradation to `ExhaustedProvider` (lines 831-867)

### 8.5 Key rotation cooldown not respected on restart (MEDIUM)
- `KEY_COOLDOWN_SECONDS = 60` (line 28 in provider_manager)
- On restart: `_restore_cooldowns` (line 41) reads from DB
- But if DB unavailable or migration → cooldowns lost

---

## 9. Message Deduplication

### 9.1 `mirror.py` — NO deduplication at all (CRITICAL)
**File**: `src/userbot/mirror.py:155-293`
- `on_message` handler fires for EVERY `NewMessage` event
- Telegram sends `NewMessage` for: incoming, outgoing, edits, deletes (sometimes)
- **No check** for duplicate `message_id` per chat
- If Telethon reconnects and replays updates → duplicate DB rows
- `upsert_message` (line 234) uses `message_id` + `peer_id` + `user_id` — should be unique but no verification

### 9.2 `reply_dedup.py` only prevents bot SENDING duplicates, not receiving (MEDIUM)
**File**: `src/bot/reply_dedup.py`
- `ReplyDedup.is_duplicate(chat_id, text)` — checks outgoing text hash
- **Does not prevent** processing duplicate incoming messages
- `mirror.py` and `auto_reply.py` both handle `NewMessage` independently

### 9.3 Update handlers can fire multiple times for same message (HIGH)
**File**: `src/userbot/mirror.py:293`, `src/userbot/auto_reply.py:552`
- Both attach to `events.NewMessage()` — **same event triggers both handlers**
- `mirror.py` processes ALL messages (in/out)
- `auto_reply.py` filters `incoming=True` (line 552) but still fires
- No coordination — both open separate DB sessions for same message

### 9.4 Message edits not handled (MEDIUM)
- Telethon sends `MessageEdited` event
- No handler for `events.MessageEdited` in mirror or auto_reply
- Edited message → new DB row with same `message_id` → `upsert_message` updates (good)
- But inbox/auto-reply logic not re-triggered

---

## 10. Timezone Handling

### 10.1 `ZoneInfo` uses system tzdata — may be stale (MEDIUM)
**File**: `src/core/infra/timeutil.py:8, 37-43`
- `ZoneInfo` relies on system `/usr/share/zoneinfo` or `tzdata` package
- In containers: `tzdata` may be outdated (DST rules change)
- No mechanism to update tzdata or verify version
- **Risk**: Wrong local time calculations during DST transitions

### 10.2 Naive datetime handling inconsistent (HIGH)
**File**: `src/core/infra/timeutil.py:58-61`, `73-83`
- `utc_to_local()`: if `dt.tzinfo is None` → assumes UTC (line 59-60)
- `ensure_utc()`: same assumption (line 81-82)
- **But**: SQLite with `DateTime(timezone=True)` returns **aware** datetime for new rows
- Old rows (pre-migration) may be naive → double-conversion bug
- **Reproduction**: Query old message (naive) → `utc_to_local` treats as UTC → wrong local time

### 10.3 Scheduler uses local time but stores UTC (MEDIUM)
**File**: `src/core/infra/timeutil.py:54-55`, `86-94`
- `now_in_tz()` returns `datetime.now(parse_tz(tz_name))` — **local aware datetime**
- Digest/scheduler tasks likely compare with UTC-stored times
- DST transition: 2:30 AM may not exist (spring) or exist twice (fall)
- No handling for ambiguous/non-existent times

### 10.4 `get_user_tz` reads from settings on every call (LOW)
**File**: `src/core/infra/timeutil.py:31-34`
- No caching — hits DB via `user.settings` relationship each call
- Called frequently in auto-reply, scheduling, digests
- **Fix**: Cache in request context or use `selectinload`

---

## Summary by Severity

| Severity | Count | Key Issues |
|----------|-------|------------|
| **CRITICAL** | 5 | Race in restore_all, Alembic timeout crash, Qdrant lock, no Telethon reconnect, no message dedup |
| **HIGH** | 10 | Nested transactions, pending login leak, FSM timeout, dimension mismatch, corruption recovery destroys data, circuit breaker not persisted, etc. |
| **MEDIUM** | 12 | Cache thread-safety, shutdown races, 2FA memory, auto-reply on disconnect, FTS5 not versioned, etc. |
| **LOW** | 5 | Weakref usage, Qdrant thread safety, tzdata staleness, data migration runs every startup, scheduler DST |

## Recommended Priority Fixes

1. **P0 (Blocker)**: Qdrant embedded lock — migrate to client-server mode or add file lock coordination
2. **P0 (Blocker)**: Alembic timeout → add retry + migration status endpoint
3. **P0 (Blocker)**: Telethon auto-reconnect + periodic health check
4. **P1**: Message deduplication in mirror.py (use message_id + peer_id unique constraint)
5. **P1**: FSM state timeouts for login flow
6. **P1**: Nested transaction audit — use single session per logical operation
7. **P2**: Circuit breaker persistence to DB
8. **P2**: Pending login cleanup task
9. **P3**: ReplyDedup thread safety (use asyncio.Lock)
10. **P3**: Timezone handling audit + DST test cases