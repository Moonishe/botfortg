"""Tool/intent confirmation callbacks — extracted from free_text/_core.py.

confirm_router is re-exported via __init__.py for bot/app.py.
Uses lazy imports to avoid circular dependency with _core.py.
"""

import asyncio
import json
import logging
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.core.infra.text_sanitizer import sanitize_html
from src.core.infra.task_manager import track_ff
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.userbot.manager import UserbotManager

logger = logging.getLogger(__name__)

# ── Pending tool confirmations ────────────────────────────────────────
# Stores tool calls awaiting user confirmation (from maestro tool loop / guardrails).
# Format: {uid_str: {"telegram_id": int, "kind": "tool|intent", "tool": str,
#                    "tool_params": dict, "ts": float}}
_pending_confirmations: dict[str, dict] = {}
_pending_confirmations_lock = asyncio.Lock()

# Per-user confirm-tool lock — prevents double-execution race
_tool_confirm_locks: dict[int, asyncio.Lock] = {}
_tool_confirm_locks_last_used: dict[int, float] = {}
_tool_confirm_locks_lock = asyncio.Lock()
_TOOL_CONFIRM_LOCK_TTL_SEC = 300  # 5 minutes

# PERF-018: background timer that evicts stale pending confirmations
_BACKGROUND_CLEANUP_INTERVAL = 60
_cleanup_timer_registered: bool = False


def _cleanup_stale_pending() -> None:
    """Remove entries older than ``_PENDING_TTL`` seconds."""
    from src.bot.handlers.free_text._shared import _PENDING_TTL

    now = time.monotonic()
    for uid in list(_pending_confirmations):
        entry = _pending_confirmations[uid]
        if now - entry.get("ts", 0) > _PENDING_TTL:
            del _pending_confirmations[uid]


async def _background_cleanup_stale_pending() -> None:
    """Periodic cleanup task — runs every ``_BACKGROUND_CLEANUP_INTERVAL`` seconds."""
    try:
        while True:
            await asyncio.sleep(_BACKGROUND_CLEANUP_INTERVAL)
            try:
                async with _pending_confirmations_lock:
                    _cleanup_stale_pending()
            except Exception:
                logger.exception("Stale-pending cleanup failed")
    except asyncio.CancelledError:
        logger.debug("Stale-pending cleanup timer stopped (cancelled)")
        raise


def register_cleanup_timer() -> None:
    """Register the background stale-pending cleanup timer (fire-and-forget).

    Idempotent: only registers once, even if called multiple times.
    """
    global _cleanup_timer_registered
    if _cleanup_timer_registered:
        return
    _cleanup_timer_registered = True
    try:
        loop = asyncio.get_running_loop()
        track_ff(loop.create_task(_background_cleanup_stale_pending()))
    except RuntimeError:
        _cleanup_timer_registered = False  # rollback — no loop, can retry later
        pass


# ── Tool confirm lock management ───────────────────────────────────────


async def _get_tool_confirm_lock(telegram_id: int) -> asyncio.Lock:
    """Return a per-user lock; serialize creation to avoid duplicate locks."""
    now = time.monotonic()
    async with _tool_confirm_locks_lock:
        _cleanup_tool_confirm_locks(now)
        lock = _tool_confirm_locks.get(telegram_id)
        if lock is None:
            lock = asyncio.Lock()
            _tool_confirm_locks[telegram_id] = lock
        _tool_confirm_locks_last_used[telegram_id] = now
        return lock


def _cleanup_tool_confirm_locks(now: float) -> None:
    """Remove stale locks that are not currently held."""
    stale = [
        tid
        for tid, ts in _tool_confirm_locks_last_used.items()
        if now - ts > _TOOL_CONFIRM_LOCK_TTL_SEC
    ]
    for tid in stale:
        lock = _tool_confirm_locks.get(tid)
        if lock is None or not lock.locked():
            _tool_confirm_locks.pop(tid, None)
            _tool_confirm_locks_last_used.pop(tid, None)


# ── Keyboard and params helpers ───────────────────────────────────────


def _confirm_tool_keyboard(
    callback_data: str, cancel_data: str
) -> InlineKeyboardMarkup:
    """Inline-кнопки для подтверждения/отмены действия.

    Week 2: accepts pre-formatted unified callback strings
    (``ap:tool:{action_key}:{signature}`` and ``ap:cancel:tool:{action_key}``).
    """
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="✅ Выполнить", callback_data=callback_data),
        InlineKeyboardButton(text="❌ Отмена", callback_data=cancel_data),
    )
    return kb.as_markup()


def _redact_confirmation_params(params: dict) -> dict:
    redacted = {}
    sensitive = (
        "key",
        "token",
        "secret",
        "password",
        "credential",
        "value",
        "api_hash",
        "database_url",
        "proxy_url",
    )
    for name, value in params.items():
        if any(marker in str(name).lower() for marker in sensitive):
            redacted[name] = "***"
        else:
            redacted[name] = value
    return redacted


# ── Store confirmations ───────────────────────────────────────────────


async def _store_confirmation(
    telegram_id: int,
    kind: str,
    params: dict,
    *,
    human_summary: str = "",
    risk: str,
    verb,  # ponytail: Literal["tool","intent"]; untyped to pass through to _ApprovalVerb
    metadata=None,
) -> tuple[str, str]:
    """Shared confirmation storage for tool and intent confirmations.

    Week 2: hybrid routing. HIGH/CRITICAL actions persisted to DB;
    medium actions kept in memory with HMAC-signed callbacks.
    """
    from src.core.security import approval

    route = approval.route_for(risk)
    summary = human_summary or f"Выполнить {kind}"
    payload = dict(params)

    if route == "db":
        from src.db.repo import create_pending_action

        async with get_session() as session:
            user = await get_or_create_user(session, telegram_id)
            action = await create_pending_action(
                session,
                user_id=user.id,
                kind=kind,
                payload=json.dumps(payload, ensure_ascii=False),
                route="db",
                verb=verb,
                risk=risk,
                human_summary=summary,
            )
        action_key = str(action.id)
        sig = action.hmac_signature or ""
        if not sig:
            logger.error(
                "Pending action %s created with empty HMAC signature", action_key
            )
            raise RuntimeError("Failed to create confirmable action: empty signature")
    else:
        action_key, entry = approval.memory_entry(
            user_id=telegram_id,
            verb=verb,
            risk=risk,
            human_summary=summary,
            payload=payload,
            metadata=metadata or {},
        )
        async with _pending_confirmations_lock:
            _cleanup_stale_pending()
            _pending_confirmations[action_key] = entry
        sig = entry["signature"]
        if not sig:
            logger.error(
                "Memory confirmation entry %s created with empty signature", action_key
            )
            raise RuntimeError("Failed to create confirmable action: empty signature")

    confirm_cb = approval.format_callback(verb, action_key, sig)
    cancel_cb = approval.format_cancel_callback(verb, action_key)
    return confirm_cb, cancel_cb


async def _store_tool_confirmation(
    telegram_id: int,
    tool: str,
    tool_params: dict,
    *,
    human_summary: str = "",
    risk: str | None = None,
) -> tuple[str, str]:
    """Store a tool confirmation and return (confirm_callback, cancel_callback).

    Week 2: hybrid routing. HIGH/CRITICAL tools are persisted to the DB;
    medium/read-only tools are kept in memory with HMAC-signed callbacks.
    """
    from src.core.actions import tool_registry

    if risk is None:
        spec = tool_registry.get(tool)
        risk = "medium"
        if spec is not None:
            risk = spec.effective_risk(tool_params.get("action")) or "medium"

    return await _store_confirmation(
        telegram_id=telegram_id,
        kind=tool,
        params=tool_params,
        human_summary=human_summary,
        risk=risk,
        verb="tool",
        metadata={"tool": tool},
    )


async def _store_intent_confirmation(
    telegram_id: int,
    intent_name: str,
    intent: dict,
    *,
    human_summary: str = "",
    risk: str | None = None,
) -> tuple[str, str]:
    """Store an intent confirmation and return (confirm_callback, cancel_callback)."""
    from src.core.intelligence.guardrails import get_action_risk

    if risk is None:
        risk = get_action_risk(intent_name).value

    return await _store_confirmation(
        telegram_id=telegram_id,
        kind=intent_name,
        params=intent,
        human_summary=human_summary,
        risk=risk,
        verb="intent",
        metadata={"intent": intent_name},
    )


# ── Pop confirmations ─────────────────────────────────────────────────


async def _pop_memory_confirmation(
    action_key: str, telegram_id: int, signature: str
) -> dict | None:
    """Pop and verify an in-memory confirmation entry."""
    from src.core.security import approval

    async with _pending_confirmations_lock:
        _cleanup_stale_pending()
        pending = _pending_confirmations.pop(action_key, None)
        if pending is None:
            return None
        if not approval.verify_memory_entry(pending, telegram_id, signature):
            # Ownership or HMAC mismatch — put it back if not expired.
            try:
                if time.monotonic() <= float(pending.get("expires_at", 0)):
                    _pending_confirmations[action_key] = pending
            except (TypeError, ValueError):
                pass  # expired or corrupt entry — don't put it back
            return None
        metadata = pending.get("metadata") or {}
        tool_name = (
            metadata.get("tool") or metadata.get("intent") or pending.get("tool")
        )
        return {
            "user_id": telegram_id,
            "kind": pending.get("verb", "tool"),
            "tool": tool_name,
            "tool_params": pending.get("payload", {}),
        }


async def _pop_tool_confirmation(
    action_key: str, telegram_id: int, signature: str, *, legacy: bool = False
) -> dict | None:
    """Extract and remove a confirmation. Returns None if missing/invalid.

    When ``legacy=True``, HMAC signature verification is skipped (old callbacks
    that pre-date the Hybrid Approval Kernel don't carry signatures). Legacy is
    only supported for the DB route; memory-route confirmations always require
    HMAC, otherwise anyone with the action_key could consume another user's
    pending confirmation.
    """
    from src.db.session import get_session

    # Memory route: check and pop atomically under lock to avoid TOCTOU.
    async with _pending_confirmations_lock:
        _cleanup_stale_pending()
        if action_key in _pending_confirmations:
            # Memory route: legacy callbacks are unsupported (no signature = no security).
            if legacy:
                logger.warning(
                    "_pop_tool_confirmation: legacy memory callback rejected for action_key=%s",
                    action_key,
                )
                return None
            pending = _pending_confirmations.pop(action_key, None)
            if pending is None:
                return None
            from src.core.security import approval

            if not approval.verify_memory_entry(pending, telegram_id, signature):
                # Ownership or HMAC mismatch — put it back if not expired.
                try:
                    if time.monotonic() <= float(pending.get("expires_at", 0)):
                        _pending_confirmations[action_key] = pending
                except (TypeError, ValueError):
                    pass  # expired or corrupt entry — don't put it back
                return None
            metadata = pending.get("metadata") or {}
            tool_name = (
                metadata.get("tool") or metadata.get("intent") or pending.get("tool")
            )
            return {
                "user_id": telegram_id,
                "kind": pending.get("verb", "tool"),
                "tool": tool_name,
                "tool_params": pending.get("payload", {}),
            }

    # DB route: numeric action_key refers to a PendingAction row.
    if action_key.isdigit():
        lock = await _get_tool_confirm_lock(telegram_id)
        try:
            async with asyncio.timeout(_TOOL_CONFIRM_LOCK_TTL_SEC):
                async with lock:
                    async with get_session() as session:
                        from src.db.models import PendingAction
                        from sqlalchemy import select

                        user = await get_or_create_user(session, telegram_id)
                        result = await session.execute(
                            select(PendingAction).where(
                                PendingAction.id == int(action_key),
                                PendingAction.user_id == user.id,
                            )
                        )
                        action = result.scalar_one_or_none()
                        if action is None:
                            return None
                        from src.db.repos.commitment_repo import (
                            is_pending_action_expired,
                            verify_pending_action_hmac,
                        )

                        if is_pending_action_expired(action):
                            logger.info(
                                "_pop_tool_confirmation: expired action_id=%s",
                                action_key,
                            )
                            await session.delete(action)
                            await session.flush()
                            return None
                        if not legacy and not verify_pending_action_hmac(
                            action, signature
                        ):
                            logger.warning(
                                "_pop_tool_confirmation: HMAC mismatch action_id=%s",
                                action_key,
                            )
                            return None
                        try:
                            payload = json.loads(action.payload)
                        except (json.JSONDecodeError, TypeError) as exc:
                            logger.warning(
                                "_pop_tool_confirmation: corrupt payload "
                                "action_id=%s: %s",
                                action_key,
                                exc,
                            )
                            await session.delete(action)
                            await session.flush()
                            return None
                        await session.delete(action)
                        await session.flush()
                        return {
                            "user_id": telegram_id,
                            "kind": action.verb,
                            "tool": action.kind,
                            "tool_params": payload,
                        }
        except TimeoutError:
            async with _tool_confirm_locks_lock:
                if _tool_confirm_locks.get(telegram_id) is lock:
                    _tool_confirm_locks[telegram_id] = asyncio.Lock()
                    _tool_confirm_locks_last_used[telegram_id] = time.monotonic()
            logger.warning(
                "_pop_tool_confirmation: lock timeout for user %d", telegram_id
            )
            return None

    return None


# ── Tool confirmation callback router ─────────────────────────────────

confirm_router = Router(name="free_text_tool_confirm")
confirm_router.callback_query.filter(OwnerOnly())


@confirm_router.callback_query(
    F.data.startswith("tool:confirm:")
    | F.data.startswith("ap:tool:")
    | F.data.startswith("ap:intent:")
)
async def _cb_tool_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    """Callback: пользователь подтвердил выполнение инструмента.

    Week 2: accepts unified ``ap:tool:{action_key}:{signature}`` and legacy
    ``tool:confirm:{uid}`` callbacks.
    """
    from src.core.security import approval

    data = callback.data or ""
    legacy = data.startswith("tool:confirm:")
    action_key = ""
    signature = ""

    if not legacy:
        parsed = approval.parse_callback(data)
        if parsed is None:
            await callback.answer("⏳ Действие устарело", show_alert=True)
            return
        _, action_key, signature = parsed
    else:
        action_key = data.split(":", 2)[2]
        signature = ""

    pending = await _pop_tool_confirmation(
        action_key, callback.from_user.id, signature, legacy=legacy
    )
    if pending is None:
        await callback.answer("⏳ Действие устарело или уже выполнено", show_alert=True)
        return

    tool_name = pending["tool"]
    tool_params = pending["tool_params"]
    logger.info(
        "User %d confirmed tool %s with params %s",
        callback.from_user.id,
        tool_name,
        _redact_confirmation_params(tool_params),
    )

    if callback.message is None:
        await callback.answer("⏳ Сообщение устарело", show_alert=True)
        return

    try:
        if pending.get("kind") == "intent":
            # Lazy imports to avoid circular dependency with _core.py
            from src.bot.handlers.free_text._core import (
                CLASSIC_INTENT_HANDLERS,
                INTENT_HANDLERS,
            )

            handler_info = INTENT_HANDLERS.get(
                tool_name
            ) or CLASSIC_INTENT_HANDLERS.get(tool_name)
            if handler_info is None:
                raise RuntimeError(f"Intent {tool_name!r} not found")
            handler, _ = handler_info
            # Avoid double-confirmation: the guardrail already asked the user.
            confirmed_params = dict(tool_params)
            confirmed_params["_confirmed"] = True
            result = await handler(
                confirmed_params,
                callback.message,
                state,
                userbot_manager,
                tz_name=confirmed_params.get("tz_name", "UTC"),
            )
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(str(result["error"]))
            ok = result.get("ok", True) if isinstance(result, dict) else True
        else:
            from src.core.actions.tool_registry import tool_registry

            async with get_session() as session:
                owner = await get_or_create_user(session, callback.from_user.id)
                client = (
                    userbot_manager.get_client(callback.from_user.id)
                    if userbot_manager
                    else None
                )
                result = await tool_registry.execute(
                    tool_name,
                    _confirmed=True,
                    session=session,
                    user=owner,
                    client=client,
                    userbot_manager=userbot_manager,
                    **tool_params,
                )
            if isinstance(result, dict) and result.get("error"):
                raise RuntimeError(str(result["error"]))
            ok = result.get("ok", True) if isinstance(result, dict) else True
        if isinstance(callback.message, Message):
            if ok:
                await callback.message.edit_text(
                    sanitize_html(f"✅ {tool_name}: выполнено")
                )
            else:
                await callback.message.edit_text(
                    sanitize_html(f"⚠️ {tool_name}: выполнено с предупреждениями")
                )
        await callback.answer("✅ Выполнено")
    except Exception as e:
        logger.warning("tool_confirm_execution failed: %s", e)
        await callback.answer("❌ Произошла ошибка. Попробуй ещё раз", show_alert=True)
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                sanitize_html("❌ Произошла ошибка. Попробуй ещё раз")
            )


@confirm_router.callback_query(
    F.data.startswith("tool:cancel:")
    | F.data.startswith("ap:cancel:tool:")
    | F.data.startswith("ap:cancel:intent:")
)
async def _cb_tool_cancel(
    callback: CallbackQuery,
    state: FSMContext,
    userbot_manager: UserbotManager,
) -> None:
    """Callback: пользователь отменил выполнение инструмента.

    Week 2: accepts unified ``ap:cancel:tool:{action_key}`` and legacy
    ``tool:cancel:{uid}`` callbacks. Unified cancel callbacks do NOT carry a
    signature (they are intentionally short), so we verify ownership via
    user_id for DB-route and by matching the stored telegram_id for memory-route.
    Legacy memory-route cancel callbacks are rejected to prevent action-key
    guessing attacks.
    """
    from src.core.security import approval

    data = callback.data or ""
    legacy = data.startswith("tool:cancel:")
    action_key = ""
    if data.startswith("ap:cancel:"):
        parsed = approval.parse_cancel_callback(data)
        if parsed:
            action_key = parsed[1]
    else:
        action_key = data.split(":", 2)[2]

    if not action_key:
        await callback.answer("⏳ Действие устарело", show_alert=True)
        return

    # DB-route: numeric action_key — delete PendingAction if it belongs to user.
    if action_key.isdigit():
        from src.db.session import get_session
        from src.db.models import PendingAction
        from sqlalchemy import select

        lock = await _get_tool_confirm_lock(callback.from_user.id)
        try:
            async with asyncio.timeout(_TOOL_CONFIRM_LOCK_TTL_SEC):
                async with lock:
                    async with get_session() as session:
                        user = await get_or_create_user(session, callback.from_user.id)
                        result = await session.execute(
                            select(PendingAction).where(
                                PendingAction.id == int(action_key),
                                PendingAction.user_id == user.id,
                            )
                        )
                        action = result.scalar_one_or_none()
                        if action is not None:
                            await session.delete(action)
                            await session.flush()
                            logger.info(
                                "User %d cancelled DB action %s",
                                callback.from_user.id,
                                action_key,
                            )
        except TimeoutError:
            logger.warning(
                "_cb_tool_cancel: lock timeout for user %d", callback.from_user.id
            )
            await callback.answer("⏳ Действие устарело", show_alert=True)
            return
    else:
        # Memory route: legacy cancel without signature is rejected.
        if legacy:
            logger.warning(
                "_cb_tool_cancel: legacy memory cancel rejected for action_key=%s",
                action_key,
            )
            await callback.answer("⏳ Действие устарело", show_alert=True)
            return

        async with _pending_confirmations_lock:
            _cleanup_stale_pending()
            pending = _pending_confirmations.pop(action_key, None)
            if pending is not None and pending.get("user_id") != callback.from_user.id:
                # Ownership mismatch — put it back.
                _pending_confirmations[action_key] = pending
                pending = None
            if pending is not None:
                logger.info(
                    "User %d cancelled memory action %s",
                    callback.from_user.id,
                    action_key,
                )

    await callback.answer("❌ Отменено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text("❌ Действие отменено.")
