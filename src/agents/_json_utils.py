"""Shared JSON extraction helper for LLM agent responses."""

import json
import re
from typing import Any


def extract_json_from_llm_response(raw: str, *, default: Any = None) -> Any:
    """Извлекает JSON-объект/массив из сырого LLM-ответа (может быть обёрнут в ```).

    String-aware parser: отслеживает состояние «внутри строки», игнорирует
    скобки внутри строковых литералов, обрабатывает escape-последовательности.
    Поддерживает объекты {{...}} и массивы [...].

    Если результат — список, оборачивается в {{"items": [...]}}.

    Args:
        raw: Сырой текст ответа LLM.
        default: Значение, возвращаемое если JSON не найден/не парсится.

    Returns:
        Распарсенный dict или *default*.
    """
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json|JSON)?\s*\n?", "", s)
        s = re.sub(r"\n?\s*```\s*$", "", s)

    # Find first '{' or '[' — pick the earlier one
    brace_idx = s.find("{")
    bracket_idx = s.find("[")
    if brace_idx == -1 and bracket_idx == -1:
        return default

    if brace_idx == -1:
        start = bracket_idx
        open_char, close_char = "[", "]"
    elif bracket_idx == -1:
        start = brace_idx
        open_char, close_char = "{", "}"
    else:
        if brace_idx < bracket_idx:
            start = brace_idx
            open_char, close_char = "{", "}"
        else:
            start = bracket_idx
            open_char, close_char = "[", "]"

    depth = 0
    in_string = False
    escape_next = False

    for i in range(start, len(s)):
        ch = s[i]

        if escape_next:
            escape_next = False
            continue

        if ch == "\\" and in_string:
            escape_next = True
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                candidate = s[start : i + 1]
                try:
                    result = json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    return default
                if isinstance(result, dict):
                    return result
                if isinstance(result, list):
                    return {"items": result}  # ponytail: wrap array, caller expects dict
                return default

    return default
