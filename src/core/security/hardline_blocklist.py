"""Hardline blocklist — команды, блокируемые ВСЕГДА, независимо от
``approval_mode`` или ``_confirmed=True``.

Слой defense-in-depth, запускаемый ДО approval-gate'а. Покрывает необратимые
операции: разрушение данных, эскалацию привилегий, pipe-to-interpreter /
reverse-shell, остановку системы, массовое шифрование (ransomware).

Принципы дизайна:
- **Fail-safe**: при любой внутренней ошибке → БЛОКИРОВКА (никогда не пропускать).
- **Нормализация**: Unicode NFKC + удаление control-chars + casefold + замена
  омоглифов (переиспользуется ``_CYR_TO_LAT`` из prompt_injection_scanner).
- **Кэш hot-path**: ``@lru_cache(maxsize=1024)`` — LLM часто повторяет команды
  в цикле рассуждений; повторная проверка ~O(1).
- **Single source of truth**: импортируется и ``tool_registry.execute()``, и
  ``mcp_server._call_tool()`` — две независимые точки эшелонированной защиты.

Public API: :func:`check_command`, :func:`check_params`, :func:`is_confirmed_truthy`.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# ── Омоглиф-карта (переиспользуется из prompt_injection_scanner) ─────────────
# Cyrillic look-alikes → Latin. Небольшая таблица, точечная замена — дешевле
# полной NFKC и покрывает реальные векторы атак (ｒｍ вместо rm, ｓｕｄｏ и т.п.).
_HOMOGLYPH_MAP: dict[str, str] = {
    # Lowercase Cyrillic → Latin
    "\u0430": "a",  # а
    "\u0435": "e",  # е
    "\u043e": "o",  # о
    "\u0440": "p",  # р
    "\u0441": "c",  # с
    "\u0443": "y",  # у
    "\u0445": "x",  # х
    "\u0456": "i",  # і
    "\u0455": "s",  # ѕ
    "\u0458": "j",  # ј
    "\u04bb": "h",  # һ
    "\u049b": "k",  # қ
    # Uppercase Cyrillic → Latin (uppercase для полноты)
    "\u0410": "a",  # А
    "\u0412": "b",  # В
    "\u0415": "e",  # Е
    "\u041a": "k",  # К
    "\u041c": "m",  # М
    "\u041d": "h",  # Н
    "\u041e": "o",  # О
    "\u0420": "p",  # Р
    "\u0421": "c",  # С
    "\u0422": "t",  # Т
    "\u0423": "y",  # У
    "\u0425": "x",  # Х
    # Fullwidth Latin (U+FF01–U+FF5E) — NFKC сворачивает, но дублируем для надёжности
    "\uff52": "r",  # ｒ
    "\uff4d": "m",  # ｍ
    "\uff53": "s",  # ｓ
    "\uff55": "u",  # ｕ
    "\uff44": "d",  # ｄ
    "\uff4f": "o",  # ｏ
}


# ── Контрольные символы для удаления ─────────────────────────────────────────
# NULL bytes, C0/C1 control chars (кроме \t), BiDi override (RLO/LRO и т.п.)
# — могут скрывать команды от визуального ревью и от наивных regex.
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"
    r"\u202a-\u202e\u2066-\u2069\ufeff]"
)


# ── Точные совпадения (O(1) set lookup) ──────────────────────────────────────
# Короткие команды, блокируемые целиком по имени — самый быстрый путь.
_EXACT_BANNED: frozenset[str] = frozenset(
    {
        "sudo",
        "su",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "mkfs",
    }
)


# ── Категории regex-паттернов ────────────────────────────────────────────────
# Структура: категория → tuple[(rule_id, compiled_pattern), ...].
# Паттерны применяются к НОРМАЛИЗОВАННОЙ (lowercase, NFKC, без control-chars)
# строке — поэтому без re.IGNORECASE. Используем [^|;&]* вместо .* чтобы
# ограничить backtracking и не перескакивать через разделители команд.
_CATEGORY_PATTERNS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "data_destruction": (
        (
            "rm_rf_root",
            re.compile(
                r"\brm\b[^|;&]*-[a-z]*r[a-z]*f?[^|;&]*"
                r"(?:/(?:\s|$|\*)|~(?:\s|$|/)|\$home\b)"
            ),
        ),
        ("mkfs", re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b")),
        (
            "dd_of_disk",
            re.compile(r"\bdd\b[^|;&]*\bof\s*=\s*/dev/(?:sd|nvme|hd|disk)"),
        ),
        ("shred", re.compile(r"\bshred\b")),
        (
            "overwrite_disk",
            re.compile(
                r":\s*>\s*/dev/(?:sd|nvme|hd)|>\s*/dev/(?:sd|nvme|hd|disk)"
            ),
        ),
        ("win_format", re.compile(r"\bformat\b\s+[a-z]:\s*/")),
    ),
    "privilege_escalation": (
        ("sudo", re.compile(r"\bsudo\b|\bsudoedit\b")),
        ("su_root", re.compile(r"\bsu\s+(?:root|-)\b|\bsu\s*$")),
        (
            "chmod_777_root",
            re.compile(
                r"\bchmod\b[^|;&]*\b777\b[^|;&]*/(?:\s|$)"
                r"|\bchmod\b[^|;&]*[ug]\s*\+\s*s\b"
            ),
        ),
        ("chown_root", re.compile(r"\bchown\b[^|;&]*\broot\b")),
        ("setcap_admin", re.compile(r"\bsetcap\b[^|;&]*cap_sys_admin")),
        (
            "fork_bomb",
            re.compile(r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:[^}]*&\s*\}"),
        ),
    ),
    "pipe_to_interpreter": (
        (
            "curl_pipe_interp",
            re.compile(
                r"\b(?:curl|wget|fetch)\b[^|;&]*\|\s*(?:sudo\s+)?"
                r"(?:bash|sh|zsh|python\d?|perl|ruby|node)\b"
            ),
        ),
        ("pipe_to_sh", re.compile(r"\|\s*(?:/bin/)?(?:bash|sh|zsh)\b")),
        ("dev_tcp", re.compile(r"/dev/tcp/|/dev/udp/")),
        (
            "reverse_shell_python",
            re.compile(r"\bpython\d?\b[^|;&]*-c\b[^|;&]*socket"),
        ),
    ),
    "system_halt": (
        ("halt_cmd", re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b")),
        ("init_runlevel", re.compile(r"\binit\s+[06]\b|\btelinit\s+[06]\b")),
        (
            "kill_init",
            re.compile(
                r"\bkill\b[^|;&]*-9\b[^|;&]*\b(?:1|-1)\b\s*$"
                r"|\bkill\b[^|;&]*-\s*kil\s*1\b"
            ),
        ),
        (
            "systemctl_halt",
            re.compile(
                r"\bsystemctl\s+(?:halt|poweroff|reboot|rescue|emergency)\b"
            ),
        ),
    ),
    "ransomware": (
        (
            "find_exec_encrypt",
            re.compile(
                r"\bfind\b[^|;&]*-exec\b[^|;&]*"
                r"(?:encrypt|openssl\s+enc|gpg\s+-c|cryptsetup)"
            ),
        ),
        (
            "mass_chmod_000",
            re.compile(r"\bfind\b[^|;&]*-exec\b[^|;&]*chmod\s+0?00\b"),
        ),
    ),
}


# ── Параметры, потенциально содержащие команды ───────────────────────────────
# Scoped whitelist имён полей, чтобы избежать false-positive на произвольных
# строковых аргументах (draft_reply, ask_chat, и т.п.).
_COMMAND_PARAM_KEYS: frozenset[str] = frozenset(
    {
        "command",
        "cmd",
        "script",
        "code",
        "shell",
        "expr",
        "cmdline",
    }
)


class BlocklistVerdict(NamedTuple):
    """Результат проверки hardline-блоклиста."""

    blocked: bool
    category: str = ""
    rule_id: str = ""
    matched_fragment: str = ""


# ── Нормализация ─────────────────────────────────────────────────────────────


def _normalize_command(raw: str) -> str:
    """Нормализация команды перед regex-матчингом.

    Шаги (безопасны — при ошибке возвращается исходная строка):
    1. Удаление control-chars и BiDi-override (могут скрывать команды).
    2. Unicode NFKC (сворачивает fullwidth и compatibility-forms).
    3. Точечная замена омоглифов через ``_HOMOGLYPH_MAP``.
    4. ``casefold()`` (SUDO → sudo).
    5. Схлопывание пробелов (``rm   -rf`` → ``rm -rf``).
    """
    try:
        s = _CONTROL_CHARS_RE.sub("", raw)
        s = unicodedata.normalize("NFKC", s)
        for cyr, lat in _HOMOGLYPH_MAP.items():
            s = s.replace(cyr, lat)
        s = s.casefold()
        # Схлопываем любые пробельные последовательности в один пробел.
        s = re.sub(r"\s+", " ", s).strip()
        return s
    except Exception:
        # Нормализация не должна блокировать; возвращаем хотя бы strip'нутое.
        logger.exception("hardline normalize failed (fallback to raw.strip())")
        return raw.strip()


# Разделители команд для split — И приоритетны (нужны до pipe, чтобы ловить
# ``cmd1 ; rm -rf /``), затем ||/&&, затем |, затем newlines.
_CHAIN_SPLIT_RE = re.compile(r"\s*(?:&&|\|\||;|\||\n|\r)\s*")


def _split_command_chain(normalized: str) -> list[str]:
    """Разбить цепочку команд по ``&&``, ``||``, ``;``, ``|``, ``\\n``, ``\\r``.

    Сначала пробуем ``shlex.split`` (корректно обрабатывает кавычки); при
    ``ValueError`` (незакрытые кавычки — типичный приём обхода) fallback на
    regex-разделение. Возвращает подсегменты (без разделителей).
    """
    if not normalized:
        return []
    # shlex возвращает токены; нас интересует разбиение по разделителям команд,
    # а не по аргументам — поэтому сначала regex-сплит по операторам.
    parts = [p.strip() for p in _CHAIN_SPLIT_RE.split(normalized) if p.strip()]
    # Дополнительно: shlex может выявить скрытые команды внутри кавычек.
    # Но т.к. shell ВЫПОЛНЯЕТ только то, что вне кавычек на уровне операторов,
    # regex-сплит достаточно корректен для целей блок-листа.
    return parts


# ── Основная проверка ────────────────────────────────────────────────────────


@lru_cache(maxsize=1024)
def _check_normalized_cached(normalized: str) -> BlocklistVerdict | None:
    """Проверка нормализованной строки. Кэшируется по точному значению.

    Cache-hit делает повторную проверку той же команды в цикле рассуждений LLM
    практически бесплатной (~80ns).
    """
    if not normalized:
        return None

    # FAST PATH 1: точное совпадение с коротким именем команды.
    first_token = normalized.split(None, 1)[0]
    if first_token in _EXACT_BANNED:
        return BlocklistVerdict(
            blocked=True,
            category=(
                "privilege_escalation"
                if first_token in {"sudo", "su"}
                else "system_halt"
                if first_token in {"shutdown", "reboot", "halt", "poweroff"}
                else "data_destruction"  # mkfs
            ),
            rule_id=f"exact:{first_token}",
            matched_fragment=first_token,
        )

    # MAIN PATH: по каждой категории, по каждому паттерну.
    for category, rules in _CATEGORY_PATTERNS.items():
        for rule_id, pattern in rules:
            match = pattern.search(normalized)
            if match:
                return BlocklistVerdict(
                    blocked=True,
                    category=category,
                    rule_id=rule_id,
                    matched_fragment=match.group(0),
                )

    return None


def _block(
    category: str, rule_id: str, matched_fragment: str = ""
) -> dict[str, str]:
    """Сформировать error-dict в каноническом формате блокировки."""
    return {
        "error": (
            f"Blocked by hardline blocklist (category={category}, "
            f"rule={rule_id}). This operation is irreversible and is "
            f"blocked unconditionally regardless of approval_mode or "
            f"_confirmed."
        ),
        "category": category,
        "rule_id": rule_id,
        "matched_fragment": matched_fragment,
    }


def check_command(
    command: str | None, *, tool_name: str = ""
) -> dict[str, str] | None:
    """Главная точка входа. Возвращает ``dict`` если команда заблокирована,
    ``None`` если безопасна.

    **Fail-safe**: при любом исключении внутри → возвращает блокирующий dict
    (никогда не возвращает ``None`` в случае ошибки).

    Проверяет ВСЕ подсегменты цепочки (``cmd1 ; cmd2 | cmd3``).
    """
    if not command or not isinstance(command, str) or not command.strip():
        return None

    try:
        normalized = _normalize_command(command)

        # Проверяем всю строку целиком (ловит pipe-to-interpreter и т.п., где
        # паттерн spanning-оператор).
        verdict = _check_normalized_cached(normalized)
        if verdict is not None and verdict.blocked:
            return _block(
                verdict.category, verdict.rule_id, verdict.matched_fragment
            )

        # Затем — каждый подсегмент (ловит ``ls ; rm -rf /``).
        for segment in _split_command_chain(normalized):
            seg_verdict = _check_normalized_cached(segment)
            if seg_verdict is not None and seg_verdict.blocked:
                return _block(
                    seg_verdict.category,
                    seg_verdict.rule_id,
                    seg_verdict.matched_fragment,
                )

        return None
    except Exception:
        logger.exception(
            "hardline check_command failed (fail-safe: BLOCK). tool=%s",
            tool_name,
        )
        return _block("internal_error", "safe_fail", "safety check failed")


def check_params(
    tool_name: str, params: dict[str, Any]
) -> dict[str, str] | None:
    """Извлечь командные поля из ``params`` и проверить их hardline-блоклистом.

    Сканирует только поля с известными именами (``command``, ``cmd``,
    ``script``, ``code``, ``shell``, ``expr``, ``cmdline``) — это scoped
    whitelist, чтобы избежать false-positive на произвольных строковых
    аргументах (например ``draft_reply`` со словом "shutdown" в тексте).
    """
    try:
        for key in _COMMAND_PARAM_KEYS:
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                block = check_command(value, tool_name=tool_name)
                if block is not None:
                    return block
        return None
    except Exception:
        logger.exception(
            "hardline check_params failed (fail-safe: BLOCK). tool=%s",
            tool_name,
        )
        return _block("internal_error", "safe_fail", "safety check failed")


# ── Каноническая проверка _confirmed ─────────────────────────────────────────


def is_confirmed_truthy(value: Any) -> bool:
    """Единая каноническая проверка флага подтверждения.

    Принимает ТОЛЬКО настоящий boolean ``True``. Строки (``"true"``, ``"1"``,
    ``"yes"``), числа (``1``) и любые другие truthy-значения возвращают
    ``False`` — это устраняет класс обходов через тип-путаницу.

    История бага: ``tool_registry.py`` ранее делал ``params.pop("_confirmed",
    False)`` без проверки типа, поэтому ``"_confirmed": "true"`` (строка)
    обходило gate (``not "true"`` → ``False``). См. judge-фазу Max Mode.
    """
    return value is True
