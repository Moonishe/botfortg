"""Command: /stats — show cache and circuit breaker metrics."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.core.cache.manager import cache_manager

router = Router(name="stats_cmd")
router.message.filter(OwnerOnly())


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Show cache statistics and circuit breaker metrics."""
    lines = ["📊 <b>Статистика системы</b>\n"]

    # Cache stats
    cache_stats = cache_manager.all_stats()
    if cache_stats:
        lines.append("💾 <b>Кэши:</b>")
        for name, stats in sorted(cache_stats.items()):
            lines.append(f"  • {name}: {stats['size']}/{stats['max_size']}")
            lines.append(
                f"    hit rate: {stats['hit_rate']}, "
                f"hits: {stats['hits']}, misses: {stats['misses']}"
            )
            if stats["evictions"] > 0 or stats["expirations"] > 0:
                lines.append(
                    f"    evictions: {stats['evictions']}, "
                    f"expirations: {stats['expirations']}"
                )
    else:
        lines.append("💾 <b>Кэши:</b> нет данных")

    # Circuit breaker stats
    try:
        from src.llm.router import _CIRCUIT_BREAKERS, _CircuitState

        if _CIRCUIT_BREAKERS:
            lines.append("\n🔌 <b>Circuit Breakers:</b>")
            total = len(_CIRCUIT_BREAKERS)

            # Count by state
            state_counts = {}
            for cb in _CIRCUIT_BREAKERS.values():
                state_name = cb.state.name
                state_counts[state_name] = state_counts.get(state_name, 0) + 1

            lines.append(f"  • Всего: {total}")
            for state_name in ["CLOSED", "OPEN", "HALF_OPEN"]:
                count = state_counts.get(state_name, 0)
                if count > 0:
                    lines.append(f"  • {state_name}: {count}")
        else:
            lines.append("\n🔌 <b>Circuit Breakers:</b> нет активных")
    except ImportError:
        lines.append("\n🔌 <b>Circuit Breakers:</b> недоступно")

    await message.answer("\n".join(lines))
