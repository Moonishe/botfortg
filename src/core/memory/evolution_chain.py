"""Evolution Chain — обнаружение эволюции фактов пользователя через supersedes-цепочки.

В отличие от follow_supersedes_chain (простого обхода), этот модуль:
- Строит цепочки эволюции через BFS по MemoryLink (relation_type="supersedes")
- Определяет тренды: усиление/ослабление/сдвиг
- Определяет тренды сентимента: positive/negative/neutral/mixed
- Возвращает структурированный результат с аналитикой

Использование:
    from src.core.memory.evolution_chain import get_evolution_chain, AllEvolutionChains

    chains = await get_evolution_chain(owner_id)
    for chain in chains.chains:
        print(f"Chain len={chain.length}, trend={chain.trend}")
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select

from src.core.memory.relation_types import RelationType
from src.db.models import Memory, MemoryLink
from src.db.repo import get_or_create_user
from src.db.session import get_session

logger = logging.getLogger(__name__)

# Максимальная глубина BFS для цепочки эволюции
MAX_CHAIN_DEPTH = 20
# Максимальное число цепочек
MAX_CHAINS = 10


@dataclass
class EvolutionChainItem:
    """Один узел в цепочке эволюции."""

    memory_id: int
    fact: str
    sentiment: str | None = None
    created_at: str = ""
    depth: int = 0
    is_head: bool = False
    is_tail: bool = False


@dataclass
class EvolutionChainResult:
    """Результат анализа одной цепочки эволюции."""

    chain: list[EvolutionChainItem] = field(default_factory=list)
    length: int = 0
    trend: str = "neutral"  # "strengthening", "weakening", "neutral", "shifting"
    sentiment_trend: str = "neutral"  # "positive", "negative", "neutral", "mixed"
    is_evolving: bool = False


@dataclass
class AllEvolutionChains:
    """Все цепочки эволюции для пользователя."""

    user_id: int
    chains: list[EvolutionChainResult] = field(default_factory=list)
    total_chains: int = 0
    active_chains: int = 0
    generated_at: str = ""

    def summary(self) -> str:
        """Краткая сводка цепочек эволюции."""
        lines = [
            f"=== Evolution Chains (user #{self.user_id}) ===",
            f"Total chains: {self.total_chains}",
            f"Active (evolving): {self.active_chains}",
            f"Generated: {self.generated_at}",
            "",
        ]
        for i, chain in enumerate(self.chains, 1):
            lines.append(
                f"Chain {i}: len={chain.length}, "
                f"trend={chain.trend}, "
                f"sentiment={chain.sentiment_trend}, "
                f"evolving={chain.is_evolving}"
            )
            for item in chain.chain[:3]:
                head = " [HEAD]" if item.is_head else ""
                tail = " [TAIL]" if item.is_tail else ""
                lines.append(f"  d={item.depth} {item.fact}{head}{tail}")
            if chain.length > 3:
                lines.append(f"  ... ещё {chain.length - 3}")
            lines.append("")
        return "\n".join(lines)


def _detect_trend(chain_items: list[EvolutionChainItem]) -> str:
    """Определяет тренд цепочки: усиление, ослабление, сдвиг или нейтральный.

    Анализирует изменение длины фактов и наличие новых элементов.
    """
    if len(chain_items) < 2:
        return "neutral"

    # Сравниваем первый (старый) и последний (новый) элемент
    first = chain_items[0]
    last = chain_items[-1]

    first_len = len(first.fact) if first.fact else 0
    last_len = len(last.fact) if last.fact else 0

    # Если длина факта выросла >30% — усиление (больше деталей)
    if first_len > 0 and last_len > first_len * 1.3:
        return "strengthening"
    # Если длина факта уменьшилась >30% — ослабление
    if first_len > 0 and last_len < first_len * 0.7:
        return "weakening"
    # Если факты заметно разные (разные ключевые слова) — сдвиг
    first_words = set((first.fact or "").lower().split())
    last_words = set((last.fact or "").lower().split())
    if first_words and last_words:
        overlap = len(first_words & last_words) / max(len(first_words | last_words), 1)
        if overlap < 0.3:
            return "shifting"

    return "neutral"


def _detect_sentiment_trend(chain_items: list[EvolutionChainItem]) -> str:
    """Определяет тренд сентимента в цепочке."""
    sentiments = [
        (item.sentiment or "neutral") for item in chain_items if item.sentiment
    ]
    if not sentiments:
        return "neutral"

    # Считаем распределение
    pos = sum(1 for s in sentiments if s == "positive")
    neg = sum(1 for s in sentiments if s == "negative")
    neu = sum(1 for s in sentiments if s == "neutral")

    total = len(sentiments)
    if pos > total * 0.6:
        return "positive"
    if neg > total * 0.6:
        return "negative"
    if pos > 0 and neg > 0:
        return "mixed"
    return "neutral"


async def get_evolution_chain(
    owner_id: int,
    *,
    focus_memory_id: int | None = None,
    max_chains: int = MAX_CHAINS,
) -> AllEvolutionChains:
    """Строит все цепочки эволюции фактов пользователя через BFS по MemoryLink.

    Алгоритм:
    1. Находит все supersedes-связи пользователя.
    2. Вычисляет начальные узлы (tails — те, которые не являются source ни в одной supersedes-связи).
    3. Для каждого tail строит BFS-обход по supersedes-связям.
    4. Для каждой цепочки определяет тренд и сентимент-тренд.

    Args:
        owner_id: ID пользователя.
        focus_memory_id: Опционально — начать с конкретного факта.
        max_chains: Максимальное число возвращаемых цепочек.

    Returns:
        AllEvolutionChains с найденными цепочками и аналитикой.
    """
    result = AllEvolutionChains(user_id=owner_id)
    result.generated_at = datetime.now(timezone.utc).isoformat()

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_id)

        # 1. Находим все supersedes-связи
        links_query = select(MemoryLink).where(
            MemoryLink.user_id == owner.id,
            MemoryLink.relation_type == RelationType.SUPERSEDES,
        )
        links_result = await session.execute(links_query)
        all_supersedes: list[MemoryLink] = list(links_result.scalars().all())

        if not all_supersedes:
            logger.debug("No supersedes links for user %d", owner_id)
            return result

        # 2. Строим карту связей: source_id → set[target_id]
        #    source — новый факт (supersedes target — старый факт)
        source_ids: set[int] = set()
        supersedes_map: dict[int, set[int]] = {}
        for link in all_supersedes:
            source_ids.add(link.source_id)
            if link.source_id not in supersedes_map:
                supersedes_map[link.source_id] = set()
            supersedes_map[link.source_id].add(link.target_id)

        # Находим tails — memory_id, которые не являются source ни в одной supersedes-связи
        # (т.е. самые старые узлы, от которых эволюция началась)
        all_target_ids: set[int] = {link.target_id for link in all_supersedes}
        tail_ids = all_target_ids - source_ids

        # Если указан focus_memory_id, используем его как стартовый
        if focus_memory_id is not None:
            tail_ids = {focus_memory_id}

        if not tail_ids:
            logger.debug("No tail nodes found for user %d", owner_id)
            return result

        result.total_chains = len(tail_ids)

        # 3. Для каждого tail строим BFS-обход
        all_chains: list[EvolutionChainResult] = []

        for tail_id in list(tail_ids)[:max_chains]:
            try:
                chain_result = await _build_single_chain(
                    session, owner, tail_id, supersedes_map
                )
                if chain_result and chain_result.length > 1:
                    all_chains.append(chain_result)
            except Exception:
                logger.debug(
                    "Failed to build evolution chain for tail %d",
                    tail_id,
                    exc_info=True,
                )

        # Сортируем: активные цепочки первыми, потом по длине
        all_chains.sort(key=lambda c: (not c.is_evolving, -c.length))
        result.chains = all_chains
        result.active_chains = sum(1 for c in all_chains if c.is_evolving)

    return result


async def _build_single_chain(
    session,
    owner,
    start_id: int,
    supersedes_map: dict[int, set[int]],
) -> EvolutionChainResult | None:
    """Строит одну цепочку эволюции от start_id через BFS по supersedes_map.

    Идёт от tail (старый) к head (новый): tail → ... → head.
    supersedes_map[source] = {targets} — source новее, target старее.
    Значит от tail (target) идём к тем, у кого tail является target'ом.
    """
    # Строим обратную карту: target_id → set[source_id] (кто новее)
    reverse_map: dict[int, set[int]] = {}
    for src, targets in supersedes_map.items():
        for tgt in targets:
            if tgt not in reverse_map:
                reverse_map[tgt] = set()
            reverse_map[tgt].add(src)

    visited: set[int] = set()
    queue: deque[tuple[int, int]] = deque()
    # (memory_id, depth)
    queue.append((start_id, 0))
    visited.add(start_id)

    nodes: list[tuple[int, int]] = []  # (memory_id, depth)

    while queue and len(visited) < MAX_CHAIN_DEPTH:
        current_id, depth = queue.popleft()
        nodes.append((current_id, depth))

        # Ищем, кто supersedes текущий узел (кто новее)
        newer = reverse_map.get(current_id, set())
        for next_id in newer:
            if next_id not in visited:
                visited.add(next_id)
                queue.append((next_id, depth + 1))

    if len(nodes) < 2:
        return None

    # Загружаем все узлы
    mem_ids = [mid for mid, _ in nodes]
    mem_result = await session.execute(select(Memory).where(Memory.id.in_(mem_ids)))
    mem_map: dict[int, Memory] = {m.id: m for m in mem_result.scalars().all()}

    # Сортируем по depth (от tail к head) и по created_at
    nodes.sort(
        key=lambda x: (
            x[1],
            str(mem_map.get(x[0]).created_at if mem_map.get(x[0]) else ""),
        )
    )

    chain_items: list[EvolutionChainItem] = []
    for i, (mid, depth) in enumerate(nodes):
        m = mem_map.get(mid)
        if not m:
            continue
        item = EvolutionChainItem(
            memory_id=m.id,
            fact=m.fact or "",
            sentiment=m.sentiment,
            created_at=m.created_at.isoformat() if m.created_at else "",
            depth=depth,
            is_head=(i == len(nodes) - 1),
            is_tail=(i == 0),
        )
        chain_items.append(item)

    if len(chain_items) < 2:
        return None

    trend = _detect_trend(chain_items)
    sentiment_trend = _detect_sentiment_trend(chain_items)
    is_evolving = chain_items[-1].is_head and trend != "neutral"

    # Emit event if chain detected
    try:
        from src.core.events.event_bus import event_bus, EVOLUTION_CHAIN_DETECTED

        await event_bus.emit(
            EVOLUTION_CHAIN_DETECTED,
            user_id=owner.id,
            chain_length=len(chain_items),
            trend=trend,
            sentiment_trend=sentiment_trend,
        )
    except Exception:
        logger.debug("event_bus emit failed for evolution chain", exc_info=True)

    return EvolutionChainResult(
        chain=chain_items,
        length=len(chain_items),
        trend=trend,
        sentiment_trend=sentiment_trend,
        is_evolving=is_evolving,
    )
