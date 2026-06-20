"""Общие утилиты, кэш настроек, клавиатуры, post-turn optimization —
используются free_text.py, free_text_memory.py, free_text_settings.py."""

import asyncio
import logging
import re
import time
from datetime import UTC

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.reply_dedup import dedup
from src.config import settings
from src.core.actions.trajectory import record_trajectory
from src.core.contacts.smart_reply import get_reaction
from src.core.security import approval
from src.core.infra.formatting import auto_format
from src.core.infra.settings_cache import _settings_cache, invalidate_settings_cache  # noqa: F401  # pyright: ignore[reportUnusedImport] — re-export for late imports
from src.core.infra.task_manager import track_ff
from src.core.infra.timeutil import HM_RE, is_valid_tz, get_user_tz
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)

# ── Telegram message length safety ─────────────────────────────────────
TELEGRAM_SAFE_MAX = settings.safe_message_length  # Telegram hard limit is 4096 chars


def _smart_split(text: str, max_len: int = TELEGRAM_SAFE_MAX) -> list[str]:
    """Split text into chunks respecting paragraph then sentence boundaries.

    Never splits mid-word. Sent as multiple messages if too long.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    # First pass: split on paragraph boundaries
    paragraphs = text.split("\n\n")
    buf = ""

    for para in paragraphs:
        if not buf:
            buf = para
            continue
        candidate = buf + "\n\n" + para
        if len(candidate) <= max_len:
            buf = candidate
        else:
            chunks.append(buf)
            buf = para

    if buf:
        chunks.append(buf)

    # Second pass: hard-split any chunks still over the limit
    result: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_len:
            result.append(chunk)
        else:
            result.extend(_hard_split(chunk, max_len))
    return result


def _hard_split(text: str, max_len: int) -> list[str]:
    """Hard-split a single chunk at sentence boundaries, never mid-word."""
    parts: list[str] = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # Try to find a sentence boundary within the limit
        chunk = text[:max_len]
        last_period = chunk.rfind(". ")
        last_newline = chunk.rfind("\n")
        split_at = max(last_period, last_newline)
        if split_at > max_len // 2:
            parts.append(text[: split_at + 1].strip())
            text = text[split_at + 1 :].strip()
        else:
            # No good boundary found — hard cut at the last space
            last_space = chunk.rfind(" ")
            if last_space > max_len // 2:
                parts.append(text[:last_space].strip())
                text = text[last_space:].strip()
            else:
                parts.append(chunk.strip())
                text = text[max_len:].strip()
    return parts


async def safe_answer(
    message: Message, text: str, max_len: int = TELEGRAM_SAFE_MAX, **kwargs: Any
) -> None:
    """Send ``text`` via ``message.answer()``, splitting into multiple messages if too long.
    ``reply_markup`` (if any) is attached only to the last message.
    """
    # Guard: empty text is a no-op (Telegram rejects empty message)
    if not text or not text.strip():
        return
    # Reaction engine: short responses → emoji reaction instead of text
    if len(text) < 50 and "```" not in text:
        emoji = get_reaction(text)
        if emoji:
            try:
                from aiogram.types import ReactionTypeEmoji

                await message.react([ReactionTypeEmoji(emoji=emoji)])
                return
            except Exception:
                logger.debug(
                    "Non-critical error", exc_info=True
                )  # Fall through to normal text send

    if await dedup.is_duplicate(message.chat.id, text):
        return
    # Apply auto-formatting for Telegram HTML
    text = auto_format(text)

    # ── Rich Messages: пробуем отправить одним расширенным сообщением ──
    from src.bot.rich_messages import (
        RICH_MESSAGE_MAX,
        is_rich_applicable,
        send_rich_message,
        to_rich_markdown,
    )

    # is_rich_applicable() сама проверяет длину > RICH_MESSAGE_LIMIT для plain text,
    # но также возвращает True для таблиц/заголовков любой длины.
    # Ограничиваем сверху хард-лимитом API.
    if len(text) <= RICH_MESSAGE_MAX and is_rich_applicable(text):
        rich_md = to_rich_markdown(text)
        result = await send_rich_message(
            message.bot,
            message.chat.id,
            rich_md,
            reply_markup=kwargs.get("reply_markup"),
        )
        if result is not None:
            return  # Rich Message отправлен успешно
        # Fall through к обычному sendMessage, если Rich не поддерживается

    parts = _smart_split(text, max_len)
    for i, part in enumerate(parts):
        final_kwargs = {}
        if i == len(parts) - 1:
            final_kwargs = kwargs  # reply_markup only on the last chunk
        await message.answer(part, **final_kwargs)


# ── Model name validation ─────────────────────────────────────────────
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9@/_.:-]{1,128}$")


async def _get_owner_context(
    telegram_id: int, session: AsyncSession | None = None
) -> dict[str, object]:
    """Возвращает {owner_telegram_id, tz_name, use_heavy, global_style_profile} с TTL-кэшем (per-user).

    Args:
        telegram_id: Telegram user ID
        session: Optional database session (if provided, skips lock/cache for single-session optimization)
    """
    # Single-session optimization: use provided session directly
    if session is not None:
        owner = await get_or_create_user(session, telegram_id)
        return {
            "owner_telegram_id": owner.telegram_id,
            "tz_name": get_user_tz(owner),
            "use_heavy": owner.settings.use_heavy_model if owner.settings else True,
            "global_style_profile": owner.global_style_profile,
        }

    # ManagedCache path: LRU-bounded, auto-TTL, thread-safe
    # Используем single-writer pattern: только один writer делает БД-запрос,
    # остальные корутины ждут результат через asyncio.Event.

    async def _compute_ctx() -> dict[str, object]:
        async with get_session() as session:
            owner = await get_or_create_user(session, telegram_id)
            return {
                "owner_telegram_id": owner.telegram_id,
                "tz_name": get_user_tz(owner),
                "use_heavy": owner.settings.use_heavy_model if owner.settings else True,
                "global_style_profile": owner.global_style_profile,
            }

    return await _settings_cache.get_or_compute(telegram_id, _compute_ctx)


def _fire_record_trajectory(*args: object, **kwargs: object) -> None:
    """Fire-and-forget запись траектории (не блокирует ответ пользователю)."""

    async def _safe() -> None:
        try:
            await record_trajectory(*args, **kwargs)  # type: ignore[arg-type]
        except Exception:
            logger.exception("fire-and-forget trajectory failed")

    track_ff(asyncio.create_task(_safe()))


def _coerce_setting_value(spec: str, raw):
    if spec == "bool":
        if isinstance(raw, bool):
            return raw, None
        if isinstance(raw, str) and raw.lower() in {"true", "yes", "on", "вкл", "1"}:
            return True, None
        if isinstance(raw, str) and raw.lower() in {"false", "no", "off", "выкл", "0"}:
            return False, None
        return None, "ожидаю true/false"
    if spec == "int":
        try:
            return int(raw), None
        except (TypeError, ValueError):
            return None, "ожидаю целое число"
    if spec == "str":
        if not isinstance(raw, str) or not raw.strip():
            return None, "ожидаю строку"
        return raw.strip(), None
    if spec == "hm":
        if isinstance(raw, str) and HM_RE.match(raw.strip()):
            return raw.strip(), None
        return None, "ожидаю время в формате HH:MM"
    if spec == "tz":
        if isinstance(raw, str) and is_valid_tz(raw.strip()):
            return raw.strip(), None
        return None, "не нашёл такой IANA timezone"
    if spec.startswith("choice:"):
        opts = set(spec[len("choice:") :].split(","))
        if isinstance(raw, str) and raw.strip() in opts:
            return raw.strip(), None
        return None, f"допустимые значения: {', '.join(sorted(opts))}"
    if spec == "model":
        if not isinstance(raw, str):
            return None, "ожидаю строку (имя модели)"
        val = raw.strip()
        if not val or val.lower() in ("default", "по умолчанию", "сброс", "сбросить"):
            return "", None  # clear override
        if len(val) > 128:
            return None, "имя модели слишком длинное (макс. 128)"
        if not _MODEL_NAME_RE.match(val):
            return None, (
                "недопустимые символы в имени модели. "
                "Допустимы: буквы, цифры, @ / _ . : -"
            )
        return val, None
    return None, "неизвестный тип"


def _confirm_keyboard(action_id: int, hmac_signature: str | None = None):
    """Клавиатура подтверждения отправки с unified HMAC-подписью в callback_data."""
    sig = hmac_signature or ""
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="✅ Отправить",
            callback_data=approval.format_callback("send", str(action_id), sig),
        ),
        InlineKeyboardButton(text="✏ Изменить", callback_data=f"send:edit:{action_id}"),
    )
    kb.row(
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=approval.format_cancel_callback("send", str(action_id)),
        )
    )
    return kb.as_markup()


KIND_EMOJI = {"user": "👤", "group": "👥", "channel": "📢", "bot": "🤖"}


def _group_candidates(candidates: list, max_display: int = 8):
    """Group candidates by peer_kind, return (displayed_list, hidden_count)."""
    if len(candidates) <= max_display:
        return candidates, 0

    # Sort by score descending, show top max_display
    sorted_candidates = sorted(candidates, key=lambda c: c.score, reverse=True)
    return sorted_candidates[:max_display], len(sorted_candidates) - max_display


def _candidates_keyboard_send(candidates):
    kb = InlineKeyboardBuilder()
    displayed, hidden = _group_candidates(candidates, max_display=8)

    # Group by kind with visual separator
    shown_kinds: set[str] = set()
    for c in displayed:
        emoji = KIND_EMOJI.get(c.peer_kind, "•")
        if c.peer_kind not in shown_kinds:
            shown_kinds.add(c.peer_kind)
        kb.row(
            InlineKeyboardButton(
                text=f"{emoji} {c.label()} · {c.score}",
                callback_data=f"send:pick:{c.peer_id}",
            )
        )

    if hidden:
        kb.row(
            InlineKeyboardButton(
                text=f"🔍 Ещё {hidden} контактов — уточните имя",
                callback_data=approval.format_cancel_callback(
                    "send", "0"
                ),  # cancel just dismisses
            )
        )
    kb.row(
        InlineKeyboardButton(
            text="❌ Отмена", callback_data=approval.format_cancel_callback("send", "0")
        )
    )
    return kb.as_markup()


def _candidates_keyboard_chat(action: str, candidates):
    # action ∈ {summary, tasks, draft, catchup} — re-use chat:* callback'ов из chat_cmd
    kb = InlineKeyboardBuilder()
    displayed, hidden = _group_candidates(candidates, max_display=8)

    shown_kinds: set[str] = set()
    for c in displayed:
        emoji = KIND_EMOJI.get(c.peer_kind, "•")
        if c.peer_kind not in shown_kinds:
            shown_kinds.add(c.peer_kind)
        kb.row(
            InlineKeyboardButton(
                text=f"{emoji} {c.label()} · {c.score}",
                callback_data=f"chat:{action}:{c.peer_id}",
            )
        )

    if hidden:
        kb.row(
            InlineKeyboardButton(
                text=f"🔍 Ещё {hidden} контактов — уточните имя",
                callback_data="chat:cancel:0",
            )
        )
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="chat:cancel:0"))
    return kb.as_markup()


def memory_quick_keyboard(contact_name: str = "") -> InlineKeyboardMarkup:
    """Inline-кнопки быстрых действий с памятью."""
    explain_cb = f"memq:explain:{contact_name}" if contact_name else "memq:explain:"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🧠 Что помню", callback_data="memq:list"),
                InlineKeyboardButton(text="➕ Запомни", callback_data="memq:add"),
                InlineKeyboardButton(text="❌ Забудь", callback_data="memq:forget"),
                InlineKeyboardButton(text="🤔 Почему?", callback_data=explain_cb),
            ]
        ]
    )


def _summarize_intent_for_memory(intent: dict) -> str:
    # компактная запись «что я только что сделал» для памяти диалога
    kind = intent.get("intent")
    if kind == "multi":
        return "несколько действий: " + ", ".join(
            (a or {}).get("intent", "?") for a in intent.get("actions", [])[:5]
        )
    if kind == "send_message":
        return f"подготовил отправку «{(intent.get('text') or '')[:60]}» для {intent.get('recipient')}"
    if kind in {"summarize_chat", "tasks_for_chat", "draft_reply", "catchup"}:
        return f"{kind} с контактом {intent.get('contact')}"
    if kind == "find_in_chats":
        return f"искал в чатах: {intent.get('query')}"
    if kind == "news_digest":
        return f"новости: {intent.get('topic')}"
    if kind == "set_setting":
        return f"настройка {intent.get('key')} → {intent.get('value')}"
    if kind == "add_news_topic":
        return f"добавил тему: {intent.get('topic')}"
    if kind == "remove_news_topic":
        return f"убрал тему: {intent.get('topic')}"
    if kind == "add_reminder":
        return f"напоминание: {intent.get('text')}"
    if kind == "remove_reminder":
        return f"убрал напоминание: {intent.get('query')}"
    if kind == "add_reminders_from_chat":
        return f"вытащил обещания из чата с {intent.get('contact')}"
    if kind == "list_todos":
        return "показал список обещаний"
    if kind == "chat":
        return (intent.get("reply") or "")[:160]
    if kind == "store_memory":
        return "запомнил факт"
    if kind == "forget_memory":
        return "удалил из памяти"
    if kind == "list_memories":
        return "посмотрел память"
    if kind == "extract_memories_from_chat":
        return "извлёк факты из переписки"
    if kind == "check_memories":
        return "проверил актуальность памяти"
    if kind == "update_memory":
        return "обновил факт в памяти"
    if kind == "link_memories":
        return "связал два факта в памяти"
    if kind == "show_memory_health":
        return "посмотрел здоровье памяти"
    if kind == "show_memory_graph":
        return "посмотрел граф памяти"
    if kind == "show_sessions":
        return "посмотрел историю сессий"
    if kind == "show_suggestions":
        return "посмотрел паттерны памяти"
    if kind == "change_auto_mode":
        return "изменил авто-режим"
    if kind == "set_quiet_hours":
        return "настроил тихие часы"
    if kind == "show_inbox":
        return "посмотрел входящие"
    if kind == "full_analysis":
        return "запустил полный анализ"
    if kind == "clarify":
        return f"переспросил: {intent.get('question', '')[:100]}"
    return kind or ""


def _parse_iso_to_utc_naive(value, tz_name: str | None = None):
    if not value:
        return None
    try:
        from datetime import datetime
        from src.core.infra.timeutil import parse_tz

        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            return dt.astimezone(UTC).replace(tzinfo=None)
        if tz_name:
            tz = parse_tz(tz_name)
            local_dt = dt.replace(tzinfo=tz)
            return local_dt.astimezone(UTC).replace(tzinfo=None)
        return dt
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dispatch adapters — приводят хендлеры к единой сигнатуре
# ---------------------------------------------------------------------------


def h_adapter(fn):
    """Адаптер: fn(intent, message) → унифицированная dispatch-сигнатура."""

    async def _w(intent, message, state, userbot_manager, *, tz_name):
        return await fn(intent, message)

    return _w


def hu_adapter(fn):
    """Адаптер: fn(intent, message, userbot_manager) → унифицированная."""

    async def _w(intent, message, state, userbot_manager, *, tz_name):
        return await fn(intent, message, userbot_manager)

    return _w


def ht_adapter(fn):
    """Адаптер: fn(intent, message, *, tz_name) → унифицированная."""

    async def _w(intent, message, state, userbot_manager, *, tz_name):
        return await fn(intent, message, tz_name=tz_name)

    return _w


# ---------------------------------------------------------------------------
# InstructionOptimizer integration — post-turn LLM review
# ---------------------------------------------------------------------------

# Rate-limit для post_turn_optimize: не чаще 1 раза в 5 минут на пользователя.
# Безопасен под asyncio: защищён _post_turn_lock, доступ только внутри async with.
_post_turn_last_call: dict[int, float] = {}
_post_turn_lock: "asyncio.Lock | None" = None
_post_turn_tasks: "dict[int, asyncio.Task]" = {}


def _get_post_turn_lock() -> asyncio.Lock:
    global _post_turn_lock
    if _post_turn_lock is None:
        _post_turn_lock = asyncio.Lock()
    return _post_turn_lock


async def _post_turn_optimize(
    telegram_id: int,
    user_message: str,
    assistant_response: str,
) -> None:
    """
    Запускает LLM-ревью диалога через InstructionOptimizer.
    FIRE-AND-FORGET: не ждёт результат, rate-limited (1 раз в 5 мин).
    """
    if not user_message or not assistant_response:
        return

    now = time.monotonic()
    async with _get_post_turn_lock():
        # Cleanup: удаляем записи старше 1 часа
        stale = [uid for uid, ts in _post_turn_last_call.items() if now - ts > 3600]
        for uid in stale:
            del _post_turn_last_call[uid]
        # Cleanup: удаляем completed/cancelled tasks из _post_turn_tasks
        stale_tasks = [
            uid for uid, t in _post_turn_tasks.items() if t.done() or (uid in stale)
        ]
        for uid in stale_tasks:
            _post_turn_tasks.pop(uid, None)
        # Guard: prevent unbounded growth if cleanup is ineffective
        if len(_post_turn_tasks) > 100:
            oldest = sorted(_post_turn_tasks.keys())[:50]
            for uid in oldest:
                _post_turn_tasks.pop(uid, None)
        if telegram_id in _post_turn_last_call:
            if now - _post_turn_last_call[telegram_id] < 300:
                return  # rate-limited
        _post_turn_last_call[telegram_id] = now

    async def _do_optimize():
        try:
            from src.db.session import get_session
            from src.db.repo import get_or_create_user
            from src.core.intelligence.instruction_optimizer import (
                instruction_optimizer,
            )

            async with get_session() as session:
                owner = await get_or_create_user(session, telegram_id)
                await instruction_optimizer.post_turn_review(
                    session=session,
                    user_id=owner.id,
                    user_obj=owner,
                    user_message=user_message,
                    assistant_response=assistant_response,
                )

            # ── Background Self-Review (v3.1) ──────────────────────────
            # После instruction_optimizer — лёгкий LLM-вызов решает:
            # сохранить ли факт в память, обновить ли навык.
            # Fire-and-forget, не блокирует основной диалог.
            try:
                from src.core.intelligence.background_review import (
                    background_reviewer,
                )

                track_ff(
                    asyncio.create_task(
                        background_reviewer.review_response(
                            user_id=telegram_id,
                            user_text=user_message,
                            assistant_response=assistant_response,
                            provider=None,  # build own cheap background provider
                        )
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(
                    "background_reviewer.review_response skipped", exc_info=True
                )
        except asyncio.CancelledError:
            raise  # must propagate for proper task cancellation
        except Exception:
            logger.debug("post_turn_optimize skipped", exc_info=True)

    async with _get_post_turn_lock():
        existing = _post_turn_tasks.get(telegram_id)
        if existing and not existing.done():
            existing.cancel()

            # Retrieve exception to suppress "Task exception was never retrieved"
            # asyncio warning when the cancelled task is garbage-collected.
            def _retrieve_exc(t: asyncio.Task) -> None:
                t.exception()

            existing.add_done_callback(_retrieve_exc)
        _post_turn_tasks[telegram_id] = track_ff(
            asyncio.create_task(_do_optimize(), name="post-turn-optimize")
        )
