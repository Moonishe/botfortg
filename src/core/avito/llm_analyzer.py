"""LLM-анализ объявлений Авито.

Использует существующую LLM-инфраструктуру бота для оценки
качества сделки, выявления red flags и генерации рекомендаций
на основе полного описания и характеристик товара.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
#  Промпт для анализа
# ═══════════════════════════════════════════════════════════════════════════

_ANALYSIS_SYSTEM_PROMPT = """Ты — эксперт по анализу объявлений на Авито (российская доска объявлений).
Твоя задача: оценить выгодность сделки и выявить потенциальные риски.

Оценивай:
1. Соответствие цены рыночной (если указана средняя цена)
2. Состояние товара и его влияние на цену
3. Надёжность продавца (рейтинг, отзывы, дата регистрации)
4. Полноту и качество описания
5. Наличие скрытых дефектов или подозрительных формулировок
6. Качество фотографий и их достаточность

Ответь СТРОГО в JSON-формате без лишнего текста:
{
    "deal_quality": <число 0-100>,
    "red_flags": ["флаг1", "флаг2", ...],
    "recommendation": "<buy | consider | skip | investigate>",
    "summary": "<1-2 предложения>",
    "reasoning": "<подробное обоснование>"
}

Рекомендации:
- "buy" — отличная сделка, стоит брать
- "consider" — нормальная сделка, можно рассмотреть
- "skip" — плохая сделка или подозрительно
- "investigate" — нужна дополнительная проверка перед покупкой
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Формирование пользовательского промпта
# ═══════════════════════════════════════════════════════════════════════════


def _build_user_prompt(listing: dict[str, Any]) -> str:
    """Собирает пользовательский промпт из данных объявления."""
    parts: list[str] = []

    title = listing.get("title", "")
    if title:
        parts.append(f"Заголовок: {title}")

    price = listing.get("price")
    if price is not None:
        parts.append(f"Цена: {price} ₽")

    condition = listing.get("condition", "")
    if condition:
        parts.append(f"Состояние: {condition}")

    city = listing.get("city", "")
    if city:
        parts.append(f"Город: {city}")

    delivery = listing.get("delivery")
    if delivery:
        parts.append("Доставка: доступна")
    else:
        parts.append("Доставка: не указана")

    # Продавец
    seller_parts: list[str] = []
    seller_name = listing.get("seller_name", "")
    if seller_name:
        seller_parts.append(f"Продавец: {seller_name}")

    seller_rating = listing.get("seller_rating")
    if seller_rating is not None:
        seller_parts.append(f"Рейтинг: {seller_rating}/5")

    seller_reviews = listing.get("seller_reviews")
    if seller_reviews is not None:
        seller_parts.append(f"Отзывов: {seller_reviews}")

    seller_joined = listing.get("seller_joined_date")
    if seller_joined:
        seller_parts.append(f"На Авито с: {seller_joined}")

    seller_other = listing.get("seller_other_listings_count")
    if seller_other is not None:
        seller_parts.append(f"Других объявлений: {seller_other}")

    if seller_parts:
        parts.append(" | ".join(seller_parts))

    # Полное описание (ключевая часть)
    full_description = listing.get("full_description", "")
    if full_description:
        parts.append(f"\nОписание:\n{full_description}")
    else:
        # Fallback к короткому описанию из поиска
        short_desc = listing.get("description", "")
        if short_desc:
            parts.append(f"\nКраткое описание:\n{short_desc}")

    # Характеристики
    characteristics = listing.get("listing_characteristics", {})
    if characteristics:
        chars_lines = ["\nХарактеристики:"]
        for key, val in characteristics.items():
            chars_lines.append(f"  {key}: {val}")
        parts.append("\n".join(chars_lines))

    # Дополнительные данные
    deal_score = listing.get("deal_score", {})
    if isinstance(deal_score, dict) and deal_score.get("score"):
        parts.append(f"\nОценка эвристики: {deal_score['score']}/100")

    view_count = listing.get("view_count")
    if view_count is not None:
        parts.append(f"Просмотров: {view_count}")

    scam_check = listing.get("scam_check", {})
    if isinstance(scam_check, dict):
        risk = scam_check.get("risk", "low")
        reasons = scam_check.get("reasons", [])
        if risk != "low" or reasons:
            parts.append(f"\nПодозрения ({risk}): {'; '.join(reasons)}")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
#  Вспомогательные: создание провайдера
# ═══════════════════════════════════════════════════════════════════════════


async def _get_llm_provider() -> Any | None:
    """Пытается получить LLM-провайдер через существующую инфраструктуру.

    Использует lazy import чтобы не тянуть тяжёлые зависимости при старте.
    Возвращает объект с методом chat(messages, *, heavy=False) -> str.
    """
    try:
        from src.config import settings as app_settings

        openai_key = getattr(app_settings, "openai_api_key", None)
        if not openai_key:
            try:
                import os

                openai_key = os.environ.get("OPENAI_API_KEY")
            except Exception:
                logger.debug("Non-critical error", exc_info=True)

        if not openai_key:
            logger.debug("_get_llm_provider: нет API-ключа")
            return None

        base_url = getattr(app_settings, "openai_base_url", "") or None

        # Пробуем через существующий OpenAIProvider
        try:
            from src.llm.openai_provider import OpenAIProvider

            return OpenAIProvider(
                api_key=openai_key,
                base_url=base_url,
            )
        except ImportError:
            pass

        # Fallback: прямой вызов через openai
        try:
            from openai import AsyncOpenAI

            client_kwargs: dict = {"api_key": openai_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            return AsyncOpenAI(**client_kwargs)
        except ImportError:
            pass

    except Exception:
        logger.exception("_get_llm_provider: ошибка инициализации")

    return None


# ═══════════════════════════════════════════════════════════════════════════
#  Публичный API
# ═══════════════════════════════════════════════════════════════════════════


async def analyze_listing_llm(
    listing: dict[str, Any],
    *,
    provider: Any = None,
) -> dict[str, Any]:
    """Анализирует объявление через LLM.

    Args:
        listing: Данные объявления (из parser + listing_fetcher).
        provider: LLM-провайдер (опционально, lazy import если None).

    Returns:
        dict: {
            "deal_quality": int 0-100,
            "red_flags": list[str],
            "recommendation": str,
            "summary": str,
            "reasoning": str,
            "error": str | None,
        }
    """
    result: dict[str, Any] = {
        "deal_quality": 0,
        "red_flags": [],
        "recommendation": "skip",
        "summary": "",
        "reasoning": "",
        "error": None,
    }

    # Проверяем конфигурацию
    try:
        from src.config import settings

        if not settings.avito_llm_analysis:
            result["error"] = "LLM-анализ отключён в настройках"
            return result
    except Exception:
        logger.debug("analyze_listing_llm: не удалось загрузить настройки")
        # Продолжаем без проверки

    # Собираем промпт
    user_prompt = _build_user_prompt(listing)
    if len(user_prompt.strip()) < 20:
        result["error"] = "Недостаточно данных для анализа"
        return result

    # Получаем провайдер
    if provider is None:
        provider = await _get_llm_provider()

    if provider is None:
        result["error"] = "LLM-провайдер недоступен"
        return result

    # Отправляем запрос
    try:
        # Определяем как вызывать чат (у разных провайдеров разный API)
        response_text: str | None = None

        # AsyncOpenAI — имеет .chat.completions.create (проверяем ПЕРВЫМ)
        if hasattr(provider, "chat") and hasattr(
            getattr(provider, "chat", None), "completions"
        ):
            resp = await provider.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
            )
            response_text = resp.choices[0].message.content or ""

        # OpenAIProvider — имеет метод chat() (проверяем ВТОРЫМ)
        elif hasattr(provider, "chat"):
            # Пробуем через ChatMessage (стандартный интерфейс)
            try:
                from src.llm.base import ChatMessage

                response_text = await provider.chat(
                    messages=[
                        ChatMessage(role="system", content=_ANALYSIS_SYSTEM_PROMPT),
                        ChatMessage(role="user", content=user_prompt),
                    ],
                )
            except TypeError:
                # Может принимать dict вместо ChatMessage
                response_text = await provider.chat(
                    messages=[
                        {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                )

        else:
            result["error"] = "Неподдерживаемый тип провайдера"
            return result

        if not response_text:
            result["error"] = "Пустой ответ от LLM"
            return result

        # Парсим JSON из ответа
        content = response_text.strip()

        # Убираем markdown-обёртку если есть
        if content.startswith("```"):
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1:
                content = content[start : end + 1]

        parsed = json.loads(content)

        result["deal_quality"] = max(0, min(100, int(parsed.get("deal_quality", 0))))
        result["red_flags"] = parsed.get("red_flags", [])
        result["recommendation"] = parsed.get("recommendation", "skip")
        result["summary"] = parsed.get("summary", "")
        result["reasoning"] = parsed.get("reasoning", "")

        logger.info(
            "analyze_listing_llm: quality=%d, recommendation=%s, flags=%d",
            result["deal_quality"],
            result["recommendation"],
            len(result["red_flags"]),
        )

    except (json.JSONDecodeError, ValueError) as exc:
        result["error"] = f"Ошибка парсинга ответа LLM: {exc}"
        logger.warning("analyze_listing_llm: невалидный JSON от LLM")
    except Exception:
        result["error"] = "Ошибка LLM-анализа"
        logger.exception("analyze_listing_llm: ошибка")

    return result
