"""Photo/video media handlers — extracted from free_text_legacy.py.

All symbols re-exported via free_text_legacy.py to preserve import paths.
Handlers are registered on free_text_legacy.router after import.
"""

import asyncio
import hashlib
import io
import logging
import time
from typing import NamedTuple

from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from src.config import settings
from src.core.infra.key_guard import safe_str
from src.core.infra.text_sanitizer import sanitize_html
from src.db.repo import get_api_key, get_or_create_user
from src.db.session import get_session
from sqlalchemy.exc import SQLAlchemyError
from src.llm.vision_provider import OpenAIVisionProvider

logger = logging.getLogger(__name__)


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


# ── Handler functions (decorators applied in free_text_legacy.py) ─────────


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
            from src.core.humanizer.humanizer import humanize_response_async

            desc = await humanize_response_async(desc)
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
            # Recording the photo-analysis turn is non-critical for the user
            # response, but silently swallowing the error would hide data loss
            # from the operator. Log it (mirrors _process_text's handling).
            logger.warning(
                "Failed to record photo-analysis turn for user %s",
                message.from_user.id,
                exc_info=True,
            )

    except Exception as e:
        await message.answer(sanitize_html(f"❌ Ошибка анализа: {safe_str(e)}"))


async def handle_video(message: Message) -> None:
    """Видео — отправка первого кадра на анализ. Требует vision-модель и извлечение кадра (ffmpeg)."""
    # NOTE: Video analysis requires: ffmpeg frame extraction + vision model API call.
    # Currently not implemented. For now, suggest photo.
    await message.answer("🎬 Видео пока не анализируются. Отправь фото.")


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

                    # Lazy import to avoid circular dependency
                    from src.bot.handlers.free_text_legacy import (
                        _extract_correction_pattern,
                    )

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
