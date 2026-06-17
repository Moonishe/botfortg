"""CronScheduler — ядро системы повторяющихся задач.

Основной цикл:
1. Tick (каждые 15 секунд) → проверяет due-задачи через БД
2. Dispatch → отправляет каждую due-задачу через delivery
3. Advance → обновляет next_run_at через croniter
4. Bulk disable → отключает просроченные задачи

Интеграция с task_manager:
    @task_manager.task("cron-scheduler")
    async def cron_scheduler_loop():
        await cron_scheduler.run()
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from src.core.scheduling.cron.parser import get_next_run, validate_cron
from src.core.scheduling.cron.delivery import close_delivery_bot, dispatch_cron_job

logger = logging.getLogger(__name__)

# Как часто проверять due-задачи (секунд)
CRON_TICK_SECONDS = 15

# Максимальное количество due-задач за один tick
MAX_DUE_PER_TICK = 50

# Пауза между dispatch-ами (чтобы не флудить)
DISPATCH_PAUSE = 1.0


class CronScheduler:
    """Планировщик cron-задач.

    Singleton. Используется через модульный экземпляр ``cron_scheduler``.
    """

    def __init__(self) -> None:
        self._overlap_guard = asyncio.Lock()

    async def run(self) -> None:
        """Бесконечный цикл планировщика.

        Регистрируется в ``task_manager`` через декоратор @task_manager.task.
        """
        logger.info(
            "CronScheduler: запущен (tick=%ds, max_due=%d)",
            CRON_TICK_SECONDS,
            MAX_DUE_PER_TICK,
        )
        try:
            while True:
                if self._overlap_guard.locked():
                    await asyncio.sleep(CRON_TICK_SECONDS)
                    continue

                async with self._overlap_guard:
                    try:
                        await self._tick()
                    except Exception:
                        logger.exception("CronScheduler: tick failed")

                await asyncio.sleep(CRON_TICK_SECONDS)
        except asyncio.CancelledError:
            logger.info("CronScheduler: graceful shutdown (CancelledError)")
            try:
                await close_delivery_bot()
            except Exception:
                logger.exception("CronScheduler: ошибка при закрытии delivery Bot")
            raise

    async def _tick(self) -> None:
        """Один тик планировщика: проверить и выполнить due-задачи."""
        from src.db.repos.cron_repo import (
            get_due_jobs,
            bulk_disable_expired,
        )

        # Шаг 1: Отключить просроченные задачи
        try:
            disabled = await self._run_in_session(bulk_disable_expired)
            if disabled > 0:
                logger.info("CronScheduler: отключено %d просроченных задач", disabled)
        except Exception:
            logger.exception("CronScheduler: bulk_disable_expired failed")

        # Шаг 2: Получить due-задачи (с лимитом на уровне SQL)
        due_jobs = await self._run_in_session(get_due_jobs, limit=MAX_DUE_PER_TICK)
        if not due_jobs:
            return

        logger.info(
            "CronScheduler: %d due-задач(и) для выполнения",
            len(due_jobs),
        )

        # Шаг 3: Последовательно выполнить каждую
        for job in due_jobs:
            try:
                await self._execute_job(job)
            except Exception:
                logger.exception(
                    "CronScheduler: фатальная ошибка выполнения задачи #%d",
                    job.id,
                )
            await asyncio.sleep(DISPATCH_PAUSE)

    @staticmethod
    async def _resolve_llm_prompt_payload(
        user_id: int,
        payload: str | None,
    ) -> str:
        """Generate text for a headless cron ``llm_prompt`` task.

        Uses the ``cron_headless`` route profile so the LLM sees only
        safe tools and cannot send messages or execute code.
        """
        parsed: dict[str, Any] = {}
        if payload:
            try:
                parsed = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                parsed = {"prompt": payload}

        prompt = parsed.get("prompt") or parsed.get("text") or ""
        if not prompt:
            return json.dumps({"text": "Пустой prompt для llm_prompt-задачи"})

        try:
            from src.db.session import get_session
            from src.db.repos.session_repo import get_or_create_user
            from src.llm import build_provider
            from src.core.actions.tool_registry import tool_registry

            async with get_session() as session:
                user = await get_or_create_user(session, user_id)
                provider = await build_provider(session, user, purpose="main")
                if provider is None:
                    return json.dumps(
                        {"text": "LLM-провайдер недоступен для cron-задачи"}
                    )

                system = (
                    "Ты — авто-помощник для cron-задачи. "
                    "Отвечай кратко и по делу. "
                    "Тебе доступны только безопасные инструменты "
                    "без отправки сообщений и выполнения кода.\n\n"
                    + tool_registry.format_tools_for_route(
                        task_context=prompt,
                        route="cron_headless",
                        available_only=True,
                    )
                    + "\n\nОтветь на запрос пользователя."
                )
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ]
                text = await provider.chat(messages)
                return json.dumps({"text": text})
        except Exception:
            logger.exception(
                "CronScheduler: ошибка генерации LLM для llm_prompt задачи"
            )
            return json.dumps({"text": "Ошибка генерации LLM для cron-задачи"})

    async def _execute_job(self, job) -> None:
        """Выполнить одну cron-задачу.

        Логика:
        1. Re-check (FOR UPDATE): задача всё ещё enabled и не исчерпала max_runs.
        2. Рассчитать next_run_at через croniter.
        3. Для ``llm_prompt`` сгенерировать текст через LLM с route="cron_headless".
        4. Отправить через delivery (вне транзакции БД).
        5. Атомарно обновить задачу (run_count, last_run_at, next_run_at).
        """
        from src.db.repos.cron_repo import advance_job, get_cron_job_for_update
        from src.db.session import get_session

        # 1. Re-check: получить свежее состояние задачи с FOR UPDATE
        async with get_session() as session:
            fresh = await get_cron_job_for_update(session, job.id)
            if fresh is None:
                logger.warning(
                    "CronScheduler: задача #%d не найдена в БД, пропускаем",
                    job.id,
                )
                return
            if not fresh.enabled:
                logger.info(
                    "CronScheduler: задача #%d '%s' отключена, пропускаем",
                    job.id,
                    fresh.name,
                )
                return
            if fresh.max_runs > 0 and fresh.run_count >= fresh.max_runs:
                logger.info(
                    "CronScheduler: задача #%d '%s' исчерпала лимит (%d/%d), "
                    "пропускаем",
                    job.id,
                    fresh.name,
                    fresh.run_count,
                    fresh.max_runs,
                )
                return

            # 2. Рассчитать следующее время — validate + get_next в одном проходе
            next_run = self._calc_next_run(
                job.id, fresh.name, fresh.cron_expression, fresh.timezone
            )

            # Снимаем атрибуты ДО коммита (после коммита объект expired)
            dispatch_user_id = fresh.user_id
            dispatch_payload_type = fresh.payload_type
            dispatch_payload = fresh.payload
            dispatch_channel = fresh.channel

            # Отпускаем FOR UPDATE перед доставкой (dispatch не трогает БД)
            await session.commit()

        # 3. Для llm_prompt сгенерировать текст через LLM с headless toolset
        if dispatch_payload_type == "llm_prompt":
            dispatch_payload = await self._resolve_llm_prompt_payload(
                dispatch_user_id, dispatch_payload
            )

        # 4. Отправить (вне транзакции, чтобы не держать лок)
        result = await dispatch_cron_job(
            job_id=job.id,
            user_id=dispatch_user_id,
            payload_type=dispatch_payload_type,
            payload=dispatch_payload,
            channel=dispatch_channel,
        )

        if result.get("success"):
            logger.info(
                "CronScheduler: задача #%d '%s' выполнена — %s",
                job.id,
                job.name,
                result.get("output", "")[:100],
            )
        else:
            logger.warning(
                "CronScheduler: задача #%d '%s' не выполнена — %s",
                job.id,
                job.name,
                result.get("output", ""),
            )

        # 5. Продвинуть задачу (новая сессия; get_session сам коммитит)
        try:
            async with get_session() as session:
                updated = await advance_job(session, job.id, next_run)
                if updated is None:
                    logger.info(
                        "CronScheduler: задача #%d '%s' — advance_job не обновил "
                        "(отключена или лимит исчерпан между re-check и advance)",
                        job.id,
                        job.name,
                    )
        except Exception:
            logger.exception(
                "CronScheduler: задача #%d '%s' — не удалось продвинуть "
                "(dispatch выполнен, но БД не обновлена — возможен повтор)",
                job.id,
                job.name,
            )

    @staticmethod
    def _calc_next_run(
        job_id: int,
        name: str,
        cron_expression: str,
        timezone: str = "UTC",
    ) -> datetime | None:
        """Рассчитать next_run, если cron-выражение валидно.

        Избегает двойного создания croniter (validate + get_next_run)
        — get_next_run сам валидирует выражение и возвращает None при ошибке.
        """
        next_run = get_next_run(cron_expression, tz_str=timezone)
        if next_run is None:
            logger.warning(
                "CronScheduler: задача #%d '%s' — невалидный cron: %r",
                job_id,
                name,
                cron_expression,
            )
        return next_run

    async def _run_in_session(self, fn, *args, **kwargs):
        """Выполнить функцию в отдельной сессии БД.

        Примечание: get_session() сам коммитит при выходе из async with,
        явный session.commit() не нужен и приводит к двойному коммиту.
        """
        from src.db.session import get_session

        async with get_session() as session:
            result = await fn(session, *args, **kwargs)
            return result

    async def create_and_schedule(
        self,
        user_id: int,
        name: str,
        cron_expression: str,
        payload_type: str = "message",
        payload: dict | None = None,
        **kwargs,
    ) -> dict:
        """Создать cron-задачу и сразу рассчитать next_run_at.

        Args:
            user_id: ID пользователя-владельца.
            name: Название задачи.
            cron_expression: 5-польное cron-выражение.
            payload_type: Тип действия.
            payload: Параметры действия.
            **kwargs: Дополнительные поля (description, timezone, channel и др.)

        Returns:
            {"success": bool, "job_id": int|None, "next_run": str|None,
             "error": str|None}
        """
        # Валидация cron-выражения
        if not validate_cron(cron_expression):
            return {
                "success": False,
                "error": f"Невалидное cron-выражение: {cron_expression}",
                "job_id": None,
                "next_run": None,
            }

        # Расчёт первого next_run_at
        tz = kwargs.get("timezone", "UTC")
        next_run = get_next_run(cron_expression, tz_str=tz)

        # Сохранение в БД (get_session сам коммитит при выходе)
        from src.db.repos.cron_repo import create_cron_job
        from src.db.session import get_session

        async with get_session() as session:
            try:
                job = await create_cron_job(
                    session=session,
                    user_id=user_id,
                    name=name,
                    cron_expression=cron_expression,
                    payload_type=payload_type,
                    payload=payload,
                    next_run_at=next_run,
                    **kwargs,
                )
            except Exception as e:
                logger.exception("CronScheduler: не удалось создать задачу")
                return {
                    "success": False,
                    "error": str(e),
                    "job_id": None,
                    "next_run": None,
                }

        return {
            "success": True,
            "job_id": job.id,
            "next_run": next_run.isoformat() if next_run else None,
        }


# ── Глобальный singleton ───────────────────────────────────────────────────
cron_scheduler = CronScheduler()


# ── Регистрация в task_manager ────────────────────────────────────────────
# Импорт модуля автоматически регистрирует фоновый цикл через декоратор.


from src.core.infra.task_manager import task_manager


@task_manager.task("cron-scheduler")
async def _cron_scheduler_loop() -> None:
    """Точка входа для task_manager: запускает бесконечный цикл."""
    await cron_scheduler.run()
