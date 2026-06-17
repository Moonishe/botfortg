# Project memory
_Durable project-level knowledge. Persists across all sessions in this project. Edit only content under italic instructions._

## Project context
_What is this project? What's its goal? High-level identity._

TelegramHelper v2.0 — AI-ассистент на Python 3.13, aiogram 3.16, Telethon 1.39, SQLAlchemy asyncio, SQLite + Qdrant embedded. ~500 файлов в `src/`, 49+ ORM-моделей, 70+ MCP-инструментов, 14 LLM-провайдеров, 12 навыков. OpenCode-управляемый проект с Zero-Risk Pipeline (D5→R5).

## Patterns & Insights (discovered durable knowledge)
_Durable findings from trajectory analysis. Updated by dream-agent._

### Distill 2026-06-14: созданы 2 skills на основе повторяющихся workflow
- `overlap-guard` (skill) — Background asyncio loop overlap guard: предотвращает запуск дубликатов фоновых циклов через asyncio.Lock. На основе 16+ инстансов в `src/core/scheduling/*`. Триггер: создание/рефакторинг фонового asyncio-цикла.
- `gateway-caching` (skill) — Добавление TTL-кэширования через ManagedCache/AdaptiveTTLCache/TTLCache. На основе 14+ инстансов в 10+ модулях. Триггер: запрос на добавление кэширования/оптимизацию.
- Источник: trajectory analysis (без trajectory DB — анализ memory.md + checkpoint.md + grep по кодовой базе), 2026-06-14.

### Успешные подходы
- **Swarm debugging (D5→R5)**: 5-20 параллельных debugger'ов находят 5-30 багов за раунд. Наибольшая отдача — первые 2 итерации. (подтверждено в ~250 fix-коммитах, 2026-05-18 – 2026-06-14)
- **Domain Package Pattern**: новый компонент → пакет из 5±2 модулей + ORM-модель + репозиторий + Alembic-миграция + MCP-инструменты + тесты. Подтверждено на Cron Scheduler (5 файлов, 67 тестов, 8 MCP-инструментов). (источник: T3, checkpoint.md §4, 2026-06-14)
- **Goal Judge Architecture**: ABC + Pydantic-модель вердикта + фабрика `create_*()` + LLM-реализация через TaskType. Max Mode (5 candidates) выбрал отдельный GoalJudgeLLM вместо встраивания в pipeline. (источник: T4, goal_judge.py, 2026-06-14)
- **LazyModule — прозрачный deferred import**: `__getattr__` прокси + `asyncio.Lock` thread-safety + `LazyDepRegistry` для health-check аудита + `lazy_import_or_none()` для опциональных зависимостей. 32 теста, 0 дефектов с первой попытки. (источник: T5, lazy_import.py, 2026-06-14)
- **Hermes Self-Management Pattern**: 6 MCP self-* инструментов (model/config/info/usage/delegate/restart) + mcp_expose auto-discovery + FTS5 tool search + MiddlewareChain + Tool Guardrails. Модульные паттерны для самоуправляемого агента. (источник: checkpoint.md §9, 2026-06-14)
- **N+1 → asyncio.gather + Semaphore(N)**: замена sequential на parallel — dialogs: 30 sequential → 6 волн × 5 parallel = 5× ускорение. (источник: fa817ef, 4863236)
- **Hybrid RRF search**: Qdrant semantic + FTS5 keyword → Reciprocal Rank Fusion (k=60). +30% точности vs чистый semantic. (источник: 3d8510b)
- **Batch операции**: embedding batch (50x), parallel LLM calls (asyncio.gather), auto-save batch — дают наибольший perf-выигрыш. (источник: eb1fd01, 85a2a0c, ebeebeb)
- **Файловый сплит при >2000 строк**: разбиение по domain responsibility с ре-экспортами в __init__.py. 7 успешных применений (repo, free_text, memory_cmd, keys, dreaming_reval, memory_repo, free_text_pipeline). (источник: AD-009, 747c08e)
- **Config externalization**: вынос всех интервалов/параметров в config.py через pydantic-settings. 21 поле вынесено. (источник: ef3ccf9)
- **WAL mode + PRAGMA busy_timeout=120000**: решило 80% проблем Alembic deadlock на SQLite. (источник: 80d7f51)

### Анти-паттерны (чего избегать)
- **threading.Lock в async-коде**: блокирует event loop → дедлок. Только asyncio.Lock. (13+ race-багов, источник: cc1ef84)
- **Bare `except: pass`**: маскирует баги. Всегда logger.error(exc_info=True). (0 remaining — 756 исправлено за всё время, источник: 1515837, 991f89f)
- **Detached ORM через async-границы**: MissingGreenlet crash. Всегда загружать eager или в рамках одной сессии. (4+ багов, источник: cc1ef84, acdc39e, 85b67b9)
- **Фоновый цикл без overlap guard**: asyncio.create_task в while True запускает дубликаты. (11+ циклов исправлено, источник: d215432)
- **Хардкод asyncio.sleep(X)**: магические числа в коде. Выносить в config.py. (источник: ef3ccf9)

### Статистика багов (2026-05-18 – 2026-06-14, ~300 коммитов, ~250 fix-коммитов)
- Race conditions: #1 категория (15+ коммитов)
- None-guard issues: #2 категория (8+ коммитов)
- Security (SSRF, RCE, leak, sanitize): #3 категория (30+ коммитов)
- Alembic/SQLite: 16 коммитов
- CancelledError: 10+ коммитов
- Detached ORM: 5+ коммитов
- Circuit breaker / deadlock: 4+ коммитов (948fe45, 53838fe)

## Rules
_Hard constraints from user that every session must respect._

- Всегда D5→R5 после изменений кода (Zero-Risk Pipeline)
- Перед DCP-сжатием — checkpoint-writer
- Goal Judge перед объявлением «done» (независимая модель, JSON-вердикт)
- Critical задача → Max Mode (5 propose-only candidates + judge + replay)
- Max Mode также для архитектурных решений с несколькими вариантами (источник: T4 Goal Judge)
- `schedule.json` — единственный источник расписания для dream/distill
- Интервалы: dream 2 дня, distill 4 дня
- Не выдумывать файлы, не писать секреты в код
- Асинхронность: все I/O через async/await
- Конфигурация: только через pydantic-settings из `.env`
- **Новый компонент → Domain Package Pattern**: пакет из 5±2 модулей + ORM + repo + Alembic + MCP + тесты. Не монолит. (источник: Cron Scheduler T3, 2026-06-14)
- **Опциональные зависимости → LazyModule**: использовать `lazy_import()` / `lazy_import_or_none()` из `src.core.infra.lazy_import`. Не блокировать event loop тяжёлыми импортами. (источник: T5, 2026-06-14)
- **Все фоновые asyncio-циклы — с overlap guard** (семафор/boolean). Без него create_task в цикле запускает дубликат пока первый не завершился. (11+ багов исправлено, источник: d215432)
- **Bare `except: pass` запрещён** — всегда `logger.error(msg, exc_info=True)`. (0 remaining, источник: 1515837, 991f89f)
- **После изменений async-кода проверять**: размещение asyncio.Lock (не threading.Lock!), CancelledError handling, detached ORM объекты через async-границы. (13+ race condition багов + 10+ CancelledError багов)
- **Все конфигурируемые интервалы — в config.py** через pydantic-settings. Никаких хардкод `asyncio.sleep(X)`. (21 поле вынесено, источник: ef3ccf9)

## Architecture decisions
_Major design choices with rationale. The "why" matters more than the "what" for future sessions._

### AD-001: Constitution как высший приоритет
**Когда:** 2026-06-11. **Контекст:** нужен явный law layer для разрешения конфликтов инструкций.
**Решение:** `.opencode/constitution.json` + `CONSTITUTION.md` с authority hierarchy: user request > code > AGENTS.md > rules.md > memory > handoffs.
**Источник:** CodeWhale (Hmbown/CodeWhale).

### AD-002: Sub-agent Output Contract
**Когда:** 2026-06-11. **Контекст:** агенты возвращают произвольный текст, невозможна автоматизация анализа.
**Решение:** стандартизированный OUTPUT CONTRACT: SUMMARY/CHANGES/EVIDENCE/RISKS/BLOCKERS.
**Источник:** CodeWhale subagent system.

### AD-003: Persistent Memory (из MiMo-Code)
**Когда:** 2026-06-11. **Контекст:** DCP сжатие теряет контекст, нужна долгосрочная память.
**Решение:** `.opencode/memory/` с CHECKPOINT_TEMPLATE (§1-§11) + MEMORY_TEMPLATE (4 секции), checkpoint-writer + dream-agent + distill-agent.
**Источник:** MiMo-Code (XiaomiMiMo/MiMo-Code).

### AD-004: `schedule.json` как аналог SessionTable
**Когда:** 2026-06-11. **Контекст:** MiMo-Code использует SQLite SessionTable для отслеживания времени последнего dream/distill. В OpenCode engine-level недоступен.
**Решение:** structured JSON `.opencode/memory/schedule.json` с unix timestamp полями.
**Альтернативы:** текстовый поиск дат в memory.md — отвергнут (ненадёжно).

### AD-005: Project opencode.json MUST include mcp section
**Когда:** 2026-06-11. **Контекст:** Serena отвалилась после добавления project opencode.json без mcp секции.
**Решение:** OpenCode deep-merges configs. Project config БЕЗ mcp → global MCP overridden to empty. Project config ДОЛЖЕН включать mcp секцию.

### AD-006: D5→R5 Convergence — diminishing returns after 2-3 iterations
**Когда:** 2026-06-10 (обобщение из 24 дней). **Контекст:** swarm-отладка находит 5-30 багов за раунд, но returns diminish. Паттерн: 30→5→9→0.
**Решение:** Максимум 3 итерации D5→R5 для большинства задач. 10 итераций — только для critical. После 3 раундов без новых HIGH/CRITICAL — эскалация.
**Источник:** commit 936d1e3 + ~250 fix-коммитов.

### AD-007: SQLite + Alembic — PRAGMAs и init_db() fallback
**Когда:** 2026-05-29 – 2026-06-13. **Контекст:** 16 коммитов чинили Alembic на SQLite: deadlock, MultipleHeads, has_table, alter_column unsupported, FTS5, Railway hang, circuit breaker.
**Решение:** (1) PRAGMA busy_timeout=120000 + WAL mode. (2) timeout 120s на alembic upgrade head. (3) fallback: stamp head → init_db() при зависании. (4) init_db() проверяет ORM-таблицы, не только alembic_version. (5) sa.inspect().get_table_names() вместо op.has_table.
**Источник:** commits 80d7f51, 320d975, 43b9577, 8131768, 6e591f9, ff4e9cb, 87a24ba, a65e0b7, 948fe45.

### AD-008: Background asyncio loop overlap guard
**Когда:** 2026-06-07. **Контекст:** 11 фоновых циклов запускали дубликаты (asyncio.create_task в while True без проверки что предыдущий инстанс ещё работает).
**Решение:** Каждый фоновый цикл должен иметь boolean guard или asyncio.Semaphore(1). Паттерн: `if self._loop_running: return` + `self._loop_running = True/False` в finally.
**Источник:** commit d215432.

### AD-009: Monolithic file splitting — threshold ~2000 строк
**Когда:** 2026-05-20 – 2026-06-14. **Контекст:** 7 крупных рефакторингов по разбиению файлов-монолитов.
**Решение:** При достижении ~2000 строк — разбивать по domain responsibility. Сохранять ре-экспорты в `__init__.py`. Каждый модуль ≤500 строк. Именование: `{domain}_{subdomain}.py`.
**Источник:** commits 8e43b08, 0bf4763, eca67f2, a3e50e8, bfb539b, 747c08e.

### AD-010: Hybrid RRF Search — Qdrant semantic + FTS5 keyword
**Когда:** 2026-05-21. **Контекст:** Чисто семантический поиск промахивался на keyword-запросах.
**Решение:** Параллельный поиск через asyncio.gather: Qdrant (семантический) + SQLite FTS5 (ключевые слова) → Reciprocal Rank Fusion с k=60. +30% точности.
**Источник:** commit 3d8510b.

### AD-011: asyncio.Lock vs threading.Lock — строгое разделение
**Когда:** 2026-05-24. **Контекст:** ~30 багов из-за threading.Lock в async-контексте.
**Решение:** ВСЕ lock-объекты в async-коде — asyncio.Lock. threading.Lock только внутри asyncio.to_thread(). Qdrant write — asyncio.Lock для предотвращения RocksDB corruption.
**Источник:** commits cc1ef84, b1d1714.

### AD-012: Domain Package Pattern — структура нового компонента
**Когда:** 2026-06-14. **Контекст:** Generic Cron Scheduler — первый компонент, спроектированный по этому паттерну.
**Решение:** Компонент средней сложности (5±2 модуля) организуется как: пакет в `src/core/{domain}/` с модулями по responsibilities + ORM-модель + репозиторий + Alembic-миграция + MCP-инструменты в `src/core/actions/` + тесты в `tests/`. Не монолит, не микросервис — domain package.
**Альтернативы:** монолитный модуль (отвергнут — AD-009), микросервис (избыточен для этого масштаба).
**Источник:** T3 Cron Scheduler (5 файлов), checkpoint.md §4, 2026-06-14.

### AD-013: LazyModule — deferred import с прозрачным прокси
**Когда:** 2026-06-14. **Контекст:** тяжёлые опциональные зависимости (telethon, qrcode, playwright) замедляют cold start и блокируют event loop.
**Решение:** `LazyModule` — transparent proxy через `__getattr__` с `importlib.import_module` при первом доступе. `asyncio.Lock` для thread-safe загрузки. `LazyDepRegistry` для аудита. `lazy_import_or_none()` для опциональных зависимостей (graceful None при ImportError).
**Альтернативы:** ручной import внутри функций (фрагментирует код), dynamic import без аудита (невидимые сбои).
**Источник:** T5 lazy_import.py (281 строка, 32 теста), 2026-06-14.

### AD-014: Goal Judge как отдельный LLM-вызов
**Когда:** 2026-06-14. **Контекст:** архитектурный спор: встроить goal-check в pipeline или сделать отдельным LLM-вызовом. Max Mode (5 candidates) выбрал отдельный вызов.
**Решение:** `GoalJudge` ABC + `GoalVerdict` Pydantic модель (ok/impossible/reason/confidence) + `GoalJudgeLLM` через `TaskType.GOAL_JUDGE` + фабрика `create_goal_judge()`. Отдельный LLM-вызов обеспечивает независимость вердикта (нет влияния от основного pipeline-контекста) и тестируемость (mock LLM).
**Альтернативы:** встраивание в pipeline (отвергнут — меньше независимость, хуже тестируемость); rule-based проверка (отвергнут — не покрывает semantic edge cases).
**Источник:** T4 goal_judge.py (268 строк, 19 тестов), Max Mode decision, 2026-06-14.

### AD-015: Hermes Self-Management — MCP self-* инструменты
**Когда:** 2026-06-14. **Контекст:** агенту нужна runtime-интроспекция и самоуправление: сменить LLM-модель, перечитать конфиг, проверить health, посмотреть usage, делегировать под-агента, перезапуститься.
**Решение:** 6 MCP-инструментов (self-model, self-config, self-info, self-usage, delegate, restart) + admin_mode для mcp_shell + mcp_expose auto-discovery. Каждый инструмент — отдельный модуль в `src/core/actions/mcp_self_*.py`. MiddlewareChain для pre/post хуков.
**Источник:** commits 35f1a36, 86e7363, c0837ca, d3d3e7e, 2026-06-14.

### AD-016: MCP Expose Auto-Discovery
**Когда:** 2026-06-14. **Контекст:** ручная регистрация MCP-инструментов не масштабируется при 70+ инструментах.
**Решение:** `mcp_expose.py` — module-level `expose_to_mcp()` декоратор, который автоматически обнаруживает и регистрирует инструменты. Enum-based категории (research, memory, system, self, cron). Интеграция с Tool Registry FTS5.
**Источник:** checkpoint.md §4, mcp_expose.py, 2026-06-14.

### AD-017: Hybrid Approval Kernel — unified HMAC-signed callbacks
**Когда:** 2026-06-16. **Контекст:** двойная схема HMAC (repo-level action_id HMAC + approval-level payload HMAC) и старые клавиатуры `send:confirm:` без подписи; двойной confirmation в free-text send.
**Решение:** Единое ядро `src/core/security/approval.py` с форматом `ap:{verb}:{action_key}:{signature}` и HMAC-SHA256 по payload `action_key:user_id:verb:expires_at:payload_hash`. Два маршрута: DB (high/critical/destructive) и memory (medium/low). DB route использует `user.id`, memory route — `telegram_id`. Legacy callback'и принимаются без HMAC, но новые клавиатуры генерируют только unified. Двойной confirmation устранён: `_confirmed=True` вставляется только верифицированным callback-путём `_cb_tool_confirm` после HMAC; `_execute_intent` и `_dispatch` вырезают LLM-инжектированный `_confirmed`.
**Источник:** `src/core/security/approval.py`, `src/db/repos/commitment_repo.py`, `src/bot/handlers/send.py`, `src/bot/handlers/free_text/_core.py`, `tests/test_approval.py`, `tests/test_hybrid_approval.py`, 2026-06-16.

### AD-018: Telegram Cron Panel — Approval Kernel for destructive actions + progress cards
**Когда:** 2026-06-17. **Контекст:** пользователю нужен Telegram UI для управления cron-задачами: список, создание, шаблоны, запуск/удаление. Деструктивные действия (run/delete) требуют подтверждения; long-running LLM-задачи должны показывать прогресс.
**Решение:** Отдельный Telegram handler `src/bot/handlers/cron_cmd.py` с `/cron`, `/cron add`, `/cron blueprints` и inline-клавиатурой. Destructive actions (`cron_run`, `cron_delete`) маршрутизируются через Approval Kernel (`_store_intent_confirmation` + `INTENT_HANDLERS` в `free_text/_core.py`), риск HIGH. Для `llm_prompt` задач показывается progress card, удаляемая после завершения. Телеграм-специфичные executor'ы вынесены в `src/bot/handlers/cron_exec.py` вместо inline-логики в `_core.py` для соблюдения SRP. `user_id` передаётся через intent params, потому что `callback.message.from_user` — бот, а не пользователь.
**Источник:** `src/bot/handlers/cron_cmd.py`, `src/bot/handlers/cron_exec.py`, `src/bot/handlers/free_text/_core.py`, `tests/test_cron_cmd.py`, commit 456a5fe, 2026-06-17.

### AD-019: Bounded Session Memory — structured snapshot + prompt audit
**Когда:** 2026-06-17. **Контекст:** `frozen_snapshot` содержал только 3 факта; `session_summary` в `AssemblyContext` существовал, но никогда не заполнялся; `ContactProfile.memory_digest` строился, но не попадал в промпт; pending-вопросы хранились отдельно; нет токен-бюджетирования на этапе сборки промпта.
**Решение:** Новый модуль `src/core/memory/session_snapshot.py` собирает bounded snapshot (3-7 фактов, per-contact digest, pending-вопросы, стиль, риски, session summary) с токен-бюджетом (512) и prompt-injection-сканированием. `_set_frozen` в `context_gatherer.py` теперь заполняет `ctx.frozen_snapshot` (форматированный блок) и `ctx.session_summary` (сырой summary), прокидывает `contact_id` из `maestro.process`. `prompt_assembler.py` ведёт audit размера промпта (chars/tokens/stage) в `_capacity_check`. `pending_questions.py` получил `peek_pending()` без drain'а очереди и общий helper `_append_in_memory` с cap 20 для обоих путей записи.
**Источник:** `src/core/memory/session_snapshot.py`, `src/core/memory/pending_questions.py`, `src/core/intelligence/context_gatherer.py`, `src/core/intelligence/prompt_assembler.py`, `src/core/intelligence/maestro.py`, `tests/test_session_snapshot.py`, commit 23222d9, 2026-06-17.

### AD-020: Skills Lifecycle — dry-run → approve → apply + per-skill evolve
**Когда:** 2026-06-17. **Контекст:** панель `/skills` уже показывала статусы и метрики, но не было ручного триггера эволюции для одного навыка и не было явного dry-run → approve → apply flow для пачки кандидатов.
**Решение:** Добавлены callback'и `evolve_one`, `evolve_dryrun`, `evolve_apply` в `src/bot/handlers/skills_callbacks.py`. UI-вспомогательные функции (`_format_evolve_dryrun`, `_format_evolve_apply`) изолированы в `src/bot/handlers/skills_ui.py`. Пачка кандидатов эволюционируется параллельно через `asyncio.gather` с общим `asyncio.Semaphore(2)` (`_EVOLVE_SEMAPHORE` из `src/core/intelligence/auto_evolve.py`), чтобы не превысить rate limit LLM. HTML-escape применяется ко всем user/LLM-контролируемым строкам. Метрики `_format_metrics` теперь защищены от отрицательных счётчиков и `validation_score > 1`. `__all__` в `auto_evolve.py` дополнен публичным API. Добавлены тесты `tests/test_skills_evolve.py` (20 тестов) и обновлён `tests/test_skills_cmd.py`.
**Источник:** `src/bot/handlers/skills_callbacks.py`, `src/bot/handlers/skills_ui.py`, `src/core/intelligence/auto_evolve.py`, `tests/test_skills_evolve.py`, `tests/test_skills_cmd.py`, commit 7ed5ffe, 2026-06-17.

## Open Questions
_Unresolved issues. Move to §A or §B when resolved._

- **Оптимальный размер swarm**: 5 debugger'ов находят ~15 багов, 20 находят ~70. Но diminishing returns после 10. Какой оптимальный размер для moderate vs complex задач? (источник: AD-006)
- **Qdrant embedded vs server-mode**: сейчас используется embedded (RocksDB), требующий asyncio.Lock сериализации. Стоит ли переходить на server-mode для production? (источник: b1d1714)
- **SQLite → PostgreSQL миграция**: текущий scale — SQLite держит. При каком пороге трафика нужна миграция? (источник: AD-007, 16 alembic-фиксов)
- **Мониторинг race conditions в production**: нет автоматического детектора гонок. Sentinel-паттерн или runtime checker? (источник: статистика — race #1 категория багов)
- ~~**Distill Agent ни разу не запускался**~~ — **RESOLVED 2026-06-14**: первый запуск, созданы 2 skills (overlap-guard, gateway-caching).
- **Стандартизация LazyModule**: стоит ли перевести все опциональные зависимости (telethon, playwright, qrcode, yt-dlp, etc.) на LazyModule для ускорения cold start? (источник: AD-013, 2026-06-14)
