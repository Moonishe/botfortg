"""Factory for API key input handlers.

Создаёт handler для ввода API-ключа с валидацией и сохранением.
Заменяет 9 copy-paste обработчиков в settings.py.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)


def make_key_handler(
    state: State,
    provider_name: str,
    provider_class: Optional[type] = None,
    category: str = "llm",
    *,
    validation_error_msg: Optional[str] = None,
    provider_label: Optional[str] = None,
) -> Any:
    """Создаёт асинхронный handler для ввода API-ключа провайдера.

    Логика:
      1. /cancel /back /menu — очистка state и возврат в меню.
      2. Пустой ввод — повтор.
      3. Парсинг нескольких ключей через запятую.
      4. Валидация первого ключа через provider_class(args[0]).validate_key().
      5. Сохранение:
         - category='llm'  → upsert_api_key (все ключи одной строкой, legacy).
         - category='stt'  → add_key_slot для каждого ключа с category='stt'.
      6. Показ кол-ва сохранённых ключей и общего числа в БД.
      7. Очистка state.

    Args:
        state:        FSM-состояние, на которое зарегистрирован handler
                      (не используется внутри, но сохраняется для единообразия).
        provider_name: Имя провайдера для БД и callback_data
                      (напр. "openai", "gemini", "deepgram").
        provider_class: Класс провайдера с методом .validate_key().
        category:      "llm" (upsert_api_key) или "stt" (add_key_slot).
        validation_error_msg: Кастомное сообщение при ошибке валидации.
        provider_label: Отображаемое имя (по умолчанию provider_name.capitalize()).
    """
    if provider_label is None:
        provider_label = provider_name.capitalize()

    async def handler(message: Message, fsm_context: FSMContext) -> None:
        # Ленивые импорты — избегаем циклических зависимостей,
        # т.к. settings.py импортирует этот модуль.
        from src.bot.handlers.settings import _count_slots_for_provider, _render_menu
        from src.db.repo import add_key_slot, get_or_create_user, upsert_api_key
        from src.db.session import get_session

        raw = (message.text or "").strip()
        if raw in ("/cancel", "/back", "/menu"):
            await fsm_context.clear()
            text, kb = await _render_menu(message.from_user.id)
            await message.answer("❌ Ввод ключа отменён.")
            await message.answer(text, reply_markup=kb)
            return

        if not raw:
            await message.answer("Пустой ключ. Повтори или /cancel.")
            return

        parts = [k.strip() for k in raw.split(",") if k.strip()]
        if not parts:
            await message.answer("Нет ни одного непустого ключа. Повтори или /cancel.")
            return

        try:
            await message.delete()
        except Exception:
            logger.exception("failed to delete message with %s key", provider_name)

        # Валидация всех ключей + sanity length check
        if provider_class is not None:
            MAX_KEY_LENGTH = 256
            for part in parts:
                if len(part) > MAX_KEY_LENGTH:
                    await message.answer(
                        f"❌ Ключ слишком длинный (>{MAX_KEY_LENGTH} символов). /cancel"
                    )
                    return

            for i, part in enumerate(parts):
                try:
                    valid = await provider_class(part).validate_key()
                except Exception:
                    valid = False
                if not valid:
                    msg = (
                        f"❌ Ключ #{i + 1} не работает. Повтори или /cancel."
                        if not validation_error_msg
                        else validation_error_msg
                    )
                    await message.answer(msg)
                    return

        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)

            if category == "stt":
                for single_key in parts:
                    await add_key_slot(
                        session,
                        owner,
                        provider_name,
                        single_key,
                        purpose="main",
                        label=f"{provider_name}/main",
                        priority=0,
                        category="stt",
                    )
            else:
                await upsert_api_key(session, owner, provider_name, ",".join(parts))

            total = await _count_slots_for_provider(session, owner, provider_name)

        # Инвалидируем кэш провайдера
        from src.core.context_cache import invalidate

        await invalidate(f"provider:{message.from_user.id}:main:default")
        await invalidate(f"provider:{message.from_user.id}:main:search")

        await fsm_context.clear()
        count = len(parts)
        kb = InlineKeyboardBuilder()
        kb.button(text="➕ Ещё ключ", callback_data=f"set:input:{provider_name}_key")
        kb.button(text="✅ Назад", callback_data="set:done:key")
        kb.adjust(2)
        await message.answer(
            f"✅ Сохранено {provider_label} ключей: {count}.\n"
            f"🔑 В базе {provider_label} ключей: {total}.\n\n"
            "Добавить ещё?",
            reply_markup=kb.as_markup(),
        )

    return handler
