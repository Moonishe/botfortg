"""Trigger word lists for the Smart AutoRouter.
Extracted from smart_autorouter.py to keep routing logic clean.

Also contains LearnedRouter — self-improving keyword router that learns
from successful LLM intent classifications.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Final

# =============================================================================
# Context chain words (follow-up detection)
# =============================================================================
CHAIN_WORDS: Final[tuple[str, ...]] = (
    "а ещё",
    "тоже",
    "также",
    "кстати",
    "заодно",
    "ещё",
    "а также",
)

# =============================================================================
# Greetings
# =============================================================================
GREETINGS: Final[tuple[str, ...]] = (
    "привет",
    "здаров",
    "хай",
    "ку",
    "доброе",
    "как дела",
    "чё как",
    "приветствую",
    "добрый",
    "здравствуй",
    "хеллоу",
    "hello",
    "hi",
)

# =============================================================================
# Send / action words
# =============================================================================
SEND_WORDS: Final[tuple[str, ...]] = (
    "отправь",
    "напиши",
    "скажи",
    "передай",
    "ответь",
    "скинь",
    "перешли",
)

# =============================================================================
# Search words
# =============================================================================
SEARCH_WORDS: Final[tuple[str, ...]] = (
    "найди",
    "поищи",
    "посмотри",
    "глянь",
    "чекни",
    "покажи",
    "найдётся",
    "поиск",
)

# =============================================================================
# Analysis words
# =============================================================================
ANALYSIS_WORDS: Final[tuple[str, ...]] = (
    "анализ",
    "сводка",
    "проанализируй",
    "разбери",
    "выжимка",
    "обзор",
    "резюме",
    "кратко",
    "саммари",
    "статистика",
    "сколько",
)

# =============================================================================
# Draft words
# =============================================================================
DRAFT_WORDS: Final[tuple[str, ...]] = (
    "напиши ответ",
    "черновик",
    "draft",
    "набросай ответ",
    "подготовь ответ",
    "сформулируй ответ",
    "ответь ему",
    "ответь ей",
)

# =============================================================================
# Reminder / task words
# =============================================================================
REMINDER_WORDS: Final[tuple[str, ...]] = (
    "напомни",
    "задача",
    "дедлайн",
    "обещание",
    "план",
    "поставь задачу",
    "создай напоминание",
    "запиши",
    "запомни",
    "не забудь",
    "напоминание",
)

# =============================================================================
# Risk classification words
# =============================================================================
RISK_CRITICAL_WORDS: Final[tuple[str, ...]] = (
    "удали",
    "забудь",
    "сбрось",
    "очисти",
    "отмени всё",
)
RISK_HIGH_WORDS: Final[tuple[str, ...]] = (
    "отправь",
    "напиши",
    "скажи",
    "настрой",
    "измени",
    "включи",
    "выключи",
)
RISK_MEDIUM_WORDS: Final[tuple[str, ...]] = (
    "найди",
    "поищи",
    "посмотри",
    "глянь",
    "анализ",
    "сводка",
    "статистика",
    "сколько",
)

# =============================================================================
# Heavy words (trigger MAESTRO mode)
# =============================================================================
HEAVY_WORDS: Final[tuple[str, ...]] = (
    "анализ",
    "сводка",
    "найди все",
    "проанализируй",
    "расскажи подробно",
    "подробно",
    "глубокий анализ",
    "развёрнуто",
    "полный разбор",
    "детально",
)

# =============================================================================
# Contact-action regex pattern (raw string, compiled at import site)
# =============================================================================
CONTACT_ACTION_PATTERN: Final[str] = (
    r"(?:скажи|напиши|отправь|передай|ответь|скинь|перешли)\s+"
    r"([А-ЯЁA-Z][а-яёa-zA-Z\s]{1,35}?)(?:\s+(?:что|про|о|насчёт|по поводу|чтобы|чтоб)\b|$)"
)

# =============================================================================
# Person-info query patterns — вопросы о человеке (мнение, характер, описание)
# =============================================================================
# Regex: извлекает имя контакта из вопроса о личности
PERSON_INFO_PATTERN: Final[str] = (
    r"(?:как тебе|что думаешь о|расскажи про|что за человек|какой человек|"
    r"что знаешь о|опиши|что можешь сказать о|"
    r"что за|как тебе|кто такой|кто такая)\s+"
    r"([А-ЯЁA-Z][а-яёa-zA-Z]{1,20})"
    r"(?:\s+(?:как человек|как личность|по характеру|в общении))?"
)

# Generic name extractor: вытаскивает все слова с заглавной буквы (потенциальные имена)
# Поддерживает как «Влад», так и «ВЛАД» (все капсом)
GENERIC_NAME_PATTERN: Final[str] = r"\b([А-ЯЁA-Z][а-яёА-ЯЁa-zA-Z]{1,20})\b"

# Person-info trigger words — если запрос содержит эти слова И имя контакта,
# выполняем контакт-резолвинг и таргетированный memory recall
PERSON_INFO_WORDS: Final[tuple[str, ...]] = (
    "как тебе",
    "что думаешь",
    "расскажи про",
    "что за человек",
    "какой человек",
    "что знаешь о",
    "опиши",
    "что можешь сказать",
    "кто такой",
    "кто такая",
    "как человек",
    "по характеру",
    "в общении",
    "что из себя представляет",
)

# =============================================================================
# Instant reply patterns (raw regex strings, compiled at import site)
# =============================================================================
INSTANT_GREETING_PATTERN: Final[str] = (
    r"^(привет|здаров|хай|ку|hello|hi|приветствую|здравствуй|хеллоу|доброе утро|добрый вечер|добрый день)\b"
)
INSTANT_HOWAREYOU_PATTERN: Final[str] = r"^(как дела|чё как|как ты|как сам)\b"
INSTANT_BYE_PATTERN: Final[str] = r"^(спокойной ночи|пока|до завтра|ладн[оа])\b"
INSTANT_THANKS_PATTERN: Final[str] = r"^(спасибо|благодарю|спс|thx)\b"
INSTANT_ACK_PATTERN: Final[str] = r"^(ясно|понял|ок|окей|ага|угу|ладно)\b"

# =============================================================================
# Instant replies dict (key → reply string)
# =============================================================================
INSTANT_REPLIES: Final[dict[str, str]] = {
    "привет": "Привет! 👋",
    "здаров": "Здаров! 😎",
    "хай": "Хай! ✌️",
    "ку": "Ку! 👋",
    "hello": "Hello! 👋",
    "hi": "Hi there! 👋",
    "приветствую": "Приветствую! 🤝",
    "здравствуй": "Здравствуй! 👋",
    "доброе утро": "Доброе утро! ☀️",
    "добрый вечер": "Добрый вечер! 🌆",
    "добрый день": "Добрый день! ☀️",
    "как дела": "Всё отлично! Работаю над твоими задачами 💪",
    "чё как": "Да норм! А у тебя? 😄",
    "как ты": "Я в порядке! Работаю в штатном режиме 🤖",
    "спокойной ночи": "Спокойной ночи! Сладких снов 😴🌙",
    "пока": "Пока! До связи 👋",
    "до завтра": "До завтра! 🌙",
    "спасибо": "Всегда пожалуйста! 🤗",
    "спс": "Не за что! 💪",
    "ясно": "👍",
    "понял": "✅",
    "ок": "👌",
    "окей": "👌",
    "ага": "😄",
    "ладно": "👌",
    "благодарю": "Рад помочь! 🤗",
}

# =============================================================================
# LearnedRouter — self-improving keyword router
# =============================================================================

LEARNED_ROUTING_FILE: Final[str] = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "learned_routing.json",
)

_logger = logging.getLogger(__name__)

# Stop words — не учимся на служебных словах
_STOP_WORDS: Final[set[str]] = {
    "и",
    "в",
    "на",
    "с",
    "со",
    "к",
    "ко",
    "у",
    "о",
    "об",
    "от",
    "до",
    "по",
    "за",
    "про",
    "для",
    "без",
    "через",
    "из",
    "изо",
    "над",
    "под",
    "а",
    "но",
    "да",
    "или",
    "же",
    "бы",
    "не",
    "ни",
    "вот",
    "вон",
    "то",
    "это",
    "эти",
    "эта",
    "этот",
    "так",
    "как",
    "что",
    "кто",
    "где",
    "когда",
    "почему",
    "зачем",
    "сколько",
    "чей",
    "какой",
    "меня",
    "мне",
    "тебя",
    "тебе",
    "себя",
    "себе",
    "его",
    "ему",
    "её",
    "ей",
    "их",
    "им",
    "нас",
    "нам",
    "вас",
    "вам",
    "ты",
    "вы",
    "я",
    "мы",
    "он",
    "она",
    "оно",
    "они",
    "всё",
    "все",
    "весь",
    "очень",
    "уже",
    "ещё",
    "тоже",
    "также",
    "тут",
    "там",
    "здесь",
    "сейчас",
    "потом",
    "сегодня",
    "завтра",
    "вчера",
}

# Action-интенты, которым можно учиться
_LEARNABLE_INTENTS: Final[set[str]] = {
    "send_message",
    "draft_reply",
    "search",
    "find_in_chats",
    "summarize_chat",
    "tasks_for_chat",
    "catchup",
    "news_digest",
    "add_reminder",
    "store_memory",
    "add_api_key",
    "remove_api_key",
    "list_keys",
    "check_memories",
    "set_setting",
}


class LearnedRouter:
    """Персистентный роутер, обучающийся на успешных LLM-интентах."""

    def __init__(self, filepath: str = LEARNED_ROUTING_FILE) -> None:
        self._filepath = filepath
        self._words: dict[str, str] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            with open(self._filepath, encoding="utf-8") as f:
                self._words = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._words = {}
        self._loaded = True

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
            with open(self._filepath, "w", encoding="utf-8") as f:
                json.dump(self._words, f, ensure_ascii=False, indent=2)
        except OSError:
            _logger.exception("Failed to save learned routing")

    def match(self, user_text: str) -> str | None:
        """Ищет изученное слово в тексте. Возвращает intent_category или None."""
        self._ensure_loaded()
        words = user_text.lower().split()
        for w in words:
            if w in self._words:
                return self._words[w]
        return None

    def learn(self, user_text: str, intent_kind: str) -> None:
        """Извлекает значащие слова из запроса и связывает с интентом.

        Вызывать ТОЛЬКО после успешного выполнения intent'а.
        """
        if intent_kind not in _LEARNABLE_INTENTS:
            return
        self._ensure_loaded()
        words = user_text.lower().split()
        learned_any = False
        for w in words:
            w_clean = w.strip(".,!?\"'()[]{}:;")
            if not w_clean or len(w_clean) < 3 or w_clean in _STOP_WORDS:
                continue
            # Не перезаписываем, если слово уже изучено (первый wins)
            if w_clean not in self._words:
                self._words[w_clean] = intent_kind
                _logger.debug("Learned: '%s' → %s", w_clean, intent_kind)
                learned_any = True
        if learned_any:
            self._save()

    def reset(self) -> None:
        """Сброс всех изученных слов."""
        self._words = {}
        self._save()


# Глобальный синглтон (lazy load)
_router: LearnedRouter | None = None


def get_learned_router() -> LearnedRouter:
    global _router
    if _router is None:
        _router = LearnedRouter()
    return _router


def learned_match(user_text: str) -> str | None:
    """Удобная обёртка для одноразовой проверки."""
    return get_learned_router().match(user_text)


def learn_routing(user_text: str, intent_kind: str) -> None:
    """Удобная обёртка для одноразового обучения."""
    get_learned_router().learn(user_text, intent_kind)


def reset_learned_routing() -> None:
    """Сброс всех изученных правил."""
    get_learned_router().reset()
