"""Авто-саммари длинных чатов.

Если в зеркалируемом чате накопилось ≥50 новых сообщений с последнего пересказа —
бот проактивно предлагает сделать краткий пересказ.

Использует лёгкую LLM-модель (purpose="background") и существующую таблицу
ConversationSummary для хранения чекпоинтов (created_at = время последнего пересказа).
"""

import logging
from datetime import datetime, timedelta, UTC

from sqlalchemy import select, func

from src.db.models import Contact, ConversationSummary, Message
from src.db.session import get_session
from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)

_CHAT_SUMMARY_THRESHOLD = 50  # сообщений


async def check_chat_needs_summary(chat_id: int, user_id: int) -> dict | None:
    """Проверяет, накопилось ли в чате достаточно новых сообщений для пересказа.

    Сравнивает количество сообщений с момента последнего сохранённого саммари
    (или за последние 24 часа, если саммари ещё не делали).

    Возвращает:
        {"chat_name": str, "new_count": int, "since": datetime} — если нужен пересказ,
        None — если сообщений недостаточно.
    """
    async with get_session() as session:
        # Ищем последний сохранённый саммари для этого чата
        result = await session.execute(
            select(ConversationSummary)
            .where(
                ConversationSummary.user_id == user_id,
                ConversationSummary.last_peer_id == chat_id,
            )
            .order_by(ConversationSummary.created_at.desc())
            .limit(1)
        )
        last_summary = result.scalar_one_or_none()

        if last_summary is not None:
            since = last_summary.created_at
        else:
            since = datetime.now(UTC) - timedelta(hours=24)

        # Считаем все новые сообщения (входящие + исходящие) с момента последнего саммари
        result = await session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.user_id == user_id,
                Message.peer_id == chat_id,
                Message.date > since,
            )
        )
        new_count = result.scalar_one()

        if new_count < _CHAT_SUMMARY_THRESHOLD:
            return None

        # Получаем имя чата из контактов
        contact_result = await session.execute(
            select(Contact.display_name)
            .where(Contact.user_id == user_id, Contact.peer_id == chat_id)
            .limit(1)
        )
        chat_name_row = contact_result.scalar_one_or_none()
        chat_name = chat_name_row if chat_name_row else f"чат {chat_id}"

        return {
            "chat_name": chat_name,
            "new_count": new_count,
            "since": since,
        }


async def generate_chat_summary(chat_id: int, user_id: int) -> str:
    """Генерирует краткий LLM-пересказ последней активности в чате.

    Использует лёгкую LLM-модель через build_provider(purpose="background").

    Возвращает:
        Строку из 3-5 предложений: ключевые темы, решения, настроение.
        В случае ошибки — сообщение с описанием проблемы.
    """
    from src.core.contacts.chat_service import message_to_text
    from src.db.repo import fetch_chat_messages, get_or_create_user
    from src.llm.router import build_provider

    async with get_session() as session:
        owner = await get_or_create_user(session, user_id)
        provider = await build_provider(
            session, owner, purpose="background", task_type=TaskType.SUMMARIZE
        )
        if provider is None:
            return "❌ Не удалось создать LLM-провайдер для фоновой задачи."

        # Загружаем последние 80 сообщений — достаточно для контекстного пересказа
        messages = await fetch_chat_messages(session, owner, chat_id, limit=80)

        if not messages:
            return "📭 В чате нет сохранённых сообщений."

        # Получаем имя чата
        contact_result = await session.execute(
            select(Contact.display_name)
            .where(Contact.user_id == user_id, Contact.peer_id == chat_id)
            .limit(1)
        )
        chat_name_row = contact_result.scalar_one_or_none()
        chat_name = chat_name_row if chat_name_row else f"чат {chat_id}"

    # Строим транскрипт из последних сообщений
    transcript_lines: list[str] = []
    for m in messages[-80:]:
        label = message_to_text(m)
        if label.strip():
            transcript_lines.append(label)

    if not transcript_lines:
        return "📭 Нет текстовых сообщений для анализа."

    transcript = "\n".join(transcript_lines)

    system_prompt = (
        "Ты делаешь КРАТКИЙ пересказ чата. 3-5 предложений, живой язык, без маркдауна.\n"
        "Опиши: 1) ключевые темы обсуждения, 2) принятые решения/договорённости, "
        "3) общий настрой (дружеский/рабочий/напряжённый).\n"
        "Не используй списки. Пиши связным текстом на русском."
    )

    user_prompt = (
        f"Чат: {chat_name}\nПоследние {len(messages)} сообщений:\n\n{transcript[:6000]}"
    )

    try:
        summary = await provider.chat(
            [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_prompt),
            ],
            task_type=TaskType.SUMMARIZE,
        )
    except Exception:
        logger.exception(
            "Не удалось сгенерировать пересказ для чата %s (user %s)",
            chat_id,
            user_id,
        )
        return "❌ Ошибка LLM при генерации пересказа. Попробуй позже."

    return summary


async def save_summary_checkpoint(
    chat_id: int, user_id: int, last_message_id: int
) -> None:
    """Сохраняет отметку, что пересказ сделан до этого сообщения.

    Предотвращает повторный пересказ одних и тех же сообщений.
    Чекпоинт хранится в таблице ConversationSummary:
    - created_at = время создания чекпоинта (используется как «с какого момента считать новые»)
    - last_peer_id = chat_id
    - last_message_id передаётся в комментарии для отладки, но не сохраняется в БД
      (таблица ConversationSummary не имеет поля last_message_id; используется временной чекпоинт).
    """
    async with get_session() as session:
        summary = ConversationSummary(
            user_id=user_id,
            last_peer_id=chat_id,
            summary_text="",  # заполняется при генерации; здесь — только чекпоинт
            turn_count=0,
        )
        session.add(summary)
        await session.commit()
        logger.debug(
            "Сохранён чекпоинт пересказа: user=%s, chat=%s, up_to_msg=%s",
            user_id,
            chat_id,
            last_message_id,
        )
