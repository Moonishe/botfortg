"""Per-user rate-limit для free-text LLM-запросов и команд.


Предотвращает спам-запросы к LLM и дорогим командам.


Поддерживает два режима:


- Базовый: 1 запрос в 3 секунды на пользователя (без аргументов).


- Sliding-window: N запросов за T секунд (с аргументами window/max_requests).


# NOTE: in-memory rate limits reset on restart. Acceptable for single-user bot.


"""

from __future__ import annotations


import asyncio


import logging


import time


from typing import Any


logger = logging.getLogger(__name__)


# Запись: {telegram_id: (last_request_time, lock)}


_last_request: dict[int, tuple[float, asyncio.Lock]] = {}


# Скользящее окно: {telegram_id: [timestamp, ...]}


_request_history: dict[int, list[float]] = {}


_locks: dict[int, asyncio.Lock] = {}


_lock_init_lock: asyncio.Lock = asyncio.Lock()


_counter_lock: asyncio.Lock = asyncio.Lock()


_MIN_INTERVAL: float = 3.0  # секунд между запросами одного пользователя


_CLEANUP_TTL: float = 60.0  # удаляем записи старше 1 минуты


_HARD_TTL: float = 3600.0  # жёсткий лимит — удаляем записи старше 1 часа


_LOCK_CLEANUP_INTERVAL: int = 1000  # каждые N вызовов check_rate_limit чистим _locks


_check_call_counter: int = 0


_last_cleanup: float = 0.0  # время последнего _cleanup_stale (для троттлинга)


async def _get_user_lock(telegram_id: int) -> asyncio.Lock:
    """Атомарно получить или создать блокировку для пользователя.


    Использует double-check с _lock_init_lock для защиты от гонки:


    два сопрограммы не могут одновременно создать два разных asyncio.Lock


    для одного uid (что сломало бы взаимное исключение).


    """

    if telegram_id in _locks:
        return _locks[telegram_id]

    async with _lock_init_lock:
        if telegram_id not in _locks:
            _locks[telegram_id] = asyncio.Lock()

    return _locks[telegram_id]


async def check_rate_limit(
    telegram_id: int,
    window: float | None = None,
    max_requests: int | None = None,
) -> bool:
    """Проверить rate-limit для пользователя.


    Args:


        telegram_id: ID пользователя Telegram.


        window: Размер окна в секундах (для sliding-window режима).


        max_requests: Максимальное число запросов в окне.


    Returns:


        True если запрос разрешён, False если нужно подождать.


    Поведение:


        - Без аргументов: классический lock, 1 запрос в 3 секунды.


        - С window + max_requests: sliding-window, до max_requests запросов за window секунд.


    """

    global _check_call_counter, _last_cleanup

    now = time.monotonic()

    lock = await _get_user_lock(telegram_id)

    async with lock:
        # Периодическая очистка устаревших записей (не чаще раза в минуту)

        if now - _last_cleanup > 60.0:
            _last_cleanup = now

            await _cleanup_stale(now)

        # Периодическая очистка _locks от неактивных блокировок

        _check_call_counter += 1

        if _check_call_counter >= _LOCK_CLEANUP_INTERVAL:
            _check_call_counter = 0

            _cleanup_locks(now)

        # Sliding-window режим

        if window is not None and max_requests is not None:
            async with _counter_lock:
                history = _request_history.get(telegram_id, [])

                cutoff = now - window

                # Отсекаем устаревшие

                history = [t for t in history if t > cutoff]

                if len(history) >= max_requests:
                    _request_history[telegram_id] = history

                    return False

                history.append(now)

                _request_history[telegram_id] = history

                return True

        # Классический режим (1 запрос в 3 секунды)

        if telegram_id in _last_request:
            last_time, _ = _last_request[telegram_id]

            elapsed = now - last_time

            if elapsed < _MIN_INTERVAL:
                return False

        _last_request[telegram_id] = (now, lock)

        return True


async def _cleanup_stale(now: float) -> None:
    """Удалить записи старше _CLEANUP_TTL (+ 1-hour hard TTL для _last_request).


    Lock cleanup вынесен в _cleanup_locks — только она проверяет lock.locked()


    (синхронно, без yield-точек) и поэтому безопасно удаляет _locks.


    Удаление активного asyncio.Lock из _locks сломало бы mutual exclusion:


    новый coroutine создал бы свежий незаблокированный lock и вошёл бы в


    критическую секцию одновременно с держателем старого lock'а.


    """

    # Cleanup _last_request (TTL)

    for uid in list(_last_request):
        t, _ = _last_request[uid]

        if now - t > _CLEANUP_TTL:
            del _last_request[uid]

    # Hard TTL — удаляем _last_request старше 1 часа (защита от утечек)

    # _locks не трогаем — _cleanup_locks разрулит безопасно

    for uid in list(_locks):
        if uid in _last_request:
            t, _ = _last_request[uid]

            if now - t > _HARD_TTL:
                del _last_request[uid]

    # Cleanup _request_history: remove entries with no recent timestamps

    # Защищено _counter_lock — concurrent sliding-window операции не увидят

    # частично очищенный history

    async with _counter_lock:
        for uid in list(_request_history):
            history = _request_history[uid]

            # Remove timestamps older than hard TTL

            _request_history[uid] = [t for t in history if now - t < _HARD_TTL]

            if not _request_history[uid]:
                del _request_history[uid]

    # Компенсация: чистим _locks здесь же (раз в минуту), а не только

    # по _LOCK_CLEANUP_INTERVAL — без этого неиспользуемые lock'и копились бы

    # до 1000 вызовов check_rate_limit.

    _cleanup_locks(now)


def _cleanup_locks(now: float) -> None:
    """Удалить блокировки, которые никто не держит и не использовались давно.


    NOTE: TOCTOU между lock.locked() и del бенигна в asyncio —


    функция синхронная (без await), поэтому между проверкой и удалением


    не может выполниться другой coroutine.


    """

    for uid in list(_locks):
        lock = _locks[uid]

        if not lock.locked():
            # Если нет записи в _last_request — блокировка не используется

            if uid not in _last_request:
                del _locks[uid]

            else:
                t, _ = _last_request[uid]

                if now - t > _CLEANUP_TTL:
                    del _locks[uid]


async def with_rate_limit(
    kwargs: dict[str, Any], tool_name: str = "tool"
) -> dict[str, str] | None:
    """Проверить rate limit для MCP-инструмента.


    Извлекает telegram_id из kwargs["user"] и проверяет rate-limit.


    Возвращает {"error": ...} при превышении, None если можно продолжать.


    Args:


        kwargs: kwargs MCP-инструмента, содержащие ключ "user".


        tool_name: Имя инструмента для логирования (не используется в ответе, только для будущего).


    Returns:


        Словарь с ошибкой при превышении лимита, None если запрос разрешён.


    """

    _user_val = kwargs.get("user") or 0

    telegram_id = int(getattr(_user_val, "telegram_id", _user_val))

    if telegram_id and not await check_rate_limit(telegram_id):
        return {"error": "Слишком много запросов. Подождите немного."}

    return None
