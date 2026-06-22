---
name: deepresearch
description: 'Deep Research: веб-поиск через DDGS + параллельная загрузка источников + LLM-саммаризация. Запуск: skill("deepresearch") или скажи "дипресерч".'
---

# Deep Research

**Что это:** Двухфазный pipeline веб-исследования через DuckDuckGo Search (DDGS).

## Как работает

1. **Фаза 1 — Поиск:** DDGS веб-поиск по запросу → список URL
2. **Фаза 2 — Загрузка + саммаризация:** Параллельный fetch страниц → LLM-саммаризация

## Использование

```
skill("deepresearch")
→ "Какой запрос исследовать?"
→ "Сколько минут (1-10, default: 5)?"
```

Или через MCP-инструмент:

```python
deep_research(query="...", max_minutes=5)
```

## Параметры

| Параметр | Тип | Default | Описание |
|----------|-----|---------|----------|
| `query` | str | (обязательный) | Поисковый запрос |
| `max_minutes` | int | 5 | Макс. время выполнения (1–10) |

## Результат

- `job_id` — ID задачи для отслеживания
- Результаты сохраняются в `data/research/<job_id>/`
- `SUMMARY.md` — сводный отчёт
- `sources.jsonl` — собранные источники

## Прогресс в Telegram

Если передан `message` — прогресс стримится в реальном времени через эмодзи-фазы:
🔍 searching → 📖 deep_dive → ⚔️ cross_ref → 🧩 synthesis → ✅ completed

## Ограничения

- Поиск через DDGS (DuckDuckGo) — без API ключа
- Нет retry для failed fetches
- `max_minutes` ограничивает общее время
- Memory seeding / Knowledge Graph / Timeline — **не реализованы** (требуют `pipeline.configure()` который не вызывается)
- Cross-validation / 5 researchers / 10 iterations — **не реализованы**

## Файлы

| Файл | Назначение |
|------|-----------|
| `src/core/rag/deep_research_pipeline.py` | Pipeline (885 строк) |
| `src/core/actions/mcp_deep_research.py` | MCP-инструмент (159 строк) |
| `src/db/models/_research.py` | ORM-модель ResearchJob |
| `src/db/repos/research_repo.py` | Repository для ResearchJob |
| `src/bot/handlers/research_cb.py` | Telegram callback handler |
