"""Dreaming V3 — semantic re-evaluation of stale facts (LLM-driven).

OpenAI's "Dreaming V3" (2026) feature: background LLM re-evaluates facts that
have aged past their `validity_end` / `expires_at` and updates them semantically.

Example:
    "Пользователь планирует поездку в Сингапур в июле 2026" (created Jan 2026)
        → LLM in August 2026: "Пользователь съездил в Сингапур в июле 2026"
        → old fact is superseded, new fact is personal/contact_fact, not temporary

This module is the LLM-driven equivalent of TelegramHelper's math-only decay
in `memory_checker.py`.  It runs nightly inside the Dream Cycle (phase 3.5)
and selectively updates only:
  - memory_type='temporary' facts
  - confidence >= settings.dreaming_reval_confidence_threshold
  - NOT pinned
  - up to settings.dreaming_reval_max_per_run per run

Action types returned by LLM:
  - past:   event has happened — supersede with updated_fact, switch type
  - skip:   fact is still current — leave alone
  - invalid: fact is no longer relevant — deactivate (is_active=False)
  - permanent: fact should be promoted to long-term (decay → 0.01, type → personal)

Safety nets:
  - LLM errors fall back to "skip" (no destructive changes)
  - Empty / malformed / out-of-whitelist actions are dropped
  - Pinned facts are NEVER touched (user explicit opt-out)
  - New fact must pass add_memory() dedup (won't create duplicates)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.infra.key_guard import mask_keys, safe_str
from src.core.infra.text_sanitizer import sanitize_html
from src.core.security.prompt_injection_scanner import scan_content
from src.db.models._memory import Memory, MemoryLink

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

# Whitelist of allowed actions and memory_types — защита от LLM-галлюцинаций
_ALLOWED_ACTIONS: frozenset[str] = frozenset({"past", "skip", "invalid", "permanent"})
_ALLOWED_MEMORY_TYPES: frozenset[str] = frozenset(
    {"contact_fact", "personal", "relationship", "preference", "task", "general"}
)
_MAX_FACT_LEN = 500
_MIN_FACT_LEN = 3

# Module-level inflight-guard: prevents dream_cycle (cron) and
# /memory --reval (manual) from processing the same facts concurrently.
# Set is mutated only under the asyncio event loop's single-thread guarantee
# (no `await` between read-and-add in revaluation_run()).
_REVAL_INFLIGHT: set[int] = set()


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
    if new_type is not None and new_type not in _ALLOWED_MEMORY_TYPES:
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
        if not (3 <= len(updated_fact) <= _MAX_FACT_LEN):
            updated_fact = None
    return {
        "action": action,
        "updated_fact": updated_fact,
        "new_memory_type": new_type,
        "decay_rate": decay,
        "reason": str(parsed.get("reason") or "").strip()[:120],
    }


# ── DB helpers ─────────────────────────────────────────────────────


async def select_stale_facts_for_reval(
    session: AsyncSession,
    user_id: int,
    *,
    limit: int = 50,
    confidence_threshold: float = 0.5,
    lookback_days: int | None = None,
) -> list[Memory]:
    """Select active temporary facts older than 7 days with high confidence.

    Filters:
      - is_active=True
      - pinned=False
      - confidence >= confidence_threshold
      - memory_type IN ('temporary', 'task')  # task may also have temporal marker
      - created_at older than 7 days (don't reval fresh facts)
      - within lookback_days (don't touch very old facts — let auto-forget handle)

    Returns up to `limit` memories ordered by oldest-first.
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    cutoff_old = now - timedelta(days=7)
    cutoff_recent = now - timedelta(
        days=lookback_days if lookback_days is not None else 365
    )

    result = await session.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.is_active.is_(True),
            Memory.pinned.is_(False),
            Memory.confidence >= confidence_threshold,
            Memory.memory_type.in_(("temporary", "task")),
            Memory.created_at < cutoff_old,
            Memory.created_at > cutoff_recent,
        )
        .order_by(Memory.created_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def deactivate_memory(
    session: AsyncSession,
    memory_id: int,
    *,
    reason: str = "reval",
) -> None:
    """Mark a memory inactive. Used for action='invalid' and manual --correct.

    Sets is_active=False and updates updated_at. Does NOT delete — history
    is preserved for audit and undo.
    """
    mem = await session.get(Memory, memory_id)
    if not mem or mem.user_id is None:
        return
    mem.is_active = False
    mem.updated_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info("Deactivated memory %d (reason=%s)", memory_id, reason)


async def update_memory_text(
    session: AsyncSession,
    memory_id: int,
    new_fact: str,
    *,
    new_memory_type: str | None = None,
    new_decay_rate: float | None = None,
) -> Memory | None:
    """In-place update of fact text + optional type/decay. Used by /memory --correct.

    Also bumps embedding_hash so the dedup layer treats this as a new fact
    (prevents merge-back with the old version).
    """
    import hashlib

    new_fact = new_fact.strip()
    if not (3 <= len(new_fact) <= _MAX_FACT_LEN):
        return None
    mem = await session.get(Memory, memory_id)
    if not mem:
        return None
    mem.fact = new_fact
    mem.embedding_hash = hashlib.sha256(new_fact.lower().encode()).hexdigest()[:16]
    if new_memory_type is not None and new_memory_type in _ALLOWED_MEMORY_TYPES:
        mem.memory_type = new_memory_type
    if new_decay_rate is not None:
        mem.decay_rate = max(0.01, min(0.30, new_decay_rate))
    mem.updated_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info("Updated memory %d → new text len=%d", memory_id, len(new_fact))
    return mem


async def add_supersedes_link(
    session: AsyncSession,
    user_id: int,
    *,
    old_id: int,
    new_id: int,
    confidence: float = 1.0,
) -> MemoryLink | None:
    """Create a MemoryLink(old → new) of type 'supersedes'.

    No-op if the same link already exists (idempotency for re-runs).
    """
    if old_id == new_id:
        return None
    # Check existing
    existing_q = await session.execute(
        select(MemoryLink).where(
            MemoryLink.user_id == user_id,
            MemoryLink.source_id == old_id,
            MemoryLink.target_id == new_id,
            MemoryLink.relation_type == "supersedes",
        )
    )
    if existing_q.scalar_one_or_none() is not None:
        return None
    # NOTE: MemoryLink has no `source` column (introspection found only
    # id, user_id, source_id, target_id, weight, relation_type, created_at).
    # The logical source marker is carried in Memory.source of the target row.
    link = MemoryLink(
        user_id=user_id,
        source_id=old_id,
        target_id=new_id,
        relation_type="supersedes",
        weight=confidence,
    )
    session.add(link)
    await session.flush()
    logger.info(
        "Created supersedes link: %d → %d (source=dreaming_reval)", old_id, new_id
    )
    return link


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
    provider: "LLMProvider",
    fact: Memory,
    today: datetime | None = None,
) -> dict[str, Any] | None:
    """Call LLM to re-evaluate one fact. Returns parsed dict or None on error.

    Never raises — caller decides what to do with None (treat as skip).
    """
    today = today or datetime.now(timezone.utc)
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
    user,
    fact: Memory,
    parsed: dict[str, Any] | None,
    *,
    vector_store_obj: Any = None,
) -> RevalResult:
    """Apply parsed LLM decision to a fact. Returns RevalResult with outcome.

    Actions:
      - past:       create new fact via add_memory(), supersede old
      - permanent:  create new fact via add_memory(), supersede old
      - invalid:    deactivate old fact
      - skip:       do nothing
      - parsed=None: treat as skip
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
        try:
            await deactivate_memory(session, fact.id, reason="reval_invalid")
        except Exception as exc:
            base.error = f"deactivate failed: {exc}"
        return base

    # past / permanent → create new fact and supersede old
    # Use explicit `is not None` (not `or`) so legitimate falsy values
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
    else:
        new_type = "personal" if (fact.contact_id is None) else "contact_fact"
    new_decay_raw = parsed.get("decay_rate")
    new_decay = new_decay_raw if new_decay_raw is not None else 0.02

    try:
        from src.db.repos.memory_repo import add_memory

        new_mem = await add_memory(
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
            # Deactivate old fact to keep recall clean
            await deactivate_memory(session, fact.id, reason="superseded_by_reval")
        elif new_mem and new_mem.id == fact.id:
            # Dedup caught it — fact already exists in some form. Leave alone.
            base.reason = (base.reason or "") + " [dedup: same fact exists]"
    except Exception as exc:
        logger.exception("apply_reval_result: add_memory failed for fact %d", fact.id)
        base.error = f"add_memory failed: {exc}"
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
    # Module-level set ensures only one reval per owner at a time.
    global _REVAL_INFLIGHT
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
        return await _reval_run_impl(
            owner_telegram_id,
            limit,
            confidence_threshold,
            vector_store_obj=vector_store_obj,
        )
    finally:
        _REVAL_INFLIGHT.discard(owner_telegram_id)


async def _reval_run_impl(
    owner_telegram_id: int,
    limit: int | None = None,
    confidence_threshold: float | None = None,
    vector_store_obj: Any = None,
) -> RevalBatchSummary:
    """Inner implementation of reval_run, called under inflight-guard."""
    summary = RevalBatchSummary()

    limit = limit if limit is not None else settings.dreaming_reval_max_per_run
    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.dreaming_reval_confidence_threshold
    )

    from src.db.repo import get_or_create_user
    from src.llm.router import build_provider
    from src.llm.base import TaskType

    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)

        # Build provider BEFORE selecting facts so we don't hold a session
        # open across a network call
        try:
            provider = await build_provider(session, owner, task_type=TaskType.MEMORY)
        except Exception as exc:
            logger.exception("Dreaming reval: build_provider failed: %s", safe_str(exc))
            return summary
        if provider is None:
            logger.warning("Dreaming reval: no LLM provider available, skipping")
            return summary

        # Select candidates
        facts = await select_stale_facts_for_reval(
            session,
            owner.id,
            limit=limit,
            confidence_threshold=threshold,
            lookback_days=getattr(settings, "dreaming_reval_lookback_days", None),
        )
        if not facts:
            logger.info("Dreaming reval: no stale facts to re-evaluate")
            try:
                await provider.close()
            except Exception:
                pass
            return summary

        summary.examined = len(facts)
        today = datetime.now(timezone.utc)

        # Semaphore limits concurrent LLM calls (future-proof if loop becomes
        # concurrent; harmless in sequential mode — documents intent).
        sem = asyncio.Semaphore(3)

        # Track results in the current uncommitted batch for rollback recovery
        batch_results: list[RevalResult] = []

        for i, fact in enumerate(facts):
            try:
                async with sem:
                    parsed = await reval_fact(provider, fact, today=today)
                result = await apply_reval_result(
                    session, owner, fact, parsed, vector_store_obj=vector_store_obj
                )
            except Exception as exc:
                logger.exception("Dreaming reval: unhandled error for fact %d", fact.id)
                result = RevalResult(
                    memory_id=fact.id,
                    original_fact=fact.fact,
                    action="skip",
                    error=safe_str(exc),
                )
                summary.errors += 1  # counted here only (see below)

            summary.results.append(result)
            batch_results.append(result)
            # Error already counted in the except block above; this guard
            # only exists to skip the action-counter elif chain below.
            if result.error:
                pass
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

            # Gentle rate limiting between LLM calls
            await asyncio.sleep(0.1)

            # Incremental commit every 10 facts — avoids losing all progress
            # if the session fails mid-batch.
            if (i + 1) % 10 == 0:
                try:
                    await session.commit()
                    batch_results.clear()
                except Exception:
                    logger.exception(
                        "Dreaming reval: incremental commit failed at fact %d/%d",
                        i + 1,
                        len(facts),
                    )
                    await session.rollback()
                    summary.errors += 1
                    # Revert counters for this batch's results (they were rolled back)
                    for rr in batch_results:
                        if rr.error:
                            summary.errors -= 1
                        elif rr.action == "past":
                            summary.past -= 1
                            if rr.new_memory_id:
                                summary.new_facts_created -= 1
                        elif rr.action == "permanent":
                            summary.permanent -= 1
                            if rr.new_memory_id:
                                summary.new_facts_created -= 1
                        elif rr.action == "invalid":
                            summary.invalid -= 1
                        else:
                            summary.skip -= 1
                    break  # stop — state is inconsistent, don't continue

        # Final commit at the end of the batch
        try:
            await session.commit()
        except Exception:
            logger.exception("Dreaming reval: commit failed")
            await session.rollback()

        # Invalidate recall cache so the user sees the updated facts
        try:
            from src.core.actions.stats_cache import invalidate
            from src.core.memory.memory_recall import bump_recall_version

            await invalidate("mem_")
            await bump_recall_version(owner.telegram_id)
        except Exception:
            pass

        try:
            await provider.close()
        except Exception:
            pass

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


# ── History / rollback for UI (--reval, rollback_all) ──────────────


async def recent_reval_results(owner_telegram_id: int, *, limit: int = 10) -> str:
    """Show recent memories created by Dreaming V3 (source='dreaming_reval').

    Used by /memory --reval "Подробнее" button.
    """
    from src.db.repo import get_or_create_user
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        result = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == owner.id,
                Memory.source == "dreaming_reval",
            )
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        facts = list(result.scalars().all())

    if not facts:
        return "🧠 Нет фактов, созданных через Dreaming V3."

    lines = [f"🧠 <b>Последние {len(facts)} переоценок Dreaming V3:</b>", ""]
    for f in facts:
        ts = f.created_at.strftime("%d.%m %H:%M") if f.created_at else "?"
        status = "✅ активен" if f.is_active else "🚫 деактивирован"
        # Sanitize LLM-generated fact text — Telegram parse_mode=HTML is in
        # effect; without escaping, LLM could inject <a>, <tg-spoiler>, etc.
        fact_text = sanitize_html((f.fact or "")[:120])
        lines.append(f"• <code>#{f.id}</code> [{ts}] {status}\n  <i>{fact_text}</i>")
    return "\n".join(lines)


async def rollback_recent_revals(owner_telegram_id: int, *, limit: int = 20) -> int:
    """Rollback recent dreaming_reval changes.

    1. Find Memory rows with source='dreaming_reval' and is_active=True
       (most recent first), up to `limit`.
    2. For each: deactivate it.
    3. Find supersedes MemoryLink (target=new_mem) and reactivate the source fact.
    4. Delete the MemoryLink (so the relationship is gone).

    Returns count of rolled-back revaluations (deactivated new facts).
    """
    from src.db.repo import get_or_create_user
    from src.db.session import get_session

    undone = 0
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)

        new_facts_q = await session.execute(
            select(Memory)
            .where(
                Memory.user_id == owner.id,
                Memory.source == "dreaming_reval",
                Memory.is_active.is_(True),
            )
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
        new_facts = list(new_facts_q.scalars().all())
        if not new_facts:
            return 0

        new_ids = [m.id for m in new_facts]

        # Find supersedes links pointing to these new facts.
        # NOTE: MemoryLink has no `source` column; we filter by target_id
        # (new_facts created by dreaming_reval) and relation_type.
        links_q = await session.execute(
            select(MemoryLink).where(
                MemoryLink.user_id == owner.id,
                MemoryLink.target_id.in_(new_ids),
                MemoryLink.relation_type == "supersedes",
            )
        )
        links = list(links_q.scalars().all())

        # Build map: new_id → old_id
        new_to_old: dict[int, int] = {int(l.target_id): int(l.source_id) for l in links}

        for new_fact in new_facts:
            new_fact.is_active = False
            new_fact.updated_at = datetime.now(timezone.utc)
            old_id = new_to_old.get(new_fact.id)
            if old_id is not None:
                old = await session.get(Memory, old_id)
                if old and old.user_id == owner.id:
                    old.is_active = True
                    old.updated_at = datetime.now(timezone.utc)
            undone += 1

        # Drop the supersedes links — they are no longer the truth
        for link in links:
            await session.delete(link)

        try:
            await session.commit()
        except Exception:
            logger.exception("rollback_recent_revals: commit failed")
            await session.rollback()
            return 0

    # Invalidate cache outside the session
    try:
        from src.core.actions.stats_cache import invalidate
        from src.core.memory.memory_recall import bump_recall_version

        await invalidate("mem_")
        await bump_recall_version(owner_telegram_id)
    except Exception:
        pass

    logger.info("rollback_recent_revals: undone=%d", undone)
    return undone
