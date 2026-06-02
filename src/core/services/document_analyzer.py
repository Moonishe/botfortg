"""Background document analysis via AgentOrchestrator.

Downloads document, extracts text, runs sub-agent analysis
in a fire-and-forget background task.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.core.infra.documents import extract_text as _extract_text
from src.core.infra.task_manager import track_ff
from src.core.intelligence.agent_orchestrator import AgentOrchestrator
from src.core.intelligence.auto_evolve import sanitize_for_prompt

logger = logging.getLogger(__name__)


async def analyze_document_background(
    orchestrator: AgentOrchestrator,
    owner_telegram_id: int,
    document_text: str,
    filename: str,
    peer_id: int | None = None,
) -> None:
    """Analyze document content in background via sub-agents.

    Does NOT block — fires a fire-and-forget task.
    Extracts: commitments, memory facts, key topics.
    """
    try:
        # Truncate if too long (context budget)
        if len(document_text) > 8000:
            document_text = document_text[:8000] + "\n...(truncated)"

        # Build a prompt with first 50 lines of content
        lines = document_text.split("\n")[:50]
        bullet_lines = "\n".join(f"* {line}" for line in lines)

        prompt = f"""Analyze this document: {sanitize_for_prompt(filename)}

{bullet_lines}

Extract:
1. Any commitments/deadlines mentioned
2. Key facts worth remembering
3. Main topics/summary (1-2 sentences)

Reply in Russian."""

        # Fire and forget — don't await
        track_ff(
            asyncio.create_task(
                _run_background_analysis(
                    orchestrator, owner_telegram_id, prompt, peer_id, filename
                )
            )
        )
    except Exception:
        logger.exception("Failed to start background document analysis")


async def _run_background_analysis(
    orchestrator: AgentOrchestrator,
    owner_telegram_id: int,
    prompt: str,
    peer_id: int | None,
    filename: str = "",
) -> None:
    """Execute document analysis via orchestrator delegate agent."""
    from src.db.repo import get_or_create_user
    from src.db.session import get_session
    from src.llm.base import TaskType
    from src.llm.router import build_provider

    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_telegram_id)
            provider = await build_provider(
                session, owner, purpose="main", task_type=TaskType.DEFAULT
            )

        if provider is None:
            logger.warning(
                "No provider available for background doc analysis (user %s)",
                owner_telegram_id,
            )
            return

        results, errors = await orchestrator.execute(
            agents_to_call=[
                {
                    "agent": "delegate",
                    "query": prompt,
                    "task": prompt,
                    "instructions": (
                        "Ты анализируешь содержимое документа. "
                        "Извлеки: обязательства/дедлайны, "
                        "ключевые факты, краткое саммари (1-2 предложения). "
                        "Ответ на русском, лаконично."
                    ),
                }
            ],
            provider=provider,
            owner_id=owner_telegram_id,
        )

        if results:
            for r in results:
                data = r.get("data", {})
                analysis = (
                    data.get("analysis", "") if isinstance(data, dict) else str(data)
                )
                if analysis:
                    logger.info(
                        "Background doc analysis done for user %s: %s",
                        owner_telegram_id,
                        analysis[:200],
                    )
                    # Notify user about analysis result
                    try:
                        from src.core.infra.notifier import notifier

                        if len(str(analysis)) > 10:
                            await notifier.notify(
                                f"📊 Анализ {filename}:\n{str(analysis)[:1500]}",
                            )
                    except Exception:
                        logger.exception("Failed to notify about analysis result")
                    return

        logger.info(
            "Background doc analysis completed (no text result) for user %s. "
            "Errors: %s",
            owner_telegram_id,
            errors or "none",
        )

    except Exception:
        logger.exception(
            "Background document analysis failed for user %s",
            owner_telegram_id,
        )


async def download_and_analyze(
    orchestrator: AgentOrchestrator,
    owner_telegram_id: int,
    file_path: Path,
    filename: str,
    peer_id: int | None = None,
) -> None:
    """Download a document, extract text, and analyze in background.

    Convenience wrapper — call from document handlers.
    """
    try:
        text = await _extract_text(file_path)
        if not text or not text.strip():
            logger.info(
                "Empty document text for %s (user %s)", filename, owner_telegram_id
            )
            return
        await analyze_document_background(
            orchestrator=orchestrator,
            owner_telegram_id=owner_telegram_id,
            document_text=text,
            filename=filename,
            peer_id=peer_id,
        )
    except Exception:
        logger.exception(
            "Failed to download_and_analyze %s for user %s",
            filename,
            owner_telegram_id,
        )
