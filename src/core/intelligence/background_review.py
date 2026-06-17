"""Background Self-Review — lightweight post-turn LLM analysis.

After each assistant response, forks a cheap LLM call (same provider, background task)
to decide whether to save a fact to memory, propose a skill update, or flag an error.

Non-blocking: all work happens via asyncio.create_task.  Graceful degradation:
any exception is silently logged and swallowed — the main dialog is never affected.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from src.llm.base import ChatMessage, TaskType

if TYPE_CHECKING:
    from src.llm.base import LLMProvider

logger = logging.getLogger(__name__)

# ── Lightweight prompt: <200 tokens, forces JSON-only response ──────────
_REVIEW_PROMPT = (
    "Review this exchange (return ONLY valid JSON, no markdown):\n\n"
    "User: {user_text}\n"
    "Assistant: {assistant_text}\n\n"
    '{{"action":"none|save|skill","fact":"...","skill_hint":"..."}}'
)

# Max chars to feed into the prompt (keeps token budget low)
_USER_MAX = 180
_ASSISTANT_MAX = 250


def _extract_json(raw: str) -> dict:
    """Extract the first JSON object from an LLM response string."""
    # Find outermost braces — handles nested {} in values
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or start >= end:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}


class BackgroundReviewer:
    """Lightweight post-turn self-review.

    Reuses the same provider (or builds a cheap background one) to ask
    a single yes/no/action question after every assistant response.
    """

    async def review_response(
        self,
        user_id: int,
        user_text: str,
        assistant_response: str,
        provider: LLMProvider | None = None,
    ) -> None:
        """Main entry point.  Schedules background review, never blocks.

        Args:
            user_id: Telegram user id (owner_telegram_id convention).
            user_text: Raw user message.
            assistant_response: Final assistant response (post-humanize).
            provider: Optional existing LLM provider.  If None, a cheap
                      background provider is built inside the task.
        """
        if not user_text.strip() or not assistant_response.strip():
            return

        # track_ff keeps a strong reference to the task — without it the
        # fire-and-forget coroutine is held only by a weakref inside asyncio
        # and can be garbage-collected mid-execution ("Task was destroyed but
        # it is pending!"), silently dropping the self-review.
        from src.core.infra.task_manager import track_ff

        track_ff(
            asyncio.create_task(
                self._review_and_learn(
                    user_id=user_id,
                    user_text=user_text,
                    assistant_response=assistant_response,
                    provider=provider,
                )
            )
        )

    # ── Core logic ─────────────────────────────────────────────────────
    async def _review_and_learn(
        self,
        user_id: int,
        user_text: str,
        assistant_response: str,
        provider: LLMProvider | None,
    ) -> None:
        """Ask LLM whether to save / update / flag.  Then act on it."""
        own_provider = False
        try:
            # 1. Ensure we have a provider
            if provider is None:
                provider = await self._build_light_provider(user_id)
                own_provider = True
            if provider is None:
                return  # no keys configured — silently skip

            # 2. Lightweight LLM call
            prompt = _REVIEW_PROMPT.format(
                user_text=user_text[:_USER_MAX],
                assistant_text=assistant_response[:_ASSISTANT_MAX],
            )
            raw = await provider.chat(
                [ChatMessage(role="user", content=prompt)],
                task_type=TaskType.BACKGROUND,
            )
            decision = _extract_json(raw)

            action = str(decision.get("action", "none")).lower()

            # 3. Act on the decision
            if action == "save":
                fact = str(decision.get("fact", "")).strip()
                if fact:
                    await self._save_memory(user_id, fact)
            elif action == "skill":
                hint = str(decision.get("skill_hint", "")).strip()
                await self._propose_skill_update(user_id, hint)

            # "none" and "error" are intentionally no-ops here —
            # error logging is handled by the outer exception handler.
        except asyncio.CancelledError:
            raise  # must propagate
        except Exception:
            logger.debug("BackgroundReviewer: review skipped", exc_info=True)
        finally:
            if own_provider and provider is not None:
                try:
                    await provider.close()
                except Exception:
                    logger.debug(
                        "BackgroundReviewer: failed to close provider",
                        exc_info=True,
                    )

    # ── Helpers ─────────────────────────────────────────────────────────
    async def _build_light_provider(self, user_id: int) -> LLMProvider | None:
        """Build a cheap background provider for the given user."""
        try:
            from src.db.session import get_session
            from src.db.repo import get_or_create_user
            from src.llm.provider_manager import build_provider

            async with get_session() as session:
                user = await get_or_create_user(session, user_id)
                return await build_provider(
                    session,
                    user,
                    purpose="background",
                    task_type=TaskType.BACKGROUND,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("BackgroundReviewer: build_provider failed", exc_info=True)
            return None

    async def _save_memory(self, user_id: int, fact: str) -> None:
        """Persist a fact to the user's memory store."""
        try:
            from src.db.session import get_session
            from src.db.repo import add_memory, get_or_create_user

            async with get_session() as session:
                user = await get_or_create_user(session, user_id)
                await add_memory(
                    session,
                    user,
                    fact=fact,
                    source="background_review",
                    confidence=0.45,
                    memory_tier=1,
                )
            logger.debug(
                "BackgroundReviewer: saved fact for user %d: %.80s",
                user_id,
                fact,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("BackgroundReviewer: save_memory failed", exc_info=True)

    async def _propose_skill_update(self, user_id: int, hint: str) -> None:
        """Trigger lightweight skill analysis for the user."""
        try:
            from src.core.intelligence.skills import propose_skills_from_analysis

            # tier="light" → cheap regex + medium-model pass (~500 tokens)
            await propose_skills_from_analysis(
                user_id,
                tier="light",
                force=False,
            )
            if hint:
                logger.info(
                    "BackgroundReviewer: skill hint from LLM: %s",
                    hint[:200],
                )
            logger.debug(
                "BackgroundReviewer: skill proposal triggered for %d (hint: %.80s)",
                user_id,
                hint,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "BackgroundReviewer: propose_skill_update failed",
                exc_info=True,
            )


# ── Module-level singleton ──────────────────────────────────────────────
background_reviewer = BackgroundReviewer()
