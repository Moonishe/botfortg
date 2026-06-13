# Project memory
_Durable project-level knowledge. Persists across all sessions in this project. Edit only content under italic instructions._

## Project context
_What is this project? What's its goal? High-level identity._

TelegramHelper v2.0 — AI-ассистент на Python 3.13, aiogram 3.16, Telethon 1.39, SQLAlchemy asyncio, SQLite + Qdrant embedded. ~500 файлов в `src/`, 49+ ORM-моделей, 70+ MCP-инструментов, 14 LLM-провайдеров, 12 навыков. OpenCode-управляемый проект с Zero-Risk Pipeline (D5→R5).

## Patterns & Insights (discovered durable knowledge)
_Durable findings from trajectory analysis. Updated by dream-agent._

### Успешные подходы
- **Swarm debugging (D5→R5)**: 5-20 параллельных debugger'ов находят 5-30 багов за раунд. Наибольшая отдача — первые 2 итерации. (подтверждено в 124 fix-коммитах, 2026-05-18 – 2026-06-10)
- **Hybrid RRF search**: Qdrant semantic + FTS5 keyword → Reciprocal Rank Fusion (k=60). +30% точности vs чистый semantic. (источник: 3d8510b)
- **Batch операции**: embedding batch (50x), parallel LLM calls (asyncio.gather), auto-save batch — дают наибольший perf-выигрыш. (источник: eb1fd01, 85a2a0c, ebeebeb)
- **Файловый сплит при >2000 строк**: разбиение по domain responsibility с ре-экспортами в __init__.py. 5 успешных применений. (источник: AD-009)
- **Config externalization**: вынос всех интервалов/параметров в config.py через pydantic-settings. 21 поле вынесено за один рефакторинг. (источник: ef3ccf9)
- **WAL mode + PRAGMA busy_timeout=120000**: решило 80% проблем Alembic deadlock на SQLite. (источник: 80d7f51)

### Анти-паттерны (чего избегать)
- **threading.Lock в async-коде**: блокирует event loop → дедлок. Только asyncio.Lock. (13+ race-багов, источник: cc1ef84)
- **Bare `except: pass`**: маскирует баги. Всегда logger.error(exc_info=True). (20+ мест, источник: 1515837)
- **Detached ORM через async-границы**: MissingGreenlet crash. Всегда загружать eager или в рамках одной сессии. (4+ багов, источник: cc1ef84, acdc39e, 85b67b9)
- **Фоновый цикл без overlap guard**: asyncio.create_task в while True запускает дубликаты. (11+ циклов, источник: d215432)
- **Хардкод asyncio.sleep(X)**: магические числа в коде. Выносить в config.py. (источник: ef3ccf9)

### Статистика багов (2026-05-18 – 2026-06-10, ~214 коммитов)
- Race conditions: #1 категория (13 коммитов, ~15% всех fix-коммитов)
- None-guard issues: #2 категория (7+ коммитов)
- Security (SSRF, leak, sanitize, XSS): #3 категория (25 коммитов)
- Alembic/SQLite: 14 коммитов
- CancelledError: 8 коммитов
- Detached ORM: 4+ коммитов

## Rules
_Hard constraints from user that every session must respect._

- Всегда D5→R5 после изменений кода (Zero-Risk Pipeline)
- Перед DCP-сжатием — checkpoint-writer
- Goal Judge перед объявлением «done» (независимая модель, JSON-вердикт)
- Critical задача → Max Mode (5 propose-only candidates + judge + replay)
- `schedule.json` — единственный источник расписания для dream/distill
- Интервалы: dream 2 дня, distill 4 дня
- Не выдумывать файлы, не писать секреты в код
- Асинхронность: все I/O через async/await
- Конфигурация: только через pydantic-settings из `.env`
- **Все фоновые asyncio-циклы — с overlap guard** (семафор/boolean). Без него create_task в цикле запускает дубликат пока первый не завершился. (11+ багов исправлено, источник: d215432)
- **Bare `except: pass` запрещён** — всегда `logger.error(msg, exc_info=True)`. (20+ мест исправлено, источник: 1515837)
- **После изменений async-кода проверять**: размещение asyncio.Lock (не threading.Lock!), CancelledError handling, detached ORM объекты через async-границы. (13+ race condition багов + 8 CancelledError багов, источник: git log grep 'race\|Cancel')
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
**Когда:** 2026-06-10 (обобщение из 24 дней). **Контекст:** swarm-отладка (5-20 параллельных debugger'ов) находит 5-30 багов за раунд, но returns diminish. Зафиксирован паттерн: 30→5→9→0.
**Решение:** Максимум 3 итерации D5→R5 для большинства задач. 10 итераций — только для critical. После 3 раундов без новых HIGH/CRITICAL — эскалация.
**Источник:** commit 936d1e3 «Convergence pattern: 30→5→9. Diminishing returns confirmed.» + 124 fix-коммита.

### AD-007: SQLite + Alembic — PRAGMAs и init_db() fallback
**Когда:** 2026-05-29 – 2026-06-06. **Контекст:** 14 коммитов чинили Alembic на SQLite: deadlock, MultipleHeads, has_table, alter_column unsupported, FTS5, Railway hang.
**Решение:** (1) PRAGMA busy_timeout=120000 + WAL mode. (2) timeout 120s на alembic upgrade head. (3) fallback: stamp head → init_db() при зависании. (4) init_db() проверяет ORM-таблицы, не только alembic_version. (5) sa.inspect().get_table_names() вместо op.has_table.
**Источник:** commits 80d7f51, 320d975, 43b9577, 8131768, 6e591f9, ff4e9cb, 87a24ba, a65e0b7.

### AD-008: Background asyncio loop overlap guard
**Когда:** 2026-06-07. **Контекст:** 11 фоновых циклов запускали дубликаты (asyncio.create_task в while True без проверки что предыдущий инстанс ещё работает).
**Решение:** Каждый фоновый цикл должен иметь boolean guard или asyncio.Semaphore(1) для предотвращения overlap. Паттерн: `if self._loop_running: return` + `self._loop_running = True/False` в finally.
**Источник:** commit d215432 «add overlap guard to 11 remaining background loops».

### AD-009: Monolithic file splitting — threshold ~2000 строк
**Когда:** 2026-05-20 – 2026-06-06. **Контекст:** 5 крупных рефакторингов по разбиению файлов-монолитов: repo.py (2386→214), free_text.py (1911→1066), memory_cmd.py (2363→1457), /keys subsystem, dreaming_reval.py → 3 модуля.
**Решение:** При достижении ~2000 строк — разбивать по domain responsibility. Сохранять ре-экспорты в `__init__.py`. Каждый модуль ≤500 строк. Именование: `{domain}_{subdomain}.py`.
**Источник:** commits 8e43b08, 0bf4763, eca67f2, a3e50e8, bfb539b.

### AD-010: Hybrid RRF Search — Qdrant semantic + FTS5 keyword
**Когда:** 2026-05-21. **Контекст:** Чисто семантический поиск промахивался на keyword-запросах. Решение из академической литературы (Cormack et al. 2009).
**Решение:** Параллельный поиск через asyncio.gather: Qdrant (семантический) + SQLite FTS5 (ключевые слова) → Reciprocal Rank Fusion с k=60. Превосходит чистый semantic на ~30%.
**Источник:** commit 3d8510b «Phase 1: Hybrid RRF — Qdrant semantic + FTS5 keyword fused via Reciprocal Rank Fusion».

### AD-011: asyncio.Lock vs threading.Lock — строгое разделение
**Когда:** 2026-05-24. **Контекст:** ~30 багов из-за использования threading.Lock в async-контексте. threading.Lock блокирует event loop, вызывая дедлоки. Особенно критично для SQLite/Qdrant.
**Решение:** ВСЕ lock-объекты в async-коде должны быть asyncio.Lock. threading.Lock допустим только внутри asyncio.to_thread(). Qdrant write operations сериализованы через asyncio.Lock для предотвращения RocksDB corruption.
**Источник:** commits cc1ef84 «threading.Lock→asyncio.Lock», b1d1714 «serialize write operations with asyncio.Lock».

## Open Questions
_Unresolved issues. Move to §A or §B when resolved._

- **Оптимальный размер swarm**: 5 debugger'ов находят ~15 багов, 20 находят ~70. Но diminishing returns после 10. Какой оптимальный размер для moderate vs complex задач? (источник: AD-006)
- **Qdrant embedded vs server-mode**: сейчас используется embedded (RocksDB), требующий asyncio.Lock сериализации. Стоит ли переходить на server-mode для production? (источник: b1d1714)
- **SQLite → PostgreSQL миграция**: текущий scale — SQLite держит. При каком пороге трафика нужна миграция? (источник: AD-007, 14 alembic-фиксов)
- **Мониторинг race conditions в production**: нет автоматического детектора гонок. Sentinel-паттерн или runtime checker? (источник: статистика — race #1 категория багов)
- **Документирование MCP deep-merge поведения**: не описано в документации OpenCode. Нужен ли upstream issue? (источник: AD-005)
