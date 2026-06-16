"""UI helpers for the skills inline panel.

Pure presentation layer — no DB access, no business logic.
Imported by both ``skills_cmd`` and ``skills_callbacks``.
"""

from __future__ import annotations

import html
import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

# ── UI Constants ─────────────────────────────────────────────────────

_STATUS_LABELS: dict[str, str] = {
    "all": "📋 Все",
    "proposed": "🆕 Предложен",
    "active": "✅ Активен",
    "stale": "🥀 Устарел",
    "archived": "📦 Архив",
    "rejected": "❌ Отклонён",
}

_PAGE_SIZE = 5
CALLBACK_PREFIX = "skills"

# Decay markers written into Skill.description by the curator.
_DECAY_MARKER = "[DECAYED"
_SUCCESS_RATE_MARKER = "success_rate"


# ── Helpers ──────────────────────────────────────────────────────────


def _is_stale(skill) -> bool:
    """Check if a disabled skill is considered stale by decay markers."""
    desc = skill.description or ""
    return _DECAY_MARKER in desc or _SUCCESS_RATE_MARKER in desc


def _ui_status(skill) -> str:
    """Map DB skill state to UI status label."""
    if skill.review_status == "rejected":
        return "rejected"
    if skill.review_status == "proposed":
        return "proposed"
    if not skill.enabled:
        return "stale" if _is_stale(skill) else "archived"
    return "active"


def _format_metrics(skill) -> str:
    """Single-line metrics for a skill."""
    usage = (skill.success_count or 0) + (skill.failure_count or 0)
    success_rate = (skill.success_count or 0) / max(usage, 1)
    score = (
        f"{skill.validation_score * 100:.0f}%"
        if skill.validation_score is not None
        else "—"
    )
    last_used = skill.last_used_at.strftime("%d.%m") if skill.last_used_at else "—"
    return (
        f"исп:{usage} | усп:{skill.success_count or 0} | "
        f"неусп:{skill.failure_count or 0} | sr:{success_rate:.0%} | "
        f"score:{score} | last:{last_used}"
    )


def _skill_button(skill) -> InlineKeyboardButton:
    """Row button for a skill in the list."""
    status_icon = {
        "proposed": "🆕",
        "active": "✅",
        "stale": "🥀",
        "archived": "📦",
        "rejected": "❌",
    }.get(_ui_status(skill), "•")
    label = f"{status_icon} {(skill.name or '?')[:32]}"
    return InlineKeyboardButton(
        text=label, callback_data=f"{CALLBACK_PREFIX}:detail:{skill.id}"
    )


def _pagination_buttons(
    status: str, page: int, total: int
) -> list[InlineKeyboardButton]:
    """Prev/Next pagination row."""
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"{CALLBACK_PREFIX}:page:{status}:{page - 1}",
            )
        )
    if (page + 1) * _PAGE_SIZE < total:
        buttons.append(
            InlineKeyboardButton(
                text="Вперёд ▶️",
                callback_data=f"{CALLBACK_PREFIX}:page:{status}:{page + 1}",
            )
        )
    return buttons


def _status_tabs(status: str) -> list[InlineKeyboardButton]:
    """Status filter tabs."""
    return [
        InlineKeyboardButton(
            text=f"{_STATUS_LABELS[s]}{' ✅' if s == status else ''}",
            callback_data=f"{CALLBACK_PREFIX}:page:{s}:0",
        )
        for s in ("all", "proposed", "active", "stale", "archived", "rejected")
    ]


def _skill_list_keyboard(
    skills: list, status: str, page: int, total: int
) -> InlineKeyboardMarkup:
    """Build inline keyboard for the skill list."""
    kb = InlineKeyboardBuilder()

    # Status tabs (1 row per tab)
    for tab in _status_tabs(status):
        kb.row(tab)

    # Skill rows
    for skill in skills:
        kb.row(_skill_button(skill))

    # Pagination
    pagination = _pagination_buttons(status, page, total)
    if pagination:
        kb.row(*pagination)

    # Evolve action
    kb.row(
        InlineKeyboardButton(
            text="🧬 Auto-evolve",
            callback_data=f"{CALLBACK_PREFIX}:evolve:0",
        ),
        InlineKeyboardButton(
            text="📊 Stats",
            callback_data=f"{CALLBACK_PREFIX}:stats:0",
        ),
    )

    return kb.as_markup()


def _skill_detail_keyboard(skill) -> InlineKeyboardMarkup:
    """Build inline keyboard for a single skill detail view."""
    kb = InlineKeyboardBuilder()
    status = _ui_status(skill)

    actions: list[InlineKeyboardButton] = []
    if status == "proposed":
        actions.append(
            InlineKeyboardButton(
                text="✅ Approve",
                callback_data=f"{CALLBACK_PREFIX}:approve:{skill.id}",
            )
        )
        actions.append(
            InlineKeyboardButton(
                text="❌ Reject",
                callback_data=f"{CALLBACK_PREFIX}:reject:{skill.id}",
            )
        )
    elif status in ("active", "stale", "archived"):
        toggle_text = "⏸ Disable" if skill.enabled else "▶️ Enable"
        actions.append(
            InlineKeyboardButton(
                text=toggle_text,
                callback_data=f"{CALLBACK_PREFIX}:toggle:{skill.id}",
            )
        )
        if skill.best_body:
            actions.append(
                InlineKeyboardButton(
                    text="↩️ Rollback",
                    callback_data=f"{CALLBACK_PREFIX}:rollback:{skill.id}",
                )
            )
        if status == "active":
            actions.append(
                InlineKeyboardButton(
                    text="🌍 Promote",
                    callback_data=f"{CALLBACK_PREFIX}:promote:{skill.id}",
                )
            )
    elif status == "rejected":
        actions.append(
            InlineKeyboardButton(
                text="🔄 Re-approve",
                callback_data=f"{CALLBACK_PREFIX}:approve:{skill.id}",
            )
        )

    if actions:
        kb.row(*actions)

    kb.row(
        InlineKeyboardButton(
            text="🔙 К списку",
            callback_data=f"{CALLBACK_PREFIX}:page:{status}:0",
        )
    )
    return kb.as_markup()


def _format_skill_detail(skill) -> str:
    """Detailed text for a skill."""
    status = _ui_status(skill)
    status_label = _STATUS_LABELS.get(status, status)
    last_used = (
        skill.last_used_at.strftime("%Y-%m-%d %H:%M") if skill.last_used_at else "—"
    )
    version = skill.version or "1.0.0"
    edits = len(skill.edit_history_json or [])
    rejected = len(skill.rejected_edits_json or [])
    name_safe = html.escape(skill.name or "Unnamed")
    body_safe = html.escape((skill.body or "")[:2500])
    return (
        f"<b>{name_safe}</b> v{html.escape(version)}\n"
        f"Статус: {status_label}\n"
        f"Включён: {'да' if skill.enabled else 'нет'}\n"
        f"Метрики: {_format_metrics(skill)}\n"
        f"Последнее использование: {last_used}\n"
        f"Правок: {edits} | Отклонённых правок: {rejected}\n\n"
        f"<pre>{body_safe}</pre>"
    )


async def _edit_callback_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Safely edit the callback message, handling InaccessibleMessage.

    Suppresses TelegramBadRequest for “message is not modified” (no-op re-render).
    """
    if callback.message is None or isinstance(callback.message, InaccessibleMessage):
        return
    try:
        await callback.message.edit_text(
            text, reply_markup=reply_markup, parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning("edit_text failed: %s", e)
            raise


def _skills_summary(skills: list) -> str:
    """Short summary text for the list message."""
    if not skills:
        return "\nНет skills в этой категории."
    lines = []
    for skill in skills:
        lines.append(
            f"• <b>{html.escape(skill.name or 'Unnamed')}</b> — {_format_metrics(skill)}"
        )
    return "\n" + "\n".join(lines)
