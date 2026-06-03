"""Persona persistence — DB operations and snapshots."""

import json
import logging

from src.db.session import get_session
from src.db.repo import get_or_create_user, get_persona, update_persona
from src.core.intelligence.persona_prompts import (
    BASE_TONE_PROMPTS,
    LEVEL_PROMPTS,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1. apply_persona_changes — сохранить изменения в БД и сформировать правила
# ──────────────────────────────────────────────────────────────────────────────


async def apply_persona_changes(telegram_id: int, changes: dict):
    """Применяет изменения к persona."""

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)

        # SELECT ... FOR UPDATE — пессимистичная блокировка строки
        # предотвращает read-modify-write race при параллельной адаптации
        from sqlalchemy import select as sa_select
        from src.db.models._learning import AdaptivePersona

        stmt = (
            sa_select(AdaptivePersona)
            .where(AdaptivePersona.user_id == owner.id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        p = result.scalar_one_or_none()

        if p is None:
            return None

        # Apply the requested changes to persona in DB
        if changes:
            await update_persona(session, p, **changes)

    rules = []
    if p.brevity == "short":
        rules.append("отвечай коротко (1-2 предложения)")
    elif p.brevity == "detailed":
        rules.append("отвечай подробно")
    if p.formality == "formal":
        rules.append("формальный тон, на «вы»")
    elif p.formality == "casual":
        rules.append("очень неформально, с юмором")
    if p.initiative == "proactive":
        rules.append("проявляй инициативу — предлагай, напоминай, спрашивай")
    elif p.initiative == "reactive":
        rules.append("только отвечай на вопросы, не предлагай сам")
    if p.preferred_format == "bullets":
        rules.append("форматируй списком")
    elif p.preferred_format == "numbered":
        rules.append("нумеруй пункты")
    if p.max_response_len:
        rules.append(f"ответ не длиннее {p.max_response_len} символов")
    if p.work_mode == "focus":
        rules.append("режим фокуса — не отвлекай, только срочное")
    elif p.work_mode == "relax":
        rules.append("режим отдыха — только приятное общение")

    # -- Новые поля личности (ChatGPT-style) --

    # Базовый тон
    if p.base_tone and p.base_tone != "default":
        tone_prompt = BASE_TONE_PROMPTS.get(p.base_tone, "")
        if tone_prompt:
            rules.append(tone_prompt)

    # Теплота
    if p.warmth and p.warmth != "normal":
        warmth_text = LEVEL_PROMPTS["warmth"].get(p.warmth, "")
        if warmth_text:
            rules.append(warmth_text)

    # Энтузиазм
    if p.enthusiasm and p.enthusiasm != "normal":
        enthusiasm_text = LEVEL_PROMPTS["enthusiasm"].get(p.enthusiasm, "")
        if enthusiasm_text:
            rules.append(enthusiasm_text)

    # Заголовки/списки
    if p.headings_lists and p.headings_lists != "normal":
        hl_text = LEVEL_PROMPTS["headings_lists"].get(p.headings_lists, "")
        if hl_text:
            rules.append(hl_text)

    # Эмодзи: новое поле emoji_level имеет приоритет над старым emoji_usage
    if p.emoji_level and p.emoji_level != "normal":
        emoji_text = LEVEL_PROMPTS["emoji_level"].get(p.emoji_level, "")
        if emoji_text:
            rules.append(emoji_text)
    else:
        # Старое поле — только если emoji_level не переопределён
        if p.emoji_usage == "none":
            rules.append("НЕ используй эмодзи")
        elif p.emoji_usage == "minimal":
            rules.append("минимум эмодзи")
        elif p.emoji_usage == "rich":
            rules.append("используй больше эмодзи")

    # Обращение
    if p.alias:
        rules.append(f"обращайся ко мне «{p.alias}»")

    # Пользовательские инструкции (свободный текст)
    if p.custom_instructions:
        rules.append(f"ДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ ВЛАДЕЛЬЦА:\n{p.custom_instructions}")

    if not rules:
        result = ""
    else:
        result = "\n\n## ТВОЙ СТИЛЬ ОБЩЕНИЯ (установлен владельцем):\n" + "\n".join(
            f"- {r}" for r in rules
        )

    from src.core.context_cache import put as cache_put

    await cache_put(f"persona:{telegram_id}", result, ttl=5)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# 2. _make_snapshot — создание JSON-снапшота
# ──────────────────────────────────────────────────────────────────────────────


def _make_snapshot(persona) -> str:
    """Создаёт JSON-снапшот базовых настроек persona для возможности сброса."""
    snapshot = {
        "base_tone": persona.base_tone,
        "warmth": persona.warmth,
        "enthusiasm": persona.enthusiasm,
        "headings_lists": persona.headings_lists,
        "emoji_level": persona.emoji_level,
        "custom_instructions": persona.custom_instructions,
        "alias": persona.alias,
    }
    return json.dumps(snapshot, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────────────
# 3. reset_persona_to_snapshot — сброс к снапшоту
# ──────────────────────────────────────────────────────────────────────────────


async def reset_persona_to_snapshot(telegram_id: int) -> bool:
    """Сбрасывает persona к базовому снапшоту. Возвращает True если сброс выполнен."""
    from src.core.context_cache import invalidate as cache_invalidate

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        p = await get_persona(session, owner)

        if not p.base_snapshot_json:
            return False

        try:
            snapshot = json.loads(p.base_snapshot_json)
        except Exception:
            return False

        for field, value in snapshot.items():
            if hasattr(p, field):
                setattr(p, field, value)

        p.base_snapshot_json = None  # Снапшот использован
        await session.commit()

        await cache_invalidate(f"persona:{telegram_id}")

        return True


# ──────────────────────────────────────────────────────────────────────────────
# 4. adapt_persona_from_feedback — адаптация на основе обратной связи пользователя
# ──────────────────────────────────────────────────────────────────────────────


async def adapt_persona_from_feedback(
    telegram_id: int, feedback_text: str
) -> dict | None:
    """
    Анализирует обратную связь пользователя и плавно корректирует persona.

    Возвращает словарь с изменениями или None если ничего не изменено.
    Работает только при adaptive_mode_enabled=True.
    """
    from src.core.context_cache import invalidate as cache_invalidate

    text = feedback_text.lower().strip()

    adjustments = {}

    # Тон
    if any(w in text for w in ["серьёзнее", "официальнее", "формальнее", "строже"]):
        adjustments["base_tone"] = "professional"
    elif any(w in text for w in ["дружелюбнее", "проще", "теплее", "мягче"]):
        adjustments["base_tone"] = "friendly"
    elif any(w in text for w in ["короче", "быстрее", "лаконичнее", "без воды"]):
        adjustments["base_tone"] = "efficient"
    elif any(w in text for w in ["веселее", "игривее", "креативнее", "шутливее"]):
        adjustments["base_tone"] = "whimsical"
    elif any(
        w in text for w in ["увереннее", "настойчивее", "жёстче", "assertive", "спорь"]
    ):
        adjustments["base_tone"] = "assertive"
    elif any(
        w in text
        for w in ["дерзко", "бунтарски", "rebellious", "провокационно", "восстань"]
    ):
        adjustments["base_tone"] = "rebellious"

    # Теплота
    if any(w in text for w in ["теплее", "душевнее", "ближе"]):
        adjustments["warmth"] = "high"
    elif any(w in text for w in ["холоднее", "отстранённее", "нейтральнее"]):
        adjustments["warmth"] = "low"

    # Энтузиазм
    if any(w in text for w in ["энергичнее", "бодрее", "активнее", "восторженнее"]):
        adjustments["enthusiasm"] = "high"
    elif any(w in text for w in ["спокойнее", "тише", "медленнее"]):
        adjustments["enthusiasm"] = "low"

    # Эмодзи
    if any(
        w in text
        for w in ["меньше эмодзи", "меньше смайлов", "без эмодзи", "без смайлов"]
    ):
        adjustments["emoji_level"] = "low"
    elif any(w in text for w in ["больше эмодзи", "больше смайлов", "добавь эмодзи"]):
        adjustments["emoji_level"] = "high"

    # Заголовки/списки
    if any(w in text for w in ["больше списков", "структурируй", "форматируй"]):
        adjustments["headings_lists"] = "high"
    elif any(w in text for w in ["меньше списков", "без списков", "сплошным текстом"]):
        adjustments["headings_lists"] = "low"

    if not adjustments:
        return None

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        p = await get_persona(session, owner)

        if not p.adaptive_mode_enabled:
            return None  # Адаптивный режим выключен

        # Сохраняем снапшот ДО изменений (чтобы сброс работал корректно)
        if not p.base_snapshot_json:
            p.base_snapshot_json = _make_snapshot(p)

        changes = {}
        for field, value in adjustments.items():
            old = getattr(p, field)
            if old != value:
                setattr(p, field, value)
                changes[field] = {"old": old, "new": value}

        if changes:
            p.total_corrections = (p.total_corrections or 0) + 1
            await session.commit()
            await cache_invalidate(f"persona:{telegram_id}")

        return changes
