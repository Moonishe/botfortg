"""Send Guard — единый предохранитель перед отправкой сообщения."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.core.infra.text_sanitizer import sanitize_html
from src.core.memory.temporal_layers import utc_naive, utcnow_naive
from src.db.session import get_session
from src.db.repo import (
    get_or_create_user,
    get_contact,
    list_memories,
    get_contact_profile,
)

logger = logging.getLogger(__name__)


@dataclass
class SendGuardResult:
    risk_level: str = "low"
    warnings: list[str] = field(default_factory=list)
    memory_hints: list[str] = field(default_factory=list)
    profile_hints: list[str] = field(default_factory=list)

    @property
    def formatted_html(self) -> str:
        parts = []
        if self.warnings:
            parts.append("\n".join(f"⚠️ {w}" for w in self.warnings))
        if self.memory_hints:
            parts.append("\n".join(f"🧠 {h}" for h in self.memory_hints))
        if self.profile_hints:
            parts.append("\n".join(f"👤 {h}" for h in self.profile_hints))
        return "\n".join(parts)


async def build_send_guard(
    telegram_id: int, peer_id: int, draft_text: str = ""
) -> SendGuardResult:
    result = SendGuardResult(risk_level="low")
    now = utcnow_naive()

    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        contact = await get_contact(session, owner, peer_id)
        name = contact.display_name if contact else str(peer_id)

        mems = await list_memories(session, owner, contact_id=peer_id, is_active=True)
        neg = [
            m
            for m in mems
            if m.contact_id == peer_id
            and m.sentiment == "negative"
            and m.created_at
            and (now - utc_naive(m.created_at)).days < 14
        ]
        if neg:
            result.risk_level = "high"
            neg_texts = "; ".join(m.fact[:50] for m in neg[:3])
            result.warnings.append(
                "За последние 2 недели негативные факты о "
                f"{sanitize_html(name)}: {sanitize_html(neg_texts)}"
            )

        try:
            prof = await get_contact_profile(session, owner, peer_id)
            if prof:
                if prof.communication_style:
                    result.profile_hints.append(
                        f"Стиль: {sanitize_html(prof.communication_style[:60])}"
                    )
                if prof.communication_dos:
                    dos = (
                        json.loads(prof.communication_dos)
                        if (prof.communication_dos or "").startswith("[")
                        else [prof.communication_dos]
                    )
                    if dos:
                        result.profile_hints.append(
                            f"✅ {sanitize_html(', '.join(dos[:3]))}"
                        )
                if prof.communication_donts:
                    donts = (
                        json.loads(prof.communication_donts)
                        if (prof.communication_donts or "").startswith("[")
                        else [prof.communication_donts]
                    )
                    if donts:
                        result.profile_hints.append(
                            f"❌ {sanitize_html(', '.join(donts[:3]))}"
                        )
                if prof.sensitivity and prof.sensitivity > 0.7:
                    result.risk_level = "high"
                    result.warnings.append(
                        "Высокая чувствительность контакта — будь аккуратнее."
                    )
        except Exception:
            logger.debug("send_guard: profile check skipped", exc_info=True)
            pass

        if contact and contact.archetype == "toxic":
            if result.risk_level != "high":
                result.risk_level = "medium"
            result.warnings.append("Конфликтный контакт — перепроверь сообщение.")

    return result
