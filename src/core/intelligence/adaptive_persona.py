"""Adaptive Persona — facade module (re-exports).

ChatGPT-style personality system:
- base_tone: базовый стиль и тон (default/professional/friendly/frank/whimsical/efficient/cynical)
- warmth / enthusiasm / headings_lists / emoji_level: характеристики (low/normal/high)
- custom_instructions: пользовательские инструкции
- alias: псевдоним (как обращаться)
- adaptive_mode_enabled: авто-адаптация на основе обратной связи
- base_snapshot_json: снапшот базовых настроек для сброса
"""

from src.core.intelligence.persona_detector import detect_persona_change
from src.core.intelligence.persona_persistence import (
    adapt_persona_from_feedback,
    apply_persona_changes,
    reset_persona_to_snapshot,
)
from src.core.intelligence.persona_adapter import (
    auto_adapt_from_context,
    format_persona_for_prompt,
)

__all__ = [
    "adapt_persona_from_feedback",
    "apply_persona_changes",
    "auto_adapt_from_context",
    "detect_persona_change",
    "format_persona_for_prompt",
    "reset_persona_to_snapshot",
]
