"""Батчевая обработка авто-сохранения фактов.

Вместо одного LLM-вызова на сообщение, накапливает сообщения в буфере
и отправляет их единым запросом, экономя токены и latency.

Схема:
  _maybe_auto_save_facts()
    → pre-check (хватает ли ключевых слов?)
    → FactBatchBuffer.add()   // fire-and-forget
       ├─ batch enabled  → добавить в буфер, flush если полный / таймаут
       └─ batch disabled → сразу _save_single()
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
import sys as _sys
import time
from typing import Any

from httpx import RequestError, HTTPStatusError
from sqlalchemy.exc import SQLAlchemyError

from src.config import settings
from src.core.infra.task_manager import track_ff
from src.llm.base import ChatMessage, TaskType

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# Батчевый промпт
# ══════════════════════════════════════════════════════════════════════════

_BATCH_AUTO_SAVE_PROMPT = """You are a fact extractor. You will receive {N} user-assistant message pairs.
For each message, extract ANY personal facts the user revealed about themselves.
Only extract facts where the user explicitly states something about:
- Personal details (name, birthday, job, location)
- Preferences (likes, dislikes, habits)
- Plans, commitments, goals
- Relationships (family, friends, colleagues)
- Experiences, events, memories

Ignore general questions or requests. Return ONLY JSON:
{{"results": [{{"msg_index": 1, "facts": [{{"fact": "...", "sentiment": "positive|negative|neutral"}}]}}]}}
Fact must be a concise statement in third person (e.g. 'User works as a designer').
If no personal facts — return empty facts array for that message.

{messages_block}"""


def _build_batch_prompt(messages: list[dict[str, Any]]) -> str:
    """Собрать батчевый промпт из накопленных сообщений."""
    msg_blocks: list[str] = []
    for i, m in enumerate(messages, 1):
        user_text = m["user_text"][:500].replace("{", "{{").replace("}", "}}")
        assistant_text = m["response_text"][:300].replace("{", "{{").replace("}", "}}")
        msg_blocks.append(
            f"[{i}] User message: {user_text}\n[{i}] Assistant reply: {assistant_text}"
        )
    return _BATCH_AUTO_SAVE_PROMPT.format(
        N=len(messages),
        messages_block="\n\n".join(msg_blocks),
    )


# ══════════════════════════════════════════════════════════════════════════
# Автономное сохранение одного факта (режим без батчинга)
# ══════════════════════════════════════════════════════════════════════════

_AUTO_SAVE_PROMPT = (
    "You are a fact extractor. Given a user message and assistant reply, "
    "extract ANY personal facts the user revealed about themselves. "
    "Only extract facts where the user explicitly states something about:\n"
    "- Personal details (name, birthday, job, location)\n"
    "- Preferences (likes, dislikes, habits)\n"
    "- Plans, commitments, goals\n"
    "- Relationships (family, friends, colleagues)\n"
    "- Experiences, events, memories\n\n"
    "Ignore general questions or requests. Return ONLY JSON:\n"
    '{{"facts": [{{"fact": "...", "sentiment": "positive|negative|neutral"}}]}} '
    'or {{"facts": []}} if no personal facts revealed. '
    "Fact must be a concise statement in third person (e.g. 'User works as a designer').\n\n"
    "User message: {user_text}\n"
    "Assistant reply: {assistant_text}"
)


async def _save_single(
    telegram_id: int,
    user_text: str,
    response_text: str,
    provider: Any,
) -> None:
    """LLM-вызов + сохранение фактов для одного сообщения (режим без батчинга)."""
    try:
        prompt = _AUTO_SAVE_PROMPT.format(
            user_text=user_text[:500].replace("{", "{{").replace("}", "}}"),
            assistant_text=response_text[:300].replace("{", "{{").replace("}", "}}"),
        )
        raw_json = await provider.chat(
            [ChatMessage(role="user", content=prompt)], task_type=TaskType.DEFAULT
        )
        facts = _parse_single_facts(raw_json)
        if facts:
            await _save_facts_to_db(telegram_id, facts)
    except asyncio.CancelledError:
        raise
    except (RequestError, HTTPStatusError, SQLAlchemyError, _json.JSONDecodeError):
        logger.debug("Auto-save facts skipped (single mode)", exc_info=True)


def _parse_single_facts(raw_json: str) -> list[dict[str, str]]:
    """Разобрать JSON-ответ LLM для одного сообщения → список фактов."""
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\s*|\s*```$", "", cleaned).strip()
    facts_data = _json.loads(cleaned)
    facts_list: list[dict[str, str]] = facts_data.get("facts", [])
    return [
        f
        for f in facts_list
        if f.get("fact", "").strip() and len(f.get("fact", "").strip()) >= 5
    ]


def _parse_batch_facts(raw_json: str) -> list[tuple[int, list[dict[str, str]]]]:
    """Разобрать JSON-ответ LLM для батча → список (msg_index, [факты])."""
    cleaned = raw_json.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-z]*\s*|\s*```$", "", cleaned).strip()
    data = _json.loads(cleaned)
    results: list[dict[str, Any]] = data.get("results", [])
    parsed: list[tuple[int, list[dict[str, str]]]] = []
    for r in results:
        idx = r.get("msg_index")
        if idx is None:
            continue
        facts = r.get("facts", [])
        valid_facts = [
            f
            for f in facts
            if f.get("fact", "").strip() and len(f.get("fact", "").strip()) >= 5
        ]
        if valid_facts:
            parsed.append((idx, valid_facts))
    return parsed


def _score_extraction_clarity(fact_text: str) -> tuple[float, float]:
    """Оценивает качество извлечения факта: насколько это прямое утверждение.

    Прямые утверждения («Мне 30 лет», «Я работаю в X») → высокое качество.
    Намёки/неуверенные («наверное, я люблю кофе») → низкое качество.

    Returns:
        (extraction_quality, confidence) — оба 0.0–1.0.
    """
    text_lower = fact_text.lower()
    quality = 0.5  # базовое качество

    # Признаки прямого утверждения (уверенные формулировки)
    direct_markers = (
        "работает в",
        "работаю в",
        "живёт в",
        "живу в",
        "зовут",
        "года",
        "лет",
        "день рождения",
        "работает",
        "работаю",
        "учится",
        "учусь",
        "любит",
        "люблю",
        "не любит",
        "не люблю",
        "хочет",
        "хочу",
    )
    # Признаки неуверенности / намёка
    uncertain_markers = (
        "наверное",
        "возможно",
        "может быть",
        "кажется",
        "вроде",
        "скорее всего",
        "думаю",
        "по-моему",
        "не уверен",
        "не знаю",
    )

    direct_count = sum(1 for m in direct_markers if m in text_lower)
    uncertain_count = sum(1 for m in uncertain_markers if m in text_lower)

    if direct_count > 0:
        quality += 0.2 * min(direct_count, 2)
    if uncertain_count > 0:
        quality -= 0.15 * min(uncertain_count, 2)

    # Длина факта: короткие (3–6 слов) чаще прямые утверждения
    word_count = len(fact_text.split())
    if 3 <= word_count <= 8:
        quality += 0.1

    # Clamp
    quality = max(0.2, min(1.0, quality))
    # Консервативно: confidence чуть ниже extraction_quality для auto-фактов
    confidence = max(0.3, quality - 0.1)
    return round(quality, 2), round(confidence, 2)


async def _save_facts_to_db(
    telegram_id: int,
    facts: list[dict[str, str]],
) -> int:
    """Сохранить факты в БД. Возвращает количество сохранённых."""
    from src.db.repo import add_memory, get_or_create_user
    from src.db.session import get_session

    stored = 0
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        for f in facts:
            fact_text = f.get("fact", "").strip()
            if not fact_text or len(fact_text) < 5:
                continue
            sentiment = f.get("sentiment", "neutral")
            if sentiment not in ("positive", "negative", "neutral"):
                sentiment = "neutral"
            # Meta-Memory: оцениваем качество извлечения
            extraction_quality, initial_confidence = _score_extraction_clarity(
                fact_text
            )
            # source_quality для auto-извлечения = 0.4 (ниже чем chat/user)
            await add_memory(
                session,
                owner,
                fact=fact_text,
                contact_id=None,
                sentiment=sentiment,
                source="auto",
                confidence=initial_confidence,
                source_quality=0.4,
                extraction_quality=extraction_quality,
            )
            stored += 1
    if stored:
        logger.info(
            "Auto-saved %d facts for user %d: %s",
            stored,
            telegram_id,
            "; ".join(f["fact"][:50] for f in facts),
        )
    return stored


# ══════════════════════════════════════════════════════════════════════════
# Буфер батчевой обработки
# ══════════════════════════════════════════════════════════════════════════


class FactBatchBuffer:
    """Накапливает сообщения и отправляет их единым LLM-запросом."""

    def __init__(
        self,
        batch_size: int = 5,
        timeout: float = 10.0,
        enabled: bool = True,
        max_wait: float = 60.0,
    ) -> None:
        self._buffer: list[dict[str, Any]] = []
        self._batch_size = max(1, batch_size)
        self._timeout = max(0.5, timeout)
        # B3: защита от MagicMock в тестах — float() может упасть
        try:
            self._max_wait = max(1.0, float(max_wait))
        except (TypeError, ValueError):
            self._max_wait = 60.0
        self._enabled = enabled
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[Any] | None = None
        # B3: время создания текущего батча — защита от бесконечного откладывания flush
        self._created_at: float | None = None
        self._is_flushing: bool = False  # защита от отмены активного flush

    # ── Публичный API ──────────────────────────────────────────────────

    async def add(
        self,
        telegram_id: int,
        user_text: str,
        response_text: str,
        provider: Any,
    ) -> None:
        """Добавить сообщение в буфер. Возвращается сразу, не ждёт LLM.

        Если батчинг выключен — делает одиночный LLM-вызов здесь же.
        """
        if not self._enabled:
            await _save_single(telegram_id, user_text, response_text, provider)
            return

        async with self._lock:
            was_empty = len(self._buffer) == 0
            self._buffer.append(
                {
                    "telegram_id": telegram_id,
                    "user_text": user_text,
                    "response_text": response_text,
                    "provider": provider,
                }
            )
            # B3: фиксируем время создания батча при первом сообщении
            if was_empty:
                self._created_at = time.monotonic()

            if len(self._buffer) >= self._batch_size:
                # Буфер полон — сбросить немедленно
                if self._flush_task and not self._flush_task.done():
                    if not self._is_flushing:
                        # Отменяем ожидающий таймер
                        self._flush_task.cancel()
                    else:
                        # flush уже идёт — сообщение остаётся в буфере,
                        # новый flush запустится после завершения текущего
                        return
                batch = list(self._buffer)
                # Запустить фоновую обработку ДО очистки буфера —
                # если asyncio.create_task упадёт, сообщение останется в буфере
                self._flush_task = asyncio.create_task(
                    self._flush_batch(batch), name="auto-save-flush-batch"
                )
                self._buffer.clear()
                self._created_at = None
            else:
                # Запустить/перезапустить таймер — сброс после паузы
                if self._is_flushing:
                    # flush уже идёт — не трогаем _flush_task,
                    # сообщение остаётся в буфере до следующего цикла
                    return
                if self._flush_task and not self._flush_task.done():
                    self._flush_task.cancel()
                self._flush_task = asyncio.create_task(
                    self._timeout_flush(), name="auto-save-flush-timeout"
                )

    async def flush_now(self) -> None:
        """Принудительный сброс буфера (для graceful shutdown)."""
        self._is_flushing = True
        try:
            async with self._lock:
                if not self._buffer:
                    return
                batch = list(self._buffer)
                self._buffer.clear()
                if self._flush_task and not self._flush_task.done():
                    self._flush_task.cancel()
            await self._flush_batch(batch)
        finally:
            self._is_flushing = False

    @property
    def enabled(self) -> bool:
        """Включён ли батчинг."""
        return self._enabled

    @property
    def pending_count(self) -> int:
        """Количество сообщений, ожидающих в буфере."""
        return len(self._buffer)

    # ── Внутренние методы ──────────────────────────────────────────────

    async def _timeout_flush(self) -> None:
        """Фоновый таймер: через self._timeout секунд сбрасывает буфер.

        B3: дополнительно проверяет max_wait — если батч живёт дольше max_wait,
        flush происходит независимо от активности (защита от бесконечного postpone)."""
        try:
            await asyncio.sleep(self._timeout)
        except asyncio.CancelledError:
            logger.debug(
                "Batch timeout flush cancelled (timer reset, new flush scheduled)"
            )
            return
        async with self._lock:
            if not self._buffer:
                return
            # B3: если батч висит дольше max_wait — flush немедленно
            if self._created_at is not None:
                elapsed = time.monotonic() - self._created_at
                if elapsed >= self._max_wait:
                    logger.debug(
                        "Batch max_wait (%.1fs) exceeded (elapsed=%.1fs), force flush",
                        self._max_wait,
                        elapsed,
                    )
            batch = list(self._buffer)
            # Создаём задачу ДО очистки буфера: если create_task упадёт,
            # данные останутся в буфере и не будут потеряны
            task = asyncio.create_task(
                self._flush_batch(batch), name="auto-save-flush-timeout-batch"
            )
            track_ff(task)
            self._buffer.clear()
            self._created_at = None

    async def _flush_batch(self, batch: list[dict[str, Any]]) -> None:
        """Обработать накопленный батч: LLM → парсинг → сохранение в БД.

        B4: retry-цикл (до 3 попыток) при LLM-ошибках (сеть, таймаут, rate-limit).
        После 3 неудач — батч теряется, но логируется warning."""
        if not batch:
            return

        self._is_flushing = True
        try:
            provider = batch[0]["provider"]
            prompt = _build_batch_prompt(batch)

            # B4: retry loop — network/rate-limit errors shouldn't silently drop the batch
            raw_json: str | None = None
            last_error: Any = None
            for attempt in range(3):
                try:
                    raw_json = await provider.chat(
                        [ChatMessage(role="user", content=prompt)],
                        task_type=TaskType.DEFAULT,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except (RequestError, HTTPStatusError):
                    last_error = _sys.exc_info()[1]
                    if attempt < 2:
                        logger.debug(
                            "Batch flush attempt %d/3 failed (retry in 5s): %s",
                            attempt + 1,
                            last_error,
                        )
                        await asyncio.sleep(5)
                    else:
                        logger.warning(
                            "Batch flush failed after 3 attempts: %d facts lost — %s",
                            len(batch),
                            last_error,
                        )
                except _json.JSONDecodeError as e:
                    # JSON parse error — not retryable (prompt/format issue)
                    logger.debug(
                        "Batch parse error (non-retryable): %d messages — %s",
                        len(batch),
                        e,
                    )
                    break
                except Exception:
                    logger.error(
                        "Unexpected error in batch flush (non-retryable): %d facts lost",
                        len(batch),
                        exc_info=True,
                    )
                    raise

            if raw_json is None:
                return  # все попытки исчерпаны или не-retryable ошибка

            try:
                parsed = _parse_batch_facts(raw_json)

                # Сопоставить результаты с сообщениями по индексу
                facts_by_index: dict[int, list[dict[str, str]]] = {}
                for idx, facts in parsed:
                    facts_by_index[idx] = facts

                total_facts = 0
                for i, msg in enumerate(batch, 1):
                    facts = facts_by_index.get(i, [])
                    if facts:
                        stored = await _save_facts_to_db(msg["telegram_id"], facts)
                        total_facts += stored

                logger.debug(
                    "Batch auto-save: %d messages → %d facts saved",
                    len(batch),
                    total_facts,
                )

            except asyncio.CancelledError:
                logger.warning(
                    "Batch flush cancelled with %d facts — данные могут быть утеряны, msg_ids=%s",
                    len(batch),
                    [m.get("telegram_id") for m in batch],
                )
                raise
            except (
                RequestError,
                HTTPStatusError,
                SQLAlchemyError,
                _json.JSONDecodeError,
            ):
                logger.debug(
                    "Batch auto-save failed for %d messages", len(batch), exc_info=True
                )
            except Exception:
                logger.exception(
                    "Unexpected error during batch auto-save (%d messages)", len(batch)
                )
        finally:
            self._is_flushing = False
            # M1: если во время flush в буфер добавились новые сообщения —
            # запускаем таймер для их сброса (раньше буфер «зависал» до следующего add)
            if self._buffer:
                self._flush_task = asyncio.create_task(
                    self._timeout_flush(), name="auto-save-flush-post-flush"
                )


# ══════════════════════════════════════════════════════════════════════════
# Глобальный экземпляр (ленивая инициализация)
# ══════════════════════════════════════════════════════════════════════════

_batch_buffer: FactBatchBuffer | None = None
_buffer_lock = asyncio.Lock()


async def get_batch_buffer() -> FactBatchBuffer:
    """Вернуть глобальный экземпляр FactBatchBuffer (создать при первом вызове)."""
    global _batch_buffer
    if _batch_buffer is not None:
        return _batch_buffer
    async with _buffer_lock:
        if _batch_buffer is not None:
            return _batch_buffer
        _batch_buffer = FactBatchBuffer(
            batch_size=settings.auto_save_batch_size,
            timeout=settings.auto_save_batch_timeout,
            enabled=settings.auto_save_batch_enabled,
            max_wait=settings.auto_save_batch_max_wait,
        )
        logger.info(
            "FactBatchBuffer initialized: batch_size=%d, timeout=%.1fs, max_wait=%.1fs, enabled=%s",
            settings.auto_save_batch_size,
            settings.auto_save_batch_timeout,
            settings.auto_save_batch_max_wait,
            settings.auto_save_batch_enabled,
        )
        return _batch_buffer


def reset_batch_buffer() -> None:
    """Сбросить глобальный буфер (для тестов)."""
    global _batch_buffer
    if _batch_buffer is not None:
        # Защита от гонки: сбрасываем ссылку атомарно,
        # существующий буфер продолжит работу до завершения текущих операций.
        _batch_buffer = None
