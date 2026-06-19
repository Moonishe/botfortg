"""Singalong (подпевание) — extracted from free_text_legacy.py.

Previously nested closures are now module-level functions with explicit parameters.
"""

import logging
import time

from aiogram.types import Message

from src.core.cache import ManagedCache, cache_manager
from src.core.infra.text_sanitizer import sanitize_html
from src.core.intelligence.singalong import (
    _is_confirmation,
    _looks_like_lyrics,
    _search_lyrics,
    consume_pending_singalong,
    get_singalong_reply,
    identify_and_get_next_line,
    peek_pending_singalong,
    store_pending_singalong,
)
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.llm.base import TaskType
from src.llm.router import build_provider

logger = logging.getLogger(__name__)

# ── Singalong search cache ───────────────────────────────────────────────

_singalong_search_cache: ManagedCache[int, list | None] = cache_manager.register(
    ManagedCache(name="singalong_search", max_size=100, default_ttl=300)
)


async def _pop_singalong_search(key: int) -> list | None:
    """Atomically get and remove from singalong search cache."""
    val = await _singalong_search_cache.get(key)
    if val is not None:
        await _singalong_search_cache.invalidate(key)
    return val


# ── Singalong helpers ───────────────────────────────────────────────────


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
    message: Message,
    owner_telegram_id: int,
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


async def _try_singalong(
    raw: str,
    message: Message,
    owner_telegram_id: int,
    use_heavy: bool,
    turn_started: float,
) -> bool:
    """Stage 0e: попробовать обработать как текст песни. Возвращает True если сообщение consumed."""
    # Lazy import to avoid circular dependency
    from src.bot.handlers.free_text_common import _fire_record_trajectory

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
                await _ask_singalong_confirmation(
                    raw, identified, message, owner_telegram_id
                )
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
                    await message.answer(sanitize_html(text))

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
                            latency_ms=int((time.monotonic() - turn_started) * 1000),
                        )
                        return True
                    # LLM не смог — спрашиваем уточнение
                    await consume_pending_singalong(owner_telegram_id)
                    await _singalong_search_cache.invalidate(owner_telegram_id)
                    await message.answer(
                        sanitize_html(
                            f"Не могу найти текст «{title}». Напиши название песни?"
                        )
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
            identified = await _singalong_identify(raw, owner_telegram_id, use_heavy)
            await _ask_singalong_confirmation(
                raw, identified, message, owner_telegram_id
            )
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
