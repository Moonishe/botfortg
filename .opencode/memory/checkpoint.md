# Session Checkpoint
**Written:** 2026-06-20T13:35:00Z | **Session:** b7c8d9e0-f1a2-3456-7890-bcdef1234567 | **Branch:** main

---

## §1: Task Snapshot
<!-- Бюджет: 2000 chars. Сверь задачи из task tool + tasks/*.md -->

**Deepresearch Bug Audit (завершён, не закоммичен):**
- [x] Deepresearch: 5 explorer agents — полный security-скан проекта (814 py файлов) — **completed**
- [x] Deepresearch: 5 verify agents — верификация всех находок — **completed**
- [x] Deepresearch: 5 judges consensus — топ-5 фиксов — **completed**
- [x] 3 critical, 8 high, 8 medium, 5 low — все confirmed; 8 false positives refuted — **completed**

**P0/P1 Bug Fixing — Sprint 2 (в работе, не закоммичен):**
- [x] Homoglyph PI bypass: synced maps web_sanitizer↔prompt_injection_scanner, added U+2061-U+2064 strip, removed redundant math map, reordered constants — **completed**
- [ ] Homoglyph full Unicode block coverage (Greek/Math/Combining/Zero-width) — **deferred**
- [x] Router stale now: refreshed `now` timestamp in circuit check — **completed**
- [x] Legacy HMAC bypass: removed `tool:confirm:` legacy path in `_confirm.py` + `send.py` — **completed**
- [x] send:cancel: fixed callbacks — **completed**
- [x] Case-insensitive rich tag conversion in `rich_messages.py` — **completed**
- [x] Exception logging in `restore_state` (session_recorder.py) — **completed**
- [x] D5→R5 cycle 3: 0 blockers — **completed**
- [ ] D5→R5 cycle 4: pending user input — **ready**
- [x] Full suite: 3118 passed, 2 warnings (pre-existing) — **completed**
- [x] Ruff on changed files: passed — **completed**

**Deferred risks (from R5):**
- [ ] 1569 pre-existing global ruff errors — **ask user**
- [ ] Service layer debt (18+ handler files with direct session.commit/execute)
- [ ] Large files (_core.py 2123 строк)
- [ ] provider.close() 5s dead time per LLM call
- [ ] 11 background loops без overlap guard

**Задачи из tasks/:**
- [x] T1: Восстановить .opencode/ после wipe — **completed**
- [ ] T2: Перезапустить OpenCode и проверить Serena MCP — **blocked** (требует ручного рестарта)

---

## §2: Goal Anchor
<!-- Бюджет: 400 chars. ОДНО предложение — явная цель сессии. -->

Fix P0/P1 bugs from deepresearch audit (homoglyph PI bypass, router stale now, legacy HMAC, send callbacks, rich tag conversion) with D5→R5 cycles until no blockers, then decide on commit vs global ruff cleanup.

---

## §3: Active File Snapshot
<!-- Бюджет: 2000 chars. Файлы в работе + что именно в каждом меняется. -->

**P0/P1 bug fixes (this session, uncommitted):**

*Security:*
- `src/core/security/prompt_injection_scanner.py` — synced homoglyph map with web_sanitizer; added U+2061-U+2064 combining char strip; removed redundant math map; reordered constants — **готов**
- `src/core/security/web_sanitizer.py` — synced homoglyph map; added U+2061-U+2064 strip — **готов**

*Bot handlers:*
- `src/bot/handlers/free_text/_confirm.py` — removed legacy `tool:confirm:` HMAC bypass path; `_pop_tool_confirmation` no longer accepts `legacy=True` — **готов**
- `src/bot/handlers/send.py` — fixed `send:cancel` callbacks — **готов**
- `src/bot/handlers/free_text_common.py` — fixes for callback/confirmation flow — **готов**
- `src/bot/rich_messages.py` — case-insensitive rich tag conversion — **готов**

*LLM:*
- `src/llm/router.py` — refreshed `now` timestamp at each circuit state check (stale now fix) — **готов**

*Memory:*
- `src/core/memory/pending_questions.py` — bug fixes — **готов**
- `src/core/memory/session_recorder.py` — added exception logging in `restore_state` — **готов**

*MCP/actions:*
- `src/core/actions/mcp_code_exec.py` — sandbox hardening (previously, continued refinements) — **готов**
- `src/core/actions/sdd_executor.py` — fixes — **готов**
- `src/core/actions/mcp_calculator.py` — fixes — **готов**
- `src/core/actions/vector_store.py` — fixes — **готов**

*Tests:*
- `tests/test_p0p1_fixes.py` — **new** — тесты для P0/P1 фиксов — **готов**
- `tests/test_mcp_code_exec.py` — **new** — sandbox escape тесты — **готов**

*Config:*
- `ruff.toml` — added per-file ignores for test_p0p1_fixes.py, test_mcp_code_exec.py — **готов**

*Other touched (minor fixes):*
- `src/core/contacts/health_score.py`, `src/core/security/approval.py`, `src/core/security/prompt_guard.py`, `src/main.py`, `src/bot/handlers/inbox_cmd.py`, `src/bot/handlers/free_text_legacy.py`, `src/userbot/manager.py`, `scripts/rotate_keys.py`, `src/db/models/_encryption.py`, `src/db/repos/key_repo.py`, `src/llm/*_provider.py` (11 files) — **готов**

**Total: ~56 modified + 5 new files (61 uncommitted).**

---

## §4: Architecture Snapshot
<!-- Бюджет: 1500 chars. Текущее состояние архитектуры: какие компоненты затронуты, их связи. -->

**P0/P1 bug fixing changes (this session, on top of previous deepresearch fixes):**

1. **HMAC bypass eliminated** (`_confirm.py`, `send.py`): removed legacy `tool:confirm:` callback path — now `_pop_tool_confirmation` always requires HMAC signature. Unified `ap:tool:` and `ap:intent:` prefixes only. This closes the P1 HMAC bypass vector identified by deepresearch.

2. **Homoglyph detection synced** (`web_sanitizer.py`, `prompt_injection_scanner.py`): both files now share the same homoglyph map. Added U+2061-U+2064 (invisible mathematical operators) to strip list. Removed redundant math map. Constants reordered for maintainability. Still lacks Greek/Math/Combining full Unicode block coverage (deferred).

3. **Router stale now fix** (`router.py`): replaced cached `self._now` with fresh `time.time()` at each circuit state check, so HALF_OPEN recovery no longer delayed up to 2700s.

4. **send:cancel callbacks** (`send.py`, `free_text_common.py`): fixed callback routing for cancel action — was missing or misrouted.

5. **Rich messages** (`rich_messages.py`): case-insensitive tag name conversion — `<Bold>` → `<b>` etc.

6. **Exception logging** (`session_recorder.py`): `restore_state` now logs exceptions instead of silent failure.

7. **Infrastructure debt unchanged**: 18+ handler files with direct session.commit/execute remain; _core.py 2123 lines; provider.close() 5s dead time per call; 11 background loops without overlap guard; 1569 global ruff errors pre-existing.

---

## §5: Recent Findings
<!-- Бюджет: 1500 chars. Ключевые находки из D5/R5/тестов. -->

**D5→R5 Cycle 3 (P0/P1 bug fixes) — 0 blockers:**

- **Deepresearch P0/P1 findings addressed this cycle:**
  - Homoglyph PI bypass (P0) — map synced, U+2061-U+2064 stripped, math map deduped. Full Unicode coverage deferred.
  - Router stale now (P1) — fixed: `now` refreshed each circuit check.
  - Legacy HMAC bypass (P1) — removed `tool:confirm:` path entirely.
  - send:cancel callbacks (medium) — fixed.
  - Case-insensitive rich tag conversion (low) — fixed.
  - Exception logging in restore_state (low) — fixed.

- **Test results:**
  - Full suite: 3118 passed (+48 from previous 3070 due to new tests), 0 failed
  - 2 warnings (pre-existing — unrelated)
  - Ruff on changed files: passed (0 new issues)
  - Global ruff: 1569 pre-existing errors (unchanged)

- **R5 cycle 3 — 0 blockers.** Remaining observations (all pre-existing, accepted):
  - Service layer debt: 18+ handlers with direct session.commit/execute
  - Large file: `_core.py` at 2123 lines
  - 11 background loops without overlap guard
  - `provider.close()` per call — 5s dead time
  - 1569 global ruff errors (mostly E501, RUF, S)

**Previous deepresearch findings (unchanged):**
- CRITICAL: sandbox escape __traceback__ (mcp_code_exec.py) — **fixed**
- CRITICAL: homoglyph PI bypass — **partially fixed**, full coverage deferred
- CRITICAL: sdd_executor repr() injection — **partially fixed**
- HIGH: OAuth plaintext credentials (mcp_gmail.py) — **deferred**
- HIGH: legacy HMAC bypass — **fixed**
- HIGH: router stale now — **fixed**
- HIGH: 11 background loops no overlap guard — **deferred**

---

## §6: Risk Register
<!-- Бюджет: 1000 chars. Известные риски текущей работы. -->

| Risk | Severity | Mitigation |
|------|----------|------------|
| Homoglyph PI bypass — partial fix (Greek/Math/Combining/Zero-width not covered) | critical | Deferred — full Unicode block coverage needed |
| sdd_executor repr() injection | critical | Partially fixed; full sanitization deferred |
| OAuth plaintext в mcp_gmail.py:146 | high | Deferred — store encrypted, decrypt at use |
| 11 background loops без overlap guard | high | Deferred — применить overlap-guard skill |
| DNS Rebinding SSRF (ssrf_guard.py:164) | high | Documented, fix in next sprint |
| provider.close() 5s dead time per LLM call | high | Deferred — batch close or lazy init |
| 1569 pre-existing global ruff errors | medium | Ask user: fix now or defer |
| ~61 uncommitted файла (56 modified + 5 new) | low | Commit pending (blocked by user decision) |
| T2 blocked (Serena MCP not verified after restart) | low | Ручной рестарт OpenCode |
| Dream/Distill overdue (last ~2026-06-14) | low | Запустить в следующей сессии |

---

## §7: Agent State
<!-- Бюджет: 500 chars. Какие sub-agents активны/завершены. -->

- **Deepresearch Wave (read-only):** 5 Explorers, 5 Verifiers, 5 Judges — **completed**
- **Mass Bug Fixing Wave 1:** 5 Debuggers (D5), 5 Reviewers (R5) — **completed** → 0 blockers
- **Mass Bug Fixing Wave 2:** D5 + R5 — **completed** → Lead Reviewer GO
- **P0/P1 Bug Fixing Sprint 2:**
  - Debuggers (D5 cycle 3) — **completed** → no blockers
  - Reviewers (R5 cycle 3) — **completed** → 0 blockers
  - Full suite (3118 passed) — **completed**
- Checkpoint Writer — **active**
- D5→R5 cycle 4 — **pending user input** (global ruff: fix or commit?)

---

## §8: Next Steps
<!-- Бюджет: 800 chars. Что делать дальше (из todowrite). -->

1. **Согласовать с пользователем:** фиксить 1569 глобальных ruff-ошибок или закоммитить текущие ~61 файл
2. **Если commit:** закоммитить все P0/P1 bug fixes (8 ключевых файлов + сопутствующие)
3. **Если ruff fix:** запустить целевые авто-фиксы (ruff check --fix) по категориям
4. **Запустить dream-agent** (overdue с 2026-06-14)
5. **Запустить distill-agent** (overdue с 2026-06-14)
6. **Sprint 3:** deferred топ-5 — homoglyph full coverage, sdd_exec, OAuth, background loops overlap guard
7. **Phase F:** split _core.py (2123 строк), SQLAlchemy debt, _now_utc dedup, provider.close() optimization

---

## §9: Learnings
<!-- Бюджет: 800 chars. Чему научились в этой сессии. -->

**Новые learnings (P0/P1 bug fixing):**
- Legacy HMAC removal: `tool:confirm:` callback pattern был HMAC bypass — любой action_key мог быть использован без подписи. Единый `ap:tool:` prefix + обязательная HMAC верификация закрывают вектор.
- Homoglyph map sync: при дублировании map между модулями (web_sanitizer ↔ prompt_injection_scanner) нужно синхронизировать или вынести в shared константу — иначе расхождение даёт false sense of security.
- U+2061-U+2064 (invisible mathematical operators) — ещё один класс zero-width символов, не covered исходным strip'ом.
- 3118 тестов passed — тестовая база продолжает расти; каждое изменение покрывается новыми тестами.
- Ruff per-file-ignore: ruff.toml поддерживает `package/module.py = ["RUF001"]` для тестов с intentional homoglyph data.

**Предыдущие learnings (сохранены):**
- Sandbox escape через `f_globals/tb_frame/__traceback__` — блокировать chain до `__builtins__`
- Homoglyph bypass: Cyrillic-only map не покрывает Greek/Math/Combining/Zero-width
- `repr()` injection: Python `repr()` не безопасен для subprocess
- 20 parallel agents успешно — cross-judge consensus сильный signal
- False positives — проверять существование symbols перед включением в отчёт
- mcp_shell deny-list: проверять ВСЕ аргументы cmd
- sum(1 for _ in reader) — single-pass CSV counting
- Anthropic validate_key: transient errors ≠ invalid key
- queue.get(timeout=N) обязателен для blocking queues
- InaccessibleMessage monkeypatch через setattr
- tool_pairing cache invalidation: очищать ВСЕ связанные ключи
- Silent except:pass — всегда логировать
- Max Mode: Creative candidate может добавить hardening

---

## §10: Tool-Specific
<!-- Бюджет: 500 chars. Особые настройки тулов (если менялись). -->

None. Все MCP-серверы (serena, codegraph, memory, context7, git, playwright, chrome-devtools, open-computer-use) и конфигурации остались без изменений.

---

## §11: Final Notes
<!-- Бюджет: 500 chars. Любые замечания, не вошедшие в другие секции. -->

P0/P1 bug fixing sprint 2 завершён: homoglyph map sync + U+2061-U+2064 strip, router stale now fix, legacy HMAC removal, send:cancel callback fix, case-insensitive rich tags, exception logging. D5→R5 cycle 3: 0 blockers. Full suite: 3118 passed. Остаётся 1569 pre-existing ruff ошибок — решение за пользователем (commit сейчас или fix сначала). Deferred: full homoglyph Unicode coverage, sdd_exec repr(), OAuth, 11 overlap guards. Dream/distill overdue. 61 uncommitted файл.
