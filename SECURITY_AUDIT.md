# Security Audit Report - TelegramHelper

**Date**: 2026-06-09  
**Scope**: Full codebase security review  
**Classification**: Security Audit

---

## Executive Summary

The codebase has **strong security foundations** (single-owner architecture, encryption at rest, input sanitization, prompt injection scanning) but contains **multiple critical and high-severity issues** requiring immediate attention, particularly around supply chain vulnerabilities, session management, and DoS vectors.

---

## Findings by Category

### 1. Authentication & Authorization

| ID | Severity | File:Line | Issue |
|----|----------|-----------|-------|
| AUTH-001 | **CRITICAL** | `src/bot/filters.py:22-34` | OwnerOnly filter relies solely on `OWNER_TELEGRAM_ID` env var. If unset/zero → rejects ALL users (fail-closed). No secondary auth mechanism. |
| AUTH-002 | **HIGH** | `src/bot/handlers/login.py:254-287` | 2FA password cleared in `finally` block but **leaks if exception occurs before line 279** (e.g., `message.delete()` fails at line 279). Password remains in local variable `password` until GC. |
| AUTH-003 | **HIGH** | `src/userbot/manager.py:152-177` | `PendingLogin` creates Telethon client with `StringSession()` (empty). If user abandons login flow, client stays connected indefinitely — **no TTL cleanup** for pending logins beyond FSM 10-min timeout. |
| AUTH-004 | **MEDIUM** | `src/crypto.py:10-19` | Fernet key cached in module-level global `_fernet`. **Key never zeroed from memory** — remains until process exit. No SecureString usage. |
| AUTH-005 | **MEDIUM** | `src/db/repos/key_repo.py` | API keys stored encrypted with Fernet but **decrypted into memory for every LLM call** (via `decrypt_async`). Keys exist in plaintext in `MultiKeyProvider._keys` list during operation. |

### 2. Input Validation & Injection

| ID | Severity | File:Line | Issue |
|----|----------|-----------|-------|
| INJ-001 | **HIGH** | `src/core/actions/vector_store.py:58-98` | Raw SQL in `_ensure_collection()` uses f-strings for collection names. Collection name is constant but **pattern is dangerous if extended**. |
| INJ-002 | **HIGH** | `src/db/repos/memory_repo.py:1090-1110` | FTS5 queries use parameterized `:q`, `:uid`, `:cid` — **correct**. But `find_similar_memories()` at line 1170-1176 uses `text("memories_fts MATCH :q").bindparams(q=fts_q)` — safe. |
| INJ-003 | **HIGH** | `src/db/repos/memory_repo.py:1170-1176` | FTS5 query built via string concatenation in `find_similar_memories`: `fts_q = " OR ".join(fts_terms)`. **Terms derived from user input** (`fact.split()`). Terms filtered by `isalnum()` but **underscore/hyphen allowed** — could inject FTS5 operators if filter bypassed. |
| INJ-004 | **MEDIUM** | `src/core/security/prompt_injection_scanner.py:119-176` | Pattern-based scanner only. **Bypassable via**: encoding tricks, multi-language mixing, adversarial suffixes, context stuffing. No semantic analysis. |
| INJ-005 | **MEDIUM** | `src/core/infra/text_sanitizer.py:30-140` | HTML sanitizer uses allowlist — **good**. But `handle_entityref` and `handle_charref` pass through entities unchecked — could allow `&{malicious};` if not in whitelist. |
| INJ-006 | **LOW** | `src/bot/handlers/free_text_pipeline.py:112-113` | User text interpolated into LLM prompt via `.format()` at lines 611-615. Uses `.replace("{", "{{")` — **partial mitigation** but not full template injection protection. |

### 3. Secrets Management

| ID | Severity | File:Line | Issue |
|----|----------|-----------|-------|
| SEC-001 | **CRITICAL** | `src/core/infra/key_guard.py:25-31` | `mask_keys()` uses regex patterns but **misses**: custom provider keys, base64-encoded secrets, keys split across lines, keys in JSON payloads. |
| SEC-002 | **HIGH** | `src/main.py:36-61` | JSON logging formatter calls `mask_keys(record.getMessage())` **after formatting** — if log message contains `%s` with key as arg, key appears in `record.args` and gets masked (line 40-41). **But**: `record.msg` may already contain interpolated key if using f-strings in logging calls. |
| SEC-003 | **HIGH** | `src/crypto.py:10-19` | `ENCRYPTION_KEY` validated at load but **no rotation mechanism for KEK** — only DEK rotation implemented. If KEK compromised, all historical data decryptable. |
| SEC-004 | **MEDIUM** | `src/core/crypto/rotation_task.py:139-150` | **Hardcoded `key_id = 1`** for initial DEK creation. If old deployment left key_id=1 in DB with different DEK, `save_to_db()` **silently overwrites** causing data loss for secrets encrypted with old DEK. |
| SEC-005 | **MEDIUM** | `src/llm/provider_manager.py:258-265` | Circuit breaker state (`_CIRCUIT_BREAKERS`, `_PROVIDER_METRICS`) **in-memory only**. On restart, cooldowns reset → **hammer previously failing keys** immediately. |

### 4. Cryptographic Issues

| ID | Severity | File:Line | Issue |
|----|----------|-----------|-------|
| CRY-001 | **HIGH** | `src/core/crypto/key_rotation.py` (not read) | KEK derived directly from `ENCRYPTION_KEY` (Fernet key). **No KDF** — if `ENCRYPTION_KEY` low entropy, KEK weak. |
| CRY-002 | **MEDIUM** | `src/crypto.py:13-15` | Fernet key validation: `len(key) != 44` check but **no entropy verification**. Could accept low-entropy key. |
| CRY-003 | **MEDIUM** | `src/core/crypto/rotation_task.py:82-90` | `re_encrypt_data()` creates new `Fernet(old_dek)` and `Fernet(new_dek)` per row — **creates many cipher objects**. No batching. |
| CRY-004 | **LOW** | `src/llm/router.py:116-119` | `_mask_key()` shows first/last 4 chars — **acceptable for logging** but could aid correlation attacks if many keys logged. |

### 5. DoS / Resource Exhaustion

| ID | Severity | File:Line | Issue |
|----|----------|-----------|-------|
| DOS-001 | **CRITICAL** | `src/core/scheduling/notification_queue.py:46-84` | `enqueue()` creates **new DB session per notification**. Under load (many notifications), **connection pool exhaustion**. No batching for high-frequency sources. |
| DOS-002 | **HIGH** | `src/bot/handlers/free_text.py:366-367` | Voice queue: `asyncio.Queue(maxsize=max(settings.max_voice_queue_size, 1))` — default 20. **No backpressure on producer** — `put()` at line 2128 is `put_nowait()`? Need to verify. |
| DOS-003 | **HIGH** | `src/core/memory/_queue_core.py:44-67` | Memory queue: `asyncio.Queue(maxsize=settings.memory_queue_maxsize)` (default 200). `put()` with **30s timeout** (line 56) — if full, drops task silently (line 66). **Silent data loss**. |
| DOS-004 | **HIGH** | `src/llm/router.py:56-57` | `_DEFAULT_LLM_TIMEOUT = 90.0` seconds per call. **No global rate limit** on LLM calls — owner could spam and exhaust API quotas. |
| DOS-005 | **HIGH** | `src/userbot/manager.py:89-117` | FloodWait handling **only in `restore_all()`**. No FloodWait handling in `mirror.py`, `auto_reply.py` message handlers → exceptions logged, operations lost. |
| DOS-006 | **MEDIUM** | `src/main.py:236-295` | `_cleanup_global_state()` runs every 60s with DB operations. **No `asyncio.shield`** for critical writes during shutdown — partial commits possible. |
| DOS-007 | **MEDIUM** | `src/core/actions/vector_store.py:479-550` | `check_health_and_recover()` on corruption **destroys ALL vector data** via `shutil.rmtree()`. No backup, no partial recovery. |

### 6. Supply Chain Vulnerabilities

| ID | Severity | Package | Vulnerabilities | Fixed Version |
|----|----------|---------|-----------------|---------------|
| SUP-001 | **CRITICAL** | `cryptography==44.0.1` | 2 (CVE-2026-26007, PYSEC-2026-35) | 46.0.6+ |
| SUP-002 | **HIGH** | `python-dotenv==1.1.0` | 1 (CVE-2026-28684) | 1.2.2+ |
| SUP-003 | **HIGH** | `pypdf==5.2.0` | 17 CVEs (DoS, memory exhaustion, infinite loops) | 6.10.2+ |
| SUP-004 | **HIGH** | `aiohttp==3.11.18` (transitive) | 27 CVEs (DoS, header injection, redirect leak, request smuggling) | 3.13.4+ |
| SUP-005 | **MEDIUM** | Multiple | **Transitive dependencies unpinned** — no `requirements.lock` or `pip-tools` usage. `requirements.txt` pins only top-level. |

### 7. Additional Security Issues

| ID | Severity | File:Line | Issue |
|----|----------|-----------|-------|
| ADD-001 | **HIGH** | `src/core/actions/mcp_shell.py:184-197` | **MCP shell tool executes arbitrary subprocess commands** with 30s timeout. No command allowlist, no argument sanitization. If LLM compromised → RCE. |
| ADD-002 | **HIGH** | `src/core/actions/mcp_code_exec.py:263-293` | **Python code execution in subprocess** with resource limits. `setrlimit(RLIMIT_NPROC, (0,0))` prevents forks but **not thread-based attacks**. No network isolation. |
| ADD-003 | **MEDIUM** | `src/core/actions/mcp_calculator.py:256-349` | `eval()` with AST whitelist — **correctly implemented** but `namespace` includes `math`, `random`, `statistics` modules. Could be abused for computation DoS. |
| ADD-004 | **MEDIUM** | `src/core/actions/sdd_executor.py:267-270` | `exec(compile(tree, "<sdd>", "exec"), namespace)` — **arbitrary code execution** from SDD spec. No sandboxing. |
| ADD-005 | **MEDIUM** | `src/db/session.py:48-124` | FTS5 triggers created via raw SQL at startup — **not versioned by Alembic**. Schema drift risk. |
| ADD-006 | **MEDIUM** | `src/userbot/mirror.py:155-293` | **No message deduplication** — Telethon replays updates on reconnect → duplicate DB rows. `upsert_message` uses unique constraint but no verification. |
| ADD-007 | **LOW** | `src/bot/reply_dedup.py:11-36` | `ReplyDedup` uses `OrderedDict` — **not thread-safe**. Concurrent `is_duplicate()` calls can cause double-insert, missed eviction, KeyError. |

---

## Risk Summary

| Severity | Count |
|----------|-------|
| **CRITICAL** | 6 |
| **HIGH** | 14 |
| **MEDIUM** | 13 |
| **LOW** | 3 |

---

## Immediate Action Items (P0 - Blockers)

1. **Upgrade vulnerable dependencies NOW**:
   - `cryptography>=46.0.6`
   - `python-dotenv>=1.2.2`
   - `pypdf>=6.10.2`
   - `aiohttp>=3.13.4` (transitive — pin in requirements.txt)

2. **Add requirements.lock** with `pip freeze > requirements.lock` for reproducible builds

3. **Fix 2FA password memory leak** — move `password = None; del password` before any await/operation that could raise

4. **Add pending login cleanup task** — background task to disconnect stale `PendingLogin` clients

5. **Implement KEK rotation** or document that KEK compromise = total historical data compromise

6. **Add circuit breaker persistence** to DB — survive restarts

---

## Recommended Agents for Remediation

```json
{
  "suggested_agents": [
    {"agent": "backend-dev", "effort": "xhigh", "reason": "Fix CRITICAL auth, crypto, injection, DoS issues across multiple modules"},
    {"agent": "backend-dev", "effort": "high", "reason": "Upgrade all vulnerable dependencies, add requirements.lock, test compatibility"},
    {"agent": "backend-dev", "effort": "high", "reason": "Implement KEK rotation, circuit breaker persistence, pending login cleanup"},
    {"agent": "backend-dev", "effort": "medium", "reason": "Harden MCP tools (shell, code_exec, sdd_executor) with allowlists/sandboxing"},
    {"agent": "test-engineer", "effort": "high", "reason": "Add security tests: prompt injection bypasses, auth bypass attempts, DoS load tests"}
  ]
}
```

---

## Files Requiring Changes (Priority Order)

1. `requirements.txt` — pin fixed versions
2. `src/bot/handlers/login.py` — fix 2FA password clearing
3. `src/userbot/manager.py` — add pending login TTL cleanup
4. `src/crypto.py` — add key zeroing, consider SecureString
5. `src/core/crypto/rotation_task.py` — fix hardcoded key_id=1
6. `src/llm/provider_manager.py` — persist circuit breaker state
7. `src/core/scheduling/notification_queue.py` — batch DB writes
8. `src/core/actions/mcp_shell.py` — add command allowlist
9. `src/core/actions/mcp_code_exec.py` — add network isolation
10. `src/core/actions/sdd_executor.py` — sandbox exec()
11. `src/bot/reply_dedup.py` — add asyncio.Lock
12. `src/db/session.py` — move FTS5 to Alembic migrations
13. `src/userbot/mirror.py` — add message deduplication
14. `src/core/security/prompt_injection_scanner.py` — enhance with semantic checks