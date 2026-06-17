"""Shared JSON extraction helper for LLM agent responses."""

import json
import re
from typing import Any


def extract_json_from_llm_response(raw: str, *, default: Any = None) -> Any:
    """Извлекает JSON-объект из сырого LLM-ответа (может быть обёрнут в ```).

    Парсит сбалансированные скобки: находит первый '{', считает баланс,
    извлекает подстроку до закрывающей '}' с учётом вложенности.

    Args:
        raw: Сырой текст ответа LLM.
        default: Значение, возвращаемое если JSON не найден/не парсится.

    Returns:
        Распарсенный JSON-объект или *default*.
    """
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json|JSON)?\s*\n?", "", s)
        s = re.sub(r"\n?\s*```\s*$", "", s)

    # Balanced bracket parser: finds the first '{' then tracks nesting depth.
    start = s.find("{")
    if start == -1:
        return default
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = s[start : i + 1]
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    return default
    return default
