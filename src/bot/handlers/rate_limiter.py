"""Per-user rate-limit для free-text LLM-запросов.

Предотвращает спам-запросы к LLM: 1 запрос в 3 секунды на пользователя.
Не блокирует команды (они без LLM).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

# Запись: {telegram_id: (last_request_time, lock)}
_last_request: dict[int, tuple[float, asyncio.Lock]] = {}
_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_MIN_INTERVAL: float = 3.0  # секунд между запросами одного пользователя
_CLEANUP_TTL: float = 60.0  # удаляем записи старше 1 минуты
_HARD_TTL: float = 3600.0  # жёсткий лимит — удаляем записи старше 1 часа
_LOCK_CLEANUP_INTERVAL: int = 1000  # каждые N вызовов check_rate_limit чистим _locks
_check_call_counter: int = 0


async def check_rate_limit(telegram_id: int) -> bool:
    """Проверить rate-limit для пользователя.

    Возвращает True если запрос разрешён, False если нужно подождать.
    Автоматически чистит устаревшие записи.
    """
    global _check_call_counter
    now = time.monotonic()
    lock = _locks[telegram_id]

    async with lock:
        # Периодическая очистка устаревших записей
        _cleanup_stale(now)

        # Периодическая очистка _locks от неактивных блокировок
        _check_call_counter += 1
        if _check_call_counter >= _LOCK_CLEANUP_INTERVAL:
            _check_call_counter = 0
            _cleanup_locks(now)

        if telegram_id in _last_request:
            last_time, _ = _last_request[telegram_id]
            elapsed = now - last_time
            if elapsed < _MIN_INTERVAL:
                return False

        _last_request[telegram_id] = (now, lock)
        return True


def _cleanup_stale(now: float) -> None:
    """Удалить записи старше _CLEANUP_TTL (+ чистит _locks и 1-hour hard TTL)."""
    for uid in list(_last_request.keys()):
        t, _ = _last_request[uid]
        if now - t > _CLEANUP_TTL:
            del _last_request[uid]
            _locks.pop(uid, None)

    # Hard TTL — удаляем всё, что старше 1 часа (защита от утечек)
    for uid in list(_locks.keys()):
        if uid not in _last_request:
            lock = _locks[uid]
            if not lock.locked():
                _locks.pop(uid, None)
        else:
            t, _ = _last_request[uid]
            if now - t > _HARD_TTL:
                del _last_request[uid]
                _locks.pop(uid, None)


def _cleanup_locks(now: float) -> None:
    """Удалить блокировки, которые никто не держит и не использовались давно."""
    for uid in list(_locks.keys()):
        lock = _locks[uid]
        if not lock.locked():
            # Если нет записи в _last_request — блокировка не используется
            if uid not in _last_request:
                del _locks[uid]
            else:
                t, _ = _last_request[uid]
                if now - t > _CLEANUP_TTL:
                    del _locks[uid]
