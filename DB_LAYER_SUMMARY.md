# TelegramHelper Database Layer - Comprehensive Summary

## Overview
SQLite + SQLAlchemy (async) with Alembic migrations. FTS5 virtual tables for full-text search on messages, memories, and agent session messages.

---

## 1. Database Models (`src/db/models/`)

### Core / Base (`_base.py`)
- **User** - Root entity (PK: `id`, unique `telegram_id`), relationships to all child entities

### Authentication & Sessions (`_auth.py`)
| Model | Table | Key Columns | Relationships |
|-------|-------|-------------|---------------|
| UserSettings | `user_settings` | `user_id` (FK→users, PK), 80+ settings columns | 1:1 ← User |
| TelegramSession | `telegram_sessions` | `user_id` (FK→users, PK), `api_id`, `api_hash_enc`, `session_string_enc` | 1:1 ← User |
| ApiKey | `api_keys` | `id` (PK), `user_id` (FK), `provider`, `key_enc` | M:1 ← User, unique(user_id, provider) |
| LlmKeySlot | `llm_key_slots` | `id` (PK), `user_id` (FK), `provider`, `purpose`, `key_enc`, `category`, `priority` | M:1 ← User, 1:M → LlmKeySlotModel |
| LlmKeySlotModel | `llm_key_slot_models` | `id` (PK), `slot_id` (FK), `model_name`, `enabled` | M:1 ← LlmKeySlot |
| PendingQuestion | `pending_questions` | `id` (PK, BigInteger), `owner_id`, `question` | No FKs |

### Contacts & Conversations (`_contacts.py`)
| Model | Table | Key Columns | Relationships |
|-------|-------|-------------|---------------|
| Contact | `contacts` | `id` (PK), `user_id` (FK), `peer_id` (BigInteger), `peer_kind`, `is_archived`, `is_news_source`, `display_name`, `folder_names`, `archetype` | M:1 ← User, unique(user_id, peer_id) |
| ContactProfile | `contact_profiles` | `id` (PK), `user_id` (FK), `contact_id` (BigInteger), `closeness`, `sensitivity`, `current_status`, `memory_digest`, `custom_instructions` | M:1 ← User |
| ConversationState | `conversation_states` | `id` (PK), `user_id` (FK), `peer_id`, `status`, `unread_count`, `last_incoming_at`, `radar_snoozed_until` | M:1 ← User, unique(user_id, peer_id) |
| AllowedContact | `allowed_contacts` | `telegram_id` (PK, BigInteger), `approved_at`, `label` | Standalone (pairing allowlist) |

### Messaging (`_messaging.py`)
| Model | Table | Key Columns | Relationships |
|-------|-------|-------------|---------------|
| Message | `messages` | `id` (PK), `user_id` (FK), `peer_id`, `message_id`, `sender_id`, `is_outgoing`, `date`, `kind`, `text`, `transcript`, `indexed_in_vector` | M:1 ← User, indexes on (user_id, peer_id, date) |
| Commitment | `commitments` | `id` (PK), `user_id` (FK), `peer_id`, `direction`, `text`, `deadline_at`, `status` | M:1 ← User |
| AutoReplyLog | `auto_reply_logs` | `id` (PK), `user_id` (FK), `peer_id`, `incoming_text`, `reply_text` | M:1 ← User |
| IndexJob | `index_jobs` | `id` (PK), `user_id` (FK), `peer_id`, `last_indexed_message_id` | M:1 ← User, unique(user_id, peer_id) |
| TranscriptionCache | `transcription_cache` | `file_id` (PK, String), `text`, `duration_seconds` | Standalone (global cache) |
| PendingAction | `pending_actions` | `id` (PK), `user_id` (FK), `kind`, `payload`, `expires_at`, `hmac_signature` | M:1 ← User |
| NewsTopic | `news_topics` | `id` (PK), `user_id` (FK), `topic`, `hours`, `enabled` | M:1 ← User |
| Notification | `notifications` | `id` (PK, auto), `topic`, `priority`, `category`, `text`, `metadata_json`, `batch_id` | Standalone (queue) |
| Folder | `folders` | `id` (PK), `user_id` (FK), `telegram_folder_id`, `title`, `emoji` | M:1 ← User |
| ConversationSummary | `conversation_summaries` | `id` (PK, BigInteger), `user_id` (FK), `last_peer_id`, `summary_text`, `turn_count` | M:1 ← User |
| ScheduledMessage | `scheduled_messages` | `id` (PK, BigInteger), `user_id` (FK), `contact_name`, `text`, `send_at`, `status` | M:1 ← User |

### Memory System (`_memory.py`)
| Model | Table | Key Columns | Relationships |
|-------|-------|-------------|---------------|
| Memory | `memories` | `id` (PK), `user_id` (FK), `contact_id`, `fact`, `sentiment`, `source`, `confidence`, `is_active`, `memory_type`, `importance`, `decay_rate`, `memory_tier`, `temporal_layer`, `related_memory_id`, `relation_type` | M:1 ← User, self-referential FK (related_memory_id) |
| MemoryLink | `memory_links` | `id` (PK), `user_id` (FK), `source_id` (FK→memories), `target_id` (FK→memories), `weight`, `relation_type` | M:1 ← User, M:M via source/target |
| MemoryCluster | `memory_clusters` | `id` (PK), `user_id` (FK), `topic`, `summary`, `fact_count` | M:1 ← User |
| MemoryClusterMember | `memory_cluster_members` | `id` (PK), `user_id` (FK), `memory_id` (FK), `cluster_id` (FK), `relevance_score` | M:1 ← User, M:M |
| MemoryCandidate | `memory_candidates` | `id` (PK), `user_id` (FK), `contact_id`, `fact`, `sentiment`, `memory_type`, `importance` | M:1 ← User |

### Cache (`_cache.py`)
| Model | Table | Key Columns |
|-------|-------|-------------|
| SmartCacheEntry | `smart_cache` | `cache_key` (PK, String), `cache_value`, `source`, `owner_id`, `accessed_at`, `importance_score`, `graduated`, `content_hash` |

### Avito Monitoring (`_avito.py`)
| Model | Table | Key Columns | Relationships |
|-------|-------|-------------|---------------|
| AvitoListing | `avito_listings` | `id` (PK), `user_id` (FK), `avito_id`, `search_query`, `title`, `price`, `url`, `deal_score`, `is_suspicious`, `is_active` | M:1 ← User, 1:M → AvitoPriceHistory, 1:M → AvitoWatch |
| AvitoPriceHistory | `avito_price_history` | `id` (PK), `listing_id` (FK), `price`, `recorded_at` | M:1 ← AvitoListing |
| AvitoWatch | `avito_watches` | `id` (PK), `user_id` (FK), `listing_id` (FK), `price_threshold`, `is_active` | M:1 ← User, M:1 ← AvitoListing |

### Channel Monitoring (`_monitor.py`)
| Model | Table | Key Columns | Relationships |
|-------|-------|-------------|---------------|
| MonitoredSource | `monitored_sources` | `id` (PK), `user_id` (FK), `entity_id`, `entity_type`, `title`, `username`, `is_active`, `last_fetched_at`, `settings` (JSON) | M:1 ← User, 1:M → MonitorRule, 1:M → MonitoredMessage |
| MonitorRule | `monitor_rules` | `id` (PK), `user_id` (FK), `source_id` (FK), `conditions` (JSON), `actions` (JSON), `is_active` | M:1 ← User, M:1 ← MonitoredSource, 1:M → MonitoredAlert |
| MonitoredMessage | `monitored_messages` | `id` (PK), `source_id` (FK), `message_id`, `date`, `sender_id`, `text`, `media_type`, `entities` (JSON) | M:1 ← MonitoredSource, 1:M → MonitoredAlert |
| MonitoredAlert | `monitor_alerts` | `id` (PK), `user_id` (FK), `rule_id` (FK), `message_id` (FK), `status`, `summary` | M:1 ← User, M:1 ← MonitorRule, M:1 ← MonitoredMessage |

### Encryption (`_encryption.py`)
| Model | Table | Key Columns |
|-------|-------|-------------|
| EncryptionKey | `encryption_keys` | `key_id` (PK), `encrypted_dek`, `is_active`, `created_at`, `rotated_at` |

### Learning / Adaptation (`_learning.py`)
| Model | Table | Key Columns |
|-------|-------|-------------|
| AgentCache | `agent_cache` | `cache_key` (PK), `result_json`, `ttl_seconds` |
| SelfProfile | `self_profile` | `id` (PK), `user_id` (FK, unique), `preferences`, `goals`, `current_projects`, `decision_style`, etc. |
| InstructionProfile | `instruction_profiles` | `id` (PK), `user_id` (FK), `rules_json`, `updated_at` |
| InstructionCandidate | `instruction_candidates` | `id` (PK), `user_id` (FK), `rule`, `category`, `is_safe`, `llm_reviewed` |
| InstructionEvent | `instruction_events` | `id` (PK), `user_id` (FK), `raw_text`, `detected_rule`, `action` |
| AdaptivePersona | `adaptive_personas` | `id` (PK), `user_id` (FK, unique), 30+ style/format/mode fields |
| SoulSnapshot | `soul_snapshots` | `id` (PK), `user_id` (FK), `version`, `snapshot_type`, `blocks_json` (JSON), `is_active` |
| Trajectory | `trajectories` | `id` (PK), `user_id` (FK), `request_text`, `route_mode`, `intent_json`, `actions_json`, `used_skills_json`, `memory_ids_json`, `success`, `latency_ms` |
| Skill | `skills` | `id` (PK), `user_id` (FK), `name`, `description`, `trigger_patterns_json`, `body`, `enabled`, `review_status`, `version`, `edit_history_json`, `rejected_edits_json`, `validation_score`, `best_body`, `last_compressed_at` |
| SkillUsage | `skill_usages` | `id` (PK), `user_id` (FK), `skill_id` (FK), `trajectory_id` (FK), `success` |

### Agent Sessions (`_session.py`)
| Model | Table | Key Columns | Relationships |
|-------|-------|-------------|---------------|
| AgentSession | `agent_sessions` | `id` (PK), `user_id` (FK), `session_type`, `started_at`, `ended_at`, `summary`, `turn_count` | M:1 ← User, 1:M → AgentSessionMessage |
| AgentSessionMessage | `agent_session_messages` | `id` (PK), `session_id` (FK), `role`, `content` | M:1 ← AgentSession |

---

## 2. Key Relationships & Foreign Keys

### User-Centric (all entities link to User)
```
User (1) ────── (1) UserSettings
User (1) ────── (1) TelegramSession
User (1) ────── (M) ApiKey
User (1) ────── (M) LlmKeySlot ────── (M) LlmKeySlotModel
User (1) ────── (M) Contact ────── (1) ContactProfile
User (1) ────── (M) ConversationState
User (1) ────── (M) Message
User (1) ────── (M) Commitment
User (1) ────── (M) AutoReplyLog
User (1) ────── (M) IndexJob
User (1) ────── (M) PendingAction
User (1) ────── (M) NewsTopic
User (1) ────── (M) Folder
User (1) ────── (M) ConversationSummary
User (1) ────── (M) ScheduledMessage
User (1) ────── (M) Memory ────── (M) MemoryLink (self-ref)
User (1) ────── (M) MemoryCluster ────── (M) MemoryClusterMember
User (1) ────── (M) MemoryCandidate
User (1) ────── (M) SmartCacheEntry (owner_id)
User (1) ────── (M) AvitoListing ────── (M) AvitoPriceHistory
User (1) ────── (M) AvitoWatch
User (1) ────── (M) MonitoredSource ────── (M) MonitorRule ────── (M) MonitoredAlert
User (1) ────── (M) EncryptionKey
User (1) ────── (1) SelfProfile
User (1) ────── (M) InstructionProfile
User (1) ────── (M) InstructionCandidate
User (1) ────── (M) InstructionEvent
User (1) ────── (1) AdaptivePersona
User (1) ────── (M) SoulSnapshot
User (1) ────── (M) Trajectory
User (1) ────── (M) Skill ────── (M) SkillUsage
User (1) ────── (M) AgentSession ────── (M) AgentSessionMessage
```

### Notable Composite/Unique Constraints
- `contacts`: unique(user_id, peer_id)
- `api_keys`: unique(user_id, provider)
- `conversation_states`: unique(user_id, peer_id)
- `index_jobs`: unique(user_id, peer_id)
- `allowed_contacts`: PK on telegram_id (global, not per-user)
- `monitored_sources`: unique(user_id, entity_id)
- `monitored_messages`: unique(source_id, message_id)
- `monitor_alerts`: unique(rule_id, message_id) — added in latest migration
- `avito_listings`: unique(user_id, avito_id)
- `avito_watches`: unique(user_id, listing_id)

---

## 3. Migration Status

### Current State (Alembic)
- **Current revision**: `m1n2o3p4q5r6` (add_llm_key_slot_models)
- **Heads**: `9ab21154bc81` (merge_monitor_tables_into_main), `x9y8z7w6v5u4` (add_monitor_alert_unique)
- **Total migrations**: ~35 versions including merge points
- **Two heads detected** - merge migration `9ab21154bc81` exists but not applied

### Migration History (Key Milestones)
| Rev ID | Description |
|--------|-------------|
| `0ea3133e3615` | Initial schema (create_all from ORM) |
| `318404aba419` | Add scheduling persist columns |
| `6c81883d69f4` | Add watched_peers to user_settings |
| `fe658c1e6a41` | Add personality fields (branchpoint) |
| `a7c3d9e1f0b2` | Add smart_cache table |
| `c6c5965acc9d` | Add last_compressed_at to skills (branchpoint) |
| `e1d7c0f3ac9c` | Merge memory refactor + LLM refactor |
| `fb56dd543d87` | Merge scheduled_messages |
| `z9y8x7w6v5u4` | Add FTS5 virtual tables to Alembic |
| `m1n2o3p4q5r6` | Add llm_key_slot_models (current) |
| `m5n6o7p8q9r0` | Add monitor tables (parallel branch) |
| `x9y8z7w6v5u4` | Add unique constraint on monitor_alerts |
| `9ab21154bc81` | Merge monitor tables into main |

### ⚠️ Migration Concern: Two Heads
The migration graph has **two heads** that need merging:
1. `m1n2o3p4q5r6` → `9ab21154bc81` (merge point)
2. `m5n6o7p8q9r0` → `x9y8z7w6v5u4` (monitor alert unique)

**Action needed**: Apply merge `9ab21154bc81` or create new merge migration.

---

## 4. Database Configuration (`src/db/session.py`)

### Engine & Session
```python
engine = create_async_engine(
    settings.database_url,  # sqlite+aiosqlite:///data/app.db
    future=True,
    connect_args={"check_same_thread": False}
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
```

### SQLite PRAGMAs (performance/reliability)
| PRAGMA | Value | Purpose |
|--------|-------|---------|
| journal_mode | WAL | Write-Ahead Logging for concurrency |
| synchronous | NORMAL | Balance safety/performance |
| cache_size | -64000 | 64MB page cache (negative = KB) |
| mmap_size | 134217728 | 128MB memory-mapped I/O |
| busy_timeout | 30000 | 30s lock wait |
| foreign_keys | ON | Enforce FK constraints |
| temp_store | MEMORY | Temp tables in RAM |
| wal_autocheckpoint | 1000 | Checkpoint every 1000 pages |

### Schema Management Policy
- **Alembic is canonical** — all schema changes via migrations
- `Base.metadata.create_all` only as **bootstrap fallback** when `alembic_version` table missing
- FTS5 virtual tables managed by **raw SQL** in `init_db()`, excluded from Alembic via `include_object()`

### FTS5 Virtual Tables (created in `init_db()`)
| Virtual Table | Source Table | Content Columns | Triggers |
|---------------|--------------|-----------------|----------|
| `messages_fts` | `messages` | text, transcript, extracted_text, sender_name | INSERT/DELETE/UPDATE sync triggers |
| `agent_session_messages_fts` | `agent_session_messages` | content, role, session_id (UNINDEXED) | None (external-content) |
| `memories_fts` | `memories` | fact, sentiment, cluster_topic | INSERT/DELETE/UPDATE sync triggers |

All use `tokenize='unicode61 remove_diacritics 2 categories ''L* N* Co'''`

### Data Migration (in `init_db()`)
- `related_memory_id` → `memory_links` one-time migration (idempotent)

---

## 5. Repository / DAO Layer (`src/db/repos/`)

### Pattern
- **Thin re-export** in `repo.py` → domain modules (`contact_repo.py`, `memory_repo.py`, etc.)
- Each repo: async functions taking `AsyncSession` + user entity
- Raw SQL for complex queries (FTS, graph traversals)
- ORM for simple CRUD

### Key Repositories

| Repo File | Exports | Notable Patterns |
|-----------|---------|------------------|
| `session_repo.py` | 9 functions | User CRUD, TelegramSession encryption, session messages |
| `key_repo.py` | 8 functions | ApiKey + LlmKeySlot dual-write, encryption/decryption |
| `contact_repo.py` | 15 functions | Contact upsert, watched peers, folder sync |
| `message_repo.py` | 13 functions | Message caching, conversation state, FTS search |
| `memory_repo.py` | ~50 functions | **Largest repo** - graph queries, FTS with morphological expansion, clustering, decay |
| `commitment_repo.py` | 14 functions | Commitments, pending actions with HMAC |
| `skill_repo.py` | 7 functions | Skills, trajectories, usage tracking |
| `cache_repo.py` | 2 functions | AgentCache get/upsert |
| `news_repo.py` | 4 functions | NewsTopic CRUD |
| `scheduled_repo.py` | 5 functions | ScheduledMessage queue |

### Notable Query Patterns in `memory_repo.py`
- **FTS5 search** with Russian morphological expansion (hand-coded dict)
- **Graph traversal** via `memory_links` (BFS for related memories)
- **Temporal layering** queries (`recent` ≤7d, `medium` 8-30d, `longterm` >30d)
- **Cluster management** with relevance scoring
- **Cross-chat search** via FTS + contact filtering
- **Decay/importance** scoring for memory retrieval

---

## 6. Notable Patterns & Concerns

### ✅ Strengths
1. **Clear separation** of models by domain (auth, contacts, messaging, memory, monitoring, learning)
2. **Alembic-first** schema management with proper fallback
3. **FTS5 integration** for full-text search with triggers for sync
4. **Encryption at rest** for sensitive fields (API keys, session strings, DEKs)
5. **Rich indexing** strategy (composite indexes on query patterns)
6. **JSON columns** for flexible settings (monitor rules, user settings overrides)
7. **Soft deletes** via `is_active` flags instead of hard deletes

### ⚠️ Concerns / Risks

| Issue | Location | Impact |
|-------|----------|--------|
| **Two alembic heads** | Migration graph | Cannot cleanly upgrade; merge needed |
| **Missing FK on `Memory.related_memory_id`** | `_memory.py:105` | Self-referential FK to `memories.id` but no explicit FK defined (uses BigInteger) |
| **`ContactProfile.contact_id` type mismatch** | `_contacts.py:69` | Uses `BigInteger` but references `Contact.id` (Integer) — potential overflow |
| **`ConversationSummary.user_id` type** | `_messaging.py:235` | `BigInteger` vs `User.id` (Integer) |
| **`ScheduledMessage.user_id` type** | `_messaging.py:256` | `BigInteger` vs `User.id` (Integer) |
| **`PendingQuestion.owner_id`** | `_auth.py:221` | `BigInteger` not linked to User FK |
| **`AllowedContact` standalone** | `_contacts.py:162` | Global table, not per-user — may be intentional for pairing |
| **FTS5 not in Alembic** | `env.py:34-52` | Excluded from autogenerate — manual sync risk |
| **Large `memory_repo.py` (1875 lines)** | `memory_repo.py` | God object; consider splitting |
| **No connection pooling config** | `session.py` | Default pool; may need tuning for production |

### 🔍 Type Consistency Issues
Multiple models use `BigInteger` for columns referencing `User.id` (Integer):
- `ConversationSummary.user_id` (BigInteger)
- `ScheduledMessage.user_id` (BigInteger)
- `ContactProfile.contact_id` (BigInteger, should reference Contact.id)
- `PendingQuestion.owner_id` (BigInteger, no FK)

In SQLite this works (type affinity), but could cause issues if migrating to PostgreSQL.

### 📦 Repository Organization
The `repo.py` re-exports 100+ functions from 11 modules. Consider:
- Grouping by domain in sub-packages
- Adding `__init__.py` with curated exports per domain
- Type stubs for better IDE support

---

## 7. Recommendations

### Immediate (High Priority)
1. **Merge alembic heads** — create migration merging `x9y8z7w6v5u4` into main branch
2. **Fix FK type mismatches** — align `BigInteger` → `Integer` where referencing User.id
3. **Add explicit FK** for `Memory.related_memory_id` → `Memory.id`

### Medium Priority
4. **Split `memory_repo.py`** into `memory_queries.py`, `memory_graph.py`, `memory_fts.py`
5. **Add alembic autogenerate test** in CI to catch model/migration drift
6. **Document FTS5 sync triggers** — ensure they fire correctly on bulk operations

### Low Priority / Tech Debt
7. **Standardize timestamp columns** — mix of `DateTime(timezone=True)` and plain `DateTime`
8. **Consider partitioning** for `messages` and `memories` tables (by date/user)
9. **Add database health checks** — `PRAGMA foreign_key_check`, `PRAGMA integrity_check`

---

## 8. File Inventory

### Models (13 files)
```
src/db/models/
├── __init__.py          # Re-exports all 50+ models
├── _base.py             # Base, User
├── _auth.py             # UserSettings, TelegramSession, ApiKey, LlmKeySlot*, PendingQuestion
├── _contacts.py         # Contact, ContactProfile, ConversationState, AllowedContact
├── _messaging.py        # Message, Commitment, AutoReplyLog, IndexJob, TranscriptionCache, PendingAction, NewsTopic, Notification, Folder, ConversationSummary, ScheduledMessage
├── _memory.py           # Memory, MemoryLink, MemoryCluster, MemoryClusterMember, MemoryCandidate
├── _cache.py            # SmartCacheEntry
├── _avito.py            # AvitoListing, AvitoPriceHistory, AvitoWatch
├── _monitor.py          # MonitoredSource, MonitorRule, MonitoredMessage, MonitoredAlert
├── _encryption.py       # EncryptionKey
├── _learning.py         # AgentCache, SelfProfile, AdaptivePersona, SoulSnapshot, Trajectory, Skill, SkillUsage, InstructionProfile, InstructionCandidate, InstructionEvent
├── _session.py          # AgentSession, AgentSessionMessage
```

### Migrations (35+ files)
```
alembic/versions/
├── 0ea3133e3615_initial_schema.py
├── ... (31 incremental migrations)
├── m1n2o3p4q5r6_add_llm_key_slot_models.py  ← CURRENT
├── m5n6o7p8q9r0_add_monitor_tables.py        ← PARALLEL BRANCH
├── x9y8z7w6v5u4_add_monitor_alert_unique.py  ← HEAD 2
├── 9ab21154bc81_merge_monitor_tables_into_main.py  ← MERGE POINT
└── z9y8x7w6v5u4_add_fts5_virtual_tables.py
```

### Repositories (11 files)
```
src/db/repos/
├── __init__.py
├── repo.py                    # Master re-export
├── session_repo.py
├── key_repo.py
├── contact_repo.py
├── message_repo.py
├── memory_repo.py             # 1875 lines - largest
├── commitment_repo.py
├── skill_repo.py
├── cache_repo.py
├── news_repo.py
├── scheduled_repo.py
```

### Configuration
```
src/db/
├── __init__.py
├── session.py                 # Engine, SessionLocal, init_db(), FTS5 setup, PRAGMAs
└── repo.py                    # Thin re-export
alembic/
├── env.py                     # Async migration runner, FTS5 exclusion
├── script.py.mako
├── README
└── alembic.ini
```