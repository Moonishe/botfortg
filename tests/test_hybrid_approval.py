"""Integration tests for the Hybrid Approval Kernel (DB + memory routes)."""

from __future__ import annotations


import json
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup

from src.core.actions import register_builtin_tools
from src.userbot.manager import UserbotManager
from src.core.security import approval
from src.core.security.approval import memory_entry
from src.db.models import PendingAction
from src.db.repos.commitment_repo import (
    create_pending_action,
    verify_pending_action_hmac,
)
from src.db.session import get_session
from src.db.repo import get_or_create_user


@pytest.fixture(autouse=True)
async def setup_db():
    """Recreate tables before each test — per-connection :memory: safety."""
    from src.db.session import engine, Base, init_db
    from sqlalchemy import text
    from src.bot.handlers.free_text._core import _pending_confirmations

    _pending_confirmations.clear()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
    await init_db()

    yield

    _pending_confirmations.clear()
    engine.sync_engine.dispose()


async def test_create_pending_action_stores_hybrid_fields() -> None:
    async with get_session() as session:
        user = await get_or_create_user(session, 1)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 2, "text": "hi"}),
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hi",
        )
        await session.flush()

    assert action.id is not None
    assert action.route == "db"
    assert action.verb == "send"
    assert action.risk == "high"
    assert action.human_summary == "Send hi"
    assert action.hmac_signature is not None


async def test_verify_pending_action_hmac_accepts_valid_signature() -> None:
    async with get_session() as session:
        user = await get_or_create_user(session, 1)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 2, "text": "hi"}),
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hi",
        )
        await session.flush()

    sig = action.hmac_signature
    assert sig is not None
    assert verify_pending_action_hmac(action, sig)
    assert not verify_pending_action_hmac(action, "")
    assert not verify_pending_action_hmac(action, "deadbeef")


async def test_pop_tool_confirmation_db_route() -> None:
    from src.bot.handlers.free_text._core import _pop_tool_confirmation

    async with get_session() as session:
        user = await get_or_create_user(session, 1)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 2, "text": "hi"}),
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hi",
        )
        await session.flush()
        action_id = action.id

    expires_at = (
        action.expires_at.replace(tzinfo=UTC).timestamp() if action.expires_at else None
    )
    sig = approval.compute_hmac(
        action_key=str(action_id),
        user_id=user.id,
        verb="send",
        expires_at=expires_at,
        payload_hash=approval._hash_payload(json.loads(action.payload)),
    )
    assert (
        await _pop_tool_confirmation(str(action_id), user.telegram_id, sig) is not None
    )

    # Row should be deleted after pop.
    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(PendingAction).where(PendingAction.id == action_id)
        )
        assert result.scalar_one_or_none() is None


async def test_pop_tool_confirmation_db_wrong_signature() -> None:
    from src.bot.handlers.free_text._core import _pop_tool_confirmation

    async with get_session() as session:
        user = await get_or_create_user(session, 1)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 2, "text": "hi"}),
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hi",
        )
        await session.flush()
        action_id = action.id

    assert (
        await _pop_tool_confirmation(str(action_id), user.telegram_id, "badbeef")
        is None
    )


async def test_pop_tool_confirmation_memory_route() -> None:
    from src.bot.handlers.free_text._core import _pop_tool_confirmation

    action_key, entry = memory_entry(
        user_id=42,
        verb="tool",
        risk="medium",
        human_summary="run echo",
        payload={"args": ["hi"]},
        metadata={"tool": "echo"},
    )
    # Inject into module-level in-memory store via its lock.
    from src.bot.handlers.free_text import _core as core_module

    core_module._pending_confirmations[action_key] = entry
    pending = await _pop_tool_confirmation(action_key, 42, entry["signature"])
    assert pending is not None
    assert pending["tool"] == "echo"
    assert pending["tool_params"] == {"args": ["hi"]}
    assert action_key not in core_module._pending_confirmations


async def test_send_confirm_keyboard_uses_unified_callbacks() -> None:
    from src.bot.handlers.free_text_common import _confirm_keyboard

    kb = _confirm_keyboard(7, "a1b2c3d4e5f67890abcd1234ef567890ab")
    assert isinstance(kb, InlineKeyboardMarkup)
    row1 = kb.inline_keyboard[0]
    row2 = kb.inline_keyboard[1]
    confirm_btn = next(b for b in row1 if b.text == "✅ Отправить")
    edit_btn = next(b for b in row1 if b.text == "✏ Изменить")
    cancel_btn = next(b for b in row2 if b.text == "❌ Отмена")
    assert confirm_btn.callback_data == "ap:send:7:a1b2c3d4e5f67890abcd1234ef567890ab"
    assert edit_btn.callback_data == "send:edit:7"
    assert cancel_btn.callback_data == "ap:cancel:send:7"


async def test_send_cb_confirm_unified_format_accepts_valid_signature() -> None:
    """Smoke test for the new ap:send: callback path with mocked userbot."""
    from src.bot.handlers.send import cb_confirm
    from src.db.session import get_session

    async with get_session() as session:
        owner = await get_or_create_user(session, 123456)
        action = await create_pending_action(
            session,
            user_id=owner.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 100500, "text": "hello"}),
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hello",
        )
        await session.flush()
        action_id = action.id
        owner_tg = owner.telegram_id

    expires_at = (
        action.expires_at.replace(tzinfo=UTC).timestamp() if action.expires_at else None
    )
    sig = approval.compute_hmac(
        action_key=str(action_id),
        user_id=owner.id,
        verb="send",
        expires_at=expires_at,
        payload_hash=approval._hash_payload(json.loads(action.payload)),
    )

    callback = MagicMock()
    callback.from_user.id = owner_tg
    callback.data = f"ap:send:{action_id}:{sig}"
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    userbot_manager = MagicMock()
    client = AsyncMock()
    client.send_message = AsyncMock()
    userbot_manager.get_client = MagicMock(return_value=client)

    await cb_confirm(callback, userbot_manager)

    callback.answer.assert_awaited()
    # Pending action should be deleted after successful send.
    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(PendingAction).where(PendingAction.id == action_id)
        )
        assert result.scalar_one_or_none() is None


async def test_send_cb_cancel_unified_format() -> None:
    from src.bot.handlers.send import cb_cancel

    async with get_session() as session:
        owner = await get_or_create_user(session, 123456)
        action = await create_pending_action(
            session,
            user_id=owner.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 100500, "text": "hello"}),
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hello",
        )
        await session.flush()
        action_id = action.id

    callback = MagicMock()
    callback.from_user.id = owner.telegram_id
    callback.data = f"ap:cancel:send:{action_id}"
    callback.message = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock()
    state.clear = AsyncMock()

    await cb_cancel(callback, state)

    callback.answer.assert_awaited()
    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(PendingAction).where(PendingAction.id == action_id)
        )
        assert result.scalar_one_or_none() is None


async def test_legacy_send_confirm_rejected() -> None:
    """Legacy send:confirm: callbacks are rejected — parse_callback returns None."""
    from src.bot.handlers.send import cb_confirm

    callback = MagicMock()
    callback.from_user.id = 123456
    callback.data = "send:confirm:42"
    callback.message = AsyncMock()
    callback.answer = AsyncMock()
    userbot_manager = MagicMock()
    userbot_manager.get_client = MagicMock(return_value=AsyncMock())

    await cb_confirm(callback, userbot_manager)
    callback.answer.assert_awaited()
    alert_text = callback.answer.call_args[0][0]
    assert "Ошибка данных" in alert_text


async def test_tool_cb_confirm_legacy_and_unified() -> None:
    """Memory-route tool confirmation accepts both legacy and unified formats."""
    from src.bot.handlers.free_text._core import (
        _cb_tool_confirm,
        _cb_tool_cancel,
        _pending_confirmations,
    )

    register_builtin_tools()

    action_key, entry = memory_entry(
        user_id=42,
        verb="tool",
        risk="medium",
        human_summary="list contacts",
        payload={},
        metadata={"tool": "list_contacts"},
    )
    _pending_confirmations[action_key] = entry

    callback = MagicMock()
    callback.from_user.id = 42
    callback.data = f"ap:tool:{action_key}:{entry['signature']}"
    callback.message = AsyncMock()
    callback.answer = AsyncMock()
    state = MagicMock()
    userbot_manager = MagicMock()

    await _cb_tool_confirm(callback, state, userbot_manager)
    callback.answer.assert_awaited()
    assert action_key not in _pending_confirmations

    # Cancel unified memory route
    action_key2, entry2 = memory_entry(
        user_id=42,
        verb="tool",
        risk="medium",
        human_summary="list contacts",
        payload={},
        metadata={"tool": "list_contacts"},
    )
    _pending_confirmations[action_key2] = entry2

    cancel_callback = MagicMock()
    cancel_callback.from_user.id = 42
    cancel_callback.data = f"ap:cancel:tool:{action_key2}"
    cancel_callback.message = AsyncMock()
    cancel_callback.answer = AsyncMock()

    await _cb_tool_cancel(cancel_callback, state, userbot_manager)
    cancel_callback.answer.assert_awaited()
    assert action_key2 not in _pending_confirmations

    # Legacy memory cancel is rejected (no signature = no ownership proof).
    action_key3, entry3 = memory_entry(
        user_id=42,
        verb="tool",
        risk="medium",
        human_summary="legacy cancel",
        payload={},
        metadata={"tool": "echo"},
    )
    _pending_confirmations[action_key3] = entry3

    legacy_cancel = MagicMock()
    legacy_cancel.from_user.id = 42
    legacy_cancel.data = f"tool:cancel:{action_key3}"
    legacy_cancel.message = AsyncMock()
    legacy_cancel.answer = AsyncMock()

    await _cb_tool_cancel(legacy_cancel, state, userbot_manager)
    legacy_cancel.answer.assert_awaited()
    assert action_key3 in _pending_confirmations


async def test_pop_tool_confirmation_memory_wrong_user() -> None:
    """Memory-route: wrong user_id cannot pop another user's confirmation."""
    from src.bot.handlers.free_text._core import (
        _pop_tool_confirmation,
        _pending_confirmations,
    )

    action_key, entry = memory_entry(
        user_id=42,
        verb="tool",
        risk="medium",
        human_summary="run echo",
        payload={"args": ["hi"]},
        metadata={"tool": "echo"},
    )
    _pending_confirmations[action_key] = entry

    # Wrong user — should return None and put entry back.
    pending = await _pop_tool_confirmation(action_key, 999, entry["signature"])
    assert pending is None
    assert action_key in _pending_confirmations  # put back

    # Cleanup
    _pending_confirmations.pop(action_key, None)


async def test_pop_tool_confirmation_memory_wrong_signature() -> None:
    """Memory-route: wrong signature returns None, puts entry back."""
    from src.bot.handlers.free_text._core import (
        _pop_tool_confirmation,
        _pending_confirmations,
    )

    action_key, entry = memory_entry(
        user_id=42,
        verb="tool",
        risk="medium",
        human_summary="run echo",
        payload={"args": ["hi"]},
        metadata={"tool": "echo"},
    )
    _pending_confirmations[action_key] = entry

    pending = await _pop_tool_confirmation(action_key, 42, "deadbeef")
    assert pending is None
    assert action_key in _pending_confirmations  # put back (not expired)

    _pending_confirmations.pop(action_key, None)


async def test_pop_tool_confirmation_memory_double_pop() -> None:
    """Memory-route: second pop returns None (already consumed)."""
    from src.bot.handlers.free_text._core import (
        _pop_tool_confirmation,
        _pending_confirmations,
    )

    action_key, entry = memory_entry(
        user_id=42,
        verb="tool",
        risk="medium",
        human_summary="run echo",
        payload={"args": ["hi"]},
        metadata={"tool": "echo"},
    )
    _pending_confirmations[action_key] = entry

    # First pop — succeeds.
    pending1 = await _pop_tool_confirmation(action_key, 42, entry["signature"])
    assert pending1 is not None
    assert action_key not in _pending_confirmations

    # Second pop — already consumed.
    pending2 = await _pop_tool_confirmation(action_key, 42, entry["signature"])
    assert pending2 is None


async def test_pop_tool_confirmation_memory_expired() -> None:
    """Memory-route: expired entry returns None."""
    import time
    from src.bot.handlers.free_text._core import (
        _pop_tool_confirmation,
        _pending_confirmations,
    )

    action_key, entry = memory_entry(
        user_id=42,
        verb="tool",
        risk="medium",
        human_summary="run echo",
        payload={"args": ["hi"]},
        metadata={"tool": "echo"},
    )
    entry["expires_at"] = time.monotonic() - 10.0  # expired 10s ago
    _pending_confirmations[action_key] = entry

    pending = await _pop_tool_confirmation(action_key, 42, entry["signature"])
    assert pending is None
    # Expired entry should NOT be put back.
    assert action_key not in _pending_confirmations


async def test_pop_tool_confirmation_db_double_pop() -> None:
    """DB-route: second pop after first successful pop returns None."""
    from src.bot.handlers.free_text._core import _pop_tool_confirmation
    from src.db.session import get_session

    async with get_session() as session:
        user = await get_or_create_user(session, 1)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="send_message",
            payload='{"peer_id": 2, "text": "hi"}',
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hi",
        )
        await session.flush()
        action_id = action.id

    expires_at = (
        action.expires_at.replace(tzinfo=UTC).timestamp() if action.expires_at else None
    )
    sig = approval.compute_hmac(
        action_key=str(action_id),
        user_id=user.id,
        verb="send",
        expires_at=expires_at,
        payload_hash=approval._hash_payload({"peer_id": 2, "text": "hi"}),
    )

    # First pop — succeeds.
    result1 = await _pop_tool_confirmation(str(action_id), user.telegram_id, sig)
    assert result1 is not None

    # Second pop — row deleted, should return None.
    result2 = await _pop_tool_confirmation(str(action_id), user.telegram_id, sig)
    assert result2 is None


async def test_approval_verify_hmac_verb_mismatch() -> None:
    """HMAC computed for 'send' should not verify with verb='tool'."""
    sig = approval.compute_hmac("42", 1, "send", 1_700_000_000.0, "abcdef")
    assert not approval.verify_hmac(sig, "42", 1, "tool", 1_700_000_000.0, "abcdef")


async def test_approval_hash_payload_different_keys() -> None:
    """Different payloads produce different hashes."""
    h1 = approval._hash_payload({"a": 1})
    h2 = approval._hash_payload({"a": 2})
    h3 = approval._hash_payload({"a": 1, "b": 2})
    assert h1 != h2
    assert h1 != h3
    assert h2 != h3


async def test_pending_action_risk_defaults() -> None:
    """create_pending_action stores risk default if not specified."""
    async with get_session() as session:
        user = await get_or_create_user(session, 1)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="test",
            payload="{}",
            route="memory",
            verb="cron",
            risk="low",
            human_summary="test",
        )
        await session.flush()

    assert action.risk == "low"
    assert action.verb == "cron"


async def test_pop_tool_confirmation_intent_memory_route() -> None:
    """Memory-route intent confirmation (ap:intent:) pops with correct handler key."""
    from src.bot.handlers.free_text._core import (
        _pending_confirmations,
        _pop_tool_confirmation,
    )

    action_key, entry = memory_entry(
        user_id=42,
        verb="intent",
        risk="high",
        human_summary="send message to friend",
        payload={"peer_id": 123, "text": "hi"},
        metadata={"intent": "send_message"},
    )
    _pending_confirmations[action_key] = entry

    pending = await _pop_tool_confirmation(action_key, 42, entry["signature"])
    assert pending is not None
    assert pending["kind"] == "intent"
    assert pending["tool"] == "send_message"
    assert pending["tool_params"] == {"peer_id": 123, "text": "hi"}
    assert action_key not in _pending_confirmations


async def test_cb_tool_cancel_intent_memory_route() -> None:
    """ap:cancel:intent:{action_key} removes the in-memory intent confirmation."""
    from src.bot.handlers.free_text._core import (
        _cb_tool_cancel,
        _pending_confirmations,
    )

    action_key, entry = memory_entry(
        user_id=42,
        verb="intent",
        risk="high",
        human_summary="send message to friend",
        payload={"peer_id": 123, "text": "hi"},
        metadata={"intent": "send_message"},
    )
    _pending_confirmations[action_key] = entry

    cancel_callback = MagicMock()
    cancel_callback.data = f"ap:cancel:intent:{action_key}"
    cancel_callback.from_user.id = 42
    cancel_callback.answer = AsyncMock()
    cancel_callback.message = None

    state = MagicMock(spec=FSMContext)
    userbot_manager = MagicMock(spec=UserbotManager)

    await _cb_tool_cancel(cancel_callback, state, userbot_manager)
    cancel_callback.answer.assert_awaited()
    assert action_key not in _pending_confirmations


# ── Edge case: DB user_id vs telegram_id mismatch in HMAC ──────────────


async def test_store_intent_confirmation_db_route_hmac_uses_db_user_id() -> None:
    """Verify: _store_intent_confirmation uses user.id, not telegram_id, for HMAC.

    Reproduces: when telegram_id (e.g. 999888777) differs from DB user.id
    (auto-increment), the HMAC computed in _store_intent_confirmation must
    match the verification in _pop_tool_confirmation which uses user.id.
    """
    from src.bot.handlers.free_text._core import (
        _store_intent_confirmation,
        _pop_tool_confirmation,
    )

    # Create a user where telegram_id != user.id (simulate later user).
    async with get_session() as session:
        await get_or_create_user(session, 111111)  # id=1
        await get_or_create_user(session, 222222)  # id=2
        user3 = await get_or_create_user(session, 999888777)  # id=3 (likely)

    # user3.telegram_id=999888777, user3.id should be 3
    assert user3.telegram_id != user3.id, (
        f"Expected telegram_id={user3.telegram_id} != user.id={user3.id}"
    )

    # Verify the HMAC algorithm manually — compute expected for both paths.
    from src.core.security import approval

    payload_dict = {"recipient": "Alice", "text": "hello"}
    payload_hash = approval._hash_payload(payload_dict)

    # HMAC with telegram_id (what _store_intent_confirmation does at line 413)
    hmac_with_tg = approval.compute_hmac(
        action_key="test",
        user_id=user3.telegram_id,
        verb="intent",
        expires_at=1_700_000_000.0,
        payload_hash=payload_hash,
    )
    # HMAC with db user.id (what _pop_tool_confirmation does at line 480)
    hmac_with_db = approval.compute_hmac(
        action_key="test",
        user_id=user3.id,
        verb="intent",
        expires_at=1_700_000_000.0,
        payload_hash=payload_hash,
    )
    assert hmac_with_tg != hmac_with_db, (
        f"HMAC collision! tg_id={user3.telegram_id}, db_id={user3.id} "
        f"produced same HMAC: {hmac_with_tg}"
    )

    # Store intent confirmation (simulates what _dispatch does).
    confirm_cb, _cancel_cb = await _store_intent_confirmation(
        telegram_id=user3.telegram_id,
        intent_name="send_message",
        intent=payload_dict,
        human_summary="Send hello to Alice",
        risk="high",  # forces DB route
    )

    # Parse the callback to get action_key and signature.
    parsed = approval.parse_callback(confirm_cb)
    assert parsed is not None, f"Failed to parse confirm_cb: {confirm_cb}"
    _, action_key, sig = parsed

    # pop the confirmation — verification uses user.id, so the stored HMAC
    # (computed with user.id) must match. This verifies the unification fix.
    pending = await _pop_tool_confirmation(action_key, user3.telegram_id, sig)
    assert pending is not None, (
        "HMAC verification failed: _store_intent_confirmation should use user.id "
        f"({user3.id}) for DB-route HMAC, not telegram_id ({user3.telegram_id})."
    )

    # Verify the row was deleted (pop consumes it).
    async with get_session() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(PendingAction).where(PendingAction.id == int(action_key))
        )
        assert result.scalar_one_or_none() is None


async def test_store_intent_confirmation_memory_route_hmac_consistent() -> None:
    """Memory route uses telegram_id throughout — should always be consistent."""
    from src.bot.handlers.free_text._core import (
        _store_intent_confirmation,
        _pop_tool_confirmation,
    )

    confirm_cb, _cancel_cb = await _store_intent_confirmation(
        telegram_id=42,
        intent_name="send_message",
        intent={"recipient": "Bob", "text": "hi"},
        human_summary="Send hi to Bob",
        risk="medium",  # forces memory route
    )

    from src.core.security import approval

    parsed = approval.parse_callback(confirm_cb)
    assert parsed is not None
    _, action_key, sig = parsed

    pending = await _pop_tool_confirmation(action_key, 42, sig)
    assert pending is not None
    assert pending["tool"] == "send_message"


# ── Edge case: _confirmed with multiple candidates ─────────────────────


async def test_exec_classic_send_message_confirmed_multi_candidates() -> None:
    """_confirmed=True + multiple candidates: picks top candidate, sends directly.

    exec_classic_send_message checks is_confirmed_truthy before candidate
    disambiguation (line 996), so the picker is never shown when the
    guardrail already confirmed the action.
    """
    from unittest.mock import patch, AsyncMock, MagicMock
    from src.bot.handlers.free_text_exec import exec_classic_send_message

    # Simulate the scenario: intent has _confirmed=True, recipient=query,
    # but resolve_with_llm returns 2 candidates with scores < 90.
    intent = {
        "_confirmed": True,
        "recipient": "Alice",
        "text": "Hello!",
    }

    message = MagicMock()
    message.from_user.id = 42
    message.answer = AsyncMock()

    state = AsyncMock()
    userbot_manager = MagicMock()
    client = AsyncMock()
    client.get_entity = AsyncMock()
    client.send_message = AsyncMock(return_value=MagicMock(id=1001))
    userbot_manager.get_client = MagicMock(return_value=client)

    # Patch resolve_with_llm to return multiple low-score candidates.
    from src.core.contacts.contact_resolver import ContactCandidate

    candidates = [
        ContactCandidate(
            peer_id=111,
            display_name="Alice W",
            username=None,
            peer_kind="user",
            score=70,
        ),
        ContactCandidate(
            peer_id=222,
            display_name="Alice S",
            username=None,
            peer_kind="user",
            score=65,
        ),
    ]

    with (
        patch(
            "src.bot.handlers.free_text_exec.resolve_with_llm",
            AsyncMock(return_value=candidates),
        ),
        patch(
            "src.bot.handlers.free_text_exec.build_provider",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "src.core.contacts.send_guard.build_send_guard",
            AsyncMock(return_value=MagicMock(formatted_html="")),
        ),
    ):
        await exec_classic_send_message(
            intent, message, state, userbot_manager, tz_name="UTC"
        )

    # Should have sent the message, not shown a picker.
    client.send_message.assert_awaited_once()
    message.answer.assert_awaited()
    answer_text = message.answer.call_args[0][0]
    # Should show "Отправлено", not "Готов отправить" or candidate picker.
    assert "Отправлено" in answer_text, (
        f"Expected 'Отправлено' in answer, got: {answer_text[:200]}"
    )


# ── Edge case: empty signature rejection (no legacy fallback) ──────────


async def test_verify_pending_action_hmac_empty_signature() -> None:
    """Empty signature is always rejected (no legacy fallback)."""
    async with get_session() as session:
        user = await get_or_create_user(session, 1)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 2, "text": "hi"}),
            route="db",
            verb="send",
            risk="high",
        )
        await session.flush()

    # Empty and whitespace-only signatures are always rejected.
    assert not verify_pending_action_hmac(action, ""), (
        "Empty signature must be rejected"
    )
    assert not verify_pending_action_hmac(action, "   "), (
        "Whitespace-only signature must be rejected"
    )


async def test_verify_pending_action_hmac_empty_payload() -> None:
    """Empty or corrupt payload is rejected without raising."""
    async with get_session() as session:
        user = await get_or_create_user(session, 1)
        action = await create_pending_action(
            session,
            user_id=user.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 2, "text": "hi"}),
            route="db",
            verb="send",
            risk="high",
        )
        await session.flush()

    # Empty string payload (DB edge case / corruption)
    action.payload = ""
    assert not verify_pending_action_hmac(action, "valid_sig"), (
        "Empty payload must be rejected"
    )

    # Corrupt JSON payload
    action.payload = "not-json"
    assert not verify_pending_action_hmac(action, "valid_sig"), (
        "Corrupt payload must be rejected"
    )


# ── Edge case: cb_cancel int parsing robustness ────────────────────────


async def test_cb_cancel_robustness_garbage_action_id() -> None:
    """cb_cancel must not crash on malformed callback_data."""
    from src.bot.handlers.send import cb_cancel

    # Garbage in unified format — parse_cancel_callback returns None.
    callback = MagicMock()
    callback.from_user.id = 42
    callback.data = "ap:cancel:send:garbage_not_int"
    callback.message = AsyncMock()
    callback.answer = AsyncMock()
    state = AsyncMock()

    # Should not raise.
    await cb_cancel(callback, state)
    callback.answer.assert_awaited()

    # Garbage in legacy format.
    callback.data = "send:cancel:xyz"
    await cb_cancel(callback, state)

    # Empty data edge case.
    callback.data = ""
    await cb_cancel(callback, state)

    # Truncated data.
    callback.data = "send:cancel"
    await cb_cancel(callback, state)


async def test_ap_send_empty_signature_rejected() -> None:
    """Unified ap:send: callback with empty signature is rejected."""
    from src.bot.handlers.send import cb_confirm
    from src.core.security import approval

    async with get_session() as session:
        owner = await get_or_create_user(session, 123456)
        action = await create_pending_action(
            session,
            user_id=owner.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 100500, "text": "hello"}),
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hello",
        )
        await session.flush()
        action_id = action.id

    callback = MagicMock()
    callback.from_user.id = owner.telegram_id
    callback.data = approval.format_callback("send", str(action_id), "")  # empty sig
    callback.message = AsyncMock()
    callback.answer = AsyncMock()
    userbot_manager = MagicMock()
    userbot_manager.get_client = MagicMock(return_value=AsyncMock())

    await cb_confirm(callback, userbot_manager)
    callback.answer.assert_awaited()
    alert_text = callback.answer.call_args[0][0]
    assert "Ошибка данных" in alert_text or "Недействительная подпись" in alert_text


async def test_execute_intent_strips_llm_confirmed_bypass() -> None:
    """_confirmed from LLM must be ignored; only the verified
    callback path may set it."""
    from src.bot.handlers.free_text._core import _execute_intent

    captured: dict = {}

    async def fake_handler(intent, message, state, userbot_manager, *, tz_name):
        captured["intent"] = intent

    with patch.dict(
        "src.bot.handlers.free_text._core.CLASSIC_INTENT_HANDLERS",
        {"send_message": (fake_handler, "Отправить")},
        clear=False,
    ):
        message = AsyncMock()
        state = AsyncMock()
        userbot_manager = MagicMock()
        await _execute_intent(
            {
                "intent": "send_message",
                "peer_id": 1,
                "text": "hi",
                "_confirmed": True,
            },
            message,
            state,
            userbot_manager,
            tz_name="UTC",
        )

    assert captured["intent"].get("_confirmed") is None


# ── Race condition: double-send via per-user lock ──────────────────────


async def test_double_send_race_prevented_by_lock() -> None:
    """Two concurrent cb_confirm calls: only one client.send_message is awaited."""
    from src.bot.handlers.send import cb_confirm

    async with get_session() as session:
        owner = await get_or_create_user(session, 123456)
        action = await create_pending_action(
            session,
            user_id=owner.id,
            kind="send_message",
            payload=json.dumps({"peer_id": 100500, "text": "hello"}),
            route="db",
            verb="send",
            risk="high",
            human_summary="Send hello",
        )
        await session.flush()
        action_id = action.id
        owner_tg = owner.telegram_id

    expires_at = (
        action.expires_at.replace(tzinfo=UTC).timestamp() if action.expires_at else None
    )
    sig = approval.compute_hmac(
        action_key=str(action_id),
        user_id=owner.id,
        verb="send",
        expires_at=expires_at,
        payload_hash=approval._hash_payload(json.loads(action.payload)),
    )

    client = AsyncMock()
    client.send_message = AsyncMock()

    async def make_callback() -> MagicMock:
        cb = MagicMock()
        cb.from_user.id = owner_tg
        cb.data = f"ap:send:{action_id}:{sig}"
        cb.message = AsyncMock()
        cb.answer = AsyncMock()
        return cb

    userbot_manager = MagicMock()
    userbot_manager.get_client = MagicMock(return_value=client)

    callback1 = await make_callback()
    callback2 = await make_callback()

    import asyncio as _asyncio

    await _asyncio.gather(
        cb_confirm(callback1, userbot_manager),
        cb_confirm(callback2, userbot_manager),
    )

    # Only one send_message should have been awaited — the lock serialized.
    assert client.send_message.await_count == 1, (
        f"Expected 1 send_message, got {client.send_message.await_count}"
    )
