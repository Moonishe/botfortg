"""Voice transcription queue and handlers — extracted from free_text_legacy.py.

All symbols re-exported via free_text_legacy.py to preserve import paths.
Handlers are registered on free_text_legacy.router after import.
"""

import asyncio
import logging
from pathlib import Path
from typing import NamedTuple

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.config import settings
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.telemetry import start_span
from src.core.infra.transcription import transcription_service
from src.crypto import decrypt
from src.db.models import LlmKeySlot
from src.db.repo import get_api_key, get_or_create_user
from src.db.session import get_session
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from src.userbot.manager import UserbotManager
from httpx import RequestError, HTTPStatusError
from aiogram.exceptions import TelegramAPIError

from src.core.security.prompt_injection_scanner import scan_content

logger = logging.getLogger(__name__)


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

VOICE_WORKER_COUNT = 2


def _cleanup_voice_file(voice_path: Path) -> None:
    """Безопасно удалить временный файл голосового сообщения."""
    try:
        voice_path.unlink(missing_ok=True)
    except (OSError, PermissionError):
        logger.debug("cleanup voice file failed: %s", voice_path, exc_info=True)


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

    Consecutive failure counter: if the inner job loop fails N times in a
    row without successfully processing a single job, the worker restarts
    itself to recover from a potentially poisoned state (e.g. all providers
    down, corrupted queue entries).
    """
    _MAX_CONSECUTIVE_FAILURES = 10

    while True:
        # Track consecutive job failures to detect a poisoned worker.
        consecutive_failures = 0
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
                            # Lazy import _is_question to avoid circular dependency
                            from src.bot.handlers.free_text_legacy import _is_question

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
                                    logger.debug(
                                        "Non-critical error", exc_info=True
                                    )  # реакция не критична
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
                                # Lazy import to avoid circular dependency
                                from src.bot.handlers.free_text_legacy import (
                                    _process_text,
                                )

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

                    # Job succeeded — reset the consecutive failure counter
                    consecutive_failures = 0

                except asyncio.CancelledError:
                    raise  # propagate to outer handler for clean shutdown
                except Exception:
                    logger.exception("Voice worker error")
                    consecutive_failures += 1
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
                    # If too many consecutive job failures, force a worker restart
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        logger.critical(
                            "Voice worker: %d consecutive job failures — restarting",
                            consecutive_failures,
                        )
                        break  # exit inner loop to trigger outer restart
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


# ── Handler functions (decorators applied in free_text_legacy.py) ─────────


async def free_voice(
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    """Обработчик голосовых/аудио сообщений. Регистрируется в free_text_legacy.py."""
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
