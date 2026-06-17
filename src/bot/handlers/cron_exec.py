"""Cron execution intent handlers for the Approval Kernel.

Telegram-specific handlers live here; they are registered in free_text/_core.py
INTENT_HANDLERS so the unified confirmation callback can dispatch them.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram.types import Message

from src.core.infra.text_sanitizer import sanitize_html
from src.core.scheduling.cron.delivery import dispatch_cron_job
from src.db.repo import get_or_create_user
from src.db.repos.cron_repo import delete_cron_job, get_cron_job
from src.db.session import get_session

logger = logging.getLogger(__name__)


async def exec_cron_run(params: dict, message: Message) -> dict:
    """Run a cron job immediately and report result.

    Shows a progress card for llm_prompt jobs.
    """
    job_id = int(params.get("job_id", 0))
    telegram_id = int(params.get("user_id", message.from_user.id))
    async with get_session() as session:
        user = await get_or_create_user(session, telegram_id)
        job = await get_cron_job(session, job_id)
        if job is None or job.user_id != user.id:
            return {"ok": False, "error": "Задача не найдена"}

    # Progress card for long-running llm_prompt
    progress_msg = None
    if job.payload_type == "llm_prompt":
        progress_msg = await message.answer("⏳ Генерирую LLM-ответ для cron-задачи…")

    try:
        async with asyncio.timeout(60.0):
            result = await dispatch_cron_job(
                job_id=job.id,
                user_id=user.id,
                payload_type=job.payload_type,
                payload=job.payload,
                channel=job.channel,
            )
    except TimeoutError:
        logger.warning("cron run timed out for job #%d", job_id)
        return {"ok": False, "error": "Время выполнения задачи истекло"}
    except Exception as exc:
        logger.exception("cron run failed for job #%d", job_id)
        return {"ok": False, "error": str(exc)}
    finally:
        if progress_msg is not None:
            try:
                await progress_msg.delete()
            except Exception as exc:
                logger.debug("failed to delete progress message: %s", exc)

    if result.get("success"):
        output = result.get("output", "")[:400]
        await message.answer(
            f"✅ Cron-задача <b>#{job_id}</b> выполнена:\n{sanitize_html(output)}",
            parse_mode="HTML",
        )
        return {"ok": True}
    return {"ok": False, "error": result.get("output", "Ошибка выполнения")}


async def exec_cron_delete(params: dict, message: Message) -> dict:
    """Delete a cron job."""
    job_id = int(params.get("job_id", 0))
    telegram_id = int(params.get("user_id", message.from_user.id))
    async with get_session() as session:
        user = await get_or_create_user(session, telegram_id)
        job = await get_cron_job(session, job_id)
        if job is None or job.user_id != user.id:
            return {"ok": False, "error": "Задача не найдена"}
        ok = await delete_cron_job(session, job_id)
        await session.commit()

    if ok:
        await message.answer(
            f"🗑 Cron-задача <b>#{job_id}</b> удалена.", parse_mode="HTML"
        )
        return {"ok": True}
    return {"ok": False, "error": "Не удалось удалить задачу"}
