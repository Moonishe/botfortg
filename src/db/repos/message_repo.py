"""Message repository — Message, ConversationState, AutoReplyLog, TranscriptionCache."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    AutoReplyLog,
    ConversationState,
    Message,
    MessageReaction,
    TranscriptionCache,
)

logger = logging.getLogger(__name__)


async def list_active_conversations(
    session: AsyncSession, user, status: str = "active", limit: int = 50
) -> list[ConversationState]:
    # Filter: only 1:1 private chats with real humans (no groups/channels/bots)
    # INNER JOIN: Contact MUST exist AND be peer_kind='user', is_bot=False.
    # This excludes groups/channels/bots that have Contact records with peer_kind='chat'/'channel'.
    from sqlalchemy import and_
    from src.db.models import Contact

    result = await session.execute(
        select(ConversationState)
        .join(
            Contact,
            and_(
                Contact.user_id == ConversationState.user_id,
                Contact.peer_id == ConversationState.peer_id,
                Contact.peer_kind == "user",
                Contact.is_bot.is_(False),
            ),
        )
        .where(
            ConversationState.user_id == user.id,
            ConversationState.status == status,
        )
        .order_by(ConversationState.last_incoming_at.desc().nullslast())
        .limit(limit)
    )
    return list(result.scalars().all())


async def upsert_message(
    session: AsyncSession,
    *,
    user_id: int,
    peer_id: int,
    message_id: int,
    sender_id: int | None,
    sender_name: str | None,
    is_outgoing: bool,
    date,
    kind: str,
    text: str | None,
    transcript: str | None = None,
    media_path: str | None = None,
    extracted_text: str | None = None,
) -> None:
    stmt = sqlite_insert(Message).values(
        user_id=user_id,
        peer_id=peer_id,
        message_id=message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        is_outgoing=is_outgoing,
        date=date,
        kind=kind,
        text=text,
        transcript=transcript,
        media_path=media_path,
        extracted_text=extracted_text,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "peer_id", "message_id"],
        set_={
            "text": stmt.excluded.text,
            "transcript": func.coalesce(stmt.excluded.transcript, Message.transcript),
            "extracted_text": func.coalesce(
                stmt.excluded.extracted_text, Message.extracted_text
            ),
            "media_path": func.coalesce(stmt.excluded.media_path, Message.media_path),
            "kind": stmt.excluded.kind,
            "sender_name": func.coalesce(
                stmt.excluded.sender_name, Message.sender_name
            ),
        },
    )
    await session.execute(stmt)


async def fetch_chat_messages(
    session: AsyncSession,
    user,
    peer_id: int,
    limit: int = 50,
) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(Message.user_id == user.id, Message.peer_id == peer_id)
        .order_by(Message.date.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def count_messages(
    session: AsyncSession,
    user,
    peer_id: int,
) -> int:
    """Return message count for a chat with peer_id for the user."""
    result = await session.execute(
        select(func.count())
        .select_from(Message)
        .where(Message.user_id == user.id, Message.peer_id == peer_id)
    )
    return result.scalar_one()


async def fetch_my_messages_in_chat(
    session: AsyncSession,
    user,
    peer_id: int,
    limit: int = 100,
) -> list[Message]:
    result = await session.execute(
        select(Message)
        .where(
            Message.user_id == user.id,
            Message.peer_id == peer_id,
            Message.is_outgoing.is_(True),
        )
        .order_by(Message.date.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def get_cached_transcript(session: AsyncSession, file_id: str) -> str | None:
    row = await session.get(TranscriptionCache, file_id)
    return row.text if row else None


async def cache_transcript(
    session: AsyncSession,
    file_id: str,
    text: str,
    duration_seconds: float | None = None,
) -> None:
    existing = await session.get(TranscriptionCache, file_id)
    if existing is None:
        session.add(
            TranscriptionCache(
                file_id=file_id, text=text, duration_seconds=duration_seconds
            )
        )
    else:
        existing.text = text
        existing.duration_seconds = duration_seconds
    await session.flush()


async def add_auto_reply_log(
    session: AsyncSession,
    *,
    user_id: int,
    peer_id: int,
    peer_name: str | None,
    incoming_text: str | None,
    reply_text: str,
) -> None:
    session.add(
        AutoReplyLog(
            user_id=user_id,
            peer_id=peer_id,
            peer_name=peer_name,
            incoming_text=incoming_text,
            reply_text=reply_text,
        )
    )
    await session.flush()


async def list_recent_auto_replies(
    session: AsyncSession,
    user,
    *,
    limit: int = 10,
) -> list[AutoReplyLog]:
    result = await session.execute(
        select(AutoReplyLog)
        .where(AutoReplyLog.user_id == user.id)
        .order_by(AutoReplyLog.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def fetch_my_messages_global(
    session: AsyncSession,
    user,
    limit: int = 200,
) -> list[Message]:
    """Получить последние N исходящих сообщений владельца из всех чатов."""
    result = await session.execute(
        select(Message)
        .where(
            Message.user_id == user.id,
            Message.is_outgoing.is_(True),
            Message.text.isnot(None),
            Message.text != "",
        )
        .order_by(Message.date.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def upsert_conversation_state(
    session: AsyncSession,
    user,
    peer_id: int,
    *,
    status: str | None = None,
    increment_unread: bool = False,
    last_incoming_at=None,
    last_outgoing_at=None,
    last_auto_reply_at=None,
) -> ConversationState:
    """Создаёт или обновляет состояние диалога с контактом (atomic upsert)."""
    insert_data = {
        "user_id": user.id,
        "peer_id": peer_id,
        "status": status or "active",
        "unread_count": 1 if increment_unread else 0,
        "last_incoming_at": last_incoming_at,
        "last_outgoing_at": last_outgoing_at,
        "last_auto_reply_at": last_auto_reply_at,
    }
    update_data: dict = {}
    if status is not None:
        update_data["status"] = status
    if increment_unread:
        update_data["unread_count"] = ConversationState.unread_count + 1
    if last_incoming_at is not None:
        update_data["last_incoming_at"] = last_incoming_at
    if last_outgoing_at is not None:
        update_data["last_outgoing_at"] = last_outgoing_at
    if last_auto_reply_at is not None:
        update_data["last_auto_reply_at"] = last_auto_reply_at

    stmt = sqlite_insert(ConversationState).values(insert_data)
    if update_data:
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "peer_id"],
            set_=update_data,
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["user_id", "peer_id"])
    result = await session.execute(stmt)
    await session.flush()

    if not update_data or result.rowcount == 0:
        # Fallback: either the row existed and we did nothing, or no update needed
        row = await session.execute(
            select(ConversationState).where(
                ConversationState.user_id == user.id,
                ConversationState.peer_id == peer_id,
            )
        )
        return row.scalar_one()
    # SQLite UPSERT: inserted_primary_key может быть None при ON CONFLICT UPDATE
    pk = result.inserted_primary_key
    if pk is not None:
        cs = await session.get(ConversationState, pk)
        if cs is not None:
            return cs
    # Fallback: fetch by compound key
    row = await session.execute(
        select(ConversationState).where(
            ConversationState.user_id == user.id,
            ConversationState.peer_id == peer_id,
        )
    )
    return row.scalar_one()


async def get_conversation_state(
    session: AsyncSession,
    user,
    peer_id: int,
) -> ConversationState | None:
    """Возвращает состояние диалога с контактом."""
    result = await session.execute(
        select(ConversationState).where(
            ConversationState.user_id == user.id,
            ConversationState.peer_id == peer_id,
        )
    )
    return result.scalar_one_or_none()


async def fetch_latest_message_per_contact(
    session: AsyncSession,
    user,
    peer_ids: list[int],
) -> dict[int, Message]:
    """Return the latest Message for each peer_id in ONE query.

    Uses ROW_NUMBER() window function partitioned by peer_id,
    ordered by date descending, keeping only row_num=1.
    """
    if not peer_ids:
        return {}

    subq = (
        select(
            Message,
            func.row_number()
            .over(
                partition_by=Message.peer_id,
                order_by=Message.date.desc(),
            )
            .label("rn"),
        )
        .where(
            Message.user_id == user.id,
            Message.peer_id.in_(peer_ids),
        )
        .subquery()
    )
    aliased_msg = aliased(Message, subq)
    result = await session.execute(select(aliased_msg, subq.c.rn).where(subq.c.rn == 1))
    # result rows are tuples (Message_instance, rn_value)
    return {row[0].peer_id: row[0] for row in result.all()}


async def fetch_latest_messages_per_contact(
    session: AsyncSession,
    user,
    peer_ids: list[int],
    limit: int = 3,
) -> dict[int, list[Message]]:
    """Return up to *limit* latest Messages per peer_id in ONE query.

    Uses ROW_NUMBER() window function partitioned by peer_id,
    ordered by date descending, keeping rows with row_num <= limit.
    """
    if not peer_ids:
        return {}

    subq = (
        select(
            Message,
            func.row_number()
            .over(
                partition_by=Message.peer_id,
                order_by=Message.date.desc(),
            )
            .label("rn"),
        )
        .where(
            Message.user_id == user.id,
            Message.peer_id.in_(peer_ids),
        )
        .subquery()
    )
    aliased_msg = aliased(Message, subq)
    result = await session.execute(
        select(aliased_msg, subq.c.rn)
        .where(subq.c.rn <= limit)
        .order_by(aliased_msg.peer_id, subq.c.rn)
    )
    mapping: dict[int, list[Message]] = {}
    for row in result.all():
        msg = row[0]
        mapping.setdefault(msg.peer_id, []).append(msg)
    return mapping


async def save_reaction(
    session: AsyncSession,
    *,
    user_id: int,
    chat_id: int,
    message_id: int,
    reactor_id: int,
    reaction: str,
    is_bot_message: bool = False,
) -> None:
    """Сохранить реакцию на сообщение в БД.

    Используется для отслеживания реакций пользователей на сообщения,
    в том числе на сообщения бота (для feedback в память).
    """
    session.add(
        MessageReaction(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            reactor_id=reactor_id,
            reaction=reaction,
            is_bot_message=is_bot_message,
        )
    )
    await session.flush()
