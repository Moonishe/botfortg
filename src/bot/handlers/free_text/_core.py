"""Pipeline stages for _process_text — extracted from free_text.py.

Architecture note: pipeline stages are intentionally sequential. Each stage depends
on the previous one's result (pre-gate → followup → persona → contact rules →
instructions → routing → dispatch). Parallelization is at the tool-execution level
(maestro, agent runtime, DAG dispatch for multi-intent).
"""

from __future__ import annotations

from src.core.security.prompt_guard import scrub_internal_tags

import asyncio
import json
import logging
import random
import sys
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.dispatcher import MaestroResult

# ── Module constants ─────────────────────────────────────────────────────
_DEFAULT_SEARCH_LIMIT = 5  # элементов — лимит deep recall по умолчанию
_DEFAULT_CONTACT_LIMIT = 3  # контактов — лимит разрешения контакта

# ── Progress messages — индикаторы этапов пайплайна ────────────────────
_PROGRESS_MESSAGES: dict[str, str] = {
    "classifying": "🔍 Анализирую запрос…",
    "recalling": "🧠 Ищу в памяти…",
    "planning": "📋 Составляю план…",
    "researching": "🌐 Ищу информацию…",
    "generating": "✍️ Формирую ответ…",
}

# Порог показа прогресса — только если этап длится дольше (секунды)
_PROGRESS_MIN_DURATION = 0.5

from httpx import RequestError, HTTPStatusError

from src.config import settings

from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError

from src.core.actions.action_guard import guard_intent
from src.core.actions.trajectory import actions_from_intent
from src.core.infra.key_guard import safe_str
from src.core.infra.task_manager import track_ff
from src.core.infra.text_sanitizer import sanitize_html
from src.core.intelligence.agent import route_intent
from src.core.intelligence.guardrails import evaluate as guardrail_evaluate
from src.core.intelligence.maestro import run_pipeline
from src.core.memory import conversation_context as ctx_store
from src.core.memory.memory_recall import recall
from src.core.memory.auto_save_batch import auto_save_single
from src.core.memory.memory_service import save_memories_batch
from sqlalchemy.exc import SQLAlchemyError

from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.userbot.manager import UserbotManager

from src.core.intelligence.pre_gate import check_pre_gate
from src.llm.base import ChatMessage, TaskType
from src.core.infra.timeutil import now_in_tz
from src.core.observability.response_trace import log_response_trace

from src.bot.handlers.free_text_common import (
    _fire_record_trajectory,
    _post_turn_optimize,
    _summarize_intent_for_memory,
    h_adapter,
    ht_adapter,
    hu_adapter,
    memory_quick_keyboard,
    safe_answer,
)
from src.core.intelligence.routing_wordlists import learn_routing as _learn_routing

from src.core.humanizer import (
    apply_anti_ai_mode,
    humanize_deep,
    analyze_ai_score,
    _cache_last_humanized,
    _preservation_check,
    normalize_anti_ai_mode,
)

from src.bot.handlers.free_text_exec import (
    exec_add_api_key,
    exec_add_news_topic,
    exec_add_reminder,
    exec_add_reminders_from_chat,
    exec_change_auto_mode,
    exec_check_memories,
    exec_classic_ask_chat,
    exec_classic_catchup,
    exec_classic_chat,
    exec_classic_draft_reply,
    exec_classic_find_in_chats,
    exec_classic_list_todos,
    exec_classic_news_digest,
    exec_classic_search,
    exec_classic_send_message,
    exec_classic_summarize_chat,
    exec_classic_tasks_for_chat,
    exec_classic_unknown,
    exec_clarify,
    exec_extract_memories,
    exec_forget_memory,
    exec_full_analysis,
    exec_index_chats,
    exec_list_keys,
    exec_list_memories,
    exec_remove_api_key,
    exec_remove_news_topic,
    exec_remove_reminder,
    exec_set_quiet_hours,
    exec_set_setting,
    exec_show_digest,
    exec_show_inbox,
    exec_show_profile,
    exec_show_self,
    exec_show_skills,
    exec_show_style,
    exec_show_threads,
    exec_show_today,
    exec_show_trajectory,
    exec_link_memories,
    exec_show_memory_graph,
    exec_show_memory_health,
    exec_show_sessions,
    exec_show_suggestions,
    exec_store_memory,
    exec_toggle_api_key,
    exec_update_memory,
)
from src.bot.handlers.cron_exec import exec_cron_delete, exec_cron_run

logger = logging.getLogger(__name__)


# ── Smart Model Routing: вспомогательная функция логирования ──────────


def _log_smart_routing(plan, raw: str) -> None:
    """Логирует решение SmartModelRouter если оно принято в плане."""
    if not plan or not getattr(plan, "model_mode", None):
        return
    try:
        from src.config import settings

        if settings.smart_routing_enabled:
            logger.debug(
                "SmartModelRouting: mode=%s text=%.60s…",
                plan.model_mode,
                raw,
            )
    except Exception:
        logger.debug("Smart routing log failed", exc_info=True)


# ── Follow-up context ────────────────────────────────────────────────

from src.core.cache import ManagedCache, cache_manager

from src.bot.handlers.free_text._shared import _LAST_INTENT_TTL
from src.bot.handlers.free_text._confirm import (
    _confirm_tool_keyboard,
    _store_intent_confirmation,
    _store_tool_confirmation,
)

_last_intent_ctx: ManagedCache[int, dict] = cache_manager.register(
    ManagedCache(name="last_intent_ctx", max_size=1000, default_ttl=_LAST_INTENT_TTL)
)


# _get_tool_confirm_lock and _cleanup_tool_confirm_locks moved to _confirm.py
async def _get_anti_ai_mode(owner_telegram_id: int) -> str:
    """Runtime mode for assistant responses: off/log/fix."""
    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_telegram_id)
            user_settings = getattr(owner, "settings", None)
            mode = getattr(user_settings, "anti_ai_mode", None)
            enabled = getattr(user_settings, "anti_ai_enabled", None)
            return normalize_anti_ai_mode(mode, enabled=enabled)
    except SQLAlchemyError:
        logger.debug("failed to load anti_ai_mode", exc_info=True)
        return "off"


async def _humanize_assistant_response(
    text: str,
    *,
    owner_telegram_id: int,
    context_hint: str | None,
    style_profile: str = "",
    source: str,
    mode: str | None = None,
) -> str:
    mode = mode or await _get_anti_ai_mode(owner_telegram_id)
    return await apply_anti_ai_mode(
        text,
        mode=mode,
        context_hint=context_hint,
        style_profile=style_profile,
        user_id=owner_telegram_id,
        source=source,
    )


# Confirmation and cleanup code moved to _confirm.py
_APPEND_KEYWORDS = ("добавь", "и ещё", "также", "кстати", "плюс", "ещё", "а ещё")
_REPLACE_KEYWORDS = ("нет", "лучше", "вместо", "точнее", "не так", "исправь", "поменяй")
_MULTI_KEYWORDS = ("и не забудь", "заодно", "и ещё")


# ── Auto-save facts about user ───────────────────────────────────────


# Dedup cache moved to _dag.py
async def _extract_entities_ff(
    telegram_id: int,
    fact_texts: list[str],
    provider,
) -> None:
    """Fire-and-forget: извлечь сущности и связи из фактов.

    Не блокирует основной поток. Ошибки логируются, не пробрасываются.
    """
    try:
        from src.core.memory.entity_extractor import extract_and_save_entities

        await extract_and_save_entities(telegram_id, fact_texts, provider=provider)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug(
            "Entity extraction fire-and-forget failed for user %d",
            telegram_id,
            exc_info=True,
        )


async def _save_extracted_facts(
    facts_list: list[dict],
    telegram_id: int,
) -> int:
    """Сохраняет список уже извлечённых фактов в память (без LLM-вызова).

    Используется для кэшированных результатов smart_extractor.
    Возвращает количество сохранённых фактов.
    """
    stored = await save_memories_batch(telegram_id, facts_list, source="auto")
    if stored:
        logger.info(
            "Auto-saved %d cached facts for user %d",
            stored,
            telegram_id,
        )
    return stored


async def _maybe_auto_save_facts(
    user_text: str,
    response_text: str,
    telegram_id: int,
    provider,
) -> None:
    """Fire-and-forget: LLM извлекает личные факты → сохраняет в память.

    Оптимизировано через SmartExtractor:
      - Пропуск тривиальных сообщений (classifier)
      - Кэширование результатов извлечения
      - Scoring приоритетности
      - Выбор лёгкой/тяжёлой модели
    """
    # ── Dedup: skip if we already processed this (user, text) recently ──
    # Lazy import to avoid circular dependency with _dag.py
    from src.bot.handlers.free_text._dag import _should_skip_auto_save

    if await _should_skip_auto_save(telegram_id, user_text):
        logger.debug(
            "Auto-save facts DEDUP: skip duplicate for user %d (text_len=%d)",
            telegram_id,
            len(user_text),
        )
        return

    # ── Оптимизация: SmartExtractor решает, извлекать ли ──
    try:
        from src.config import settings

        if getattr(settings, "smart_extract_optimized", True):
            from src.core.memory.smart_extractor import (
                make_extract_decision,
            )

            decision = await make_extract_decision(user_text, user_id=telegram_id)

            # Быстрый пропуск
            if not decision.should_extract:
                logger.debug(
                    "SmartExtractor SKIP: %s (score=%.2f)",
                    decision.reason,
                    decision.score,
                )
                return

            # Возвращаем кэшированный результат
            if decision.cached_result is not None:
                logger.debug(
                    "SmartExtractor CACHE HIT: %d facts reused",
                    len(decision.cached_result),
                )
                await _save_extracted_facts(
                    decision.cached_result,
                    telegram_id,
                )
                return

            # Определяем модель для извлечения
            use_light = decision.model_mode == "light"
            logger.debug(
                "SmartExtractor EXTRACT: priority=%s model=%s score=%.2f",
                decision.priority.name,
                decision.model_mode,
                decision.score,
            )
        else:
            use_light = False
            decision = None
    except Exception:
        logger.debug(
            "SmartExtractor failed, falling back to basic check", exc_info=True
        )
        use_light = False
        decision = None

    # Quick pre-check: skip if message is clearly not personal.
    # This legacy check runs ONLY as fallback when SmartExtractor is disabled
    # or failed. When SmartExtractor already decided should_extract=True,
    # we trust its decision and skip this check entirely.
    if decision is None:
        text_lower = user_text.lower()
        if not any(
            kw in text_lower
            for kw in (
                "я ",
                "мой ",
                "моя ",
                "моё ",
                "мои ",
                "мне ",
                "меня ",
                "у меня",
                "день рождения",
                "др ",
                "работаю",
                "учусь",
                "живу",
                "люблю",
                "нравится",
                "хочу",
                "планирую",
                "собираюсь",
                "занимаюсь",
            )
        ):
            return

    async def _do_save():
        # ── Батчевый режим: накопление сообщений + сброс единым LLM-запросом ──
        try:
            from src.core.memory.auto_save_batch import get_batch_buffer

            batch_buffer = await get_batch_buffer()
        except Exception:
            logger.error(
                "Failed to init batch buffer, falling back to single mode",
                exc_info=True,
            )
            batch_buffer = None

        if batch_buffer is not None and batch_buffer.enabled:
            # Батчевый режим: добавить в буфер (flush — внутри, fire-and-forget)
            try:
                await batch_buffer.add(telegram_id, user_text, response_text, provider)
            except asyncio.CancelledError:
                # L3: НЕ глотаем CancelledError — даём ему прокинуться вверх,
                # чтобы asyncio мог корректно отменить цепочку задач.
                # Раньше здесь было `pass`, что приводило к «тихой отмене» —
                # задача отменялась, но родительский код об этом не узнавал.
                raise
            except (
                RequestError,
                HTTPStatusError,
                SQLAlchemyError,
                json.JSONDecodeError,
            ):
                logger.debug("Auto-save facts skipped", exc_info=True)
            return

        # ── Одиночный режим: немедленный LLM-вызов + сохранение ──
        stored, facts_list = await auto_save_single(
            telegram_id, user_text, response_text, provider
        )
        if not stored:
            return

        # Logging is done inside auto_save_single / _save_facts_to_db;
        # avoid duplicating PII-laden detail logs here.
        # ── Кэшируем результат извлечения ──
        try:
            from src.config import settings

            if getattr(settings, "smart_extract_optimized", True):
                from src.core.memory.smart_extractor import (
                    cache_extraction_result,
                )

                model_mode = "light" if use_light else "heavy"
                await cache_extraction_result(
                    user_text,
                    facts_list,
                    model_mode=model_mode,
                    user_id=telegram_id,
                )
        except Exception:
            logger.debug("Failed to cache extraction result", exc_info=True)

        # ── Сущности: fire-and-forget ──
        fact_texts = [
            f.get("fact", "").strip() for f in facts_list if f.get("fact", "").strip()
        ]
        if fact_texts:
            track_ff(
                asyncio.create_task(
                    _extract_entities_ff(telegram_id, fact_texts, provider)
                )
            )

    track_ff(asyncio.create_task(_do_save()))


async def _save_intent_context(tg_id: int, intent: dict) -> None:
    await _last_intent_ctx.set(tg_id, {"intent": intent})


async def _detect_followup(raw: str, tg_id: int) -> tuple[dict, str] | None:
    """Если raw — продолжение предыдущего intent'а, вернуть (модифицированный intent, update_type).
    update_type: "append", "replace", "multi_add". Возвращает None если не продолжение."""
    entry = await _last_intent_ctx.get(tg_id)
    if not entry:
        return None
    prev = entry["intent"]
    stripped = raw.strip().lower()
    words = stripped.split()[:3]
    first3 = " ".join(words)

    for kw in _REPLACE_KEYWORDS:
        if first3.startswith(kw):
            new_text = raw.strip()
            for kw2 in _REPLACE_KEYWORDS:
                if new_text.lower().startswith(kw2):
                    new_text = new_text[len(kw2) :].strip(", ").strip()
                    break
            modified = dict(prev)
            if "text" in modified:
                modified["text"] = new_text
            elif "query" in modified:
                modified["query"] = new_text
            return (modified, "replace")

    for kw in _APPEND_KEYWORDS:
        if first3.startswith(kw):
            new_text = raw.strip()
            for kw2 in _APPEND_KEYWORDS:
                if new_text.lower().startswith(kw2):
                    new_text = new_text[len(kw2) :].strip(", ").strip()
                    break
            modified = dict(prev)
            if "text" in modified:
                modified["text"] = modified.get("text", "") + " " + new_text
            elif "query" in modified:
                modified["query"] = modified.get("query", "") + " " + new_text
            return (modified, "append")

    for kw in _MULTI_KEYWORDS:
        if first3.startswith(kw):
            new_text = raw.strip()
            for kw2 in _MULTI_KEYWORDS:
                if new_text.lower().startswith(kw2):
                    new_text = new_text[len(kw2) :].strip(", ").strip()
                    break
            new_intent = {
                "intent": prev.get("intent", "chat"),
                "text": new_text,
            }
            return (new_intent, "multi_add")

    return None


async def _execute_intent(
    intent, message, state, userbot_manager, *, tz_name: str
) -> None:
    if not isinstance(intent, dict):
        logger.error(
            "_execute_intent called with non-dict intent: %r (type=%s)",
            intent,
            type(intent).__name__,
        )
        await message.answer("⚠️ Internal routing error (invalid intent).")
        return
    # Strip any LLM-injected confirmation bypass; _confirmed is only legal
    # when injected by the verified callback path in _cb_tool_confirm.
    intent = intent.copy()
    intent.pop("_confirmed", None)
    kind = intent.get("intent")
    if not isinstance(kind, str):
        logger.error("_execute_intent: missing or invalid 'intent' key: %r", kind)
        await message.answer("⚠️ Internal routing error (invalid intent key).")
        return
    handler_info = CLASSIC_INTENT_HANDLERS.get(kind)
    if handler_info is not None:
        handler, _ = handler_info
        if handler is None:
            logger.error(
                "Intent handler for %r returned None callable — "
                "CLASSIC_INTENT_HANDLERS entry is corrupted",
                kind,
            )
            await message.answer("⚠️ Internal routing error (missing handler).")
            return
        await handler(intent, message, state, userbot_manager, tz_name=tz_name)
        return
    await message.answer("❓ Неизвестный intent.")


# ── Intent handler registries ────────────────────────────────────────


INTENT_HANDLERS: dict[str, tuple[Callable, str]] = {
    "set_setting": (h_adapter(exec_set_setting), "Изменить настройку"),
    "cron_run": (h_adapter(exec_cron_run), "Запустить cron-задачу"),
    "cron_delete": (h_adapter(exec_cron_delete), "Удалить cron-задачу"),
    "add_news_topic": (h_adapter(exec_add_news_topic), "Добавить новостную тему"),
    "remove_news_topic": (h_adapter(exec_remove_news_topic), "Удалить новостную тему"),
    "add_reminder": (ht_adapter(exec_add_reminder), "Добавить напоминание"),
    "remove_reminder": (h_adapter(exec_remove_reminder), "Удалить напоминание"),
    "add_reminders_from_chat": (
        hu_adapter(exec_add_reminders_from_chat),
        "Извлечь напоминания из чата",
    ),
    "store_memory": (h_adapter(exec_store_memory), "Сохранить в память"),
    "forget_memory": (h_adapter(exec_forget_memory), "Удалить из памяти"),
    "list_memories": (h_adapter(exec_list_memories), "Показать память"),
    "extract_memories_from_chat": (
        hu_adapter(exec_extract_memories),
        "Извлечь воспоминания из чата",
    ),
    "check_memories": (h_adapter(exec_check_memories), "Проверить память"),
    "update_memory": (h_adapter(exec_update_memory), "Обновить факт в памяти"),
    "link_memories": (h_adapter(exec_link_memories), "Связать два факта"),
    "show_memory_health": (h_adapter(exec_show_memory_health), "Здоровье памяти"),
    "show_memory_graph": (h_adapter(exec_show_memory_graph), "Граф памяти"),
    "show_sessions": (h_adapter(exec_show_sessions), "История сессий"),
    "show_suggestions": (h_adapter(exec_show_suggestions), "Паттерны памяти"),
    "change_auto_mode": (h_adapter(exec_change_auto_mode), "Сменить авто-режим"),
    "set_quiet_hours": (h_adapter(exec_set_quiet_hours), "Установить тихие часы"),
    "show_inbox": (hu_adapter(exec_show_inbox), "Показать входящие"),
    "show_self": (h_adapter(exec_show_self), "Показать свой профиль"),
    "full_analysis": (h_adapter(exec_full_analysis), "Полный анализ"),
    "add_api_key": (h_adapter(exec_add_api_key), "Добавить API-ключ"),
    "remove_api_key": (h_adapter(exec_remove_api_key), "Удалить API-ключ"),
    "toggle_api_key": (h_adapter(exec_toggle_api_key), "Включить/выключить ключ"),
    "list_keys": (h_adapter(exec_list_keys), "Показать ключи"),
    "show_digest": (h_adapter(exec_show_digest), "Показать дайджест"),
    "show_today": (h_adapter(exec_show_today), "Показать сегодня"),
    "show_skills": (h_adapter(exec_show_skills), "Показать навыки"),
    "show_threads": (h_adapter(exec_show_threads), "Показать треды"),
    "show_trajectory": (h_adapter(exec_show_trajectory), "Показать траекторию"),
    "show_style": (h_adapter(exec_show_style), "Показать стиль"),
    "show_profile": (h_adapter(exec_show_profile), "Показать профиль"),
    "index_chats": (h_adapter(exec_index_chats), "Переиндексировать чаты"),
    "clarify": (h_adapter(exec_clarify), "Уточнить"),
}

CLASSIC_INTENT_HANDLERS: dict[str, tuple[Callable, str]] = {
    "chat": (exec_classic_chat, "Чат"),
    "unknown": (exec_classic_unknown, "Неизвестный"),
    "list_todos": (exec_classic_list_todos, "Список задач"),
    "send_message": (exec_classic_send_message, "Отправить сообщение"),
    "search": (exec_classic_search, "Поиск"),
    "find_in_chats": (exec_classic_find_in_chats, "Поиск по чатам"),
    "news_digest": (exec_classic_news_digest, "Новостной дайджест"),
    "ask_chat": (exec_classic_ask_chat, "Анализ чата"),
    "summarize_chat": (exec_classic_summarize_chat, "Саммари чата"),
    "tasks_for_chat": (exec_classic_tasks_for_chat, "Задачи из чата"),
    "draft_reply": (exec_classic_draft_reply, "Черновик ответа"),
    "catchup": (exec_classic_catchup, "Где остановились"),
}


# _dag_dispatch and _run_dag_level moved to _dag.py
async def _dispatch(intent, message, state, userbot_manager, *, tz_name: str) -> None:
    if not isinstance(intent, dict):
        logger.error(
            "_dispatch called with non-dict intent: %r (type=%s)",
            intent,
            type(intent).__name__,
        )
        await message.answer("⚠️ Internal routing error (invalid intent).")
        return
    guard = guard_intent(intent)
    if not guard.allowed:
        _fire_record_trajectory(
            message.from_user.id,
            request_text=message.text or "",
            route_mode="dispatch_guard",
            intent_json=intent if isinstance(intent, dict) else None,
            actions_json=actions_from_intent(
                intent if isinstance(intent, dict) else None
            ),
            success=False,
            error=guard.reason,
        )
        await message.answer(
            sanitize_html(f"⚠️ Действие остановлено guardrail: {guard.reason}")
        )
        return
    intent = guard.intent
    kind = intent.get("intent")
    if not isinstance(kind, str):
        logger.error("_dispatch: invalid intent key: %r", kind)
        await message.answer("⚠️ Internal routing error (invalid intent).")
        return

    # Strip any LLM-injected confirmation bypass before dispatch.
    # The verified callback path in _cb_tool_confirm re-adds _confirmed.
    intent = intent.copy()
    intent.pop("_confirmed", None)

    # ── Risk-based guardrail check for HIGH/CRITICAL actions ────────
    if kind:
        gr = guardrail_evaluate(kind, intent, context={"is_new_contact": False})
        if gr.needs_confirm:
            intent_with_tz = dict(intent)
            intent_with_tz["tz_name"] = tz_name
            confirm_cb, cancel_cb = await _store_intent_confirmation(
                message.from_user.id, kind, intent_with_tz
            )
            await safe_answer(
                message,
                sanitize_html(f"🤔 {gr.confirm_message}"),
                reply_markup=_confirm_tool_keyboard(confirm_cb, cancel_cb),
            )
            _fire_record_trajectory(
                message.from_user.id,
                request_text=message.text or "",
                route_mode="dispatch_guard_confirm",
                intent_json=intent,
                actions_json=actions_from_intent(intent),
                success=True,
                error=None,
            )
            return

    handler_info = INTENT_HANDLERS.get(kind)
    if handler_info is not None:
        handler, _ = handler_info
        await handler(intent, message, state, userbot_manager, tz_name=tz_name)
        # Record action for smart correction
        try:
            from src.bot.handlers.smart_correction import record_action

            await record_action(
                message.from_user.id,
                {
                    "intent": kind,
                    "params": dict(intent),
                },
            )
        except Exception:
            logger.debug("record_action failed for %s", kind, exc_info=True)
        return
    await _execute_intent(intent, message, state, userbot_manager, tz_name=tz_name)
    # Record action for smart correction (classic intents)
    try:
        from src.bot.handlers.smart_correction import record_action

        await record_action(
            message.from_user.id,
            {
                "intent": kind,
                "params": dict(intent),
            },
        )
    except Exception:
        logger.debug("record_action failed for classic %s", kind, exc_info=True)


# ── Pipeline stages ──────────────────────────────────────────────────


async def check_instructions(
    raw: str, owner_telegram_id: int, message: Message
) -> bool:
    """Проверяет adaptive instructions. Возвращает True если обработано (early return)."""
    try:
        from src.core.intelligence.adaptive_instructions import (
            detect_instruction,
            apply_instruction,
        )

        instr = await detect_instruction(raw, owner_telegram_id)
        if instr:
            from src.db.models import InstructionCandidate, InstructionEvent

            async with get_session() as session:
                owner_db = await get_or_create_user(session, owner_telegram_id)
                event = InstructionEvent(
                    user_id=owner_db.id,
                    raw_text=raw[:500],
                    detected_rule=instr["rule"],
                    action=instr["action"],
                )
                session.add(event)
                if instr["is_safe"]:
                    await apply_instruction(owner_telegram_id, instr["rule"])
                    await session.flush()
                    await message.answer(
                        sanitize_html(f"✅ Понял! Больше не буду {instr['rule']}.")
                    )
                    return True
                else:
                    candidate = InstructionCandidate(
                        user_id=owner_db.id,
                        rule=instr["rule"],
                        category=instr["category"],
                        is_safe=False,
                        llm_reviewed=False,
                    )
                    session.add(candidate)
                    await session.flush()
                    await message.answer(
                        sanitize_html(
                            f"🤔 Понял: «{instr['rule']}». Применить это правило? (да/нет)"
                        )
                    )
                    return True
    except (
        SQLAlchemyError,
        TelegramAPIError,
        RequestError,
        HTTPStatusError,
        Exception,
    ):
        logger.exception("adaptive instruction check failed")
    return False


async def check_contact_rules(
    raw: str,
    owner_telegram_id: int,
    message: Message,
    userbot_manager: UserbotManager,
) -> bool:
    """Проверяет per-contact правила (например, «с Олей будь вежливее»).

    Возвращает True если обработано (early return).
    """
    try:
        from src.core.intelligence.adaptive_instructions import detect_contact_rule

        detected = await detect_contact_rule(raw)
        if not detected:
            return False

        contact_name = detected["contact_name"]
        rule_text = detected["rule"]

        # Разрешаем контакт по имени
        from src.bot.contact_resolver import resolve_contact_fast
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        contact = None
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_telegram_id)
            client = userbot_manager.get_client(owner_telegram_id)
            if client is None:
                logger.warning(
                    "check_contact_rules: no telethon client for user %s",
                    owner_telegram_id,
                )
                return False

            candidates = await resolve_contact_fast(
                client, owner, contact_name, limit=_DEFAULT_CONTACT_LIMIT, min_score=60
            )
            if candidates and candidates[0].score >= 60:
                contact = candidates[0]

        if not contact:
            await message.answer(
                sanitize_html(
                    f"🤔 Не могу найти контакт «{contact_name}» в твоей телефонной книге."
                )
            )
            return True

        # Сохраняем правило
        from src.core.contacts.contact_rules import add_contact_rule

        ok = await add_contact_rule(owner_telegram_id, contact.peer_id, rule_text)
        if ok:
            await message.answer(
                sanitize_html(
                    f"✅ Понял! Для контакта {contact.label()} буду соблюдать правило: «{rule_text}»."
                )
            )
        else:
            await message.answer(
                sanitize_html(f"⚠️ Не удалось сохранить правило для {contact.label()}.")
            )
        return True
    except (SQLAlchemyError, TelegramAPIError, RequestError, HTTPStatusError):
        logger.exception("check_contact_rules failed")
        return False


async def check_persona(raw: str, owner_telegram_id: int, message: Message) -> bool:
    """Проверяет adaptive persona.

    Два режима:
    1. Явная команда (detect_persona_change) — блокирует дальнейшую обработку,
       бот подтверждает изменение.
    2. Авто-адаптация (auto_adapt_from_context) — НЕ блокирует, работает тихо
       в фоне: анализирует настроение и плавно корректирует стиль.
    """
    try:
        from src.core.intelligence.adaptive_persona import (
            detect_persona_change,
            apply_persona_changes,
            auto_adapt_from_context,
        )

        # Явная команда: пользователь сказал «короче» / «дружелюбнее»
        change = await detect_persona_change(raw)
        if change:
            await apply_persona_changes(owner_telegram_id, change["changes"])
            await message.answer(sanitize_html(f"✅ Понял! Буду {change['reason']}."))
            return True

        # Авто-адаптация: бот сам чувствует настроение
        # Не блокирует — возвращает False, чтобы сообщение обрабатывалось дальше
        try:
            await auto_adapt_from_context(owner_telegram_id, raw, provider=None)
        except (RequestError, HTTPStatusError):
            logger.debug("auto_adapt_from_context failed", exc_info=True)

    except (RequestError, HTTPStatusError, TelegramAPIError):
        logger.exception("adaptive persona check failed")
    return False


async def check_followup(
    raw: str,
    owner_telegram_id: int,
    message: Message,
    state: FSMContext | None,
    userbot_manager: UserbotManager,
    tz_name: str,
    turn_started: float,
) -> bool:
    """Проверяет follow-up контекст. Возвращает True если обработано."""
    if state is None:
        logger.debug("check_followup skipped: state is None (voice transcription path)")
        return False
    followup = await _detect_followup(raw, owner_telegram_id)
    if followup:
        intent, _update_type = followup
        await _execute_intent(intent, message, state, userbot_manager, tz_name=tz_name)
        # Record action for smart correction
        try:
            from src.bot.handlers.smart_correction import record_action

            await record_action(
                owner_telegram_id,
                {
                    "intent": intent.get("intent", ""),
                    "params": dict(intent),
                },
            )
        except Exception:
            logger.debug("record_action failed in followup", exc_info=True)
        await _save_intent_context(owner_telegram_id, intent)
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="followup",
            intent_json=intent,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return True
    return False


def _time_of_day_greeting(tz_name: str | None = None) -> str:
    """Возвращает приветствие в зависимости от времени суток пользователя."""
    hour = now_in_tz(tz_name).hour
    if 6 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 18:
        return "Добрый день"
    if 18 <= hour < 23:
        return "Добрый вечер"
    return "Доброй ночи"


def _detect_context_hint(
    raw: str,
    plan_purpose: str | None = None,
) -> str | None:
    """Определяет контекстную подсказку для humanize_response.

    Сначала смотрит на purpose из плана авто-роутера,
    затем на ключевые слова в тексте пользователя.
    """
    # 1. Purpose → hint mapping
    purpose_map: dict[str, str] = {
        "search": "search",
        "analysis": "analysis",
        "draft": "send",
        "memory": "memory",
    }
    if plan_purpose and plan_purpose in purpose_map:
        return purpose_map[plan_purpose]

    # 2. Ключевые слова в тексте
    text_lower = raw.lower()
    if any(kw in text_lower for kw in ("найди", "поиск", "поищи", "search", "ищи")):
        return "search"
    if any(kw in text_lower for kw in ("проанализируй", "анализ", "разбери", "разбор")):
        return "analysis"
    if _looks_like_send_request(text_lower):
        return "send"
    if any(kw in text_lower for kw in ("напомни", "напоминание", "remind", "напомни")):
        return "reminder"
    if any(
        kw in text_lower
        for kw in ("запомни", "сохрани", "в память", "store_memory", "remember")
    ):
        return "memory"
    if any(
        kw in text_lower for kw in ("новости", "новость", "дайджест", "digest", "news")
    ):
        return "news"
    return None


def _looks_like_send_request(text_lower: str) -> bool:
    """True only for messaging intent, not generic "write text/code/recipe" requests."""
    if any(kw in text_lower for kw in ("отправь", "отправить", "сообщение", "с draft")):
        return True
    if "напиши" not in text_lower:
        return False
    recipient_markers = (
        " оле",
        " ему",
        " ей",
        " им ",
        " маме",
        " папе",
        " артёму",
        " артему",
    )
    return any(marker in f" {text_lower} " for marker in recipient_markers)


def _safe_for_deep_humanize(text: str, context_hint: str | None = None) -> bool:
    """Avoid second-pass LLM rewriting for structured or exact outputs."""
    if context_hint == "send":
        return False
    stripped = text.strip()
    if not stripped:
        return False
    structured_markers = ("```", "<code", "</", "{", "}", "[", "]")
    if any(marker in stripped for marker in structured_markers):
        return False
    if stripped.startswith(("{", "[", "- ", "* ", "1. ")):
        return False
    if "|" in stripped and "\n" in stripped:
        return False
    exact_output_words = (
        "json",
        "yaml",
        "sql",
        "код",
        "команд",
        "traceback",
        "exception",
    )
    return not any(word in stripped.lower() for word in exact_output_words)


async def execute_instant(
    plan,
    message: Message,
    raw: str,
    owner_telegram_id: int,
    turn_started: float,
    tz_name: str | None = None,
    *,
    _via_dispatcher: bool = False,
) -> bool:
    """Выполняет INSTANT-ответ (персонализированный). Возвращает True.

    S2-T5: если план пришёл из RouteCache (plan.metrics["route_cache_hit"]),
    пропускаем recall() и DB-тяжёлые операции — сразу pre-gate → humanize → send.

    _via_dispatcher: когда True, пропускаем pre/post-хуки (их делает UnifiedDispatcher).
    """
    if not _via_dispatcher:
        try:
            from src.core.infra.hooks import hooks

            await hooks.emit("on_message_received", user_id=owner_telegram_id, text=raw)
        except Exception:
            logger.debug(
                "execute_instant hooks.emit on_message_received failed", exc_info=True
            )  # hooks are optional, never break core flow

        # ── Smart Model Routing: логгирование решения ──────────────────
        _log_smart_routing(plan, raw)

        # Log user message to session (fire-and-forget)
        from src.core.scheduling.session_logger import log_user_message

        track_ff(asyncio.create_task(log_user_message(message.from_user.id, raw)))

    # ── S2-T5: RouteCache hit — быстрый путь без recall/DB ───────────
    _route_cache_hit = (
        plan.metrics.get("route_cache_hit", False)
        if hasattr(plan, "metrics")
        else False
    )

    if not _via_dispatcher:
        # ✨ Pre-LLM gate: handle greetings/farewells without LLM
        gate_response = check_pre_gate(raw)
        if gate_response:
            response = gate_response
            _cache_last_humanized(owner_telegram_id, response)
            await safe_answer(
                message, sanitize_html(response), reply_markup=memory_quick_keyboard()
            )
            await ctx_store.add_turn(message.from_user.id, raw[:200], response[:400])
            _fire_record_trajectory(
                owner_telegram_id,
                request_text=raw,
                route_mode="instant_gate",
                intent_json={"intent": "chat"},
                response_text=response,
                success=True,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
            )
            await _post_turn_optimize(owner_telegram_id, raw, response)
            from src.core.scheduling.session_logger import log_assistant_response

            track_ff(
                asyncio.create_task(
                    log_assistant_response(message.from_user.id, response)
                )
            )
            return True

    # Fetch anti_ai_mode once before any humanize call to avoid extra DB roundtrip.
    anti_ai_mode = await _get_anti_ai_mode(owner_telegram_id)

    # ── S2-T5 быстрый путь: кэш-hit → пропускаем recall, сразу humanize ──
    if _route_cache_hit and plan.final_response:
        context_hint = _detect_context_hint(
            raw, plan_purpose=plan.tasks[0].purpose.value if plan.tasks else None
        )
        response = await _humanize_assistant_response(
            plan.final_response,
            owner_telegram_id=owner_telegram_id,
            context_hint=context_hint,
            source="free_text.execute_instant.cached",
            mode=anti_ai_mode,
        )
        _cache_last_humanized(owner_telegram_id, response)
        await safe_answer(message, sanitize_html(response))
        if not _via_dispatcher:
            await ctx_store.add_turn(message.from_user.id, raw[:200], response[:400])
            _fire_record_trajectory(
                owner_telegram_id,
                request_text=raw,
                route_mode="instant_cached",
                intent_json={"intent": "chat"},
                response_text=response,
                success=True,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
            )
            await _post_turn_optimize(owner_telegram_id, raw, response)
            from src.core.scheduling.session_logger import log_assistant_response

            track_ff(
                asyncio.create_task(
                    log_assistant_response(message.from_user.id, response)
                )
            )
        return True

    # Динамическое приветствие с учётом наличия памяти и сессии
    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_telegram_id)
            memories = await recall(
                telegram_id=owner_telegram_id,
                limit=1,
                mode="normal",  # минимальная проверка наличия памяти
            )
            has_memory = bool(memories and memories.facts)
            has_session = owner.session is not None
            name = getattr(owner, "alias", None) or ""
    except Exception:
        logger.exception("execute_instant: DB/recall failed, using default greeting")
        # ponytail: assume existing user on DB error — safer than onboarding spam
        has_memory = True
        has_session = True
        name = ""

    if not has_memory and not has_session:
        # Совершенно новый пользователь — first touch
        response = (
            "Привет! Я AI-ассистент. Обожаю своего создателя (@dutysissy)!\n\n"
            "Чтобы я заработал, нужно 3 шага:\n"
            "1. /login — привязать Telegram-аккаунт\n"
            "2. Добавить API-ключ для LLM (я подскажу как)\n"
            "3. /sync — я прочитаю твои чаты и запомню важное\n\n"
            "Поехали! Жми /login 🚀"
        )
    elif not has_memory:
        response = (
            "👋 <b>Привет! Я v3.0</b>\n\n"
            "Уже умею:\n"
            "🧠 Запоминать факты о тебе и контактах\n"
            "💬 Отвечать за тебя в ЛС (авто-ответ)\n"
            "📋 Вести список дел и напоминать\n"
            "📰 Собирать дайджест новостей\n"
            "🔍 Искать по истории переписок\n\n"
            "Чтобы я запомнил твои контакты и факты — жми /sync"
        )
    else:
        response = f"{_time_of_day_greeting(tz_name=tz_name)}{', ' + name if name else ''}! Чем займёмся?"

    # Humanize the assistant response according to Anti-AI runtime mode.
    context_hint = _detect_context_hint(
        raw, plan_purpose=plan.tasks[0].purpose.value if plan.tasks else None
    )
    response = await _humanize_assistant_response(
        response,
        owner_telegram_id=owner_telegram_id,
        context_hint=context_hint,
        source="free_text.execute_instant",
        mode=anti_ai_mode,
    )
    _cache_last_humanized(owner_telegram_id, response)

    await safe_answer(message, sanitize_html(response))
    if not _via_dispatcher:
        await ctx_store.add_turn(message.from_user.id, raw[:200], response[:400])
        # Record action for smart correction (skip first-time/intro messages)
        if has_memory or has_session:
            try:
                from src.bot.handlers.smart_correction import record_action

                await record_action(
                    owner_telegram_id,
                    {
                        "intent": "chat",
                        "params": {"reply": response[:200]},
                    },
                )
            except Exception:
                logger.debug("record_action failed in execute_instant", exc_info=True)
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="instant",
            intent_json={"intent": "chat"},
            response_text=response,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        await _post_turn_optimize(owner_telegram_id, raw, response)
        from src.core.scheduling.session_logger import log_assistant_response

        track_ff(
            asyncio.create_task(log_assistant_response(message.from_user.id, response))
        )
    return True


async def execute_fast_route(
    raw: str,
    plan,
    provider,
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
    tz_name: str,
    owner_telegram_id: int,
    history_block: str,
    turn_started: float,
    now_local_str: str,
    *,
    _via_dispatcher: bool = False,
) -> bool:
    """Выполняет FAST_ROUTE. Возвращает True.

    _via_dispatcher: когда True, пропускаем trajectory/ctx/log (их делает UnifiedDispatcher).
    """
    fast_start = time.monotonic()
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
        logger.exception("fast_route route_intent failed")
        plan.metrics["llm_ms"] = -1
        err_msg = safe_str(e)
        if not _via_dispatcher:
            _fire_record_trajectory(
                owner_telegram_id,
                request_text=raw,
                route_mode="fast_route",
                intent_json={"intent": "chat"},
                success=False,
                error=err_msg[:4000],
                latency_ms=int((time.monotonic() - turn_started) * 1000),
            )
        if len(err_msg) > 300:
            err_msg = err_msg[:300] + "…"
        await safe_answer(
            message,
            sanitize_html(
                f"❌ Ошибка при обработке запроса.\n\n"
                f"<code>{err_msg}</code>\n\n"
                "<i>Если ошибка повторяется — проверь ключ в /settings → 🔑 API-ключи "
                "и модель в /settings → 🤖 LLM.</i>"
            ),
        )
        return True

    plan.metrics["llm_ms"] = int((time.monotonic() - fast_start) * 1000)
    plan.metrics["total_ms"] = plan.metrics.get("recall_ms", 0) + plan.metrics.get(
        "llm_ms", 0
    )
    logger.info("Fast route metrics: %s", json.dumps(plan.metrics, default=str))

    # Проверка confidence — если низкий, уточняем
    if await _check_intent_confidence(intent, message):
        return True

    # Lazy import to avoid circular dependency with _dag.py
    from src.bot.handlers.free_text._dag import _dag_dispatch

    if intent.get("intent") == "multi":
        actions = intent.get("actions") or []
        if not isinstance(actions, list) or not actions:
            await message.answer("Не понял, что сделать.")
            return True
        await _dag_dispatch(actions, message, state, userbot_manager, tz_name=tz_name)
    elif "intents" in intent:
        sub_intents = intent.get("intents")
        if not isinstance(sub_intents, list):
            await message.answer("Не понял, что сделать.")
            return True
        await _dag_dispatch(
            sub_intents, message, state, userbot_manager, tz_name=tz_name
        )
    else:
        await _dispatch(intent, message, state, userbot_manager, tz_name=tz_name)

    # Learning Router: запоминаем ключевые слова из успешных интентов
    # (только для action-интентов, фильтр внутри learn_routing)
    intent_kind = intent.get("intent", "")
    if intent_kind not in ("multi",):
        _learn_routing(raw, intent_kind)
    elif intent_kind == "multi":
        for sub in intent.get("actions", intent.get("intents", [])):
            _learn_routing(raw, sub.get("intent", ""))

    await _save_intent_context(owner_telegram_id, intent)

    if not _via_dispatcher:
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="fast_route",
            intent_json=intent,
            actions_json=actions_from_intent(intent),
            used_skills_json=intent.get("used_skills", []),
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
    return True


async def execute_maestro(
    raw: str,
    plan,
    provider,
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
    tz_name: str,
    owner_telegram_id: int,
    history_block: str,
    turn_started: float,
    injected_style: str | None = None,
    *,
    _via_dispatcher: bool = False,
) -> MaestroResult:
    """Выполняет MAESTRO pipeline. Возвращает MaestroResult — handled/response_text/used_skills/trace.

    _via_dispatcher: когда True, пропускаем pre/post-хуки (их делает UnifiedDispatcher).
    """
    # Lazy import to avoid circular dep: dispatcher → _core → dispatcher
    from src.core.dispatcher import MaestroResult

    if not _via_dispatcher:
        try:
            from src.core.infra.hooks import hooks

            await hooks.emit("on_message_received", user_id=owner_telegram_id, text=raw)
        except Exception:
            logger.debug(
                "execute_maestro hooks.emit on_message_received failed", exc_info=True
            )  # hooks are optional, never break core flow

        # ── Smart Model Routing: логгирование решения ──────────────────
        _log_smart_routing(plan, raw)

        # Log user message to session (fire-and-forget)
        from src.core.scheduling.session_logger import log_user_message

        track_ff(asyncio.create_task(log_user_message(message.from_user.id, raw)))

        # ✨ Pre-LLM gate: handle greetings/farewells without LLM
        # check_pre_gate уже вызван в Stage -2 классификатора (free_text.py:757).
        # Повторный вызов — no-op, но оставлен как defense-in-depth.
        gate_response = check_pre_gate(raw)
        if gate_response:
            response = gate_response
            _cache_last_humanized(owner_telegram_id, response)
            await safe_answer(
                message, sanitize_html(response), reply_markup=memory_quick_keyboard()
            )
            _fire_record_trajectory(
                owner_telegram_id,
                request_text=raw,
                route_mode="maestro_gate",
                intent_json={"intent": "chat"},
                response_text=response,
                success=True,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
            )
            await ctx_store.add_turn(message.from_user.id, raw[:200], response[:400])
            await _post_turn_optimize(owner_telegram_id, raw, response)
            # Log assistant response to session
            from src.core.scheduling.session_logger import log_assistant_response

            track_ff(
                asyncio.create_task(
                    log_assistant_response(message.from_user.id, response)
                )
            )
            return MaestroResult(handled=True)

    # 🧠 LLM Response Cache: проверяем кэш перед LLM-вызовом
    from src.core.intelligence.llm_response_cache import response_cache

    cached_response = await response_cache.get(raw)
    if cached_response:
        # Применяем humanization и отправляем закэшированный ответ
        anti_ai_mode = await _get_anti_ai_mode(owner_telegram_id)
        context_hint = _detect_context_hint(
            raw,
            plan_purpose=plan.tasks[0].purpose.value if plan.tasks else None,
        )
        humanized = await _humanize_assistant_response(
            cached_response,
            owner_telegram_id=owner_telegram_id,
            context_hint=context_hint,
            style_profile="",
            source="free_text.cached",
            mode=anti_ai_mode,
        )
        response_text = sanitize_html(humanized)
        _cache_last_humanized(owner_telegram_id, response_text)
        await safe_answer(message, response_text, reply_markup=memory_quick_keyboard())
        if not _via_dispatcher:
            await ctx_store.add_turn(
                message.from_user.id, raw[:200], response_text[:400]
            )
            _fire_record_trajectory(
                owner_telegram_id,
                request_text=raw,
                route_mode="maestro_cache_hit",
                intent_json={"intent": "chat"},
                response_text=response_text,
                success=True,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
            )
            await _post_turn_optimize(owner_telegram_id, raw, response_text)
            # Log assistant response to session
            from src.core.scheduling.session_logger import log_assistant_response

            track_ff(
                asyncio.create_task(
                    log_assistant_response(message.from_user.id, response_text)
                )
            )
        logger.debug("LLM response cache HIT, bypassed LLM call: %.60s", raw)
        if _via_dispatcher:
            return MaestroResult(
                handled=True,
                response_text=response_text,
            )
        return MaestroResult(handled=True)

    # ── Progress: память и продуктивное обдумывание ──────────────────
    _progress_msg = await message.answer(_PROGRESS_MESSAGES["recalling"])
    _progress_start = time.monotonic()

    rag_needed = plan.recall_mode == "deep"
    # 📊 Productive thinking time: use the delay for better recall
    if not rag_needed and len(raw) > 30:
        try:
            from src.core.memory.contradiction_detector import detect_contradiction
            from src.core.memory.memory_recall import recall

            _deep = await recall(
                telegram_id=owner_telegram_id,
                query=raw,
                limit=_DEFAULT_SEARCH_LIMIT,
                mode="deep",
                include_deep=True,
            )
            _contra = await detect_contradiction(owner_telegram_id, raw)
            if _deep and _deep.facts and len(_deep.facts) > 3:
                rag_needed = True  # more facts found → upgrade to deep mode
                logger.debug(
                    "Upgraded to deep recall: %d facts found in thinking time",
                    len(_deep.facts),
                )
        except (SQLAlchemyError, RequestError, HTTPStatusError):
            logger.debug("Enhanced recall in thinking time failed", exc_info=True)

    # ── Progress: планирование ───────────────────────────────────────
    _elapsed = time.monotonic() - _progress_start
    if _elapsed >= _PROGRESS_MIN_DURATION:
        try:
            await _progress_msg.edit_text(_PROGRESS_MESSAGES["planning"])
        except TelegramAPIError:
            _progress_msg = await message.answer(_PROGRESS_MESSAGES["planning"])
    else:
        try:
            await _progress_msg.edit_text(_PROGRESS_MESSAGES["planning"])
        except TelegramAPIError:
            pass  # не критично, если сообщение пропало
    _planning_start = time.monotonic()

    try:
        pipeline_result = await run_pipeline(
            provider,
            raw,
            owner_id=owner_telegram_id,
            history_block=history_block,
            memory_context=getattr(plan, "memory_context", "") or None,
            global_style=injected_style,
            self_profile=getattr(plan, "self_profile", "") or None,
            rag_enabled=rag_needed,
            contact_id=(
                plan.tasks[0].meta.get("contact_id")
                if getattr(plan, "tasks", None)
                and plan.tasks
                and getattr(plan.tasks[0], "meta", None)
                else None
            ),
            userbot_manager=userbot_manager,
            route="default_chat",
        )

        # ── Handle tool confirmation needed ──────────────────────────
        if pipeline_result.get("confirmation_needed"):
            confirm_msg = pipeline_result.get(
                "confirm_message",
                pipeline_result.get("final_response", "Подтверди действие"),
            )
            tool_name = pipeline_result.get("tool", "")
            tool_params = pipeline_result.get("tool_params", {})
            confirm_cb, cancel_cb = await _store_tool_confirmation(
                owner_telegram_id, tool_name, tool_params
            )
            await safe_answer(
                message,
                sanitize_html(f"🤔 {confirm_msg}"),
                reply_markup=_confirm_tool_keyboard(confirm_cb, cancel_cb),
            )
            if not _via_dispatcher:
                _fire_record_trajectory(
                    owner_telegram_id,
                    request_text=raw,
                    route_mode="maestro_tool_confirm",
                    intent_json={"intent": tool_name, **tool_params},
                    actions_json=pipeline_result.get("plan", []),
                    success=True,
                    error=None,
                    latency_ms=int((time.monotonic() - turn_started) * 1000),
                )
                await ctx_store.add_turn(
                    message.from_user.id,
                    raw[:200],
                    f"[tool confirmation: {tool_name}]",
                )
                trace = dict(pipeline_result.get("trace") or {})
                log_response_trace(
                    route="maestro_tool_confirm",
                    owner_id=owner_telegram_id,
                    memory_context=getattr(plan, "memory_context", "") or "",
                    context_sources=trace.get("context_sources", []),
                    tools_proposed=trace.get("tools_proposed", []),
                    tools_executed=trace.get("tools_executed", []),
                    tools_blocked=trace.get("tools_blocked", [tool_name]),
                    guardrail_decision=trace.get("guardrail_decision", {}),
                    humanizer_mode="off",
                    humanizer_changed=False,
                    latency_ms=int((time.monotonic() - turn_started) * 1000),
                )
                # Log assistant response to session
                from src.core.scheduling.session_logger import log_assistant_response

                track_ff(
                    asyncio.create_task(
                        log_assistant_response(message.from_user.id, confirm_msg)
                    )
                )
            if _via_dispatcher:
                return MaestroResult(
                    handled=True,
                    response_text=confirm_msg,
                )
            return MaestroResult(handled=True)

        # ── Handle streaming response ────────────────────────────────
        stream = pipeline_result.get("_stream")
        if stream is not None:
            # ── Progress: генерация ответа ─────────────────────────
            _elapsed_total = time.monotonic() - _planning_start
            if _elapsed_total >= _PROGRESS_MIN_DURATION:
                try:
                    await _progress_msg.edit_text(_PROGRESS_MESSAGES["generating"])
                except TelegramAPIError:
                    pass  # сообщение могло быть удалено
            # Определяем context_hint заранее для финального humanize
            plan_purpose = plan.tasks[0].purpose.value if plan.tasks else None
            context_hint = _detect_context_hint(raw, plan_purpose=plan_purpose)

            # Получаем стилевой профиль
            try:
                from src.core.intelligence.style_matcher import (
                    get_or_update_style_profile,
                )

                style_block = await get_or_update_style_profile(owner_telegram_id)
            except (SQLAlchemyError, RequestError, HTTPStatusError):
                style_block = None

            if settings.streaming_enabled:
                # Курсор для отображения в процессе стриминга
                cursor = (
                    settings.streaming_cursor.strip()
                    if settings.streaming_cursor
                    else "▌"
                )
                # Временной интервал (сек) — страховка от лагов сети
                time_interval = settings.streaming_edit_interval
                # Символьный интервал — основная логика обновления
                char_interval = settings.streaming_update_interval

                # Response pacing — human-like задержка перед ответом.
                # ponytail: typing indicator + sleep, upgrade to per-contact pacing if needed.
                if settings.response_pacing_mode != "off":
                    delay = (
                        random.uniform(
                            settings.response_pacing_min_ms,
                            settings.response_pacing_max_ms,
                        )
                        / 1000.0
                    )
                    try:
                        await message.answer_chat_action("typing")  # type: ignore[union-attr]
                    except TelegramAPIError:
                        pass
                    await asyncio.sleep(delay)

                # Отправляем первое сообщение с курсором
                sent_msg = await message.answer(cursor)
                full_text = ""
                last_update = asyncio.get_event_loop().time()
                chars_since_update = 0

                try:
                    async for chunk in stream:
                        full_text += chunk
                        chars_since_update += len(chunk)
                        now = asyncio.get_event_loop().time()
                        # Обновляем по достижении символьного ИЛИ временного порога
                        if (
                            chars_since_update >= char_interval
                            or now - last_update >= time_interval
                        ):
                            try:
                                await sent_msg.edit_text(
                                    (scrub_internal_tags(full_text) + cursor)[:4000]
                                )
                            except TelegramAPIError:
                                pass  # сообщение удалено или устарело
                            last_update = now
                            chars_since_update = 0
                except Exception:
                    logger.debug("Stream interrupted", exc_info=True)
            else:
                # Non-streaming: silently accumulate text
                if settings.response_pacing_mode != "off":
                    delay = (
                        random.uniform(
                            settings.response_pacing_min_ms,
                            settings.response_pacing_max_ms,
                        )
                        / 1000.0
                    )
                    try:
                        await message.answer_chat_action("typing")  # type: ignore[union-attr]
                    except TelegramAPIError:
                        pass
                    await asyncio.sleep(delay)
                sent_msg = await message.answer("⏳")
                chunks: list[str] = []
                try:
                    async for chunk in stream:
                        chunks.append(chunk)
                except Exception:
                    logger.debug("Stream interrupted", exc_info=True)
                full_text = "".join(chunks)

            if not full_text.strip():
                try:
                    await sent_msg.edit_text("⚠️ Не получилось сгенерировать ответ")
                except TelegramAPIError:
                    await message.answer("⚠️ Не получилось сгенерировать ответ")
                return MaestroResult(handled=True)

            # Scrub internal tags from final text before humanize/display.
            full_text = scrub_internal_tags(full_text)
            if not full_text.strip():
                try:
                    await sent_msg.edit_text("⚠️ Не получилось сгенерировать ответ")
                except TelegramAPIError:
                    await message.answer("⚠️ Не получилось сгенерировать ответ")
                return MaestroResult(handled=True)

            # Apply Anti-AI mode after streaming; off/log keep text unchanged.
            anti_ai_mode = await _get_anti_ai_mode(owner_telegram_id)
            original_text = full_text.strip()
            humanized = await _humanize_assistant_response(
                original_text,
                owner_telegram_id=owner_telegram_id,
                context_hint=context_hint,
                style_profile=style_block or "",
                source="free_text.stream",
                mode=anti_ai_mode,
            )
            # Deep humanize if needed
            score, _ = analyze_ai_score(humanized)
            if (
                anti_ai_mode == "fix"
                and score > 0.3
                and len(humanized) > 100
                and _safe_for_deep_humanize(humanized, context_hint=context_hint)
            ):
                from src.llm.provider_manager import build_provider

                humanize_provider = None
                try:
                    from src.db.session import get_session as _get_hp_session
                    from src.db.repo import get_or_create_user as _get_hp_user

                    async with _get_hp_session() as hp_session:
                        hp_user = await _get_hp_user(hp_session, owner_telegram_id)
                        humanize_provider = await build_provider(
                            hp_session,
                            hp_user,
                            purpose="humanize",
                            task_type=TaskType.HUMANIZE,
                        )
                    if humanize_provider:
                        humanized = await humanize_deep(
                            humanized, humanize_provider, user_style=style_block or ""
                        )
                except Exception:
                    # humanize_deep внутренне ловит все исключения и возвращает исходный текст;
                    # этот catch — для неожиданных ошибок самого вызова
                    logger.debug("humanize_deep failed on streamed text", exc_info=True)
                finally:
                    if humanize_provider:
                        try:
                            await humanize_provider.close()
                        except Exception:
                            logger.debug(
                                "Failed to close humanize_provider (stream)",
                                exc_info=True,
                            )
            humanizer_changed = humanized != original_text
            response_text = sanitize_html(humanized)
            _cache_last_humanized(owner_telegram_id, response_text)

            # 🧠 Кэшируем оригинальный ответ LLM (до humanization)
            # для будущих семантически похожих запросов
            track_ff(asyncio.create_task(response_cache.set(raw, original_text)))

            # Final message without cursor
            try:
                await sent_msg.edit_text(
                    response_text[:4000], reply_markup=memory_quick_keyboard()
                )
            except TelegramAPIError:
                await safe_answer(
                    message, response_text, reply_markup=memory_quick_keyboard()
                )

            # Auto-save facts
            track_ff(
                asyncio.create_task(
                    _maybe_auto_save_facts(
                        raw, response_text, owner_telegram_id, provider
                    )
                )
            )

            used = pipeline_result.get("used_agents", [])
            errors = pipeline_result.get("agent_errors", [])
            if used:
                logger.debug("Maestro agents: %s", used)
            if errors:
                logger.debug("Maestro agent errors: %s", errors)

            _used_skills = pipeline_result.get("used_skills", [])
            trace = dict(pipeline_result.get("trace") or {})
            log_response_trace(
                route="maestro_stream",
                owner_id=owner_telegram_id,
                memory_context=getattr(plan, "memory_context", "") or "",
                context_sources=trace.get("context_sources", []),
                tools_proposed=trace.get("tools_proposed", []),
                tools_executed=trace.get("tools_executed", []),
                tools_blocked=trace.get("tools_blocked", []),
                guardrail_decision=trace.get("guardrail_decision", {}),
                humanizer_mode=anti_ai_mode,
                humanizer_changed=humanizer_changed,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
                extra={"used_skills": _used_skills},
            )
            if not _via_dispatcher:
                _fire_record_trajectory(
                    owner_telegram_id,
                    request_text=raw,
                    route_mode="maestro",
                    intent_json={"intent": "maestro"},
                    actions_json=pipeline_result.get("plan", []),
                    used_skills_json=_used_skills,
                    response_text=response_text,
                    success=True,
                    error="; ".join(errors) if errors else None,
                    latency_ms=int((time.monotonic() - turn_started) * 1000),
                )
                await ctx_store.add_turn(
                    message.from_user.id, raw[:200], response_text[:400]
                )
                await _post_turn_optimize(owner_telegram_id, raw, response_text)
                try:
                    from src.core.infra.hooks import hooks

                    await hooks.emit(
                        "on_message_post_maestro",
                        user_id=owner_telegram_id,
                        input=raw,
                        response=response_text,
                        plan=pipeline_result.get("plan", []),
                    )
                except Exception:
                    logger.debug(
                        "post_maestro hooks.emit failed (stream)", exc_info=True
                    )
                # Log assistant response to session
                from src.core.scheduling.session_logger import log_assistant_response

                track_ff(
                    asyncio.create_task(
                        log_assistant_response(message.from_user.id, response_text)
                    )
                )
                return MaestroResult(handled=True)
            return MaestroResult(
                handled=True,
                response_text=response_text,
                used_skills=_used_skills,
                trace=trace,
            )

        # ── Handle tool results from tool loop ───────────────────────
        # Если maestro вернул tool_result, используем его для обогащения
        # ответа, но итоговый ответ берём из final_response (LLM уже
        # синтезировала его с учётом результатов инструмента).
        response_text = pipeline_result.get("final_response", "")
        _used_skills = pipeline_result.get("used_skills", [])
        if response_text:
            # ── Humanizer: post-process response ──────────────────────
            # Определяем контекстную подсказку из purpose плана
            plan_purpose = plan.tasks[0].purpose.value if plan.tasks else None
            context_hint = _detect_context_hint(raw, plan_purpose=plan_purpose)

            # Получаем стилевой профиль пользователя
            try:
                from src.core.intelligence.style_matcher import (
                    get_or_update_style_profile,
                )

                style_block = await get_or_update_style_profile(owner_telegram_id)
            except (SQLAlchemyError, RequestError, HTTPStatusError):
                style_block = None

            # Stage 1: Anti-AI mode (off/log/fix). Fix clips endings and applies light replacements.
            anti_ai_mode = await _get_anti_ai_mode(owner_telegram_id)
            original_response_text = response_text

            # 🧠 Кэшируем оригинальный ответ LLM (до humanization)
            # для будущих семантически похожих запросов
            track_ff(
                asyncio.create_task(response_cache.set(raw, original_response_text))
            )

            humanized = await _humanize_assistant_response(
                original_response_text,
                owner_telegram_id=owner_telegram_id,
                context_hint=context_hint,
                style_profile=style_block or "",
                source="free_text.final_response",
                mode=anti_ai_mode,
            )

            # Stage 2: deep humanize + self-correction (общий humanize_provider).
            # X1: build_provider с purpose="humanize" подхватывает humanize_model из настроек.
            response_text = humanized
            humanize_provider = None
            try:
                from src.llm.provider_manager import build_provider

                async with get_session() as hp_session:
                    hp_user = await get_or_create_user(hp_session, owner_telegram_id)
                    humanize_provider = await build_provider(
                        hp_session,
                        hp_user,
                        purpose="humanize",
                        task_type=TaskType.HUMANIZE,
                    )

                if humanize_provider:
                    score, _ = analyze_ai_score(humanized)
                    if (
                        anti_ai_mode == "fix"
                        and score > 0.3
                        and len(humanized) > 100
                        and _safe_for_deep_humanize(
                            humanized, context_hint=context_hint
                        )
                    ):
                        try:
                            user_style_hint = style_block or ""
                            humanized = await humanize_deep(
                                humanized,
                                humanize_provider,
                                user_style=user_style_hint,
                            )
                        except Exception:
                            # humanize_deep внутренне ловит все исключения и возвращает
                            # исходный текст; этот catch — для неожиданных ошибок вызова
                            logger.debug(
                                "humanize_deep failed, using light humanized",
                                exc_info=True,
                            )

                    response_text = humanized
                    _cache_last_humanized(owner_telegram_id, response_text)

                    # ── Self-correction loop (X2: budget ≤1, stop-if-improved) ──
                    if (
                        anti_ai_mode == "fix"
                        and response_text
                        and len(response_text) > 50
                    ):
                        score_before, _ = analyze_ai_score(response_text)
                        if score_before >= 0.3:
                            correction_prompt = (
                                f"Твой ответ вышел слишком AI-шаблонным "
                                f"(score={score_before:.2f}). "
                                f"Перепиши его естественно, как человек:\n\n"
                                f"{response_text[:1000]}"
                            )
                            try:
                                rewritten = await humanize_provider.chat(
                                    [
                                        ChatMessage(
                                            role="user", content=correction_prompt
                                        )
                                    ],
                                    task_type=TaskType.HUMANIZE,
                                )
                                if rewritten and len(rewritten) > 20:
                                    rewritten = _preservation_check(
                                        response_text, rewritten
                                    )
                                    score_after, _ = analyze_ai_score(rewritten)
                                    if score_after < score_before:
                                        # Только если улучшило — применяем
                                        response_text = rewritten
                                        logger.debug(
                                            "Self-correction improved score "
                                            "%.2f -> %.2f",
                                            score_before,
                                            score_after,
                                        )
                                    else:
                                        logger.debug(
                                            "Self-correction didn't improve "
                                            "(%.2f -> %.2f)",
                                            score_before,
                                            score_after,
                                        )
                            except Exception:
                                logger.debug(
                                    "Self-correction rewrite failed", exc_info=True
                                )
            except Exception:
                logger.debug(
                    "humanize_provider build failed, humanize skipped",
                    exc_info=True,
                )
            finally:
                if humanize_provider:
                    try:
                        await humanize_provider.close()
                    except Exception:
                        logger.debug(
                            "Failed to close humanize_provider (final)",
                            exc_info=True,
                        )
            # ── End Humanizer ─────────────────────────────────────────

            # Auto-save: fire-and-forget сохранение фактов о пользователе
            track_ff(
                asyncio.create_task(
                    _maybe_auto_save_facts(
                        raw, response_text, owner_telegram_id, provider
                    )
                )
            )
            used = pipeline_result.get("used_agents", [])
            errors = pipeline_result.get("agent_errors", [])
            if used:
                logger.debug("Maestro agents: %s", used)
            if errors:
                logger.debug("Maestro agent errors: %s", errors)

            # If there's a tool_result that wasn't rendered into final_response
            # (edge case), append a brief note
            tool_result = pipeline_result.get("tool_result")
            extra_suffix = ""
            if tool_result and not response_text:
                extra_suffix = f"\n\n<code>⚙️ {json.dumps(tool_result, default=str, ensure_ascii=False)[:200]}</code>"

            trace = dict(pipeline_result.get("trace") or {})
            log_response_trace(
                route="maestro",
                owner_id=owner_telegram_id,
                memory_context=getattr(plan, "memory_context", "") or "",
                context_sources=trace.get("context_sources", []),
                tools_proposed=trace.get("tools_proposed", []),
                tools_executed=trace.get("tools_executed", []),
                tools_blocked=trace.get("tools_blocked", []),
                guardrail_decision=trace.get("guardrail_decision", {}),
                humanizer_mode=anti_ai_mode,
                humanizer_changed=response_text != original_response_text,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
                extra={"used_skills": _used_skills},
            )
            # ── Убираем прогресс-сообщение перед финальным ответом ──
            try:
                await _progress_msg.edit_text(_PROGRESS_MESSAGES["generating"])
            except TelegramAPIError:
                pass
            await safe_answer(
                message,
                sanitize_html(response_text + extra_suffix),
                reply_markup=memory_quick_keyboard(),
            )
            if not _via_dispatcher:
                _fire_record_trajectory(
                    owner_telegram_id,
                    request_text=raw,
                    route_mode="maestro",
                    intent_json={"intent": "maestro"},
                    actions_json=pipeline_result.get("plan", []),
                    used_skills_json=_used_skills,
                    response_text=response_text,
                    success=True,
                    error="; ".join(errors) if errors else None,
                    latency_ms=int((time.monotonic() - turn_started) * 1000),
                )
                await ctx_store.add_turn(
                    message.from_user.id, raw[:200], response_text[:400]
                )
                await _post_turn_optimize(owner_telegram_id, raw, response_text)
                try:
                    from src.core.infra.hooks import hooks

                    await hooks.emit(
                        "on_message_post_maestro",
                        user_id=owner_telegram_id,
                        input=raw,
                        response=response_text,
                        plan=pipeline_result.get("plan", []),
                    )
                except Exception:
                    logger.debug(
                        "post_maestro hooks.emit failed (final_response)", exc_info=True
                    )
                # Log assistant response to session
                from src.core.scheduling.session_logger import log_assistant_response

                track_ff(
                    asyncio.create_task(
                        log_assistant_response(message.from_user.id, response_text)
                    )
                )
                return MaestroResult(handled=True)
            return MaestroResult(
                handled=True,
                response_text=response_text,
                used_skills=_used_skills,
                trace=trace,
            )
        return MaestroResult(handled=False)
    except Exception:
        logger.exception("execute_maestro failed")
        try:
            await safe_answer(message, "⚠️ Произошла ошибка. Попробуй ещё раз.")
        except Exception:
            pass  # don't crash on error notification
        try:
            from src.core.infra.hooks import hooks

            await hooks.emit(
                "on_error",
                error=str(sys.exc_info()[1])
                if sys.exc_info()[1]
                else "maestro pipeline failed",
                context="free_text.execute_maestro",
            )
        except Exception:
            logger.debug(
                "execute_maestro hooks.emit failed", exc_info=True
            )  # hooks are optional, never break core flow
        logger.debug("Maestro pipeline failed, falling back to route_intent")
        return MaestroResult(handled=False)


async def _check_intent_confidence(intent: dict, message: Message) -> bool:
    """Проверяет confidence. Если низкий — уточняет и возвращает True (обработано)."""
    # Если нет поля confidence — считаем что уверен (backward compat)
    if "confidence" not in intent:
        return False
    confidence = intent.get("confidence", 1.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        return False
    if confidence >= 0.6:
        return False

    question = intent.get("question") or "Не совсем понял. Что именно сделать?"
    await message.answer(sanitize_html(f"🤔 {question}"))
    return True


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
