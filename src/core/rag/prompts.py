"""LLM prompts for deep research pipeline."""

# ── Level 0: Query Clarification ──
CLARIFY_PROMPT = """\
Analyze this research query. If it is ambiguous, too broad, or needs clarification,
generate 1-3 specific follow-up questions. If the query is clear and researchable,
reply with exactly: READY

Query: {query}

Respond in Russian."""

# ── Level 2: Fact Extraction ──
EXTRACT_CLAIMS_PROMPT = """\
Read the following web sources about: {query}

Extract ALL significant factual claims, statistics, expert opinions, and quotes.
For each claim, categorize as: fact, statistic, opinion, or quote.
Rate confidence [0.0-1.0] based on source quality and specificity.
Identify knowledge gaps — what important aspects are NOT covered?

Sources:
{sources_text}

Respond as JSON:
{{
  "claims": [
    {{"text": "...", "source_url": "...", "category": "fact", "confidence": 0.9}},
    ...
  ],
  "gaps": [
    {{"question": "...", "reason": "...", "priority": 1}},
    ...
  ]
}}"""

# ── Level 3: Cross-Referencing ──
CROSS_REF_PROMPT = """\
Compare these claims extracted from multiple web sources. Find contradictions.
For each contradictory claim, identify which source supports which side.
Update confidence scores based on cross-validation.

Claims:
{claims_text}

Respond as JSON:
{{
  "claims": [
    {{"text": "...", "confidence": 0.9, "verified_by": ["url1"], "contradicted_by": []}},
    ...
  ]
}}"""

# ── Level 4: Synthesis ──
SYNTHESIS_PROMPT = """\
Write a comprehensive research report in Russian Markdown based on these claims and sources.

Structure:
# Исследование: {query}

## 📋 Executive Summary
(2-3 предложения — ключевой вывод)

## 🔑 Ключевые находки
(Bullet points with [source] citations like [1], [2])

## 📊 Детальный анализ
(Organize by theme. Include specific data, quotes, statistics)

## ⚠️ Противоречия и неопределённости
(Flag where sources disagree, what remains unclear)

## 📚 Источники
(Numbered list with URL and brief annotation)

Claims:
{claims_text}

Sources:
{sources_text}"""

# ── Level 5: Timeline Extraction ──
EXTRACT_TIMELINE_PROMPT = """\
Извлеки ВСЕ датируемые события из этих исследовательских утверждений.
Для каждого события укажи: дату (ISO YYYY-MM-DD), описание (одна строка), индекс источника.

Утверждения:
{claims_text}

Ответь JSON:
{{
  "timeline": [
    {{"date": "2024-03-15", "description": "Описание события", "source_index": 0}}
  ]
}}
Если датируемых событий нет, верни пустую временную шкалу."""
