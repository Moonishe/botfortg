"""Persona adaptation — context-aware persona adjustment."""

import json
import logging

from src.db.session import get_session
from src.db.repo import get_or_create_user, get_persona
from src.core.intelligence.persona_prompts import (
    ARCHETYPE_TO_PERSONA,
    BASE_TONE_PROMPTS,
    LEVEL_PROMPTS,
    MOOD_ADAPTATIONS,
    TONE_NAMES,
)
from src.core.intelligence.persona_detector import (
    _classify_contact_relation,
    _detect_contact_name,
    analyze_user_mood,
)
from src.core.intelligence.persona_persistence import _make_snapshot

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Per-contact адаптация
# ═══════════════════════════════════════════════════════════════════════════════


async def get_contact_persona_override(
    telegram_id: int, contact_name: str
) -> dict[str, str] | None:
    """
    Получает persona-коррекцию для конкретного контакта.

    Приоритет:
    1. Архетип из RELATION_MARKERS (быстрый keyword)
    2. ContactProfile.archetype из БД
    3. style_profile (как пользователь реально пишет этому контакту)
    """
    # 1. Быстрая классификация по имени/роли
    archetype = _classify_contact_relation(contact_name)
    if archetype and archetype in ARCHETYPE_TO_PERSONA:
        logger.debug("Contact %s → archetype %s (keyword)", contact_name, archetype)
        return dict(ARCHETYPE_TO_PERSONA[archetype])

    # 2. Поиск в БД: Contact + ContactProfile
    try:
        from src.db.session import get_session as db_get_session
        from src.db.repo import get_or_create_user as db_get_user
        from src.db.models._contacts import Contact
        from sqlalchemy import select as sa_select

        async with db_get_session() as session:
            owner = await db_get_user(session, telegram_id)
            # Ищем контакт по display_name (fuzzy)
            stmt = (
                sa_select(Contact)
                .where(
                    Contact.user_id == owner.id,
                    Contact.display_name.ilike(f"%{contact_name}%"),
                )
                .limit(1)
            )
            result = await session.execute(stmt)
            contact = result.scalar_one_or_none()

            if contact is None:
                return None

            # 3. Проверяем ContactProfile
            from src.db.models._contacts import ContactProfile

            stmt2 = (
                sa_select(ContactProfile)
                .where(
                    ContactProfile.user_id == owner.id,
                    ContactProfile.contact_id == contact.peer_id,
                )
                .limit(1)
            )
            result2 = await session.execute(stmt2)
            profile = result2.scalar_one_or_none()

            if profile and profile.closeness_label:
                # Маппим closeness_label → archetype
                closeness = profile.closeness_label.lower()
                if any(w in closeness for w in ["близк", "друг", "best"]):
                    return dict(ARCHETYPE_TO_PERSONA["close_friend"])
                elif any(w in closeness for w in ["семь", "родн", "family"]):
                    return dict(ARCHETYPE_TO_PERSONA["family"])
                elif any(w in closeness for w in ["коллег", "работ", "colleague"]):
                    return dict(ARCHETYPE_TO_PERSONA["colleague"])
                elif any(w in closeness for w in ["романт", "любов", "romantic"]):
                    return dict(ARCHETYPE_TO_PERSONA["romantic"])
                elif any(w in closeness for w in ["знаком", "acquaint"]):
                    return dict(ARCHETYPE_TO_PERSONA["acquaintance"])
                elif any(w in closeness for w in ["токсич", "toxic", "конфликт"]):
                    return dict(ARCHETYPE_TO_PERSONA["toxic"])

            # 4. Если есть communication_style — используем его
            if profile and profile.communication_style:
                style = profile.communication_style.lower()
                if any(w in style for w in ["формаль", "офиц", "делов"]):
                    return dict(ARCHETYPE_TO_PERSONA["colleague"])
                elif any(w in style for w in ["друже", "неформ", "разговор"]):
                    return dict(ARCHETYPE_TO_PERSONA["friend"])

    except Exception:
        logger.debug("Contact persona override lookup failed", exc_info=True)

    return None


async def _merge_persona_overrides(
    mood_overrides: dict[str, str] | None,
    contact_overrides: dict[str, str] | None,
) -> dict[str, str]:
    """
    Объединяет коррекции настроения и контакта.

    Контакт имеет приоритет над настроением: если ты пишешь боссу,
    тон должен быть профессиональным, даже если ты в игривом настроении.
    Но настроение смягчает: angry + босс = сдержанный профессионал,
    happy + босс = вежливый тёплый профессионал.
    """
    if not mood_overrides and not contact_overrides:
        return {}
    if not mood_overrides:
        return dict(contact_overrides)  # type: ignore[arg-type]
    if not contact_overrides:
        return dict(mood_overrides)

    # Контакт — база, настроение — модулятор
    merged = dict(contact_overrides)

    # Настроение может влиять на enthusiasm, emoji_level, warmth
    # но НЕ на base_tone (контакт диктует тон)
    for mood_field in ("enthusiasm", "emoji_level", "warmth", "brevity"):
        if mood_field in mood_overrides:
            # Если контакт уже задал это поле — оставляем контакт
            if mood_field not in merged:
                merged[mood_field] = mood_overrides[mood_field]

    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# auto_adapt_from_context — автоматическая адаптация на каждое сообщение
# ═══════════════════════════════════════════════════════════════════════════════


async def auto_adapt_from_context(
    telegram_id: int,
    user_text: str,
    provider=None,
) -> dict | None:
    """
    Автоматическая адаптация стиля на основе настроения и контекста.

    Вызывается на КАЖДОЕ сообщение (если adaptive_mode_enabled).
    Не ждёт явной команды «измени стиль» — анализирует настроение и
    плавно корректирует persona.

    Возвращает словарь с изменениями или None.
    """
    from src.core.context_cache import invalidate as cache_invalidate

    # 1. Быстрая проверка: включён ли адаптивный режим
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        p = await get_persona(session, owner)
        if not p.adaptive_mode_enabled:
            return None

    # 2. Анализ настроения
    mood = await analyze_user_mood(telegram_id, user_text, provider)

    # 3. Анализ контакта: кому пишет пользователь?
    contact_name = _detect_contact_name(user_text)
    contact_overrides = None
    if contact_name:
        contact_overrides = await get_contact_persona_override(
            telegram_id, contact_name
        )
        if contact_overrides:
            logger.debug(
                "Contact detected: %s → overrides=%s", contact_name, contact_overrides
            )

    # 4. Объединяем коррекции: контакт > настроение
    mood_overrides = MOOD_ADAPTATIONS.get(mood) if mood else None
    target = await _merge_persona_overrides(mood_overrides, contact_overrides)

    if not target:
        return None

    # 5. Проверяем явную обратную связь (тоже через текст)
    #    чтобы не конфликтовать с adapt_persona_from_feedback
    from src.core.context_cache import get as cache_get

    # Защита от слишком частых изменений:
    # - mood-only: 120 сек
    # - contact-based: 30 сек (контакт важнее)
    last_adapt_key = f"adapt_ts:{telegram_id}"
    last_ts = await cache_get(last_adapt_key)
    now = __import__("time").monotonic()
    cooldown = 30 if contact_overrides else 120
    if last_ts is not None and (now - last_ts) < cooldown:
        return None

    # 6. Применяем изменения
    from sqlalchemy import select as sa_select
    from src.db.models._learning import AdaptivePersona

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id, use_cache=False)
        stmt = (
            sa_select(AdaptivePersona)
            .where(AdaptivePersona.user_id == owner.id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        p = result.scalar_one_or_none()
        if p is None or not p.adaptive_mode_enabled:
            return None

        # Сохраняем снапшот до изменений
        if not p.base_snapshot_json:
            p.base_snapshot_json = _make_snapshot(p)

        changes = {}
        for field, value in target.items():
            # Только для полей, которые есть в модели
            if not hasattr(p, field):
                continue
            old = getattr(p, field)

            # Пропускаем если уже совпадает или если поле не строковое
            if old == value:
                continue
            if not isinstance(old, str) or not isinstance(value, str):
                continue

            # Мягкая коррекция: не прыгаем, а смещаем на 1 шаг
            level_order = {"low": 0, "normal": 1, "high": 2}
            if old in level_order and value in level_order:
                old_idx = level_order[old]
                target_idx = level_order[value]
                if old_idx == target_idx:
                    continue
                step = 1 if target_idx > old_idx else -1
                new_idx = max(0, min(2, old_idx + step))
                new_val = {0: "low", 1: "normal", 2: "high"}[new_idx]
                if new_val != old:
                    setattr(p, field, new_val)
                    changes[field] = {"old": old, "new": new_val}
            else:
                # Для не-уровневых полей (base_tone) — применяем сразу
                setattr(p, field, value)
                changes[field] = {"old": old, "new": value}

        if changes:
            p.total_corrections = (p.total_corrections or 0) + 1
            await session.commit()
            await cache_invalidate(f"persona:{telegram_id}")
            from src.core.context_cache import put as cache_put

            await cache_put(last_adapt_key, now, ttl=130)

            logger.info(
                "Auto-adapt: user=%s mood=%s contact=%s changes=%s",
                telegram_id,
                mood or "none",
                contact_name or "none",
                {k: f"{v['old']}→{v['new']}" for k, v in changes.items()},
            )

        return changes if changes else None


# ═══════════════════════════════════════════════════════════════════════════════
# format_persona_for_prompt — форматирует persona в блок для system prompt
# ═══════════════════════════════════════════════════════════════════════════════


async def format_persona_for_prompt(telegram_id: int) -> str | None:
    """Собирает блок persona для вставки в system prompt.

    Поддерживает:
    - base_tone / tone_mix (из custom_instructions)
    - experience (из custom_instructions)
    - остальные поля (brevity, formality, etc.)
    """
    from src.core.context_cache import get as cache_get

    # Кеш: persona обновляется не чаще раза в 5 секунд
    cached = await cache_get(f"persona:{telegram_id}")
    if cached:
        return cached

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        p = await get_persona(session, owner)

    rules: list[str] = []

    # --- Tone mix (из custom_instructions) ---
    try:
        if p.custom_instructions:
            ci = (
                json.loads(p.custom_instructions)
                if isinstance(p.custom_instructions, str)
                else p.custom_instructions
            )
            tone_mix = ci.get("tone_mix") if isinstance(ci, dict) else None
            experience = ci.get("experience") if isinstance(ci, dict) else None
        else:
            tone_mix = None
            experience = None
    except (json.JSONDecodeError, TypeError):
        tone_mix = None
        experience = None
        # Если custom_instructions — просто текст, используем как есть
        if p.custom_instructions and isinstance(p.custom_instructions, str):
            rules.append(p.custom_instructions)

    # Tone mix: {"assertive": 50, "friendly": 30, ...}
    if tone_mix and isinstance(tone_mix, dict) and len(tone_mix) > 1:
        mix_lines: list[str] = []
        # Сортируем по убыванию процента
        sorted_mix = sorted(tone_mix.items(), key=lambda x: x[1], reverse=True)
        for tone, pct in sorted_mix:
            name = TONE_NAMES.get(tone, tone)
            if pct >= 10:  # только значимые компоненты
                mix_lines.append(f"- {name} ({pct}%)")
        if mix_lines:
            rules.append(
                "ТВОЙ СТИЛЬ — ЭТО КОКТЕЙЛЬ ТОНОВ:\n"
                + "\n".join(mix_lines)
                + "\nКомбинируй их естественно, как живой человек с многогранным характером."
            )
    elif tone_mix and isinstance(tone_mix, dict) and len(tone_mix) == 1:
        # Всего один тон — используем как base_tone
        sole_tone = list(tone_mix.keys())[0]
        if sole_tone in BASE_TONE_PROMPTS:
            tone_prompt = BASE_TONE_PROMPTS[sole_tone]
            if tone_prompt:
                rules.append(tone_prompt)

    # --- Base tone (если нет tone_mix) ---
    if not tone_mix and p.base_tone and p.base_tone != "default":
        tone_prompt = BASE_TONE_PROMPTS.get(p.base_tone, "")
        if tone_prompt:
            rules.append(tone_prompt)

    # --- Теплота ---
    if p.warmth and p.warmth != "normal":
        warmth_text = LEVEL_PROMPTS["warmth"].get(p.warmth, "")
        if warmth_text:
            rules.append(warmth_text)

    # --- Энтузиазм ---
    if p.enthusiasm and p.enthusiasm != "normal":
        enthusiasm_text = LEVEL_PROMPTS["enthusiasm"].get(p.enthusiasm, "")
        if enthusiasm_text:
            rules.append(enthusiasm_text)

    # --- Заголовки/списки ---
    if p.headings_lists and p.headings_lists != "normal":
        hl_text = LEVEL_PROMPTS["headings_lists"].get(p.headings_lists, "")
        if hl_text:
            rules.append(hl_text)

    # --- Старые поля (brevity, formality, initiative, work_mode) ---
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

    # --- Эмодзи ---
    if p.emoji_level and p.emoji_level != "normal":
        emoji_text = LEVEL_PROMPTS["emoji_level"].get(p.emoji_level, "")
        if emoji_text:
            rules.append(emoji_text)
    else:
        if p.emoji_usage == "none":
            rules.append("НЕ используй эмодзи")
        elif p.emoji_usage == "minimal":
            rules.append("минимум эмодзи")
        elif p.emoji_usage == "rich":
            rules.append("используй больше эмодзи")

    if p.work_mode == "focus":
        rules.append("режим фокуса — не отвлекай, только срочное")
    elif p.work_mode == "relax":
        rules.append("режим отдыха — только приятное общение")
    if p.max_response_len:
        rules.append(f"ответ не длиннее {p.max_response_len} символов")
    if p.alias:
        rules.append(f"обращайся ко мне «{p.alias}»")

    # --- Experience (вывод из опыта) ---
    if experience and isinstance(experience, str) and len(experience.strip()) > 10:
        rules.append(f"ИЗ ОПЫТА ОБЩЕНИЯ:\n{experience.strip()[:500]}")

    # --- Plain text custom_instructions (если не JSON) ---
    if (
        p.custom_instructions
        and isinstance(p.custom_instructions, str)
        and not tone_mix
        and not experience
    ):
        try:
            json.loads(p.custom_instructions)
            # Это JSON — уже обработали выше
        except (json.JSONDecodeError, TypeError):
            if p.custom_instructions.strip():
                rules.append(
                    f"ДОПОЛНИТЕЛЬНЫЕ ИНСТРУКЦИИ ВЛАДЕЛЬЦА:\n{p.custom_instructions}"
                )

    if not rules:
        return None

    result = "\n\n## ТВОЙ СТИЛЬ ОБЩЕНИЯ (установлен владельцем):\n" + "\n".join(
        f"- {r}" for r in rules
    )

    # Кешируем
    from src.core.context_cache import put as cache_put

    await cache_put(f"persona:{telegram_id}", result, ttl=5)
    return result
