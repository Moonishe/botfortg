"""MCP tool: редактирование памяти с аудит-трейлом.

Actions:
- ``action="edit"`` — редактировать текст факта
- ``action="history"`` — показать историю версий факта
- ``action="rollback"`` — откатить факт к предыдущей версии

Каждое изменение сохраняется в MemoryVersion — полный аудит-трейл
(who changed what, when, and why).
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.actions.tool_registry import tool
from src.db.repo import get_or_create_user

logger = logging.getLogger(__name__)


@tool(
    name="mcp_memory_edit",
    description=(
        "Редактирование фактов памяти с сохранением истории изменений.\n"
        "Три действия:\n"
        "- 'edit' — изменить текст факта по memory_id. "
        "Пример: action='edit', memory_id=42, new_text='Новый текст факта'\n"
        "- 'history' — показать историю правок факта. "
        "Пример: action='history', memory_id=42\n"
        "- 'rollback' — откатить факт к предыдущей версии. "
        "Пример: action='rollback', memory_id=42, version=1"
    ),
    category="memory",
    risk="high",
    params={
        "action": "str — 'edit', 'history' или 'rollback'",
        "memory_id": "int — ID факта памяти",
        "new_text": "str — новый текст факта (только для action='edit')",
        "version": "int — номер версии для отката (только для action='rollback')",
        "reason": "str — причина изменения (опционально, для action='edit')",
    },
)
async def mcp_memory_edit(
    action: str = "history",
    memory_id: int = 0,
    new_text: str = "",
    version: int = 0,
    reason: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Редактирование памяти с аудит-трейлом.

    Args:
        action: ``"edit"``, ``"history"``, или ``"rollback"``.
        memory_id: ID факта памяти.
        new_text: Новый текст (для ``action="edit"``).
        version: Целевая версия (для ``action="rollback"``).
        reason: Причина изменения (опционально).

    Returns:
        dict с результатом операции.
    """
    user = kwargs.get("user")
    if user is None:
        return {"error": "user is required"}

    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, user.telegram_id)

        # ── Проверка принадлежности факта ────────────────────────────
        from src.db.models._memory import Memory

        if memory_id <= 0:
            return {"error": "memory_id is required and must be > 0"}

        mem = await session.get(Memory, memory_id)
        if not mem or mem.user_id != owner.id:
            return {"error": f"Факт #{memory_id} не найден или не принадлежит вам"}

        # ── action = history ─────────────────────────────────────────
        if action == "history":
            from src.db.repos.memory_repo import get_memory_history

            versions = await get_memory_history(session, owner, memory_id)
            if not versions:
                return {
                    "ok": True,
                    "memory_id": memory_id,
                    "current_fact": mem.fact,
                    "history": [],
                    "message": "История правок пуста — факт ни разу не редактировался.",
                }

            history_list: list[dict[str, Any]] = []
            for v in versions:
                history_list.append(
                    {
                        "version": v.version,
                        "fact_text": v.fact_text,
                        "edited_by": v.edited_by,
                        "edited_at": v.edited_at.isoformat() if v.edited_at else None,
                        "reason": v.reason,
                    }
                )

            return {
                "ok": True,
                "memory_id": memory_id,
                "current_fact": mem.fact,
                "total_versions": len(history_list),
                "history": history_list,
            }

        # ── action = edit ────────────────────────────────────────────
        if action == "edit":
            if not new_text or not new_text.strip():
                return {"error": "new_text is required for edit action"}

            from src.core.memory.memory_admin import update_memory_text

            try:
                updated = await update_memory_text(
                    session,
                    owner,
                    memory_id,
                    new_text.strip(),
                )
                if updated is None:
                    return {
                        "error": (
                            f"Не удалось обновить факт #{memory_id}. "
                            f"Проверьте длину текста (допустимо {3}-{500} символов)."
                        )
                    }

                await session.commit()

                # Получаем актуальную историю после коммита
                from src.db.repos.memory_repo import get_memory_history

                versions = await get_memory_history(session, owner, memory_id)
                history_count = len(versions)

                return {
                    "ok": True,
                    "memory_id": memory_id,
                    "new_fact": updated.fact,
                    "reason": reason or "manual edit",
                    "total_versions": history_count,
                    "message": f"Факт #{memory_id} обновлён. Всего версий: {history_count}.",
                }
            except ValueError as exc:
                return {"error": f"Конфликт версий: {exc}"}

        # ── action = rollback ────────────────────────────────────────
        if action == "rollback":
            if version <= 0:
                return {
                    "error": "version is required for rollback action (must be > 0)"
                }

            from src.db.repos.memory_repo import rollback_memory

            rolled_back = await rollback_memory(session, owner, memory_id, version)
            if rolled_back is None:
                return {
                    "error": (
                        f"Не удалось откатить факт #{memory_id} к версии v{version}. "
                        f"Проверьте, что версия существует."
                    )
                }

            await session.commit()

            return {
                "ok": True,
                "memory_id": memory_id,
                "restored_fact": rolled_back.fact,
                "rollback_to_version": version,
                "message": f"Факт #{memory_id} откачен к версии v{version}.",
            }

        return {
            "error": f"Неизвестное действие: {action}. Используйте 'edit', 'history' или 'rollback'."
        }
