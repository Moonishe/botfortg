---
name: gateway-caching
description: Добавление TTL-кэширования в компоненты через ManagedCache/AdaptiveTTLCache/TTLCache. Использовать при запросе "добавь кэширование", "ускорь", "оптимизируй", создании нового gateway или cache layer.
---

# Gateway Caching — TTL-кэширование в проекте

Ты добавляешь кэширование в компонент TelegramHelper. Используй **только** существующую инфраструктуру кэширования проекта — не изобретай свой кэш.

## Инфраструктура кэширования

Проект предоставляет 3 типа кэшей (все async-safe через `asyncio.Lock`):

| Кэш | Модуль | Когда использовать |
|-----|--------|-------------------|
| **ManagedCache** | `src.core.cache.manager` | Основной кэш: LRU + TTL + метрики + auto-cleanup. Для большинства случаев. |
| **AdaptiveTTLCache** | `src.core.cache.adaptive` | Адаптивный TTL: часто используемые ключи живут дольше. Для hot-data (digest'ы, паттерны). |
| **TTLCache** | `src.core.memory.ttl_cache` | Простой generic TTL-кэш. Для случаев где не нужен cache_manager. |

## Правило выбора

```
Нужен кэш для данных которые:
├── Часто запрашиваются одни и те же ключи?
│   └── AdaptiveTTLCache (растущий TTL)
├── Нужна глобальная регистрация + авто-очистка?
│   └── ManagedCache + cache_manager.register()
└── Простой in-memory TTL без инфраструктуры?
    └── TTLCache
```

## ManagedCache (основной)

```python
from src.core.cache.manager import ManagedCache, cache_manager

# Регистрация (глобальный модульный уровень)
_my_cache: ManagedCache[str, MyType] = cache_manager.register(
    ManagedCache[str, MyType](
        name="my_cache",         # Уникальное имя для мониторинга
        max_size=500,            # LRU-лимит ключей
        default_ttl=300.0,       # TTL в секундах
    )
)

# Использование
value = await _my_cache.get(key)        # None если нет/истекло
await _my_cache.set(key, value)         # Сохранить
await _my_cache.set(key, value, ttl=60) # С кастомным TTL
await _my_cache.invalidate(key)          # Инвалидировать
await _my_cache.clear()                  # Очистить всё

# Single-writer: дорогой вызов (LLM, БД) делается ОДНИМ writer'ом
value = await _my_cache.get_or_compute(key, lambda: expensive_call())
```

### Где используется ManagedCache (12+ инстансов)
- `src/core/memory/memory_recall.py` — `_recall_cache` для результатов recall
- `src/core/intelligence/routing/pattern_cache.py` — `RouterPlan` кэш
- `src/core/intelligence/pattern_cache.py` — pattern-кэш
- `src/core/intelligence/llm_response_cache.py` — LLM response кэш
- `src/core/infra/settings_cache.py` — `_settings_cache`
- `src/core/actions/stats_cache.py` — `_stats` кэш
- `src/bot/handlers/free_text/_core.py` — `_last_intent_ctx`
- `src/bot/handlers/free_text.py` — `_singalong_search_cache`

## AdaptiveTTLCache (hot-data)

```python
from src.core.cache import AdaptiveTTLCache

_my_adaptive = AdaptiveTTLCache(
    name="my_adaptive",
    base_ttl=3600.0,      # Минимальный TTL (1 час)
    max_ttl=86400.0,      # Максимальный TTL (24 часа)
    max_size=500,
    growth_factor=2.0,    # Множитель роста TTL при каждом hit
)

value = await _my_adaptive.get(key)
await _my_adaptive.set(key, value)
await _my_adaptive.invalidate(key)
```

### Где используется AdaptiveTTLCache
- `src/bot/handlers/free_text.py` — `_url_cache` (1ч → 48ч)
- `src/core/contacts/contact_memory_digest.py` — `_DIGEST_CACHE` (1ч → 24ч)

## TTLCache (простой)

```python
from src.core.memory.ttl_cache import TTLCache

_cache = TTLCache[str, MyType](
    max_size=1000,
    default_ttl=300.0,
    name="my_ttl_cache",
)

value = await _cache.get(key)
await _cache.set(key, value)
await _cache.set(key, value, ttl=60)
await _cache.invalidate(key)
await _cache.clear()
```

### Где используется TTLCache
- `src/core/memory/memory_metrics.py` — `_snapshot_cache` (60s TTL)

## ОБЯЗАТЕЛЬНЫЕ правила

1. **Всегда регистрируй ManagedCache через `cache_manager.register()`** — иначе авто-очистка не работает
2. **Имя кэша — уникальное** (`name="..."`), используется для мониторинга и `/stats`
3. **`asyncio.Lock` уже встроен** — не добавляй свой lock
4. **Не используй `functools.lru_cache` или `@cached_property`** для TTL-данных — они не expire'ятся
5. **Выноси TTL-значения в config.py** если они конфигурируемы (memory.md: анти-паттерны)
6. **Для дорогих вычислений используй `get_or_compute()`** — single-writer pattern, экономит ресурсы
7. **Не забывай `invalidate()` при изменении данных** — stale cache = баги

## Анти-паттерны

- ❌ `dict` с ручным TTL-tracking — используй ManagedCache/TTLCache
- ❌ `functools.lru_cache` для данных с expiry — нет TTL
- ❌ Кэш без регистрации в cache_manager — не чистится, memory leak
- ❌ Кастомный `time.time()` tracking — ManagedCache уже делает это
