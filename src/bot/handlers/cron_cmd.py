"""Telegram Cron Panel: /cron command and inline job management.

Provides:
- /cron — list jobs with inline buttons (toggle/run/delete/show)
- /cron add <name> <expression> <type> <payload> — quick create
- /cron blueprints — list built-in cron templates
- Destructive actions (run, delete) go through the Approval Kernel.
- Progress card for long-running llm_prompt executions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, UTC

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.bot.handlers.free_text._confirm import _store_intent_confirmation
from src.core.infra.text_sanitizer import sanitize_html
from src.core.scheduling.cron.blueprints import BLUEPRINTS, get_blueprint
from src.core.scheduling.cron.parser import parse_nl_to_cron, validate_cron
from src.db.repo import get_or_create_user
from src.db.repos.cron_repo import (
    create_cron_job,
    get_cron_job,
    list_user_jobs,
    update_cron_job,
)
from src.db.session import get_session

logger = logging.getLogger(__name__)
router = Router(name="cron")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


def _callback_message(callback: CallbackQuery) -> Message:
    """Return the message attached to a callback, guarding types."""
    msg = callback.message
    if not isinstance(msg, Message):
        raise TypeError("Callback message is not available")
    return msg


# Callback prefixes
_CRON_TOGGLE = "cron:toggle"
_CRON_RUN = "cron:run"
_CRON_DELETE = "cron:delete"
_CRON_SHOW = "cron:show"
_CRON_BLUEPRINT = "cron:blueprints"

_NO_JOBS_MSG = "У тебя пока нет cron-задач.\nСоздай через /cron add или выбери шаблон."


async def _get_owned_cron_job(session, user_id: int, job_id: int):
    """Fetch a cron job only if it belongs to the user."""
    user = await get_or_create_user(session, user_id)
    job = await get_cron_job(session, job_id)
    if job is None or job.user_id != user.id:
        return None, user
    return job, user


@router.message(Command("cron"))
async def cron_cmd(message: Message, command: CommandObject) -> None:
    """Entry point: /cron [add|blueprints|help] [args]."""
    args = (command.args or "").strip().split()
    if not args:
        await _show_list(message)
        return

    subcmd = args[0].lower()
    if subcmd == "add":
        await _quick_add(message, list(args[1:]))
    elif subcmd in ("blueprints", "templates"):
        await _show_blueprints(message)
    elif subcmd in ("help", "?"):
        await _show_help(message)
    else:
        await message.answer(
            "Неизвестная подкоманда. Используй:\n"
            "/cron — список задач\n"
            "/cron add <название> <cron> <тип> <payload>\n"
            "/cron blueprints"
        )


async def _show_help(message: Message) -> None:
    await message.answer(
        "📅 <b>Cron Panel</b>\n\n"
        "/cron — список задач\n"
        '/cron add Название 0 9 * * * message {"text":"Доброе утро"}\n'
        '/cron add Название 0 9 * * * llm_prompt {"prompt":"Сводка дня"}\n'
        "/cron blueprints — готовые шаблоны\n\n"
        "Кнопки в списке: 🟢/🔴 вкл/выкл, ▶️ запустить, 🗑 удалить, ℹ️ детали.",
        parse_mode="HTML",
    )


def _render_list(jobs: list) -> tuple[str | None, InlineKeyboardBuilder]:
    """Render the cron list text and keyboard from a list of jobs."""
    if not jobs:
        kb = InlineKeyboardBuilder()
        kb.button(text="📋 Шаблоны", callback_data="cron:blueprints")
        return None, kb

    lines = ["📅 <b>Cron-задачи</b>\n"]
    kb = InlineKeyboardBuilder()
    for job in jobs:
        status = "🟢" if job.enabled else "🔴"
        name = sanitize_html(job.name)
        expr = sanitize_html(job.cron_expression)
        payload_type = sanitize_html(job.payload_type)
        lines.append(
            f"{status} <b>#{job.id}</b> {name} — <code>{expr}</code> ({payload_type})"
        )
        next_run = job.next_run_at
        if next_run:
            lines.append(f"   След.: {next_run.strftime('%Y-%m-%d %H:%M %Z')}")
        kb.row(
            InlineKeyboardButton(
                text=f"🟢/🔴 #{job.id}", callback_data=f"{_CRON_TOGGLE}:{job.id}"
            ),
            InlineKeyboardButton(
                text=f"▶️ #{job.id}", callback_data=f"{_CRON_RUN}:{job.id}"
            ),
            InlineKeyboardButton(
                text=f"🗑 #{job.id}", callback_data=f"{_CRON_DELETE}:{job.id}"
            ),
            InlineKeyboardButton(
                text=f"ℹ️ #{job.id}", callback_data=f"{_CRON_SHOW}:{job.id}"
            ),
        )
    return "\n".join(lines), kb


async def _show_list(message: Message) -> None:
    """Render inline list of user's cron jobs."""
    async with get_session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        jobs = await list_user_jobs(session, user.id)

    text, kb = _render_list(jobs)
    if text is None:
        await message.answer(_NO_JOBS_MSG, reply_markup=kb.as_markup())
        return

    await message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


async def _quick_add(message: Message, args: list[str]) -> None:
    """Parse '/cron add <name> <expr> <type> <payload>' and create job.

    The cron expression can be a 5-field literal (spaces separate fields) or a
    single natural-language token. Payload is the rest of the line after type.
    """
    if len(args) < 4:
        await message.answer(
            "Нужно: /cron add <название> <cron-выражение> <тип> <payload>\n"
            'Пример: /cron add Утро 0 9 * * * message {"text":"Доброе утро"}'
        )
        return

    name = args[0]

    # Try 5-field cron expression first (tokens 1..5); fall back to single token.
    if len(args) >= 7:
        expr = " ".join(args[1:6])
        payload_type = args[6]
        payload_str = " ".join(args[7:])
    else:
        expr = args[1]
        payload_type = args[2]
        payload_str = " ".join(args[3:])

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        await message.answer("❌ payload должен быть валидным JSON.")
        return

    if not validate_cron(expr):
        parsed_expr = parse_nl_to_cron(expr)
        if parsed_expr:
            expr = parsed_expr
        else:
            await message.answer(
                f"❌ Невалидное cron-выражение: <code>{sanitize_html(expr)}</code>",
                parse_mode="HTML",
            )
            return

    async with get_session() as session:
        user = await get_or_create_user(session, message.from_user.id)
        job = await create_cron_job(
            session,
            user_id=user.id,
            name=name,
            cron_expression=expr,
            payload_type=payload_type,
            payload=payload,
            next_run_at=datetime.now(
                UTC
            ),  # ponytail: first run soon; scheduler recalculates
        )
        await session.commit()

    await message.answer(
        f"✅ Создана задача <b>#{job.id}</b> <code>{sanitize_html(name)}</code>\n"
        f"<code>{sanitize_html(expr)}</code>",
        parse_mode="HTML",
    )


async def _show_blueprints(message: Message) -> None:
    """Show built-in cron blueprints as inline buttons."""
    kb = InlineKeyboardBuilder()
    for bp in BLUEPRINTS:
        kb.button(
            text=f"{bp.name} ({bp.cron_expression})",
            callback_data=f"{_CRON_BLUEPRINT}:{bp.name}",
        )
    kb.adjust(1)
    await message.answer(
        "📋 Готовые шаблоны cron. Выбери, чтобы создать задачу:",
        reply_markup=kb.as_markup(),
    )


def _parse_job_id(callback: CallbackQuery) -> int | None:
    """Extract job_id from callback data safely."""
    data = callback.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


@router.callback_query(F.data.startswith(f"{_CRON_BLUEPRINT}:"))
async def _cb_blueprint(callback: CallbackQuery) -> None:
    """Create a cron job from a built-in blueprint."""
    data = callback.data or ""
    parts = data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    name = parts[2]
    bp = get_blueprint(name)
    if bp is None:
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    async with get_session() as session:
        user = await get_or_create_user(session, callback.from_user.id)
        job = await create_cron_job(
            session,
            user_id=user.id,
            name=bp.name,
            cron_expression=bp.cron_expression,
            payload_type=bp.payload_type,
            payload=bp.payload,
            description=bp.description,
            tags=bp.tags,
            next_run_at=datetime.now(UTC),
        )
        await session.commit()
        jobs = await list_user_jobs(session, user.id)

    await callback.answer(f"Создана задача #{job.id}")
    msg = _callback_message(callback)
    text, kb = _render_list(jobs)
    if text is None:
        await msg.edit_text(
            _NO_JOBS_MSG,
            reply_markup=kb.as_markup(),
        )
    else:
        await msg.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith(f"{_CRON_SHOW}:"))
async def _cb_show(callback: CallbackQuery) -> None:
    """Show full job details."""
    msg = _callback_message(callback)
    job_id = _parse_job_id(callback)
    if job_id is None:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    async with get_session() as session:
        job, _ = await _get_owned_cron_job(session, callback.from_user.id, job_id)
        if job is None:
            await callback.answer("Задача не найдена", show_alert=True)
            return

    payload = job.payload or "{}"
    try:
        payload_pretty = json.dumps(json.loads(payload), ensure_ascii=False, indent=2)
    except Exception:
        payload_pretty = payload

    text = (
        f"📅 <b>#{job.id}</b> {sanitize_html(job.name)}\n"
        f"Тип: {sanitize_html(job.payload_type)}\n"
        f"Cron: <code>{sanitize_html(job.cron_expression)}</code>\n"
        f"Канал: {sanitize_html(job.channel)}\n"
        f"Статус: {'🟢 Включена' if job.enabled else '🔴 Отключена'}\n"
        f"Запусков: {job.run_count}\n"
        f"Последний: {job.last_run_at or '—'}\n"
        f"Следующий: {job.next_run_at or '—'}\n"
        f"Payload:\n<pre>{sanitize_html(payload_pretty)}</pre>"
    )
    await callback.answer()
    await msg.edit_text(text, parse_mode="HTML")


@router.callback_query(F.data.startswith(f"{_CRON_TOGGLE}:"))
async def _cb_toggle(callback: CallbackQuery) -> None:
    """Toggle enabled state. Low-risk: direct action."""
    job_id = _parse_job_id(callback)
    if job_id is None:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    async with get_session() as session:
        job, user = await _get_owned_cron_job(session, callback.from_user.id, job_id)
        if job is None:
            await callback.answer("Задача не найдена", show_alert=True)
            return
        updated = await update_cron_job(session, job_id, enabled=not job.enabled)
        await session.commit()
        if updated is None:
            await callback.answer("Не удалось обновить задачу", show_alert=True)
            return
        jobs = await list_user_jobs(session, user.id)

    await callback.answer(f"{'Включена' if updated.enabled else 'Отключена'} #{job_id}")
    msg = _callback_message(callback)
    text, kb = _render_list(jobs)
    if text is None:
        await msg.edit_text(
            _NO_JOBS_MSG,
            reply_markup=kb.as_markup(),
        )
    else:
        await msg.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@router.callback_query(F.data.startswith(f"{_CRON_RUN}:"))
async def _cb_run(callback: CallbackQuery) -> None:
    """Request approval to run a job immediately (destructive)."""
    job_id = _parse_job_id(callback)
    if job_id is None:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    await _request_cron_action(callback, job_id, "run", "▶️ Запустить задачу")


@router.callback_query(F.data.startswith(f"{_CRON_DELETE}:"))
async def _cb_delete(callback: CallbackQuery) -> None:
    """Request approval to delete a job (destructive)."""
    job_id = _parse_job_id(callback)
    if job_id is None:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    await _request_cron_action(callback, job_id, "delete", "🗑 Удалить задачу")


async def _request_cron_action(
    callback: CallbackQuery,
    job_id: int,
    action: str,
    label: str,
) -> None:
    """Create an Approval Kernel confirmation for destructive cron actions."""
    msg = _callback_message(callback)
    async with get_session() as session:
        job, _ = await _get_owned_cron_job(session, callback.from_user.id, job_id)
        if job is None:
            await callback.answer("Задача не найдена", show_alert=True)
            return

    confirm_cb, cancel_cb = await _store_intent_confirmation(
        telegram_id=callback.from_user.id,
        intent_name=f"cron_{action}",
        intent={"job_id": job_id, "user_id": callback.from_user.id},
        human_summary=f"{label} #{job_id} ({sanitize_html(job.name)})",
        risk="high",
    )

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=confirm_cb),
        InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_cb),
    )
    await callback.answer()
    await msg.edit_text(
        f"{label} <b>#{job_id}</b> <code>{sanitize_html(job.name)}</code>?\n"
        "Подтверди действие.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
