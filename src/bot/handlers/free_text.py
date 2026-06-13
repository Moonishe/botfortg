"""Свободный текст (и голос) → агент → действие. Регистрируется последним в bot/app.py,
чтобы команды и FSM перехватывали свои события раньше."""

import asyncio
import hashlib
import io
import ipaddress
import logging
import random
import re
import sys
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot.filters import OwnerOnly
from src.config import settings
from src.core.actions.trajectory import actions_from_intent
from src.core.cache import AdaptiveTTLCache, ManagedCache, cache_manager
from src.core.infra.key_guard import safe_str
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.task_manager import track_ff
from src.core.intelligence.agent import route_intent
from src.core.intelligence.schedule_parser import parse_schedule_message
from src.core.intelligence.smart_autorouter import make_plan
from src.core.memory import conversation_context as ctx_store
from src.core.infra.timeutil import now_in_tz
from src.core.infra.transcription import transcription_service
from src.core.infra.telemetry import start_span
from src.crypto import decrypt
from src.db.models import LlmKeySlot
from src.db.repo import (
    get_api_key,
    get_or_create_user,
)
from src.db.session import get_session
from sqlalchemy import and_, select
from src.llm.base import ChatMessage, TaskType
from src.llm.router import build_provider
from src.llm.vision_provider import OpenAIVisionProvider
from src.userbot.manager import UserbotManager

from .free_text_common import (
    _fire_record_trajectory,
    _get_owner_context,
    _summarize_intent_for_memory,
)
from src.core.intelligence.character_evolution import maybe_evolve_after_turn

# ── Session Context (P2) ──────────────────────────────────────────────
from src.core.memory.session_context import (
    save_session_context,
    resume_session,
)
from src.config import settings as _settings

from .free_text_pipeline import (
    _dispatch,
    _save_intent_context,
    check_contact_rules,
    check_followup,
    check_instructions,
    check_persona,
    execute_fast_route,
    execute_instant,
    execute_maestro,
)
from src.core.humanizer import record_humanizer_feedback, _pop_last_humanized
from src.core.infra.rate_limiter import check_rate_limit
from src.core.classification import classify_message as _classify_message
from src.core.security.prompt_injection_scanner import scan_content
from httpx import RequestError, HTTPStatusError
from aiogram.exceptions import TelegramAPIError

# ── Module constants ─────────────────────────────────────────────────────
_FETCH_URL_TIMEOUT = 15.0  # секунд — таймаут HTTP-запроса для извлечения контента URL
from sqlalchemy.exc import SQLAlchemyError


logger = logging.getLogger(__name__)
router = Router(name="free_text")
router.message.filter(OwnerOnly())

# ── URL detection & auto-summary ───────────────────────────────────────

_URL_RE = re.compile(r'https?://[^\s<>"]+')


def _is_safe_url(url: str) -> bool:
    """Проверяет что URL не ведёт на localhost/private IP."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
        return not (addr.is_loopback or addr.is_private or addr.is_link_local)
    except ValueError:
        # Невалидный IP — вероятно домен, проверяем DNS
        import socket as _socket

        try:
            addrinfo = _socket.getaddrinfo(host, None, _socket.AF_UNSPEC)
            for _family, _, _, _, sockaddr in addrinfo:
                ip = ipaddress.ip_address(sockaddr[0])
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_reserved
                    or ip.is_link_local
                ):
                    return False  # DNS resolves to internal IP — SSRF
        except (_socket.gaierror, OSError):
            pass  # DNS failure — let httpx handle it
        return True


# ── URL summary cache (Adaptive TTL — hot URLs grow to 1h) ───────────

_url_cache = AdaptiveTTLCache(
    name="url_summary",
    base_ttl=600.0,  # 10 min for cold entries
    max_ttl=3600.0,  # 1 hour for frequently accessed URLs
    growth_factor=2.0,
    max_size=100,
)


async def _get_cached_url_summary(url: str) -> str | None:
    """Return cached summary if valid, else None."""
    return await _url_cache.get(url)


async def _set_url_cache(url: str, summary: str) -> None:
    """Store summary in bounded LRU cache."""
    await _url_cache.set(url, summary)


async def invalidate_url_cache(url: str | None = None) -> None:
    """Invalidate URL cache. If url=None, clear all."""
    if url is None:
        await _url_cache.clear()
    else:
        await _url_cache.invalidate(url)


async def _fetch_url_content(url: str) -> str | None:
    """Фетчит содержимое URL через httpx."""
    if not _is_safe_url(url):
        logger.warning("Blocked unsafe URL fetch: %s", url[:100])
        return None
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=_FETCH_URL_TIMEOUT, follow_redirects=False
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "TelegramHelper/1.0"})
            if resp.status_code != 200:
                return None
            html = resp.text[:50000]
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE)
            title = title_m.group(1).strip()[:200] if title_m else url
            return f"Заголовок: {title}\n\n{text[:3000]}"
    except (RequestError, HTTPStatusError):
        return None


async def _summarize_url(url: str, content: str, provider) -> str:
    """Саммаризирует содержимое URL через LLM."""
    prompt = f"""Сделай краткое саммари этой страницы (2-4 предложения). Укажи самое важное.

URL: {url}

СОДЕРЖИМОЕ:
{content[:3000]}

Саммари:"""
    resp = await provider.chat([ChatMessage(role="user", content=prompt)])
    return resp[:1000]


def _extract_correction_pattern(original: str, edited: str) -> tuple[str, str] | None:
    """Извлекает паттерн исправления: (что_было, что_стало)."""
    if len(original) < 10 or len(edited) < 10:
        return None
    common = sum(1 for a, b in zip(original, edited, strict=False) if a == b)
    similarity = common / max(len(original), len(edited))
    if similarity > 0.5:
        return (original[:200], edited[:200])
    return None


_singalong_search_cache: ManagedCache[int, list | None] = cache_manager.register(
    ManagedCache(name="singalong_search", max_size=100, default_ttl=300)
)


async def _pop_singalong_search(key: int) -> list | None:
    """Atomically get and remove from singalong search cache."""
    val = await _singalong_search_cache.get(key)
    if val is not None:
        await _singalong_search_cache.invalidate(key)
    return val


# ── Session Context helpers (P2) ──────────────────────────────────────


def _now_utc() -> datetime:
    """Текущее UTC-время (для вычисления gap между сообщениями)."""

    return datetime.now(UTC)


async def _check_session_resume(user_id: int) -> str | None:
    """Проверяет, вернулся ли пользователь после перерыва >30 мин.
    Возвращает текст приветствия или None."""
    if not _settings.session_context_enabled:
        return None
    try:
        from src.db.models._session import SessionContext
        from src.db.repos.session_repo import get_or_create_user

        async with get_session() as session:
            owner = await get_or_create_user(session, user_id)
            db_uid = owner.id
            result = await session.execute(
                select(SessionContext).where(SessionContext.user_id == db_uid)
            )
            ctx = result.scalar_one_or_none()
            if ctx is not None and ctx.last_active_at is not None:
                gap = (_now_utc() - ctx.last_active_at).total_seconds()
                gap_minutes = _settings.session_context_gap_minutes
                if gap > gap_minutes * 60:
                    return await resume_session(user_id)
    except Exception:
        logger.debug("Session resume check failed for user %d", user_id, exc_info=True)
    return None


async def _save_session_context_ff(telegram_id: int, messages: list[str]) -> None:
    """Fire-and-forget: сохранить контекст сессии (не блокирует ответ)."""
    if not _settings.session_context_enabled:
        return

    async def _do_save():
        try:
            await save_session_context(telegram_id, messages)
        except Exception:
            logger.debug(
                "Fire-and-forget session save failed for user %d",
                telegram_id,
                exc_info=True,
            )

    track_ff(asyncio.create_task(_do_save()))


# ── Contact prefetch helpers ──────────────────────────────────────────


def _extract_contact_hint(message: Message) -> str | None:
    """Extract a contact hint from message entities and reply context.

    Checks:
    1. @mention entities (type='mention' or type='text_mention')
    2. Reply context (reply_to_message author name)

    Returns the hint string or None.
    """
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention" and entity.offset is not None and message.text:
                mention = message.text[entity.offset : entity.offset + entity.length]
                return mention.lstrip("@").strip()
            if entity.type == "text_mention" and entity.user:
                # text_mention has a User object — use first_name or username
                if entity.user.username:
                    return entity.user.username
                if entity.user.first_name:
                    return entity.user.first_name

    # Reply context
    if message.reply_to_message and message.reply_to_message.from_user:
        replied = message.reply_to_message.from_user
        if replied.username:
            return replied.username
        if replied.first_name:
            return replied.first_name

    return None


async def _do_prefetch_contact(
    user_id: int,
    contact_hint: str | None,
    userbot_manager,
) -> None:
    """Fire-and-forget: prefetch contact data into cache.

    Runs in background — errors are caught and logged, never propagated.
    """
    try:
        from src.bot.prefetch import prefetch_contact
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        telethon_client = None
        owner = None
        if contact_hint is not None and userbot_manager is not None:
            try:
                telethon_client = userbot_manager.get_client(user_id)
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
            if telethon_client is not None:
                try:
                    async with get_session() as session:
                        owner = await get_or_create_user(session, user_id)
                except Exception:
                    logger.debug("Non-critical error", exc_info=True)

        await prefetch_contact(
            user_id,
            contact_hint=contact_hint,
            telethon_client=telethon_client,
            owner=owner,
        )
    except Exception:
        logger.debug(
            "_do_prefetch_contact failed for user=%d hint=%r",
            user_id,
            contact_hint,
            exc_info=True,
        )


_QUESTION_STARTS = (
    "что",
    "как",
    "почему",
    "кто",
    "где",
    "когда",
    "зачем",
    "сколько",
    "какой",
    "какая",
    "какое",
    "какие",
    "чей",
)


_QUESTION_PUNCTUATION = set(",.!;:…«»\"'()[]{}—–-")


def _is_question(text: str) -> bool:
    """Определяет, является ли текст вопросом (есть '?' или вопросительное слово)."""
    if "?" in text:
        return True
    stripped = text.strip()
    if not stripped:
        return False
    first_word = stripped.lower().split()[0]
    # Удаляем прилипшую пунктуацию: «что,» → «что», «как...» → «как»
    first_word_clean = first_word.rstrip("".join(_QUESTION_PUNCTUATION))
    return first_word_clean in _QUESTION_STARTS


class VoiceJob(NamedTuple):
    voice_path: Path
    message: object  # aiogram Message
    state_str: str | None
    userbot_manager: object
    file_unique_id: str
    mode: str
    api_provider: str
    openai_key: str | None
    gemini_key: str | None
    mistral_key: str | None
    custom_stt_key: str | None = None
    custom_stt_model: str | None = None
    custom_stt_endpoint: str | None = None


# Voice transcription queue (non-blocking background processing)
_voice_queue: asyncio.Queue = asyncio.Queue(
    maxsize=max(settings.max_voice_queue_size, 1)
)
_voice_worker_tasks: list[asyncio.Task] = []

# Per-user active tasks for priority preemption
# Light tasks (instant, fast_route, send, draft) preempt heavy tasks (maestro, analysis)
_active_tasks: dict[int, asyncio.Task] = {}
_active_tasks_lock = asyncio.Lock()


_WAITING_MESSAGES = [
    "⏳ Дай подумать…",
    "🤔 Сейчас соображу…",
    "💭 Уже думаю…",
    "🔍 Смотрю в переписке…",
    "📝 Анализирую…",
    "⏳ Секунду…",
    "🤖 Обрабатываю…",
    "💡 Генерирую ответ…",
]


def _get_waiting_message() -> str:
    return random.choice(_WAITING_MESSAGES)


VOICE_WORKER_COUNT = 2


def start_voice_worker() -> list[asyncio.Task]:
    """Запустить фоновых worker'ов для транскрипции голоса (если ещё не запущены).

    Вызывается при старте приложения (main.py).
    Запускает VOICE_WORKER_COUNT параллельных worker'ов, чтобы очередь не
    блокировалась при нескольких голосовых подряд.
    """
    global _voice_worker_tasks
    if not _voice_worker_tasks or all(t.done() for t in _voice_worker_tasks):
        _voice_worker_tasks = [
            asyncio.create_task(_voice_worker(), name=f"voice-transcription-worker-{i}")
            for i in range(VOICE_WORKER_COUNT)
        ]
    return _voice_worker_tasks


async def stop_voice_worker() -> None:
    """Остановить всех voice worker'ов (graceful shutdown)."""
    global _voice_worker_tasks
    for task in _voice_worker_tasks:
        if not task.done():
            task.cancel()
    # Дожидаемся завершения всех отменённых задач
    results = await asyncio.gather(*_voice_worker_tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
            logger.warning("Voice worker stopped with error: %s", r)
    count = len(_voice_worker_tasks)
    _voice_worker_tasks.clear()
    logger.info("Voice transcription workers stopped (%d tasks)", count)


async def _voice_worker() -> None:
    """Фоновый обработчик очереди голосовой транскрипции.

    Бесконечный цикл: забирает задание из очереди, транскрибирует,
    чистит файл, отвечает пользователю и передаёт текст в _process_text.
    При крахе одной задачи не падает — логирует и идёт дальше.

    Если внутренний цикл неожиданно прерывается (неперехваченная ошибка),
    автоматически перезапускает worker после паузы в 5 секунд.
    """
    while True:
        try:
            while True:
                got_job = False
                try:
                    job = await _voice_queue.get()
                    got_job = True
                    voice_path = job.voice_path
                    message = job.message
                    # _state_str зарезервирован для будущего использования:
                    # может понадобиться при асинхронном восстановлении FSM-контекста
                    # после транскрипции голосового сообщения.
                    _state_str = (
                        job.state_str
                    )  # string | None — FSMContext value, NOT the FSMContext object
                    userbot_manager = job.userbot_manager
                    file_unique_id = job.file_unique_id
                    mode = job.mode
                    api_provider = job.api_provider
                    openai_key = job.openai_key
                    gemini_key = job.gemini_key
                    mistral_key = job.mistral_key
                    custom_stt_key = job.custom_stt_key
                    custom_stt_model = job.custom_stt_model
                    custom_stt_endpoint = job.custom_stt_endpoint

                    try:
                        try:
                            text = await transcription_service.transcribe(
                                voice_path,
                                file_id=file_unique_id,
                                mode=mode,
                                openai_key=openai_key,
                                gemini_key=gemini_key,
                                mistral_key=mistral_key,
                                api_provider=api_provider,
                                custom_stt_key=custom_stt_key,
                                custom_stt_model=custom_stt_model,
                                custom_stt_endpoint=custom_stt_endpoint,
                            )
                        except (RequestError, HTTPStatusError, ValueError):
                            logger.exception("voice transcription failed in worker")
                            try:
                                await message.answer(
                                    "❌ Не удалось распознать голосовое."
                                )
                            except TelegramAPIError:
                                logger.exception(
                                    "failed to send error message from worker"
                                )
                            continue

                        text = (text or "").strip()
                        if not text:
                            try:
                                await message.answer(
                                    "🎙 Не услышал текста в этом сообщении."
                                )
                            except TelegramAPIError:
                                logger.exception(
                                    "failed to send empty transcription message"
                                )
                            continue

                        try:
                            # Определяем, вопрос ли это — для кнопки-подсказки
                            _is_q = _is_question(text)
                            _voice_kb = None
                            if _is_q:
                                from aiogram.types import (
                                    InlineKeyboardButton as _IKB,
                                    InlineKeyboardMarkup as _IKM,
                                )

                                _voice_kb = _IKM(
                                    inline_keyboard=[
                                        [
                                            _IKB(
                                                text="🔍 Исследовать это",
                                                callback_data=f"voice_research:{message.from_user.id}",
                                            )
                                        ]
                                    ]
                                )
                            await message.answer(
                                sanitize_html(f"🎙 <i>Услышал:</i> {text}"),
                                reply_markup=_voice_kb,
                            )
                            if not _is_q:
                                try:
                                    from aiogram.types import ReactionTypeEmoji

                                    await message.react([ReactionTypeEmoji(emoji="✅")])
                                except Exception:
                                    pass  # реакция не критична
                        except TelegramAPIError:
                            logger.exception("failed to send transcription result")

                        # ── Сохраняем метаданные транскрипции для контекста ──
                        transcription_meta = {
                            "is_transcription": True,
                            "provider": api_provider,
                            "language": "ru",
                            "length": len(text),
                        }
                        try:
                            from src.core.memory import conversation_context as _cc

                            await _cc.set_transcription_meta(
                                message.from_user.id, transcription_meta
                            )
                        except asyncio.CancelledError:
                            raise  # propagate to outer handler for clean shutdown
                        except Exception:
                            logger.debug(
                                "Failed to save transcription_meta", exc_info=True
                            )

                        try:
                            # Scan transcribed text for prompt injection (M-33)
                            _voice_scan = scan_content(
                                text, f"voice:{message.from_user.id}"
                            )
                            if _voice_scan.blocked:
                                logger.warning(
                                    "Prompt injection blocked in voice transcription from user %d: %s",
                                    message.from_user.id,
                                    _voice_scan.category,
                                )
                                try:
                                    await message.answer(
                                        "⚠️ Распознанный текст содержит потенциально опасные конструкции и был заблокирован."
                                    )
                                except TelegramAPIError:
                                    pass
                                continue

                            # State is stale in background worker — pass None.
                            # Any code needing FSMContext methods will log a warning and skip.
                            _vuid = (
                                str(message.from_user.id) if message.from_user else "0"
                            )
                            with start_span(
                                "message.process", user_id=_vuid, text_len=len(text)
                            ):
                                await _process_text(
                                    text, message, None, userbot_manager
                                )
                        except asyncio.CancelledError:
                            raise  # propagate to outer handler for clean shutdown
                        except Exception:
                            logger.exception(
                                "Failed to process transcribed text in worker"
                            )

                        # ── Сохраняем транскрибированный текст в память ──
                        try:
                            from src.core.memory.session_recorder import record_turn

                            uid = message.from_user.id if message.from_user else None
                            if uid is not None:
                                async with get_session() as rec_session:
                                    await record_turn(rec_session, uid, "user", text)
                                    await record_turn(
                                        rec_session,
                                        uid,
                                        "assistant",
                                        "(ответ отправлен)",
                                    )
                        except SQLAlchemyError:
                            logger.warning(
                                "Failed to record voice transcription turn for user %s",
                                message.from_user.id
                                if message.from_user
                                else "unknown",
                            )
                    finally:
                        _cleanup_voice_file(voice_path)

                except asyncio.CancelledError:
                    raise  # propagate to outer handler for clean shutdown
                except Exception:
                    logger.exception("Voice worker error")
                    try:
                        from src.core.infra.hooks import hooks

                        await hooks.emit(
                            "on_error",
                            error="Voice worker error",
                            context="free_text._voice_worker",
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.debug("Voice worker hooks.emit failed", exc_info=True)
                finally:
                    if got_job:
                        _voice_queue.task_done()

        except asyncio.CancelledError:
            break  # intentional shutdown
        except Exception:
            logger.critical("Voice worker crashed, restarting in 5s", exc_info=True)
            try:
                from src.core.infra.hooks import hooks

                await hooks.emit(
                    "on_error",
                    error="Voice worker crashed",
                    context="free_text._voice_worker",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Voice worker crash hooks.emit failed", exc_info=True)
            await asyncio.sleep(5.0)


def _cleanup_voice_file(voice_path: Path) -> None:
    """Безопасно удалить временный файл голосового сообщения."""
    try:
        voice_path.unlink(missing_ok=True)
    except (OSError, PermissionError):
        logger.debug("cleanup voice file failed: %s", voice_path, exc_info=True)


async def _process_text_fallback(
    raw: str,
    provider,
    message: Message,
    state: FSMContext | None,
    userbot_manager: UserbotManager,
    tz_name: str,
    owner_telegram_id: int,
    history_block: str,
    plan,
    turn_started: float,
    now_local_str: str,
) -> None:
    """Stage 9: Fallback — route_intent → _dispatch (extracted for reuse from background tasks)."""
    try:
        intent = await route_intent(
            provider,
            raw,
            heavy=False,
            now_local=now_local_str,
            tz_name=tz_name,
            history_block=history_block,
            memory_context=getattr(plan, "memory_context", "") or None,
            user_id=owner_telegram_id,
        )
    except Exception as e:
        logger.exception("agent route_intent failed")
        err_msg = safe_str(e)
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="intent",
            success=False,
            error=err_msg[:4000],
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        if len(err_msg) > 300:
            err_msg = err_msg[:300] + "…"
        await message.answer(
            sanitize_html(
                f"❌ Ошибка при обработке запроса.\n\n"
                f"<code>{err_msg}</code>\n\n"
                "<i>Если ошибка повторяется — проверь ключ в /settings → 🔑 API-ключи "
                "и модель в /settings → 🤖 LLM.</i>"
            )
        )
        return

    if intent.get("intent") == "multi":
        actions = intent.get("actions") or []
        if not isinstance(actions, list) or not actions:
            await message.answer("Не понял, что сделать.")
            return
        for sub in actions:
            await _dispatch(sub, message, state, userbot_manager, tz_name=tz_name)
    elif "intents" in intent:
        for sub in intent["intents"]:
            await _dispatch(sub, message, state, userbot_manager, tz_name=tz_name)
    else:
        await _dispatch(intent, message, state, userbot_manager, tz_name=tz_name)

    await _save_intent_context(owner_telegram_id, intent)

    _fire_record_trajectory(
        owner_telegram_id,
        request_text=raw,
        route_mode="intent",
        intent_json=intent,
        actions_json=actions_from_intent(intent),
        response_text=_summarize_intent_for_memory(intent),
        success=True,
        latency_ms=int((time.monotonic() - turn_started) * 1000),
    )

    summary = _summarize_intent_for_memory(intent)
    await ctx_store.add_turn(message.from_user.id, raw, summary)
    try:
        if plan and plan.tasks:
            await ctx_store.set_last_purpose(
                message.from_user.id, plan.tasks[0].purpose.value
            )
    except Exception:
        logger.exception("failed to set last purpose")


# ── INSTANT bypass helpers ─────────────────────────────────────────


def _get_classify_mode(classification: dict) -> str | None:
    """Определяет режим ответа на основе результата Stage -2 классификатора.

    Если классификация содержит категории, для которых не нужен LLM —
    возвращает "INSTANT", чтобы пропустить Stage 0-3 полностью.
    """
    if not classification:
        return None
    # Категории, для которых достаточно мгновенного ответа без LLM
    instant_categories = {"agreement", "gratitude", "emotion"}
    if any(classification.get(cat) for cat in instant_categories):
        return "INSTANT"
    return None


def _instant_response(text: str, classification: dict, message: Message) -> str | None:
    """Генерирует мгновенный протокольный ответ без LLM, памяти и планирования.

    Использует результат Stage -2 классификатора + эвристики по тексту.
    Возвращает None если сообщение не подходит для мгновенного ответа —
    тогда пайплайн продолжается в Stage 0.
    """
    text_lower = text.strip().lower()

    # Согласие (ага, ок, да, понял, ладно, etc.)
    if classification.get("agreement"):
        responses = ["👍", "👌", "🤝", "ага", "ок", "добро"]
        return random.choice(responses)

    # Благодарность
    if classification.get("gratitude"):
        return "😊"

    # Эмоции (смех, удивление, etc.)
    if classification.get("emotion"):
        # Смех / laughter
        _laugh_markers = (
            "ха",
            "ахах",
            "хех",
            "hehe",
            "lol",
            "lmao",
            "ржа",
            "ржу",
            "смех",
            "смешно",
            "угар",
        )
        if any(m in text_lower for m in _laugh_markers):
            return random.choice(["😂", "😄", "😆", "ахах"])

        # Удивление / surprise
        _surprise_markers = ("ого", "вау", "ничего себе", "wow", "огонь", "обалдеть")
        if any(m in text_lower for m in _surprise_markers):
            return random.choice(["😮", "😯", "ого"])

        # Общая эмоция
        return random.choice(["😊", "👍", "ок"])

    # Короткая команда (отправь, напиши, найди — но коротко)
    if classification.get("command"):
        return "👀"

    # Очень короткое сообщение — универсальный мгновенный ответ
    if len(text_lower) < 10:
        return random.choice(["👍", "ок", "м"])

    # Не подходит для мгновенного ответа — продолжаем пайплайн
    return None


async def _process_text(
    raw: str,
    message: Message,
    state: FSMContext | None,
    userbot_manager: UserbotManager,
    session=None,
) -> None:

    turn_started = time.monotonic()

    # Rate-limit: не чаще 1 запроса в 3 секунды на пользователя
    if not await check_rate_limit(message.from_user.id):
        await message.answer("⏳ Подожди пару секунд, обрабатываю предыдущий запрос…")
        return

    ctx = await _get_owner_context(message.from_user.id, session)
    tz_name = str(ctx["tz_name"])
    owner_telegram_id = int(ctx["owner_telegram_id"])  # type: ignore[arg-type]
    use_heavy = bool(ctx["use_heavy"])

    now_local_str = now_in_tz(tz_name).strftime("%Y-%m-%d %H:%M")
    history_block = await ctx_store.render_history_block(message.from_user.id)

    # ── Stage -2: Trie/Aho-Corasick classifier (additive, pre-pipeline) ──
    # Fast O(n) classification to skip expensive LLM checks for trivial messages.
    # Does NOT replace pre_gate — it augments it by catching more patterns.
    if settings.classifier_enabled:
        try:
            classification = _classify_message(raw)
            logger.debug("Classifier result: %s", classification)
        except Exception:
            logger.debug("Classifier failed, proceeding normally", exc_info=True)
            classification = None
    else:
        classification = None

    # ── Classifier fast-path: greeting/trivial/farewell → pre_gate response ──
    if classification and (
        classification.get("greeting")
        or classification.get("farewell")
        or (classification.get("trivial") and not classification.get("command"))
    ):
        try:
            from src.core.intelligence.pre_gate import check_pre_gate

            gate_resp = check_pre_gate(raw)
            if gate_resp:
                await message.answer(sanitize_html(gate_resp))
                # Record turn
                try:
                    from src.core.memory.session_recorder import record_turn

                    async with get_session() as rec_session:
                        await record_turn(
                            rec_session, message.from_user.id, "user", raw[:100]
                        )
                        await record_turn(
                            rec_session,
                            message.from_user.id,
                            "assistant",
                            gate_resp[:100],
                        )
                except Exception:
                    logger.debug("Failed to record classifier gate turn", exc_info=True)
                _fire_record_trajectory(
                    owner_telegram_id,
                    request_text=raw,
                    route_mode="classifier_gate",
                    intent_json={
                        "intent": "greeting",
                        "classification": classification,
                    },
                    response_text=gate_resp,
                    success=True,
                    latency_ms=int((time.monotonic() - turn_started) * 1000),
                )
                return
        except Exception:
            logger.debug("Classifier fast-path failed, continuing", exc_info=True)

    # ── INSTANT bypass: пропускаем Stage 0-3 для мгновенных ответов ──
    # Срабатывает после классификатора, но ДО извлечения фактов и LLM.
    # Экономит токены и latency для сообщений типа «ага», «спс», «😂», «ок».
    if classification:
        classify_mode = _get_classify_mode(classification)
        if classify_mode == "INSTANT":
            # Edge-кейсы: не отвечаем мгновенно на URL, forwarded и @mention
            _has_url = bool(_URL_RE.search(raw))
            _is_forwarded = bool(
                message.forward_date
                or message.forward_from
                or message.forward_from_chat
                or message.forward_from_message_id
            )
            _has_mention = any(
                ent.type in ("mention", "text_mention")
                for ent in (message.entities or [])
            )

            if not _has_url and not _is_forwarded and not _has_mention:
                response = _instant_response(raw, classification, message)
                if response is not None:
                    await message.answer(response)

                    # Запись в историю диалога (best-effort)
                    try:
                        from src.core.memory.session_recorder import record_turn

                        async with get_session() as rec_session:
                            await record_turn(
                                rec_session,
                                message.from_user.id,
                                "user",
                                raw[:100],
                            )
                            await record_turn(
                                rec_session,
                                message.from_user.id,
                                "assistant",
                                response[:100],
                            )
                    except Exception:
                        logger.debug("Failed to record instant turn", exc_info=True)

                    _fire_record_trajectory(
                        owner_telegram_id,
                        request_text=raw,
                        route_mode="classifier_instant",
                        intent_json={
                            "intent": "instant",
                            "classification": classification,
                        },
                        response_text=response,
                        success=True,
                        latency_ms=int((time.monotonic() - turn_started) * 1000),
                    )

                    # Эмитим хук on_message_processed
                    try:
                        from src.core.infra.hooks import hooks

                        await hooks.emit(
                            "on_message_processed",
                            user_id=str(owner_telegram_id),
                            raw=raw[:200],
                            mode="instant",
                            response=response[:200],
                        )
                    except Exception:
                        logger.debug(
                            "hooks.emit failed for instant bypass", exc_info=True
                        )

                    return  # Полностью пропускаем Stage 0-3

    # ── Stage -1: Background fact extraction (enqueue) ───────────────
    # Без этого extract_and_save_memories() НЕ вызывается в main flow,
    # а значит supersedes evolution chains в Stage 0c не работают:
    # 5-минутное окно в check_contradiction_response остаётся пустым,
    # потому что новый факт физически не создаётся между двумя ходами
    # пользователя. pre_filter отсекает шумовые сообщения, чтобы не
    # тратить LLM-токены на «привет», «ок», «ага» и т.п.
    try:
        from src.core.memory._queue_core import MemoryJob, enqueue
        from src.core.memory.pre_filter import should_extract

        if should_extract(raw):
            await enqueue(
                MemoryJob(
                    telegram_id=owner_telegram_id,
                    messages_text=raw,
                    job_type="extract",
                    source="chat",
                )
            )
            # ---- Phase 2: record pre-filter accept ----
            try:
                from src.core.memory.memory_metrics import memory_metrics

                await memory_metrics.record_pre_filter(accepted=True)
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
        else:
            # ---- Phase 2: record pre-filter reject ----
            try:
                from src.core.memory.memory_metrics import memory_metrics

                await memory_metrics.record_pre_filter(accepted=False)
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
    except Exception:
        logger.debug("Background extract enqueue failed", exc_info=True)

    # ── Stage 0: Smart emoji/sticker replies ─────────────────────────
    from src.core.contacts.smart_reply import get_simple_reply

    emoji_reply = get_simple_reply(raw)
    if emoji_reply:
        await message.answer(emoji_reply)
        # Сохраняем в историю диалога
        try:
            from src.core.memory.session_recorder import record_turn

            async with get_session() as rec_session:
                await record_turn(rec_session, message.from_user.id, "user", raw[:100])
                await record_turn(
                    rec_session, message.from_user.id, "assistant", emoji_reply[:100]
                )
        except Exception:
            logger.debug("Failed to record smart_reply turn", exc_info=True)
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="smart_reply",
            intent_json={"intent": "smart_reply"},
            response_text=emoji_reply,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # ── Stage 0b: Memory correction detection (Feature 2) ────────────
    from src.core.contacts.smart_reply import (
        detect_memory_correction,
        handle_memory_correction,
    )

    correction = detect_memory_correction(raw)
    if correction:
        response = await handle_memory_correction(correction, owner_telegram_id)

        # ── Humanizer feedback loop ───────────────────────────────
        # Если пользователь поправляет бота — последний humanized-ответ
        # был отвергнут. Записываем фидбек.
        last_humanized = _pop_last_humanized(owner_telegram_id)
        if last_humanized:
            record_humanizer_feedback(
                user_id=owner_telegram_id,
                original=last_humanized,
                corrected=raw,
                accepted=False,
            )
        # ── End feedback loop ─────────────────────────────────────

        await message.answer(response)
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="memory_correction",
            intent_json={"intent": "memory_correction", "action": correction["action"]},
            response_text=response,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # ── Stage 0c: Contradiction detection ────────────────────────────
    from src.core.memory.contradiction_detector import (
        check_contradiction_response,
        detect_contradiction,
        store_pending_contradiction,
    )

    # Check if this message is a response to a pending contradiction question
    cr_response = await check_contradiction_response(owner_telegram_id, raw)
    if cr_response:
        await message.answer(cr_response)
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="contradiction_response",
            intent_json={"intent": "contradiction_response"},
            response_text=cr_response,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # Check for new contradictions against stored facts
    contradiction = await detect_contradiction(owner_telegram_id, raw)
    if contradiction:
        await store_pending_contradiction(owner_telegram_id, contradiction)
        await message.answer(
            sanitize_html(
                f"🤔 {contradiction['suggestion']}\n"
                f"(уверенность: {contradiction['confidence']:.0%})"
            )
        )
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="contradiction",
            intent_json={"intent": "contradiction"},
            response_text=contradiction["suggestion"],
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # ── Stage 0d: Smart correction / cancellation detection ──────────
    from src.bot.handlers.smart_correction import (
        apply_correction,
        detect_correction,
    )

    correction = await detect_correction(owner_telegram_id, raw)
    if correction:
        reply = await apply_correction(owner_telegram_id, correction)
        await message.answer(reply)

        # ── Learn from correction (Feature: Learning from Corrections) ──
        try:
            from src.core.intelligence.correction_learner import learn_correction

            if correction["action"] == "cancel":
                await learn_correction(
                    owner_telegram_id,
                    original_text="[cancelled]",
                    corrected_text="",
                    feedback_type="cancel",
                )
            elif correction["action"] == "replace":
                new_text = correction.get("new_text", "")
                is_fact = any(
                    w in (new_text or "").lower() for w in ("факт", "помню", "знаю")
                )
                await learn_correction(
                    owner_telegram_id,
                    original_text=raw,
                    corrected_text=new_text,
                    feedback_type="fact" if is_fact else "style",
                )
        except Exception:
            logger.debug(
                "Correction learner failed", exc_info=True
            )  # never break core flow

        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="smart_correction",
            intent_json={"intent": "smart_correction", "action": correction["action"]},
            response_text=reply,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # ── Stage 0e: Singalong — подпевание строчками из песен ───────────
    # Flow: LLM определяет песню → спрашивает подтверждение → подпевает
    # Если отказано → ищет через DuckDuckGo → снова спрашивает
    # ⚠️ Ответ отправляется НАПРЯМУЮ через message.answer(), минуя humanizer.
    # Все LLM output проходит через sanitize_html() для защиты от HTML injection.
    from src.core.intelligence.singalong import (
        _is_confirmation,
        _looks_like_lyrics,
        identify_and_get_next_line,
        get_singalong_reply,
        consume_pending_singalong,
        peek_pending_singalong,
        store_pending_singalong,
    )

    # Импорт _search_lyrics для denial flow
    from src.core.intelligence.singalong import _search_lyrics

    async def _singalong_identify(
        text: str,
        telegram_id: int,
        use_heavy: bool,
        *,
        search_hint: str | None = None,
        session=None,
    ) -> dict | None:
        """Определить песню через LLM. Возвращает {song, artist, next_line} или None."""
        if session is None:
            async with get_session() as session:
                owner = await get_or_create_user(session, telegram_id)
                provider = await build_provider(
                    session, owner, purpose="main", task_type=TaskType.DEFAULT
                )
        else:
            owner = await get_or_create_user(session, telegram_id)
            provider = await build_provider(
                session, owner, purpose="main", task_type=TaskType.DEFAULT
            )
        if not provider:
            return None
        return await identify_and_get_next_line(
            text, provider, heavy=use_heavy, search_hint=search_hint
        )

    async def _singalong_get_reply(
        text: str,
        telegram_id: int,
        use_heavy: bool,
        session=None,
    ) -> str | None:
        """Получить следующую строчку через LLM."""
        if session is None:
            async with get_session() as session:
                owner = await get_or_create_user(session, telegram_id)
                provider = await build_provider(
                    session, owner, purpose="main", task_type=TaskType.DEFAULT
                )
        else:
            owner = await get_or_create_user(session, telegram_id)
            provider = await build_provider(
                session, owner, purpose="main", task_type=TaskType.DEFAULT
            )
        if not provider:
            return None
        return await get_singalong_reply(text, provider, heavy=use_heavy)

    async def _ask_singalong_confirmation(
        lyrics: str,
        identified: dict | None,
    ) -> None:
        """Общий хелпер: сохранить pending и отправить подтверждение."""
        if identified and identified.get("song"):
            title = identified["song"]
            artist = identified.get("artist", "")
            display = f"{title}" + (f" — {artist}" if artist else "")
            await store_pending_singalong(
                owner_telegram_id,
                lyrics,
                song_title=display,
                next_line=identified.get("next_line"),
            )
            await message.answer(sanitize_html(f"Это {display}? Подпевать? 🎵"))
        else:
            await store_pending_singalong(owner_telegram_id, lyrics)
            await message.answer("Подпевать тебе? 🎵")

    async def _try_singalong() -> bool:
        """Stage 0e: попробовать обработать как текст песни. Возвращает True если сообщение consumed."""
        try:
            pending = await peek_pending_singalong(owner_telegram_id)

            # ── Pending exists ──────────────────────────────────────
            if pending:
                # Новая песня при существующем pending — заменяем
                if _looks_like_lyrics(raw):
                    await consume_pending_singalong(owner_telegram_id)
                    await _singalong_search_cache.invalidate(owner_telegram_id)
                    identified = await _singalong_identify(
                        raw, owner_telegram_id, use_heavy
                    )
                    await _ask_singalong_confirmation(raw, identified)
                    _fire_record_trajectory(
                        owner_telegram_id,
                        request_text=raw,
                        route_mode="singalong_ask",
                        intent_json={"intent": "singalong", "phase": "ask"},
                        response_text="Подпевать тебе? 🎵",
                        success=True,
                        latency_ms=int((time.monotonic() - turn_started) * 1000),
                    )
                    return True

                # Проверить confirmation/denial
                decision = _is_confirmation(raw)

                # ── Подтверждение ───────────────────────────────────
                if decision is True:
                    data = await consume_pending_singalong(owner_telegram_id)
                    if not data:
                        return False  # pending истёк

                    if data.get("next_line"):
                        await message.answer(sanitize_html(data["next_line"]))
                        _fire_record_trajectory(
                            owner_telegram_id,
                            request_text=raw,
                            route_mode="singalong",
                            intent_json={"intent": "singalong"},
                            response_text=data["next_line"],
                            success=True,
                            latency_ms=int((time.monotonic() - turn_started) * 1000),
                        )
                        return True

                    # Нет next_line — вызываем LLM
                    reply = await _singalong_get_reply(
                        data["lyrics"], owner_telegram_id, use_heavy
                    )
                    if reply:
                        await message.answer(sanitize_html(reply))
                        _fire_record_trajectory(
                            owner_telegram_id,
                            request_text=raw,
                            route_mode="singalong",
                            intent_json={"intent": "singalong"},
                            response_text=reply,
                            success=True,
                            latency_ms=int((time.monotonic() - turn_started) * 1000),
                        )
                        return True
                    await message.answer("Не узнал эту песню \U0001f914")
                    return True

                # ── Отклонение ──────────────────────────────────────
                if decision is False:
                    data = await consume_pending_singalong(owner_telegram_id)
                    if not data:
                        return False  # pending истёк

                    # Ищем через DuckDuckGo (общий хелпер из singalong)
                    search_items = await _search_lyrics(data["lyrics"])

                    if search_items:
                        variants = []
                        for i, item in enumerate(search_items[:3], 1):
                            t = sanitize_html(item.get("title", "?"))
                            variants.append(f"{i}. {t}")
                        text = "Какая из этих?\n" + "\n".join(variants)
                        await message.answer(text)

                        # Сохраняем pending для numeric selection
                        await store_pending_singalong(
                            owner_telegram_id,
                            data["lyrics"],
                            song_title=search_items[0].get("title", ""),
                            next_line=None,
                        )
                        await _singalong_search_cache.set(
                            owner_telegram_id, search_items[:3]
                        )
                        _fire_record_trajectory(
                            owner_telegram_id,
                            request_text=raw,
                            route_mode="singalong_search",
                            intent_json={"intent": "singalong", "phase": "search"},
                            response_text=text,
                            success=True,
                            latency_ms=int((time.monotonic() - turn_started) * 1000),
                        )
                        return True

                    await message.answer("Не нашёл такую песню в интернете \U0001f914")
                    _fire_record_trajectory(
                        owner_telegram_id,
                        request_text=raw,
                        route_mode="singalong_fail",
                        intent_json={"intent": "singalong", "result": "not_found"},
                        response_text="Не нашёл такую песню",
                        success=True,
                        latency_ms=int((time.monotonic() - turn_started) * 1000),
                    )
                    return True

                # ── Numeric selection (1/2/3) после поиска ──────────
                stripped_num = raw.strip()
                if stripped_num in ("1", "2", "3"):
                    cache = await _pop_singalong_search(owner_telegram_id)
                    if cache is None:
                        return False
                    idx = int(stripped_num) - 1
                    if 0 <= idx < len(cache):
                        chosen = cache[idx]
                        title = chosen.get("title", "")
                        # Re-peek чтобы не использовать stale pending
                        current = await peek_pending_singalong(owner_telegram_id)
                        if not current:
                            return False
                        # Пытаемся определить next_line через LLM с контекстом
                        identified = await _singalong_identify(
                            current.get("lyrics", raw),
                            owner_telegram_id,
                            use_heavy,
                            search_hint=title,
                        )
                        if identified and identified.get("next_line"):
                            await message.answer(sanitize_html(identified["next_line"]))
                            await consume_pending_singalong(owner_telegram_id)
                            _fire_record_trajectory(
                                owner_telegram_id,
                                request_text=raw,
                                route_mode="singalong",
                                intent_json={"intent": "singalong", "selected": title},
                                response_text=identified["next_line"],
                                success=True,
                                latency_ms=int(
                                    (time.monotonic() - turn_started) * 1000
                                ),
                            )
                            return True
                        # LLM не смог — спрашиваем уточнение
                        await consume_pending_singalong(owner_telegram_id)
                        await _singalong_search_cache.invalidate(owner_telegram_id)
                        await message.answer(
                            f"Не могу найти текст «{sanitize_html(title)}». Напиши название песни?"
                        )
                        return True
                    # Неверный номер — очищаем кеш
                    await _singalong_search_cache.invalidate(owner_telegram_id)

                # decision is None + не numeric — другое сообщение, идём дальше
                return False

            # ── Нет pending — новое сообщение ───────────────────────
            # Clean stale search cache from previous sessions
            await _singalong_search_cache.invalidate(owner_telegram_id)

            if _looks_like_lyrics(raw):
                identified = await _singalong_identify(
                    raw, owner_telegram_id, use_heavy
                )
                await _ask_singalong_confirmation(raw, identified)
                _fire_record_trajectory(
                    owner_telegram_id,
                    request_text=raw,
                    route_mode="singalong_ask",
                    intent_json={"intent": "singalong", "phase": "ask"},
                    response_text="Подпевать тебе? 🎵",
                    success=True,
                    latency_ms=int((time.monotonic() - turn_started) * 1000),
                )
                return True

            return False

        except Exception:
            logger.warning("singalong check failed", exc_info=True)
            return False

    if await _try_singalong():
        return

    # Stage 1: Adaptive instructions
    if await check_instructions(raw, owner_telegram_id, message):
        return

    # Stage 1b: Contact-specific rules (e.g. "с Олей будь вежливее")
    if await check_contact_rules(raw, owner_telegram_id, message, userbot_manager):
        return

    # Stage 2: Adaptive persona
    if await check_persona(raw, owner_telegram_id, message):
        return

    # Stage 3: Follow-up context
    if await check_followup(
        raw,
        owner_telegram_id,
        message,
        state,  # type: ignore[arg-type]
        userbot_manager,
        tz_name,
        turn_started,
    ):
        return

    # Stage 4: Smart AutoRouter
    _last_purpose = None
    try:
        _last_purpose = await ctx_store.get_last_purpose(message.from_user.id)
    except Exception:
        logger.exception("failed to get last purpose")

    # S1-T1: получить prefetched recall результат если есть
    _prefetched_ctx: str | None = None
    if settings.prefetch_recall_enabled:
        try:
            from src.core.memory.prefetch_recall import get_prefetched_recall

            _pf_data = await get_prefetched_recall(owner_telegram_id)
            if _pf_data:
                _prefetched_ctx = _pf_data.get("memory_context", "") or None
        except Exception:
            logger.debug(
                "Prefetched recall unavailable", exc_info=True
            )  # prefetch — оптимизация

    # ── Progress: вспоминаем контекст перед планированием ─────────
    progress_msg = await message.answer("🧠 Вспоминаю контекст…")

    plan = await make_plan(
        raw,
        owner_telegram_id,
        heavy_available=use_heavy,
        last_purpose=_last_purpose,
        prefetched_context=_prefetched_ctx,
    )

    # ── Progress: план готов, думаем ───────────────────────────────
    try:
        await progress_msg.edit_text("💭 Думаю…")
    except TelegramAPIError:
        pass  # сообщение могло быть удалено

    if plan is None:
        return
    if plan.tasks:
        t0 = plan.tasks[0]
        logger.debug(
            "AutoRouter plan: risk=%s purpose=%s heavy=%s cache_ttl=%d agents=%s",
            t0.risk.value,
            t0.purpose.value,
            t0.heavy,
            t0.cache_ttl,
            t0.need_agents or "—",
        )

    # ── S2-T5: FAST_ROUTE shortcut — кэш-hit пропускает полный пайплайн ──
    _route_cache_hit = plan.metrics.get("route_cache_hit", False)
    if _route_cache_hit and plan.response_mode in ("instant", "fast_route"):
        logger.debug(
            "S2-T5 FAST_ROUTE shortcut: cache hit, mode=%s, skipping provider",
            plan.response_mode,
        )
        if plan.response_mode == "instant":
            await execute_instant(
                plan, message, raw, owner_telegram_id, turn_started, tz_name=tz_name
            )
        else:
            # FAST_ROUTE cache hit: pre_gate + humanize → send
            await execute_instant(
                plan, message, raw, owner_telegram_id, turn_started, tz_name=tz_name
            )
        return

    # Stage 5: INSTANT mode
    if plan.response_mode == "instant" and plan.final_response:
        await execute_instant(
            plan, message, raw, owner_telegram_id, turn_started, tz_name=tz_name
        )
        return

    # Stage 6: Build provider (Single session per request optimization)
    purpose = (
        plan.tasks[0].purpose.value if plan.tasks and plan.tasks[0].purpose else "main"
    )
    if session is None:
        async with get_session() as session:
            owner_db = await get_or_create_user(session, owner_telegram_id)
            provider = await build_provider(
                session, owner_db, purpose=purpose, task_type=TaskType.DEFAULT
            )
            if provider is None and purpose != "main":
                logger.debug("No key for purpose '%s', falling back to main", purpose)
                provider = await build_provider(
                    session, owner_db, purpose="main", task_type=TaskType.DEFAULT
                )
    else:
        owner_db = await get_or_create_user(session, owner_telegram_id)
        provider = await build_provider(
            session, owner_db, purpose=purpose, task_type=TaskType.DEFAULT
        )
        if provider is None and purpose != "main":
            logger.debug("No key for purpose '%s', falling back to main", purpose)
            provider = await build_provider(
                session, owner_db, purpose="main", task_type=TaskType.DEFAULT
            )

    if provider is None:
        await message.answer(
            "Чтобы я мог понимать свободный текст — добавь LLM-ключ в /settings → 🔑 API-ключи."
        )
        return

    # ── Smart Model Routing: переопределяем тяжёлую/лёгкую модель ──
    if settings.smart_routing_enabled and plan.model_mode:
        try:
            if plan.model_mode == "light":
                provider._default_heavy = False  # type: ignore[attr-defined]
                logger.debug("SmartRouter override: forcing LIGHT model")
            elif plan.model_mode == "heavy":
                provider._default_heavy = True  # type: ignore[attr-defined]
                logger.debug("SmartRouter override: forcing HEAVY model")
        except Exception:
            logger.debug("SmartRouter override failed", exc_info=True)

    # Stage 7: FAST_ROUTE
    if plan.response_mode == "fast_route":
        if state is None:
            # Голосовой путь: FSMContext недоступен, fallback к route_intent
            logger.debug(
                "fast_route skipped: state is None (voice transcription path), "
                "falling back to route_intent for user %d",
                owner_telegram_id,
            )
            await _process_text_fallback(
                raw,
                provider,
                message,
                state,
                userbot_manager,
                tz_name,
                owner_telegram_id,
                history_block,
                plan,
                turn_started,
                now_local_str,
            )
            return
        await execute_fast_route(
            raw,
            plan,
            provider,
            message,
            state,  # type: ignore[arg-type]
            userbot_manager,
            tz_name,
            owner_telegram_id,
            history_block,
            turn_started,
            now_local_str,
        )
        # Character evolution: fire-and-forget (никогда не блокирует)
        track_ff(
            asyncio.create_task(
                maybe_evolve_after_turn(owner_telegram_id, raw, None, provider)
            )
        )
        return

    # Stage 8: MAESTRO — heavy tasks run as background tasks for preemption
    if plan.response_mode == "maestro":
        if state is None:
            # Голосовой путь: FSMContext недоступен, maestro не может работать
            logger.debug(
                "maestro skipped: state is None (voice transcription path), "
                "falling back to route_intent for user %d",
                owner_telegram_id,
            )
            await _process_text_fallback(
                raw,
                provider,
                message,
                state,
                userbot_manager,
                tz_name,
                owner_telegram_id,
                history_block,
                plan,
                turn_started,
                now_local_str,
            )
            return
        injected_style: str | None = ctx.get("global_style_profile") or None  # type: ignore[assignment]

        async def _run_maestro_background():
            _my_task = asyncio.current_task()
            try:
                ok = await execute_maestro(
                    raw,
                    plan,
                    provider,
                    message,
                    state,
                    userbot_manager,
                    tz_name,
                    owner_telegram_id,
                    history_block,
                    turn_started,
                    injected_style,
                )
                # Character evolution: fire-and-forget после ответа
                track_ff(
                    asyncio.create_task(
                        maybe_evolve_after_turn(owner_telegram_id, raw, None, provider)
                    )
                )
                if not ok:
                    await _process_text_fallback(
                        raw,
                        provider,
                        message,
                        state,
                        userbot_manager,
                        tz_name,
                        owner_telegram_id,
                        history_block,
                        plan,
                        turn_started,
                        now_local_str,
                    )
            except asyncio.CancelledError:
                logger.debug("Maestro task cancelled for user %s", owner_telegram_id)
            except Exception as e:
                logger.exception(
                    "Maestro background task failed for user %s", owner_telegram_id
                )
                err_msg = safe_str(e)
                if len(err_msg) > 300:
                    err_msg = err_msg[:300] + "…"
                await message.answer(
                    sanitize_html(
                        f"❌ Ошибка при обработке запроса.\n\n"
                        f"<code>{err_msg}</code>\n\n"
                        "<i>Если ошибка повторяется — проверь ключ в /settings → 🔑 API-ключи "
                        "и модель в /settings → 🤖 LLM.</i>"
                    )
                )
            finally:
                async with _active_tasks_lock:
                    if _active_tasks.get(owner_telegram_id) is _my_task:
                        _active_tasks.pop(owner_telegram_id, None)

        task = asyncio.create_task(_run_maestro_background())
        async with _active_tasks_lock:
            _active_tasks[owner_telegram_id] = task
        await message.answer(_get_waiting_message())
        return

    # ── Event Bus: emit user message received ──────────────────────────
    try:
        from src.core.events.event_bus import event_bus, USER_MESSAGE_RECEIVED

        await event_bus.emit(
            USER_MESSAGE_RECEIVED, user_id=owner_telegram_id, text_len=len(raw)
        )
    except Exception:
        logger.debug("EventBus emit failed for USER_MESSAGE_RECEIVED", exc_info=True)

    # Stage 9: Fallback — route_intent → _dispatch
    await _process_text_fallback(
        raw,
        provider,
        message,
        state,
        userbot_manager,
        tz_name,
        owner_telegram_id,
        history_block,
        plan,
        turn_started,
        now_local_str,
    )
    # Character evolution: fire-and-forget
    track_ff(
        asyncio.create_task(
            maybe_evolve_after_turn(owner_telegram_id, raw, None, provider)
        )
    )


@router.message(F.text & ~F.text.startswith("/"))
async def free_text(
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    if await state.get_state() is not None:
        return
    raw = (message.text or "").strip()
    if not raw:
        return

    uid = message.from_user.id

    # Scan for prompt injection in user input
    scan_result = scan_content(raw, f"user:{uid}")
    if scan_result.blocked:
        logger.warning(
            "Prompt injection blocked from user %d: %s (%s)",
            uid,
            scan_result.category,
            scan_result.match,
        )
        await message.answer(
            "⚠️ Сообщение содержит потенциально опасные конструкции и было заблокировано.\n"
            "Если это ошибка — переформулируйте запрос."
        )
        return

    # 🎭 Onboarding: первый контакт → предложить настроить личность
    is_new = False
    async with get_session() as session:
        owner = await get_or_create_user(session, uid)

        # Atomically: UPDATE ... SET total_interactions=1 WHERE total_interactions=0
        # If rowcount==1, this is a new user (no race condition possible).
        # Если строки нет вообще — создаём новую запись AdaptivePersona.
        from src.db.models._learning import AdaptivePersona
        from sqlalchemy import update as sa_update

        result = await session.execute(
            sa_update(AdaptivePersona)
            .where(AdaptivePersona.user_id == owner.id)
            .where(AdaptivePersona.total_interactions == 0)
            .values(total_interactions=1)
        )
        if result.rowcount > 0:  # type: ignore[attr-defined]
            is_new = True
        else:
            # Проверяем, существует ли запись вообще
            from sqlalchemy import select as sa_select

            row_exists = await session.execute(
                sa_select(AdaptivePersona.id).where(AdaptivePersona.user_id == owner.id)
            )
            if row_exists.first() is None:
                session.add(AdaptivePersona(user_id=owner.id, total_interactions=1))
                is_new = True

    if is_new:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎭 Настроить личность",
                        callback_data="set:sec:personality",
                    ),
                    InlineKeyboardButton(
                        text="⏭ Пропустить", callback_data="persona:skip_onboarding"
                    ),
                ]
            ]
        )
        await message.answer(
            "🎭 <b>Привет! Давай настроим мой характер под тебя?</b>\n\n"
            "Я умею общаться в разных стилях: профессионально, дружелюбно, "
            "игриво, лаконично и даже с сарказмом!\n\n"
            "Это займёт меньше минуты и улучшит наше общение. "
            "В любой момент можно изменить в /settings → 🎭 Личность.",
            reply_markup=kb,
        )
        await _save_session_context_ff(uid, [raw[:500]])
        return

    if len(raw) > 2000:
        raw = raw[:1987] + "...(truncated)"

    # ── Stage -2: Contact prefetch (fire-and-forget) ───────────────────
    # Extract contact hints from message entities and reply context.
    # Prefetches contact data so later resolution can skip DB queries.
    if settings.contact_prefetch_enabled:
        try:
            _contact_hint = _extract_contact_hint(message)
            if _contact_hint is not None:
                # Fire-and-forget: prefetch contact data in background
                track_ff(
                    asyncio.create_task(
                        _do_prefetch_contact(uid, _contact_hint, userbot_manager)
                    )
                )
            else:
                # No hint found — prefetch the user's contact list anyway
                track_ff(
                    asyncio.create_task(
                        _do_prefetch_contact(uid, None, userbot_manager)
                    )
                )
        except Exception:
            logger.debug(
                "Contact prefetch failed", exc_info=True
            )  # never block message processing

    # ── S1-T1: Prefetch memory recall (fire-and-forget) ───────────────
    # Оптимистичный prefetch: запускаем recall в фоне до того,
    # как роутинг решит, нужна ли память. Если решит что нужна —
    # результат уже будет в кэше (экономия 50-500ms).
    if settings.prefetch_recall_enabled:
        try:
            from src.core.memory.prefetch_recall import prefetch_recall as _pf_recall

            track_ff(asyncio.create_task(_pf_recall(uid, raw)))
        except Exception:
            logger.debug(
                "Prefetch recall task creation failed", exc_info=True
            )  # prefetch — оптимизация, никогда не блокируем

    # ── Stage -1: Scheduled message NL intent ─────────────────────────
    # "напомни Маше про встречу завтра в 10:00" → создаём ScheduledMessage
    try:
        ctx_sched = await _get_owner_context(message.from_user.id)
        sched_tz = str(ctx_sched["tz_name"])
    except (SQLAlchemyError, KeyError, TypeError, AttributeError):
        sched_tz = None
    try:
        scheduled = parse_schedule_message(raw, sched_tz)
        if scheduled:
            async with get_session() as session:
                owner = await get_or_create_user(session, uid)
                from src.db.repo import create_scheduled as _create_scheduled

                await _create_scheduled(
                    session,
                    owner.id,
                    scheduled["contact"],
                    scheduled["text"],
                    scheduled["send_at"],
                )
                await session.commit()
    except KeyError as e:
        logger.warning(
            "parse_schedule_message returned incomplete result: missing %s", e
        )
        await message.answer("❌ Не удалось разобрать сообщение. Проверь формат.")
        return
    except (TypeError, ValueError) as e:
        logger.warning("parse_schedule_message error: %s", e)
        await message.answer("❌ Ошибка формата сообщения.")
        return
    except SQLAlchemyError:
        await message.answer("❌ Произошла ошибка. Попробуй ещё раз.")
        return
    except Exception as e:
        # Широкий catch для неожиданных ошибок парсинга расписания
        logger.warning("parse_schedule_message unexpected error: %s", e)
        await message.answer("❌ Не удалось обработать сообщение.")
        return

    if scheduled:
        send_at_str = scheduled["send_at"].strftime("%d.%m в %H:%M")
        await message.answer(
            sanitize_html(
                f"✅ Запланировано:\n"
                f"📤 <b>{scheduled['contact']}</b>\n"
                f"📝 {scheduled['text'][:100]}\n"
                f"🕐 {send_at_str}"
            )
        )
        # ── P2: сохраняем контекст сессии ──
        await _save_session_context_ff(uid, [raw[:500]])
        return

    # ── URL detection ──
    urls = _URL_RE.findall(raw)
    if urls:
        url = urls[0]
        is_pure_url = raw.strip() == url.strip()

        if is_pure_url:
            # Check URL summary cache first (skip HTTP + LLM if cached)
            cached = await _get_cached_url_summary(url)
            if cached:
                await message.answer(sanitize_html(f"📄 {cached}\n\n🔗 {url}"))
                await _save_session_context_ff(uid, [raw[:500]])
                return

            try:
                await message.answer(f"🔍 Читаю {url[:50]}...")
            except Exception:
                logger.debug("Failed to send URL reading notification", exc_info=True)
            content = await _fetch_url_content(url)
            if content:
                try:
                    async with get_session() as session:
                        owner_db = await get_or_create_user(
                            session, message.from_user.id
                        )
                        provider = await build_provider(
                            session, owner_db, task_type=TaskType.SUMMARIZE
                        )
                except SQLAlchemyError:
                    provider = None

                if provider:
                    try:
                        summary = await _summarize_url(url, content, provider)
                        await _set_url_cache(url, summary)
                        await message.answer(sanitize_html(f"📄 {summary}\n\n🔗 {url}"))
                    except Exception:
                        await message.answer(
                            sanitize_html(f"📄 {content[:1000]}...\n\n🔗 {url}")
                        )
                else:
                    await message.answer(
                        sanitize_html(f"📄 {content[:1000]}...\n\n🔗 {url}")
                    )
            else:
                try:
                    await message.answer(f"❌ Не удалось загрузить {url}")
                except Exception:
                    logger.debug("Failed to send URL error notification", exc_info=True)
            # ── P2: сохраняем контекст сессии ──
            await _save_session_context_ff(uid, [raw[:500]])
            return

    # Session resume check (P2): вернулся ли пользователь после перерыва
    resume_msg = await _check_session_resume(uid)
    if resume_msg:
        await message.answer(sanitize_html(resume_msg))

    # Priority preemption: if a heavy task is running, cancel it for the new request
    uid = message.from_user.id
    async with _active_tasks_lock:
        existing = _active_tasks.get(uid)
        if existing and not existing.done():
            logger.info(
                "Preempting running task for user %s with new request: %s",
                uid,
                raw[:80],
            )
            existing.cancel()
            _active_tasks.pop(uid, None)
            should_send_preempt = True
        else:
            should_send_preempt = False

    if should_send_preempt:
        await message.answer("⏯ Прервал предыдущую задачу. Обрабатываю новый запрос…")

    try:
        with start_span("message.process", user_id=str(uid), text_len=len(raw)):
            await _process_text(raw, message, state, userbot_manager)
    except asyncio.CancelledError:
        raise  # не перехватываем CancelledError — даём штатно завершиться
    except Exception:
        logger.exception("_process_text failed for user %s", uid)
        try:
            from src.core.infra.hooks import hooks

            await hooks.emit(
                "on_error",
                error=str(sys.exc_info()[1])
                if sys.exc_info()[1]
                else "_process_text failed",
                context="free_text.free_text",
            )
        except Exception:
            logger.debug(
                "free_text hooks.emit failed", exc_info=True
            )  # hooks are optional, never break core flow
        raise

    # ── P2: сохраняем контекст сессии (fire-and-forget) ──
    await _save_session_context_ff(uid, [raw[:500]])

    # ── P3: Episodic Memory — авто-создание эпизодов (fire-and-forget) ──
    if settings.episodic_memory_enabled:
        try:
            from src.core.memory.episodic import (
                should_create_episode,
                track_message,
                reset_counter,
                create_episode,
            )

            await track_message(uid, raw[:500])

            if should_create_episode(uid):
                # Снимок буфера без сброса счётчика (чтобы не терять данные при ошибке)
                from src.core.memory.episodic import _message_buffer

                buf = _message_buffer.get(uid, [])
                messages_batch = list(buf[-settings.episodic_batch_size :])

                async def _create_episode_ff():
                    try:
                        await create_episode(uid, messages_batch)
                        await reset_counter(
                            uid
                        )  # Сброс только после успешного создания
                    except Exception:
                        logger.debug(
                            "Fire-and-forget episode creation failed for user %d",
                            uid,
                            exc_info=True,
                        )

                track_ff(asyncio.create_task(_create_episode_ff()))
        except Exception:
            logger.debug("Episodic memory tracking failed", exc_info=True)

    # ── Session recording (non-blocking, best-effort) ─────────────────
    try:
        from src.core.memory.session_recorder import record_turn

        async with get_session() as rec_session:
            await record_turn(rec_session, uid, "user", raw[:4000])
            await record_turn(rec_session, uid, "assistant", "(ответ отправлен)")
    except SQLAlchemyError:
        logger.warning(
            "Failed to record conversation turn for user %s", uid, exc_info=True
        )


@router.message(F.voice | F.audio)
async def free_voice(
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    if await state.get_state() is not None:
        return

    media = message.voice or message.audio
    if media is None:
        return

    # 1. Быстрая загрузка настроек пользователя из БД
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        mode = owner.settings.transcription_mode
        openai_key = await get_api_key(session, owner, "openai")
        gemini_key = await get_api_key(session, owner, "gemini")
        mistral_key = await get_api_key(session, owner, "mistral")
        api_provider = getattr(owner.settings, "transcription_api_provider", "openai")

        # Retrieve STT-specific keys for Deepgram/AssemblyAI from LlmKeySlot
        custom_stt_key = None
        custom_stt_model = None
        custom_stt_endpoint = None
        if api_provider in ("deepgram", "assemblyai"):
            slots = await session.execute(
                select(LlmKeySlot).where(
                    and_(
                        LlmKeySlot.user_id == owner.id,
                        LlmKeySlot.provider == api_provider,
                        LlmKeySlot.category == "stt",
                    )
                )
            )
            slot = slots.scalar_one_or_none()
            if slot:
                try:
                    custom_stt_key = decrypt(slot.key_enc)
                except ValueError:
                    logger.warning(
                        "STT key decryption failed for provider=%s", api_provider
                    )
                    custom_stt_key = None
                custom_stt_model = slot.model
                custom_stt_endpoint = slot.endpoint

    # 2. Скачивание .ogg файла (быстрая сетевая операция)
    media_dir = settings.data_dir / "media" / "control_bot"
    media_dir.mkdir(parents=True, exist_ok=True)
    target = media_dir / f"{message.message_id}_{media.file_unique_id}.ogg"

    try:
        await message.bot.download(media.file_id, destination=str(target))
    except (TelegramAPIError, OSError):
        logger.exception("voice download failed")
        await message.answer("❌ Не удалось скачать голосовое.")
        return

    # 3. Извлекаем текущее состояние FSM (как строку) до того, как хендлер завершится.
    #    Сам FSMContext в фоне станет stale, поэтому передаём только значение.
    current_state = await state.get_state()

    # Ставим в очередь фоновой обработки (транскрипция + process_text)
    # Таймаут 10с — если очередь переполнена, не блокируем event loop
    try:
        await asyncio.wait_for(
            _voice_queue.put(
                VoiceJob(
                    voice_path=target,
                    message=message,
                    state_str=current_state,
                    userbot_manager=userbot_manager,
                    file_unique_id=media.file_unique_id,
                    mode=mode,
                    api_provider=api_provider,
                    openai_key=openai_key,
                    gemini_key=gemini_key,
                    mistral_key=mistral_key,
                    custom_stt_key=custom_stt_key,
                    custom_stt_model=custom_stt_model,
                    custom_stt_endpoint=custom_stt_endpoint,
                )
            ),
            timeout=settings.voice_queue_timeout,
        )
    except TimeoutError:
        logger.warning(
            "Voice queue full for user %d, dropping voice message",
            message.from_user.id,
        )
        _cleanup_voice_file(target)
        await message.answer("⏳ Слишком много голосовых в очереди. Попробуй позже.")
        return

    # 4. Мгновенный ответ — пользователь не ждёт транскрипцию
    await message.answer("🎙 Принял, расшифровываю…")


# ── Voice research callback ────────────────────────────────────────────


@router.callback_query(F.data.startswith("voice_research:"))
async def _cb_voice_research(
    callback: CallbackQuery,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    """Callback: кнопка «Исследовать это» после голосового вопроса."""
    uid = int(callback.data.split(":")[1])
    if callback.from_user.id != uid:
        await callback.answer("⛔ Не ваш запрос", show_alert=True)
        return
    await callback.answer()
    # Отправляем как новое текстовое сообщение, которое попадёт в free_text
    await callback.message.answer("🔍 Исследуй это подробно")


# ── C3: Photo cache ──────────────────────────────────────────────────────


class _CacheEntry(NamedTuple):
    description: str
    tokens: int
    expire_ts: float


class _PhotoCache:
    """LRU-кэш результатов анализа фото с TTL."""

    def __init__(self, max_size: int = 200, ttl_sec: int = 300):
        self._cache: dict[str, _CacheEntry] = {}
        self._max_size = max_size
        self._ttl = ttl_sec
        self._lock = asyncio.Lock()

    async def _key(self, data: bytes) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: hashlib.sha256(data, usedforsecurity=False).hexdigest()
        )

    async def get(self, data: bytes) -> _CacheEntry | None:
        async with self._lock:
            key = await self._key(data)
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() > entry.expire_ts:
                del self._cache[key]
                return None
            return entry

    async def set(self, data: bytes, description: str, tokens: int):
        async with self._lock:
            key = await self._key(data)
            # LRU-вытеснение если переполнен
            if len(self._cache) >= self._max_size:
                oldest_key = min(self._cache, key=lambda k: self._cache[k].expire_ts)
                del self._cache[oldest_key]
            self._cache[key] = _CacheEntry(description, tokens, time.time() + self._ttl)


# Глобальный экземпляр кэша фотографий (синглтон в памяти процесса)
_photo_cache = _PhotoCache()


# ── C3: Photo handler ───────────────────────────────────────────────────


@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext) -> None:
    """Обрабатывает фото — анализирует с кэшированием и сохраняет описание в память."""
    if not message.photo:
        return

    # Проверяем глобальный тоггл vision_enabled
    if not settings.vision_enabled:
        await message.answer(
            "🔍 Vision отключён. Включи в /settings → 🧠 LLM и модели."
        )
        return

    try:
        # Берём самое большое фото
        photo = message.photo[-1]

        # Скачиваем фото в память
        file = await message.bot.get_file(photo.file_id)
        if not file.file_path:
            await message.answer("⚠️ Не удалось получить файл изображения.")
            return
        bio = io.BytesIO()
        await message.bot.download_file(file.file_path, bio)
        image_data = bio.getvalue()

        # Проверяем кэш
        cache_entry = await _photo_cache.get(image_data)
        if cache_entry:
            await message.answer(
                sanitize_html(
                    f"🖼 {cache_entry.description}\n\n⚡ Из кэша (сэкономлено ~{cache_entry.tokens} токенов)"
                )
            )
            return

        # Показываем статус «печатает»
        await message.bot.send_chat_action(message.chat.id, "upload_photo")

        # Получаем API-ключ для vision
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            vision_key = await get_api_key(session, owner, "openai")

        if not vision_key:
            await message.answer(
                "⚠️ Нет ключа для Vision. Добавь OpenAI ключ в /settings."
            )
            return

        # Анализируем изображение
        provider = OpenAIVisionProvider(vision_key)
        try:
            result = await provider.chat_with_image(image_data, "image/jpeg")
        finally:
            await provider.close()

        # Сохраняем в кэш
        await _photo_cache.set(image_data, result.description, result.total_tokens)

        # Humanize vision output
        desc = result.description
        try:
            from src.core.humanizer.humanizer import humanize_response

            desc = humanize_response(desc)
        except Exception:
            logger.debug(
                "Photo humanizer failed", exc_info=True
            )  # best-effort, не ломаем если humanizer упал

        await message.answer(
            sanitize_html(f"🖼 {desc[:2000]}\n\n📊 Токенов: {result.total_tokens}")
        )

        # Сохраняем в память (best-effort)
        try:
            from src.core.memory.session_recorder import record_turn

            async with get_session() as rec_session:
                await record_turn(
                    rec_session,
                    message.from_user.id,
                    "user",
                    f"[Фото] {result.description[:500]}",
                )
                await record_turn(
                    rec_session,
                    message.from_user.id,
                    "assistant",
                    "(фото проанализировано)",
                )
        except SQLAlchemyError:
            pass

    except Exception as e:
        await message.answer(sanitize_html(f"❌ Ошибка анализа: {safe_str(e)}"))


@router.message(F.video_note | F.video)
async def handle_video(message: Message) -> None:
    """Видео — отправка первого кадра на анализ. Требует vision-модель и извлечение кадра (ffmpeg)."""
    # NOTE: Video analysis requires: ffmpeg frame extraction + vision model API call.
    # Currently not implemented. For now, suggest photo.
    await message.answer("🎬 Видео пока не анализируются. Отправь фото.")


# ── Edit feedback handler ────────────────────────────────────────────


@router.edited_message(OwnerOnly())
async def handle_edited_message(
    message: Message, state: FSMContext | None = None
) -> None:
    """Ловит правку ответа бота — сохраняет как фидбек."""
    if not message.text:
        return

    edited_text = message.text.strip()
    if not edited_text:
        return

    try:
        from src.core.memory.session_recorder import get_session_history

        async with get_session() as session:
            history = await get_session_history(session, message.from_user.id, limit=5)
            all_messages = []
            for s in history:
                all_messages.extend(s.get("messages", []))
            bot_messages = [m for m in all_messages if m.get("role") == "assistant"]

            if bot_messages:
                last_bot_msg = bot_messages[0]
                original = last_bot_msg.get("content", "")

                if original and edited_text != original:
                    from src.core.humanizer.humanizer import store_feedback

                    pattern = _extract_correction_pattern(original, edited_text)
                    if pattern:
                        store_feedback(message.from_user.id, pattern[0], pattern[1])
                        logger.debug(
                            "Feedback stored: %s → %s",
                            pattern[0][:50],
                            pattern[1][:50],
                        )
    except Exception:
        logger.debug(
            "handle_edited_message best-effort failed for user %d",
            message.from_user.id if message.from_user else 0,
            exc_info=True,
        )  # best-effort — не ломаем основной поток при ошибках фидбека
