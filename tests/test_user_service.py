"""Test user_service error handling and mass-assignment guards."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestUserServiceErrorPropagation:
    """Verify non-SQLAlchemy exceptions propagate from get_or_create_user."""

    @pytest.mark.asyncio
    async def test_non_sqlalchemy_exception_propagates(self):
        """Non-SQLAlchemy exceptions should NOT be caught by get_or_create_user."""
        from src.services.user_service import get_or_create_user

        # Patch _repo_get_or_create_user to raise a non-SQLAlchemy error
        with (
            patch(
                "src.services.user_service._repo_get_or_create_user",
                new=AsyncMock(side_effect=ValueError("simulated non-DB error")),
            ),
            pytest.raises(ValueError, match="simulated non-DB error"),
        ):
            await get_or_create_user(12345)


class _FakeSettings:
    """Stand-in for UserSettings that records real attribute assignments."""

    def __init__(self):
        self.timezone = "UTC"


class TestUpdateUserSettingsGuards:
    """Verify update_user_settings rejects user_id and unknown kwargs."""

    @pytest.fixture
    def _mocks(self):
        user = MagicMock()
        user.settings = _FakeSettings()
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=session_cm)
        session_cm.__aexit__ = AsyncMock(return_value=None)
        return {
            "user": user,
            "session_cm": session_cm,
        }

    @pytest.mark.asyncio
    async def test_user_id_rejected(self, _mocks):
        """user_id is immutable and must be ignored."""
        from src.services.user_service import update_user_settings

        with (
            patch(
                "src.services.user_service._repo_get_or_create_user",
                new=AsyncMock(return_value=_mocks["user"]),
            ),
            patch(
                "src.services.user_service.get_session",
                return_value=_mocks["session_cm"],
            ),
        ):
            result = await update_user_settings(
                12345, user_id=999, timezone="Europe/Moscow"
            )

        assert result is True
        assert _mocks["user"].settings.timezone == "Europe/Moscow"
        assert "user_id" not in _mocks["user"].settings.__dict__

    @pytest.mark.asyncio
    async def test_unknown_kwargs_rejected(self, _mocks):
        """Unknown kwargs are ignored; valid kwargs are applied."""
        from src.services.user_service import update_user_settings

        with (
            patch(
                "src.services.user_service._repo_get_or_create_user",
                new=AsyncMock(return_value=_mocks["user"]),
            ),
            patch(
                "src.services.user_service.get_session",
                return_value=_mocks["session_cm"],
            ),
        ):
            result = await update_user_settings(
                12345, timezone="America/New_York", evil_payload="injection"
            )

        assert result is True
        assert _mocks["user"].settings.timezone == "America/New_York"
        assert "evil_payload" not in _mocks["user"].settings.__dict__

    @pytest.mark.asyncio
    async def test_all_rejected_kwargs_returns_false(self, _mocks):
        """If only rejected kwargs are passed, return False."""
        from src.services.user_service import update_user_settings

        with (
            patch(
                "src.services.user_service._repo_get_or_create_user",
                new=AsyncMock(return_value=_mocks["user"]),
            ),
            patch(
                "src.services.user_service.get_session",
                return_value=_mocks["session_cm"],
            ),
        ):
            result = await update_user_settings(
                12345, user_id=999, evil_payload="injection"
            )

        assert result is False
