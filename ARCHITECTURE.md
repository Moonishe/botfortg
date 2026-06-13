# Архитектура TelegramHelper v2.0

## Слои (от нижнего к верхнему)
- config (pydantic-settings)
- db/models (SQLAlchemy ORM)
- db/repos (repository layer — facade through db/repo.py)
- core/ (business logic: intelligence, memory, actions, scheduling)
- llm/ (LLM providers — depends on config + core)
- bot/ (aiogram handlers — depends on repo + core)
- userbot/ (Telethon MTProto)
- agents/ (internal LLM agents)

## Правила зависимостей
- bot → core, repo ✅
- core → db/models ✅ (allowed)
- db/repos → core ❌ (violation — documented as Observer pattern, 19 lazy imports)
- core → bot ❌ (fixed: classifier moved to core/classification)

## Известные нарушения
1. 19 db→core imports (memory_repo.py) — Observer pattern via lazy import. Fix: EventBus.
2. 5 handlers direct model imports (avito, monitor, settings_service, settings_menu, free_text_pipeline) — need repo modules.
3. God-modules: memory_repo.py (2024), free_text_pipeline.py (2334), free_text.py (2386), keys_cmd.py (1769).

## Ключевые конвенции
- async/await для всего I/O
- pydantic-settings из .env (не os.environ)
- Alembic для миграций
- pytest-asyncio + in-memory SQLite
- Ruff: per-file-ignores для E501 (бот + 3 файла), RUF001-3 (кириллица)

## План исправления
1. memory_repo.py → 9 domain sub-modules
2. avito_repo.py + monitor_repo.py (новые repo для handler'ов)
3. 756 except:pass → logger.debug (топ-30 сделано)
4. Ruff E501: постепенно убирать per-file-ignores
