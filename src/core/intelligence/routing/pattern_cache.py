"""Кэш маршрутных решений (S2-T1) — избегает повторных classify+route вычислений.

RouteCache хранит (normalized_text_hash, user_id) → RouterPlan.
При cache hit — пропускаем все стадии make_plan() и возвращаем готовый план.
Кэшируются только планы где response_mode != "maestro" и prefetched_context не использовался.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import TYPE_CHECKING

from src.core.cache.manager import ManagedCache, cache_manager
from src.config import settings
from src.core.events.event_bus import event_bus, MEMORY_MUTATED

if TYPE_CHECKING:
    from .planner import RouterPlan

logger = logging.getLogger(__name__)

# Глобальная нормализация: пунктуация + lowercase + первые 100 символов
_RE_NORMALIZE = re.compile(r"[^\w\s]", re.UNICODE)


class RouteCache:
    """In-memory TTL-кэш: (normalized_text_hash, user_id) → RouterPlan.

    Ключ строится из нормализованного текста (без пунктуации, lowercase, до 100 символов)
    и user_id.  Использует MD5 для детерминированного короткого хеша.

    TTL по умолчанию 300 сек (5 минут) — обратная совместимость со стандартными
    cache_ttl из classify_risk.
    """

    def __init__(self, max_size: int = 1000, ttl: float = 300.0):
        self._max_size = max_size
        self._ttl = ttl
        self._cache: ManagedCache[str, "RouterPlan"] = cache_manager.register(
            ManagedCache(
                name="route_plans",
                max_size=max_size,
                default_ttl=ttl,
            )
        )
        self._hits: int = 0
        self._misses: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def cache_key(text: str, user_id: int) -> str:
        """Нормализовать текст → хеш для ключа кэша.

        Нормализация:
          - strip + lowercase
          - удаление пунктуации (_RE_NORMALIZE)
          - обрезка до 100 символов
          - MD5 хеш (16 байт → 32 hex символов)
        """
        normalized = text.strip().lower()
        normalized = _RE_NORMALIZE.sub("", normalized)[:100]
        digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()
        return f"rc:{user_id}:{digest}"

    async def get(self, text: str, user_id: int) -> "RouterPlan | None":
        """Получить закэшированный RouterPlan.

        Возвращает None если:
          - запись отсутствует
          - TTL истёк (ManagedCache удаляет автоматически)
          - route_cache_enabled == False
        """
        if not settings.route_cache_enabled:
            return None
        key = self.cache_key(text, user_id)
        plan = await self._cache.get(key)
        if plan is not None:
            self._hits += 1
            logger.debug("RouteCache HIT: key=%s mode=%s", key, plan.response_mode)
            # Обновляем elapsed_ms для метрик (план был создан ранее)
            plan.metrics["route_cache_hit"] = True
            return plan
        self._misses += 1
        return None

    async def set(
        self, text: str, user_id: int, plan: "RouterPlan", *, ttl: float | None = None
    ) -> None:
        """Сохранить RouterPlan в кэш.

        План кэшируется только если:
          - response_mode != "maestro" (глубокие планы не кэшируем)
          - prefetched_context НЕ использовался
          - route_cache_enabled == True
        """
        if not settings.route_cache_enabled:
            return
        # Не кэшируем deep-режимы
        if plan.response_mode == "maestro":
            return
        # Не кэшируем если использовался prefetch (результат зависит от внешнего состояния)
        if plan.metrics.get("prefetch_hit"):
            return
        key = self.cache_key(text, user_id)
        entry_ttl = ttl if ttl is not None else self._ttl
        await self._cache.set(key, plan, ttl=entry_ttl)
        logger.debug(
            "RouteCache SET: key=%s mode=%s ttl=%.0f",
            key,
            plan.response_mode,
            entry_ttl,
        )

    async def invalidate_user(self, user_id: int) -> None:
        """Clear cached routes for a user (called on memory mutation).

        ManagedCache не поддерживает инвалидацию по префиксу, поэтому
        очищаем весь кэш целиком.  Это консервативно, но безопасно —
        свежие маршруты перестроятся на следующем холодном промахе.

        Приемлемо для single-user бота с <1000 записей;
        для multi-user сценария нужна миграция на префиксную эвикцию.
        """
        await self._cache.clear()
        logger.debug("RouteCache: full clear for user=%d", user_id)

    async def clear(self) -> None:
        """Полная очистка кэша."""
        await self._cache.clear()
        self._hits = 0
        self._misses = 0
        logger.debug("RouteCache: cleared")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "entries": await self._cache.size(),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
            "ttl": self._ttl,
        }


# Глобальный синглтон
route_cache = RouteCache(max_size=1000, ttl=300.0)


# ── Event Bus subscriber ────────────────────────────────────────────────
@event_bus.on(MEMORY_MUTATED)
async def _on_memory_mutated(user_id: int, action: str):
    """Инвалидировать RouteCache при мутации памяти."""
    await route_cache.invalidate_user(user_id)
