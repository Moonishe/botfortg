"""Команды /pubmed, /pubmed_abstract, /pubmed_full — поиск статей в PubMed."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from aiogram import Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command

from src.bot.filters import OwnerOnly
from src.core.infra.text_sanitizer import sanitize_html

if TYPE_CHECKING:
    pass

router = Router(name="pubmed")
router.message.filter(OwnerOnly())

logger = logging.getLogger(__name__)

_PMID_RE = re.compile(r"^[0-9]{1,20}$")


@router.message(Command("pubmed"))
async def cmd_pubmed_search(message: types.Message) -> None:
    """Поиск научных статей в PubMed по текстовому запросу."""
    if not message.text:
        return

    parts = message.text.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    if not query:
        await message.answer(
            "🔬 <b>PubMed поиск</b>\n\n"
            "Используй: <code>/pubmed quantum computing</code>\n"
            "Например: /pubmed CRISPR gene editing",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        from src.core.actions import pubmed_client

        results = await pubmed_client.search_pubmed(query, max_results=5)
        if not results:
            await message.answer(
                f"😕 Ничего не нашёл по «{sanitize_html(query)}»",
                parse_mode=ParseMode.HTML,
            )
            return

        pmids = [p["pmid"] for p in results]
        articles = await pubmed_client.fetch_summaries(pmids)

        parts: list[str] = [
            f"🔬 <b>Найдено: {len(articles)} статей</b> по «{sanitize_html(query)}»\n"
        ]
        for i, art in enumerate(articles, 1):
            title = sanitize_html(art.get("title") or "Без названия")
            pmid = art["pmid"]
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

            authors = art.get("authors", [])
            if len(authors) > 3:
                author_str = "Authors: " + ", ".join(authors[:3]) + " et al."
            else:
                author_str = (
                    "Authors: " + ", ".join(authors) if authors else "Неизвестно"
                )
            author_str = sanitize_html(author_str)

            year = (art.get("pubdate") or "")[:4]
            journal = sanitize_html(art.get("journal") or "")
            doi = art.get("doi")
            doi_line = f"\n🔗 DOI: {sanitize_html(doi)}" if doi else ""

            parts.append(
                f'<b>{i}.</b> <b><a href="{url}">{title}</a></b>\n'
                f"👥 {author_str}\n"
                f"📅 {year}{' · ' + journal if journal else ''}{doi_line}"
            )

        parts.append("")
        parts.append("💡 /pubmed_abstract &lt;pmid&gt; — подробнее")
        response_text = "\n\n".join(parts)
        # Telegram API limit: 4096 символов
        if len(response_text) > 4096:
            # Fallback to plain text to avoid HTML parsing errors
            plain_text = re.sub(r"<[^>]+>", "", response_text)  # Strip HTML tags
            plain_text = re.sub(r"&[a-zA-Z]+;", "", plain_text)  # Strip HTML entities
            if len(plain_text) > 4096:
                plain_text = plain_text[:4090] + "..."
            response_text = plain_text
        await message.answer(response_text, parse_mode=ParseMode.HTML)

    except Exception:
        logger.exception("pubmed search failed")
        await message.answer(
            "⚠️ Ошибка при поиске в PubMed",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("pubmed_abstract"))
async def cmd_pubmed_abstract(message: types.Message) -> None:
    """Получить абстракт статьи по её PMID."""
    if not message.text:
        return

    parts = message.text.split(maxsplit=1)
    pmid = parts[1].strip() if len(parts) > 1 else ""
    if not pmid or not _PMID_RE.match(pmid):
        await message.answer(
            "📄 <b>Абстракт статьи</b>\n\n"
            "Используй: <code>/pubmed_abstract 12345678</code>\n"
            "PMID — это номер статьи в PubMed.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        from src.core.actions import pubmed_client

        articles = await pubmed_client.fetch_summaries([pmid])
        if not articles:
            await message.answer(
                "📄 Абстракт недоступен",
                parse_mode=ParseMode.HTML,
            )
            return

        art = articles[0]
        title = sanitize_html(art.get("title") or "Без названия")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

        authors = art.get("authors", [])
        author_str = ", ".join(authors) if authors else "Неизвестно"
        author_str = sanitize_html(author_str)

        year = (art.get("pubdate") or "")[:4]
        journal = sanitize_html(art.get("journal") or "")

        abstract = await pubmed_client.fetch_abstract(pmid)
        abstract_text = sanitize_html(abstract) if abstract else "Абстракт недоступен"

        text = (
            f'📄 <b><a href="{url}">{title}</a></b>\n'
            f"👥 {author_str} · 📅 {year} · {journal}\n\n"
            f"{abstract_text}\n\n"
            f"💡 Скажи «объясни статью» чтобы я саммаризировал"
        )
        # Telegram API limit: 4096 символов
        if len(text) > 4096:
            # Fallback to plain text to avoid HTML parsing errors
            plain_text = re.sub(r"<[^>]+>", "", text)  # Strip HTML tags
            plain_text = re.sub(r"&[a-zA-Z]+;", "", plain_text)  # Strip HTML entities
            if len(plain_text) > 4096:
                plain_text = plain_text[:4090] + "..."
            text = plain_text
        await message.answer(text, parse_mode=ParseMode.HTML)

    except Exception:
        logger.exception("pubmed abstract failed")
        await message.answer(
            "⚠️ Ошибка при получении абстракта",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("pubmed_full"))
async def cmd_pubmed_full(message: types.Message) -> None:
    """Получить полный текст статьи (заглушка, в разработке)."""
    await message.answer(
        "🔧 Полный текст доступен только для статей в PMC (PubMed Central).\n"
        "Команда в разработке.",
        parse_mode=ParseMode.HTML,
    )
