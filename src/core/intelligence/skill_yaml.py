"""YAML frontmatter utilities for skill metadata.

Позволяет хранить структурированные метаданные в поле description навыка
в формате YAML frontmatter, не меняя схему SQLite.
"""

from __future__ import annotations

import logging
from typing import Any

from src.db.models import Skill

logger = logging.getLogger(__name__)

# Ключи, которые маппятся из YAML frontmatter в структурированные метаданные
_YAML_METADATA_KEYS = frozenset(
    {
        "tags",
        "category",
        "priority",
        "cooldown_seconds",
        "requires_contact",
        "example_usage",
    }
)


def parse_skill_frontmatter(skill: Skill) -> dict[str, Any]:
    """Извлекает YAML frontmatter из skill.description если он есть.

    Формат frontmatter:
        ---
        tags: [поиск, контакты]
        category: search
        priority: 5
        cooldown_seconds: 60
        requires_contact: true
        example_usage: "найди Петю"
        ---
        Остальной текст описания...

    Returns:
        Словарь с ключами:
        - "metadata": dict — распарсенные метаданные из frontmatter
        - "description": str — текст описания БЕЗ frontmatter-блока
        - "has_frontmatter": bool
    """
    desc = (skill.description or "").strip()
    if not desc.startswith("---"):
        return {"metadata": {}, "description": desc, "has_frontmatter": False}

    # Ищем закрывающий ---
    rest = desc[3:]  # после первого ---
    end_idx = rest.find("---")
    if end_idx == -1:
        # Нет закрывающего блока — это не frontmatter
        return {"metadata": {}, "description": desc, "has_frontmatter": False}

    yaml_block = rest[:end_idx].strip()
    remaining_desc = rest[end_idx + 3 :].strip()

    if not yaml_block:
        return {"metadata": {}, "description": remaining_desc, "has_frontmatter": False}

    try:
        import yaml

        metadata = yaml.safe_load(yaml_block)
        if not isinstance(metadata, dict):
            metadata = {}
    except Exception as e:
        logger.debug("YAML frontmatter parse error: %s", e)
        return {"metadata": {}, "description": desc, "has_frontmatter": False}

    # Фильтруем только известные ключи
    filtered = {}
    for key in _YAML_METADATA_KEYS:
        if key in metadata:
            filtered[key] = metadata[key]

    return {
        "metadata": filtered,
        "description": remaining_desc,
        "has_frontmatter": bool(filtered),
    }


def format_skill_frontmatter(metadata: dict[str, Any]) -> str:
    """Генерирует YAML frontmatter строку из словаря метаданных.

    Args:
        metadata: Словарь с ключами tags, category, priority, cooldown_seconds,
                  requires_contact, example_usage.

    Returns:
        Строка вида:
        ---
        tags: [tag1, tag2]
        category: search
        ---
    """
    if not metadata:
        return ""

    try:
        import yaml

        filtered = {k: v for k, v in metadata.items() if k in _YAML_METADATA_KEYS}
        if not filtered:
            return ""
        yaml_str = yaml.dump(
            filtered, default_flow_style=False, allow_unicode=True
        ).strip()
        return f"---\n{yaml_str}\n---"
    except Exception as e:
        logger.warning("YAML frontmatter format error: %s", e)
        return ""


def extract_frontmatter_metadata(description: str | None) -> tuple[dict[str, Any], str]:
    """Утилита: извлекает метаданные и чистый description из строки.

    Returns:
        (metadata_dict, clean_description)
    """
    if not description:
        return {}, ""

    desc = description.strip()
    if not desc.startswith("---"):
        return {}, desc

    rest = desc[3:]
    end_idx = rest.find("---")
    if end_idx == -1:
        return {}, desc

    yaml_block = rest[:end_idx].strip()
    remaining = rest[end_idx + 3 :].strip()

    if not yaml_block:
        return {}, remaining

    try:
        import yaml

        metadata = yaml.safe_load(yaml_block)
        if not isinstance(metadata, dict):
            metadata = {}
    except Exception:
        return {}, desc

    filtered = {k: v for k, v in metadata.items() if k in _YAML_METADATA_KEYS}
    return filtered, remaining


def build_skill_description(
    description_text: str, metadata: dict[str, Any] | None = None
) -> str:
    """Собирает полное описание навыка: frontmatter + текст.

    Args:
        description_text: Основной текст описания.
        metadata: Метаданные для frontmatter.

    Returns:
        Полная строка description с YAML frontmatter.
    """
    fm = format_skill_frontmatter(metadata or {})
    if not fm:
        return description_text
    return f"{fm}\n{description_text}"
