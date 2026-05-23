from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.db.repo import get_or_create_user, get_persona, update_persona
from src.db.session import get_session


router = Router(name="mode")
router.message.filter(OwnerOnly())


# ─── Predefined mode presets ───────────────────────────────────────────
# Values are mapped to AdaptivePersona DB columns:
#   brevity:   short / normal / detailed
#   formality: casual / friendly / formal
#   warmth:    low / normal / high   (spec "empathy" → warmth)
#   emoji_level: none / minimal / normal / rich  (spec 0-3 int → str)

MODES: dict[str, dict[str, str]] = {
    "work": {
        "base_tone": "professional",
        "brevity": "short",
        "formality": "formal",
        "warmth": "normal",
        "emoji_level": "none",
    },
    "caring": {
        "base_tone": "friendly",
        "brevity": "detailed",
        "formality": "casual",
        "warmth": "high",
        "emoji_level": "rich",
    },
    "brief": {
        "base_tone": "efficient",
        "brevity": "short",
        "formality": "friendly",
        "warmth": "low",
        "emoji_level": "none",
    },
    "default": {
        "base_tone": "default",
        "brevity": "normal",
        "formality": "friendly",
        "warmth": "normal",
        "emoji_level": "normal",
    },
    "cynical": {
        "base_tone": "cynical",
        "brevity": "normal",
        "formality": "casual",
        "warmth": "low",
        "emoji_level": "minimal",
    },
    "warm": {
        "base_tone": "friendly",
        "brevity": "normal",
        "formality": "casual",
        "warmth": "high",
        "emoji_level": "rich",
    },
}


# Human-readable descriptions for each mode
MODE_DESCRIPTIONS: dict[str, str] = {
    "work": "👔 Деловой — профессиональный тон, сжато и по делу",
    "caring": "🤗 Заботливый — дружелюбный, развёрнутый, тёплый",
    "brief": "⚡ Краткий — только суть, без лишнего",
    "default": "🔵 Стандартный — сбалансированный стиль по умолчанию",
    "cynical": "😏 Циничный — с иронией, без прикрас",
    "warm": "☀️ Тёплый — максимально дружелюбный и эмоциональный",
}


def _describe(mode_name: str, mode: dict[str, str]) -> str:
    """Return a one-line summary of the mode values."""
    parts = [
        f"тон: {mode['base_tone']}",
        f"краткость: {mode['brevity']}",
        f"формальность: {mode['formality']}",
        f"теплота: {mode['warmth']}",
        f"эмодзи: {mode['emoji_level']}",
    ]
    return f"<b>{mode_name}</b> — {MODE_DESCRIPTIONS.get(mode_name, '')}\n<code>{', '.join(parts)}</code>"


# ─── Handlers ──────────────────────────────────────────────────────────


@router.message(Command("mode"))
async def cmd_mode(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip().lower()

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        persona = await get_persona(session, owner)

        # No args → show current mode + list available modes
        if not args or args not in MODES:
            # Figure out which preset (if any) matches current persona
            current_preset = _guess_current_mode(persona)

            lines: list[str] = []
            if current_preset:
                lines.append(
                    f"🎯 <b>Текущий режим:</b> {current_preset}\n"
                    f"{_describe(current_preset, MODES[current_preset])}\n"
                )
            else:
                # Show raw current values
                current = (
                    f"тон: {persona.base_tone}, "
                    f"краткость: {persona.brevity}, "
                    f"формальность: {persona.formality}, "
                    f"теплота: {persona.warmth}, "
                    f"эмодзи: {persona.emoji_level}"
                )
                lines.append(
                    f"📋 <b>Текущие настройки</b> (нет сохранённого пресета)\n<code>{current}</code>\n"
                )

            lines.append("🔄 <b>Доступные режимы:</b>\n")
            for name in MODES:
                lines.append(_describe(name, MODES[name]))
                lines.append("")

            await message.answer("\n".join(lines))
            return

        # Apply the mode
        mode_name = args
        mode_values = MODES[mode_name].copy()

        await update_persona(session, persona, **mode_values)

    # Invalidate persona cache after commit (outside session)
    from src.core.context_cache import invalidate

    await invalidate(f"persona:{message.from_user.id}")

    await message.answer(
        f"✅ <b>Режим «{mode_name}» применён</b>\n{_describe(mode_name, mode_values)}"
    )


# ─── Helpers ───────────────────────────────────────────────────────────


def _guess_current_mode(persona) -> str | None:
    """Return the preset name whose values match the current persona, or None."""
    for name, preset in MODES.items():
        if all(getattr(persona, k, None) == v for k, v in preset.items()):
            return name
    return None
