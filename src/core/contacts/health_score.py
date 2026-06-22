"""Contact relationship health scoring — 0-100 metric."""

from __future__ import annotations

from datetime import datetime, UTC
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def get_contact_health(owner_id: int, peer_id: int) -> dict[str, Any]:
    """Returns {score, days_since_last, open_commitments, message_count, reply_ratio, status}"""
    results = await get_contacts_health_batch(owner_id, [peer_id])
    return results.get(peer_id, _default_health())


def _default_health() -> dict[str, Any]:
    return {
        "score": 0,
        "status": "🔴 проблемные",
        "days_since_last": 365,
        "open_commitments": 0,
        "message_count": 0,
        "reply_ratio": 0.0,
    }


def _compute_health_score(
    days_gap: float, open_count: int, msg_count: int, reply_ratio: float
) -> tuple[int, str]:
    """Pure scoring function — no DB access."""
    score = 100.0
    score -= min(days_gap / 7.0 * 10.0, 60.0)
    score -= min(open_count * 5.0, 20.0)
    if msg_count < 10:
        score -= 10.0
    if 0.3 <= reply_ratio <= 0.7:
        score += 10.0
    elif reply_ratio > 0.9:
        score -= 15.0
    elif reply_ratio < 0.1 and msg_count > 5:
        score -= 20.0

    score = max(0.0, min(100.0, score))
    score_int = round(score)

    if score_int >= 80:
        status = "🟢 здоровые"
    elif score_int >= 50:
        status = "🟡 требуют внимания"
    else:
        status = "🔴 проблемные"

    return score_int, status


async def get_contacts_health_batch(
    owner_id: int, peer_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """Batch health scoring for multiple contacts — single DB round-trip.

    Returns dict mapping peer_id → {score, status, days_since_last,
    open_commitments, message_count, reply_ratio}.
    """
    from src.db.session import get_session
    from src.db.repo import get_or_create_user

    if not peer_ids:
        return {}

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id, use_cache=True)
        from sqlalchemy import select, func
        from src.db.models import Commitment, Message

        peer_set = set(peer_ids)

        # Single query: last message date per peer
        last_msg_rows = (
            await session.execute(
                select(
                    Message.peer_id,
                    func.max(Message.date).label("last_date"),
                )
                .where(
                    Message.user_id == owner.id,
                    Message.peer_id.in_(peer_set),
                )
                .group_by(Message.peer_id)
            )
        ).all()
        last_date_map: dict[int, datetime] = {
            row.peer_id: (
                row.last_date.replace(tzinfo=UTC)
                if row.last_date and row.last_date.tzinfo is None
                else row.last_date
            )
            for row in last_msg_rows
            if row.last_date is not None
        }

        # Single query: message counts per peer
        msg_count_rows = (
            await session.execute(
                select(
                    Message.peer_id,
                    func.count().label("msg_count"),
                )
                .where(
                    Message.user_id == owner.id,
                    Message.peer_id.in_(peer_set),
                )
                .group_by(Message.peer_id)
            )
        ).all()
        msg_count_map: dict[int, int] = {
            row.peer_id: row.msg_count for row in msg_count_rows
        }

        # Single query: outgoing message counts per peer
        outgoing_rows = (
            await session.execute(
                select(
                    Message.peer_id,
                    func.count().label("outgoing"),
                )
                .where(
                    Message.user_id == owner.id,
                    Message.peer_id.in_(peer_set),
                    Message.is_outgoing.is_(True),
                )
                .group_by(Message.peer_id)
            )
        ).all()
        outgoing_map: dict[int, int] = {
            row.peer_id: row.outgoing for row in outgoing_rows
        }

        # Single query: open commitments count per peer (batch, no N+1)
        commitments_map: dict[int, int] = {}
        if peer_set:
            cmt_rows = (
                await session.execute(
                    select(
                        Commitment.peer_id,
                        func.count(Commitment.id).label("open_count"),
                    )
                    .where(
                        Commitment.user_id == owner.id,
                        Commitment.peer_id.in_(peer_set),
                        Commitment.status == "open",
                    )
                    .group_by(Commitment.peer_id)
                )
            ).all()
            commitments_map = {row.peer_id: row.open_count for row in cmt_rows}

    now = datetime.now(UTC)
    result: dict[int, dict[str, Any]] = {}
    for peer_id in peer_ids:
        last_date = last_date_map.get(peer_id)
        if last_date:
            days_gap = max(0, (now - last_date).days)
        else:
            days_gap = 365

        msg_count = msg_count_map.get(peer_id, 0)
        outgoing = outgoing_map.get(peer_id, 0)
        open_count = commitments_map.get(peer_id, 0)
        reply_ratio = outgoing / max(msg_count, 1)

        score_int, status = _compute_health_score(
            days_gap, open_count, msg_count, reply_ratio
        )

        result[peer_id] = {
            "score": score_int,
            "status": status,
            "days_since_last": days_gap,
            "open_commitments": open_count,
            "message_count": msg_count,
            "reply_ratio": reply_ratio,
        }

    return result
