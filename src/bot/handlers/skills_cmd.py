"""Skills inline panel — main entry point.

Imports UI helpers from ``skills_ui``, callback handlers from
``skills_callbacks``, and registers message handlers here.

``app.py`` imports ``skills_cmd.router`` — this router also includes
the callbacks router, so everything is registered with a single
``dp.include_router(skills_cmd.router)`` call.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.core.intelligence.skill_yaml import extract_frontmatter_metadata
from src.db.repo import (
    get_or_create_user,
    get_skill_by_name,
    set_skill_enabled,
    upsert_skill,
)
from src.db.session import get_session
from .skills_callbacks import (
    _fetch_skills_by_status,
    _perform_rollback,
    router as callbacks_router,
)
from .skills_ui import (
    _format_skill_detail,
    _skill_detail_keyboard,
    _skill_list_keyboard,
    _skills_summary,
)

router = Router(name="skills_cmd")
router.message.filter(OwnerOnly())
router.include_router(callbacks_router)

logger = logging.getLogger(__name__)


# ── Message handlers ───────────────────────────────────────────────────


@router.message(Command("skills"))
async def cmd_skills(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    parts = args.split(maxsplit=1)

    # Legacy text subcommands still work
    if parts and parts[0] in {
        "show",
        "disable",
        "off",
        "enable",
        "on",
        "rollback",
        "yaml",
    }:
        await _legacy_text_command(message, parts)
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        page_skills, total = await _fetch_skills_by_status(session, owner, "all", 0)

    if not page_skills:
        await message.answer(
            "Skills пока пусты. Они появятся после /evolve.",
            reply_markup=_skill_list_keyboard([], "all", 0, 0),
        )
        return

    await message.answer(
        f"<b>Skills</b> (всего {total}):\n" + _skills_summary(page_skills),
        reply_markup=_skill_list_keyboard(page_skills, "all", 0, total),
        parse_mode="HTML",
    )


async def _legacy_text_command(message: Message, parts: Sequence[str]) -> None:
    """Handle legacy /skills subcommands for users who prefer text."""
    sub = parts[0]
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)

        if sub == "show" and len(parts) > 1:
            skill = await get_skill_by_name(session, owner, parts[1])
            if not skill:
                await message.answer("Skill не найден.")
                return
            await message.answer(
                _format_skill_detail(skill),
                reply_markup=_skill_detail_keyboard(skill),
                parse_mode="HTML",
            )
            return

        if sub in {"disable", "off"} and len(parts) > 1:
            skill = await set_skill_enabled(session, owner, parts[1], False)
            await message.answer("Skill отключен." if skill else "Skill не найден.")
            return

        if sub in {"enable", "on"} and len(parts) > 1:
            skill = await set_skill_enabled(
                session, owner, parts[1], True, review_status="approved"
            )
            await message.answer("Skill включен." if skill else "Skill не найден.")
            return

        if sub == "rollback" and len(parts) > 1:
            await _rollback_skill(message, owner, parts[1])
            return

        if sub == "yaml" and len(parts) > 1:
            await _handle_yaml_add(message, parts[1])
            return

    await message.answer(
        "Использование:\n"
        "/skills — панель\n"
        "/skills show <name>\n"
        "/skills enable|disable <name>\n"
        "/skills rollback <name>\n"
        "/skills yaml add <name> ---\\nkey: value\\n---"
    )


async def _rollback_skill(message: Message, owner, name: str) -> None:
    """Rollback a skill to its best_body."""
    async with get_session() as session:
        skill = await get_skill_by_name(session, owner, name)
        if not skill:
            await message.answer("Skill не найден.")
            return
        if skill.best_body is None:
            await message.answer("Нет сохранённой стабильной версии для отката.")
            return

        await _perform_rollback(session, skill, owner, "Manual rollback to best_body")
        new_version = skill.version
        await message.answer(
            f"✅ Skill <b>{html.escape(skill.name)}</b> откачен к стабильной версии v{new_version}.",
            parse_mode="HTML",
        )


async def _handle_yaml_add(message: Message, yaml_args: str) -> None:
    """Add a skill via YAML frontmatter."""
    yaml_parts = yaml_args.split(maxsplit=1)
    if len(yaml_parts) < 2 or yaml_parts[0] != "add":
        await message.answer(
            "⚠️ Использование: /skills yaml add &lt;name&gt; ---\\n"
            "tags: [tag1, tag2]\\ncategory: search\\n---\\n"
            "Описание навыка..."
        )
        return

    name_and_yaml = yaml_parts[1].split(maxsplit=1)
    skill_name = name_and_yaml[0].strip()
    yaml_description = name_and_yaml[1].strip() if len(name_and_yaml) > 1 else ""

    if not skill_name:
        await message.answer("⚠️ Имя навыка не может быть пустым.")
        return

    if not yaml_description:
        await message.answer(
            "⚠️ Использование: /skills yaml add &lt;name&gt; ---\\n"
            "tags: [tag1, tag2]\\ncategory: search\\n---\\n"
            "Описание навыка..."
        )
        return

    try:
        yaml_meta, clean_desc = extract_frontmatter_metadata(yaml_description)
    except Exception as e:
        logger.warning("skill_yaml_parse failed: %s", e)
        await message.answer("⚠️ Ошибка парсинга YAML. Проверь формат")
        return

    if not yaml_meta:
        await message.answer(
            "⚠️ Не найден YAML frontmatter (---...---).\n"
            "Добавьте в начале описания:\n"
            "<code>---\ntags: [tag1]\ncategory: mycat\n---</code>",
            parse_mode="HTML",
        )
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        try:
            skill = await upsert_skill(
                session,
                owner,
                name=skill_name[:128],
                description=yaml_description,
                trigger_patterns_json=None,
                body=clean_desc or yaml_description,
                enabled=False,
                review_status="proposed",
            )
        except Exception as e:
            logger.warning("skill_create failed: %s", e)
            await message.answer("⚠️ Ошибка создания навыка. Попробуй позже")
            return

    meta_str = ", ".join(f"{k}={v}" for k, v in yaml_meta.items())
    await message.answer(
        f"✅ Skill <b>{html.escape(skill.name)}</b> создан с YAML метаданными.\n"
        f"Метаданные: {html.escape(meta_str)}\n"
        f"Статус: proposed (включите через /skills enable {html.escape(skill.name)})",
        parse_mode="HTML",
    )
