"""/avito — поиск и мониторинг объявлений на Авито."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import threading
import time
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, select, update
from sqlalchemy.orm import selectinload

from src.bot.filters import OwnerOnly
from src.config import settings
from src.core.avito.service import ScanResult, SearchParams, scan_avito_cached
from src.core.infra.sqlite_persistent import (
    PersistentSQLite,
    get_db_path,
    migrate_from_app_db,
)

from src.db.models._avito import AvitoListing, AvitoWatch
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.core.infra.text_sanitizer import sanitize_html

logger = logging.getLogger(__name__)

router = Router(name="avito_cmd")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())

def _callback_message(callback: CallbackQuery) -> Message | None:
    return callback.message if isinstance(callback.message, Message) else None

def _migrate_avito_cache_from_app_db(conn: sqlite3.Connection) -> None:
    """Copy legacy avito_query_cache rows from app.db if new DB is empty."""
    migrate_from_app_db(
        conn,
        table_name="avito_query_cache",
        columns=["hash", "query", "created_at"],
        old_db_path=get_db_path(),
        log_label="avito query cache",
    )


# Persisted query cache for callback_data (query_hash → (query_string, ts))
# Uses SQLite so cache survives bot restart. In-memory entries are TTL-capped.
_QUERY_CACHE: dict[str, tuple[str, float]] = {}
# Lock for the in-memory _QUERY_CACHE dict only. SQLite access is serialised
# by the internal lock inside the shared PersistentSQLite helper.
_cache_lock = threading.RLock()
_QUERY_CACHE_TTL_SEC = 3600

# Persistent SQLite connection managed by the shared infra helper.
# The bot layer no longer owns raw SQLite DDL or connection lifecycle.
_cache_db = PersistentSQLite(
    db_path=settings.data_dir / "avito_query_cache.db",
    table_ddl="""
        CREATE TABLE IF NOT EXISTS avito_query_cache(
            hash TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            created_at REAL
        )
    """,
    init_fn=_migrate_avito_cache_from_app_db,
)


def _evict_expired_cache_entries() -> None:
    """Drop in-memory cache entries older than TTL."""
    cutoff = time.time() - _QUERY_CACHE_TTL_SEC
    with _cache_lock:
        stale = [h for h, (_, ts) in _QUERY_CACHE.items() if ts < cutoff]
        for h in stale:
            del _QUERY_CACHE[h]


def _cache_put_query(hash_str: str, query: str) -> None:
    """Сохраняет маппинг хэша в SQLite и in-memory."""
    now = time.time()
    try:
        with _cache_lock:
            _evict_expired_cache_entries()
        with _cache_db.locked() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO avito_query_cache(hash, query, created_at) "
                "VALUES (?, ?, ?)",
                (hash_str, query, now),
            )
            conn.commit()  # commit INSERT before pruning — avoids orphaned INSERT
            # Prune rows older than 24h (TTL is 1h, 24h gives generous headroom)
            cutoff = now - 86400
            try:
                deleted = conn.execute(
                    "DELETE FROM avito_query_cache WHERE created_at < ?", (cutoff,)
                ).rowcount
                if deleted:
                    logger.debug("Pruned %d stale avito query cache rows", deleted)
                conn.commit()
            except Exception:
                logger.debug("Avito cache pruning failed (non-critical)", exc_info=True)
        # Update in-memory cache only AFTER DB commit succeeds.
        # If DB write fails, the stale entry never enters in-memory,
        # keeping the two layers consistent.
        with _cache_lock:
            _QUERY_CACHE[hash_str] = (query, now)
    except Exception:
        logger.exception("_cache_put_query failed for hash=%s", hash_str)


def _cache_get_query(hash_str: str) -> str | None:
    """Извлекает запрос из in-memory или SQLite по хэшу."""
    try:
        with _cache_lock:
            _evict_expired_cache_entries()
            cached = _QUERY_CACHE.get(hash_str)
            if cached is not None:
                query, _ = cached
                return query
        # Query SQLite without holding _cache_lock (prevents lock-ordering inversion)
        query_text: str | None = None
        with _cache_db.locked() as conn:
            row = conn.execute(
                "SELECT query FROM avito_query_cache WHERE hash = ?", (hash_str,)
            ).fetchone()
            if row:
                query_text = row[0]
        # Update in-memory cache AFTER releasing DB lock
        if query_text is not None:
            with _cache_lock:
                _QUERY_CACHE[hash_str] = (query_text, time.time())
            return query_text
    except Exception:
        logger.exception("_cache_get_query failed for hash=%s", hash_str)
    return None


async def close_avito_cache_db() -> None:
    """Close the persistent avito query-cache SQLite connection."""
    await asyncio.to_thread(_cache_db.close)


async def _cb_hash(query: str) -> str:
    """Короткий хэш запроса для callback_data (макс 16 символов)."""
    h = hashlib.sha256(query.encode()).hexdigest()[:16]
    await asyncio.to_thread(_cache_put_query, h, query)
    return h


async def _cb_query(hash_str: str) -> str | None:
    """Извлекает запрос из кэша по хэшу (in-memory + SQLite fallback)."""
    return await asyncio.to_thread(_cache_get_query, hash_str)


# ═══════════════════════════════════════════════════════════════════════════
#  Константы и утилиты
# ═══════════════════════════════════════════════════════════════════════════

ITEMS_PER_PAGE = 5

# Безопасен под asyncio: read-only константа, инициализируется при загрузке модуля.
_GRADE_DISPLAY: dict[str, str] = {
    "A": "🏆 Отличная сделка",
    "B": "✅ Хорошая цена",
    "C": "👌 Нормально",
    "D": "⚠️ Дороговато",
    "F": "❌ Не стоит",
}

# Безопасен под asyncio: read-only константа, инициализируется при загрузке модуля.
_RISK_DISPLAY: dict[str, str] = {
    "low": "🟢 Низкий",
    "medium": "🟡 Средний",
    "high": "🔴 Высокий",
}


def _price_to_int(price: object) -> int | None:
    """Приводит цену к int, если возможно."""
    if price is None:
        return None
    if isinstance(price, str):
        try:
            return int(float(price.replace(" ", "").replace("\xa0", "")))
        except (ValueError, TypeError):
            return None
    if isinstance(price, (int, float)):
        return int(price)
    return None


def _fmt_price(price: object) -> str:
    """Форматирует цену с разделителем тысяч."""
    num = _price_to_int(price)
    if num is None:
        return "не указана"
    return f"{num:,}".replace(",", " ") + " ₽"


def _grade_label(grade: str | None, score: int | None) -> str:
    """Возвращает строку оценки с эмодзи."""
    if grade and grade in _GRADE_DISPLAY:
        score_str = f" ({score}/100)" if score is not None else ""
        return f"{_GRADE_DISPLAY[grade]}{score_str}"
    return "Нет оценки"


def _scam_line(scam: dict[str, object] | None) -> str:
    """Форматирует строку мошенничества."""
    if not scam or not scam.get("is_suspicious"):
        return ""
    raw_risk = scam.get("risk") or ""
    risk = _RISK_DISPLAY.get(str(raw_risk), str(raw_risk))
    reasons_raw = scam.get("reasons") or []
    if not isinstance(reasons_raw, list):
        reasons_raw = []
    reasons = "; ".join(str(r) for r in reasons_raw[:2])
    return f"\n⚠️ Подозрительно ({sanitize_html(risk)}): {sanitize_html(reasons)}"


def _deal_score_key(item: dict[str, Any]) -> int:
    """Sort key for listings by deal_score."""
    score = (item.get("deal_score") or {}).get("score", 0)
    return score if score is not None else 0


def _condition_line(condition: str | None) -> str:
    """Форматирует состояние."""
    if not condition:
        return ""
    return f" | 📦 {sanitize_html(condition)}"


def _delivery_line(has_delivery: bool) -> str:
    """Индикатор доставки."""
    return " | 🚚 Доставка" if has_delivery else ""


def _listing_summary(listing: dict, idx: int) -> str:
    """Короткое описание одного объявления для списка."""
    title = sanitize_html(listing.get("title", "Без названия"))
    price = _fmt_price(listing.get("price"))
    deal = listing.get("deal_score") or {}
    grade = deal.get("grade")
    score = deal.get("score")
    scam = listing.get("scam_check")
    condition = listing.get("condition")
    delivery = listing.get("delivery", False)
    url = sanitize_html(listing.get("url", ""))

    lines = [
        f"<b>{idx}. {title}</b>",
        f"💰 {price}  {_grade_label(grade, score)}",
        f"{_condition_line(condition)}{_delivery_line(delivery)}",
    ]
    scam_text = _scam_line(scam)
    if scam_text:
        lines.append(scam_text)
    if url:
        lines.append(f"🔗 {url}")
    return "\n".join(lines)


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """Разбивает длинный текст на части, не превышающие max_len."""
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # Ищем последний перенос строки в пределах лимита
        cut = text.rfind("\n", 0, max_len)
        if cut == -1:
            cut = max_len
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


# ═══════════════════════════════════════════════════════════════════════════
#  /avito <query> — поиск на Авито прямо сейчас
# ═══════════════════════════════════════════════════════════════════════════


@router.message(Command("avito"))
async def cmd_avito(message: Message, command: CommandObject) -> None:
    """Поиск объявлений на Авито по запросу."""
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Использование: <code>/avito запрос</code>\n"
            "Например: <code>/avito iPhone 15 Pro</code>"
        )
        return

    status_msg = await message.answer("🔍 Ищу на Авито…")

    try:
        params = SearchParams(
            city=settings.avito_default_city, category="", query=query
        )
        result: ScanResult = await scan_avito_cached(params)
    except Exception:
        logger.exception("avito scan failed for query=%s", query)
        await status_msg.edit_text("❌ Ошибка при поиске на Авито. Попробуй позже.")
        return

    if result.error:
        await status_msg.edit_text(f"❌ Ошибка: {sanitize_html(result.error)}")
        return

    if not result.listings:
        safe_url = sanitize_html(result.url or "")
        await status_msg.edit_text(
            f"😕 По запросу «<i>{sanitize_html(query)}</i>» ничего не найдено.\n"
            f"Попробуй изменить запрос или проверь URL: {safe_url}"
        )
        return

    # Сортируем по deal_score (от лучшего к худшему)
    sorted_listings = sorted(
        result.listings,
        key=_deal_score_key,
        reverse=True,
    )

    # Статистика
    total = len(result.listings)
    new_count = len(result.new_listings)
    price_changes_count = len(result.price_changes)

    # Топ-5
    top5 = sorted_listings[:5]

    summary_parts = [
        f"🔍 <b>Результаты поиска: «{sanitize_html(query)}»</b>\n",
        f"📊 Всего: <b>{total}</b> объявлений",
    ]
    if new_count:
        summary_parts.append(f"🆕 Новых: <b>{new_count}</b>")
    if price_changes_count:
        summary_parts.append(f"📈 Изменений цены: <b>{price_changes_count}</b>")
    summary_parts.append("")

    summary_parts.append("<b>🏆 Топ-5 лучших сделок:</b>\n")
    for i, listing in enumerate(top5, 1):
        summary_parts.append(_listing_summary(listing, i))
        summary_parts.append("")

    text = "\n".join(summary_parts)

    # Клавиатура
    qh = await _cb_hash(query)
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text="📊 Таблица лучших",
            callback_data=f"avito:table:{qh}",
        ),
        InlineKeyboardButton(
            text="📈 Средняя цена",
            callback_data=f"avito:stats:{qh}",
        ),
    )
    kb.row(
        InlineKeyboardButton(
            text="🔔 Следить",
            callback_data=f"avito:watch:{qh}",
        ),
        InlineKeyboardButton(
            text="📋 Все",
            callback_data=f"avito:all:{qh}",
        ),
    )
    if total > ITEMS_PER_PAGE:
        kb.row(
            InlineKeyboardButton(
                text="Вперёд ▶",
                callback_data=f"avito:page:{qh}:{ITEMS_PER_PAGE}",
            ),
        )

    await status_msg.edit_text(
        text,
        reply_markup=kb.as_markup(),
        disable_web_page_preview=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  /avito_list — список отслеживаемых запросов
# ═══════════════════════════════════════════════════════════════════════════


@router.message(Command("avito_list"))
async def cmd_avito_list(message: Message) -> None:
    """Показывает список отслеживаемых запросов."""
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        stmt = (
            select(AvitoWatch)
            .where(AvitoWatch.user_id == owner.id)
            .options(selectinload(AvitoWatch.listing))
            .order_by(AvitoWatch.created_at.desc())
        )
        watches = (await session.execute(stmt)).scalars().all()

    if not watches:
        await message.answer(
            "📋 Список отслеживания пуст.\n"
            "Используй /avito <code>запрос</code> для поиска, "
            "затем нажми «🔔 Следить»."
        )
        return

    lines = ["📋 <b>Отслеживаемые запросы:</b>\n"]
    for w in watches:
        status = "✅ Активно" if w.is_active else "⏸ Пауза"
        threshold = (
            f" (порог: {_fmt_price(w.price_threshold)})"
            if w.price_threshold is not None
            else ""
        )
        created = w.created_at.strftime("%d.%m.%Y %H:%M") if w.created_at else "?"
        # Получаем search_query из связанного listing
        listing = w.listing if hasattr(w, "listing") and w.listing else None
        query_text = listing.search_query if listing else f"listing_id={w.listing_id}"
        safe_query = sanitize_html(query_text)
        lines.append(
            f"• <b>{safe_query}</b>{threshold}\n"
            f"  {status} | 📅 {created} | ID: <code>{w.id}</code>"
        )

    text = "\n".join(lines)

    # Кнопки для каждого watch
    kb = InlineKeyboardBuilder()
    for w in watches:
        listing = w.listing if hasattr(w, "listing") and w.listing else None
        query_text = listing.search_query if listing else f"#{w.id}"
        safe_btn = sanitize_html(query_text[:30])
        btn_text = f"{'▶️' if w.is_active else '⏸'} {safe_btn}"
        kb.row(
            InlineKeyboardButton(
                text=btn_text,
                callback_data=f"avito:watch_pause:{w.id}",
            ),
            InlineKeyboardButton(
                text="🗑 Удалить",
                callback_data=f"avito:watch_del:{w.id}",
            ),
        )

    await message.answer(
        text, reply_markup=kb.as_markup(), disable_web_page_preview=True
    )


# ═══════════════════════════════════════════════════════════════════════════
#  /avito_remove <id> — удалить отслеживание
# ═══════════════════════════════════════════════════════════════════════════


@router.message(Command("avito_remove"))
async def cmd_avito_remove(message: Message, command: CommandObject) -> None:
    """Удаляет отслеживание по ID."""
    arg = (command.args or "").strip()
    if not arg or not arg.isdigit():
        await message.answer("Использование: <code>/avito_remove ID</code>")
        return

    watch_id = int(arg)
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        stmt = delete(AvitoWatch).where(
            AvitoWatch.id == watch_id,
            AvitoWatch.user_id == owner.id,
        )
        result = await session.execute(stmt)

    if result.rowcount:
        await message.answer(f"✅ Отслеживание #{watch_id} удалено.")
    else:
        await message.answer(f"❌ Отслеживание #{watch_id} не найдено.")


# ═══════════════════════════════════════════════════════════════════════════
#  Callback: avito:table:{query} — полная таблица результатов
# ═══════════════════════════════════════════════════════════════════════════



async def _resolve_avito_query(
    callback: CallbackQuery,
    qh: str,
) -> tuple[str, ScanResult, Message] | None:
    """Разрешает хэш запроса → поиск на Авито. Возвращает (query, result, msg) или None.

    Если возвращён None — колбэк уже обработан (ошибка).
    """
    msg = _callback_message(callback)
    if msg is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return None

    query = await _cb_query(qh)
    if not query:
        await callback.answer("Ошибка данных.", show_alert=True)
        return None

    await callback.answer("Загружаю…")

    try:
        params = SearchParams(
            city=settings.avito_default_city, category="", query=query
        )
        result = await scan_avito_cached(params)
    except Exception:
        logger.exception("avito scan failed for query=%s", query)
        await msg.edit_text("❌ Ошибка загрузки.")
        return None

    if result.error or not result.listings:
        text = f"❌ {sanitize_html(result.error or 'Нет данных')}"
        await msg.edit_text(text)
        return None

    return query, result, msg

@router.callback_query(F.data.startswith("avito:table:"))
async def cb_avito_table(callback: CallbackQuery) -> None:
    """Показывает полную таблицу объявлений, отсортированных по deal_score."""
    qh = callback.data.split(":", 2)[2] if callback.data.count(":") >= 2 else ""
    resolved = await _resolve_avito_query(callback, qh)
    if resolved is None:
        return
    query, result, msg = resolved

    sorted_listings = sorted(
        result.listings,
        key=_deal_score_key,
        reverse=True,
    )

    # Формируем таблицу в monospace
    header = f"{'#':<3} {'Цена':>10} {'Оценка':>5} {'Заголовок':<40}"
    sep = "─" * 62
    rows = [f"<b>📊 Таблица: «{sanitize_html(query)}»</b>\n", f"<pre>{header}\n{sep}"]

    for i, listing in enumerate(sorted_listings[:30], 1):
        price_num = _price_to_int(listing.get("price"))
        price_str = f"{price_num:>10,}" if price_num is not None else "       N/A"
        deal = listing.get("deal_score") or {}
        score = deal.get("score") or 0  # None → 0 fallback
        grade = deal.get("grade") or "?"  # None → "?" fallback
        title = (listing.get("title") or "?")[:40]
        rows.append(f"{i:<3} {price_str} {grade:>3}{score:>2}  {title}")

    rows.append("</pre>")
    rows.append(f"\nВсего: {len(sorted_listings)} объявлений")

    text = "\n".join(rows)
    parts = list(_split_message(text))
    if not parts:
        return
    await msg.edit_text(parts[0], disable_web_page_preview=True)
    for part in parts[1:]:
        await msg.answer(part, disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
#  Callback: avito:stats:{query} — статистика цен
# ═══════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("avito:stats:"))
async def cb_avito_stats(callback: CallbackQuery) -> None:
    """Показывает статистику цен по запросу."""
    qh = callback.data.split(":", 2)[2] if callback.data.count(":") >= 2 else ""
    resolved = await _resolve_avito_query(callback, qh)
    if resolved is None:
        return
    query, result, msg = resolved

    listings = result.listings
    prices = [_price_to_int(item["price"]) for item in listings]
    prices = [p for p in prices if p is not None]

    if not prices:
        await msg.edit_text(
            "📊 Цены не найдены ни в одном объявлении."
        )
        return

    avg_price = sum(prices) / len(prices)
    min_price = min(prices)
    max_price = max(prices)
    median_idx = len(prices) // 2
    sorted_prices = sorted(prices)
    median_price = sorted_prices[median_idx]

    # Разделение на новые и б/у
    new_prices = [
        p
        for item in listings
        if (p := _price_to_int(item.get("price"))) is not None
        and (item.get("condition") or "").lower() in ("новый", "новое")
    ]
    used_prices = [
        p
        for item in listings
        if (p := _price_to_int(item.get("price"))) is not None
        and (item.get("condition") or "").lower() not in ("новый", "новое", "")
    ]

    lines = [
        f"📈 <b>Статистика цен: «{sanitize_html(query)}»</b>\n",
        f"📊 Всего объявлений: <b>{len(listings)}</b>",
        f"💰 Средняя цена: <b>{_fmt_price(int(avg_price))}</b>",
        f"📉 Минимальная: <b>{_fmt_price(min_price)}</b>",
        f"📈 Максимальная: <b>{_fmt_price(max_price)}</b>",
        f"📍 Медиана: <b>{_fmt_price(median_price)}</b>",
    ]

    if new_prices:
        new_avg = sum(new_prices) / len(new_prices)
        lines.append(
            f"\n🆕 Новые ({len(new_prices)} шт): средняя <b>{_fmt_price(int(new_avg))}</b>"
        )
    if used_prices:
        used_avg = sum(used_prices) / len(used_prices)
        lines.append(
            f"📦 Б/У ({len(used_prices)} шт): средняя <b>{_fmt_price(int(used_avg))}</b>"
        )

    text = "\n".join(lines)
    await msg.edit_text(text, disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
#  Callback: avito:watch:{query} — добавить в отслеживание
# ═══════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("avito:watch:"))
async def cb_avito_watch(callback: CallbackQuery) -> None:
    """Добавляет запрос в список отслеживания."""
    qh = callback.data.split(":", 2)[2] if callback.data.count(":") >= 2 else ""
    resolved = await _resolve_avito_query(callback, qh)
    if resolved is None:
        return
    query, result, msg = resolved

    # Сохраняем лучшее объявление как привязку к watch
    best = max(
        result.listings,
        key=_deal_score_key,
    )

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)

        # Upsert listing
        avito_id = best.get("avito_id", "") or ""
        if not avito_id:
            url = best.get("url", "") or ""
            # include query in fallback to prevent collision when both
            # avito_id and url are empty across different watches
            fallback_src = f"{url}|{query}"
            avito_id = (
                "fallback_" + hashlib.sha1(fallback_src.encode()).hexdigest()[:16]
            )
        stmt = select(AvitoListing).where(
            AvitoListing.user_id == owner.id,
            AvitoListing.avito_id == avito_id,
        )
        listing_obj = (await session.execute(stmt)).scalar_one_or_none()

        if listing_obj is None:
            listing_obj = AvitoListing(
                user_id=owner.id,
                avito_id=avito_id,
                search_query=query,
                title=best.get("title", ""),
                price=best.get("price"),
                url=best.get("url", ""),
                image_url=best.get("image_url"),
                city=best.get("city"),
                condition=best.get("condition"),
                delivery=best.get("delivery", False),
                seller_name=best.get("seller_name"),
                seller_rating=best.get("seller_rating"),
                seller_reviews=best.get("seller_reviews"),
                description=best.get("description"),
                deal_score=(best.get("deal_score") or {}).get("score"),
                is_suspicious=(best.get("scam_check") or {}).get(
                    "is_suspicious", False
                ),
                scam_reasons="; ".join(
                    (best.get("scam_check") or {}).get("reasons", [])
                ),
            )
            session.add(listing_obj)
            await session.flush()

        # Проверяем, нет ли уже watch
        watch_stmt = select(AvitoWatch).where(
            AvitoWatch.user_id == owner.id,
            AvitoWatch.listing_id == listing_obj.id,
        )
        existing_watch = (await session.execute(watch_stmt)).scalar_one_or_none()

        if existing_watch:
            await msg.edit_text(
                f"ℹ️ «<i>{sanitize_html(query)}</i>» уже отслеживается (ID: <code>{existing_watch.id}</code>)."
            )
            return

        watch = AvitoWatch(
            user_id=owner.id,
            listing_id=listing_obj.id,
            is_active=True,
        )
        session.add(watch)

    await msg.edit_text(
        f"🔔 Отслеживание добавлено!\n\n"
        f"📌 <b>{sanitize_html(query)}</b>\n"
        f"💰 Лучшая цена: {_fmt_price(best.get('price'))}\n\n"
        f"Используй /avito_list для управления."
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Callback: avito:watch_pause:{id} — пауза/возобновление
# ═══════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("avito:watch_pause:"))
async def cb_avito_watch_pause(callback: CallbackQuery) -> None:
    """Переключает активность отслеживания."""
    msg = _callback_message(callback)
    if msg is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        watch_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверный ID.", show_alert=True)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        stmt = select(AvitoWatch).where(
            AvitoWatch.id == watch_id,
            AvitoWatch.user_id == owner.id,
        )
        watch = (await session.execute(stmt)).scalar_one_or_none()

        if watch is None:
            await callback.answer("Отслеживание не найдено.", show_alert=True)
            return

        new_state = not watch.is_active
        await session.execute(
            update(AvitoWatch)
            .where(AvitoWatch.id == watch_id)
            .values(is_active=new_state)
        )

    status = "▶️ Возобновлено" if new_state else "⏸ На паузе"
    await callback.answer(status)
    await msg.edit_text(f"{status} (ID: <code>{watch_id}</code>)")


# ═══════════════════════════════════════════════════════════════════════════
#  Callback: avito:watch_del:{id} — удалить отслеживание
# ═══════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("avito:watch_del:"))
async def cb_avito_watch_del(callback: CallbackQuery) -> None:
    """Удаляет отслеживание."""
    msg = _callback_message(callback)
    if msg is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        watch_id = int(parts[2])
    except ValueError:
        await callback.answer("Неверный ID.", show_alert=True)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        stmt = delete(AvitoWatch).where(
            AvitoWatch.id == watch_id,
            AvitoWatch.user_id == owner.id,
        )
        result = await session.execute(stmt)

    if result.rowcount:
        await callback.answer("🗑 Удалено")
        await msg.edit_text(f"🗑 Отслеживание #{watch_id} удалено.")
    else:
        await callback.answer("Не найдено.", show_alert=True)


# ═══════════════════════════════════════════════════════════════════════════
#  Callback: avito:all:{query} — все результаты
# ═══════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("avito:all:"))
async def cb_avito_all(callback: CallbackQuery) -> None:
    """Показывает все найденные объявления."""
    qh = callback.data.split(":", 2)[2] if callback.data.count(":") >= 2 else ""
    resolved = await _resolve_avito_query(callback, qh)
    if resolved is None:
        return
    query, result, msg = resolved

    sorted_listings = sorted(
        result.listings,
        key=_deal_score_key,
        reverse=True,
    )

    lines = [f"📋 <b>Все объявления: «{sanitize_html(query)}»</b>\n"]
    for i, listing in enumerate(sorted_listings, 1):
        lines.append(_listing_summary(listing, i))
        lines.append("")

    lines.append(f"Всего: {len(sorted_listings)} объявлений")

    text = "\n".join(lines)
    for part in _split_message(text):
        await msg.answer(part, disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
#  Callback: avito:page:{qh}:{offset} — постраничный просмотр
# ═══════════════════════════════════════════════════════════════════════════


@router.callback_query(F.data.startswith("avito:page:"))
async def cb_avito_page(callback: CallbackQuery) -> None:
    """Постраничный просмотр всех результатов."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    qh = parts[2]
    try:
        offset = int(parts[3])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return
    if offset < 0:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    resolved = await _resolve_avito_query(callback, qh)
    if resolved is None:
        return
    query, result, msg = resolved

    sorted_listings = sorted(
        result.listings,
        key=_deal_score_key,
        reverse=True,
    )

    total = len(sorted_listings)
    if offset >= total:
        offset = max(0, total - ITEMS_PER_PAGE)
    page_num = offset // ITEMS_PER_PAGE + 1
    page_items = sorted_listings[offset : offset + ITEMS_PER_PAGE]

    lines = [
        f"📋 <b>Результаты: «{sanitize_html(query)}»</b>  (стр. {page_num})\n",
    ]
    for i, listing in enumerate(page_items, offset + 1):
        lines.append(_listing_summary(listing, i))
        lines.append("")

    end_idx = min(offset + ITEMS_PER_PAGE, total)
    lines.append(f"Показано {offset + 1}–{end_idx} из {total}")

    text = "\n".join(lines)

    # Навигационные кнопки
    kb = InlineKeyboardBuilder()
    if offset > 0:
        kb.add(
            InlineKeyboardButton(
                text="◀ Назад",
                callback_data=f"avito:page:{qh}:{offset - ITEMS_PER_PAGE}",
            ),
        )
    if offset + ITEMS_PER_PAGE < total:
        kb.add(
            InlineKeyboardButton(
                text="Вперед ▶",
                callback_data=f"avito:page:{qh}:{offset + ITEMS_PER_PAGE}",
            ),
        )
    kb.adjust(2)
    await msg.edit_text(text, reply_markup=kb.as_markup())
    await callback.answer()
