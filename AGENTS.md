# Repository Agent Notes

Copy this file to `AGENTS.md` for local, machine-specific agent instructions.
`AGENTS.md` is intentionally ignored so private workflow notes are not released.

Suggested local content:

- Project-specific coding constraints.
- Commands that are safe to run on this machine.
- Local services, ports, and test data notes.
- Areas that should not be edited without explicit approval.

---

## Project: TelegramHelper v2.0
Python 3.13, aiogram 3.16, Telethon 1.39, SQLAlchemy 2.0 asyncio, SQLite + Qdrant embedded.

### Constitution (ВЫСШИЙ ПРИОРИТЕТ)
- `.opencode/constitution.json` — машиночитаемый law layer
- `.opencode/CONSTITUTION.md` — prose-версия для system prompt
- Authority: user > code > AGENTS.md > rules.md > memory > handoffs
- Protected invariants: async/await, pydantic-settings, Alembic, no raw SQL

### Zero-Risk Pipeline (ОБЯЗАТЕЛЕН после ЛЮБЫХ изменений)
- D5: 5 параллельных debugger'ов (логика, типы, ресурсы, edge cases, интеграция)
- R5: 5 параллельных reviewers (correctness, security, architecture, performance, maintainability)
- Цикл до 0 проблем. Максимум 10 итераций, затем эскалация.
- Goal Judge перед «done»: независимая модель, JSON {ok, impossible, reason}
- Max Mode для critical: 5 propose-only candidates → judge → replay → D5→R5

### Sub-agent Output Contract
Все sub-agents возвращают стандартизированный формат:
- SUMMARY/CHANGES/EVIDENCE/RISKS/BLOCKERS

### Persistent Memory (`.opencode/memory/`)
- `checkpoint.md` — снапшот сессии (§1-§11)
- `memory.md` — долгосрочные знания (4 секции)
- `notes.md` — заметки по ходу сессии
- `.schedule.json` — расписание dream (2д) / distill (4д)
- `.session.json` — boot ID + recovery
- `.metrics.json` — метрики качества

### Memory agents
- checkpoint-writer: снапшот перед DCP, после важных изменений, в конце сессии
- dream-agent: анализ паттернов → memory.md (каждые 2 дня)
- distill-agent: создание skills из workflow (каждые 4 дня)
- rollback-guardian: git-снапшоты + /restore

### Coding Constraints
- ALL I/O must be async/await. Use asyncio.to_thread() for sync wrappers.
- NEVER use raw SQL — always via SQLAlchemy ORM or repository layer.
- Database migrations via Alembic only (`alembic revision --autogenerate`).
- FTS5 indexes via init_db() — never manual DDL in migrations.
- Config always via pydantic-settings from .env. NO hardcoded secrets.
- Error handling: log every exception. NO bare except: pass.
- Graceful shutdown: every component must have cleanup with timeouts.
- New feature = new test in tests/. Use pytest-asyncio with in-memory SQLite.
- Imports: src. prefix for internal, standard library first, then third-party, then src.
- Type annotations on ALL public functions/methods (pyright strict mode).
- Ruff linting: full rule set including S (security), PT (pytest), TCH (type checking), PIE (performance).

### Files NEVER to modify without explicit permission
- src/config.py (Settings class)
- alembic/versions/* (migrations)
- src/db/models/ (ORM models)
- .env (secrets)

### Local services
- SQLite: data/app.db
- Qdrant embedded: data/qdrant/
- Voice files: data/voice/

### Testing
- Run: pytest tests/ -x -v
- In-memory SQLite for tests (sqlite+aiosqlite:///:memory:)
- No real Telegram API calls in tests

---

### Serena — Symbol-Level Code Intelligence (LSP)

Проект использует **Serena 1.5.3** как MCP-сервер для symbol-level анализа кода через LSP (pyright).  
Сервер запускается автоматически OpenCode через `opencode.json` конфиг.

**Когда использовать serena_* инструменты:**

| Инструмент | Что даёт | Приоритет |
|-----------|---------|-----------|
| `serena_find_symbol` | Точный поиск символа по имени | Выше grep'а |
| `serena_get_symbols_overview` | Структура файла: классы, методы, сигнатуры | Вместо read целого файла |
| `serena_find_referencing_symbols` | Кто использует символ (transitive) | Перед rename/delete |
| `serena_rename_symbol` | Безопасное переименование по всему коду | Вместо grep+replace |
| `serena_replace_symbol_body` | Замена тела функции, сохранение сигнатуры | Для точечных правок |
| `serena_insert_before_symbol` / `serena_insert_after_symbol` | Вставка относительно символа | Точное добавление кода |
| `serena_safe_delete_symbol` | Удаление с проверкой references | Безопасная чистка |
| `serena_get_diagnostics_for_file` | LSP-диагностика файла (ошибки, предупреждения) | После изменений |
| `serena_replace_content` | Regex-замена в файле | Для сложных паттернов |
| `serena_search_for_pattern` | Regex-поиск по проекту | Вместо grep |

**Правила использования в Zero-Risk Pipeline:**
- В **D5 Debugger'ах**: `serena_find_symbol` + `serena_find_referencing_symbols` + `serena_get_diagnostics_for_file`
- В **R5 Reviewer'ах**: `serena_get_diagnostics_for_file` + `serena_find_referencing_symbols`
- В **Worker'е**: `serena_rename_symbol` + `serena_replace_symbol_body` + `serena_safe_delete_symbol`
- В **Explorer'е**: `serena_find_symbol` + `serena_get_symbols_overview` + `serena_search_for_pattern`
- В **FINAL AUDIT**: `serena_get_diagnostics_for_file` для всех изменённых файлов

**Схема приоритетов инструментов:**
```
serena_* (LSP-точность) > CodeGraph (FTS5-скорость) > grep/glob (текстовый поиск)
```
Serena для точных LSP-операций (safe rename, diagnostics).  
CodeGraph для быстрого поиска и навигации (FTS5).  
grep/glob — только для текста вне анализируемых файлов.

---

### Ponytail — Lazy Senior Dev Mode (АКТИВЕН ВСЕГДА)

Проект использует **[Ponytail v4.4.0](https://github.com/DietrichGebert/ponytail)** — плагин, заставляющий AI-агента мыслить как ленивый senior-разработчик. Принцип: **«Лучший код — тот, который ты не написал»**.

**Режим по умолчанию:** `full` (включается автоматически при старте OpenCode).

#### Лестница принятия решений (каждый ход, перед написанием кода)

Остановись на первом сработавшем правиле:
1. **Нужно ли это вообще?** (YAGNI) → нет = пропусти
2. **Стандартная библиотека делает это?** → используй
3. **Нативная фича платформы?** → используй
4. **Уже установленная зависимость?** → используй
5. **Можно в одну строку?** → одна строка
6. **Только тогда:** пиши минимальный рабочий код

#### Правила Ponytail (накладываются на Zero-Risk Pipeline)

- Никаких непрошеных абстракций, бойлерплейта, заготовок «на будущее».
- Удаление лучше добавления. Скучное лучше хитрого. Минимум файлов.
- Сомневаешься в сложном запросе → предложи ленивую версию и спроси.
- Два варианта из stdlib одного размера → выбери корректный на edge cases.
- **Маркируй осознанные упрощения** комментарием `# ponytail: <что упрощено, предел, путь апгрейда>`.

#### Что NEVER не упрощать (неприкосновенно)

- Валидация на trust-boundaries
- Обработка ошибок, предотвращающая потерю данных
- Безопасность, accessibility, секреты
- Всё, что явно запрошено пользователем
- D5→R5 pipeline: Ponytail уменьшает **количество** кода в Фазе 1, но Фазы 2–5 выполняются полностью

#### Проверки для ленивого кода

Нетривиальная логика (ветвление, цикл, парсер, money/security) → оставь ОДНУ проверку:
- `assert`-based `demo()` / `__main__` self-check
- или один маленький `test_*.py`
- Без фреймворков, без фикстур. Тривиальным one-liner'ам тесты не нужны.

#### Команды

| Команда | Что делает |
|---------|-----------|
| `/ponytail lite` | Предлагает альтернативы, но строит что просят |
| `/ponytail full` | Лестница обязательна. Stdlib/native прежде всего. (по умолчанию) |
| `/ponytail ultra` | Экстремальный YAGNI. **Не использовать в этом проекте** |
| `/ponytail off` | Выключить Ponytail |
| `/ponytail-review` | Проверить diff на переинжиниринг |
| `/ponytail-audit` | Аудит всего репо на переусложнённый код |
| `/ponytail-debt` | Инвентаризация `ponytail:`-комментариев (техдолг) |

#### Совместимость с Zero-Risk Pipeline

```
Запрос пользователя
  │
  ▼
[Ponytail: лестница YAGNI → минимум кода]   ← ФАЗА 1 (уменьшена)
  │
  ▼
[D5: 5 debuggers]                            ← ФАЗА 2 (быстрее — меньше кода)
  │
  ▼
[R5: 5 reviewers]                            ← ФАЗА 3 (быстрее — меньше кода)
  │
  ▼
[Цикл до 0 проблем]                          ← ФАЗА 4
  │
  ▼
[FINAL AUDIT + Goal Judge]                   ← ФАЗА 5
```

**Ключевое:** Ponytail НЕ отменяет D5→R5. Он уменьшает размер кодовой базы → D5→R5 работает быстрее. Полная синергия.
