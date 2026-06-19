"""Security audit — read-only security posture check.

Runs 8 checks and returns an :class:`AuditReport` with findings.

Usage::

    auditor = SecurityAuditor()
    report = await auditor.run()
    print(report.overall)  # "secure" | "warning" | "critical"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Literal

from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class AuditFinding:
    """Single security check result."""

    check_id: str
    title: str
    status: Literal["ok", "warning", "critical", "info"]
    message: str
    recommendation: str | None = None


@dataclass
class AuditReport:
    """Aggregated security audit result."""

    overall: Literal["secure", "warning", "critical"]
    findings: list[AuditFinding] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SecurityAuditor:
    """Runs a read-only security audit and returns an :class:`AuditReport`.

    All checks are read-only — no config, state, or side effects are modified.
    """

    _SANDBOX_CACHE_TTL: float = 60.0  # seconds
    _sandbox_cache: ClassVar[tuple[float, AuditFinding] | None] = None

    def __init__(self) -> None:
        pass

    async def run(self) -> AuditReport:
        """Execute all 8 security checks and return a report."""
        findings: list[AuditFinding] = [
            self._check_owner(),
            self._check_approval_mode(),
            self._check_hmac_key(),
            await self._check_pairing(),
            self._check_tools(),
            self._check_blocklist(),
            await self._check_sandbox(),
            self._check_network(),
        ]

        if any(f.status == "critical" for f in findings):
            overall: Literal["secure", "warning", "critical"] = "critical"
        elif any(f.status == "warning" for f in findings):
            overall = "warning"
        else:
            overall = "secure"

        return AuditReport(
            overall=overall,
            findings=findings,
            timestamp=datetime.now(timezone.utc),
        )

    # ── Check 1: Owner configured ──────────────────────────────────────

    def _check_owner(self) -> AuditFinding:
        if settings.owner_telegram_id > 0:
            return AuditFinding(
                check_id="owner",
                title="Owner",
                status="ok",
                message=f"Configured (ID: {settings.owner_telegram_id})",
            )
        return AuditFinding(
            check_id="owner",
            title="Owner",
            status="critical",
            message="OWNER_TELEGRAM_ID is not set (zero or negative)",
            recommendation="Set OWNER_TELEGRAM_ID in .env to your real Telegram user ID",
        )

    # ── Check 2: Approval mode ─────────────────────────────────────────

    def _check_approval_mode(self) -> AuditFinding:
        mode = settings.approval_mode
        if mode == "manual":
            return AuditFinding(
                check_id="approval_mode",
                title="Approval mode",
                status="ok",
                message=f"manual — all medium+ tools require confirmation",
            )
        elif mode == "smart":
            return AuditFinding(
                check_id="approval_mode",
                title="Approval mode",
                status="warning",
                message=f"smart — only high/critical tools require confirmation",
                recommendation="Set approval_mode=manual for production",
            )
        else:  # "off"
            return AuditFinding(
                check_id="approval_mode",
                title="Approval mode",
                status="critical",
                message="off — confirmation disabled (hardline blocklist still active)",
                recommendation="Set approval_mode=smart or manual",
            )

    # ── Check 3: HMAC key ──────────────────────────────────────────────

    def _check_hmac_key(self) -> AuditFinding:
        hmac_key = settings.approval_hmac_key
        if hmac_key and hmac_key.strip():
            return AuditFinding(
                check_id="hmac_key",
                title="HMAC key",
                status="ok",
                message="Dedicated APPROVAL_HMAC_KEY configured",
            )
        # ponytail: check for file-based fallback (data/.approval_hmac_key)
        hmac_file = settings.data_dir / ".approval_hmac_key"
        if hmac_file.exists():
            return AuditFinding(
                check_id="hmac_key",
                title="HMAC key",
                status="info",
                message="Using file-based HMAC key: data/.approval_hmac_key",
            )
        return AuditFinding(
            check_id="hmac_key",
            title="HMAC key",
            status="warning",
            message="Falling back to encryption_key for HMAC signatures",
            recommendation="Set APPROVAL_HMAC_KEY in .env for separate signing key",
        )

    # ── Check 4: DM pairing state ──────────────────────────────────────

    async def _check_pairing(self) -> AuditFinding:
        try:
            from src.core.security.pairing import pairing

            allowed = await pairing.allowlist_size()
            pending = await pairing.pending_count()
        except Exception:
            logger.debug("Pairing check failed", exc_info=True)
            return AuditFinding(
                check_id="pairing",
                title="DM pairing",
                status="warning",
                message="Could not query pairing state",
            )
        return AuditFinding(
            check_id="pairing",
            title="DM pairing",
            status="info",
            message=f"Approved contacts: {allowed}, pending: {pending}",
        )

    # ── Check 5: Tools inventory ───────────────────────────────────────

    def _check_tools(self) -> AuditFinding:
        try:
            from src.core.actions.tool_registry import tool_registry

            tools = tool_registry.get_available_tools()
        except Exception:
            logger.debug("Tool registry check failed", exc_info=True)
            return AuditFinding(
                check_id="tools",
                title="Tools",
                status="warning",
                message="Could not query tool registry",
            )

        counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        high_no_confirm: list[str] = []

        for t in tools:
            risk = t.effective_risk()
            counts[risk] = counts.get(risk, 0) + 1
            if risk in ("high", "critical") and not t.effective_requires_confirmation():
                high_no_confirm.append(t.name)

        msg = f"low={counts['low']} medium={counts['medium']} high={counts['high']} critical={counts['critical']}"

        status: Literal["ok", "warning", "critical", "info"]
        if high_no_confirm:
            # ponytail: show at most 5 names to keep output readable
            names = ", ".join(high_no_confirm[:5])
            if len(high_no_confirm) > 5:
                names += f" +{len(high_no_confirm) - 5} more"
            msg += f" | ⚠️ High/critical without confirmation: {names}"
            status = "warning"
        else:
            status = "ok"

        return AuditFinding(
            check_id="tools",
            title="Tools",
            status=status,
            message=msg,
        )

    # ── Check 6: Hardline blocklist ────────────────────────────────────

    def _check_blocklist(self) -> AuditFinding:
        try:
            from src.core.security.hardline_blocklist import check_params

            # Verify check_params is callable and doesn't crash on trivial input
            result = check_params("_audit_test", {})
        except Exception as e:
            return AuditFinding(
                check_id="blocklist",
                title="Hardline blocklist",
                status="critical",
                message=f"check_params raised: {e}",
                recommendation="Check src/core/security/hardline_blocklist.py",
            )

        if result is not None:
            # Unexpected — empty params should not trigger blocklist
            return AuditFinding(
                check_id="blocklist",
                title="Hardline blocklist",
                status="warning",
                message=f"check_params returned unexpected block: {result.get('rule_id', '?')}",
            )

        return AuditFinding(
            check_id="blocklist",
            title="Hardline blocklist",
            status="ok",
            message="Active and operational",
        )

    # ── Check 7: Sandbox status ────────────────────────────────────────

    async def _check_sandbox(self) -> AuditFinding:
        # ponytail: class-level TTL cache to avoid repeated Docker CLI calls
        now = datetime.now(timezone.utc).timestamp()
        if SecurityAuditor._sandbox_cache is not None:
            cached_ts, cached_finding = SecurityAuditor._sandbox_cache
            if now - cached_ts < SecurityAuditor._SANDBOX_CACHE_TTL:
                return cached_finding

        finding: AuditFinding
        if not settings.sandbox_enabled:
            finding = AuditFinding(
                check_id="sandbox",
                title="Sandbox",
                status="critical",
                message="sandbox_enabled=False — code isolation disabled",
                recommendation="Enable sandbox_enabled for code execution isolation",
            )
        else:
            try:
                from src.core.sandbox.manager import SandboxManager

                manager = SandboxManager(settings)
                available = await manager.is_available()
            except Exception:
                logger.debug("Sandbox check failed", exc_info=True)
                finding = AuditFinding(
                    check_id="sandbox",
                    title="Sandbox",
                    status="critical",
                    message="sandbox_enabled=True but sandbox check raised an error",
                    recommendation="Verify Docker is installed and the sandbox image is pulled",
                )
            else:
                if not available:
                    finding = AuditFinding(
                        check_id="sandbox",
                        title="Sandbox",
                        status="critical",
                        message="sandbox_enabled=True but Docker/sandbox image not available",
                        recommendation="Install Docker and pull the sandbox image",
                    )
                else:
                    msg = (
                        f"image={settings.sandbox_image} "
                        f"memory={settings.sandbox_memory_limit} "
                        f"network={'disabled' if settings.sandbox_network_disabled else 'enabled'}"
                    )
                    finding = AuditFinding(
                        check_id="sandbox",
                        title="Sandbox",
                        status="ok",
                        message=msg,
                    )

        SecurityAuditor._sandbox_cache = (now, finding)
        return finding

    # ── Check 8: Network exposure ──────────────────────────────────────

    def _check_network(self) -> AuditFinding:
        webhook_url = (settings.webhook_url or "").strip()
        if not webhook_url:
            return AuditFinding(
                check_id="network",
                title="Network",
                status="info",
                message="Polling mode (no public endpoint)",
            )

        if (
            not settings.webhook_secret_token
            or not settings.webhook_secret_token.strip()
        ):
            return AuditFinding(
                check_id="network",
                title="Network",
                status="critical",
                message=f"Webhook mode: {webhook_url} — no secret token set",
                recommendation="Set WEBHOOK_SECRET_TOKEN in .env for webhook authentication",
            )

        return AuditFinding(
            check_id="network",
            title="Network",
            status="ok",
            message=f"Webhook mode: {webhook_url} (secret token configured)",
        )
