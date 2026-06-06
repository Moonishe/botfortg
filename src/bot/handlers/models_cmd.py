"""Model Discovery — /models command.

Показывает доступные модели для указанного ключа.

Использование:
  /models          — показать модели для всех ключей (выбор слота)
  /models 3        — показать модели для ключа в слоте #3
  /models openai   — показать модели для всех ключей провайдера (выбор слота)
"""

import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.db.models import LlmKeySlot
from src.db.repo import get_or_create_user, list_key_slots
from src.db.session import get_session

logger = logging.getLogger(__name__)
router = Router(name="models_cmd")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())

# ─── In-memory cache ─────────────────────────────────────────────────
# slot_id → (timestamp, [model_ids])
_MODEL_CACHE: dict[int, tuple[float, list[str]]] = {}
_CACHE_TTL: float = 3600.0  # 1 час

MODELS_PER_PAGE: int = 10
# Безопасное ограничение длины callback_data (Telegram: ≤ 64 байт).
# Префикс "models:save:{slot_id}:" занимает ~20 символов,
# остаётся ~40 символов на имя модели.
_MODEL_CB_MAX_LEN: int = 40


# ─── Кэш моделей ──────────────────────────────────────────────────────


def _cache_get(slot_id: int) -> list[str] | None:
    """Получить модели из кэша, если они свежие."""
    entry = _MODEL_CACHE.get(slot_id)
    if entry is None:
        return None
    ts, models = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _MODEL_CACHE[slot_id]
        return None
    return models


def _cache_set(slot_id: int, models: list[str]) -> None:
    """Сохранить модели в кэш."""
    _MODEL_CACHE[slot_id] = (time.monotonic(), models)


def _cache_invalidate(slot_id: int) -> None:
    """Сбросить кэш для слота."""
    _MODEL_CACHE.pop(slot_id, None)


# ─── Получение моделей ─────────────────────────────────────────────────


async def _fetch_models(slot: LlmKeySlot) -> tuple[list[str], str | None]:
    """Получить доступные модели для слота ключа через API провайдера.

    Returns:
        (models, error_message). Если error_message is None — успех.
    """
    try:
        # Lazy imports — соответствуют паттерну проекта
        from src.llm.provider_manager import _provider_class_for
        from src.crypto import decrypt

        provider_cls = _provider_class_for(slot.provider)
        if provider_cls is None:
            return [], f"Неизвестный провайдер: {slot.provider}"

        api_key = decrypt(slot.key_enc)
        endpoint = slot.endpoint  # может быть None → default провайдера

        # Создаём провайдера: с endpoint или без
        if endpoint:
            provider = provider_cls(api_key=api_key, base_url=endpoint)
        else:
            provider = provider_cls(api_key=api_key)

        try:
            models = await asyncio.wait_for(provider.list_models(), timeout=15.0)
            return models, None
        finally:
            await provider.close()

    except NotImplementedError:
        return [], f"{slot.provider} не поддерживает список моделей"
    except asyncio.TimeoutError:
        return [], "Таймаут запроса (проверь endpoint)"
    except Exception as e:
        msg = str(e)
        short_msg = msg[:100]
        # Классификация ошибок для читаемых сообщений
        if "401" in msg or "Unauthorized" in msg or "AuthenticationError" in msg:
            return [], "Неверный API ключ (401 Unauthorized)"
        if (
            "ConnectionError" in msg
            or "ConnectError" in msg
            or "APIConnectionError" in msg
        ):
            return [], "Ошибка сети: не удалось подключиться к endpoint"
        logger.warning(
            "Model fetch failed for slot %d (%s): %s",
            slot.id,
            slot.provider,
            short_msg,
        )
        return [], f"Ошибка: {short_msg}"


# ─── Клавиатура моделей с пагинацией ───────────────────────────────────


def _build_models_message(
    slot: LlmKeySlot, error: str | None, page: int, enabled_count: int = 0
) -> str:
    """Форматирует текст сообщения со списком моделей."""
    if error:
        return f"❌ <b>Слот #{slot.id}</b> ({slot.provider}/{slot.purpose})\n{error}"
    cached = _cache_get(slot.id)
    if not cached:
        return (
            f"⚠️ <b>Слот #{slot.id}</b> ({slot.provider}/{slot.purpose})\n"
            f"Модели не найдены"
        )
    total = len(cached)
    total_pages = max(1, (total + MODELS_PER_PAGE - 1) // MODELS_PER_PAGE)
    lines = [
        f"📋 <b>Модели для слота #{slot.id}</b> ({slot.provider}/{slot.purpose})",
    ]
    if slot.endpoint:
        lines.append(f"🔗 {slot.endpoint}")
    lines.append(
        f"Найдено: {total} | Включено: {enabled_count} | стр. {page + 1}/{total_pages}"
    )
    return "\n".join(lines)


async def _build_models_keyboard(
    slot_id: int,
    page: int = 0,
) -> InlineKeyboardMarkup:
    """Строит инлайн-клавиатуру с моделями, навигацией и toggle-состоянием."""
    cached = _cache_get(slot_id)
    if not cached:
        # Нет данных — только кнопка закрытия
        kb = InlineKeyboardBuilder()
        kb.button(text="❌ Закрыть", callback_data="models:close")
        return kb.as_markup()

    # Загружаем enabled-модели из БД
    enabled_set: set[str] = set()
    try:
        async with get_session() as session:
            from src.db.repos.key_repo import get_enabled_models as _gem

            enabled_set = set(await _gem(session, slot_id))
    except Exception:
        pass

    total = len(cached)
    total_pages = max(1, (total + MODELS_PER_PAGE - 1) // MODELS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * MODELS_PER_PAGE
    page_models = cached[start : start + MODELS_PER_PAGE]

    kb = InlineKeyboardBuilder()

    for i, m in enumerate(page_models):
        global_idx = start + i
        checked = "✅" if m in enabled_set else "⬜"
        display = m[:40] + "…" if len(m) > 40 else m
        kb.button(
            text=f"{checked} {display}",
            callback_data=f"models:toggle:{slot_id}:{global_idx}",
        )

    kb.adjust(1)

    # Строка навигации
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="◀ Назад",
                callback_data=f"models:page:{slot_id}:{page - 1}",
            )
        )
    if total > MODELS_PER_PAGE:
        nav_row.append(
            InlineKeyboardButton(
                text=f"стр. {page + 1}/{total_pages}",
                callback_data="models:noop",
            )
        )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="Вперёд ▶",
                callback_data=f"models:page:{slot_id}:{page + 1}",
            )
        )

    if nav_row:
        kb.row(*nav_row)

    # Строка действий
    kb.row(
        InlineKeyboardButton(
            text="✅ Выбрать все",
            callback_data=f"models:enable_all:{slot_id}",
        ),
        InlineKeyboardButton(
            text="❌ Снять все",
            callback_data=f"models:disable_all:{slot_id}",
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=f"models:refresh:{slot_id}",
        ),
        InlineKeyboardButton(
            text="❌ Закрыть",
            callback_data="models:close",
        ),
    )

    return kb.as_markup()


# ─── Вспомогательная: слот-пикер для провайдера/всех слотов ────────────


def _build_slot_picker_keyboard(
    slots: list[LlmKeySlot],
) -> InlineKeyboardMarkup:
    """Клавиатура для выбора слота из списка."""
    kb = InlineKeyboardBuilder()
    for s in slots:
        label = f"#{s.id} {s.provider}/{s.purpose}"
        if s.model:
            label += f" [{s.model}]"
        kb.button(
            text=label,
            callback_data=f"models:slot:{s.id}",
        )
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(
            text="❌ Закрыть",
            callback_data="models:close",
        )
    )
    return kb.as_markup()


# ─── Основная команда /models ──────────────────────────────────────────


@router.message(Command("models"))
async def cmd_list_models(message: Message, command: CommandObject) -> None:
    """Показывает доступные модели для указанного ключа.

    Использование:
      /models          — показать модели для всех ключей
      /models 3        — показать модели для ключа в слоте #3
      /models openai   — показать модели для всех ключей провайдера
    """
    args = (command.args or "").strip().split()
    slot_id: int | None = None
    provider_filter: str | None = None

    if args:
        arg = args[0]
        # Пробуем распарсить как номер слота
        try:
            slot_id = int(arg)
        except ValueError:
            # Не число — считаем именем провайдера
            provider_filter = arg.lower()

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)

        if slot_id is not None:
            # Конкретный слот — загружаем и показываем сразу
            slot = await session.get(LlmKeySlot, slot_id)
            if slot is None or slot.user_id != owner.id:
                await message.answer(
                    f"❌ Слот #{slot_id} не найден или не принадлежит тебе."
                )
                return
            await _fetch_and_show_models(message, slot)
            return

        # Фильтр по провайдеру или все слоты
        slots = await list_key_slots(session, owner, provider=provider_filter)
        if not slots:
            what = provider_filter or "ни одного"
            await message.answer(
                f"❌ Нет ключевых слотов для «{what}». Добавь ключ через /keys add."
            )
            return

        if len(slots) == 1:
            # Один слот — загружаем сразу
            await _fetch_and_show_models(message, slots[0])
            return

        # Несколько слотов — показываем пикер
        provider_text = f" для «{provider_filter}»" if provider_filter else ""
        await message.answer(
            f"📋 <b>Слоты{provider_text}:</b>\nНайдено {len(slots)}. Выбери слот:",
            reply_markup=_build_slot_picker_keyboard(slots),
        )


async def _fetch_and_show_models(
    target: Message | CallbackQuery,
    slot: LlmKeySlot,
    *,
    edit: bool = False,
) -> None:
    """Загружает модели для слота и показывает результат.

    Для Message с edit=False отправляет новое сообщение.
    Для CallbackQuery всегда редактирует существующее.
    """
    # Показываем статус загрузки
    loading_text = (
        f"⏳ <b>Загружаю модели для слота #{slot.id}</b> "
        f"({slot.provider}/{slot.purpose})…"
    )

    if isinstance(target, CallbackQuery):
        msg = target.message
        if msg is None:
            return
        await msg.edit_text(loading_text)
        await target.answer()
    elif edit and isinstance(target, Message):
        msg = target
        await msg.edit_text(loading_text)
    else:
        msg = await target.answer(loading_text)

    # Проверяем кэш
    cached = _cache_get(slot.id)
    if cached is not None:
        logger.debug(
            "Using cached models for slot %d (%d models)", slot.id, len(cached)
        )
    else:
        models, error = await _fetch_models(slot)
        if error:
            await msg.edit_text(
                _build_models_message(slot, error, 0),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔄 Попробовать снова",
                                callback_data=f"models:refresh:{slot.id}",
                            ),
                            InlineKeyboardButton(
                                text="❌ Закрыть",
                                callback_data="models:close",
                            ),
                        ]
                    ]
                ),
            )
            return
        _cache_set(slot.id, models)

    await msg.edit_text(
        _build_models_message(slot, None, 0),
        reply_markup=await _build_models_keyboard(slot.id, page=0),
    )


# ─── Inline-колбэки ────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("models:slot:"))
async def cb_models_slot_pick(callback: CallbackQuery) -> None:
    """Пользователь выбрал слот из пикера — загружаем модели."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверный номер слота.", show_alert=True)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot is None or slot.user_id != owner.id:
            await callback.answer("Слот не найден или не твой.", show_alert=True)
            return

    await _fetch_and_show_models(callback, slot)


@router.callback_query(F.data == "models:noop")
async def cb_models_noop(callback: CallbackQuery) -> None:
    """No-op для индикатора страницы."""
    await callback.answer()


@router.callback_query(F.data == "models:close")
async def cb_models_close(callback: CallbackQuery) -> None:
    """Закрыть клавиатуру моделей."""
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            # Сообщение уже удалено или нет прав
            await callback.answer()
            return
    await callback.answer()


@router.callback_query(F.data.startswith("models:page:"))
async def cb_models_page(callback: CallbackQuery) -> None:
    """Пагинация: перейти на другую страницу."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
        page = int(parts[3])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return

    # Проверяем принадлежность слота (быстрая проверка)
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot is None or slot.user_id != owner.id:
            await callback.answer("Слот не найден или не твой.", show_alert=True)
            return

    cached = _cache_get(slot_id)
    if cached is None:
        # Кэш протух — перезагружаем
        await _fetch_and_show_models(callback, slot)
        return

    if callback.message is None:
        return

    await callback.message.edit_text(
        _build_models_message(slot, None, page),
        reply_markup=await _build_models_keyboard(slot_id, page=page),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("models:refresh:"))
async def cb_models_refresh(callback: CallbackQuery) -> None:
    """Перезагрузить список моделей для слота."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверный номер слота.", show_alert=True)
        return

    # Инвалидируем кэш
    _cache_invalidate(slot_id)

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot is None or slot.user_id != owner.id:
            await callback.answer("Слот не найден или не твой.", show_alert=True)
            return

    await _fetch_and_show_models(callback, slot)


@router.callback_query(F.data.startswith("models:save:"))
async def cb_models_save(callback: CallbackQuery) -> None:
    """Сохранить выбранную модель в слот ключа."""
    parts = callback.data.split(":")
    # Формат: models:save:{slot_id}:{model_index}
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
        model_idx = int(parts[3])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return

    cached = _cache_get(slot_id)
    if cached is None or model_idx < 0 or model_idx >= len(cached):
        await callback.answer("Данные устарели. Нажми «🔄 Обновить».", show_alert=True)
        return

    model_name = cached[model_idx]

    # Сохраняем модель в БД
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot is None or slot.user_id != owner.id:
            await callback.answer("Слот не найден или не твой.", show_alert=True)
            return

        slot.model = model_name
        await session.commit()

    logger.info(
        "Model saved: slot=%d provider=%s model=%s",
        slot_id,
        slot.provider,
        model_name,
    )

    # Инвалидируем кэш настроек
    try:
        from src.bot.handlers.free_text_common import invalidate_settings_cache

        await invalidate_settings_cache(callback.from_user.id)
    except Exception:
        pass

    await callback.answer(
        f"✅ Модель «{model_name[:50]}» сохранена в слот #{slot_id}.",
        show_alert=True,
    )


# ─── Toggle модели: включить/выключить в LlmKeySlotModel ──────────────


@router.callback_query(F.data.startswith("models:toggle:"))
async def cb_models_toggle(callback: CallbackQuery) -> None:
    """Включить/выключить модель в мульти-модельном выборе слота."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
        model_idx = int(parts[3])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return

    cached = _cache_get(slot_id)
    if cached is None or model_idx < 0 or model_idx >= len(cached):
        await callback.answer("Данные устарели. Нажми «🔄 Обновить».", show_alert=True)
        return

    model_name = cached[model_idx]

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot is None or slot.user_id != owner.id:
            await callback.answer("Слот не найден или не твой.", show_alert=True)
            return

        from src.db.repos.key_repo import toggle_slot_model, get_enabled_models

        # Проверяем текущее состояние и переключаем
        enabled_models = await get_enabled_models(session, slot_id)
        currently_enabled = model_name in enabled_models
        await toggle_slot_model(session, slot_id, model_name, not currently_enabled)

        # Обратная совместимость: обновляем slot.model
        updated = await get_enabled_models(session, slot_id)
        slot.model = updated[0] if updated else None
        await session.commit()

    # Инвалидируем кэш настроек
    try:
        from src.bot.handlers.free_text_common import invalidate_settings_cache

        await invalidate_settings_cache(callback.from_user.id)
    except Exception:
        pass

    # Перерисовываем клавиатуру
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=await _build_models_keyboard(
                slot_id, page=_get_current_page(callback)
            )
        )
    await callback.answer(
        f"{'✅ Включена' if not currently_enabled else '❌ Выключена'}: {model_name[:50]}"
    )


@router.callback_query(F.data.startswith("models:enable_all:"))
async def cb_models_enable_all(callback: CallbackQuery) -> None:
    """Включить все модели для слота."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return

    cached = _cache_get(slot_id)
    if not cached:
        await callback.answer("Нет моделей для выбора.", show_alert=True)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot is None or slot.user_id != owner.id:
            await callback.answer("Слот не найден или не твой.", show_alert=True)
            return

        from src.db.repos.key_repo import set_slot_models

        await set_slot_models(session, slot_id, cached)
        slot.model = cached[0] if cached else None
        await session.commit()

    # Инвалидируем кэш
    try:
        from src.bot.handlers.free_text_common import invalidate_settings_cache

        await invalidate_settings_cache(callback.from_user.id)
    except Exception:
        pass

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=await _build_models_keyboard(slot_id, page=0)
        )
    await callback.answer(f"✅ Включены все {len(cached)} моделей")


@router.callback_query(F.data.startswith("models:disable_all:"))
async def cb_models_disable_all(callback: CallbackQuery) -> None:
    """Выключить все модели для слота."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot is None or slot.user_id != owner.id:
            await callback.answer("Слот не найден или не твой.", show_alert=True)
            return

        from src.db.repos.key_repo import set_slot_models

        await set_slot_models(session, slot_id, [])
        slot.model = None
        await session.commit()

    try:
        from src.bot.handlers.free_text_common import invalidate_settings_cache

        await invalidate_settings_cache(callback.from_user.id)
    except Exception:
        pass

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=await _build_models_keyboard(slot_id, page=0)
        )
    await callback.answer("❌ Все модели выключены")


def _get_current_page(callback: CallbackQuery) -> int:
    """Извлекает текущую страницу из текста сообщения (если возможно)."""
    # Простая эвристика: из текста вида "стр. X/Y" парсим X
    if callback.message and callback.message.text:
        import re

        m = re.search(r"стр\.\s*(\d+)/", callback.message.text)
        if m:
            return int(m.group(1)) - 1
    return 0


# ─── Обработчик из keys_cmd: показать модели после добавления ключа ────


@router.callback_query(F.data.startswith("keys:disc:"))
async def cb_keys_show_models(callback: CallbackQuery) -> None:
    """Показать модели для только что добавленного ключа (вызов из keys_cmd)."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверный номер слота.", show_alert=True)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot is None or slot.user_id != owner.id:
            await callback.answer("Слот не найден или не твой.", show_alert=True)
            return

    await _fetch_and_show_models(callback, slot)
