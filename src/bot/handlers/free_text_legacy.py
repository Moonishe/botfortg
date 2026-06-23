"""Свободный текст (и голос) → агент → действие. Регистрируется последним в bot/app.py,
чтобы команды и FSM перехватывали свои события раньше."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.config import settings
from src.core.actions.trajectory import actions_from_intent
from src.core.infra.key_guard import safe_str
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.task_manager import track_ff
from src.core.intelligence.agent import route_intent
from src.core.intelligence.smart_autorouter import make_plan
from src.core.memory import conversation_context as ctx_store
from src.core.infra.timeutil import now_in_tz
from src.db.repo import (
    get_or_create_user,
)
from src.db.session import get_session
from src.llm.base import TaskType

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
from src.llm.router import build_provider
from src.userbot.manager import UserbotManager

from .free_text_common import (
    _fire_record_trajectory,
    _get_owner_context,
    _summarize_intent_for_memory,
)
from src.core.intelligence.character_evolution import maybe_evolve_after_turn

# ── Session Context (P2) ──────────────────────────────────────────────

from .free_text import (
    _dispatch,
    _extract_contact_hint,
    _save_intent_context,
    check_contact_rules,
    check_followup,
    check_instructions,
    check_persona,
    execute_fast_route,
    execute_instant,
    execute_maestro,
)
from src.core.humanizer import record_humanizer_feedback, _pop_last_humanized
from src.core.infra.rate_limiter import check_rate_limit
from src.core.classification import classify_message as _classify_message
from src.core.agents.proactive_scheduler import proactive_scheduler
from aiogram.exceptions import TelegramAPIError

# ── Re-imports from split sub-modules (preserving public API) ────────────
from src.bot.handlers.free_text._voice import (
    _active_tasks,
    _active_tasks_lock,
    start_voice_worker,  # noqa: F401 — re-exported for src/main.py
    stop_voice_worker,  # noqa: F401 — re-exported for src/main.py
    free_voice as _voice_free_voice,
    _cb_voice_research as _voice_cb_voice_research,
)
from src.bot.handlers.free_text._media import (
    handle_photo as _media_handle_photo,
    handle_video as _media_handle_video,
    handle_edited_message as _media_handle_edited_message,
)
from src.bot.handlers.free_text._singalong import _try_singalong

logger = logging.getLogger(__name__)
router = Router(name="free_text")
router.message.filter(OwnerOnly())

# ── URL detection ──────────────────────────────────────────────────────

_URL_RE = re.compile(r'https?://[^\s<>"]+')


def _extract_correction_pattern(original: str, edited: str) -> tuple[str, str] | None:
    """Извлекает паттерн исправления: (что_было, что_стало)."""
    if len(original) < 10 or len(edited) < 10:
        return None
    common = sum(1 for a, b in zip(original, edited, strict=False) if a == b)
    similarity = common / max(len(original), len(edited))
    if similarity > 0.5:
        return (original[:200], edited[:200])
    return None


# ── Session Context helpers (P2) ──────────────────────────────────────


async def _do_prefetch_contact(
    user_id: int,
    contact_hint: str | None,
    userbot_manager,
) -> None:
    """Fire-and-forget: prefetch contact data into cache.

    Runs in background — errors are caught and logged, never propagated.
    """
    try:
        from src.bot.prefetch import prefetch_contact
        from src.db.repo import get_or_create_user
        from src.db.session import get_session

        telethon_client = None
        owner = None
        if contact_hint is not None and userbot_manager is not None:
            try:
                telethon_client = userbot_manager.get_client(user_id)
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
            if telethon_client is not None:
                try:
                    async with get_session() as session:
                        owner = await get_or_create_user(session, user_id)
                except Exception:
                    logger.debug("Non-critical error", exc_info=True)

            if owner is not None:
                await prefetch_contact(
                    user_id,
                    contact_hint=contact_hint,
                    telethon_client=telethon_client,
                    owner=owner,
                )
    except Exception:
        logger.debug(
            "_do_prefetch_contact failed for user=%d hint=%r",
            user_id,
            contact_hint,
            exc_info=True,
        )


_QUESTION_STARTS = (
    "что",
    "как",
    "почему",
    "кто",
    "где",
    "когда",
    "зачем",
    "сколько",
    "какой",
    "какая",
    "какое",
    "какие",
    "чей",
)


_QUESTION_PUNCTUATION = set(",.!;:…«»\"'()[]{}—–-")


def _is_question(text: str) -> bool:
    """Определяет, является ли текст вопросом (есть '?' или вопросительное слово)."""
    if "?" in text:
        return True
    stripped = text.strip()
    if not stripped:
        return False
    first_word = stripped.lower().split()[0]
    # Удаляем прилипшую пунктуацию: «что,» → «что», «как...» → «как»
    first_word_clean = first_word.rstrip("".join(_QUESTION_PUNCTUATION))
    return first_word_clean in _QUESTION_STARTS


_WAITING_MESSAGES = [
    "⏳ Дай подумать…",
    "🤔 Сейчас соображу…",
    "💭 Уже думаю…",
    "🔍 Смотрю в переписке…",
    "📝 Анализирую…",
    "⏳ Секунду…",
    "🤖 Обрабатываю…",
    "💡 Генерирую ответ…",
]


def _get_waiting_message() -> str:
    return random.choice(_WAITING_MESSAGES)


async def _process_text_fallback(
    raw: str,
    provider,
    message: Message,
    state: FSMContext | None,
    userbot_manager: UserbotManager,
    tz_name: str,
    owner_telegram_id: int,
    history_block: str,
    plan,
    turn_started: float,
    now_local_str: str,
) -> None:
    """Stage 9: Fallback — route_intent → _dispatch (extracted for reuse from background tasks)."""
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
        logger.exception("agent route_intent failed")
        err_msg = safe_str(e)
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="intent",
            success=False,
            error=err_msg[:4000],
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        if len(err_msg) > 300:
            err_msg = err_msg[:300] + "…"
        await message.answer(
            sanitize_html(
                f"❌ Ошибка при обработке запроса.\n\n"
                f"<code>{err_msg}</code>\n\n"
                "<i>Если ошибка повторяется — проверь ключ в /settings → 🔑 API-ключи "
                "и модель в /settings → 🤖 LLM.</i>"
            )
        )
        return

    if intent.get("intent") == "multi":
        actions = intent.get("actions") or []
        if not isinstance(actions, list) or not actions:
            await message.answer("Не понял, что сделать.")
            return
        for sub in actions:
            await _dispatch(sub, message, state, userbot_manager, tz_name=tz_name)
    elif "intents" in intent:
        for sub in intent["intents"]:
            await _dispatch(sub, message, state, userbot_manager, tz_name=tz_name)
    else:
        await _dispatch(intent, message, state, userbot_manager, tz_name=tz_name)

    await _save_intent_context(owner_telegram_id, intent)

    _fire_record_trajectory(
        owner_telegram_id,
        request_text=raw,
        route_mode="intent",
        intent_json=intent,
        actions_json=actions_from_intent(intent),
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


# ── INSTANT bypass helpers ─────────────────────────────────────────


def _get_classify_mode(classification: dict) -> str | None:
    """Определяет режим ответа на основе результата Stage -2 классификатора.

    Если классификация содержит категории, для которых не нужен LLM —
    возвращает "INSTANT", чтобы пропустить Stage 0-3 полностью.
    """
    if not classification:
        return None
    # Категории, для которых достаточно мгновенного ответа без LLM
    instant_categories = {"agreement", "gratitude", "emotion"}
    if any(classification.get(cat) for cat in instant_categories):
        return "INSTANT"
    return None


def _instant_response(text: str, classification: dict, message: Message) -> str | None:
    """Генерирует мгновенный протокольный ответ без LLM, памяти и планирования.

    Использует результат Stage -2 классификатора + эвристики по тексту.
    Возвращает None если сообщение не подходит для мгновенного ответа —
    тогда пайплайн продолжается в Stage 0.
    """
    text_lower = text.strip().lower()

    # Согласие (ага, ок, да, понял, ладно, etc.)
    if classification.get("agreement"):
        responses = ["👍", "👌", "🤝", "ага", "ок", "добро"]
        return random.choice(responses)

    # Благодарность
    if classification.get("gratitude"):
        return "😊"

    # Эмоции (смех, удивление, etc.)
    if classification.get("emotion"):
        # Смех / laughter
        _laugh_markers = (
            "ха",
            "ахах",
            "хех",
            "hehe",
            "lol",
            "lmao",
            "ржа",
            "ржу",
            "смех",
            "смешно",
            "угар",
        )
        if any(m in text_lower for m in _laugh_markers):
            return random.choice(["😂", "😄", "😆", "ахах"])

        # Удивление / surprise
        _surprise_markers = ("ого", "вау", "ничего себе", "wow", "огонь", "обалдеть")
        if any(m in text_lower for m in _surprise_markers):
            return random.choice(["😮", "😯", "ого"])

        # Общая эмоция
        return random.choice(["😊", "👍", "ок"])

    # Короткая команда (отправь, напиши, найди — но коротко)
    if classification.get("command"):
        return "👀"

    # Очень короткое сообщение — универсальный мгновенный ответ
    if len(text_lower) < 10:
        return random.choice(["👍", "ок", "м"])

    # Не подходит для мгновенного ответа — продолжаем пайплайн
    return None


# ── NL Programming: detect cron-like phrases → BackgroundGoal ──────────

_NL_TRIGGERS = (
    "каждый",
    "каждые",
    "ежедневно",
    "еженедельно",
    "ежемесячно",
    "раз в",
    "по понедельникам",
    "по вторникам",
    "по средам",
    "по четвергам",
    "по пятницам",
    "по субботам",
    "по воскресеньям",
    "напомни",
    "напоминай",
)


async def _maybe_schedule_nl_goal(
    text: str,
    message: Message,
    owner_telegram_id: int,
    session: AsyncSession | None = None,
) -> bool:
    """Проверить текст на cron-like фразы и зарегистрировать BackgroundGoal.

    Возвращает True если текст был обработан как NL-задача (и отправил ответ),
    иначе False — тогда пайплайн продолжается.
    """
    if not settings.nl_programming_enabled:
        return False

    # Guard: empty/whitespace-only text cannot be a scheduling intent
    text_lower = text.strip().lower()
    if not text_lower:
        return False

    if not any(trigger in text_lower for trigger in _NL_TRIGGERS):
        return False

    try:
        from src.bot.nl_programming import NLProgrammer
        from src.db.repo import get_or_create_user

        if session is None:
            async with get_session() as session_ctx:
                user = await get_or_create_user(session_ctx, owner_telegram_id)
                if user is None:
                    return False
                goal = await NLProgrammer().parse(text, session_ctx, user)
        else:
            user = await get_or_create_user(session, owner_telegram_id)
            if user is None:
                return False
            goal = await NLProgrammer().parse(text, session, user)

        if goal is None:
            return False

        await proactive_scheduler.register(goal)
        desc = goal.description[:200]
        await message.answer(f"✅ Задача запланирована: {sanitize_html(desc)}")
        logger.info("NL scheduled goal %r for user %d", goal.id, owner_telegram_id)
        return True
    except Exception:
        logger.exception("NL scheduling failed for user %d", owner_telegram_id)
        return False


# ── Text message handler ─────────────────────────────────────────────────


@router.message(F.text)
async def _free_text_handler(
    message: Message,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    """Обработчик свободного текста — передаёт в _process_text."""
    if message.text is None:
        return

    # ── NL Router: check if message is a natural language command ──
    from src.bot.handlers.nl_router import try_nl_route

    handled = await try_nl_route(message.text, message, state, userbot_manager)
    if handled:
        return  # NL router handled it, don't proceed to LLM pipeline

    async with get_session() as session:
        await _process_text(
            message.text, message, state, userbot_manager, session=session
        )


async def _process_text(
    raw: str,
    message: Message,
    state: FSMContext | None,
    userbot_manager: UserbotManager,
    session=None,
) -> None:

    turn_started = time.monotonic()

    # Rate-limit: не чаще 1 запроса в 3 секунды на пользователя
    if not await check_rate_limit(message.from_user.id):
        await message.answer("⏳ Подожди пару секунд, обрабатываю предыдущий запрос…")
        return

    # ── Prefetch contacts (fire-and-forget, populates resolve_contact_fast cache) ──
    if settings.contact_prefetch_enabled:
        contact_hint = _extract_contact_hint(message)
        track_ff(
            asyncio.create_task(
                _do_prefetch_contact(
                    message.from_user.id,
                    contact_hint=contact_hint,
                    userbot_manager=userbot_manager,
                )
            )
        )

    ctx = await _get_owner_context(message.from_user.id, session)
    tz_name = str(ctx["tz_name"])
    owner_telegram_id = int(ctx["owner_telegram_id"])  # type: ignore[arg-type]
    use_heavy = bool(ctx["use_heavy"])

    # ── NL Programming: detect cron-like phrases → BackgroundGoal ──
    if await _maybe_schedule_nl_goal(raw, message, owner_telegram_id, session=session):
        return

    now_local_str = now_in_tz(tz_name).strftime("%Y-%m-%d %H:%M")
    history_block = await ctx_store.render_history_block(message.from_user.id)

    # ── Stage -2: Trie/Aho-Corasick classifier (additive, pre-pipeline) ──
    # Fast O(n) classification to skip expensive LLM checks for trivial messages.
    # Does NOT replace pre_gate — it augments it by catching more patterns.
    if settings.classifier_enabled:
        try:
            classification = _classify_message(raw)
            logger.debug("Classifier result: %s", classification)
        except Exception:
            logger.debug("Classifier failed, proceeding normally", exc_info=True)
            classification = None
    else:
        classification = None

    # ── Classifier fast-path: greeting/trivial/farewell → pre_gate response ──
    if classification and (
        classification.get("greeting")
        or classification.get("farewell")
        or (classification.get("trivial") and not classification.get("command"))
    ):
        try:
            from src.core.intelligence.pre_gate import check_pre_gate

            gate_resp = check_pre_gate(raw)
            if gate_resp:
                await message.answer(sanitize_html(gate_resp))
                # Record turn
                try:
                    from src.core.memory.session_recorder import record_turn

                    async with get_session() as rec_session:
                        await record_turn(
                            rec_session, message.from_user.id, "user", raw[:100]
                        )
                        await record_turn(
                            rec_session,
                            message.from_user.id,
                            "assistant",
                            gate_resp[:100],
                        )
                except Exception:
                    logger.debug("Failed to record classifier gate turn", exc_info=True)
                _fire_record_trajectory(
                    owner_telegram_id,
                    request_text=raw,
                    route_mode="classifier_gate",
                    intent_json={
                        "intent": "greeting",
                        "classification": classification,
                    },
                    response_text=gate_resp,
                    success=True,
                    latency_ms=int((time.monotonic() - turn_started) * 1000),
                )
                return
        except Exception:
            logger.debug("Classifier fast-path failed, continuing", exc_info=True)

    # ── INSTANT bypass: пропускаем Stage 0-3 для мгновенных ответов ──
    # Срабатывает после классификатора, но ДО извлечения фактов и LLM.
    # Экономит токены и latency для сообщений типа «ага», «спс», «😂», «ок».
    if classification:
        classify_mode = _get_classify_mode(classification)
        if classify_mode == "INSTANT":
            # Edge-кейсы: не отвечаем мгновенно на URL, forwarded и @mention
            _has_url = bool(_URL_RE.search(raw))
            _is_forwarded = bool(
                message.forward_date
                or message.forward_from
                or message.forward_from_chat
                or message.forward_from_message_id
            )
            _has_mention = any(
                ent.type in ("mention", "text_mention")
                for ent in (message.entities or [])
            )

            if not _has_url and not _is_forwarded and not _has_mention:
                response = _instant_response(raw, classification, message)
                if response is not None:
                    await message.answer(response)

                    # Запись в историю диалога (best-effort)
                    try:
                        from src.core.memory.session_recorder import record_turn

                        async with get_session() as rec_session:
                            await record_turn(
                                rec_session,
                                message.from_user.id,
                                "user",
                                raw[:100],
                            )
                            await record_turn(
                                rec_session,
                                message.from_user.id,
                                "assistant",
                                response[:100],
                            )
                    except Exception:
                        logger.debug("Failed to record instant turn", exc_info=True)

                    _fire_record_trajectory(
                        owner_telegram_id,
                        request_text=raw,
                        route_mode="classifier_instant",
                        intent_json={
                            "intent": "instant",
                            "classification": classification,
                        },
                        response_text=response,
                        success=True,
                        latency_ms=int((time.monotonic() - turn_started) * 1000),
                    )

                    # Эмитим хук on_message_processed
                    try:
                        from src.core.infra.hooks import hooks

                        await hooks.emit(
                            "on_message_processed",
                            user_id=str(owner_telegram_id),
                            raw=raw[:200],
                            mode="instant",
                            response=response[:200],
                        )
                    except Exception:
                        logger.debug(
                            "hooks.emit failed for instant bypass", exc_info=True
                        )

                    return  # Полностью пропускаем Stage 0-3

    # ── Stage -1: Background fact extraction (enqueue) ───────────────
    # Без этого extract_and_save_memories() НЕ вызывается в main flow,
    # а значит supersedes evolution chains в Stage 0c не работают:
    # 5-минутное окно в check_contradiction_response остаётся пустым,
    # потому что новый факт физически не создаётся между двумя ходами
    # пользователя. pre_filter отсекает шумовые сообщения, чтобы не
    # тратить LLM-токены на «привет», «ок», «ага» и т.п.
    try:
        from src.core.memory._queue_core import MemoryJob, enqueue
        from src.core.memory.pre_filter import should_extract

        if should_extract(raw):
            await enqueue(
                MemoryJob(
                    telegram_id=owner_telegram_id,
                    messages_text=raw,
                    job_type="extract",
                    source="chat",
                )
            )
            # ---- Phase 2: record pre-filter accept ----
            try:
                from src.core.memory.memory_metrics import memory_metrics

                await memory_metrics.record_pre_filter(accepted=True)
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
        else:
            # ---- Phase 2: record pre-filter reject ----
            try:
                from src.core.memory.memory_metrics import memory_metrics

                await memory_metrics.record_pre_filter(accepted=False)
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
    except Exception:
        logger.debug("Background extract enqueue failed", exc_info=True)

    # ── Stage 0: Smart emoji/sticker replies ─────────────────────────
    from src.core.contacts.smart_reply import get_simple_reply

    emoji_reply = get_simple_reply(raw)
    if emoji_reply:
        await message.answer(emoji_reply)
        # Сохраняем в историю диалога
        try:
            from src.core.memory.session_recorder import record_turn

            async with get_session() as rec_session:
                await record_turn(rec_session, message.from_user.id, "user", raw[:100])
                await record_turn(
                    rec_session, message.from_user.id, "assistant", emoji_reply[:100]
                )
        except Exception:
            logger.debug("Failed to record smart_reply turn", exc_info=True)
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="smart_reply",
            intent_json={"intent": "smart_reply"},
            response_text=emoji_reply,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # ── Stage 0b: Memory correction detection (Feature 2) ────────────
    from src.core.contacts.smart_reply import (
        detect_memory_correction,
        handle_memory_correction,
    )

    correction = detect_memory_correction(raw)
    if correction:
        response = await handle_memory_correction(correction, owner_telegram_id)

        # ── Humanizer feedback loop ───────────────────────────────
        # Если пользователь поправляет бота — последний humanized-ответ
        # был отвергнут. Записываем фидбек.
        last_humanized = _pop_last_humanized(owner_telegram_id)
        if last_humanized:
            record_humanizer_feedback(
                user_id=owner_telegram_id,
                original=last_humanized,
                corrected=raw,
                accepted=False,
            )
        # ── End feedback loop ─────────────────────────────────────

        await message.answer(sanitize_html(response))
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="memory_correction",
            intent_json={"intent": "memory_correction", "action": correction["action"]},
            response_text=response,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # ── Stage 0c: Contradiction detection ────────────────────────────
    from src.core.memory.contradiction_detector import (
        check_contradiction_response,
        detect_contradiction,
        store_pending_contradiction,
    )

    # Check if this message is a response to a pending contradiction question
    cr_response = await check_contradiction_response(owner_telegram_id, raw)
    if cr_response:
        await message.answer(sanitize_html(cr_response))
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="contradiction_response",
            intent_json={"intent": "contradiction_response"},
            response_text=cr_response,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # Check for new contradictions against stored facts
    contradiction = await detect_contradiction(owner_telegram_id, raw)
    if contradiction:
        await store_pending_contradiction(owner_telegram_id, contradiction)
        await message.answer(
            sanitize_html(
                f"🤔 {contradiction['suggestion']}\n"
                f"(уверенность: {contradiction['confidence']:.0%})"
            )
        )
        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="contradiction",
            intent_json={"intent": "contradiction"},
            response_text=contradiction["suggestion"],
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # ── Stage 0d: Smart correction / cancellation detection ──────────
    from src.bot.handlers.smart_correction import (
        apply_correction,
        detect_correction,
    )

    correction = await detect_correction(owner_telegram_id, raw)
    if correction:
        reply = await apply_correction(owner_telegram_id, correction)
        await message.answer(sanitize_html(reply))

        # ── Learn from correction (Feature: Learning from Corrections) ──
        try:
            from src.core.intelligence.correction_learner import learn_correction

            if correction["action"] == "cancel":
                await learn_correction(
                    owner_telegram_id,
                    original_text="[cancelled]",
                    corrected_text="",
                    feedback_type="cancel",
                )
            elif correction["action"] == "replace":
                new_text = correction.get("new_text", "")
                is_fact = any(
                    w in (new_text or "").lower() for w in ("факт", "помню", "знаю")
                )
                await learn_correction(
                    owner_telegram_id,
                    original_text=raw,
                    corrected_text=new_text,
                    feedback_type="fact" if is_fact else "style",
                )
        except Exception:
            logger.debug(
                "Correction learner failed", exc_info=True
            )  # never break core flow

        _fire_record_trajectory(
            owner_telegram_id,
            request_text=raw,
            route_mode="smart_correction",
            intent_json={"intent": "smart_correction", "action": correction["action"]},
            response_text=reply,
            success=True,
            latency_ms=int((time.monotonic() - turn_started) * 1000),
        )
        return

    # ── Stage 0e: Singalong — подпевание строчками из песен (extracted to _singalong.py) ──
    if await _try_singalong(raw, message, owner_telegram_id, use_heavy, turn_started):
        return

    # Stage 1: Adaptive instructions
    if await check_instructions(raw, owner_telegram_id, message):
        return

    # Stage 1b: Contact-specific rules (e.g. "с Олей будь вежливее")
    if await check_contact_rules(raw, owner_telegram_id, message, userbot_manager):
        return

    # Stage 2: Adaptive persona
    if await check_persona(raw, owner_telegram_id, message):
        return

    # Stage 3: Follow-up context
    if await check_followup(
        raw,
        owner_telegram_id,
        message,
        state,  # type: ignore[arg-type]
        userbot_manager,
        tz_name,
        turn_started,
    ):
        return

    # Stage 4: Smart AutoRouter
    _last_purpose = None
    try:
        _last_purpose = await ctx_store.get_last_purpose(message.from_user.id)
    except Exception:
        logger.exception("failed to get last purpose")

    # S1-T1: получить prefetched recall результат если есть
    _prefetched_ctx: str | None = None
    if settings.prefetch_recall_enabled:
        try:
            from src.core.memory.prefetch_recall import get_prefetched_recall

            _pf_data = await get_prefetched_recall(owner_telegram_id)
            if _pf_data:
                _prefetched_ctx = _pf_data.get("memory_context", "") or None
        except Exception:
            logger.debug(
                "Prefetched recall unavailable", exc_info=True
            )  # prefetch — оптимизация

    # ── Progress: вспоминаем контекст перед планированием ─────────
    progress_msg = await message.answer("🧠 Вспоминаю контекст…")

    plan = await make_plan(
        raw,
        owner_telegram_id,
        heavy_available=use_heavy,
        last_purpose=_last_purpose,
        prefetched_context=_prefetched_ctx,
    )

    # ── Progress: план готов, думаем ───────────────────────────────
    try:
        await progress_msg.edit_text("💭 Думаю…")
    except TelegramAPIError:
        pass  # сообщение могло быть удалено

    if plan is None:
        return
    if plan.tasks:
        t0 = plan.tasks[0]
        logger.debug(
            "AutoRouter plan: risk=%s purpose=%s heavy=%s cache_ttl=%d agents=%s",
            t0.risk.value,
            t0.purpose.value,
            t0.heavy,
            t0.cache_ttl,
            t0.need_agents or "—",
        )

    # ── S2-T5: FAST_ROUTE shortcut — кэш-hit пропускает полный пайплайн ──
    _route_cache_hit = plan.metrics.get("route_cache_hit", False)
    if _route_cache_hit and plan.response_mode in ("instant", "fast_route"):
        logger.debug(
            "S2-T5 FAST_ROUTE shortcut: cache hit, mode=%s, skipping provider",
            plan.response_mode,
        )
        if plan.response_mode == "instant":
            await execute_instant(
                plan, message, raw, owner_telegram_id, turn_started, tz_name=tz_name
            )
        else:
            # FAST_ROUTE cache hit: pre_gate + humanize → send
            await execute_instant(
                plan, message, raw, owner_telegram_id, turn_started, tz_name=tz_name
            )
        return

    # ── Unified Dispatcher: single entry for all response modes ──
    if settings.use_unified_dispatcher:
        from src.bot.dispatcher import dispatcher

        if plan.response_mode == "instant" and plan.final_response:
            await dispatcher.dispatch(
                raw=raw,
                plan=plan,
                provider=None,  # instant doesn't need provider
                message=message,
                state=state,
                userbot_manager=userbot_manager,
                owner_telegram_id=owner_telegram_id,
                tz_name=tz_name,
                history_block=history_block,
                turn_started=turn_started,
            )
            return

    # Stage 5: INSTANT mode
    if plan.response_mode == "instant" and plan.final_response:
        await execute_instant(
            plan, message, raw, owner_telegram_id, turn_started, tz_name=tz_name
        )
        return

    # Stage 6: Build provider (Single session per request optimization)
    purpose = (
        plan.tasks[0].purpose.value if plan.tasks and plan.tasks[0].purpose else "main"
    )
    if session is None:
        async with get_session() as session:
            owner_db = await get_or_create_user(session, owner_telegram_id)
            if owner_db is None:
                await message.answer("⚠️ Внутренняя ошибка. Попробуй ещё раз.")
                return
            provider = await build_provider(
                session, owner_db, purpose=purpose, task_type=TaskType.DEFAULT
            )
            if provider is None and purpose != "main":
                logger.debug("No key for purpose '%s', falling back to main", purpose)
                provider = await build_provider(
                    session, owner_db, purpose="main", task_type=TaskType.DEFAULT
                )
    else:
        owner_db = await get_or_create_user(session, owner_telegram_id)
        if owner_db is None:
            await message.answer("⚠️ Внутренняя ошибка. Попробуй ещё раз.")
            return
        provider = await build_provider(
            session, owner_db, purpose=purpose, task_type=TaskType.DEFAULT
        )
        if provider is None and purpose != "main":
            logger.debug("No key for purpose '%s', falling back to main", purpose)
            provider = await build_provider(
                session, owner_db, purpose="main", task_type=TaskType.DEFAULT
            )

    if provider is None:
        await message.answer(
            "Чтобы я мог понимать свободный текст — добавь LLM-ключ в /settings → 🔑 API-ключи."
        )
        return

    # ── Smart Model Routing: переопределяем тяжёлую/лёгкую модель ──
    if settings.smart_routing_enabled and plan.model_mode:
        try:
            if plan.model_mode == "light":
                provider._default_heavy = False  # type: ignore[attr-defined]
                logger.debug("SmartRouter override: forcing LIGHT model")
            elif plan.model_mode == "heavy":
                provider._default_heavy = True  # type: ignore[attr-defined]
                logger.debug("SmartRouter override: forcing HEAVY model")
        except Exception:
            logger.debug("SmartRouter override failed", exc_info=True)

    # ── Unified Dispatcher: handle fast_route/maestro/full-pipeline ──
    if settings.use_unified_dispatcher and provider is not None:
        from src.bot.dispatcher import dispatcher

        injected_style: str | None = ctx.get("global_style_profile") or None  # type: ignore[assignment]

        # For maestro, we may need background task handling
        if plan.response_mode == "maestro" and state is not None:

            async def _run_maestro_via_dispatcher():
                try:
                    result = await dispatcher.dispatch(
                        raw=raw,
                        plan=plan,
                        provider=provider,
                        message=message,
                        state=state,
                        userbot_manager=userbot_manager,
                        owner_telegram_id=owner_telegram_id,
                        tz_name=tz_name,
                        history_block=history_block,
                        turn_started=turn_started,
                        injected_style=injected_style,
                    )
                    if not result.handled:
                        await _process_text_fallback(
                            raw,
                            provider,
                            message,
                            state,
                            userbot_manager,
                            tz_name,
                            owner_telegram_id,
                            history_block,
                            plan,
                            turn_started,
                            now_local_str,
                        )
                except asyncio.CancelledError:
                    logger.debug(
                        "Maestro via dispatcher cancelled for user %s",
                        owner_telegram_id,
                    )
                except Exception as e:
                    logger.exception(
                        "Maestro via dispatcher failed for user %s", owner_telegram_id
                    )
                    err_msg = safe_str(e)
                    if len(err_msg) > 300:
                        err_msg = err_msg[:300] + "…"
                    await message.answer(
                        sanitize_html(
                            f"❌ Ошибка при обработке запроса.\n\n"
                            f"<code>{err_msg}</code>\n\n"
                            "<i>Если ошибка повторяется — проверь ключ в /settings → 🔑 API-ключи "
                            "и модель в /settings → 🤖 LLM.</i>"
                        )
                    )
                finally:
                    async with _active_tasks_lock:
                        if _active_tasks.get(owner_telegram_id) is _my_task:
                            _active_tasks.pop(owner_telegram_id, None)

            task = asyncio.create_task(_run_maestro_via_dispatcher())
            track_ff(task)
            _my_task = task
            async with _active_tasks_lock:
                _active_tasks[owner_telegram_id] = _my_task
            await message.answer(_get_waiting_message())
            return

        # For fast_route / uncategorised: call directly (no background task needed)
        await dispatcher.dispatch(
            raw=raw,
            plan=plan,
            provider=provider,
            message=message,
            state=state,
            userbot_manager=userbot_manager,
            owner_telegram_id=owner_telegram_id,
            tz_name=tz_name,
            history_block=history_block,
            turn_started=turn_started,
            injected_style=injected_style,
        )
        # Character evolution: fire-and-forget
        track_ff(
            asyncio.create_task(
                maybe_evolve_after_turn(owner_telegram_id, raw, None, provider)
            )
        )
        return

    # Stage 7: FAST_ROUTE
    if plan.response_mode == "fast_route":
        if state is None:
            # Голосовой путь: FSMContext недоступен, fallback к route_intent
            logger.debug(
                "fast_route skipped: state is None (voice transcription path), "
                "falling back to route_intent for user %d",
                owner_telegram_id,
            )
            await _process_text_fallback(
                raw,
                provider,
                message,
                state,
                userbot_manager,
                tz_name,
                owner_telegram_id,
                history_block,
                plan,
                turn_started,
                now_local_str,
            )
            return
        await execute_fast_route(
            raw,
            plan,
            provider,
            message,
            state,  # type: ignore[arg-type]
            userbot_manager,
            tz_name,
            owner_telegram_id,
            history_block,
            turn_started,
            now_local_str,
        )
        # Character evolution: fire-and-forget (никогда не блокирует)
        track_ff(
            asyncio.create_task(
                maybe_evolve_after_turn(owner_telegram_id, raw, None, provider)
            )
        )
        return

    # Stage 8: MAESTRO — heavy tasks run as background tasks for preemption
    if plan.response_mode == "maestro":
        if state is None:
            # Голосовой путь: FSMContext недоступен, maestro не может работать
            logger.debug(
                "maestro skipped: state is None (voice transcription path), "
                "falling back to route_intent for user %d",
                owner_telegram_id,
            )
            await _process_text_fallback(
                raw,
                provider,
                message,
                state,
                userbot_manager,
                tz_name,
                owner_telegram_id,
                history_block,
                plan,
                turn_started,
                now_local_str,
            )
            return
        injected_style: str | None = ctx.get("global_style_profile") or None  # type: ignore[assignment]

        async def _run_maestro_background():
            _my_task = asyncio.current_task()
            try:
                mr = await execute_maestro(
                    raw,
                    plan,
                    provider,
                    message,
                    state,
                    userbot_manager,
                    tz_name,
                    owner_telegram_id,
                    history_block,
                    turn_started,
                    injected_style,
                )
                # Character evolution: fire-and-forget после ответа
                track_ff(
                    asyncio.create_task(
                        maybe_evolve_after_turn(owner_telegram_id, raw, None, provider)
                    )
                )
                if not mr.handled:
                    await _process_text_fallback(
                        raw,
                        provider,
                        message,
                        state,
                        userbot_manager,
                        tz_name,
                        owner_telegram_id,
                        history_block,
                        plan,
                        turn_started,
                        now_local_str,
                    )
            except asyncio.CancelledError:
                logger.debug("Maestro task cancelled for user %s", owner_telegram_id)
            except Exception as e:
                logger.exception(
                    "Maestro background task failed for user %s", owner_telegram_id
                )
                err_msg = safe_str(e)
                if len(err_msg) > 300:
                    err_msg = err_msg[:300] + "…"
                await message.answer(
                    sanitize_html(
                        f"❌ Ошибка при обработке запроса.\n\n"
                        f"<code>{err_msg}</code>\n\n"
                        "<i>Если ошибка повторяется — проверь ключ в /settings → 🔑 API-ключи "
                        "и модель в /settings → 🤖 LLM.</i>"
                    )
                )
            finally:
                async with _active_tasks_lock:
                    if _active_tasks.get(owner_telegram_id) is _my_task:
                        _active_tasks.pop(owner_telegram_id, None)

        task = asyncio.create_task(_run_maestro_background())
        track_ff(task)
        async with _active_tasks_lock:
            _active_tasks[owner_telegram_id] = task
        await message.answer(_get_waiting_message())
        return

    # ── Event Bus: emit user message received ──────────────────────────
    try:
        from src.core.events.event_bus import event_bus, USER_MESSAGE_RECEIVED

        await event_bus.emit(
            USER_MESSAGE_RECEIVED, user_id=owner_telegram_id, text_len=len(raw)
        )
    except Exception:
        logger.debug("EventBus emit failed for USER_MESSAGE_RECEIVED", exc_info=True)

    # Stage 9: Fallback — route_intent → _dispatch
    await _process_text_fallback(
        raw,
        provider,
        message,
        state,
        userbot_manager,
        tz_name,
        owner_telegram_id,
        history_block,
        plan,
        turn_started,
        now_local_str,
    )
    # Character evolution: fire-and-forget
    track_ff(
        asyncio.create_task(
            maybe_evolve_after_turn(owner_telegram_id, raw, None, provider)
        )
    )


# ── Re-registered handlers (implementations moved to sub-modules) ────────

router.message(F.voice | F.audio)(_voice_free_voice)
router.callback_query(F.data.startswith("voice_research:"))(_voice_cb_voice_research)
router.message(F.photo)(_media_handle_photo)
router.message(F.video_note | F.video)(_media_handle_video)
router.edited_message(OwnerOnly())(_media_handle_edited_message)
