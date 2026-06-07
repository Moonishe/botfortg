"""MemoryMode — enum для режимов извлечения памяти (Phase 2).

Заменяет строковые "light"/"normal"/"deep" на типобезопасное перечисление.
Наследует str для обратной совместимости — можно использовать там же, где строки.
"""

from __future__ import annotations

from enum import Enum


class MemoryMode(str, Enum):
    """Режим глубины извлечения памяти.

    SHALLOW — только pinned (limit 3) + recent 5, без семантического поиска, ~10ms.
    LIGHT   — только pinned + task-context (быстро, ~10ms).
    NORMAL  — + Qdrant-semantic + fresh + frequently-used + self + contact (~50ms).
    DEEP    — всё из NORMAL + tier 2-3 prefetch + BFS по MemoryLink графу (~200ms).
    """

    SHALLOW = "shallow"
    LIGHT = "light"
    NORMAL = "normal"
    DEEP = "deep"

    @classmethod
    def _missing_(cls, value: object) -> MemoryMode:
        """Любое неизвестное значение → DEEP (безопасное по умолчанию)."""
        return cls.DEEP

    @classmethod
    def from_string(cls, value: str | None) -> MemoryMode:
        """Безопасное приведение строки к MemoryMode.

        None / пустая строка / неизвестное → DEEP.
        """
        if not value:
            return cls.DEEP
        try:
            return cls(value.lower())
        except ValueError:
            return cls.DEEP

    @property
    def includes_semantic(self) -> bool:
        """Включать ли Qdrant-semantic поиск."""
        return self in (MemoryMode.NORMAL, MemoryMode.DEEP)

    @property
    def includes_deep(self) -> bool:
        """Включать ли tier 2-3 prefetch + BFS граф."""
        return self == MemoryMode.DEEP

    @property
    def includes_frequent(self) -> bool:
        """Включать ли блок frequently-used фактов."""
        return self in (MemoryMode.NORMAL, MemoryMode.DEEP)

    @property
    def includes_self_facts(self) -> bool:
        """Включать ли self-факты (глобальные, без contact_id)."""
        return self in (MemoryMode.NORMAL, MemoryMode.DEEP)

    @property
    def includes_contact_facts(self) -> bool:
        """Включать ли contact-факты (привязанные к контакту)."""
        return self in (MemoryMode.NORMAL, MemoryMode.DEEP)

    @property
    def is_shallow(self) -> bool:
        """SHALLOW режим — только pinned + recent, без DB/семантики."""
        return self == MemoryMode.SHALLOW

    def __repr__(self) -> str:
        return f"MemoryMode.{self.name}"
