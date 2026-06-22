"""Dreaming V3 — semantic re-evaluation of stale facts (LLM-driven).

OpenAI's "Dreaming V3" (2026) feature: background LLM re-evaluates facts that
have aged past their ``validity_end`` / ``expires_at`` and updates them semantically.

Example:
    "Пользователь планирует поездку в Сингапур в июле 2026" (created Jan 2026)
        → LLM in August 2026: "Пользователь съездил в Сингапур в июле 2026"
        → old fact is superseded, new fact is personal/contact_fact, not temporary

This module is the LLM-driven equivalent of TelegramHelper's math-only decay
in ``memory_checker.py``.  It runs nightly inside the Dream Cycle (phase 3.5)
and selectively updates only:
  - ``memory_type='temporary'`` facts
  - ``confidence >= settings.dreaming_reval_confidence_threshold``
  - NOT pinned
  - up to ``settings.dreaming_reval_max_per_run`` per run

Action types returned by LLM:
  - ``past``:   event has happened — supersede with updated_fact, switch type
  - ``skip``:   fact is still current — leave alone
  - ``invalid``: fact is no longer relevant — deactivate (is_active=False)
  - ``permanent``: fact should be promoted to long-term (decay → 0.01, type → personal)

Safety nets:
  - LLM errors fall back to "skip" (no destructive changes)
  - Empty / malformed / out-of-whitelist actions are dropped
  - Pinned facts are NEVER touched (user explicit opt-out)
  - New fact must pass ``save_memory_single`` dedup (won't create duplicates)

DB helpers live in :mod:`src.core.memory.memory_admin`;
history / rollback functions live in :mod:`src.core.memory.dreaming_reval_history`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.infra.key_guard import mask_keys, safe_str
from src.core.infra.telemetry import start_span
from src.core.memory.memory_admin import (
    ALLOWED_MEMORY_TYPES,
    MAX_FACT_LEN,
    MIN_FACT_LEN,
    select_old_temporary_facts,
    deactivate_memory,
    add_supersedes_link,
)
from src.core.security.prompt_injection_scanner import scan_content
from src.db.models._memory import Memory
from src.db.models._base import User

if TYPE_CHECKING:
    from src.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# ── LLM system prompt ──────────────────────────────────────────────
# LLM decides the action; we apply the action with full validation.
_REVAL_SYSTEM = (
    "Ты — модуль семантической переоценки фактов в личной памяти ассистента.\n"
    "Твоя задача: для каждого факта решить, что с ним произошло с момента записи.\n"
    "Сегодняшняя дата указана в подсказке. Учитывай её строго.\n\n"
    "Правила:\n"
    "1. action='past' — событие, описанное в факте, УЖЕ произошло.\n"
    "   • Переформулируй факт в прошедшем времени на русском.\n"
    "   • Смени memory_type на 'contact_fact' или 'personal' "
    "(зависит от того, о ком факт).\n"
    "   • Поставь decay_rate=0.01-0.05 (долгосрочный факт).\n"
    "   • Если в факте была явная дата/срок — оставь их в updated_fact.\n"
    "2. action='skip' — факт всё ещё актуален, ничего не меняй.\n"
    "3. action='invalid' — факт устарел, больше не имеет смысла "
    "(например, ссылка на событие, которого не было).\n"
    "4. action='permanent' — факт стоит сохранить навсегда как долгосрочный.\n"
    "   • Смени memory_type на 'personal' или 'contact_fact'.\n"
    "   • Поставь decay_rate=0.01.\n\n"
    "Формат ответа — СТРОГО JSON без markdown-обёрток:\n"
    '{"action": "past|skip|invalid|permanent", '
    '"updated_fact": "<новый текст на русском, только для past/permanent>", '
    '"new_memory_type": "contact_fact|personal|relationship|preference|general", '
    '"decay_rate": <float 0.01-0.30>, '
    '"reason": "<кратко, 5-10 слов, почему именно это действие>"}\n\n'
    "Если факт уже сформулирован корректно — action='skip' и reason.\n"
    "Не выдумывай. Не добавляй новых деталей, которых не было в оригинале."
)

# Whitelist of allowed actions — защита от LLM-галлюцинаций
_ALLOWED_ACTIONS: frozenset[str] = frozenset({"past", "skip", "invalid", "permanent"})

# Module-level inflight-guard: prevents dream_cycle (cron) and
# /memory --reval (manual) from processing the same facts concurrently.
# Set is mutated only under the asyncio event loop's single-thread guarantee
# (no ``await`` between read-and-add in revaluation_run()).
# L6: asyncio однопоточный — .discard() и __contains__ атомарны на уровне
# Python (GIL защищает dict/set операции). Блокировка не требуется,
# H-N1: TOCTOU race fixed — asyncio.Lock() guards inflight check.
_REVAL_INFLIGHT: set[int] = set()
_REVAL_INFLIGHT_LOCK = asyncio.Lock()


@dataclass
class RevalResult:
    """One fact's revaluation outcome."""

    memory_id: int
    original_fact: str
    action: str  # past | skip | invalid | permanent
    updated_fact: str | None = None
    new_memory_type: str | None = None
    decay_rate: float | None = None
    reason: str = ""
    error: str | None = None
    new_memory_id: int | None = None  # populated when past/permanent creates new fact


@dataclass
class RevalBatchSummary:
    """Aggregated stats from one revaluation run."""

    examined: int = 0
    past: int = 0
    skip: int = 0
    invalid: int = 0
    permanent: int = 0
    errors: int = 0
    new_facts_created: int = 0
    results: list[RevalResult] = field(default_factory=list)


# ── JSON response parsing ──────────────────────────────────────────


def _parse_reval_response(text: str | None) -> dict[str, Any] | None:
    """Parse LLM JSON response. Returns None on parse/validation failure.

    Strips markdown fences defensively, validates action and memory_type
    against whitelists, coerces decay_rate to float in [0.01, 0.30].
    """
    if not text:
        return None
    text = text.strip()
    # Strip ```json ... ``` fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Dreaming reval: JSON parse failed: %r", mask_keys(text[:120]))
        return None
    if not isinstance(parsed, dict):
        return None
    action = parsed.get("action")
    if action not in _ALLOWED_ACTIONS:
        logger.warning("Dreaming reval: invalid action %r", action)
        return None
    new_type = parsed.get("new_memory_type")
    if new_type is not None and new_type not in ALLOWED_MEMORY_TYPES:
        # Silently drop unknown type — defaults to original
        new_type = None
    decay = parsed.get("decay_rate")
    if decay is not None:
        try:
            decay = float(decay)
            decay = max(0.01, min(0.30, decay))
        except (TypeError, ValueError):
            decay = None
    updated_fact = parsed.get("updated_fact")
    if updated_fact is not None:
        updated_fact = str(updated_fact).strip()
        if not (MIN_FACT_LEN <= len(updated_fact) <= MAX_FACT_LEN):
            updated_fact = None
    return {
        "action": action,
        "updated_fact": updated_fact,
        "new_memory_type": new_type,
        "decay_rate": decay,
        "reason": str(parsed.get("reason") or "").strip()[:120],
    }


# ── LLM call (one fact) ────────────────────────────────────────────


def _build_user_prompt(fact: Memory, today: datetime) -> str | None:
    """Build the per-fact LLM prompt with rich context.

    Returns None if the fact content is blocked by the content scanner
    (prompt injection detected) — caller should treat as skip.
    """
    # Scan fact content for prompt injection before passing to LLM
    scan_result = scan_content(fact.fact, "memory_fact")
    if scan_result.blocked:
        logger.warning(
            "Dreaming reval: fact %d blocked by content scanner, skipping",
            fact.id,
        )
        return None

    today_str = today.strftime("%Y-%m-%d")
    created_str = fact.created_at.strftime("%Y-%m-%d") if fact.created_at else "unknown"
    validity_end_str = (
        fact.validity_end.strftime("%Y-%m-%d")
        if getattr(fact, "validity_end", None)
        else "не указана"
    )
    expires_str = (
        fact.expires_at.strftime("%Y-%m-%d")
        if getattr(fact, "expires_at", None)
        else "не указан"
    )
    lines = [
        f"Сегодня: {today_str}",
        f"Факт создан: {created_str}",
        f"validity_end: {validity_end_str}",
        f"expires_at: {expires_str}",
        f"memory_type: {fact.memory_type or 'unknown'}",
        f"importance: {fact.importance:.2f}" if fact.importance is not None else "",
        "",
        f"Факт: {fact.fact}",
    ]
    return "\n".join(line for line in lines if line is not None)


async def reval_fact(
    provider: LLMProvider,
    fact: Memory,
    today: datetime | None = None,
) -> dict[str, Any] | None:
    """Call LLM to re-evaluate one fact. Returns parsed dict or None on error.

    Never raises — caller decides what to do with None (treat as skip).
    """
    today = today or datetime.now(UTC)
    user_prompt = _build_user_prompt(fact, today)
    if user_prompt is None:
        # Content scanner blocked the fact — treat as skip
        return None
    try:
        from src.llm.base import ChatMessage, TaskType

        raw = await provider.chat(
            [
                ChatMessage(role="system", content=_REVAL_SYSTEM),
                ChatMessage(role="user", content=user_prompt),
            ],
            task_type=TaskType.MEMORY,
        )
    except Exception as exc:  # provider/connection/json errors all funnel here
        logger.warning(
            "Dreaming reval: LLM call failed for fact %d: %s", fact.id, safe_str(exc)
        )
        return None
    return _parse_reval_response(raw)


# ── Apply reval result ─────────────────────────────────────────────


async def apply_reval_result(
    session: AsyncSession,
    user: User,
    fact: Memory,
    parsed: dict[str, Any] | None,
    *,
    vector_store_obj: Any = None,
) -> RevalResult:
    """Apply parsed LLM decision to a fact. Returns RevalResult with outcome.

    Actions:
      - ``past``:       create new fact via ``save_memory_single``, supersede old
      - ``permanent``:  create new fact via ``save_memory_single``, supersede old
      - ``invalid``:    deactivate old fact
      - ``skip``:       do nothing
      - ``parsed=None``: treat as skip
    """
    if parsed is None:
        return RevalResult(
            memory_id=fact.id,
            original_fact=fact.fact,
            action="skip",
            reason="LLM returned no parseable response",
        )

    action = parsed["action"]
    base = RevalResult(
        memory_id=fact.id,
        original_fact=fact.fact,
        action=action,
        updated_fact=parsed.get("updated_fact"),
        new_memory_type=parsed.get("new_memory_type"),
        decay_rate=parsed.get("decay_rate"),
        reason=parsed.get("reason", ""),
    )

    if action == "skip":
        return base

    if action == "invalid":
        # Шаг 1: деактивация — атомарная в рамках сессии вызывающего
        try:
            # Сохраняем версию в аудит-трейл перед деактивацией
            from src.db.repos.memory_repo import save_memory_version

            await save_memory_version(
                session,
                user,
                fact.id,
                fact.fact,
                edited_by="agent",
                reason="reval_invalid",
            )
            await deactivate_memory(
                session, fact.id, reason="reval_invalid", user_id=user.id
            )
        except Exception as exc:
            logger.exception(
                "apply_reval_result: invalid deactivate failed for fact %d", fact.id
            )
            await session.rollback()
            base.error = f"deactivate failed: {exc}"
        return base

    # past / permanent → create new fact and supersede old
    # (шаги 2-3: save_memory_single + supersedes_link + deactivate — атомарно
    # в рамках сессии вызывающего)
    # Use explicit ``is not None`` (not ``or``) so legitimate falsy values
    # (e.g. decay_rate=0.01, empty string) are preserved instead of
    # silently falling back to defaults.
    updated_raw = parsed.get("updated_fact")
    updated = (
        updated_raw
        if (isinstance(updated_raw, str) and updated_raw.strip())
        else fact.fact
    )

    # Scan updated fact content for prompt injection before persisting
    scan_result = scan_content(updated, "memory_fact")
    if scan_result.blocked:
        logger.warning(
            "Dreaming reval: updated fact blocked by content scanner "
            "for fact %d, treating as skip",
            fact.id,
        )
        base.error = "updated fact blocked by content scanner"
        return base

    new_type_raw = parsed.get("new_memory_type")
    if new_type_raw is not None:
        new_type = new_type_raw
        # Guard: LLM must not cross personal/contact_fact boundary via type rename.
        # ponytail: 2-line guard, upgrade to enum+validator if more types need coupling.
        if fact.contact_id is not None and new_type == "personal":
            new_type = "contact_fact"
        elif fact.contact_id is None and new_type == "contact_fact":
            new_type = "personal"
    else:
        new_type = "personal" if (fact.contact_id is None) else "contact_fact"
    new_decay_raw = parsed.get("decay_rate")
    new_decay = new_decay_raw if new_decay_raw is not None else 0.02

    try:
        # Атомарное создание нового факта + supersedes-связь + деактивация старого
        # в рамках сессии вызывающего. При ошибке откатываем всю сессию.
        from src.core.memory.memory_service import save_memory_single

        new_mem = await save_memory_single(
            session,
            user,
            fact=updated,
            contact_id=fact.contact_id,
            sentiment=fact.sentiment,
            source="dreaming_reval",
            confidence=max(0.6, fact.confidence or 0.5),
            memory_type=new_type,
            decay_rate=new_decay,
            pinned=False,
            deduplicate=True,
            vector_store_obj=vector_store_obj,
        )
        if new_mem and new_mem.id != fact.id:
            await add_supersedes_link(
                session,
                user.id,
                old_id=fact.id,
                new_id=new_mem.id,
            )
            base.new_memory_id = new_mem.id
            # Сохраняем версию в аудит-трейл перед деактивацией старого факта
            from src.db.repos.memory_repo import save_memory_version

            await save_memory_version(
                session,
                user,
                fact.id,
                fact.fact,
                edited_by="agent",
                reason="superseded_by_reval",
            )
            # Deactivate old fact to keep recall clean
            await deactivate_memory(
                session, fact.id, reason="superseded_by_reval", user_id=user.id
            )
        elif new_mem and new_mem.id == fact.id:
            # Dedup caught it — fact already exists in some form. Leave alone.
            base.reason = (base.reason or "") + " [dedup: same fact exists]"
    except Exception as exc:
        logger.exception(
            "apply_reval_result: save_memory_single failed for fact %d", fact.id
        )
        await session.rollback()
        base.error = f"save_memory_single failed: {exc}"
        return base

    return base


# ── Public entry point ─────────────────────────────────────────────


async def reval_run(
    owner_telegram_id: int,
    *,
    limit: int | None = None,
    confidence_threshold: float | None = None,
    vector_store_obj: Any = None,
) -> RevalBatchSummary:
    """Run one batch of LLM re-evaluations. Designed to be called from dream cycle.

    Returns aggregated summary. Never raises — every step is wrapped in try/except
    to keep the rest of the dream cycle running.
    """
    summary = RevalBatchSummary()
    if not getattr(settings, "dreaming_reval_enabled", True):
        logger.info("Dreaming reval: disabled in settings, skipping")
        return summary

    # Inflight-guard: dream_cycle (cron) and /memory --reval (manual) can both
    # call revaluation_run. Without coordination, the same facts would be
    # processed twice → duplicate supersedes links + wasted LLM budget.
    # Module-level set + asyncio.Lock to prevent TOCTOU race.
    async with _REVAL_INFLIGHT_LOCK:
        if owner_telegram_id in _REVAL_INFLIGHT:
            logger.info(
                "Dreaming reval: already in progress for owner %d, skipping",
                owner_telegram_id,
            )
            # Caller (cmd_memory / dream_cycle) surfaces this via summary.errors path;
            # no dedicated field needed — the warning line is enough signal.
            return summary
        _REVAL_INFLIGHT.add(owner_telegram_id)
    try:
        with start_span(
            "dreaming.reval_run",
            user_id=str(owner_telegram_id),
            limit=limit or settings.dreaming_reval_max_per_run,
        ):
            return await _reval_run_impl(
                owner_telegram_id,
                limit,
                confidence_threshold,
                vector_store_obj=vector_store_obj,
            )
    finally:
        async with _REVAL_INFLIGHT_LOCK:
            _REVAL_INFLIGHT.discard(owner_telegram_id)


async def _reval_run_impl(
    owner_telegram_id: int,
    limit: int | None = None,
    confidence_threshold: float | None = None,
    vector_store_obj: Any = None,
) -> RevalBatchSummary:
    """Inner implementation of reval_run, called under inflight-guard.

    Phase 1 (read-only, single short session): get owner, build provider with
    ``purpose="background"`` (uses router's Semaphore(3) instead of main's 2),
    fetch candidate facts.

    Phase 2 (parallel): ``asyncio.gather`` over all facts with a per-fact
    Semaphore bounding concurrent LLM calls. Each fact gets its own session
    for the apply step because AsyncSession is NOT safe for concurrent awaits.

    Per-fact session is safe: ``save_memory_single`` holds a per-user lock
    internally, so concurrent calls for the same owner serialize at insert,
    but the LLM calls themselves run in parallel (the actual bottleneck).

    Early termination: 5 consecutive errors triggers a stop event; remaining
    tasks short-circuit as ``action="skip"`` to avoid wasting LLM budget when
    the provider is down.
    """
    summary = RevalBatchSummary()

    limit = limit if limit is not None else settings.dreaming_reval_max_per_run
    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.dreaming_reval_confidence_threshold
    )
    concurrency = max(1, getattr(settings, "dreaming_reval_concurrency", 3))

    from src.db.repo import get_or_create_user
    from src.llm.router import build_provider
    from src.llm.base import TaskType

    from src.db.session import get_session

    # ── Phase 1: setup (owner + provider + facts) — single short session ──
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)

        # Build provider with purpose="background" so we use the router's
        # background Semaphore(3) — main's Semaphore(2) would cap us at 2
        # and the previous local Semaphore(3) was effectively a no-op.
        try:
            provider = await build_provider(
                session,
                owner,
                purpose="background",
                task_type=TaskType.MEMORY,
            )
        except Exception as exc:
            logger.exception("Dreaming reval: build_provider failed: %s", safe_str(exc))
            return summary
        if provider is None:
            logger.warning("Dreaming reval: no LLM provider available, skipping")
            return summary

        # Select candidates (read-only)
        try:
            facts = await select_old_temporary_facts(
                session,
                owner.id,
                limit=limit,
                confidence_threshold=threshold,
                lookback_days=getattr(settings, "dreaming_reval_lookback_days", None),
            )
        except Exception:
            logger.exception("Dreaming reval: select_old_temporary_facts failed")
            try:
                await provider.close()
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
            return summary

    # Phase 1 session closed; the LLM provider is shared and its chat()
    # internally acquires slots from the background Semaphore.

    if not facts:
        logger.info("Dreaming reval: no stale facts to re-evaluate")
        try:
            await provider.close()
        except Exception:
            logger.debug("Non-critical error", exc_info=True)
        return summary

    summary.examined = len(facts)
    today = datetime.now(UTC)

    # ── Phase 2: parallel processing with bounded concurrency ──
    sem = asyncio.Semaphore(concurrency)
    error_streak_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    error_streak = 0  # mutated only under error_streak_lock

    async def _tracked_process(fact: Memory) -> RevalResult:
        """Run _process_one_fact and track consecutive errors for early stop."""
        nonlocal error_streak
        if stop_event.is_set():
            return RevalResult(
                memory_id=fact.id,
                original_fact=fact.fact,
                action="skip",
                reason="early_termination_error_streak",
            )
        result = await _process_one_fact(
            fact,
            owner_id=owner.id,
            owner_telegram_id=owner_telegram_id,
            provider=provider,
            today=today,
            vector_store_obj=vector_store_obj,
            sem=sem,
        )
        async with error_streak_lock:
            if result.error:
                error_streak += 1
                if error_streak >= 5:
                    logger.warning(
                        "Dreaming reval: %d consecutive errors — stopping batch "
                        "early (processed %d/%d facts)",
                        error_streak,
                        len(summary.results),
                        len(facts),
                    )
                    stop_event.set()
            else:
                error_streak = 0
        return result

    # gather() with default return_exceptions=False: _process_one_fact catches
    # all internal exceptions, so an exception here means a programming bug
    # (e.g. asyncio.CancelledError) and should propagate.
    # L4: явный handler для CancelledError — чистим provider и пробрасываем дальше,
    # чтобы вызывающий код мог корректно обработать отмену.
    try:
        results: list[RevalResult] = await asyncio.gather(
            *[_tracked_process(f) for f in facts]
        )
    except asyncio.CancelledError:
        try:
            await provider.close()
        except Exception:
            logger.debug("Non-critical error", exc_info=True)
        raise
    except Exception:
        logger.exception("Dreaming reval: gather failed")
        try:
            await provider.close()
        except Exception:
            logger.debug("Non-critical error", exc_info=True)
        return summary

    # ── Phase 3: aggregate counters ──
    for result in results:
        summary.results.append(result)
        if result.error:
            summary.errors += 1
        elif result.action == "past":
            summary.past += 1
            if result.new_memory_id:
                summary.new_facts_created += 1
        elif result.action == "permanent":
            summary.permanent += 1
            if result.new_memory_id:
                summary.new_facts_created += 1
        elif result.action == "invalid":
            summary.invalid += 1
        else:
            summary.skip += 1

    # ── Phase 4: cleanup ──
    try:
        await provider.close()
    except Exception:
        logger.warning("Dreaming reval: provider close failed", exc_info=True)

    # Invalidate recall cache so the user sees the updated facts.
    # Done after provider.close() because the cache is independent.
    try:
        from src.core.actions.stats_cache import invalidate
        from src.core.memory.memory_recall import bump_recall_version

        await invalidate("mem_")
        await bump_recall_version(owner_telegram_id)
    except Exception:
        logger.warning(
            "Dreaming reval: recall cache invalidation failed", exc_info=True
        )

    logger.info(
        "Dreaming reval: examined=%d past=%d permanent=%d invalid=%d skip=%d errors=%d",
        summary.examined,
        summary.past,
        summary.permanent,
        summary.invalid,
        summary.skip,
        summary.errors,
    )
    return summary


async def _process_one_fact(
    fact: Memory,
    *,
    owner_id: int,
    owner_telegram_id: int,
    provider: LLMProvider,
    today: datetime,
    vector_store_obj: Any = None,
    sem: asyncio.Semaphore,
) -> RevalResult:
    """Process a single fact: LLM reval + apply result in its own session.

    Two phases:
      1. LLM call (network-bound, parallel-safe, bounded by ``sem``).
      2. DB apply in a fresh ``get_session()`` context (auto-commits on exit).

    Per-fact session is required: ``AsyncSession`` is NOT safe for concurrent
    awaits on the same instance.  Concurrent ``save_memory_single`` calls for the
    same owner serialize internally via the per-user lock from
    ``_get_user_lock(user.id)`` — see ``src/db/repos/memory_repo.py:307``.

    Never raises: every step is wrapped in try/except and returns a
    ``RevalResult`` with ``error`` populated on failure.
    """
    from src.db.repo import get_or_create_user
    from src.db.session import get_session

    # Phase 1: LLM call (bounded by semaphore)
    try:
        async with sem:
            parsed = await reval_fact(provider, fact, today=today)
    except Exception as exc:
        logger.exception("reval_fact failed for fact %d", fact.id)
        return RevalResult(
            memory_id=fact.id,
            original_fact=fact.fact,
            action="skip",
            error=safe_str(exc),
        )

    if parsed is None:
        return RevalResult(
            memory_id=fact.id,
            original_fact=fact.fact,
            action="skip",
            reason="LLM returned no parseable response",
        )

    # Phase 2: apply result in a fresh session
    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_telegram_id)
            result = await apply_reval_result(
                session,
                owner,
                fact,
                parsed,
                vector_store_obj=vector_store_obj,
            )
            # session auto-commits on context exit (see src/db/session.py:289)
    except Exception as exc:
        logger.exception("apply_reval_result failed for fact %d", fact.id)
        return RevalResult(
            memory_id=fact.id,
            original_fact=fact.fact,
            action="skip",
            error=safe_str(exc),
        )

    return result


def reval_summary_text(summary: RevalBatchSummary) -> str:
    """Format summary for /memory --reval and notifications."""
    if summary.examined == 0:
        return "🧠✨ Dreaming V3: нет устаревших фактов для переоценки."
    lines = [
        "🧠 <b>Dreaming V3 — переоценка фактов</b>",
        f"📊 Проверено: {summary.examined}",
    ]
    if summary.past:
        lines.append(f"✅ Произошло (обновлено): {summary.past}")
    if summary.permanent:
        lines.append(f"♾ Сделано постоянным: {summary.permanent}")
    if summary.invalid:
        lines.append(f"🚫 Деактивировано: {summary.invalid}")
    if summary.skip:
        lines.append(f"⏭ Без изменений: {summary.skip}")
    if summary.errors:
        lines.append(f"⚠️ Ошибок: {summary.errors}")
    return "\n".join(lines)
