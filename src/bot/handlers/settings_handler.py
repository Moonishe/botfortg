"""Main command and callback handlers for settings.

SRP: aiogram handlers only — no rendering, no business logic, no validation.
"""

import io
import json
import logging

from aiogram import F
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Message,
)

from src.bot.callbacks import SettingsCB
from src.bot.handlers.settings_router import router
from src.bot.handlers.settings_menu import _render_menu
from src.bot.handlers.settings_sections import _render_section
from src.bot.handlers.settings_service import (
    _collect_export_config,
    _apply_import_config,
)
from src.bot.handlers.settings_validator import (
    BOOL_KEYS,
    CHOICE_KEYS,
    NUMERIC_KEYS,
    PERSONA_KEYS,
    SEARCHABLE_SETTINGS,
    section_for_key,
)
from src.bot.states import SettingsStates
from src.core.infra.key_guard import safe_str
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.timeutil import is_valid_tz
from src.core.intelligence.adaptive_persona import reset_persona_to_snapshot
from src.db.repo import (
    get_or_create_user,
    get_persona,
    list_key_slots,
)
from sqlalchemy.exc import SQLAlchemyError
from src.db.session import get_session
from src.userbot import get_active_telethon_client, get_userbot_manager
from src.userbot.dialogs import sync_dialogs

logger = logging.getLogger(__name__)


# =====================================================================
#  HELPERS
# =====================================================================


async def _safe_edit(message, text: str, kb) -> None:
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in safe_str(e).lower():
            raise


async def _show_main_menu(callback: CallbackQuery) -> None:
    text, kb = await _render_menu(callback.from_user.id)
    await _safe_edit(callback.message, text, kb)


async def _refresh_section(callback: CallbackQuery, section: str) -> None:
    if section == "menu":
        text, kb = await _render_menu(callback.from_user.id)
    else:
        text, kb = await _render_section(callback.from_user.id, section)
    await _safe_edit(callback.message, text, kb)


# =====================================================================
#  COMMAND: /settings
# =====================================================================


@router.message(Command("settings"))
async def cmd_settings(message: Message, command: CommandObject) -> None:
    """/settings поиск <keyword> — быстрый поиск по настройкам."""
    args = (command.args or "").strip()
    if args.startswith("поиск "):
        query = args[6:].strip().lower()
        if not query:
            await message.answer("Использование: /settings поиск <ключевое слово>")
            return

        results = []
        for key, desc in SEARCHABLE_SETTINGS.items():
            if query in key.lower() or query in desc.lower():
                results.append(f"• <b>{key}</b> — {desc}")

        if results:
            await message.answer(
                f"🔍 Результаты по «{sanitize_html(query)}»:\n\n"
                + "\n".join(results[:15])
            )
        else:
            await message.answer(f"❌ Ничего не найдено по «{sanitize_html(query)}».")
        return

    text, kb = await _render_menu(message.from_user.id)
    await message.answer(text, reply_markup=kb)


# =====================================================================
#  MAIN MENU CALLBACKS
# =====================================================================


@router.callback_query(F.data == SettingsCB.MENU)
async def cb_menu(callback: CallbackQuery) -> None:
    text, kb = await _render_menu(callback.from_user.id)
    await _safe_edit(callback.message, text, kb)
    await callback.answer()


@router.callback_query(F.data.startswith(SettingsCB.back("")))
async def cb_settings_back(callback: CallbackQuery) -> None:
    parts = callback.data.split(":", 2)
    parent = parts[2] if len(parts) > 2 else "menu"
    if parent == "menu":
        await _show_main_menu(callback)
    else:
        text, kb = await _render_section(callback.from_user.id, parent)
        await _safe_edit(callback.message, text, kb)
    await callback.answer()


@router.callback_query(F.data == SettingsCB.CLOSE)
async def cb_close(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.delete()
    await callback.answer()


# =====================================================================
#  EXPORT / IMPORT CONFIG
# =====================================================================


@router.callback_query(F.data == SettingsCB.EXPORT_CONFIG)
async def cb_export_config(callback: CallbackQuery) -> None:
    """Экспорт всех настроек бота в JSON-файл."""
    await callback.answer("📤 Готовлю экспорт...")

    config = await _collect_export_config(callback.from_user.id)

    json_str = json.dumps(config, ensure_ascii=False, indent=2)
    if callback.message is None:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    await callback.message.answer_document(
        BufferedInputFile(
            json_str.encode("utf-8"), filename="telegram_helper_config.json"
        ),
        caption="📤 Твой конфиг бота. Сохрани этот файл.\n\n"
        "⚠️ Файл содержит зашифрованные API-ключи — не передавай его третьим лицам.\n"
        "Для восстановления используй 📥 Импорт конфига в настройках.",
    )


@router.callback_query(F.data == SettingsCB.IMPORT_CONFIG)
async def cb_import_config(callback: CallbackQuery, state: FSMContext) -> None:
    """Запуск импорта — просим прислать файл."""
    await state.set_state(SettingsStates.waiting_config_import)
    await callback.message.answer(
        "📥 Пришли JSON-файл конфига (telegram_helper_config.json).\n/cancel — отмена."
    )
    await callback.answer()


@router.message(SettingsStates.waiting_config_import, F.document)
async def step_import_config(message: Message, state: FSMContext) -> None:
    """Обрабатываем загруженный конфиг-файл."""
    if (
        not message.document
        or not message.document.file_name
        or not message.document.file_name.endswith(".json")
    ):
        await message.answer("❌ Нужен .json файл. Попробуй ещё раз или /cancel.")
        return

    await message.answer("📥 Импортирую конфиг...")

    try:
        file = await message.bot.get_file(message.document.file_id)
        bio = io.BytesIO()
        await message.bot.download_file(file.file_path, bio)
        config = json.loads(bio.getvalue().decode("utf-8"))

        if "version" not in config:
            await message.answer("❌ Невалидный файл конфига (нет version).")
            await state.clear()
            return

        result = await _apply_import_config(message.from_user.id, config)

        # Invalidate settings cache after mutation
        from src.bot.handlers.free_text_common import invalidate_settings_cache

        await invalidate_settings_cache(message.from_user.id)

        await message.answer(
            f"✅ Конфиг импортирован!\n"
            f"⚙️ Настроек: {result['settings_count']}\n"
            f"🔑 Ключей: {result['keys_count']}\n"
            f"📋 Переопределений моделей: {result['overrides_count']}\n\n"
            f"Проверь настройки в /settings."
        )

    except json.JSONDecodeError:
        await message.answer("❌ Файл повреждён — невалидный JSON.")
    except (TelegramAPIError, SQLAlchemyError) as e:
        logger.warning("import_config failed: %s", e)
        await message.answer("❌ Ошибка импорта конфигурации. Проверь файл")
    finally:
        await state.clear()


@router.message(SettingsStates.waiting_config_import)
async def step_import_config_text(message: Message) -> None:
    """Text input when file upload expected."""
    await message.answer("📥 Пришли JSON-файл (документом). /cancel — отмена.")


# =====================================================================
#  ANALYZE
# =====================================================================


@router.callback_query(F.data == SettingsCB.ANALYZE)
async def cb_settings_analyze(callback: CallbackQuery) -> None:
    await callback.answer("Запускаю анализ...")
    if callback.message is None:
        return
    await callback.message.answer(
        "🧠 <b>Полный анализ переписок</b>\n\n"
        "Используй команду /analyze для полного анализа.\n\n"
        "<b>Примеры:</b>\n"
        "<code>/analyze</code> — все контакты из выбранных папок\n"
        "<code>/analyze Работа</code> — только папка «Работа»\n"
        "<code>/analyze Работа Семья</code> — папки «Работа» и «Семья»"
    )


# =====================================================================
#  TOGGLE / CHOOSE
# =====================================================================


@router.callback_query(F.data.startswith(SettingsCB.toggle("")))
async def cb_toggle(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in BOOL_KEYS:
        await callback.answer("Неизвестный переключатель", show_alert=True)
        return

    if key == "adaptive_mode_enabled":
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            p = await get_persona(session, owner)
            p.adaptive_mode_enabled = not p.adaptive_mode_enabled
            new_value = p.adaptive_mode_enabled  # захватываем до выхода из сессии
        # Примечание: commit делает контекстный менеджер get_session()
        from src.core.context_cache import invalidate

        await invalidate(f"persona:{callback.from_user.id}")
        await callback.answer(
            f"Адаптивный режим {'✅ ВКЛ' if new_value else '❌ ВЫКЛ'}"
        )
        await _refresh_section(callback, "personality")
        return

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            current = getattr(owner.settings, key)
            setattr(owner.settings, key, not current)
    except AttributeError:
        await callback.answer("Ошибка: настройка не найдена", show_alert=True)
        return
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(callback.from_user.id)
    await callback.answer("Готово")
    await _refresh_section(callback, section_for_key(key))


@router.callback_query(F.data.startswith("set:choose:"))
async def cb_choose(callback: CallbackQuery) -> None:
    parts = callback.data.split(":", 3)
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    try:
        _, _, key, value = parts
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    PERSONALITY_FIELDS = {
        "base_tone",
        "warmth",
        "enthusiasm",
        "headings_lists",
        "emoji_level",
    }
    if key in PERSONALITY_FIELDS:
        valid_values = CHOICE_KEYS.get(key, set())
        if value not in valid_values:
            await callback.answer("Невалидное значение", show_alert=True)
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            p = await get_persona(session, owner)
            setattr(p, key, value)
            await session.flush()
        from src.core.context_cache import invalidate

        await invalidate(f"persona:{callback.from_user.id}")
        await callback.answer("Готово")
        await _refresh_section(callback, "personality")
        return

    if key in CHOICE_KEYS:
        if value not in CHOICE_KEYS[key]:
            await callback.answer("Невалидное значение", show_alert=True)
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            if key in PERSONA_KEYS:
                persona = await get_persona(session, owner)
                setattr(persona, key, value)
            else:
                setattr(owner.settings, key, value)
    elif key in NUMERIC_KEYS:
        try:
            ivalue = max(0, int(value))
        except ValueError:
            await callback.answer("Невалидное число", show_alert=True)
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            setattr(owner.settings, key, ivalue)
    else:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(callback.from_user.id)
    await callback.answer("Готово")
    await _refresh_section(callback, section_for_key(key))


# =====================================================================
#  OPEN SECTION
# =====================================================================


@router.callback_query(F.data.startswith(SettingsCB.section("")))
async def cb_open_section(callback: CallbackQuery) -> None:
    section = callback.data.split(":", 2)[2]
    text, kb = await _render_section(callback.from_user.id, section)
    await _safe_edit(callback.message, text, kb)
    await callback.answer()


# =====================================================================
#  FOLDER TOGGLE / REFRESH
# =====================================================================


@router.callback_query(F.data.startswith(SettingsCB.folder_toggle("")))
async def cb_folder_toggle(callback: CallbackQuery) -> None:
    folder_name = callback.data.split(":", 3)[3]

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        s = owner.settings

        try:
            monitored = json.loads(s.monitored_folders) if s.monitored_folders else []
        except json.JSONDecodeError:
            monitored = []
        if not isinstance(monitored, list):
            monitored = []

        if folder_name in monitored:
            monitored.remove(folder_name)
        else:
            monitored.append(folder_name)

        s.monitored_folders = json.dumps(monitored, ensure_ascii=False)
        await session.flush()

    await _refresh_section(callback, "privacy")
    await callback.answer()


@router.callback_query(F.data == SettingsCB.FOLDER_REFRESH)
async def cb_folder_refresh(callback: CallbackQuery) -> None:
    client = get_active_telethon_client(callback.from_user.id)
    if client:
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
        await sync_dialogs(client, owner, limit=500)
        await callback.answer("✅ Папки обновлены!")
    else:
        mgr = get_userbot_manager()
        if mgr is None:
            await callback.answer("❌ Userbot не запущен", show_alert=True)
        else:
            await callback.answer("❌ Сначала /login", show_alert=True)

    await _refresh_section(callback, "privacy")


# =====================================================================
#  MODELS CALLBACKS
# =====================================================================


@router.callback_query(F.data == SettingsCB.MODEL_RESET_ALL)
async def cb_model_reset_all(callback: CallbackQuery) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        owner.settings.model_overrides = None
        await session.flush()
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(callback.from_user.id)
    await callback.answer("🗑 Все переопределения моделей сброшены")
    await _refresh_section(callback, "models_brain")


@router.callback_query(F.data.startswith("set:model:set:"))
async def cb_model_set(callback: CallbackQuery) -> None:
    """set:model:set:<task_type>:<model_name>"""
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("Ошибка данных", show_alert=True)
        return
    task_type = parts[3]
    model_name = ":".join(parts[4:])

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        s = owner.settings
        try:
            overrides = json.loads(s.model_overrides) if s.model_overrides else {}
        except (json.JSONDecodeError, TypeError):
            overrides = {}

        if model_name == "__default__":
            overrides.pop(task_type, None)
        else:
            overrides[task_type] = model_name

        s.model_overrides = (
            json.dumps(overrides, ensure_ascii=False) if overrides else None
        )
        await session.flush()
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(callback.from_user.id)

    display = model_name if model_name != "__default__" else "по умолчанию"
    await callback.answer(f"✅ {task_type} → {display}")
    await _refresh_section(callback, f"model_sel:{task_type}")


@router.callback_query(F.data.startswith("set:model:del:"))
async def cb_model_del(callback: CallbackQuery) -> None:
    """set:model:del:<task_type>"""
    task_type = callback.data.split(":")[3]
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        s = owner.settings
        try:
            overrides = json.loads(s.model_overrides) if s.model_overrides else {}
        except (json.JSONDecodeError, TypeError):
            overrides = {}
        overrides.pop(task_type, None)
        s.model_overrides = (
            json.dumps(overrides, ensure_ascii=False) if overrides else None
        )
        await session.flush()
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(callback.from_user.id)
    await callback.answer(f"🗑 Переопределение для {task_type} удалено")
    await _refresh_section(callback, "models_brain")


@router.callback_query(F.data.startswith(SettingsCB.model_custom("")))
async def cb_model_custom(callback: CallbackQuery, state: FSMContext) -> None:
    """set:model:custom:<task_type> — ввод имени модели вручную."""
    task_type = callback.data.split(":")[3]
    await state.set_state(SettingsStates.waiting_custom_model_name)
    await state.update_data(custom_model_task_type=task_type)
    await callback.message.answer(
        "✏ Введи название модели (например: <code>deepseek-reasoner</code>, "
        "<code>gpt-4o-mini</code>). /cancel — отмена."
    )
    await callback.answer()


@router.message(SettingsStates.waiting_custom_model_name)
async def step_custom_model_name(message: Message, state: FSMContext) -> None:
    model_name = (message.text or "").strip()
    if not model_name:
        await message.answer("Пустое название. Повтори или /cancel.")
        return
    if len(model_name) > 128:
        await message.answer(
            "Слишком длинное название (макс. 128). Повтори или /cancel."
        )
        return

    from src.bot.handlers.free_text_common import _MODEL_NAME_RE

    if not _MODEL_NAME_RE.match(model_name):
        await message.answer(
            "❌ Недопустимые символы в имени модели. "
            "Допустимы: буквы, цифры, <code>@ / _ . : -</code>\n"
            "Повтори или /cancel."
        )
        return

    data = await state.get_data()
    task_type = data.get("custom_model_task_type", "default")

    catalog_warning = ""
    try:
        from src.llm.provider_catalog import get_provider, LLM_PROVIDERS

        current_provider = None
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            current_provider = owner.settings.llm_provider
            slots = await list_key_slots(session, owner)
            user_providers = {s.provider for s in slots if s.enabled}
        found_in = None
        for pi in LLM_PROVIDERS:
            if pi.name in user_providers and pi.models and model_name in pi.models:
                found_in = pi
                break
        if found_in is None and current_provider:
            provider_info = get_provider(current_provider)
            if provider_info and provider_info.models:
                catalog_warning = (
                    f"\n\n⚠️ Модель <code>{model_name}</code> не найдена в каталогах "
                    f"твоих провайдеров.\n"
                    f"Доступные у <b>{current_provider}</b>: "
                    f"{', '.join(f'<code>{m}</code>' for m in provider_info.models[:8])}\n"
                    f"Сохраняю, но проверь имя на опечатки."
                )
    except (ImportError, SQLAlchemyError, AttributeError, KeyError):
        logger.debug("catalog soft-validation skipped", exc_info=True)

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        s = owner.settings
        try:
            overrides = json.loads(s.model_overrides) if s.model_overrides else {}
        except (json.JSONDecodeError, TypeError):
            overrides = {}
        overrides[task_type] = model_name
        s.model_overrides = json.dumps(overrides, ensure_ascii=False)
        await session.flush()
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Модель для <b>{task_type}</b>: <code>{model_name}</code>{catalog_warning}"
    )


@router.callback_query(F.data.startswith(SettingsCB.model_sel("")))
async def cb_model_open(callback: CallbackQuery) -> None:
    """set:model_sel:<task_type> — открыть подменю выбора модели."""
    task_type = callback.data.split(":")[2]
    text, kb = await _render_section(callback.from_user.id, f"model_sel:{task_type}")
    await _safe_edit(callback.message, text, kb)
    await callback.answer()


# =====================================================================
#  TIMEZONE
# =====================================================================


@router.callback_query(F.data.startswith(SettingsCB.timezone("")))
async def cb_pick_tz(callback: CallbackQuery) -> None:
    tz_value = callback.data[len("set:tz:") :]
    if not is_valid_tz(tz_value):
        await callback.answer("Неизвестный TZ", show_alert=True)
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        owner.settings.timezone = tz_value
    from src.bot.handlers.free_text_common import invalidate_settings_cache

    await invalidate_settings_cache(callback.from_user.id)
    await callback.answer(f"TZ: {tz_value}")
    await _refresh_section(callback, "tz")


@router.callback_query(F.data == SettingsCB.input("timezone"))
async def cb_input_tz(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_timezone)
    await callback.message.answer(
        "Введи название часового пояса в формате IANA, например <code>Europe/Moscow</code> или "
        "<code>Asia/Tashkent</code>. /cancel — отмена."
    )
    await callback.answer()


# =====================================================================
#  DONE ADDING KEY
# =====================================================================


@router.callback_query(F.data == "set:done:key")
async def cb_done_adding_key(callback: CallbackQuery, state: FSMContext) -> None:
    """Закрывает ввод ключей, возвращается в настройки."""
    await state.clear()
    text, kb = await _render_section(callback.from_user.id, "keys")
    await _safe_edit(callback.message, text, kb)
    await callback.answer()


# =====================================================================
#  PERSONA RESET
# =====================================================================


@router.callback_query(F.data == SettingsCB.persona_reset())
async def cb_persona_reset(callback: CallbackQuery) -> None:
    ok = await reset_persona_to_snapshot(callback.from_user.id)
    if ok:
        await callback.answer("♻️ Настройки сброшены к базовым", show_alert=True)
    else:
        await callback.answer("Нет сохранённого снапшота для сброса", show_alert=True)
    await _refresh_section(callback, "personality")


# =====================================================================
#  CANCEL (for all settings FSM states)
# =====================================================================


@router.message(Command("cancel"), F.state.in_(SettingsStates))
async def cancel_settings_state(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = await _render_menu(message.from_user.id)
    await message.answer(f"🚫 Отменено.\n\n{text}", reply_markup=kb)


# =====================================================================
#  NOOP catch-all — handles decorative "set:noop:*" callbacks silently
# =====================================================================


@router.callback_query(F.data.startswith("set:noop:"))
async def cb_noop_catch_all(callback: CallbackQuery) -> None:
    """Consume noop callbacks silently (decorative buttons)."""
    await callback.answer()
