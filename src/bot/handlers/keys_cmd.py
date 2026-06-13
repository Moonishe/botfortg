"""API keys management — extracted from memory_cmd.py (Stage 3 refactor).

Handlers: /keys, interactive add/remove/import via inline keyboards.
"""

import asyncio
import logging
import time
from datetime import datetime, UTC

from aiogram import F, Router

# ── Module constants ─────────────────────────────────────────────────────
_FETCH_MODELS_TIMEOUT = 15.0  # секунд — таймаут запроса списка моделей
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

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

_PENDING_IMPORTS: dict[int, dict] = {}  # user_id → {"purpose": str, "deadline": float}

# ─── /keys add: очередь интерактивного выбора модели (inline-клавиатура) ──

_PENDING_KEY_ENTRIES: dict[
    int, dict
] = {}  # user_id → {provider, model, model_pending, category}

# ─── /keys add: кэш обнаруженных моделей ──────────────────────────────

_discovery_cache: dict[int, list[str]] = {}  # slot_id → models
_discovery_cache_ts: dict[int, float] = {}  # slot_id → timestamp
_DISCOVERY_CACHE_TTL: float = 3600.0  # 1 час

# ─── Multi-select state (выбор нескольких моделей для слота) ────────────
# Design trade-off: in-memory state resets on restart. Acceptable for single-user bot.
_multiselect_state: dict[int, set[str]] = {}  # slot_id → {выбранные model names}
_multiselect_page: dict[int, int] = {}  # slot_id → текущая страница

# ─── Capability metadata для авто-обнаруженных моделей (Improvement 2) ─────
_discovery_models_info: dict[int, list[dict]] = {}
# slot_id → [{"name": ..., "vision": bool, "embeddings": bool}, ...]

# ─── Фильтр multi-select (Improvement 2) ────────────────────────────────
_multiselect_filter: dict[int, str] = {}  # slot_id → "all" | "vision" | "embeddings"

# ─── Результаты поиска моделей (Improvement 3, временный filtered view) ───
_discovery_cache_search: dict[int, list[str]] = {}


def _get_visible_models_for_slot(slot_id: int) -> list[str]:
    """Возвращает список моделей, видимых пользователем с учётом фильтра/поиска.

    Никогда не модифицирует _discovery_cache — читает из _discovery_models_info
    (никогда не портится) или из _discovery_cache_search (временный результат поиска).
    """
    # 1. Есть активный результат поиска — возвращаем его
    if slot_id in _discovery_cache_search:
        return _discovery_cache_search[slot_id]

    # 2. Есть активный фильтр по capabilities
    ftype = _multiselect_filter.get(slot_id, "all")
    all_info = _discovery_models_info.get(slot_id, [])
    if ftype == "vision":
        return [m["name"] for m in all_info if m["vision"]]
    elif ftype == "embeddings":
        return [m["name"] for m in all_info if m["embeddings"]]

    # 3. "all" — полный список из _discovery_models_info (не _discovery_cache,
    #    который мог быть испорчен предыдущими вызовами фильтра)
    if all_info:
        return [m["name"] for m in all_info]

    # 4. Fallback: _discovery_cache (для совместимости со старыми данными)
    return _discovery_cache.get(slot_id, [])


def _cache_models_discovery(slot_id: int, models: list[str]) -> None:
    """Сохранить обнаруженные модели в кэш."""
    _discovery_cache[slot_id] = models
    _discovery_cache_ts[slot_id] = time.monotonic()


def _get_cached_discovery(slot_id: int) -> list[str] | None:
    """Получить модели из кэша, если они свежие."""
    ts = _discovery_cache_ts.get(slot_id, 0)
    if time.monotonic() - ts < _DISCOVERY_CACHE_TTL:
        return _discovery_cache.get(slot_id)
    _discovery_cache.pop(slot_id, None)
    _discovery_cache_ts.pop(slot_id, None)
    return None


def _fetch_model_capabilities(models: list[str], provider_name: str) -> list[dict]:
    """Эвристически определяет возможности моделей по имени (Improvement 2).

    Поскольку /v1/models не всегда возвращает capabilities, используем
    эвристики на основе известных паттернов в названиях моделей.
    """
    result = []
    for name in models:
        name_lower = name.lower()
        result.append(
            {
                "name": name,
                "vision": any(
                    kw in name_lower
                    for kw in (
                        "vision",
                        "gpt-4o",
                        "gpt-4-turbo",
                        "claude-3-opus",
                        "claude-3-sonnet",
                        "claude-4",
                        "gemini-2",
                        "gemini-1.5",
                        "pixtral",
                        "llava",
                        "cogvlm",
                        "qwen-vl",
                    )
                ),
                "embeddings": any(
                    kw in name_lower
                    for kw in (
                        "embed",
                        "bge",
                        "e5",
                        "text-embedding",
                        "babbage",
                        "cohere-embed",
                    )
                ),
            }
        )
    return result


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
            logger.debug("Non-critical error", exc_info=True)

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
            try:
                prov = (
                    prov_class(key, base_url=endpoint) if endpoint else prov_class(key)
                )
            except TypeError:
                prov = prov_class(key)
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


# ─── /keys add: авто-обнаружение моделей после ввода ключа ────────────


def _build_empty_model_keyboard(slot_id: int) -> InlineKeyboardMarkup:
    """Клавиатура когда модели не найдены — ручной ввод или пропуск."""
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✏️ Ввести вручную",
        callback_data=f"keys:disc_manual:{slot_id}",
    )
    kb.button(
        text="🚫 Пропустить",
        callback_data=f"keys:disc_skip:{slot_id}",
    )
    kb.adjust(2)
    return kb.as_markup()


def _build_discovered_keyboard(
    slot_id: int, models: list[str], page: int = 0
) -> InlineKeyboardMarkup:
    """Клавиатура с обнаруженными моделями, 8 на страницу."""
    kb = InlineKeyboardBuilder()
    per_page = 8
    total_pages = max(1, (len(models) + per_page - 1) // per_page)
    page = min(page, total_pages - 1) if total_pages > 0 else 0
    start = page * per_page
    page_models = models[start : start + per_page]

    for i, model in enumerate(page_models):
        global_idx = start + i
        display = model[:45] + "…" if len(model) > 45 else model
        kb.button(
            text=display,
            callback_data=f"keys:disc_pick:{slot_id}:{global_idx}",
        )
    kb.adjust(1)

    # Навигация
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="◀", callback_data=f"keys:disc_page:{slot_id}:{page - 1}"
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}", callback_data="keys:noop"
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="▶", callback_data=f"keys:disc_page:{slot_id}:{page + 1}"
                )
            )
        kb.row(*nav)

    # Ручной ввод + пропуск
    kb.row(
        InlineKeyboardButton(
            text="✏️ Ввести вручную",
            callback_data=f"keys:disc_manual:{slot_id}",
        ),
        InlineKeyboardButton(
            text="🚫 Пропустить",
            callback_data=f"keys:disc_skip:{slot_id}",
        ),
    )

    return kb.as_markup()


def _build_model_multiselect_keyboard(
    slot_id: int, models: list[str], selected: set[str], page: int = 0
) -> InlineKeyboardMarkup:
    """Multi-select клавиатура с чекбоксами для выбора моделей."""
    kb = InlineKeyboardBuilder()
    per_page = 8
    total_pages = (len(models) + per_page - 1) // per_page if models else 1
    page = max(0, min(page, total_pages - 1)) if total_pages > 0 else 0
    start = page * per_page
    page_models = models[start : start + per_page]

    # ── Строка фильтров (Improvement 2) ──────────────────────────────
    active_filter = _multiselect_filter.get(slot_id, "all")
    filter_row: list[InlineKeyboardButton] = []
    for ftype, label in [
        ("all", "🔍 Все"),
        ("vision", "👁 Vision"),
        ("embeddings", "📊 Embeddings"),
    ]:
        prefix = "▶ " if active_filter == ftype else ""
        filter_row.append(
            InlineKeyboardButton(
                text=f"{prefix}{label}",
                callback_data=f"keys:msel_filter:{slot_id}:{ftype}",
            )
        )
    kb.row(*filter_row)

    for i, model_name in enumerate(page_models):
        idx = start + i
        checked = "✅" if model_name in selected else "⬜"
        kb.row(
            InlineKeyboardButton(
                text=f"{checked} {model_name[:40]}",
                callback_data=f"keys:msel:{slot_id}:{idx}",
            )
        )

    # Навигация + кнопки действий
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀",
                callback_data=f"keys:msel_pg:{slot_id}:{page - 1}",
            )
        )
    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{max(1, total_pages)}",
            callback_data="keys:noop",
        )
    )
    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="▶",
                callback_data=f"keys:msel_pg:{slot_id}:{page + 1}",
            )
        )
    # Кнопка поиска (Improvement 3)
    nav.append(
        InlineKeyboardButton(
            text="🔍 Поиск",
            callback_data=f"keys:msel_search:{slot_id}",
        )
    )
    if nav:
        kb.row(*nav)

    kb.row(
        InlineKeyboardButton(
            text="✅ Выбрать все",
            callback_data=f"keys:msel_all:{slot_id}",
        ),
        InlineKeyboardButton(
            text="❌ Снять все",
            callback_data=f"keys:msel_none:{slot_id}",
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text=f"💾 Готово ({len(selected)} выбрано)",
            callback_data=f"keys:msel_done:{slot_id}",
        )
    )
    kb.row(
        InlineKeyboardButton(
            text="✏️ Ввести вручную",
            callback_data=f"keys:disc_manual:{slot_id}",
        )
    )

    return kb.as_markup()


async def _fetch_models_for_slot(slot) -> tuple[list[str], str | None]:
    """Получить доступные модели через API провайдера, используя ключ из слота."""
    from src.llm.provider_manager import _provider_class_for
    from src.crypto import decrypt

    provider_cls = _provider_class_for(slot.provider)
    if provider_cls is None:
        return [], f"Неизвестный провайдер: {slot.provider}"

    api_key = decrypt(slot.key_enc)
    endpoint = slot.endpoint

    # Создаём провайдера: с endpoint или без
    if endpoint:
        try:
            provider = provider_cls(api_key=api_key, base_url=endpoint)
        except TypeError:
            provider = provider_cls(api_key=api_key)
    else:
        provider = provider_cls(api_key=api_key)

    try:
        models = await asyncio.wait_for(
            provider.list_models(), timeout=_FETCH_MODELS_TIMEOUT
        )
        return sorted(models), None
    except NotImplementedError:
        return [], f"{slot.provider} не поддерживает список моделей"
    except TimeoutError:
        return [], "Таймаут запроса"
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "unauthorized" in error_msg.lower():
            return [], "Неверный API ключ"
        return [], f"Ошибка: {error_msg[:100]}"
    finally:
        try:
            await provider.close()
        except Exception:
            logger.debug("Non-critical error", exc_info=True)


async def _show_model_discovery(message: Message, slot: LlmKeySlot) -> None:
    """Загружает и показывает доступные модели для только что созданного слота."""
    # M1: очищаем старые multiselect-состояния при новом заходе —
    # предотвращает утечку stale state между сессиями одного slot_id.
    _multiselect_state.pop(slot.id, None)
    _multiselect_page.pop(slot.id, None)
    _discovery_cache_search.pop(slot.id, None)
    _multiselect_filter.pop(slot.id, None)

    status_msg = await message.answer("🔄 Загружаю список доступных моделей с сервера…")

    # Получаем модели через API провайдера
    models, error = await _fetch_models_for_slot(slot)

    # Кэшируем результат
    _cache_models_discovery(slot.id, models)

    # Сохраняем информацию о возможностях моделей (Improvement 2)
    _discovery_models_info[slot.id] = _fetch_model_capabilities(models, slot.provider)

    if error or not models:
        await status_msg.edit_text(
            f"⚠️ {'Не удалось загрузить модели: ' + error if error else 'Сервер не вернул ни одной модели.'}\n\n"
            f"Слот #{slot.id} создан. Модель можно указать позже через /models {slot.id}.",
            reply_markup=_build_empty_model_keyboard(slot.id),
        )
        return

    # Инициализируем multi-select — пока ничего не выбрано
    _multiselect_state[slot.id] = set()
    _multiselect_page[slot.id] = 0

    await status_msg.edit_text(
        f"📋 Доступные модели ({len(models)}) для {slot.provider}:\n"
        f"<i>Выбери нужные (можно несколько)</i>",
        reply_markup=_build_model_multiselect_keyboard(slot.id, models, set(), 0),
    )


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
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📋 Посмотреть доступные модели",
                            callback_data=f"keys:disc:{slot.id}",
                        )
                    ]
                ]
            )
            if not is_new:
                await message.answer(
                    f"ℹ️ Ключ {provider}/{purpose} уже был добавлен ранее (слот #{slot.id}).",
                    reply_markup=kb,
                )
            else:
                await message.answer(
                    f"✅ Ключ {provider}/{purpose} добавлен и проверен! (слот #{slot.id})",
                    reply_markup=kb,
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
                f"В кулдауне: {sum(1 for s in slots if (c := _ensure_utc(s.cooldown_until)) and c > datetime.now(UTC))}"
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
        _PENDING_IMPORTS[message.from_user.id] = {
            "purpose": purpose,
            "deadline": time.monotonic() + 300,
        }
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
                and c > datetime.now(UTC)
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
    """User picked a provider → запрос API ключа (модели — потом авто-обнаружены)."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        return
    category = parts[2]
    provider_name = parts[3]
    p = get_provider(provider_name)
    if not p:
        await callback.answer("Провайдер не найден", show_alert=True)
        return

    # Сохраняем выбор в pending и сразу запрашиваем ключ
    key_prefix = p.key_prefix if p and p.key_prefix else "API-ключ"
    endpoint_hint = (
        f"\n🔗 Endpoint: {p.default_endpoint}" if p and p.default_endpoint else ""
    )
    _PENDING_KEY_ENTRIES[callback.from_user.id] = {
        "provider": provider_name,
        "model": None,
        "model_pending": False,
        "category": category,
        "deadline": time.monotonic() + 300,
    }
    await callback.message.edit_text(
        f"📦 <b>{p.display}</b>\n"
        f"Вставь {key_prefix}\n"
        f"Или: <code>{provider_name}:ключ</code>{endpoint_hint}\n"
        f"С endpoint: <code>{provider_name}:ключ:https://твой-url.com/v1</code>",
        reply_markup=None,
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
            "deadline": time.monotonic() + 300,
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
            "deadline": time.monotonic() + 300,
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
            "deadline": time.monotonic() + 300,
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


# ─── /keys add: колбэки авто-обнаружения моделей ──────────────────────


@router.callback_query(F.data == "keys:noop")
async def cb_keys_noop(callback: CallbackQuery) -> None:
    """No-op для индикатора страницы."""
    await callback.answer()


@router.callback_query(F.data.startswith("keys:disc_pick:"))
async def cb_disc_pick(callback: CallbackQuery) -> None:
    """Пользователь выбрал модель из списка обнаруженных."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
        idx = int(parts[3])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return

    models = _get_cached_discovery(slot_id) or []
    model = models[idx] if idx < len(models) else "unknown"

    # Сохраняем модель в слот
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        stmt = select(LlmKeySlot).where(
            LlmKeySlot.id == slot_id, LlmKeySlot.user_id == owner.id
        )
        slot = (await session.execute(stmt)).scalar_one_or_none()
        if slot:
            slot.model = model
            await session.commit()

    await callback.message.edit_text(
        f"✅ Модель <b>{model}</b> сохранена в слот #{slot_id}.\n"
        f"Сменить: /models {slot_id}",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("keys:disc_page:"))
async def cb_disc_page(callback: CallbackQuery) -> None:
    """Пагинация обнаруженных моделей."""
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

    models = _get_cached_discovery(slot_id) or []
    await callback.message.edit_reply_markup(
        reply_markup=_build_discovered_keyboard(slot_id, models, page)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("keys:disc_manual:"))
async def cb_disc_manual(callback: CallbackQuery) -> None:
    """Пользователь хочет ввести модель вручную."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return

    _PENDING_KEY_ENTRIES[callback.from_user.id] = {
        "type": "manual_model",
        "slot_id": slot_id,
        "deadline": time.monotonic() + 300,
    }
    await callback.message.edit_text(
        "✏️ Введи название модели (например gpt-4o, claude-3-opus…)"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("keys:disc_skip:"))
async def cb_disc_skip(callback: CallbackQuery) -> None:
    """Пользователь пропускает выбор модели."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        slot_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверные данные.", show_alert=True)
        return

    # Получаем имя провайдера из слота для сообщения
    provider_name = "провайдера"
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        stmt = select(LlmKeySlot).where(
            LlmKeySlot.id == slot_id, LlmKeySlot.user_id == owner.id
        )
        slot = (await session.execute(stmt)).scalar_one_or_none()
        if slot:
            provider_name = slot.provider

    await callback.message.edit_text(
        f"✅ Слот #{slot_id} создан без указания модели.\n"
        f"Будет использоваться модель по умолчанию для {provider_name}.\n"
        f"Указать позже: /models {slot_id}",
    )
    await callback.answer()


# ─── Multi-select: колбэки выбора нескольких моделей ──────────────────


@router.callback_query(F.data.startswith("keys:msel:"))
async def cb_multiselect_toggle(callback: CallbackQuery):
    """Переключить чекбокс модели в multi-select.

    aiogram сериализует колбэки для одного чата — lock не нужен.
    """
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    slot_id = int(parts[2])
    idx = int(parts[3])

    models = _get_visible_models_for_slot(slot_id)
    selected = _multiselect_state.get(slot_id, set())

    model_name = models[idx] if idx < len(models) else None
    if model_name:
        if model_name in selected:
            selected.discard(model_name)
        else:
            selected.add(model_name)
    _multiselect_state[slot_id] = selected

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=_build_model_multiselect_keyboard(
                slot_id, models, selected, _multiselect_page.get(slot_id, 0)
            )
        )
    await callback.answer()


@router.callback_query(F.data.startswith("keys:msel_pg:"))
async def cb_multiselect_page(callback: CallbackQuery):
    """Пагинация multi-select."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    slot_id = int(parts[2])
    page = int(parts[3])
    _multiselect_page[slot_id] = page

    models = _get_visible_models_for_slot(slot_id)
    selected = _multiselect_state.get(slot_id, set())

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=_build_model_multiselect_keyboard(
                slot_id, models, selected, page
            )
        )
    await callback.answer()


@router.callback_query(F.data.startswith("keys:msel_all:"))
async def cb_multiselect_all(callback: CallbackQuery):
    """Выбрать все модели."""
    slot_id = int(callback.data.split(":")[2])
    models = _get_visible_models_for_slot(slot_id)
    _multiselect_state[slot_id] = set(models)

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=_build_model_multiselect_keyboard(
                slot_id, models, set(models), _multiselect_page.get(slot_id, 0)
            )
        )
    await callback.answer(f"✅ Выбрано {len(models)} моделей")


@router.callback_query(F.data.startswith("keys:msel_none:"))
async def cb_multiselect_none(callback: CallbackQuery):
    """Снять выбор со всех моделей."""
    slot_id = int(callback.data.split(":")[2])
    models = _get_visible_models_for_slot(slot_id)
    _multiselect_state[slot_id] = set()

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=_build_model_multiselect_keyboard(
                slot_id, models, set(), _multiselect_page.get(slot_id, 0)
            )
        )
    await callback.answer()


# ─── Фильтр по возможностям (Improvement 2) ───────────────────────────


@router.callback_query(F.data.startswith("keys:msel_filter:"))
async def cb_multiselect_filter(callback: CallbackQuery):
    """Фильтрация моделей по capabilities: all / vision / embeddings."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    slot_id = int(parts[2])
    ftype = parts[3]
    _multiselect_filter[slot_id] = ftype
    # Сбрасываем результаты поиска (фильтр переопределяет поиск)
    _discovery_cache_search.pop(slot_id, None)

    # _get_visible_models_for_slot учитывает активный фильтр,
    # используя _discovery_models_info (никогда не портится)
    models = _get_visible_models_for_slot(slot_id)

    selected = _multiselect_state.get(slot_id, set())
    # Убираем выбор с моделей, которых нет в отфильтрованном списке
    selected = selected & set(models)
    _multiselect_state[slot_id] = selected
    _multiselect_page[slot_id] = 0

    if callback.message:
        await callback.message.edit_text(
            callback.message.html_text or "📋 Модели",
            reply_markup=_build_model_multiselect_keyboard(
                slot_id, models, selected, 0
            ),
        )
    await callback.answer(f"Показано {len(models)} моделей")


# ─── Поиск моделей по названию (Improvement 3) ────────────────────────


@router.callback_query(F.data.startswith("keys:msel_search:"))
async def cb_multiselect_search(callback: CallbackQuery):
    """Активирует режим поиска модели по названию."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    slot_id = int(parts[2])
    _PENDING_KEY_ENTRIES[callback.from_user.id] = {
        "type": "model_search",
        "slot_id": slot_id,
        "deadline": time.monotonic() + 300,
    }
    await callback.message.edit_text(
        "🔍 Введи часть названия модели для поиска (минимум 2 символа):"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("keys:msel_done:"))
async def cb_multiselect_done(callback: CallbackQuery):
    """Сохранить выбранные модели в БД."""
    slot_id = int(callback.data.split(":")[2])
    selected = _multiselect_state.get(slot_id, set())

    if not selected:
        await callback.answer("⚠️ Выбери хотя бы одну модель", show_alert=True)
        return

    async with get_session() as session:
        from src.db.repos.key_repo import set_slot_models

        owner = await get_or_create_user(session, callback.from_user.id)

        # Проверка владения слотом
        stmt = select(LlmKeySlot).where(
            LlmKeySlot.id == slot_id, LlmKeySlot.user_id == owner.id
        )
        slot = (await session.execute(stmt)).scalar_one_or_none()
        if not slot:
            await callback.answer("Слот не найден", show_alert=True)
            return

        await set_slot_models(session, slot_id, list(selected))
        # Обратная совместимость: slot.model = первая выбранная модель
        slot.model = list(selected)[0] if selected else None
        await session.commit()

    # Очистка состояния
    _multiselect_state.pop(slot_id, None)
    _multiselect_page.pop(slot_id, None)

    model_list = "\n".join(f"  • {m}" for m in list(selected)[:10])
    suffix = f"\n  ... и ещё {len(selected) - 10}" if len(selected) > 10 else ""

    if callback.message:
        await callback.message.edit_text(
            f"✅ Сохранено {len(selected)} моделей для слота #{slot_id}:\n"
            f"{model_list}{suffix}\n\n"
            f"Управлять: /models {slot_id}"
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
        uid = message.from_user.id
        pending = _PENDING_KEY_ENTRIES.get(uid)
        if pending is None:
            return False
        # Check TTL — cleanup is opportunistic (on next message).
        # TTL is 300s, bounded by user count — no separate sweep needed.
        deadline = pending.get("deadline", 0)
        if time.monotonic() > deadline:
            _PENDING_KEY_ENTRIES.pop(uid, None)
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

    # Поиск модели по названию (Improvement 3)
    if entry.get("type") == "model_search":
        slot_id = entry["slot_id"]
        query = text.lower()[:200]  # L4: cap search query to prevent abuse

        if len(query) < 2:
            await message.answer("⚠️ Введи минимум 2 символа для поиска.")
            # Возвращаем pending для повторной попытки
            _PENDING_KEY_ENTRIES[uid] = {
                "type": "model_search",
                "slot_id": slot_id,
                "deadline": time.monotonic() + 300,
            }
            return

        # Ищем во всех моделях (полный кэш discovery, никогда не портим)
        models = _discovery_cache.get(slot_id, [])
        filtered = [m for m in models if query in m.lower()]

        if not filtered:
            await message.answer(f"🔍 Ничего не найдено по «{text}».")
            # Возвращаемся к полному списку через multi-select
            selected = _multiselect_state.get(slot_id, set())
            await message.answer(
                f"📋 Доступные модели ({len(models)}) для слота #{slot_id}:\n"
                f"<i>Выбери нужные (можно несколько)</i>",
                reply_markup=_build_model_multiselect_keyboard(
                    slot_id, models, selected, 0
                ),
            )
            return

        # Сохраняем отфильтрованные результаты только во временный кэш поиска
        # НЕ трогаем _discovery_cache — он должен оставаться нетронутым
        _discovery_cache_search[slot_id] = filtered
        selected = _multiselect_state.get(slot_id, set())
        # Убираем выбор с моделей, которых нет в результате поиска
        selected = selected & set(filtered)
        _multiselect_state[slot_id] = selected
        _multiselect_page[slot_id] = 0

        await message.answer(
            f"🔍 Найдено {len(filtered)} моделей по «{text}»:",
            reply_markup=_build_model_multiselect_keyboard(
                slot_id, filtered, selected, 0
            ),
        )
        return

    # Ручной ввод модели (из экрана авто-обнаружения)
    if entry.get("type") == "manual_model":
        slot_id = entry["slot_id"]
        model = text

        async with get_session() as session:
            owner = await get_or_create_user(session, uid)
            stmt = select(LlmKeySlot).where(
                LlmKeySlot.id == slot_id, LlmKeySlot.user_id == owner.id
            )
            slot = (await session.execute(stmt)).scalar_one_or_none()
            if slot:
                slot.model = model
                await session.commit()

        await message.answer(f"✅ Модель <b>{model}</b> сохранена в слот #{slot_id}.")
        return

    if entry.get("model_pending"):
        # User typed a custom model name → now ask for the key
        provider_name = entry["provider"]
        model = text
        _PENDING_KEY_ENTRIES[uid] = {
            "provider": provider_name,
            "model": model,
            "model_pending": False,
            "deadline": time.monotonic() + 300,
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
    # Используем категорию из pending (если есть), иначе fallback на категорию провайдера
    category = entry.get("category") or (p.category if p else "llm")

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
        logger.debug("Non-critical error", exc_info=True)

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
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 Посмотреть доступные модели",
                        callback_data=f"keys:disc:{slot.id}",
                    )
                ]
            ]
        )
        await message.answer(
            f"ℹ️ Ключ {provider_name}/{model or ''} "
            f"уже был добавлен ранее (слот #{slot.id}).",
            reply_markup=kb,
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
        try:
            prov = (
                prov_class(key_dec, base_url=endpoint)
                if endpoint
                else prov_class(key_dec)
            )
        except TypeError:
            prov = prov_class(key_dec)
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
            # Авто-обнаружение моделей после успешной валидации
            await _show_model_discovery(message, slot)
    except Exception:
        # Авто-обнаружение моделей (проверка ключа недоступна, но ключ сохранён)
        await _show_model_discovery(message, slot)


class _PendingImportFilter(BaseFilter):
    """Фильтр: сообщение от юзера, ожидающего импорта ключей.
    Пропускает только если у юзера нет активного FSM-состояния.
    """

    async def __call__(
        self, message: Message, state: FSMContext | None = None
    ) -> bool | dict:
        if message.from_user is None:
            return False
        uid = message.from_user.id
        pending = _PENDING_IMPORTS.get(uid)
        if pending is None:
            return False
        # Check TTL — cleanup is opportunistic (on next message).
        # TTL is 300s, bounded by user count — no separate sweep needed.
        deadline = pending.get("deadline", 0)
        if time.monotonic() > deadline:
            _PENDING_IMPORTS.pop(uid, None)
            return False
        if state is not None and await state.get_state() is not None:
            return False
        return True


@router.message(_PendingImportFilter())
async def _pending_import_handler(message: Message) -> None:
    """Принимает ключи после /keys import (без FSM)."""
    uid = message.from_user.id  # type: ignore[union-attr]
    entry = _PENDING_IMPORTS.pop(uid)
    purpose = entry["purpose"]
    keys_text = message.text or ""

    if not keys_text.strip() or keys_text.strip().lower() in ("/cancel", "отмена"):
        await message.answer("❌ Импорт отменён.")
        return

    await _do_import_keys(message, keys_text, purpose)
