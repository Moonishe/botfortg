"""Prompt Assembler — единственная точка сборки system-prompt из трёх tiers.

Tier 1 (STABLE):   неизменяемый якорь — core identity, safety rules.
Tier 2 (CONTEXT):  полу-стабильный контекст — persona, confirmed rules, agents.
Tier 3 (VOLATILE): динамический контекст — memory, history, RAG, candidates.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.actions.tool_registry import tool_registry
from src.core.intelligence.soul_blocks import ANTI_AI_BLOCK, _load_blocks
from src.core.security.prompt_injection_scanner import scan_content
from src.db.repo import get_or_create_user, get_self_profile
from src.db.session import get_session

logger = logging.getLogger(__name__)

# Максимальная длина промпта в символах (безопасный лимит для большинства LLM)
MAX_PROMPT_CHARS = 32_000

# Максимальная длина промпта в токенах (prompt budget — НЕ output limit).
# DEFAULT_MAX_TOKENS (4096) — это output limit; prompt может быть намного больше.
# 24000 = ~32K chars при ratio ~0.75 tokens/char для русского текста.
MAX_PROMPT_TOKENS = 24_000

# ---------------------------------------------------------------------------
# Owner self-reference patterns
# ---------------------------------------------------------------------------

_OWNER_PATTERNS = re.compile(
    r"\b(обо мне|про меня|расскажи обо мне|что ты знаешь обо мне|"
    r"что ты обо мне знаешь|мои данные|мой профиль|что ты помнишь обо мне)\b",
    re.IGNORECASE,
)


def _user_refers_to_self(msg: str) -> bool:
    return bool(_OWNER_PATTERNS.search(msg or ""))


# ---------------------------------------------------------------------------
# SOUL.md — внешний файл личности бота (заменяет hardcoded soul_blocks)
# ---------------------------------------------------------------------------

_SOUL_MD: str | None = None
_soul_md_loaded: bool = False

_soul_md_path = Path(__file__).resolve().parent.parent.parent.parent / "SOUL.md"


def _load_soul_md() -> str | None:
    """Lazy-load SOUL.md — avoids blocking file I/O at import time."""
    global _SOUL_MD, _soul_md_loaded
    if _soul_md_loaded:
        return _SOUL_MD
    _soul_md_loaded = True
    if _soul_md_path.exists():
        try:
            content = _soul_md_path.read_text(encoding="utf-8").strip()
            if content:
                # Scan for prompt injection before accepting
                scan = scan_content(content, _soul_md_path.name)
                if scan.blocked:
                    logger.warning(
                        "SOUL.md blocked by prompt injection scanner: %s",
                        scan.message,
                    )
                    _SOUL_MD = scan.message  # inject blocking message instead
                else:
                    _SOUL_MD = content
                    logger.info("SOUL.md loaded (%d chars)", len(_SOUL_MD))
            else:
                _SOUL_MD = None
        except Exception:
            logger.warning("Failed to read SOUL.md", exc_info=True)
            _SOUL_MD = None
    return _SOUL_MD


def _truncate_smart(text: str, max_chars: int) -> str:
    """Truncate text at the last sentence-ending punctuation within limit."""
    if len(text) <= max_chars:
        return text
    # Find last sentence boundary (., !, ?, newline + letter)
    truncated = text[:max_chars]
    matches = list(re.finditer(r"[.!?]\s", truncated))
    if matches:
        cut = matches[-1].end()
        return truncated[:cut].rstrip()
    # Fallback: last space
    last_space = truncated.rfind(" ")
    if last_space > max_chars // 2:
        return truncated[:last_space].rstrip() + "…"
    return truncated[: max_chars - 1].rstrip() + "…"


# Приоритет при усечении: что выкидывать в первую очередь
TRUNCATION_PRIORITY = [
    "preview_candidates",
    "rag_context",
    "conversation_history",
    "deep_memory",
]


@dataclass
class AssemblyContext:
    """Контекст для сборки промпта — передаётся в PromptAssembler.assemble()."""

    target: str  # "maestro" | "agent" | "summarizer"
    user_id: int
    contact_id: int | None = None
    conversation_history: list = field(default_factory=list)
    memory_context: str = ""
    deep_memory: str = ""
    persona_block: str = ""
    style_match_block: str = ""
    confirmed_rules: list = field(default_factory=list)
    preview_candidates: list = field(default_factory=list)
    rag_context: str = ""
    skill_index: str = ""
    # Anti-AI humanizer
    anti_ai: bool = True
    # Сырой текст сообщения пользователя (для поиска имён контактов)
    user_message: str = ""
    # Дополнительные поля для agent target
    now_local: str = ""
    tz_name: str = ""
    history_block: str = ""
    self_profile: str = ""
    frozen_snapshot: str = ""
    # Per-contact rules block (pre-loaded and passed as string to avoid async in sync method)
    contact_rules_block: str = ""
    # Recent user corrections (pre-loaded in maestro, injected into Tier 2)
    correction_context: str = ""
    # DSM — project memory from previous sessions (pre-loaded in maestro)
    dsm_context: str = ""
    # Session replay summary (from session_recorder.py)
    session_summary: str = ""
    # Voice transcription metadata (set by voice handler)
    transcription_meta: dict | None = None
    # Contact graph — pre-built cross-contact relationship graph (set by maestro)
    contact_graph: str = ""


class PromptAssembler:
    """Собирает system prompt из трёх tiers.

    Используется как синглтон через prompt_assembler.
    """

    def __init__(self):
        self._blocks = _load_blocks()
        # ponytail: memoize stable tier + tier2 static prefix.
        # Invalidated by update_context_block(). Add TTL if blocks become dynamic.
        self._tier1_cache: dict[str, str] = {}
        self._tier2_static_cache: dict[str, str] = {}

    def clear_prompt_cache(self) -> None:
        """Clear prompt caches (for tests / block updates)."""
        self._tier1_cache.clear()
        self._tier2_static_cache.clear()

    # ------------------------------------------------------------------
    # Tier helpers
    # ------------------------------------------------------------------

    def _tier1_stable(self, target: str) -> str:
        """Tier 1 — неизменяемый якорь (cached)."""
        if target not in self._tier1_cache:
            self._tier1_cache[target] = self._compute_tier1(target)
        return self._tier1_cache[target]

    def _compute_tier1(self, target: str) -> str:
        """Compute Tier 1 content (uncached)."""
        if target == "maestro":
            # SOUL.md загружен → используем его вместо hardcoded блоков
            soul_md = _load_soul_md()
            if soul_md is not None:
                return (
                    soul_md
                    + "\n\n## ПРАВИЛА\nСледуй границам, стилю и правилам из SOUL.md."
                )
            return (
                self._blocks["stable_maestro_core"]
                + "\n\n"
                + self._blocks["stable_maestro_convictions"]
                + "\n\n"
                + self._blocks["stable_maestro_safety"]
            )
        elif target == "agent":
            return self._blocks["stable_agent_core"]
        else:
            return ""

    def _tier2_static(self, target: str) -> str:
        """Static prefix of Tier 2 — agent list, intents, format, tool hints."""
        if target not in self._tier2_static_cache:
            self._tier2_static_cache[target] = self._compute_tier2_static(target)
        return self._tier2_static_cache[target]

    def _compute_tier2_static(self, target: str) -> str:
        """Compute static prefix of Tier 2 (uncached)."""
        parts = []

        # Agent list / intents / format для maestro
        if target == "maestro":
            parts.append(self._blocks["context_maestro_agents"])
            parts.append(self._blocks["context_maestro_intents"])
            parts.append(self._blocks["context_maestro_format"])
            # Memory directive
            parts.append("")
            parts.append("[ПАМЯТЬ] Не читай файлы вручную.")
            parts.append("Используй recall_memory, search_contexts, cross_chat_search.")
            parts.append("Frozen snapshot уже в промпте. Для деталей — вызывай tools.")
            parts.append(
                "Для сложных запросов — обрабатывай данные через tools, не загружай в контекст."
            )
            # Tool usage hints — compact catalog reference (full list in SOUL.md)
            by_cat = tool_registry.list_by_category()
            total = sum(len(tools) for tools in by_cat.values())
            cat_parts = []
            for cat_name in sorted(by_cat.keys()):
                cat_parts.append(f"{cat_name}({len(by_cat[cat_name])})")
            cat_str = ", ".join(cat_parts)

            tool_hints = (
                f"\n\n[ИНСТРУМЕНТЫ — {total} доступно]\n"
                f"Категории: {cat_str}.\n"
                "Правила: 1) память first 2) один вызов=один инструмент 3) комбинируй результаты.\n"
                "Полный список — в SOUL.md. "
                "Для деталей о каждом инструменте используй list_for_prompt() в туллупе.\n"
            )
            parts.append(tool_hints)
        elif target == "agent":
            parts.append(self._blocks["context_agent_intents"])
            parts.append(self._blocks["context_agent_format"])

        return "\n".join(parts)

    def _tier2_context(self, target: str, ctx: AssemblyContext) -> str:
        """Tier 2 — полу-стабильный контекст (static prefix cached + dynamic suffix)."""
        parts = [self._tier2_static(target)]

        # Dynamic parts — change per turn
        # Correction learning — feed recent user corrections into prompt
        if target == "maestro" and ctx.correction_context:
            parts.append("")
            parts.append(
                "[УЧТИ ИСПРАВЛЕНИЯ] Пользователь поправлял: " + ctx.correction_context
            )

        # Anti-AI block (controlled by per-user setting)
        if ctx.anti_ai:
            from src.core.humanizer.humanizer import get_few_shot_examples

            anti_ai_block = ANTI_AI_BLOCK
            if ctx.user_id:
                few_shot = get_few_shot_examples(ctx.user_id)
                if few_shot:
                    anti_ai_block += "\n\n" + few_shot
            parts.append(anti_ai_block)

        # Persona block (из adaptive_persona)
        if ctx.persona_block:
            parts.append(ctx.persona_block)

        # Style‑match block (динамический анализ стиля пользователя)
        if ctx.style_match_block:
            parts.append(ctx.style_match_block)

        # Contact-specific context files (data/contexts/{name}.md)
        if ctx.user_message:
            try:
                from src.core.memory.context_files import find_relevant_contexts

                matched = find_relevant_contexts(ctx.user_message)
                for cname, ccontent in matched.items():
                    parts.append(f"[Контекст: {cname}]\n{ccontent}")
            except Exception:
                logger.debug("find_relevant_contexts failed", exc_info=True)

        # Inject owner context on self-reference
        if ctx.user_message:
            try:
                from src.core.memory.context_files import get_context, OWNER_KEY

                owner_context = get_context(OWNER_KEY)
                if owner_context and _user_refers_to_self(ctx.user_message):
                    parts.append(f"[Знания о тебе]\n{owner_context[:1500]}")
            except Exception:
                logger.debug("owner_context injection failed", exc_info=True)

        # Confirmed rules (из adaptive_instructions)
        if ctx.confirmed_rules:
            rules_lines = ["\n\n## АКТИВНЫЕ ПРАВИЛА (владелец установил):"]
            for r in ctx.confirmed_rules:
                rules_lines.append(f"- {r}")
            parts.append("\n".join(rules_lines))

        # Contact-specific rules (pre-loaded, injected only when contact_id is set)
        if ctx.contact_rules_block:
            parts.append(ctx.contact_rules_block)

        # DSM — cross-session project memory (pre-loaded in maestro)
        if ctx.dsm_context:
            parts.append(ctx.dsm_context)

        return "\n".join(parts)

    def _tier3_volatile(self, ctx: AssemblyContext) -> str:
        """Tier 3 — динамический контекст."""
        parts = []

        # Transcription meta
        if ctx.transcription_meta:
            tm = ctx.transcription_meta
            parts.append(
                f"[Это голосовое сообщение. Расшифровано через {tm.get('provider', 'STT')}, "
                f"язык: {tm.get('language', 'ru')}]"
            )

        # Contact graph (pre-built in maestro, injected as volatile context)
        if ctx.contact_graph:
            try:
                from src.core.intelligence.soul_blocks import CONTACT_GRAPH_BLOCK

                parts.append(
                    CONTACT_GRAPH_BLOCK.format(contact_graph=ctx.contact_graph)
                )
            except Exception:
                parts.append(
                    "ГРАФ КОНТАКТОВ (связи между людьми):\n"
                    + ctx.contact_graph
                    + "\n- Используй эти связи для персонализации ответов"
                )

        # Temporal context для agent
        if ctx.target == "agent" and ctx.now_local and ctx.tz_name:
            parts.append(
                f"Текущее локальное время владельца: {ctx.now_local} ({ctx.tz_name}).\n"
                f"Когда нужно превратить относительную дату («завтра», «через час», «в пятницу 18:00») "
                f"в ISO-8601, используй ЛОКАЛЬНОЕ время в TZ владельца (НЕ конвертируй в UTC). "
                f"Формат: YYYY-MM-DDTHH:MM (без Z, без смещения)."
            )

        if ctx.skill_index and ctx.target in {"agent", "maestro", "summarizer"}:
            parts.append(ctx.skill_index)

        # Deep memory
        if ctx.deep_memory:
            parts.append(ctx.deep_memory)

        # Memory context assembled by routing/ContextEngine must be in the
        # system prompt, not only in the user message.
        if ctx.memory_context:
            parts.append(ctx.memory_context)

        # Session replay hint (if available)
        if ctx.session_summary:
            scan_result = scan_content(
                ctx.session_summary, "prompt_assembler:session_summary"
            )
            if scan_result.blocked:
                logger.warning(
                    "prompt_assembler: session_summary blocked by injection scanner"
                )
                session_summary = "[blocked by security scanner]"
            else:
                session_summary = ctx.session_summary
            parts.append(f"<memory-context>\n{session_summary}\n</memory-context>")

        # Conversation history
        if ctx.history_block:
            parts.append(ctx.history_block)
        elif ctx.conversation_history:
            history_text = "\n".join(str(m) for m in ctx.conversation_history[-20:])
            if history_text:
                parts.append(f"История диалога:\n{history_text}")

        # Self profile (для agent)
        if ctx.self_profile:
            parts.append(ctx.self_profile)

        # Frozen memory snapshot (top-3 facts pre-loaded)
        if ctx.frozen_snapshot:
            scan_result = scan_content(
                ctx.frozen_snapshot, "prompt_assembler:frozen_snapshot"
            )
            if scan_result.blocked:
                logger.warning(
                    "prompt_assembler: frozen_snapshot blocked by injection scanner"
                )
                frozen_snapshot = "[blocked by security scanner]"
            else:
                frozen_snapshot = ctx.frozen_snapshot
            parts.append(f"<memory-context>\n{frozen_snapshot}\n</memory-context>")

        # RAG context
        if ctx.rag_context:
            parts.append(
                f"Релевантный контекст из истории переписок:\n{ctx.rag_context}"
            )

        # Preview candidates
        if ctx.preview_candidates:
            cand_lines = ["\n\n## КАНДИДАТЫ В ПАМЯТЬ:"]
            for c in ctx.preview_candidates[:5]:
                cand_lines.append(f"- {c}")
            parts.append("\n".join(cand_lines))

        # Context source visibility — let LLM know which sources are active
        sources_block = self._format_context_sources(ctx)
        if sources_block:
            parts.append(sources_block)

        return "\n\n".join(parts)

    def _format_context_sources(self, ctx: AssemblyContext) -> str:
        """Summarize which context sources are active for LLM visibility.

        Helps the LLM understand what information it already has, reducing
        unnecessary tool calls for data that's already in the prompt.
        """
        sources: list[str] = []
        if ctx.rag_context:
            sources.append("RAG (история переписок)")
        if ctx.persona_block:
            sources.append("Persona (адаптивный профиль)")
        if ctx.style_match_block:
            sources.append("Style match (анализ стиля)")
        if ctx.confirmed_rules:
            sources.append(f"Rules ({len(ctx.confirmed_rules)} активных)")
        if ctx.deep_memory:
            sources.append("Deep memory (tier 2-3 + граф)")
        if ctx.skill_index:
            sources.append("Skills index (доступные навыки)")
        if ctx.frozen_snapshot:
            sources.append("Frozen snapshot (топ-факты сессии)")
        if ctx.memory_context:
            sources.append("Memory context (ContextEngine)")
        if ctx.self_profile:
            sources.append("Self profile (информация о владельце)")
        if ctx.contact_graph:
            sources.append("Contact graph (связи между контактами)")
        if ctx.dsm_context:
            sources.append("DSM (проектная память)")
        if ctx.correction_context:
            sources.append("Corrections (недавние исправления)")
        if ctx.session_summary:
            sources.append("Session summary (replay)")
        if ctx.contact_rules_block:
            sources.append("Contact rules (per-contact)")
        if ctx.transcription_meta:
            sources.append("Voice transcription metadata")

        if not sources:
            return ""

        lines = ["<context_sources>", "Активные источники контекста в этом промпте:"]
        for s in sources:
            lines.append(f"- {s}")
        lines.append(
            "Не вызывай инструменты для данных, уже предоставленных этими источниками."
        )
        lines.append("</context_sources>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assemble(self, ctx: AssemblyContext) -> str:
        """Собирает полный system prompt из трёх tiers.

        Порядок: STABLE → CONTEXT → VOLATILE.
        """
        tier1 = self._tier1_stable(ctx.target)
        tier2 = self._tier2_context(ctx.target, ctx)
        tier3 = self._tier3_volatile(ctx)

        parts = [p for p in [tier1, tier2, tier3] if p]
        prompt = "\n\n".join(parts)

        # Проверка ёмкости + prompt audit
        prompt, _audit = self._capacity_check(prompt)
        logger.debug(
            "Prompt assembly audit: chars_before=%d chars_after=%d tokens=%d stage=%s",
            _audit["chars_before"],
            _audit["chars_after"],
            _audit["tokens"],
            _audit["stage"],
        )

        return prompt

    def _capacity_check(self, prompt: str) -> tuple[str, dict[str, int | str]]:
        """Проверяет длину промпта и усекает при необходимости.

        Returns:
            (final_prompt, audit_dict) where audit_dict has:
            chars_before, chars_after, tokens, stage.
        """
        from src.core.context.token_tracker import (
            DEFAULT_MAX_TOKENS,
            estimate_tokens,
            get_budget_stage,
        )

        chars_before = len(prompt)
        tokens = estimate_tokens(prompt)
        stage, _ = get_budget_stage(tokens, DEFAULT_MAX_TOKENS)

        # Check token budget — enforce MAX_PROMPT_TOKENS (prompt budget, NOT output limit)
        if tokens > MAX_PROMPT_TOKENS:
            logger.warning(
                "prompt_assembler: token budget exceeded: %d > %d, truncating",
                tokens,
                MAX_PROMPT_TOKENS,
            )
            ratio = MAX_PROMPT_TOKENS / tokens
            max_chars = int(chars_before * ratio * 0.9)  # 90% safety margin
            prompt = _truncate_smart(prompt, max_chars)
            tokens = estimate_tokens(prompt)

        if chars_before <= MAX_PROMPT_CHARS:
            return prompt, {
                "chars_before": chars_before,
                "chars_after": len(prompt),
                "tokens": tokens,
                "stage": stage,
            }

        logger.warning(
            "Prompt too long (%d chars), truncating to %d",
            chars_before,
            MAX_PROMPT_CHARS,
        )

        # Smart truncation: режем по границе предложения, не посередине слова;
        # оставляем запас на предупреждение об усечении.
        truncated = (
            _truncate_smart(prompt, MAX_PROMPT_CHARS - 100)
            + "\n\n[Промпт усечён из-за ограничения длины. Часть контекста опущена.]"
        )
        return truncated, {
            "chars_before": chars_before,
            "chars_after": len(truncated),
            "tokens": estimate_tokens(truncated),
            "stage": stage,
        }

    def inject_rule(self, rule_tier: str, rule_text: str) -> bool:
        """Проверяет, можно ли инжектить правило в tier.

        Args:
            rule_tier: "stable" | "context" | "volatile"
            rule_text: текст правила

        Returns:
            True если инжект разрешён, False если REJECT.
        """
        tier = rule_tier.lower().strip()
        if tier == "stable":
            logger.warning("REJECT: попытка инжекта в stable tier: %s", rule_text[:100])
            return False
        elif tier == "context":
            # OK, но требует подтверждения (снапшот)
            logger.info("CONTEXT инжект (требует confirm): %s", rule_text[:100])
            return True
        elif tier == "volatile":
            # OK, auto-apply
            logger.debug("VOLATILE инжект (auto-apply): %s", rule_text[:100])
            return True
        else:
            logger.warning("REJECT: неизвестный tier '%s'", rule_tier)
            return False

    def get_block(self, name: str) -> str:
        """Возвращает конкретный блок по имени (для тестов/инспекции)."""
        return self._blocks.get(name, "")

    def update_context_block(self, name: str, new_text: str) -> bool:
        """Обновляет tier-2 блок (только CONTEXT блоки).

        Returns:
            True если обновлён, False если блок не найден или это STABLE блок.
        """
        if name not in self._blocks:
            logger.warning("update_context_block: блок '%s' не найден", name)
            return False
        if name.startswith("stable_"):
            logger.warning(
                "update_context_block: REJECT — блок '%s' является STABLE", name
            )
            return False
        self._blocks[name] = new_text
        # Invalidate prompt caches — block change means cached tiers are stale
        self.clear_prompt_cache()
        logger.info("update_context_block: блок '%s' обновлён (caches cleared)", name)
        return True

    def get_context_blocks(self) -> dict[str, str]:
        """Возвращает все tier-2 блоки (для снапшотов)."""
        return {
            name: text
            for name, text in self._blocks.items()
            if name.startswith("context_")
        }


# Глобальный синглтон (ленивый — не создаёт БД-соединений при импорте)
prompt_assembler = PromptAssembler()


async def assemble_self_profile_prompt(owner_id: int, session=None) -> str:
    """Собирает блок self-profile из БД.

    Args:
        owner_id: ID владельца.
        session: опциональная асинхронная сессия (если None — создаёт новую).

    Returns:
        отформатированный блок профиля или "" если профиля нет / ошибка.
    """
    if session is not None:
        owner = await get_or_create_user(session, owner_id)
        profile = await get_self_profile(session, owner)
    else:
        async with get_session() as _session:
            owner = await get_or_create_user(_session, owner_id)
            profile = await get_self_profile(_session, owner)

    if not profile:
        return ""

    lines = ["ТВОЙ ПРОФИЛЬ (владелец):"]
    if profile.preferences:
        lines.append(f"Предпочтения: {profile.preferences}")
    if profile.goals:
        lines.append(f"Цели: {profile.goals}")
    if profile.current_projects:
        lines.append(f"Проекты: {profile.current_projects}")
    if profile.decision_style:
        lines.append(f"Стиль решений: {profile.decision_style}")
    if profile.communication_preferences:
        lines.append(f"Коммуникация: {profile.communication_preferences}")
    if profile.sleep_pattern:
        lines.append(f"Сон: {profile.sleep_pattern}")
    if profile.work_hours:
        lines.append(f"Рабочие часы: {profile.work_hours}")
    return "\n".join(lines)
