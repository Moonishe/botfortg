"""Dream cycle — unified nightly memory maintenance (Phase 5.6).

Replaces separate background tasks with a single orchestrated job:
  1. Decay + tier promotion/demotion (was memory_checker @ 03:00)
  2. Duplicate consolidation (was memory_consolidator @ every 6h)
  3. Contradiction detection (batch scan, was ad-hoc per-message)
  3.5. Dreaming V3 — LLM semantic re-evaluation of temporary facts
  4. Digest rebuild for top 20 active contacts (was on-access)
  5. DSM feed generation (auto-generated insights for user)
  6. Auto-forgetting / stale-closure of inactive facts
  7. Episodic reflection — meta-memory from recent interactions
  12. Mood tracking — sentiment change alerts for all contacts

Runs once per day at 03:00 UTC and sends a single summary notification.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, UTC

from src.config import settings
from src.db.session import get_session

logger = logging.getLogger(__name__)

# ── Защита от наложения (overlap guard) ──
# Если предыдущий запуск dream_cycle ещё не завершился — пропускаем тик,
# чтобы избежать двойной обработки и contention'а в БД.
_overlap_guard = asyncio.Lock()


async def dream_cycle(owner_telegram_id: int) -> None:
    """Run complete nightly memory maintenance.

    Executes all four phases sequentially.  Each phase is wrapped in its
    own try/except so a failure in one phase does not block the others.

    Phases:
        1. Decay + tier promotion/demotion
        2. Duplicate consolidation
        3. Contradiction detection (placeholder)
        4. Digest rebuild for active contacts
    """
    summary = {
        "decayed": 0,
        "closed": 0,
        "consolidated": 0,
        "contradictions": 0,
        "reval_examined": 0,
        "reval_past": 0,
        "reval_permanent": 0,
        "reval_invalid": 0,
        "reval_skip": 0,
        "reval_errors": 0,
        "digests": 0,
        "dsm": 0,
        "auto_forgotten": 0,
        "stale_closed": 0,
        "reflected_episodes": 0,
        "reflected_facts": 0,
        "meta_memory_updated": 0,
        "dreaming_candidates": 0,
        "dreaming_counterfactuals": 0,
        "dreaming_patterns": 0,
        "dreaming_integrated": 0,
        "dreaming_insights": 0,
        "dreaming_forgotten": 0,
        "mood_alert_details": [],
    }

    from src.db.repo import get_or_create_user

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)

        # ── Phase 1: Decay + tier promotion/demotion ──────────────────
        try:
            from src.core.memory.memory_checker import _run_decay_and_validation

            decayed, closed = await _run_decay_and_validation(owner_telegram_id)
            summary["decayed"] = decayed
            summary["closed"] = closed
            logger.info(
                "Dream cycle: phase 1 (decay) — %d decayed, %d closed",
                decayed,
                closed,
            )
        except Exception:
            logger.exception("Dream cycle: phase 1 (decay) failed")

        # ── Phase 2: Duplicate consolidation ──────────────────────────
        try:
            from src.core.memory.memory_consolidator import consolidate_memories

            merged = await consolidate_memories(owner_telegram_id)
            summary["consolidated"] = merged
            logger.info(
                "Dream cycle: phase 2 (consolidation) — %d merged",
                merged,
            )
        except Exception:
            logger.exception("Dream cycle: phase 2 (consolidation) failed")

        # ── Phase 3: Contradiction batch scan ──────────────────────────
        try:
            from src.core.memory.contradiction_detector import (
                _scan_contradictions_batch,
            )
            from src.db.repo import list_memories

            memories = await list_memories(session, owner, limit=200)
            contradictions = await _scan_contradictions_batch(
                memories,
                owner_telegram_id,
                session=session,
                owner=owner,
            )
            summary["contradictions"] = contradictions
            logger.info(
                "Dream cycle: phase 3 (contradictions) — %d found", contradictions
            )
            from src.core.memory.memory_recall import bump_recall_version

            await bump_recall_version(owner.telegram_id)
        except Exception:
            logger.exception("Dream cycle: phase 3 (contradictions) failed")
            summary["contradictions"] = 0

        # ── Phase 3.5: Dreaming V3 — LLM semantic re-evaluation ──────
        # Re-evaluates temporary/expired facts via LLM and supersedes them
        # with past-tense versions when the event has happened.
        # See src/core/memory/dreaming_reval.py for details.
        try:
            from src.core.memory.dreaming_reval import reval_run

            reval_summary = await reval_run(owner_telegram_id)
            summary["reval_examined"] = reval_summary.examined
            summary["reval_past"] = reval_summary.past
            summary["reval_permanent"] = reval_summary.permanent
            summary["reval_invalid"] = reval_summary.invalid
            summary["reval_skip"] = reval_summary.skip
            summary["reval_errors"] = reval_summary.errors
            if reval_summary.examined > 0:
                logger.info(
                    "Dream cycle: phase 3.5 (Dreaming V3 reval) — "
                    "examined=%d past=%d permanent=%d invalid=%d skip=%d errors=%d",
                    reval_summary.examined,
                    reval_summary.past,
                    reval_summary.permanent,
                    reval_summary.invalid,
                    reval_summary.skip,
                    reval_summary.errors,
                )
        except Exception:
            logger.exception("Dream cycle: phase 3.5 (Dreaming V3 reval) failed")

        # ── Phase 4: Digest rebuild for top 20 active contacts ────────
        try:
            from src.db.repo import list_contacts

            contacts = await list_contacts(session, owner, include_bots=False)
            # Non-bot, active (peer_id > 0), top 20
            active = [c for c in contacts if c.peer_id > 0][:20]

            from src.core.contacts.contact_memory_digest import get_contact_digest

            for contact in active:
                try:
                    await get_contact_digest(owner.telegram_id, contact.peer_id)
                    summary["digests"] += 1
                except Exception:
                    logger.debug("Non-critical error", exc_info=True)

            logger.info(
                "Dream cycle: phase 4 (digests) — %d rebuilt",
                summary["digests"],
            )

            # Also cleanup old conversation summaries (>7 days)
            try:
                from src.core.memory.conversation_context import cleanup_old_summaries

                await cleanup_old_summaries()
                logger.info("Dream cycle: cleaned up old conversation summaries")
            except Exception:
                logger.debug("Non-critical error", exc_info=True)
        except Exception:
            logger.exception("Dream cycle: phase 4 (digests) failed")

        # ── Phase 5: Memory Wiki ───────────────────────────────────────
        try:
            from src.core.memory.memory_wiki import generate_memory_wiki

            wiki_stats = await generate_memory_wiki(owner_telegram_id)
            total_facts = sum(wiki_stats.values())
            logger.info(
                "Dream cycle: wiki generated (%d categories, %d facts)",
                len(wiki_stats),
                total_facts,
            )
        except Exception:
            logger.warning("Dream cycle: wiki generation failed", exc_info=True)

        # ── Phase 6: DSM cleanup ───────────────────────────────────────
        try:
            from src.core.intelligence.dsm import dsm_cleanup

            removed = await dsm_cleanup(days=30)
            summary["dsm"] = removed
            if removed:
                logger.info(
                    "Dream cycle: phase 6 (DSM cleanup) — removed %d old entries",
                    removed,
                )
        except Exception:
            logger.exception("Dream cycle: phase 6 (DSM cleanup) failed")

        # ── Phase 7: Auto-forget sweep ─────────────────────────────────────
        try:
            from src.core.memory.auto_forget import auto_forget_sweep

            forgotten = await auto_forget_sweep(session, owner.id)
            if forgotten:
                await session.commit()
            summary["auto_forgotten"] = forgotten
            if forgotten:
                logger.info(
                    "Dream cycle: phase 7 (auto-forget) — %d facts deactivated",
                    forgotten,
                )
        except Exception:
            logger.exception("Dream cycle: phase 7 (auto-forget) failed")
            summary["auto_forgotten"] = 0

        # ── Phase 8: Close stale sessions ──────────────────────────────────
        try:
            from src.core.memory.session_recorder import close_stale_sessions

            stale_closed = await close_stale_sessions(session, max_age_hours=24)
            summary["stale_closed"] = stale_closed
            if stale_closed:
                logger.info(
                    "Dream cycle: phase 8 (stale sessions) — %d closed",
                    stale_closed,
                )
        except Exception:
            logger.exception("Dream cycle: phase 8 (stale sessions) failed")
            summary["stale_closed"] = 0

        # ── Phase 9: P3 Episodic Memory — Episode Reflection ──────────────
        # Перечитывает старые эпизоды и извлекает новые факты,
        # которые smart_extractor пропустил при первом проходе.
        if settings.episodic_memory_enabled and settings.episodic_reflect_enabled:
            try:
                from src.core.memory.episodic import reflect_on_episodes

                reflection_results = await reflect_on_episodes(owner_telegram_id)
                summary["reflected_episodes"] = len(reflection_results)
                summary["reflected_facts"] = sum(
                    r.get("new_facts", 0) for r in reflection_results
                )
                if reflection_results:
                    logger.info(
                        "Dream cycle: phase 9 (episode reflection) — "
                        "%d episodes, %d new facts",
                        summary["reflected_episodes"],
                        summary["reflected_facts"],
                    )
                else:
                    logger.info(
                        "Dream cycle: phase 9 (episode reflection) — no new facts found"
                    )
            except Exception:
                logger.exception("Dream cycle: phase 9 (episode reflection) failed")
                summary["reflected_episodes"] = 0
                summary["reflected_facts"] = 0
        else:
            summary["reflected_episodes"] = 0
            summary["reflected_facts"] = 0

        # ── Phase 10: Meta-Memory — пересчёт importance ──────────────────
        # Ночной пересчёт importance всех фактов с учётом
        # confidence, source_quality, corroboration и времени.
        try:
            if getattr(settings, "meta_memory_enabled", True):
                from src.core.memory.meta_memory import recalculate_all_importance

                mm_updated = await recalculate_all_importance(owner.id)
                summary["meta_memory_updated"] = mm_updated
                if mm_updated:
                    logger.info(
                        "Dream cycle: phase 10 (meta-memory) — %d importance scores updated",
                        mm_updated,
                    )
        except Exception:
            logger.exception("Dream cycle: phase 10 (meta-memory) failed")
            summary["meta_memory_updated"] = 0

        # ── Phase 11: Dreaming Consolidator ───────────────────────────────
        # Ночное закрепление: контрфактуалы, абстракция паттернов,
        # генерация инсайтов, forgetting sweep.
        if settings.dreaming_consolidation_enabled:
            try:
                from src.core.learning.dreaming import dreaming_consolidator

                dc_result = await dreaming_consolidator.nightly_cycle(
                    owner_telegram_id, session, owner
                )
                summary["dreaming_candidates"] = dc_result.get("candidates", 0)
                summary["dreaming_counterfactuals"] = dc_result.get(
                    "counterfactuals", 0
                )
                summary["dreaming_patterns"] = dc_result.get("patterns", 0)
                summary["dreaming_integrated"] = dc_result.get("integrated", 0)
                summary["dreaming_insights"] = dc_result.get("insights", 0)
                summary["dreaming_forgotten"] = dc_result.get("forgotten", 0)
                if any(v > 0 for v in dc_result.values() if isinstance(v, int)):
                    logger.info(
                        "Dream cycle: phase 11 (dreaming) — "
                        "candidates=%d counterfactuals=%d patterns=%d "
                        "integrated=%d insights=%d forgotten=%d",
                        summary["dreaming_candidates"],
                        summary["dreaming_counterfactuals"],
                        summary["dreaming_patterns"],
                        summary["dreaming_integrated"],
                        summary["dreaming_insights"],
                        summary["dreaming_forgotten"],
                    )
            except Exception:
                logger.exception("Dream cycle: phase 11 (dreaming) failed")

        # ── Phase 12: Проверка настроения контактов ──────────────────
        try:
            from src.core.memory.mood_tracker import check_mood_alerts

            mood_alerts = await check_mood_alerts(owner_telegram_id)
            summary["mood_alert_details"] = mood_alerts
            if mood_alerts:
                logger.info(
                    "Dream cycle: phase 12 (mood) — %d предупреждений",
                    len(mood_alerts),
                )
        except Exception:
            logger.exception("Dream cycle: phase 12 (mood) failed")
            summary["mood_alert_details"] = []

        # ── Graph statistics ──────────────────────────────────────────────
        try:
            from src.db.repos.memory_repo import get_graph_stats

            graph_stats = await get_graph_stats(session, owner.id)
        except Exception:
            logger.exception("Dream cycle: graph stats failed")
            graph_stats = None

        # ── Retention statistics ──────────────────────────────────────────
        try:
            from src.core.memory.temporal_layers import compute_retention, utcnow_naive
            from src.db.repo import list_memories

            memories = await list_memories(session, owner, is_active=True)
            now = utcnow_naive()
            retention_buckets = {"strong": 0, "fading": 0, "weak": 0}
            for m in memories:
                retention = compute_retention(m, now)
                if retention >= 0.8:
                    retention_buckets["strong"] += 1
                elif retention >= 0.5:
                    retention_buckets["fading"] += 1
                else:
                    retention_buckets["weak"] += 1
        except Exception:
            logger.exception("Dream cycle: retention stats failed")
            retention_buckets = None

        # ── Summary notification ──────────────────────────────────────
        try:
            from src.core.scheduling.notification_queue import notification_queue

            # — Build summary lines (skip zero-value items) —
            summary_lines: list[str] = []

            # Decay + tier changes
            decayed = summary.get("decayed", 0)
            closed = summary.get("closed", 0)
            if decayed > 0 or closed > 0:
                parts = []
                if decayed > 0:
                    parts.append(f"📉 Обновлено {decayed} фактов (decay)")
                if closed > 0:
                    parts.append(f"закрыто {closed}")
                summary_lines.append("• " + ", ".join(parts))

            # Consolidation
            consolidated = summary.get("consolidated", 0)
            if consolidated > 0:
                summary_lines.append(f"• 🔗 Смержено {consolidated} дубликатов")

            # Digest rebuild
            digests = summary.get("digests", 0)
            if digests > 0:
                summary_lines.append(f"• 📰 Обновлены профили {digests} контактов")

            # Stale sessions
            stale_closed = summary.get("stale_closed", 0)
            if stale_closed > 0:
                summary_lines.append(f"• закрыто сессий: {stale_closed}")

            # Episode reflection (P3)
            reflected_episodes = summary.get("reflected_episodes", 0)
            reflected_facts = summary.get("reflected_facts", 0)
            if reflected_facts > 0:
                summary_lines.append(
                    f"• 📖 Рефлексия эпизодов: {reflected_facts} новых фактов "
                    f"из {reflected_episodes} эпизодов"
                )

            # Auto-forget
            auto_forgotten = summary.get("auto_forgotten", 0)
            if auto_forgotten > 0:
                summary_lines.append(
                    f"• авто-забывание: {auto_forgotten} фактов деактивировано"
                )

            # Dreaming V3 re-evaluation
            reval_past = summary.get("reval_past", 0)
            reval_permanent = summary.get("reval_permanent", 0)
            reval_invalid = summary.get("reval_invalid", 0)
            reval_examined = summary.get("reval_examined", 0)
            if reval_examined > 0:
                parts = []
                if reval_past:
                    parts.append(f"обновлено {reval_past}")
                if reval_permanent:
                    parts.append(f"сделано постоянными {reval_permanent}")
                if reval_invalid:
                    parts.append(f"деактивировано {reval_invalid}")
                if parts:
                    summary_lines.append(
                        f"• 🧠 Dreaming V3: {', '.join(parts)} (из {reval_examined})"
                    )
                else:
                    summary_lines.append(
                        f"• 🧠 Dreaming V3: проверено {reval_examined}, без изменений"
                    )

            # Retention stats
            if retention_buckets:
                rb = retention_buckets
                summary_lines.append(
                    f"• удержание: 🔒 strong {rb['strong']}, "
                    f"⏳ fading {rb['fading']}, "
                    f"📦 weak {rb['weak']}"
                )

            # Meta-Memory importance recalculation
            mm_updated = summary.get("meta_memory_updated", 0)
            if mm_updated > 0:
                summary_lines.append(
                    f"• 📊 Meta-Memory: пересчитана важность {mm_updated} фактов"
                )

            # Dreaming Consolidator (Phase 11)
            dreaming_insights = summary.get("dreaming_insights", 0)
            dreaming_patterns = summary.get("dreaming_patterns", 0)
            dreaming_integrated = summary.get("dreaming_integrated", 0)
            dreaming_forgotten = summary.get("dreaming_forgotten", 0)
            if (
                dreaming_insights > 0
                or dreaming_patterns > 0
                or dreaming_integrated > 0
            ):
                parts = []
                if dreaming_patterns > 0:
                    parts.append(f"абстрагировано {dreaming_patterns} паттернов")
                if dreaming_integrated > 0:
                    parts.append(f"интегрировано {dreaming_integrated}")
                if dreaming_insights > 0:
                    parts.append(f"{dreaming_insights} инсайтов")
                if dreaming_forgotten > 0:
                    parts.append(f"забыто {dreaming_forgotten} малоценных")
                if parts:
                    summary_lines.append(f"• 🌌 Dreaming: {', '.join(parts)}")

            # Mood alerts (Phase 12)
            mood_alerts_list: list[str] = summary.get("mood_alert_details", [])
            if mood_alerts_list:
                count = len(mood_alerts_list)
                summary_lines.append(f"• 😟 Проверка настроения: {count} предупрежд.")
                # Если предупреждений мало — показываем текст
                for alert_text in mood_alerts_list[:2]:
                    summary_lines.append(f"   ⚠️ {alert_text}")
                if count > 2:
                    summary_lines.append(f"   …и ещё {count - 2}")

            # Graph stats
            if graph_stats:
                gs = graph_stats
                ebt = gs.get("edges_by_type", {})
                supports = ebt.get("supports", 0)
                contradicts = ebt.get("contradicts", 0)
                related = ebt.get("related", 0)
                summary_lines.append(
                    f"📊 Граф: {gs['node_count']} узлов, "
                    f"{gs['total_edges']} рёбер "
                    f"(supports: {supports}, contradicts: {contradicts}, "
                    f"related: {related})"
                )

            # — Build final message —
            if not summary_lines:
                # Всё по нулям — короткое сообщение
                null_messages = [
                    "🌙 Ночь прошла спокойно, всё в порядке",
                    "✨ Всё чисто, ничего не требовалось",
                    "🌅 Тихая ночь, система в норме",
                ]
                text = random.choice(null_messages)
            else:
                titles = [
                    "🌙 Ночной цикл завершён",
                    "✨ Утренняя рутина выполнена",
                    "🌅 Процедуры на сегодня завершены",
                    "🔧 Фоновая обработка закончена",
                ]
                title = random.choice(titles)
                text = f"<b>{title}</b>\n" + "\n".join(summary_lines)

            await notification_queue.enqueue(
                topic="system",
                text=text,
                priority=3,  # PRIORITY_LOW — информационное
            )
        except Exception:
            logger.debug("Non-critical error", exc_info=True)

        # ── Proactive pings ────────────────────────────────────────────
        try:
            from src.core.scheduling.proactive_pings import generate_pings
            from src.llm.router import build_provider
            from src.llm.base import TaskType

            provider = await build_provider(
                session, owner, task_type=TaskType.BACKGROUND
            )
            if provider:
                pings = await generate_pings(owner, provider, session)
                for ping in pings:
                    await notification_queue.enqueue(
                        topic="proactive-ping",
                        text=f"💡 {ping}",
                        priority=5,  # PRIORITY_NORMAL
                    )
                    await asyncio.sleep(1)
                # Очищаем pending-вопросы после того как они включены в пинги
                from src.core.memory.pending_questions import delete_pending_questions

                await delete_pending_questions(owner.telegram_id)
        except Exception:
            logger.debug("Proactive pings skipped", exc_info=True)


async def dream_loop(owner_telegram_id: int) -> None:
    """Run dream cycle once per day at 03:00 UTC.

    Calculates sleep duration to the next 03:00 target, executes the
    cycle, then repeats.  On fatal error sleeps 1 hour before retry.
    """
    while True:
        now = datetime.now(UTC)
        # Calculate seconds until next 03:00 UTC
        next_run = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run = next_run + timedelta(days=1)
        wait_sec = (next_run - now).total_seconds()

        logger.info(
            "Dream cycle: sleeping %.0f seconds until %s",
            wait_sec,
            next_run.isoformat(),
        )
        await asyncio.sleep(wait_sec)

        # Защита от наложения: если предыдущий запуск ещё не завершён — пропускаем тик
        if _overlap_guard.locked():
            logger.warning(
                "Dream cycle: предыдущий запуск ещё не завершён, пропускаем тик"
            )
            continue

        try:
            async with _overlap_guard:
                await dream_cycle(owner_telegram_id)
        except Exception:
            logger.exception("Dream cycle: fatal error, retrying in 1 hour")
            await asyncio.sleep(3600)  # retry in 1 hour


# ── Auto-register with task manager on import ────────────────────
from functools import partial
from src.core.infra.task_manager import task_manager

task_manager.register(
    "dream-cycle",
    partial(dream_loop, settings.owner_telegram_id),
    restart_on_failure=True,
    restart_delay=60,
)
