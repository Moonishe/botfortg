# Session 14 Addendum (2026-06-19) — Deep Research Security Audit

## Контекст сессии

**Тип:** Deep Research (агрессивный режим) — read-only security audit
**Агентов задействовано:** 20 (5 explorer's + 5 D5 debuggers + 5 R5 reviewers + 5 Max Mode judges)
**Длительность:** Один заход с 4 параллельными волнами

## Ключевые находки

### CRITICAL (3 — все confirmed)

1. **Sandbox escape через `__traceback__`** в `mcp_code_exec.py`
   - AST blacklist не покрывает f_globals/tb_frame/__traceback__
   - Repro: `(1/0).__traceback__.tb_frame.f_globals['__builtins__']['__import__']('os').system('whoami')`
   - Severity: RCE на хост-машине

2. **Blacklist Homoglyph PI bypass** в `web_sanitizer.py` + `prompt_injection_scanner.py`
   - Cyrillic-only homoglyph map → Greek, Math, Combining, Zero-width все bypass
   - Severity: prompt injection в LLM через external web content

3. **`sdd_executor.py` repr() injection** в `sdd_executor.py:243`
   - Python `repr()` встраивается в subprocess script → Unicode edge cases ломают Python syntax
   - Severity: subprocess RCE

### HIGH (8 — все confirmed)

- OAuth plaintext credentials в `mcp_gmail.py:146`
- DNS Rebinding SSRF (documented в `ssrf_guard.py:164`)
- FloodWaitError → userbot kill в `manager.py:339` (каждый health-check дропает сессию)
- Legacy `tool:confirm:` HMAC bypass в `_confirm.py:469` + `send.py:334`
- HMAC key plaintext на диске `approval.py:85`
- Router stale `now` задерживает HALF_OPEN recovery до 2700s
- `provider.close()` per key → 5s dead time per LLM call
- 11 background loops без overlap guard (cluster_loop, auto_sync_loop, knowledge_distiller, memory_patterns, memory_consolidator, memory_checker (2), auto_evolve, burnout_detector, compaction, ingest, mcp_server, manager health_check_loop, conflict_predictor_loop)

### MEDIUM (5 — все confirmed)

- `flush_now()` race condition `_is_flushing` сброс вне lock в `auto_save_batch.py:303`
- Reporited опасения про threading.Lock в async (3 файла: iteration_budget, context_files, approval)
- 18+ handler files с прямым session.commit/execute (architectural debt)
- `get_or_create_user` 2 divergent implementations
- `_now_utc` 3 идентичные копии (`session_context.py`, `episodic.py`, `free_text_legacy.py`)
- `extra="ignore"` silent config typo в `config.py:28`

### FALSE POSITIVES (8 REFUTED — не фиксить)

1. `_similarity_cache`/`_vector_cache` (НЕ существуют)
2. `_HALF_OPEN_KEYS`/`_RETRY_COUNTS` без cap (НЕ существуют)
3. HTML injection в `free_text_legacy.py:568` (только hardcoded strings)
4. 3 background loop overlap (`auto_sync_loop`, `cluster_loop`, `conflict_predictor_loop`) — все под task_manager
5. ThreadPoolExecutor leak в `main.py:911` — корректный `shutdown(wait=False)`
6. `provider.close()` блокирует rotation — корректное освобождение HTTP
7. `time.sleep` в `main.py` — ДО event loop (sync retry pattern)
8. `iter_budget.py:43` threading.Lock — sync метод, корректно

## 5 Judges Consensus

Все 5 judges (Minimalist, Architect, Defense-in-Depth, Risk-Based, Pragmatic) согласились с топ-5 фиксов:

| Priority | Fix | Time (Pragmatic) |
|----------|-----|------------------|
| P0 | Sandbox `__traceback__` escape | 2h |
| P0 | Homoglyph PI bypass | 3h |
| P0 | `sdd_executor` repr() injection | 1h |
| P1 | Legacy HMAC reject | 1h |
| P1 | flush race + router stale now | 0.5h + 1h |

**Sprint 1 total: ~7h** (1 день) → production-ready для single-user admin бота.

## Architecture Health Summary

- 814 Python файлов
- 236 imports of `get_session()` (coupling hotspot)
- 644 `async with get_session()` sites
- 939 `except Exception:` blocks
- 1229 `except Exception` total (some duplicates possible)
- 187 `asyncio.Lock()`
- 122 ensured futures (17 deprecated ensure_future)
- 129 `# type: ignore`
- 32 hardcoded `asyncio.sleep()` в 20+ файлах
- 25+ raw SQL через `text()` в 7 файлах

## Recommended Followups (Phase F)

- Refactor `_core.py` (2123 строк → 5×400 строк) — biggest cognitive load
- Skill `overlap-guard` — применить ко всем 17 schedulers (eliminate copy-pasta)
- `ensure_future` → `create_task` миграция (Python 3.14+ readiness)
- Service Layer для handler isolation (отдельный epic)
- `_now_utc` deduplication в `src/core/infra/timeutil.py`

## Output Format (Персистентный Сохранение)

Сохранено в:
- `.opencode/memory/research/security-audit-2026-06-19/final-report.md` — top-level summary
- `.opencode/memory/research/security-audit-2026-06-19/synthesis.md` — кросс-judge synthesis
- Этот файл — notes.md addendum

## Lessons Learned

1. **Ponytail DCP не сжал результаты** — environment-managed tool outputs (task, skill) защищены
2. **20 агентов параллельно** — успешно, OpenCode не упёрся в лимит
3. **Cross-judge consensus** нашёл значимый signal для правильного приоритизирования
4. **False positives от волны 1** — `similarity_cache/HALF_OPEN_KEYS` оказались выдуманными (важно опровергать)
5. **Sub-agents с read-only mode** — но `_core.py:2123` etc. проверены direct read'ом

## Verification Approach (для следующего раза)

Если хочется ВЫПОЛНИТЬ фиксы, а не только проверить:
1. Прочитать final-report.md §"Recommended Commits"
2. Создать ветку `fix/security-round-5`
3. Применить изменения in order (5 commits)
4. pytest tests/ -x -v после каждого commit
5. Финальный PR review через R5 process
