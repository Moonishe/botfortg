"""API keys management — extracted from memory_cmd.py (Stage 3 refactor).

Handlers: /keys, interactive add/remove/import via inline keyboards.
"""

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.llm.provider_catalog import (
    LLM_PROVIDERS,
    STT_PROVIDERS,
    get_provider,
    get_providers_by_category,
)

from src.bot.callbacks import KeysCB
from src.bot.filters import OwnerOnly
from src.core.infra.timeutil import ensure_utc as _ensure_utc
from src.db.models import LlmKeySlot
from src.db.repo import (
    add_key_slot,
    get_or_create_user,
    list_key_slots,
)
from src.db.session import get_session


logger = logging.getLogger(__name__)
router = Router(name="keys_cmd")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())

# ─── /keys import: очередь ожидающих импорта (без FSM) ────────────────

_PENDING_IMPORTS: dict[int, str] = {}  # user_id → purpose

# ─── /keys add: очередь интерактивного выбора модели (inline-клавиатура) ──

_PENDING_KEY_ENTRIES: dict[
    int, dict
] = {}  # user_id → {provider, model, model_pending, category}

# ─── /keys import helpers ─────────────────────────────────────────────

_KEY_PREFIX_MAP: list[tuple[str, str]] = [
    ("sk-or", "openrouter"),  # OpenRouter: sk-or-v1-...
    ("sk-", "openai"),  # OpenAI: sk-proj-..., sk-...
    ("sk-ant", "openai"),  # Anthropic через OpenAI-совместимость
    ("AIza", "gemini"),  # Google Gemini
    ("cfat_", "cloudflare"),  # Cloudflare API token
    ("CF-", "cloudflare"),  # Cloudflare API key
    ("ms-", "mistral"),  # Mistral API (не гарантировано)
    ("mistral-", "mistral"),  # Mistral API key
]

_PROVIDER_ORDER = ("openrouter", "openai", "gemini", "mistral", "cloudflare")


def _guess_provider(key: str) -> str | None:
    """Угадывает провайдера по префиксу API-ключа."""
    for prefix, provider in _KEY_PREFIX_MAP:
        if key.startswith(prefix):
            return provider
    return None


async def _detect_provider(key: str) -> str | None:
    """Определяет провайдера: префикс → валидация → перебор."""
    # 1. Префиксная эвристика
    by_prefix = _guess_provider(key)
    if by_prefix:
        from src.llm.router import _provider_class_for

        prov_class = _provider_class_for(by_prefix)
        try:
            prov = prov_class(key)
            if await prov.validate_key():
                return by_prefix
        except Exception:
            pass

    # 2. Перебор всех провайдеров
    from src.llm.router import _provider_class_for

    for provider_name in _PROVIDER_ORDER:
        if provider_name == by_prefix:
            continue  # уже пробовали
        try:
            prov_class = _provider_class_for(provider_name)
            prov = prov_class(key)
            if await prov.validate_key():
                return provider_name
        except Exception:
            continue

    return None


async def _do_import_keys(
    message: Message, keys_text: str, purpose: str = "main"
) -> None:
    """Парсит и импортирует ключи с автоопределением провайдера.

    Поддерживает форматы:
      - api_key                         → автоопределение провайдера
      - provider:api_key                → указан провайдер, без endpoint
      - provider:api_key:endpoint       → указан провайдер и кастомный base_url
      - provider:api_key:endpoint:model → указан провайдер, endpoint и модель
    """
    from src.llm.router import _provider_class_for
    from src.crypto import decrypt

    # Собираем имена всех провайдеров из каталога + legacy
    _catalog_names = {p.name for p in LLM_PROVIDERS + STT_PROVIDERS}
    _all_known = _catalog_names | set(_PROVIDER_ORDER)

    lines = keys_text.strip().split("\n")
    raw_entries: list[
        tuple[str | None, str, str | None, str | None]
    ] = (  # (provider, key, endpoint, model)
        []
    )
    for line in lines:
        line = line.strip()
        # Пропускаем комментарии и пустые строки
        if not line or line.startswith("#"):
            continue
        # Разбираем format: provider:key[:endpoint[:model]]
        parts = line.split(":", 3)
        first = parts[0].lower() if parts else ""
        if first in _all_known and len(parts) >= 2:
            if len(parts) == 4:
                raw_entries.append((first, parts[1], parts[2], parts[3]))
            elif len(parts) == 3:
                # Может быть provider:key:endpoint или provider:key:model
                # Если часть 2 похожа на URL — endpoint, иначе model
                p = get_provider(first)
                if p and p.models and parts[2] in p.models:
                    raw_entries.append((first, parts[1], None, parts[2]))
                else:
                    raw_entries.append((first, parts[1], parts[2], None))
            else:
                raw_entries.append((first, parts[1], None, None))
        else:
            raw_entries.append((None, line, None, None))

    if not raw_entries:
        await message.answer("❌ Не найдено ни одного ключа в сообщении.")
        return

    # Удаляем исходное сообщение (безопасность)
    try:
        await message.delete()
    except Exception:
        logger.warning("failed to delete message with keys")

    results: list[str] = []
    by_provider: dict[str, int] = {}
    total_found = 0

    for i, (explicit_provider, api_key, endpoint, explicit_model) in enumerate(
        raw_entries
    ):
        if explicit_provider:
            detected = explicit_provider
        else:
            detected = await _detect_provider(api_key)
        if not detected:
            results.append(f"  ❓ ключ #{i + 1} — провайдер не определён")
            continue

        by_provider[detected] = by_provider.get(detected, 0) + 1

        p = get_provider(detected)
        category = p.category if p else "llm"

        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            slot, is_new = await add_key_slot(
                session,
                owner,
                detected,
                api_key,
                purpose=purpose,
                label=f"{detected}/{purpose}",
                priority=i,
                endpoint=endpoint,
                model=explicit_model,
                category=category,
            )

        if not is_new:
            results.append(
                f"  #{slot.id} {detected}/{purpose} — Этот ключ уже добавлен (слот #{slot.id})"
            )
            total_found += 1
            continue

        # Валидируем (только для новых слотов)
        try:
            try:
                key = decrypt(slot.key_enc)
            except ValueError:
                logger.warning("Key decryption failed for slot %d", slot.id)
                results.append(f"  #{slot.id} — ❌ ключ повреждён")
                continue
            prov_class = _provider_class_for(detected)
            prov = prov_class(key, base_url=endpoint) if endpoint else prov_class(key)
            valid = await prov.validate_key()
            if not valid:
                async with get_session() as session:
                    owner = await get_or_create_user(session, message.from_user.id)
                    bad_slot = await session.get(LlmKeySlot, slot.id)
                    if bad_slot:
                        await session.delete(bad_slot)
                        await session.flush()
                results.append(
                    f"  #{slot.id} {detected}/{purpose} ❌ (не прошёл проверку)"
                )
            else:
                results.append(f"  #{slot.id} {detected}/{purpose} ✅")
                total_found += 1
        except Exception:
            results.append(f"  #{slot.id} {detected}/{purpose} ✅ (ошибка проверки)")
            total_found += 1

    # Итоговое сообщение
    header = f"📥 <b>Импорт ключей</b> (purpose: {purpose})\n\n"
    if by_provider:
        header += "Найдено: " + ", ".join(
            f"{p} ×{c}" for p, c in sorted(by_provider.items())
        )
        header += f"\nУспешно: {total_found}/{len(raw_entries)}\n\n"
    else:
        header += f"Успешно: 0/{len(raw_entries)}\n\n"

    await message.answer(header + "\n".join(results))


# ─── /keys add: вспомогательные клавиатуры для интерактивного выбора ────


def _build_category_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора категории ключа (LLM / STT / TTS)."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🧠 LLM (чат, память)", callback_data=KeysCB.category("llm"))
    kb.button(text="🎤 STT (голос→текст)", callback_data=KeysCB.category("stt"))
    kb.button(text="🔊 TTS (текст→голос)", callback_data=KeysCB.category("tts"))
    kb.button(text="🔙 Закрыть", callback_data=KeysCB.BACK_CLOSE)
    kb.adjust(1)
    return kb.as_markup()


def _build_provider_keyboard(category: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора провайдера для категории."""
    providers = get_providers_by_category(category)
    kb = InlineKeyboardBuilder()
    tier_icon = {"free": "🆓", "paid": "💰", "custom": "🔧", "local": "🖥️"}
    for p in providers:
        kb.button(
            text=f"{tier_icon.get(p.tier, '')} {p.display}",
            callback_data=KeysCB.provider(category, p.name),
        )
    kb.button(text="🔙 Назад", callback_data=KeysCB.BACK_CAT)
    kb.adjust(1)
    return kb.as_markup()


def _build_model_keyboard(provider_name: str) -> InlineKeyboardMarkup | None:
    """Клавиатура выбора модели для провайдера."""
    p = get_provider(provider_name)
    if not p:
        return None
    kb = InlineKeyboardBuilder()
    if p.models:
        for m in p.models:
            kb.button(text=f"📦 {m}", callback_data=KeysCB.model(p.name, m))
    elif p.category in ("stt", "tts"):
        # STT/TTS провайдеры без предопределённых моделей — пропускаем выбор модели
        kb.button(
            text="➡️ Continue without model",
            callback_data=f"keys:model:{p.name}:none",
        )
    # Custom/local провайдеры — кнопка ручного ввода модели
    if p.tier in ("custom", "local"):
        kb.button(
            text="✏️ Ввести модель вручную",
            callback_data=f"keys:model:{p.name}:__custom__",
        )
    kb.button(text="🔙 Назад", callback_data=KeysCB.back_provider(p.category))
    kb.adjust(1)
    return kb.as_markup()


@router.message(Command("keys"))
async def cmd_keys(message: Message) -> None:
    """Управление ключами LLM."""
    args = (message.text or "").split()
    # Phase 2: интерактивный выбор через inline-клавиатуру
    if len(args) >= 2 and args[1] == "add" and len(args) < 4:
        await message.answer(
            "Выбери категорию ключа:", reply_markup=_build_category_keyboard()
        )
        return

    if len(args) >= 4 and args[1] == "add":
        provider = args[2].lower()
        purpose_raw = args[3].lower()
        api_keys_raw = " ".join(args[4:])

        if provider not in ("openrouter", "openai", "gemini", "mistral", "cloudflare"):
            await message.answer(
                "❌ Провайдер: openrouter, openai, gemini, mistral или cloudflare"
            )
            return

        # Auto-increment priority when purpose ends with "+"
        auto_inc = purpose_raw.endswith("+")
        purpose = purpose_raw.rstrip("+") if auto_inc else purpose_raw

        # Split by comma for bulk add
        keys = [k.strip() for k in api_keys_raw.split(",") if k.strip()]
        if not keys:
            await message.answer("❌ Не указан ключ(и).")
            return

        # Удаляем сообщение с ключами из чата
        try:
            await message.delete()
        except Exception:
            logger.warning("failed to delete message with key")

        from src.llm.router import _provider_class_for
        from src.crypto import decrypt

        success = 0
        failed = 0
        results = []

        for i, api_key in enumerate(keys):
            priority = i if auto_inc else 0
            async with get_session() as session:
                owner = await get_or_create_user(session, message.from_user.id)
                slot, is_new = await add_key_slot(
                    session,
                    owner,
                    provider,
                    api_key,
                    purpose=purpose,
                    label=f"{provider}/{purpose}",
                    priority=priority,
                )
            if not is_new:
                results.append(
                    f"  #{slot.id} {provider}/{purpose} — Этот ключ уже добавлен (слот #{slot.id})"
                )
                success += 1
                continue
            # Валидируем ключ (только для новых слотов)
            try:
                try:
                    key = decrypt(slot.key_enc)
                except ValueError:
                    logger.warning("Key decryption failed for slot %d", slot.id)
                    results.append(f"  #{slot.id} — ❌ ключ повреждён")
                    continue
                prov_class = _provider_class_for(provider)
                prov = prov_class(key)
                valid = await prov.validate_key()
                if not valid:
                    async with get_session() as session:
                        owner = await get_or_create_user(session, message.from_user.id)
                        bad_slot = await session.get(LlmKeySlot, slot.id)
                        if bad_slot:
                            await session.delete(bad_slot)
                            await session.flush()
                    results.append(f"  #{slot.id} {provider}/{purpose} ❌")
                    failed += 1
                else:
                    results.append(f"  #{slot.id} {provider}/{purpose} ✅")
                    success += 1
            except Exception:
                results.append(
                    f"  #{slot.id} {provider}/{purpose} ✅ (ошибка проверки)"
                )
                success += 1

        if len(keys) == 1 and success == 1:
            if not is_new:
                await message.answer(
                    f"ℹ️ Ключ {provider}/{purpose} уже был добавлен ранее (слот #{slot.id})."
                )
            else:
                await message.answer(
                    f"✅ Ключ {provider}/{purpose} добавлен и проверен! (слот #{slot.id})"
                )
        elif len(keys) == 1 and failed == 1:
            await message.answer(
                f"❌ Ключ {provider}/{purpose} не прошёл валидацию. Проверь ключ."
            )
        else:
            lines = [f"<b>Добавлено {len(keys)} ключей {provider}/{purpose}:</b>", ""]
            lines.extend(results)
            await message.answer("\n".join(lines))
        return

    if len(args) >= 3 and args[1] == "remove":
        try:
            slot_id = int(args[2])
        except ValueError:
            await message.answer(
                "❌ Неверный номер слота. Использование: <code>/keys remove &lt;номер&gt;</code>\n"
                "Пример: <code>/keys remove 5</code>"
            )
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            slot = await session.get(LlmKeySlot, slot_id)
            if slot and slot.user_id == owner.id:
                provider = slot.provider
                purpose = slot.purpose
                await session.delete(slot)
                await session.commit()
                # Invalidate settings cache after mutation
                from src.bot.handlers.free_text_common import invalidate_settings_cache

                await invalidate_settings_cache(message.from_user.id)
                await message.answer(
                    f"✅ Слот #{slot_id} ({provider}/{purpose}) удалён."
                )
            else:
                await message.answer("❌ Слот не найден или не твои.")
        return

    if len(args) >= 2 and args[1] == "remove":
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            slots = await list_key_slots(session, owner)
            if not slots:
                await message.answer("❌ Нет ключевых слотов для удаления.")
                return
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{'✅' if s.enabled else '🚫'} #{s.id} {s.provider}/{s.purpose}",
                            callback_data=KeysCB.remove(s.id),
                        )
                    ]
                    for s in slots
                ]
            )
            await message.answer("🗑 Выбери слот для удаления:", reply_markup=kb)
        return

    if len(args) >= 3 and args[1] == "toggle":
        try:
            slot_id = int(args[2])
        except (ValueError, IndexError):
            await message.answer("❌ Usage: /keys toggle <number>")
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            slot = await session.get(LlmKeySlot, slot_id)
            if slot and slot.user_id == owner.id:
                slot.enabled = not slot.enabled
                await session.commit()
                # Invalidate settings cache after mutation
                from src.bot.handlers.free_text_common import invalidate_settings_cache

                await invalidate_settings_cache(message.from_user.id)
                status = "включён" if slot.enabled else "выключен"
                await message.answer(
                    f"✅ Слот #{slot_id} ({slot.provider}/{slot.purpose}) {status}."
                )
            else:
                await message.answer("❌ Слот не найден или не твой.")
        return

    if len(args) >= 2 and args[1] == "--stats":
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            slots = await list_key_slots(session, owner)
            if not slots:
                await message.answer("Нет ключевых слотов.")
                return
            lines = ["<b>📊 Статистика ключей:</b>", ""]
            total_used = sum(s.usage_count for s in slots)
            total_fail = sum(s.failure_count for s in slots)
            fail_rate = (total_fail / max(total_used, 1)) * 100
            lines.append(f"Всего вызовов: {total_used}")
            lines.append(f"Всего фейлов: {total_fail} ({fail_rate:.1f}%)")
            lines.append(f"Активных: {sum(1 for s in slots if s.enabled)}")
            lines.append(
                f"В кулдауне: {sum(1 for s in slots if (c := _ensure_utc(s.cooldown_until)) and c > datetime.now(timezone.utc))}"
            )
            lines.append("")
            for s in sorted(
                slots,
                key=lambda s: s.failure_count / max(s.usage_count, 1),
                reverse=True,
            )[:5]:
                fail_pct = (s.failure_count / max(s.usage_count, 1)) * 100
                lines.append(
                    f"<b>{s.provider}/{s.purpose}</b>: {s.usage_count}× вызовов, {s.failure_count}× фейлов ({fail_pct:.1f}%)"
                )
            await message.answer("\n".join(lines))
            return

    if len(args) >= 2 and args[1] == "import":
        # /keys import [purpose] [keys] — автоимпорт ключей с определением провайдера
        purpose = "main"
        keys_text = ""
        if len(args) >= 3:
            if args[2].lower() in (
                "main",
                "draft",
                "memory",
                "background",
                "search",
                "analysis",
                "urgent",
                "fallback",
            ):
                purpose = args[2].lower()
                keys_text = " ".join(args[3:]) if len(args) >= 4 else ""
            else:
                keys_text = " ".join(args[2:])

        # Если ключи переданы в команде — парсим сразу
        if keys_text.strip():
            keys_text = keys_text.replace(",", "\n")
            await _do_import_keys(message, keys_text, purpose)
            return

        # Иначе — ждём следующего сообщения
        _PENDING_IMPORTS[message.from_user.id] = purpose
        await message.answer(
            "📥 <b>Отправь ключи:</b>\n\n"
            "Один ключ на строку. Можно несколько.\n"
            "Провайдер определится автоматически.\n\n"
            "<i>Пример:</i>\n"
            "<code>sk-proj-abcd1234...\n"
            "cfat_xyz9876...\n"
            "AIzaSyABC...</code>\n\n"
            "Для отмены — /cancel"
        )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        slots = await list_key_slots(session, owner)
        if not slots:
            await message.answer(
                "🔑 <b>Нет ключевых слотов.</b>\n\n"
                "Добавь ключ через /keys add openai main sk-...\n"
                "Где:\n"
                "• провайдер: openrouter/openai/gemini/mistral/cloudflare\n"
                "• purpose: main/draft/memory/background/search/analysis/urgent/fallback\n"
                "• ключ: сам API ключ"
            )
            return
        lines = ["<b>🔑 Ключевые слоты:</b>", ""]
        for s in slots[:10]:
            status = "✅" if s.enabled else "🚫"
            cool = (
                " 🔒"
                if (c := _ensure_utc(s.cooldown_until))
                and c > datetime.now(timezone.utc)
                else ""
            )
            lines.append(
                f"{status} <b>{s.provider}</b> / {s.purpose} "
                f"(приоритет {s.priority}, исп. {s.usage_count}×{cool})"
            )
            if s.endpoint:
                lines.append(f"   🔗 {s.endpoint}")
            if s.last_error:
                lines.append(f"   ⚠️ {s.last_error[:80]}")
            if s.label:
                lines.append(f"   🏷 {s.label}")
        lines.append("")
        lines.append(
            "<i>/keys add &lt;provider&gt; &lt;purpose&gt; &lt;key&gt; — добавить ключ</i>"
        )
        lines.append(
            "<i>/keys add openai main sk-xxx https://api.мой-сервер.com/v1 — с кастомным endpoint</i>"
        )
        lines.append(
            "<i>/keys import [purpose] [keys...] — автоимпорт (формат: provider:key:endpoint)</i>"
        )
        lines.append("<i>/keys remove &lt;slot_id&gt; — удалить слот</i>")
        lines.append("<i>/keys toggle &lt;slot_id&gt; — вкл/выкл слот</i>")
        await message.answer("\n".join(lines))


@router.callback_query(F.data.startswith(KeysCB.remove("")))
async def cb_keys_remove(callback: CallbackQuery) -> None:
    """Удалить слот ключа по inline-кнопке."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    slot_id = int(parts[2])
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        slot = await session.get(LlmKeySlot, slot_id)
        if slot and slot.user_id == owner.id:
            provider = slot.provider
            purpose = slot.purpose
            await session.delete(slot)
            await session.commit()
            # Invalidate settings cache after mutation
            from src.bot.handlers.free_text_common import invalidate_settings_cache

            await invalidate_settings_cache(callback.from_user.id)
            text = f"✅ Слот #{slot_id} ({provider}/{purpose}) удалён."
        else:
            text = "❌ Слот не найден или не твой."
    if callback.message:
        await callback.message.edit_text(text)
    await callback.answer()


# ─── /keys add: inline keyboard callbacks (Phase 2) ────────────────────


@router.callback_query(F.data.startswith(KeysCB.category("")))
async def cb_keys_cata(callback: CallbackQuery) -> None:
    """User picked a category → show providers."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        return
    category = parts[2]
    category_names = {
        "llm": "LLM (чат, память)",
        "stt": "STT (голос→текст)",
        "tts": "TTS (текст→голос)",
    }
    await callback.message.edit_text(
        f"📂 <b>Категория: {category_names.get(category, category)}</b>\n"
        f"Выбери провайдера:",
        reply_markup=_build_provider_keyboard(category),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("keys:cat:"))
async def cb_keys_cat(callback: CallbackQuery) -> None:
    """User picked a provider → show models."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        return
    category = parts[2]  # noqa: F841
    provider_name = parts[3]
    p = get_provider(provider_name)
    if not p:
        await callback.answer("Провайдер не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"📋 <b>{p.display}</b>\n{p.description}\n\nДоступные модели:",
        reply_markup=_build_model_keyboard(provider_name),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("keys:model:"))
async def cb_keys_model(callback: CallbackQuery) -> None:
    """User picked a model → ask for API key."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        return
    provider_name = parts[2]
    model = parts[3]

    if model == "__custom__":
        # Custom model → user will type it
        _PENDING_KEY_ENTRIES[callback.from_user.id] = {
            "provider": provider_name,
            "model_pending": True,
        }
        await callback.message.edit_text(
            f"🔧 Введи название модели для <b>{provider_name}</b>.\n"
            f"Например: <code>gpt-4o</code> или <code>claude-3-opus</code>",
            reply_markup=None,
        )
    elif model == "none":
        # STT/TTS провайдер без модели → сразу запрашиваем ключ
        p = get_provider(provider_name)
        key_prefix = p.key_prefix if p and p.key_prefix else "API-ключ"
        _PENDING_KEY_ENTRIES[callback.from_user.id] = {
            "provider": provider_name,
            "model": None,
            "model_pending": False,
        }
        display = p.display if p else provider_name
        await callback.message.edit_text(
            f"📦 <b>{display}</b>\n"
            f"Вставь {key_prefix}\n"
            f"Или: <code>{provider_name}:ключ</code>",
            reply_markup=None,
        )
    else:
        # Known model → ask for API key
        p = get_provider(provider_name)
        endpoint_hint = (
            f"\n🔗 Endpoint: {p.default_endpoint}" if p and p.default_endpoint else ""
        )
        _PENDING_KEY_ENTRIES[callback.from_user.id] = {
            "provider": provider_name,
            "model": model,
            "model_pending": False,
        }
        display = p.display if p else provider_name
        key_prefix = p.key_prefix if p and p.key_prefix else "API-ключ"
        await callback.message.edit_text(
            f"📦 <b>{display} — {model}</b>\n"
            f"Вставь {key_prefix}\n"
            f"Или: <code>{provider_name}:ключ</code>{endpoint_hint}\n"
            f"С endpoint: <code>{provider_name}:ключ:https://твой-url.com/v1</code>",
            reply_markup=None,
        )
    await callback.answer()


# ─── /keys add: back navigation callbacks ───────────────────────────────


@router.callback_query(F.data == KeysCB.BACK_CLOSE)
async def cb_keys_back_close(callback: CallbackQuery) -> None:
    """Close the key addition dialog."""
    await callback.message.edit_text("🔑 Добавление ключа отменено.")
    await callback.answer()


@router.callback_query(F.data == KeysCB.BACK_CAT)
async def cb_keys_back_cat(callback: CallbackQuery) -> None:
    """Back to category selection."""
    await callback.message.edit_text(
        "Выбери категорию ключа:", reply_markup=_build_category_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith(KeysCB.back_provider("")))
async def cb_keys_back_provider(callback: CallbackQuery) -> None:
    """Back to provider list for category."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        return
    category = parts[3]
    category_names = {
        "llm": "LLM (чат, память)",
        "stt": "STT (голос→текст)",
        "tts": "TTS (текст→голос)",
    }
    await callback.message.edit_text(
        f"📂 <b>Категория: {category_names.get(category, category)}</b>\n"
        f"Выбери провайдера:",
        reply_markup=_build_provider_keyboard(category),
    )
    await callback.answer()


# ─── /keys add: фильтр и обработчик pending key entry ───────────────────


class _PendingKeyEntryFilter(BaseFilter):
    """Фильтр: сообщение от юзера с активным интерактивным выбором ключа."""

    async def __call__(
        self, message: Message, state: FSMContext | None = None
    ) -> bool | dict:
        if message.from_user is None:
            return False
        if message.from_user.id not in _PENDING_KEY_ENTRIES:
            return False
        if state is not None and await state.get_state() is not None:
            return False
        return True


@router.message(_PendingKeyEntryFilter())
async def _pending_key_entry_handler(message: Message) -> None:
    """Принимает ключ или модель после inline-выбора."""
    from src.crypto import decrypt
    from src.llm.router import _provider_class_for

    uid = message.from_user.id
    entry = _PENDING_KEY_ENTRIES.pop(uid)
    text = (message.text or "").strip()

    if not text or text.lower() in ("/cancel", "отмена"):
        await message.answer("❌ Добавление ключа отменено.")
        return

    if entry.get("model_pending"):
        # User typed a custom model name → now ask for the key
        provider_name = entry["provider"]
        model = text
        _PENDING_KEY_ENTRIES[uid] = {
            "provider": provider_name,
            "model": model,
            "model_pending": False,
        }
        p = get_provider(provider_name)
        display = p.display if p else provider_name
        await message.answer(
            f"📦 <b>{display} — {model}</b>\n"
            f"Теперь вставь API-ключ:\n"
            f"<code>{provider_name}:ключ</code> или просто сам ключ"
        )
        return

    # User sent the API key — parse and import
    provider_name = entry["provider"]
    model = entry.get("model")
    p = get_provider(provider_name)
    category = p.category if p else "llm"

    api_key = text
    endpoint = None

    # Try parsing provider:key or plain key formats
    parts = text.split(":", 1)
    if len(parts) == 2 and parts[0].lower() == provider_name:
        # Format: provider:key or provider:key:endpoint
        sub_parts = parts[1].split(":", 1)
        if len(sub_parts) == 2:
            api_key = sub_parts[0]
            endpoint = sub_parts[1]
        else:
            api_key = parts[1]

    # Delete message for security
    try:
        await message.delete()
    except Exception:
        pass

    async with get_session() as session:
        owner = await get_or_create_user(session, uid)
        slot, is_new = await add_key_slot(
            session,
            owner,
            provider_name,
            api_key,
            purpose="main",
            label=f"{provider_name}/main",
            priority=0,
            endpoint=endpoint,
            model=model,
            category=category,
        )

    if not is_new:
        await message.answer(
            f"ℹ️ Ключ {provider_name}/{model or ''} "
            f"уже был добавлен ранее (слот #{slot.id})."
        )
        return

    # Validate key
    try:
        try:
            key_dec = decrypt(slot.key_enc)
        except ValueError:
            logger.warning("Key decryption failed for slot %d", slot.id)
            await message.answer(f"  #{slot.id} — ❌ ключ повреждён")
            return
        prov_class = _provider_class_for(provider_name)
        prov = (
            prov_class(key_dec, base_url=endpoint) if endpoint else prov_class(key_dec)
        )
        valid = await prov.validate_key()
        if not valid:
            async with get_session() as session:
                bad_slot = await session.get(LlmKeySlot, slot.id)
                if bad_slot:
                    await session.delete(bad_slot)
                    await session.flush()
            await message.answer(
                f"❌ Ключ {provider_name}/{model or ''} "
                f"не прошёл валидацию. Проверь ключ."
            )
        else:
            await message.answer(
                f"✅ Ключ {provider_name}/{model or ''} "
                f"добавлен и проверен! (слот #{slot.id})"
            )
    except Exception:
        await message.answer(
            f"✅ Ключ {provider_name}/{model or ''} "
            f"добавлен! (слот #{slot.id}, проверка недоступна)"
        )


class _PendingImportFilter(BaseFilter):
    """Фильтр: сообщение от юзера, ожидающего импорта ключей.
    Пропускает только если у юзера нет активного FSM-состояния.
    """

    async def __call__(
        self, message: Message, state: FSMContext | None = None
    ) -> bool | dict:
        if message.from_user is None:
            return False
        if message.from_user.id not in _PENDING_IMPORTS:
            return False
        if state is not None and await state.get_state() is not None:
            return False
        return True


@router.message(_PendingImportFilter())
async def _pending_import_handler(message: Message) -> None:
    """Принимает ключи после /keys import (без FSM)."""
    uid = message.from_user.id  # type: ignore[union-attr]
    purpose = _PENDING_IMPORTS.pop(uid)
    keys_text = message.text or ""

    if not keys_text.strip() or keys_text.strip().lower() in ("/cancel", "отмена"):
        await message.answer("❌ Импорт отменён.")
        return

    await _do_import_keys(message, keys_text, purpose)
