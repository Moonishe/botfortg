"""Эвристический pre-filter транскриптов перед LLM-извлечением фактов.

Идея: чат-сообщения часто не содержат запоминаемых фактов (приветствия, шум,
эмодзи). Слать такой транскрипт в LLM — трата токенов. Этот модуль
синхронно оценивает «плотность фактов» и пропускает только перспективные
транскрипты дальше.

Никаких async, БД или LLM — это hot-path, должен работать за микросекунды.
"""

from __future__ import annotations

import re

# ── Маркеры личных местоимений (русские) ──────────────────────────
# Указывают на то, что говорящий рассказывает о себе.
_SELF_REF_RE = re.compile(
    r"\b(я|мне|мой|моя|моё|мои|у меня|меня|мной)\b",
    re.IGNORECASE,
)

# ── Глаголы состояния/предпочтений ─────────────────────────────────
# Высокоинформативные маркеры: «люблю», «работаю», «живу», …
_STATE_VERB_RE = re.compile(
    r"\b(люблю|ненавижу|обожаю|терпеть не могу|не люблю|перестал|"
    r"бросил|работаю|живу|учусь|нахожусь|езжу|играю|слушаю|"
    r"читаю|смотрю|готовлю|встречаюсь|женат|замужем)\b",
    re.IGNORECASE,
)

# ── Маркеры событий (время, даты, дни недели) ─────────────────────
_EVENT_RE = re.compile(
    r"\b(завтра|вчера|сегодня|послезавтра|"
    r"в понедельник|во вторник|в среду|в четверг|"
    r"в пятницу|в субботу|в воскресенье|"
    r"на следующей неделе|на этой неделе|"
    r"в \d{1,2}|через \w+|"
    r"\d{1,2}\s*(января|февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря))\b",
    re.IGNORECASE,
)

# ── Паттерн «Имя Фамилия» (две кириллические заглавные подряд) ────
_NAME_RE = re.compile(r"\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\b")

# ── Числа (возраст, адрес, деньги, телефон) ───────────────────────
_NUMBER_RE = re.compile(r"\d")

# ── Шумовые токены ────────────────────────────────────────────────
# Приветствия, междометия, эмодзи — почти никогда не несут фактов.
_NOISE_WORD_RE = re.compile(
    r"\b(привет|хай|здаров|как дела|как сам|"
    r"ок|окей|ага|угу|лол|кек)\b",
    re.IGNORECASE,
)
_NOISE_EMOJI_RE = re.compile(r"(?:👍|😂|🔥|❤️)")

# ── Пороги ─────────────────────────────────────────────────────────
_SHORT_TRANSCRIPT_CHARS = 20  # короче — почти наверняка шум
_LONG_TRANSCRIPT_CHARS = 50  # длиннее — возможно есть факты
_NOISE_RATIO_PENALTY = 0.3  # доля шумовых токенов для штрафа
_MIN_SCORE_DEFAULT = 0.3  # порог по умолчанию для should_extract


def score_transcript(transcript: str) -> float:
    """Оценка плотности фактов в транскрипте (0.0–1.0).

    Чем выше — тем вероятнее, что транскрипт содержит запоминаемые факты
    и стоит отдавать его в LLM. Аддитивная модель, итог clamp'ится в [0, 1].
    """
    if not transcript:
        return 0.0

    score = 0.0
    words = transcript.split()
    word_count = len(words)

    # Позитивные сигналы
    if _SELF_REF_RE.search(transcript):
        score += 0.3
    if _STATE_VERB_RE.search(transcript):
        score += 0.3
    if _EVENT_RE.search(transcript):
        score += 0.2
    if _NAME_RE.search(transcript):
        score += 0.2
    if len(transcript) > _LONG_TRANSCRIPT_CHARS:
        score += 0.1
    if _NUMBER_RE.search(transcript):
        score += 0.1

    # Негативные сигналы — шум
    if word_count > 0:
        noise_count = len(_NOISE_WORD_RE.findall(transcript)) + len(
            _NOISE_EMOJI_RE.findall(transcript)
        )
        if noise_count / word_count > _NOISE_RATIO_PENALTY:
            score -= 0.4
    if len(transcript) < _SHORT_TRANSCRIPT_CHARS:
        score -= 0.5

    # Clamp в [0.0, 1.0]
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def should_extract(transcript: str, *, min_score: float = _MIN_SCORE_DEFAULT) -> bool:
    """Решает, стоит ли отдавать транскрипт в LLM для извлечения фактов.

    Возвращает True, если score_transcript(transcript) >= min_score.
    Дешёвая эвристика — не вызывает LLM, не ходит в БД, не async.
    """
    return score_transcript(transcript) >= min_score
