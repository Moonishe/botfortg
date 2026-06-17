---
name: overlap-guard
description: Background asyncio loop overlap guard — предотвращает запуск дубликатов фоновых циклов через asyncio.Lock. Использовать при создании/рефакторинге любого фонового asyncio-цикла (while True + create_task).
---

# Overlap Guard для фоновых asyncio-циклов

Ты создаёшь или рефакторишь фоновый asyncio-цикл. **Всегда** добавляй overlap guard.

## Правило

Каждый фоновый цикл `while True` с `asyncio.create_task` или `asyncio.sleep` ДОЛЖЕН иметь overlap guard — иначе `create_task` в цикле запускает дубликат пока предыдущий инстанс не завершился.

Исправлено 11+ багов этого типа (источник: commit `d215432`, memory.md AD-008).

## Стандартный паттерн (asyncio.Lock)

```python
import asyncio

_overlap_guard = asyncio.Lock()

async def my_background_loop() -> None:
    """Фоновый цикл с overlap guard."""
    try:
        while True:
            if _overlap_guard.locked():
                await asyncio.sleep(TICK_SECONDS)
                continue

            async with _overlap_guard:
                try:
                    await _do_work()
                except Exception:
                    logger.exception("my_background_loop: work failed")

            await asyncio.sleep(TICK_SECONDS)
    except asyncio.CancelledError:
        logger.info("my_background_loop: cancelled")
        raise
```

## Паттерн для класса (self._overlap_guard)

```python
class MyScheduler:
    def __init__(self) -> None:
        self._overlap_guard = asyncio.Lock()

    async def run(self) -> None:
        while True:
            if self._overlap_guard.locked():
                await asyncio.sleep(TICK)
                continue
            async with self._overlap_guard:
                try:
                    await self._tick()
                except Exception:
                    logger.exception("tick failed")
            await asyncio.sleep(TICK)
```

## Паттерн с try/finally (для acquire/release вручную)

```python
_overlap_guard = asyncio.Lock()

async def my_loop() -> None:
    while True:
        if _overlap_guard.locked():
            await asyncio.sleep(TICK)
            continue
        await _overlap_guard.acquire()
        try:
            await _do_work()
        except Exception:
            logger.exception("work failed")
        finally:
            _overlap_guard.release()
        await asyncio.sleep(TICK)
```

## Чего НЕ делать

- ❌ **НЕ использовать** `threading.Lock` — блокирует event loop → deadlock (13+ race-багов, memory.md AD-011)
- ❌ **НЕ пропускать** overlap guard для новых циклов — даже если "цикл быстрый"
- ❌ **НЕ использовать** bare `except: pass` — всегда `except Exception: logger.exception(...)`
- ❌ **НЕ хардкодить** `asyncio.sleep(X)` — выносить `TICK` в config.py (memory.md: анти-паттерны)

## Проверка

После создания фонового цикла проверь:
1. `_overlap_guard = asyncio.Lock()` объявлен (модульный уровень или `self`)
2. `if _overlap_guard.locked(): await asyncio.sleep(...); continue` перед входом
3. `async with _overlap_guard:` или `try/finally` с `acquire/release`
4. `CancelledError` обрабатывается (graceful shutdown)
5. Все `TICK`-интервалы из config.py, не хардкод

## Где применяется (16+ инстансов в проекте)

- `src/core/scheduling/cron/scheduler.py` — `self._overlap_guard`
- `src/core/scheduling/weekly_summarizer.py` — `_overlap_guard`
- `src/core/scheduling/weekly_digest.py` — `_overlap_guard`
- `src/core/scheduling/smart_digest.py` — `_overlap_guard`
- `src/core/scheduling/sleep_tracker.py` — `_overlap_guard`
- `src/core/scheduling/reminders.py` — `_overlap_guard`
- `src/core/scheduling/proactive_nudge.py` — `_overlap_guard`
- `src/core/scheduling/proactive_chat_analyzer.py` — `_overlap_guard`
- `src/core/scheduling/proactive_briefing.py` — `_overlap_guard`
- `src/core/scheduling/notification_queue.py` — `_notification_queue_guard`
- `src/core/scheduling/news.py` — `_overlap_guard`
- `src/core/scheduling/habit_tracker.py` — `_overlap_guard`
- `src/core/scheduling/follow_up.py` — `_overlap_guard`
- `src/core/scheduling/dream_cycle.py` — `_overlap_guard`
- `src/core/scheduling/digest.py` — `_overlap_guard`
- `src/core/scheduling/avito.py` — `_overlap_guard`
