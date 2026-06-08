"""Cross-Session Continuity (P2) — сохранение и восстановление контекста между сессиями.

Бот помнит, о чём говорили, даже если пользователь закрыл Telegram и вернулся позже.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.db.models._session import SessionContext
from src.db.repos.session_repo import get_or_create_user
from src.db.session import get_session
from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)

# ── Промпт для лёгкого LLM-сжатия диалога ──────────────────────────────
_SUMMARY_PROMPT = (
    "Сократи этот диалог до 1-2 предложений на русском языке. "
    "Опиши только суть: о чём говорили, что решили, какие задачи обсуждали. "
    "Не добавляй приветствий и лишних слов.\n\n"
    "Диалог:\n{messages}\n\n"
    "Саммари (1-2 предложения):"
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def save_session_context(
    telegram_id: int,
    messages: list[str],
    active_tasks: list[str] | None = None,
) -> None:
    """Сохраняет контекст разговора в конце сессии / каждые N сообщений.

    Выполняет лёгкое LLM-сжатие последних сообщений и сохраняет
    компактный контекст + активные задачи + последние сообщения.

    Args:
        telegram_id: Telegram user ID владельца.
        messages: Список последних сообщений (user + assistant).
        active_tasks: Список активных задач (опционально).
    """
    if not settings.session_context_enabled:
        return

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, telegram_id)
            db_user_id = owner.id

            # ── Ищем существующую запись ──
            result = await session.execute(
                select(SessionContext).where(SessionContext.user_id == db_user_id)
            )
            ctx = result.scalar_one_or_none()

            if ctx is None:
                ctx = SessionContext(user_id=db_user_id)
                session.add(ctx)

            now = _now_utc()
            ctx.last_active_at = now

            # ── Сохраняем последние сообщения (JSON) ──
            raw_json = json.dumps(messages[-3:], ensure_ascii=False)
            ctx.raw_last_messages = raw_json

            # ── Сохраняем активные задачи (JSON) ──
            if active_tasks:
                ctx.active_tasks = json.dumps(active_tasks, ensure_ascii=False)

            # ── Лёгкое LLM-сжатие (best-effort, не блокирует сохранение) ──
            summary = await _summarize_messages(messages, telegram_id, session=session)
            if summary:
                ctx.context_summary = summary

            await session.commit()
            logger.debug(
                "Session context saved for user %d (msg_count=%d, summary=%s)",
                telegram_id,
                len(messages),
                "yes" if summary else "no",
            )
    except SQLAlchemyError:
        logger.debug(
            "Failed to save session context for user %d", telegram_id, exc_info=True
        )
    except Exception:
        logger.debug(
            "Unexpected error saving session context for user %d",
            telegram_id,
            exc_info=True,
        )


async def load_session_context(
    telegram_id: int,
    max_age_hours: int | None = None,
) -> dict | None:
    """Загружает контекст из предыдущей сессии. Возвращает None если истёк.

    Args:
        telegram_id: Telegram user ID владельца.
        max_age_hours: Максимальный возраст контекста в часах.
                       По умолчанию — из settings.session_context_max_age_hours.

    Returns:
        Словарь с ключами: context_summary, active_tasks, pending_questions,
        raw_last_messages, last_active_at. Или None если контекст отсутствует/истёк.
    """
    if not settings.session_context_enabled:
        return None

    max_age = max_age_hours or settings.session_context_max_age_hours

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, telegram_id)
            db_user_id = owner.id

            result = await session.execute(
                select(SessionContext).where(SessionContext.user_id == db_user_id)
            )
            ctx = result.scalar_one_or_none()

            if ctx is None or ctx.last_active_at is None:
                return None

            # ── Проверка срока давности ──
            age = _now_utc() - ctx.last_active_at
            if age.total_seconds() > max_age * 3600:
                logger.debug(
                    "Session context for user %d expired (age=%.1fh, max=%dh)",
                    telegram_id,
                    age.total_seconds() / 3600,
                    max_age,
                )
                return None

            return {
                "context_summary": ctx.context_summary,
                "active_tasks": ctx.active_tasks,
                "pending_questions": ctx.pending_questions,
                "raw_last_messages": ctx.raw_last_messages,
                "last_active_at": ctx.last_active_at.isoformat(),
            }
    except SQLAlchemyError:
        logger.debug(
            "Failed to load session context for user %d", telegram_id, exc_info=True
        )
        return None
    except Exception:
        logger.debug(
            "Unexpected error loading session context for user %d",
            telegram_id,
            exc_info=True,
        )
        return None


async def resume_session(telegram_id: int) -> str | None:
    """Генерирует сообщение-приветствие для вернувшегося пользователя.

    Форматирует контекст предыдущей сессии в читаемое сообщение:
    «👋 С возвращением! Мы говорили о: ... Активные задачи: ... Продолжим?»

    Returns:
        Строка с сообщением или None если контекст отсутствует.
    """
    ctx = await load_session_context(telegram_id)
    if not ctx:
        return None

    summary = ctx.get("context_summary")
    tasks_raw = ctx.get("active_tasks")

    if not summary and not tasks_raw:
        return None  # нечего показывать

    # ── Формируем сообщение ──
    parts: list[str] = ["👋 С возвращением!"]

    if summary:
        parts.append(f"Мы говорили о: {summary}")

    if tasks_raw:
        try:
            tasks = json.loads(tasks_raw)
            if isinstance(tasks, list) and tasks:
                tasks_str = ", ".join(tasks)
                parts.append(f"📋 Активные задачи: {tasks_str}")
        except (json.JSONDecodeError, TypeError):
            pass

    parts.append("\nПродолжим?")
    return "\n".join(parts)


async def _summarize_messages(
    messages: list[str],
    telegram_id: int,
    session=None,
) -> str | None:
    """Лёгкое LLM-сжатие диалога (1-2 предложения).

    Использует лёгкую модель через background purpose.
    Не бросает исключений — в случае ошибки возвращает None.

    Args:
        messages: Список сообщений для сжатия.
        telegram_id: Telegram user ID владельца.
        session: Опциональная существующая сессия БД (избегает double-session).
    """
    if not messages:
        return None

    # Ограничиваем длину: не больше 1500 символов суммарно
    dialog = "\n".join(m[:500] for m in messages[-3:])
    if len(dialog) > 1500:
        dialog = dialog[:1500]

    try:
        from src.llm.router import build_provider

        if session is not None:
            owner = await get_or_create_user(session, telegram_id)
            provider = await build_provider(
                session, owner, purpose="background", task_type=TaskType.SUMMARIZE
            )
            if provider is None:
                # fallback: попробовать main purpose
                provider = await build_provider(
                    session, owner, purpose="main", task_type=TaskType.SUMMARIZE
                )
            if provider is None:
                return None

            prompt = _SUMMARY_PROMPT.format(messages=dialog)
            result = await provider.chat(
                [ChatMessage(role="user", content=prompt)],
                task_type=TaskType.SUMMARIZE,
            )
            summary = (result or "").strip()[:300]
            return summary if summary else None
        else:
            async with get_session() as _session:
                owner = await get_or_create_user(_session, telegram_id)
                provider = await build_provider(
                    _session, owner, purpose="background", task_type=TaskType.SUMMARIZE
                )
                if provider is None:
                    provider = await build_provider(
                        _session, owner, purpose="main", task_type=TaskType.SUMMARIZE
                    )
                if provider is None:
                    return None

                prompt = _SUMMARY_PROMPT.format(messages=dialog)
                result = await provider.chat(
                    [ChatMessage(role="user", content=prompt)],
                    task_type=TaskType.SUMMARIZE,
                )
                summary = (result or "").strip()[:300]
                return summary if summary else None
    except Exception:
        logger.debug(
            "LLM summary failed for user %d session context",
            telegram_id,
            exc_info=True,
        )
        return None
