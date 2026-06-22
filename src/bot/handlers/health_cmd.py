"""Command: /health — system diagnostics."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from src.bot.filters import OwnerOnly


logger = logging.getLogger(__name__)

router = Router()
router.message.filter(OwnerOnly())


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    """Показать статус всех фоновых задач и систем."""
    lines = ["🏥 **Статус системы**\n"]

    # ── 1. Background tasks status ──────────────────────────
    from src.core.infra.task_manager import task_manager

    tasks = task_manager.status()
    lines.append("**Фоновые задачи:**")
    if tasks:
        for name, status in tasks.items():
            icon = "🟢" if status.get("running") else "🔴"
            fail_count = status.get("failures", 0)
            fail_text = f" ({fail_count} ошибок)" if fail_count else ""
            lines.append(f"  {icon} {name}{fail_text}")
    else:
        lines.append("  _(нет зарегистрированных задач)_")

    # ── 2. DB liveness ──────────────────────────────────────
    try:
        from src.db.session import get_session

        async with get_session() as session:
            await session.execute(select(1))
        lines.append("\n🗄️ БД: 🟢 OK")
    except Exception as e:
        lines.append(f"\n🗄️ БД: 🔴 ошибка ({e.__class__.__name__})")

    # ── 3. Qdrant liveness ──────────────────────────────────
    try:
        from src.core.actions.vector_store import get_vector_store

        vs = await get_vector_store()
        if vs is not None and vs._client is not None:
            # Лёгкая проверка: запрос коллекций
            _ = vs._client.get_collections()
            lines.append("🔍 Qdrant: 🟢 OK")
        else:
            lines.append("🔍 Qdrant: 🟡 не инициализирован")
    except Exception as e:
        lines.append(f"🔍 Qdrant: 🔴 ошибка ({e.__class__.__name__})")

    # ── 4. LLM providers ────────────────────────────────────
    try:
        from src.bot.handlers.keys_cmd import _PROVIDER_ORDER

        provider_count = len(_PROVIDER_ORDER)
    except ImportError:
        provider_count = 0
    lines.append(f"\n🤖 LLM провайдеров: {provider_count}")

    # ── 5. Gates (сохранено из предыдущей версии) ───────────
    try:
        from src.core.infra.gating import gates

        status = gates.status
        passed_count = len(status["passed"])
        total_count = status["total"]
        lines.append(f"\n🔧 Зависимости: {passed_count}/{total_count}")
        for name in sorted(status["failed"]):
            lines.append(f"  ❌ {name}")
    except Exception:
        logger.debug("Non-critical error", exc_info=True)

    # ── 6. DB size ──────────────────────────────────────────
    try:
        from src.config import settings

        db = settings.data_dir / "app.db"
        if db.exists():
            lines.append(f"\n📦 Размер БД: {db.stat().st_size / 1024 / 1024:.1f} MB")
    except Exception:
        logger.debug("Non-critical error", exc_info=True)

    # ── 7. Cron jobs ────────────────────────────────────────
    try:
        from sqlalchemy import func, select

        from src.db.models import CronJob
        from src.db.session import get_session

        async with get_session() as session:
            total = await session.scalar(select(func.count()).select_from(CronJob))
            enabled = await session.scalar(
                select(func.count())
                .select_from(CronJob)
                .where(CronJob.enabled.is_(True))
            )
        lines.append(f"\n⏰ Cron: {enabled or 0}/{total or 0} активных")
    except Exception:
        logger.debug("Cron status check failed", exc_info=True)

    # ── 8. Userbot ──────────────────────────────────────────
    try:
        from src.userbot.manager import userbot_manager

        clients = (
            userbot_manager._clients if hasattr(userbot_manager, "_clients") else {}
        )
        connected = sum(1 for c in clients.values() if c.is_connected())
        lines.append(f"👤 Userbot: {connected}/{len(clients)} подключено")
    except Exception:
        logger.debug("Userbot status check failed", exc_info=True)

    await message.answer("\n".join(lines))
