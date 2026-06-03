"""Команда /docs — показать документацию из docs/."""

from __future__ import annotations


from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import re

from src.bot.filters import OwnerOnly
from src.config import PROJECT_ROOT

router = Router(name="docs")
router.message.filter(OwnerOnly())

DOCS_DIR = PROJECT_ROOT / "docs"
_TOPIC_RE = re.compile(r"^[a-z0-9_\-]{1,64}$")


@router.message(Command("docs"))
async def cmd_docs(message: Message) -> None:
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        if not DOCS_DIR.exists():
            await message.answer("📭 Нет документации.")
            return
        files = sorted(f.name for f in DOCS_DIR.iterdir() if f.suffix == ".md")
        if not files:
            await message.answer("📭 Нет документации.")
            return
        await message.answer(
            "📚 <b>Документация:</b>\n"
            + "\n".join(f"/docs {f.replace('.md', '')}" for f in files)
        )
        return

    topic = args[1].strip().lower().replace(" ", "-")
    # Guard: reject path traversal attempts
    if not _TOPIC_RE.match(topic):
        await message.answer(
            "❌ Недопустимое имя документа. Разрешены буквы, цифры, _ и -"
        )
        return
    path = DOCS_DIR / f"{topic}.md"
    # Guard: ensure resolved path stays within DOCS_DIR
    try:
        resolved = path.resolve()
        resolved.relative_to(DOCS_DIR.resolve())
    except (ValueError, OSError):
        await message.answer("❌ Недопустимое имя документа.")
        return
    if not path.exists():
        # Try fuzzy: list files and find closest match
        if not DOCS_DIR.exists():
            await message.answer(f"❌ Не нашёл документацию «{topic}».")
            return
        for f in DOCS_DIR.iterdir():
            if f.suffix == ".md" and topic in f.stem.lower():
                path = f
                break
        else:
            await message.answer(f"❌ Не нашёл документацию «{topic}».")
            return

    text = path.read_text(encoding="utf-8")[:3500]
    # Strip markdown headers for cleaner Telegram output
    await message.answer(f"<b>📄 {path.stem.replace('-', ' ').title()}</b>\n\n{text}")
