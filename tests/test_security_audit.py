"""Security audit tests — SecurityAuditor checks and /audit command output."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.security.audit import AuditFinding, AuditReport, SecurityAuditor


class TestSecurityAuditor:
    """SecurityAuditor unit tests."""

    # ── run() returns a report ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_run_returns_audit_report(self) -> None:
        """run() returns AuditReport with exactly 8 findings."""
        auditor = SecurityAuditor()
        report = await auditor.run()

        assert isinstance(report, AuditReport)
        assert len(report.findings) == 8
        assert report.overall in {"secure", "warning", "critical"}
        assert report.timestamp is not None

    # ── Owner check ───────────────────────────────────────────────────────

    def test_owner_configured_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """owner_telegram_id > 0 → ok."""
        monkeypatch.setattr("src.core.security.audit.settings.owner_telegram_id", 12345)
        auditor = SecurityAuditor()
        finding = auditor._check_owner()
        assert finding.status == "ok"
        assert finding.check_id == "owner"

    def test_owner_not_set_critical(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """owner_telegram_id == 0 → critical."""
        monkeypatch.setattr("src.core.security.audit.settings.owner_telegram_id", 0)
        auditor = SecurityAuditor()
        finding = auditor._check_owner()
        assert finding.status == "critical"

    # ── Approval mode check ───────────────────────────────────────────────

    @pytest.mark.parametrize(
        "mode,expected",
        [
            ("manual", "ok"),
            ("smart", "warning"),
            ("off", "critical"),
        ],
    )
    def test_approval_mode_status(
        self, monkeypatch: pytest.MonkeyPatch, mode: str, expected: str
    ) -> None:
        """approval_mode maps to correct status."""
        monkeypatch.setattr("src.core.security.audit.settings.approval_mode", mode)
        auditor = SecurityAuditor()
        finding = auditor._check_approval_mode()
        assert finding.status == expected
        assert finding.check_id == "approval_mode"

    # ── HMAC key check ────────────────────────────────────────────────────

    def test_hmac_key_set_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """approval_hmac_key set → ok."""
        monkeypatch.setattr(
            "src.core.security.audit.settings.approval_hmac_key", "my-secret-key"
        )
        auditor = SecurityAuditor()
        finding = auditor._check_hmac_key()
        assert finding.status == "ok"

    def test_hmac_key_file_fallback_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """File-based HMAC key → info/warning."""
        monkeypatch.setattr("src.core.security.audit.settings.approval_hmac_key", None)
        auditor = SecurityAuditor()

        finding = auditor._check_hmac_key()
        # By default no file → warning or info about encryption_key fallback
        assert finding.check_id == "hmac_key"
        assert finding.status in ("warning", "info")

    # ── Overall status logic ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_overall_critical_when_any_critical(self) -> None:
        """Any critical finding → overall=critical."""
        auditor = SecurityAuditor()
        with (
            patch.object(
                auditor,
                "_check_owner",
                return_value=AuditFinding("owner", "Owner", "critical", "test"),
            ),
            patch.object(
                auditor,
                "_check_approval_mode",
                return_value=AuditFinding("mode", "Mode", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_hmac_key",
                return_value=AuditFinding("hmac", "HMAC", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_pairing",
                new_callable=AsyncMock,
                return_value=AuditFinding("pair", "Pair", "info", "test"),
            ),
            patch.object(
                auditor,
                "_check_tools",
                return_value=AuditFinding("tools", "Tools", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_blocklist",
                return_value=AuditFinding("bl", "BL", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_sandbox",
                new_callable=AsyncMock,
                return_value=AuditFinding("sb", "SB", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_network",
                return_value=AuditFinding("net", "Net", "info", "test"),
            ),
        ):
            report = await auditor.run()
        assert report.overall == "critical"

    @pytest.mark.asyncio
    async def test_overall_warning_when_any_warning_no_critical(self) -> None:
        """Any warning (no critical) → overall=warning."""
        auditor = SecurityAuditor()
        with (
            patch.object(
                auditor,
                "_check_owner",
                return_value=AuditFinding("owner", "Owner", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_approval_mode",
                return_value=AuditFinding("mode", "Mode", "warning", "test"),
            ),
            patch.object(
                auditor,
                "_check_hmac_key",
                return_value=AuditFinding("hmac", "HMAC", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_pairing",
                new_callable=AsyncMock,
                return_value=AuditFinding("pair", "Pair", "info", "test"),
            ),
            patch.object(
                auditor,
                "_check_tools",
                return_value=AuditFinding("tools", "Tools", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_blocklist",
                return_value=AuditFinding("bl", "BL", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_sandbox",
                new_callable=AsyncMock,
                return_value=AuditFinding("sb", "SB", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_network",
                return_value=AuditFinding("net", "Net", "info", "test"),
            ),
        ):
            report = await auditor.run()
        assert report.overall == "warning"

    @pytest.mark.asyncio
    async def test_overall_secure_when_all_ok_info(self) -> None:
        """All ok/info → overall=secure."""
        auditor = SecurityAuditor()
        with (
            patch.object(
                auditor,
                "_check_owner",
                return_value=AuditFinding("owner", "Owner", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_approval_mode",
                return_value=AuditFinding("mode", "Mode", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_hmac_key",
                return_value=AuditFinding("hmac", "HMAC", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_pairing",
                new_callable=AsyncMock,
                return_value=AuditFinding("pair", "Pair", "info", "test"),
            ),
            patch.object(
                auditor,
                "_check_tools",
                return_value=AuditFinding("tools", "Tools", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_blocklist",
                return_value=AuditFinding("bl", "BL", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_sandbox",
                new_callable=AsyncMock,
                return_value=AuditFinding("sb", "SB", "ok", "test"),
            ),
            patch.object(
                auditor,
                "_check_network",
                return_value=AuditFinding("net", "Net", "info", "test"),
            ),
        ):
            report = await auditor.run()
        assert report.overall == "secure"


class TestAuditCommand:
    """Tests for /audit command handler output."""

    @pytest.mark.asyncio
    async def test_audit_output_contains_expected_sections(self) -> None:
        """Audit output contains key section headers."""
        from src.bot.handlers.audit_cmd import cmd_audit

        message = AsyncMock()
        message.answer = AsyncMock()
        message.text = "/audit"

        with patch(
            "src.bot.handlers.audit_cmd.SecurityAuditor.run",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AuditReport(
                overall="warning",
                findings=[
                    AuditFinding("owner", "Owner", "ok", "Configured (ID: 123)"),
                    AuditFinding(
                        "mode",
                        "Approval mode",
                        "warning",
                        "smart",
                        "Set manual for production",
                    ),
                    AuditFinding("hmac", "HMAC key", "ok", "Dedicated key"),
                    AuditFinding(
                        "pair", "DM pairing", "info", "Approved: 3, pending: 0"
                    ),
                    AuditFinding(
                        "tools", "Tools", "ok", "low=5 medium=2 high=0 critical=0"
                    ),
                    AuditFinding("bl", "Hardline blocklist", "ok", "Active"),
                    AuditFinding(
                        "sb", "Sandbox", "critical", "Disabled", "Enable sandbox"
                    ),
                    AuditFinding("net", "Network", "info", "Polling mode"),
                ],
            )
            await cmd_audit(message)

        output = message.answer.call_args[0][0]
        assert "Security Audit" in output
        assert "Owner:" in output
        assert "Approval mode:" in output
        assert "HMAC key:" in output
        assert "DM pairing:" in output
        assert "Tools:" in output
        assert "Hardline blocklist:" in output
        assert "Sandbox:" in output
        assert "Network:" in output
        assert "Overall:" in output
        assert "Warning" in output


# ── Edge case tests ──────────────────────────────────────────────────


class TestSecurityAuditEdgeCases:
    """Tests for edge cases in security audit checks."""

    def test_check_tools_empty_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_check_tools handles empty tool registry gracefully."""
        mock_registry = MagicMock()
        mock_registry.get_available_tools.return_value = []

        # tool_registry is imported inside _check_tools, so patch the source module
        with patch("src.core.actions.tool_registry.tool_registry", mock_registry):
            auditor = SecurityAuditor()
            finding = auditor._check_tools()

        assert finding.check_id == "tools"
        assert finding.status == "ok"
        assert "low=0" in finding.message

    @pytest.mark.asyncio
    async def test_check_sandbox_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_check_sandbox returns critical when sandbox_enabled=False."""
        monkeypatch.setattr("src.core.security.audit.settings.sandbox_enabled", False)
        auditor = SecurityAuditor()
        finding = await auditor._check_sandbox()

        assert finding.check_id == "sandbox"
        assert finding.status == "critical"
        assert "sandbox_enabled=False" in finding.message
