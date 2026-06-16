"""Callback handlers for the skills inline panel.

Callback registration and handler logic.
Depends on ``skills_ui`` for presentation and ``skills_data`` for data access.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.core.intelligence.skills_curator import (
    approve_skill,
    auto_approve_high_confidence,
    curator_stats,
    list_proposed,
    promote_to_global,
    reject_skill,
)
from src.db.models import Skill
from src.db.repo import get_or_create_user, set_skill_enabled
from src.db.session import get_session
from .skills_data import (
    _fetch_skills_by_status,
    _get_skill_by_id,
    _perform_rollback,
)
from .skills_ui import (
    CALLBACK_PREFIX,
    _STATUS_LABELS,
    _edit_callback_message,
    _format_skill_detail,
    _skill_detail_keyboard,
    _skill_list_keyboard,
    _skills_summary,
)

router = Router(name="skills_callbacks")
router.callback_query.filter(OwnerOnly())

logger = logging.getLogger(__name__)


# ── Callback query handlers ──────────────────────────────────────────



# ── Callback helpers ─────────────────────────────────────────────────


def _parse_callback_skill_id(callback: CallbackQuery) -> int | None:
    """Parse skill_id from callback data like 'skills:action:<id>'.

    Returns None when callback data is malformed or the id is not a positive
    integer; callers should answer the callback with an error message.
    """
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 3:
        return None
    try:
        skill_id = int(parts[2])
    except ValueError:
        return None
    if skill_id <= 0:
        return None
    return skill_id


async def _skill_mutation(
    callback: CallbackQuery,
    action: Callable[[Any, Any, Skill], Awaitable[bool | None]],
    success_message: str | Callable[[Skill], str],
    error_message: str,
    *,
    pre_check: Callable[[Skill], tuple[bool, str]] | None = None,
) -> None:
    """Run a skill mutation, refresh the ORM object and update the detail view.

    Args:
        callback: The aiogram callback query.
        action: Async callable taking ``(session, owner, skill)`` and returning
            an optional boolean success flag. Exceptions are caught and reported.
        success_message: Static text or a callable receiving the refreshed skill.
        error_message: User-facing text shown when the action returns False or
            raises an exception.
        pre_check: Optional callable receiving the loaded skill and returning
            ``(ok, reason)``. When ``ok`` is False, ``reason`` is shown to the
            user and the action is skipped.
    """
    skill_id = _parse_callback_skill_id(callback)
    if skill_id is None:
        await callback.answer("Ошибка данных", show_alert=True)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        skill = await _get_skill_by_id(session, owner, skill_id)
        if skill is None:
            await callback.answer("Skill не найден", show_alert=True)
            return

        if pre_check is not None:
            ok, reason = pre_check(skill)
            if not ok:
                await callback.answer(reason, show_alert=True)
                return

        try:
            result = await action(session, owner, skill)
        except Exception as e:
            logger.warning("skill mutation failed: %s", e, exc_info=True)
            await callback.answer(error_message, show_alert=True)
            return

        if result is False:
            await callback.answer(error_message, show_alert=True)
            return

        # Actions may mutate a different ORM instance; reload and detach the one
        # we display so it stays usable after the session closes.
        await session.refresh(skill)
        session.expunge(skill)

    msg = success_message(skill) if callable(success_message) else success_message
    await callback.answer(msg)
    await cb_skill_detail(callback, skip_answer=True, skill=skill)

@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:page:"))
async def cb_skills_page(callback: CallbackQuery) -> None:
    """Render a paginated skill list for a status tab."""
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    status = parts[2]
    if status not in _STATUS_LABELS:
        status = "all"
    try:
        page = max(0, int(parts[3]))
    except ValueError:
        page = 0

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        page_skills, total = await _fetch_skills_by_status(session, owner, status, page)

    text = f"<b>Skills</b> — {_STATUS_LABELS[status]} (всего {total}):\n"
    text += _skills_summary(page_skills)

    await callback.answer()
    await _edit_callback_message(
        callback,
        text,
        reply_markup=_skill_list_keyboard(page_skills, status, page, total),
    )


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:detail:"))
async def cb_skill_detail(
    callback: CallbackQuery,
    *,
    skip_answer: bool = False,
    skill: Skill | None = None,
) -> None:
    """Show skill details with action buttons.

    Args:
        callback: The callback query to respond to.
        skip_answer: If True, do not call ``callback.answer()`` at the end.
            Set this when calling from another handler that already answers.
        skill: Optional already-loaded skill. If provided, the DB lookup is
            skipped, which is useful when refreshing after a mutation.
    """
    if skill is None:
        skill_id = _parse_callback_skill_id(callback)
        if skill_id is None:
            await callback.answer("Ошибка данных", show_alert=True)
            return

        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            skill = await _get_skill_by_id(session, owner, skill_id)
            if skill is None:
                await callback.answer("Skill не найден", show_alert=True)
                return
            # Detach so attributes remain available after the session closes.
            session.expunge(skill)

    if not skip_answer:
        await callback.answer()
    await _edit_callback_message(
        callback,
        _format_skill_detail(skill),
        reply_markup=_skill_detail_keyboard(skill),
    )


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:approve:"))
async def cb_skill_approve(callback: CallbackQuery) -> None:
    """Approve a proposed skill."""

    async def _action(session, owner, skill: Skill) -> bool:
        return await approve_skill(callback.from_user.id, skill.name, session=session)

    await _skill_mutation(
        callback,
        _action,
        "✅ Skill одобрен",
        "Не удалось одобрить skill",
    )


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:reject:"))
async def cb_skill_reject(callback: CallbackQuery) -> None:
    """Reject a skill."""

    async def _action(session, owner, skill: Skill) -> bool:
        return await reject_skill(callback.from_user.id, skill.name, session=session)

    await _skill_mutation(
        callback,
        _action,
        "❌ Skill отклонён",
        "Не удалось отклонить skill",
    )


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:toggle:"))
async def cb_skill_toggle(callback: CallbackQuery) -> None:
    """Enable/disable a skill."""

    def _pre_check(skill: Skill) -> tuple[bool, str]:
        if skill.review_status != "approved":
            return False, "Toggle доступен только для approved skills"
        return True, ""

    async def _action(session, owner, skill: Skill) -> bool:
        new_enabled = not skill.enabled
        updated = await set_skill_enabled(
            session, owner, skill.name, new_enabled, review_status="approved"
        )
        return updated is not None

    await _skill_mutation(
        callback,
        _action,
        lambda skill: f"Skill {'включён' if skill.enabled else 'отключён'}",
        "Не удалось переключить skill",
        pre_check=_pre_check,
    )


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:rollback:"))
async def cb_skill_rollback(callback: CallbackQuery) -> None:
    """Rollback a skill to best_body."""

    def _pre_check(skill: Skill) -> tuple[bool, str]:
        if skill.best_body is None:
            return False, "Нет стабильной версии для отката"
        return True, ""

    async def _action(session, owner, skill: Skill) -> bool:
        await _perform_rollback(
            session, skill, owner, "Manual rollback via /skills panel"
        )
        return True

    await _skill_mutation(
        callback,
        _action,
        "↩️ Skill откачен",
        "Не удалось откатить skill",
        pre_check=_pre_check,
    )


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:promote:"))
async def cb_skill_promote(callback: CallbackQuery) -> None:
    """Promote a skill to global scope."""

    async def _action(session, owner, skill: Skill) -> bool:
        return await promote_to_global(
            callback.from_user.id, skill.name, session=session
        )

    await _skill_mutation(
        callback,
        _action,
        "🌍 Skill promoted to global",
        "Не удалось продвинуть в global (возможно, уже существует)",
    )


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:evolve:"))
async def cb_skills_evolve(callback: CallbackQuery) -> None:
    """Run curator auto-evolve and report results.

    This is a dry-run + approve flow: auto_approve_high_confidence only
    approves skills that pass validation, so it is safe to run manually.
    """
    await callback.answer("🧬 Запускаю auto-evolve...")

    approved = 0
    evolve_error: str | None = None
    try:
        approved = await auto_approve_high_confidence()
    except Exception as e:
        logger.warning("auto_approve_high_confidence failed: %s", e)
        evolve_error = "Auto-approve failed"

    try:
        proposed = await list_proposed()
    except Exception as e:
        logger.warning("list_proposed failed: %s", e)
        proposed = []

    error_note = f"\n⚠️ {evolve_error}" if evolve_error else ""
    text = (
        f"🧬 <b>Auto-evolve результат</b>{error_note}\n\n"
        f"Авто-одобрено: {approved}\n"
        f"Осталось proposed: {len(proposed)}\n"
    )
    if proposed:
        lines = [
            f"• {html.escape(s['name'])} (confidence: {s['confidence']:.0%})"
            for s in proposed[:10]
        ]
        text += "\nТоп предложенных:\n" + "\n".join(lines)

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🔙 К списку",
            callback_data=f"{CALLBACK_PREFIX}:page:all:0",
        )
    )

    # ponytail: callback already answered at top for instant feedback;
    # no second answer() needed — Telegram allows only one.
    await _edit_callback_message(callback, text, reply_markup=kb.as_markup())


@router.callback_query(F.data.startswith(f"{CALLBACK_PREFIX}:stats:"))
async def cb_skills_stats(callback: CallbackQuery) -> None:
    """Show curator statistics."""
    try:
        stats = await curator_stats(callback.from_user.id)
    except Exception as e:
        logger.warning("skills stats failed: %s", e)
        await callback.answer("⚠️ Не удалось получить статистику", show_alert=True)
        return

    text = (
        "📊 <b>Skills Stats</b>\n\n"
        f"🆕 Предложен: {stats['proposed']}\n"
        f"✅ Активен: {stats['approved']}\n"
        f"❌ Отклонён: {stats['rejected']}\n"
        f"🌍 Global: {stats['global']}\n"
        f"Всего: {stats['total']}"
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="🔙 К списку",
            callback_data=f"{CALLBACK_PREFIX}:page:all:0",
        )
    )
    await callback.answer()
    await _edit_callback_message(callback, text, reply_markup=kb.as_markup())
