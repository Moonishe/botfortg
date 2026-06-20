"""Поддержка Telegram Rich Messages (Bot API v10.1 / v12.8.0).

Экспериментальный метод ``sendRichMessage`` — расширенное форматирование:
- Заголовки (``#``, ``##``), таблицы (``|...|``), чеклисты (``- [ ]``),
- Сворачиваемые блоки (``<details>``), математические формулы (``$$``),
- До 32 768 символов текста и до 500 блоков.

См. https://core.telegram.org/bots/api#sendrichmessage
"""

from __future__ import annotations

import logging
import re
from typing import Any

from aiogram.types import Message

logger = logging.getLogger(__name__)

# ── Лимиты Rich Messages ─────────────────────────────────────────────
RICH_MESSAGE_MAX = 32768  # Telegram hard limit
RICH_MESSAGE_LIMIT = (
    8000  # Порог, после которого Rich Messages выгоднее обычного sendMessage
)


# ── Public API ───────────────────────────────────────────────────────


async def send_rich_message(
    bot: Any,
    chat_id: int,
    markdown: str,
    **kwargs: Any,
) -> Message | None:
    """Отправить Rich Message через сырой Telegram API.

    Использует внутреннюю ``bot.session`` (aiohttp ClientSession aiogram'а),
    чтобы не создавать лишних HTTP-клиентов.  В случае, если метод не
    поддерживается сервером Telegram (400/404/405), возвращает ``None``
    для graceful fallback на обычный ``sendMessage``.

    Args:
        bot: Экземпляр aiogram ``Bot`` (имеет ``.token`` и ``.session``).
        chat_id: ID чата (целое число).
        markdown: GFM-разметка (не HTML!).
        **kwargs: Дополнительные параметры API (reply_markup и др.).

    Returns:
        ``Message`` при успехе, ``None`` если метод не поддерживается.
    """
    if not isinstance(markdown, str):
        logger.warning("send_rich_message called with non-string markdown")
        return None

    # ── Size guard: Telegram hard limit 32 768 ──
    if len(markdown) > RICH_MESSAGE_MAX:
        logger.warning(
            "Rich message too long (%d chars), truncating to %d",
            len(markdown),
            RICH_MESSAGE_MAX,
        )
        # Try to cut at a paragraph boundary
        cut = markdown.rfind("\n\n", 0, RICH_MESSAGE_MAX - 100)
        if cut < RICH_MESSAGE_MAX // 2:
            cut = RICH_MESSAGE_MAX - 100
        markdown = markdown[:cut] + "\n\n… *(обрезано)*"

    url = f"https://api.telegram.org/bot{bot.token}/sendRichMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": {
            "markdown": markdown,
        },
    }

    # Передаём reply_markup в корень payload'а, если он есть
    if "reply_markup" in kwargs:
        payload["reply_markup"] = kwargs["reply_markup"]

    try:
        async with bot.session.post(url, json=payload) as resp:
            body = await resp.json()

            # 200 OK — успех
            if resp.status == 200 and body.get("ok"):
                result = body.get("result", {})
                # Defensive: Telegram API should always return a full Message
                # object here, but a malformed response (empty dict) would crash
                # pydantic's Message(**{}) with ValidationError.
                if not isinstance(result, dict) or not result:
                    logger.warning(
                        "sendRichMessage returned empty/malformed result (chat_id=%s)",
                        chat_id,
                    )
                    return None
                try:
                    return Message(**result)  # type: ignore[arg-type]
                except Exception:
                    logger.warning(
                        "sendRichMessage result failed Message constructor (chat_id=%s)",
                        chat_id,
                        exc_info=True,
                    )
                    return None

            # 400/404/405 — метод не поддерживается данным сервером
            if resp.status in (400, 404, 405):
                logger.debug(
                    "sendRichMessage not supported (status=%d, description=%r)",
                    resp.status,
                    body.get("description", ""),
                )
                return None

            # Прочие ошибки — логируем и возвращаем None
            logger.warning(
                "sendRichMessage failed (status=%d, chat_id=%s): %r",
                resp.status,
                chat_id,
                body.get("description", body),
            )
            return None

    except Exception:
        logger.debug(
            "sendRichMessage network error (chat_id=%s)", chat_id, exc_info=True
        )
        return None


def to_rich_markdown(text: str) -> str:
    """Преобразовать Telegram HTML в GFM-разметку для Rich Messages.

    Поддерживаемые преобразования:
        ``<b>...</b>``           → ``**...**``
        ``<i>...</i>``           → ``_..._``
        ``<code>...</code>``     → `` `...` ``
        ``<pre>...</pre>``       → ```` ```...``` ````
        ``<a href="...">txt</a>`` → ``[txt](...)``
        ``<tg-spoiler>...</tg-spoiler>`` → ``||...||``
        ``<u>...</u>``           → ``<u>...</u>``  (нет GFM-аналога, оставляем)
        ``<s>...</s>``           → ``~~...~~``
        ``<blockquote>...</blockquote>`` → ``> ...``  (Markdown blockquote)

    Прочие теги (``<br>`` → ``\n``, ``&lt;`` → ``<`` и т.д.) обрабатываются
    как обычно. Неподдерживаемые теги удаляются (только тег, не содержимое).

    Args:
        text: Исходный текст в Telegram HTML-формате.

    Returns:
        Строка в GFM-разметке, пригодная для ``sendRichMessage``.
    """
    # Порядок важен: <a> до остальных, чтобы не сломать вложенность
    text = re.sub(
        r'<a\s+href="([^"]*)"\s*>(.*?)</a>',
        r"[\2](\1)",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"<b>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<i>(.*?)</i>", r"_\1_", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(
        r"<pre>(.*?)</pre>", r"```\n\1\n```", text, flags=re.IGNORECASE | re.DOTALL
    )
    text = re.sub(
        r"<tg-spoiler>(.*?)</tg-spoiler>",
        r"||\1||",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<s>(.*?)</s>", r"~~\1~~", text, flags=re.IGNORECASE | re.DOTALL)
    # <u> — нет GFM-аналога, оставляем как есть

    # <br> → перенос строки (ДО blockquote, чтобы разрывы строк
    # внутри blockquote получили префикс >)
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")

    # <blockquote> → Markdown blockquote (добавляем > в начало каждой строки)
    def _blockquote_repl(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        return "\n".join(f"> {line}" for line in inner.splitlines())

    text = re.sub(
        r"<blockquote>(.*?)</blockquote>",
        _blockquote_repl,
        text,
        flags=re.DOTALL,
    )

    # Удаляем оставшиеся неподдерживаемые HTML-теги (только тег, контент остаётся).
    # Явно исключаем <u> (нет GFM-аналога), <details> и <summary> (valid GFM HTML).
    text = re.sub(
        r"</?(?!(?:u|details|summary)\b)[a-zA-Z][^>]*>",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Раскодируем HTML-сущности
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')

    return text


# ── Эвристики для определения применимости Rich Messages ──────────────


def is_rich_applicable(text: str) -> bool:
    """Определить, выгодно ли использовать Rich Message вместо обычного.

    Возвращает ``True`` если в тексте есть:
    - Таблицы (строки с ``|...|``)
    - Заголовки Markdown (``#`` в начале строки)
    - Чеклисты (``- [ ]`` или ``- [x]``)
    - Математические формулы (``$$``)
    - Длина > 8000 символов (Rich Messages поддерживают до 32 768)

    Args:
        text: Исходный текст (до или после ``to_rich_markdown``).

    Returns:
        ``True`` если Rich Message предпочтительнее, иначе ``False``.
    """
    if not isinstance(text, str):
        return False

    if len(text) > RICH_MESSAGE_LIMIT:
        return True

    if re.search(r"^\|.+\|", text, re.MULTILINE):
        return True

    if re.search(r"^#{1,6}\s", text, re.MULTILINE):
        return True

    if re.search(r"^\s*-\s+\[[ xX]\]", text, re.MULTILINE):
        return True

    return "$$" in text
