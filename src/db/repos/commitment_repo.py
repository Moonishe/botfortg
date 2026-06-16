"""Commitment repository — Commitment, PendingAction, PendingQuestion."""

from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timedelta, UTC

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    Commitment,
    PendingAction,
    PendingQuestion,
)

logger = logging.getLogger(__name__)

# ─── HMAC-подпись для callback_data ────────────────────────────────────

_PENDING_TTL_MINUTES = 10  # TTL по умолчанию для PendingAction


def _compute_action_signature(action: PendingAction) -> str:
    """Вычисляет unified HMAC-подпись для PendingAction.

    Единый формат для DB-route: action_key:id, user_id (DB id), verb,
    expires_at (UTC timestamp), payload_hash. Используется тот же алгоритм,
    что и в src.core.security.approval.
    """
    from src.core.security import approval

    payload = json.loads(action.payload)
    expires_at = (
        action.expires_at.replace(tzinfo=UTC).timestamp() if action.expires_at else None
    )
    return approval.compute_hmac(
        action_key=str(action.id),
        user_id=action.user_id,
        verb=action.verb,
        expires_at=expires_at,
        payload_hash=approval._hash_payload(payload),
    )


# ─── Pending Questions ─────────────────────────────────────────────────


async def add_pending_question(
    session: AsyncSession, owner_id: int, question: str
) -> None:
    """Queue a pending question with 24h TTL to prevent stale accumulation."""
    session.add(
        PendingQuestion(
            owner_id=owner_id,
            question=question,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    await session.flush()


async def get_pending_questions(session: AsyncSession, owner_id: int) -> list[str]:
    r = await session.execute(
        select(PendingQuestion.question)
        .where(PendingQuestion.owner_id == owner_id)
        .order_by(PendingQuestion.created_at)
    )
    result = list(r.scalars().all())
    # Delete after reading
    await session.execute(
        delete(PendingQuestion).where(PendingQuestion.owner_id == owner_id)
    )
    await session.flush()
    return result


# ─── Commitments ────────────────────────────────────────────────────────


async def add_commitment(
    session: AsyncSession,
    *,
    user_id: int,
    peer_id: int,
    peer_name: str | None,
    message_id: int | None,
    direction: str,
    text: str,
    deadline_at=None,
    source_memory_id: int | None = None,
) -> Commitment:
    c = Commitment(
        user_id=user_id,
        peer_id=peer_id,
        peer_name=peer_name,
        message_id=message_id,
        direction=direction,
        text=text,
        deadline_at=deadline_at,
        source_memory_id=source_memory_id,
    )
    session.add(c)
    await session.flush()
    return c


async def list_open_commitments(
    session: AsyncSession,
    user,
    *,
    direction: str | None = None,
    peer_id: int | None = None,
) -> list[Commitment]:
    query = select(Commitment).where(
        Commitment.user_id == user.id,
        Commitment.status == "open",
    )
    if direction:
        query = query.where(Commitment.direction == direction)
    if peer_id is not None:
        query = query.where(Commitment.peer_id == peer_id)
    query = query.order_by(
        Commitment.deadline_at.is_(None), Commitment.deadline_at.asc()
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def update_commitment_status(
    session: AsyncSession, commitment_id: int, status: str
) -> None:
    c = await session.get(Commitment, commitment_id)
    if c is not None:
        c.status = status
        await session.flush()


async def get_commitment(
    session: AsyncSession, commitment_id: int
) -> Commitment | None:
    return await session.get(Commitment, commitment_id)


async def get_commitment_by_source_memory(
    session: AsyncSession, user_id: int, source_memory_id: int
) -> Commitment | None:
    result = await session.execute(
        select(Commitment).where(
            Commitment.user_id == user_id,
            Commitment.source_memory_id == source_memory_id,
        )
    )
    return result.scalar_one_or_none()


# ─── Pending Actions ────────────────────────────────────────────────────


async def create_pending_action(
    session: AsyncSession,
    *,
    user_id: int,
    kind: str,
    payload: str,
    ttl_minutes: int = _PENDING_TTL_MINUTES,
    route: str = "db",
    verb: str = "send",
    risk: str = "low",
    human_summary: str | None = None,
) -> PendingAction:
    """Создаёт PendingAction с TTL и unified HMAC-подписью."""
    pa = PendingAction(
        user_id=user_id,
        kind=kind,
        payload=payload,
        route=route,
        verb=verb,
        risk=risk,
        human_summary=human_summary,
    )
    session.add(pa)
    await session.flush()  # получаем pa.id
    # Устанавливаем TTL и HMAC после flush (нужен id)
    pa.expires_at = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
    pa.hmac_signature = _compute_action_signature(pa)
    await session.flush()
    return pa


async def get_pending_action(
    session: AsyncSession, action_id: int, user
) -> PendingAction | None:
    """Получить PendingAction по id (без проверки TTL — для чтения/редактирования)."""
    result = await session.execute(
        select(PendingAction).where(
            PendingAction.id == action_id, PendingAction.user_id == user.id
        )
    )
    return result.scalar_one_or_none()


def verify_pending_action_hmac(action: PendingAction, signature: str) -> bool:
    """Проверяет unified HMAC-подпись из callback_data.

    Empty signatures are always rejected. No legacy fallback.
    """
    if not signature:
        return False
    if not action.payload:
        logger.warning(
            "verify_pending_action_hmac: empty payload for action_id=%d", action.id
        )
        return False
    try:
        expected = _compute_action_signature(action)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            "verify_pending_action_hmac: corrupt payload for action_id=%d: %s",
            action.id,
            exc,
        )
        return False
    return hmac.compare_digest(expected, signature)


def is_pending_action_expired(action: PendingAction) -> bool:
    """Проверяет, истёк ли срок действия PendingAction."""
    if action.expires_at is None:
        return False  # старые записи без TTL — считаем не истёкшими
    expires_at = action.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < datetime.now(UTC)


async def delete_pending_action(session: AsyncSession, action_id: int, user) -> None:
    pa = await session.get(PendingAction, action_id)
    if pa is not None and pa.user_id == user.id:
        await session.delete(pa)
        await session.flush()


async def cleanup_expired_actions(session: AsyncSession) -> int:
    """Удаляет просроченные PendingAction (старше TTL + 1 час).

    Возвращает количество удалённых.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=_PENDING_TTL_MINUTES + 60)
    result = await session.execute(
        delete(PendingAction).where(
            PendingAction.expires_at.is_not(None),
            PendingAction.expires_at < cutoff,
        )
    )
    count = result.rowcount
    if count:
        logger.info("cleanup_expired_actions: удалено %d просроченных", count)
    return count
