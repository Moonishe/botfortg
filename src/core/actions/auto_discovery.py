"""Авто-импорт всех mcp_*.py модулей из src.core.actions.

Каждый модуль сам регистрирует свои инструменты через ``@tool`` декоратор
при импорте — достаточно чтобы его импортировали.

Usage::

    from src.core.actions.auto_discovery import discover_tools

    count = discover_tools()
    # count == 62  (все mcp_*.py успешно импортированы)
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Исключаемые stem'ы (не используют @tool):
_EXCLUDE_STEMS: frozenset[str] = frozenset({"mcp_expose"})


def discover_tools(package_path: str = "src.core.actions") -> int:
    """Авто-импорт всех mcp_*.py модулей из указанного пакета.

    Каждый модуль регистрирует свои инструменты через @tool декоратор при импорте.
    Исключает __init__.py и mcp_expose.py (не использует @tool).

    Args:
        package_path: dotted package path, по умолчанию ``"src.core.actions"``.

    Returns:
        Количество успешно импортированных модулей.
        Возвращает 0 если пакет не найден или директория не содержит mcp_*.py.
    """
    from importlib.util import find_spec as _find_spec

    pkg_spec = _find_spec(package_path)
    if pkg_spec is None or pkg_spec.origin is None:
        logger.warning("Cannot resolve package %r (no spec or origin)", package_path)
        return 0

    package_dir = Path(pkg_spec.origin).parent
    loaded: int = 0

    for py_file in sorted(package_dir.glob("mcp_*.py")):
        stem = py_file.stem

        if stem in _EXCLUDE_STEMS:
            logger.debug("Skipping excluded module: %s", stem)
            continue

        try:
            importlib.import_module(f"{package_path}.{stem}")
        except Exception:
            logger.exception("Failed to load module: %s", stem)
        else:
            logger.debug("Discovered tool: %s", stem)
            loaded += 1

    return loaded
