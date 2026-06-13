"""Атомарный контекстный менеджер транзакций для SQLAlchemy async сессий.

Использование::

    from src.core.infra.transaction import transaction

    async with transaction() as session:
        owner = await get_or_create_user(session, uid)
        session.add(SomeModel(user_id=owner.id, ...))
        # commit при выходе без ошибок, rollback при исключении
"""

from __future__ import annotations

from contextlib import asynccontextmanager


from src.db.session import get_session


@asynccontextmanager
async def transaction():
    """Атомарная транзакция: commit при успехе, rollback при ошибке.

    В отличие от ``get_session()``, который commit-ит даже если вызывающий
    код не завершился успешно (например, из-за исключения вне блока with),
    ``transaction()`` гарантирует rollback при любом исключении внутри блока.

    Пример::

        async with transaction() as session:
            user = await get_or_create_user(session, uid)
            user.some_field = "new_value"
            # Не нужно вызывать session.commit() — transaction сделает это
            # автоматически. При исключении — автоматический rollback.

    Примечание:
        Это новый инструмент для будущего кода. Существующие вызовы
        ``get_session()`` продолжают работать как раньше.
    """
    async with get_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
