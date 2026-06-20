"""Test user_service error handling."""

import pytest


class TestUserServiceErrorPropagation:
    """Verify non-SQLAlchemy exceptions propagate from get_or_create_user."""

    @pytest.mark.asyncio
    async def test_non_sqlalchemy_exception_propagates(self):
        """Non-SQLAlchemy exceptions should NOT be caught by get_or_create_user."""
        from unittest.mock import AsyncMock, patch
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
