"""Callback-хендлеры для интерактивных кнопок Deep Research.

Обрабатывает нажатия на inline-кнопки:

- ``research:view`` — показать сводку отчёта с кнопками действий.
- ``research:file`` — отправить полный отчёт (.md файл).
- ``research:dig_deeper`` — предложить 3 follow-up запроса.
- ``research:save_memory`` — сохранить сводку в память.
- ``research:retry`` — перезапустить исследование.
- ``research:delete`` — удалить сообщение с отчётом.

Callback data: ``research:<action>:<job_id>``

Защита: ``OwnerOnly()`` фильтр на роутере — только владелец бота
может нажать кнопки. HMAC-подпись не требуется.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.core.infra.key_guard import safe_str
from src.core.actions.mcp_tools import _safe_resolve
from src.core.rag.deep_research_pipeline import get_deep_research_pipeline
from src.core.rag.types import ResearchResult
from src.db.repo import add_memory, get_or_create_user
from src.db.session import get_session


logger = logging.getLogger(__name__)


router = Router(name="research_cb")

router.callback_query.filter(OwnerOnly())


# ── Helpers ────────────────────────────────────────────────────────────


def _strip_markdown(text: str) -> str:
    """Убрать GFM-разметку для отображения в обычном сообщении.

    Сохраняет смысловое содержимое: заголовки остаются как текст,
    ссылки — как ``title (url)``, таблицы — как строки с | разделителями.
    Удаляет только синтаксическую разметку (**Жирный** → Жирный).
    """
    import re as _re

    # <details> / <summary> блоки — убрать теги, оставить содержимое
    text = _re.sub(r"</?details>", "", text)
    text = _re.sub(r"</?summary>", "", text)

    # Markdown ссылки: [text](url) → text (url)
    text = _re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1 (\2)", text)

    # Жирный/курсив: **text** → text, _text_ → text
    text = _re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = _re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", text)

    # ``code`` → code
    text = _re.sub(r"`([^`]+)`", r"\1", text)

    # Заголовки: # Heading → Heading
    text = _re.sub(r"^#{1,6}\s+", "", text, flags=_re.MULTILINE)

    # Разделители таблиц (|---|---|) — убрать
    text = _re.sub(r"^\|[\s\-:|]+\|$", "", text, flags=_re.MULTILINE)

    # Пустые строки после удаления разделителей таблиц — схлопнуть
    text = _re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _parse_research_cb(data: str) -> tuple[str, str] | None:
    """Разобрать callback_data: research:<action>:<job_id>.

    Returns:
        (action, job_id) или None при ошибке формата.
    """
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "research":
        return None
    action, job_id = parts[1], parts[2]
    return action, job_id


async def _get_job_and_report(
    job_id: str,
) -> tuple[ResearchResult | None, str, Path | None]:
    """Загрузить статус задачи и текст отчёта.

    Returns:
        (result, report_text, report_path_or_None)
    """
    pipeline = get_deep_research_pipeline()
    result = await pipeline.get_status(job_id)

    if result is None:
        return None, "", None

    # Читаем SUMMARY.md с диска (с защитой от path traversal)
    raw = f"data/research/{job_id}/SUMMARY.md"
    report_path = _safe_resolve(raw)

    try:
        if report_path is not None and report_path.exists():
            report_text = report_path.read_text(encoding="utf-8")
        else:
            report_text = result.summary or pipeline.get_summary(job_id)
    except Exception:
        logger.exception("Failed to read report for job %s", job_id)
        report_text = result.summary or "Отчёт недоступен."

    return result, report_text, report_path


def _ensure_message(callback: CallbackQuery) -> Message | None:
    """Проверить что callback.message — редактируемый Message."""
    msg = callback.message
    if msg is None or not isinstance(msg, Message):
        return None
    return msg


# ── Хендлеры ──────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("research:view:"))
async def cb_research_view(callback: CallbackQuery) -> None:
    """Показать сводку отчёта с кнопками действий.

    Отправляет Rich Message с GFM-форматированием (таблицы, заголовки,
    сворачиваемые блоки). При недоступности Rich Messages —
    fallback на обычное сообщение с очищенным от разметки текстом.
    """
    await callback.answer("⏳ Загружаю отчёт…")

    parsed = _parse_research_cb(callback.data or "")
    if parsed is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    _action, job_id = parsed
    result, report_text, _report_path = await _get_job_and_report(job_id)

    if result is None:
        await callback.answer("Задача не найдена или устарела.", show_alert=True)
        return

    msg = _ensure_message(callback)
    if msg is None:
        await callback.answer("Сообщение недоступно для ответа.", show_alert=True)
        return

    # Собираем клавиатуру
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📄 Полный отчёт",
        callback_data=f"research:file:{job_id}",
    )
    builder.button(
        text="🔍 Копнуть глубже",
        callback_data=f"research:dig_deeper:{job_id}",
    )
    builder.button(
        text="💾 В память",
        callback_data=f"research:save_memory:{job_id}",
    )
    builder.adjust(1)  # по одной кнопке в ряд

    reply_markup = builder.as_markup()

    # ── Попытка Rich Message (GFM: таблицы, <details>, заголовки) ──
    rich_sent = False
    try:
        from src.bot.rich_messages import send_rich_message, to_rich_markdown

        rich_md = to_rich_markdown(report_text)
        rich_result = await send_rich_message(
            msg.bot, msg.chat.id, rich_md, reply_markup=reply_markup
        )
        if rich_result is not None:
            logger.debug("Rich message sent for job %s", job_id)
            rich_sent = True
            # Редактируем исходное сообщение — убираем текст, оставляем ссылку
            try:
                await msg.edit_text(
                    "📋 <b>Отчёт ниже ↙️</b>",
                    reply_markup=None,
                )
            except Exception:
                logger.debug("Failed to edit original message", exc_info=True)
    except Exception:
        logger.debug("Rich message attempt failed for job %s", job_id, exc_info=True)

    # ── Fallback: очищенный от разметки текст в исходном сообщении ──
    if not rich_sent:
        plain_text = _strip_markdown(report_text[:3500])
        if len(report_text) > 3500:
            plain_text += "\n\n… *(полный отчёт — кнопка «📄 Полный отчёт»)*"
        try:
            await msg.edit_text(
                f"📋 <b>Результат исследования</b>\n\n{plain_text}",
                reply_markup=reply_markup,
            )
        except Exception:
            logger.exception("Failed to edit research view message for job %s", job_id)
            await callback.answer(
                "Ошибка отображения отчёта. Попробуйте позже.", show_alert=True
            )
            return

    await callback.answer("✅ Отчёт загружен!")


@router.callback_query(F.data.startswith("research:file:"))
async def cb_research_file(callback: CallbackQuery) -> None:
    """Отправить полный отчёт исследования как .md файл."""
    await callback.answer("⏳ Отправляю файл…")

    parsed = _parse_research_cb(callback.data or "")
    if parsed is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    _action, job_id = parsed
    result, report_text, report_path = await _get_job_and_report(job_id)

    if result is None:
        await callback.answer("Задача не найдена или устарела.", show_alert=True)
        return

    msg = _ensure_message(callback)
    if msg is None:
        await callback.answer("Сообщение недоступно для ответа.", show_alert=True)
        return

    safe_name = job_id.replace("/", "_").replace("\\", "_")
    filename = f"research_{safe_name}.md"

    # ── Попытка отправить Rich Message со сводкой ──
    try:
        if report_text:
            from src.bot.rich_messages import send_rich_message, to_rich_markdown

            rich_md = to_rich_markdown(report_text)
            rich_result = await send_rich_message(msg.bot, msg.chat.id, rich_md)
            if rich_result is not None:
                logger.debug("Rich message sent for job %s, still sending file", job_id)
    except Exception:
        logger.debug("Rich message attempt failed for job %s", job_id, exc_info=True)

    # ── Отправка файла (основной функционал) ──
    try:
        if (
            report_path is not None
            and report_path.exists()
            and report_path.stat().st_size < 50 * 1024 * 1024
        ):
            fs_file = FSInputFile(str(report_path), filename=filename)
            await msg.answer_document(
                fs_file,
                caption=f"📄 Полный отчёт: {result.query[:200]}",
            )
        else:
            buf = BufferedInputFile(
                report_text.encode("utf-8"),
                filename=filename,
            )
            await msg.answer_document(
                buf,
                caption=f"📄 Полный отчёт: {result.query[:200]}",
            )
    except Exception:
        logger.exception("Failed to send report file for job %s", job_id)
        await callback.answer(
            "Ошибка отправки файла. Попробуйте позже.", show_alert=True
        )
        return

    await callback.answer("✅ Файл отправлен!")


@router.callback_query(F.data.startswith("research:dig_deeper:"))
async def cb_research_dig_deeper(callback: CallbackQuery) -> None:
    """Предложить 3 follow-up запроса на основе темы исследования."""
    await callback.answer("🤔 Генерирую идеи…")

    parsed = _parse_research_cb(callback.data or "")
    if parsed is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    _action, job_id = parsed
    pipeline = get_deep_research_pipeline()
    result = await pipeline.get_status(job_id)

    if result is None:
        await callback.answer("Задача не найдена или устарела.", show_alert=True)
        return

    msg = _ensure_message(callback)
    if msg is None:
        await callback.answer("Сообщение недоступно для ответа.", show_alert=True)
        return

    query = (result.query or "").strip()
    if not query:
        await callback.answer(
            "Тема исследования пуста — не могу предложить идеи.", show_alert=True
        )
        return

    # Генерируем 3 follow-up запроса на основе темы
    suggestions = _generate_followup_queries(query)

    text = (
        f"🔍 <b>Копнуть глубже: {query[:120]}</b>\n\n"
        f"Попробуйте продолжить исследование:\n\n"
        f"1️⃣ <code>/research {suggestions[0]}</code>\n"
        f"2️⃣ <code>/research {suggestions[1]}</code>\n"
        f"3️⃣ <code>/research {suggestions[2]}</code>\n\n"
        f"<i>Нажмите на команду чтобы скопировать.</i>"
    )

    try:
        await msg.answer(text)
    except Exception:
        logger.exception("Failed to send dig_deeper suggestions for job %s", job_id)
        await callback.answer("Ошибка отправки. Попробуйте позже.", show_alert=True)
        return

    await callback.answer("✅ Идеи отправлены!")


@router.callback_query(F.data.startswith("research:save_memory:"))
async def cb_research_save_memory(callback: CallbackQuery) -> None:
    """Сохранить сводку исследования в память."""
    await callback.answer("💾 Сохраняю…")

    parsed = _parse_research_cb(callback.data or "")
    if parsed is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    _action, job_id = parsed
    result, report_text, _report_path = await _get_job_and_report(job_id)

    if result is None:
        await callback.answer("Задача не найдена или устарела.", show_alert=True)
        return

    # Берём первые 500 символов как факт для памяти
    memory_fact = report_text[:500].strip()
    if len(report_text) > 500:
        memory_fact = memory_fact.rsplit(" ", 1)[0] + "…"

    if not memory_fact or len(memory_fact) < 10:
        await callback.answer("Недостаточно данных для сохранения.", show_alert=True)
        return

    msg = _ensure_message(callback)
    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            await add_memory(
                session,
                owner,
                fact=f"[Исследование: {result.query[:120]}]: {memory_fact}",
                source="research",
                confidence=0.7,
                importance=0.6,
                memory_type="fact",
            )
    except Exception:
        logger.exception(
            "Failed to save research memory for job %s, user %d",
            job_id,
            callback.from_user.id,
        )
        await callback.answer("Ошибка сохранения в память.", show_alert=True)
        return

    # Убираем клавиатуру — действие выполнено
    if msg is not None:
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Non-critical error", exc_info=True)

    await callback.answer("✅ Сохранено в память!")


@router.callback_query(F.data.startswith("research:retry:"))
async def cb_research_retry(callback: CallbackQuery) -> None:
    """Перезапустить исследование с теми же параметрами."""
    await callback.answer("⏳ Перезапускаю исследование…")

    parsed = _parse_research_cb(callback.data or "")
    if parsed is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    _action, job_id = parsed

    pipeline = get_deep_research_pipeline()
    result = await pipeline.get_status(job_id)

    if result is None:
        await callback.answer("Задача не найдена или устарела.", show_alert=True)
        return

    try:
        from src.core.rag.types import ResearchRequest

        request = ResearchRequest(
            query=result.query,
            max_minutes=5,
        )

        new_job_id = pipeline.submit(request)

        msg = callback.message
        if msg is not None:
            await msg.answer(
                f"🔄 Исследование перезапущено!\n\n"
                f"<b>Запрос:</b> {result.query[:200]}\n"
                f"<b>Новый Job ID:</b> <code>{new_job_id}</code>\n\n"
                f"Статус можно проверить или дождаться уведомления.",
            )

        await callback.answer("✅ Перезапущено!")
    except Exception as exc:
        logger.exception("Failed to retry research for job %s", job_id)
        await callback.answer(f"Ошибка перезапуска: {safe_str(exc)}", show_alert=True)


@router.callback_query(F.data.startswith("research:delete:"))
async def cb_research_delete(callback: CallbackQuery) -> None:
    """Удалить сообщение с отчётом."""
    parsed = _parse_research_cb(callback.data or "")
    if parsed is None:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    msg = callback.message
    if msg is None:
        await callback.answer("Сообщение не найдено.", show_alert=True)
        return

    if not isinstance(msg, Message):
        await callback.answer("Не могу удалить это сообщение.", show_alert=True)
        return

    try:
        await msg.delete()
    except Exception:
        logger.debug("Failed to delete research report message", exc_info=True)
        with contextlib.suppress(Exception):
            await msg.edit_text("🗑 Отчёт удалён.")

    await callback.answer("🗑 Удалено!")


# ── Follow-up генератор ───────────────────────────────────────────────


def _generate_followup_queries(query: str) -> list[str]:
    """Генерирует 3 follow-up запроса на основе исходного.

    Правила:
    1. «Расскажи подробнее о X» — углубление.
    2. «Сравни X с Y» — сравнение / альтернативы.
    3. «Как X повлияет на Z через N лет» — прогноз / тренды.
    """
    q = query.strip().rstrip("?.!：。！")
    if not q:
        return [
            "Расскажи подробнее о результате",
            "Сравни результат с альтернативами: плюсы и минусы",
            "Как изменится ситуация в ближайшие 5 лет: прогноз и тренды",
        ]
    # Обрезаем до 80 символов чтобы запросы не были слишком длинными
    short = q[:80].rsplit(" ", 1)[0] if len(q) > 80 else q

    return [
        f"Расскажи подробнее о {short}",
        f"Сравни {short} с альтернативами: плюсы и минусы",
        f"Как изменится {short[:50]} в ближайшие 5 лет: прогноз и тренды",
    ]
