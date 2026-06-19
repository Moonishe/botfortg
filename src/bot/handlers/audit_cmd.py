"""Command: /audit — security posture audit."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnlyStrict
from src.core.security.audit import SecurityAuditor

router = Router(name="audit")
router.message.filter(OwnerOnlyStrict())

_EMOJI: dict[str, str] = {
    "ok": "✅",
    "warning": "⚠️",
    "critical": "❌",
    "info": "ℹ️",
    "secure": "🟢",
}

_OVERALL_LABEL: dict[str, str] = {
    "secure": "Secure",
    "warning": "Warning",
    "critical": "Critical",
}


@router.message(Command("audit"))
async def cmd_audit(message: Message) -> None:
    """Run a security audit and return a formatted report."""
    auditor = SecurityAuditor()
    report = await auditor.run()

    lines: list[str] = ["🔐 <b>Security Audit</b>\n"]

    for finding in report.findings:
        emoji = _EMOJI.get(finding.status, "❓")
        lines.append(f"{emoji} <b>{finding.title}:</b> {finding.message}")
        if finding.recommendation:
            lines.append(f"   💡 {finding.recommendation}")
        lines.append("")

    overall_emoji = _EMOJI.get(report.overall, "❓")
    label = _OVERALL_LABEL.get(report.overall, report.overall)
    lines.append(f"Overall: {overall_emoji} <b>{label}</b>")

    await message.answer("\n".join(lines))
