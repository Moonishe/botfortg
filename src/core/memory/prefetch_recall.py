"""Оптимистичный prefetch memory recall (S1-T1).

Запускает recall() в фоне ДО завершения роутинга,
чтобы к моменту, когда роутер решит что нужна память,
результат уже был в кэше — экономим 50-500ms latency.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from src.config import settings
from src.core.events.event_bus import event_bus, MEMORY_MUTATED

# ── Module-level prefetch cache ───────────────────────────────────────
# _prefetch_cache: dict[int, tuple[float, RecallResult]]
#   user_id → (expiry_timestamp, RecallResult)
# TTL: settings.prefetch_recall_ttl (default 5 seconds)
_prefetch_cache: dict[int, tuple[float, Any]] = {}
_prefetch_lock: asyncio.Lock = asyncio.Lock()


async def prefetch_recall(
    user_id: int, message_text: str, limit: int = 10
) -> dict | None:
    """Запустить recall в фоне и закэшировать результат с TTL.

    Вызывается как fire-and-forget task:
        asyncio.create_task(prefetch_recall(...))

    Использует mode="light" — лёгкий режим, достаточный
    для большинства запросов. Если роутинг позже решит
    что нужен deep-режим — результат prefetch не используется.

    Возвращает словарь с ключами:
        memory_context — отформатированный текст для LLM-промпта
        facts_count   — количество найденных фактов
        mode          — режим recall (всегда "light")
        cached_at     — временная метка кэширования

    Если feature-флаг отключён или произошла ошибка — возвращает None
    без выбрасывания исключения.
    """
    if not settings.prefetch_recall_enabled:
        return None

    try:
        from src.core.memory.memory_recall import (
            recall,
            format_recall_for_prompt,
        )

        result = await recall(
            user_id,
            query=message_text[:200],
            limit=limit,
            mode="light",
            include_self=True,
            include_pinned=True,
            include_tasks=True,
            include_deep=False,
        )

        memory_context = format_recall_for_prompt(result)
        facts_count = len(result.facts) if result else 0

        data: dict = {
            "memory_context": memory_context,
            "facts_count": facts_count,
            "mode": "light",
            "cached_at": time.monotonic(),
        }

        ttl = settings.prefetch_recall_ttl
        expiry = time.monotonic() + ttl

        async with _prefetch_lock:
            _prefetch_cache[user_id] = (expiry, data)

        return data

    except Exception:
        # Prefetch — оптимизация, НЕ блокируем основной поток
        return None


async def get_prefetched_recall(user_id: int) -> dict | None:
    """Получить закэшированный результат prefetch recall.

    Проверяет свежесть (TTL) и feature-флаг.
    Возвращает None если:
      - feature-флаг отключён
      - для user_id нет записи в кэше
      - запись протухла (истёк TTL)
    """
    if not settings.prefetch_recall_enabled:
        return None

    async with _prefetch_lock:
        entry = _prefetch_cache.get(user_id)
        if entry is None:
            return None

        expiry, data = entry
        if time.monotonic() > expiry:
            # Протухло — удаляем из кэша
            del _prefetch_cache[user_id]
            return None

        # Возвращаем копию, чтобы вызывающий код не мутировал кэш
        return dict(data)


async def clear_prefetch(user_id: int) -> None:
    """Принудительно очистить кэш prefetch для пользователя (с локом).

    Вызывается при мутации памяти (bump_recall_version),
    чтобы prefetch не возвращал stale-результаты после add/delete/update."""
    async with _prefetch_lock:
        _prefetch_cache.pop(user_id, None)


# ── Event Bus subscriber ────────────────────────────────────────────────
@event_bus.on(MEMORY_MUTATED)
async def _on_memory_mutated(user_id: int, action: str):
    """Сбросить prefetch-кэш при мутации памяти."""
    await clear_prefetch(user_id)
