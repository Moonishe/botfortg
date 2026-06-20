"""Tests for MemOS reward loop (TD backprop, decision-repair, η posterior)."""

import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_backprop_recurrence():
    """V_t = α_t·R_t + (1-α_t)·γ·V_{t+1} — verify TD recurrence."""
    from src.core.learning.reward_loop import _validate_reward

    # Pure math test: simulate backprop
    gamma = 0.95
    alpha_base = 0.3
    rewards = [1.0, -0.5, 0.8, 0.0]  # 4 trajectories
    step_indices = [0, 1, 2, 3]

    # Backward pass
    v_next = 0.0
    values = []
    for r, s in zip(reversed(rewards), reversed(step_indices)):
        r = _validate_reward(r)
        alpha_t = max(0.01, min(1.0, alpha_base / (1.0 + s)))
        v_t = alpha_t * r + (1.0 - alpha_t) * gamma * v_next
        v_t = _validate_reward(v_t)
        values.append(v_t)
        v_next = v_t

    values.reverse()

    # Terminal value should be just α·R (no V_{t+1} contribution from beyond)
    assert len(values) == 4
    # All values should be in [-1, 1]
    assert all(-1.0 <= v <= 1.0 for v in values)
    # Terminal (last in time) has highest weight on its own reward
    assert abs(values[-1] - (0.3 / 1.3) * 0.0) < 0.01  # step=3, r=0.0, v_next=0


@pytest.mark.asyncio
async def test_decision_repair_guard_threshold():
    """≥3 fails in ≤5 steps → trip → stash."""
    from src.core.actions.tool_middleware import DecisionRepairGuard

    # Mock settings
    with patch("src.core.actions.tool_middleware.settings") as mock_settings:
        mock_settings.reward_loop_enabled = True
        mock_settings.decision_repair_failure_threshold = 3
        mock_settings.decision_repair_step_window = 5

        # Clear state
        DecisionRepairGuard._failures.clear()
        DecisionRepairGuard._stash.clear()

        sig = "test_tool|timeout"

        # 1st fail — not yet
        reached = await DecisionRepairGuard.bump_failure(sig)
        assert reached is False

        # 2nd fail — not yet
        reached = await DecisionRepairGuard.bump_failure(sig)
        assert reached is False

        # 3rd fail — threshold reached
        reached = await DecisionRepairGuard.bump_failure(sig)
        assert reached is True

        # Stash hint
        DecisionRepairGuard.stash_repair(12345, "Use alternative tool")

        # Pop stash — should return hint and clear
        hint = DecisionRepairGuard.pop_stash(12345)
        assert hint == "Use alternative tool"

        # Pop again — should be None
        assert DecisionRepairGuard.pop_stash(12345) is None


@pytest.mark.asyncio
async def test_eta_posterior_update():
    """Beta(eta_alpha, eta_beta) posterior update from outcomes."""
    from src.core.learning.reward_loop import update_eta_posterior

    skill = MagicMock()
    skill.eta_alpha = 1.0
    skill.eta_beta = 1.0
    skill.id = None  # Mock ID — skip DB persist

    # Success → alpha += 1
    await update_eta_posterior(skill, success=True)
    assert skill.eta_alpha == 2.0
    assert skill.eta_beta == 1.0

    # Failure → beta += 1
    await update_eta_posterior(skill, success=False)
    assert skill.eta_alpha == 2.0
    assert skill.eta_beta == 2.0

    # Mean = alpha / (alpha + beta) = 2/4 = 0.5
    mean = skill.eta_alpha / (skill.eta_alpha + skill.eta_beta)
    assert abs(mean - 0.5) < 0.001


def test_validate_reward_trust_boundary():
    """NaN/inf → 0.0, clamp [-1, 1]."""
    from src.core.learning.reward_loop import _validate_reward

    assert _validate_reward(float("nan")) == 0.0
    assert _validate_reward(float("inf")) == 0.0
    assert _validate_reward(float("-inf")) == 0.0
    assert _validate_reward(1.5) == 1.0
    assert _validate_reward(-1.5) == -1.0
    assert _validate_reward(0.5) == 0.5
    assert _validate_reward(0.0) == 0.0


@pytest.mark.asyncio
async def test_compute_reward_heuristic():
    """compute_reward heuristic: 6 branches — success, latency, corrected, skills."""
    from src.core.learning.reward_loop import compute_reward

    # success + fast latency + skills = max positive
    r, _ = await compute_reward(
        success=True,
        latency_ms=500,
        response_text="ok",
        used_skills_json=["skill1"],
        route_mode="default",
    )
    assert 0.7 < r <= 1.0  # 0.5 + 0.2 + 0.1 = 0.8

    # failure + slow + corrected = max negative
    r, _ = await compute_reward(
        success=False,
        latency_ms=10000,
        response_text="bad",
        used_skills_json=None,
        route_mode="default",
        corrected_by_user=True,
    )
    assert -1.0 <= r < -0.7  # -0.5 - 0.1 - 0.3 = -0.9

    # success + medium latency, no skills
    r, _ = await compute_reward(
        success=True,
        latency_ms=3000,
        response_text="ok",
        used_skills_json=None,
        route_mode="default",
    )
    assert abs(r - 0.6) < 0.01  # 0.5 + 0.1 = 0.6

    # negative latency — guard skips latency bonus
    r, _ = await compute_reward(
        success=True,
        latency_ms=-100,
        response_text="ok",
        used_skills_json=None,
        route_mode="default",
    )
    assert abs(r - 0.5) < 0.01  # only success bonus


def test_mask_pii():
    """mask_pii masks email, phone, @username, credit card, ИНН, СНИЛС, IPv4."""
    from src.core.infra.key_guard import mask_pii

    # email
    assert "***" in mask_pii("contact user@example.com please")
    assert "user@example.com" not in mask_pii("user@example.com")

    # Russian phone
    masked = mask_pii("Call +7 999 123 45 67 now")
    assert "+7 999 123 45 67" not in masked
    assert "***" in masked

    # @username (not decorator)
    masked = mask_pii("Message @ivan_petrov about this")
    assert "@ivan_petrov" not in masked

    # credit card (16 digits, various formats)
    assert "1234 5678 9012 3456" not in mask_pii("Card: 1234 5678 9012 3456")
    assert "1234567890123456" not in mask_pii("Card: 1234567890123456")
    assert "1234-5678-9012-3456" not in mask_pii("Card: 1234-5678-9012-3456")

    # ИНН 10-digit (individual)
    masked = mask_pii("ИНН: 1234567890")
    assert "1234567890" not in masked

    # ИНН 12-digit (legal entity)
    masked = mask_pii("ИНН: 123456789012")
    assert "123456789012" not in masked

    # СНИЛС (11 digits: XXX-XXX-XXX YY)
    masked = mask_pii("СНИЛС: 123-456-789 01")
    assert "123-456-789 01" not in masked

    # IPv4 (strict: 0-255 per octet)
    masked = mask_pii("Server: 192.168.1.1")
    assert "192.168.1.1" not in masked

    # IPv4: invalid quad NOT masked (300 > 255)
    masked = mask_pii("Bad IP: 300.1.1.1")
    assert "300.1.1.1" in masked  # strict regex rejects invalid octets

    # regular text preserved
    assert mask_pii("Hello world") == "Hello world"
    assert mask_pii("") == ""
    assert mask_pii(None) is None  # type: ignore[arg-type]


def test_pop_stash_no_fallback():
    """pop_stash returns None for non-existent key — no cross-user fallback."""
    from src.core.actions.tool_middleware import DecisionRepairGuard

    with patch("src.core.actions.tool_middleware.settings") as mock_settings:
        mock_settings.reward_loop_enabled = True
        mock_settings.owner_telegram_id = 111

        DecisionRepairGuard._stash.clear()
        # Stash under owner (as stash_repair does)
        DecisionRepairGuard.stash_repair(111, "hint for owner")

        # pop_stash with different ID — NO fallback, returns None
        result = DecisionRepairGuard.pop_stash(999)
        assert result is None

        # Owner can still pop their own stash
        result = DecisionRepairGuard.pop_stash(111)
        assert result == "hint for owner"

        # Second pop — stash drained
        assert DecisionRepairGuard.pop_stash(111) is None


@pytest.mark.asyncio
async def test_reward_loop_integration():
    """Full pipeline: backprop → induce → crystallize. Uses in-memory SQLite, NOT production DB."""
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import (
        create_async_engine,
        AsyncSession,
        async_sessionmaker,
    )

    # Create ISOLATED in-memory engine — never touches data/app.db
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    TestSession = async_sessionmaker(test_engine, expire_on_commit=False)

    # Patch get_session + init_db to use our isolated engine
    from contextlib import asynccontextmanager
    import src.db.session as db_session_mod
    import src.core.learning.reward_loop as rl_mod
    from unittest.mock import patch

    @asynccontextmanager
    async def _test_get_session():
        async with TestSession() as s:
            yield s

    from src.db.models._learning import Trajectory, Skill
    from src.db.models import User, Base
    from src.config import settings
    from src.core.learning.reward_loop import (
        backprop_values,
        induce_policies,
        crystallize_policies,
    )
    from sqlalchemy import select as _select, delete

    TEST_UID = 999001
    original_min = settings.reward_min_episodes
    try:
        settings.reward_min_episodes = 1

        # Patch module-level session references in a context so they are
        # restored automatically even if the test fails.
        with (
            patch.object(db_session_mod, "get_session", _test_get_session),
            patch.object(db_session_mod, "engine", test_engine),
            patch.object(rl_mod, "get_session", _test_get_session),
        ):
            # Create schema in isolated in-memory DB
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            # Create a test user (FK constraint requires it)
            async with TestSession() as session:
                session.add(User(id=TEST_UID, telegram_id=TEST_UID))
                await session.flush()

                for i in range(3):
                    t = Trajectory(
                        user_id=TEST_UID,
                        request_text=f"test request {i}",
                        response_text="test response",
                        success=True,
                        reward_value=0.8,
                        value_estimate=0.5,
                        step_index=i,
                        route_mode="default",
                    )
                    session.add(t)
                await session.commit()

            # backprop — verify no crash
            updated = await backprop_values(TEST_UID, window=10)
            assert isinstance(updated, int)

            # induce — verify no crash
            induced = await induce_policies(TEST_UID)
            assert isinstance(induced, int)

            # crystallize — functional verification
            result = await crystallize_policies(TEST_UID)
            assert isinstance(result, dict)
            assert "crystallized" in result
            assert "rejected" in result
    finally:
        settings.reward_min_episodes = original_min
        try:
            await test_engine.dispose()
        except Exception:
            pass
