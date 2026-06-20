"""Minimal tests for mirror.py dedup logic and _on_message_edited fixes."""

import pytest


# ── Test edit dedup is separate from message dedup ────────────────────────


async def _import_dedup_modules():
    """Lazy-import mirror module-level dedup state."""
    from src.userbot.mirror import (
        _SEEN_EDITS,
        _SEEN_MESSAGES,
        _SEEN_EDITS_LOCK,
        _SEEN_MESSAGES_LOCK,
        _is_edit_duplicate,
        _is_message_duplicate,
        _mark_edit_processed,
        _mark_message_processed,
        _SEEN_EDITS_MAX,
        _SEEN_MESSAGES_MAX,
    )

    return (
        _SEEN_EDITS,
        _SEEN_MESSAGES,
        _SEEN_EDITS_LOCK,
        _SEEN_MESSAGES_LOCK,
        _is_edit_duplicate,
        _is_message_duplicate,
        _mark_edit_processed,
        _mark_message_processed,
        _SEEN_EDITS_MAX,
        _SEEN_MESSAGES_MAX,
    )


@pytest.mark.asyncio
async def test_edit_dedup_separate_from_message_dedup():
    """Bug 3: edits must have independent dedup from new messages.

    A message can be new (processed by _on_message) and then edited
    many times. Using the same dedup set would block all edits after
    the first one.
    """
    (
        seen_edits,
        seen_msgs,
        edits_lock,
        msgs_lock,
        is_edit_dup,
        is_msg_dup,
        mark_edit,
        mark_msg,
        edits_max,
        msgs_max,
    ) = await _import_dedup_modules()

    msg_id, peer_id = 42, 100500

    # Clear both sets
    async with edits_lock:
        seen_edits.clear()
    async with msgs_lock:
        seen_msgs.clear()

    # Mark as NEW message (simulating _on_message)
    await mark_msg(msg_id, peer_id)
    assert await is_msg_dup(msg_id, peer_id), "Message should be seen after mark_msg"

    # Edit of same message should NOT be blocked by message dedup
    assert not await is_edit_dup(msg_id, peer_id), (
        "Edit of seen message must NOT be blocked by message dedup — "
        "edits use a separate set"
    )

    # After processing edit, it should be seen in edits
    await mark_edit(msg_id, peer_id)
    assert await is_edit_dup(msg_id, peer_id), "Edit should be seen after mark_edit"

    # But it should NOT pollute the message dedup set
    async with msgs_lock:
        seen_msgs.pop((msg_id, peer_id), None)  # remove original mark
    assert not await is_msg_dup(msg_id, peer_id), (
        "mark_edit must not affect message dedup set"
    )


@pytest.mark.asyncio
async def test_edit_dedup_eviction():
    """Dedup set evicts oldest entries (FIFO) to prevent unbounded growth."""
    (
        seen_edits,
        _,
        edits_lock,
        _,
        is_edit_dup,
        _,
        mark_edit,
        _,
        edits_max,
        _,
    ) = await _import_dedup_modules()

    async with edits_lock:
        seen_edits.clear()

    # Fill beyond max — should not crash and should evict old entries
    total = edits_max + 100
    for i in range(total):
        await mark_edit(i, 1)

    assert len(seen_edits) <= edits_max * 1.1, (
        f"After {total} inserts, size {len(seen_edits)} should be ≤ "
        f"~{edits_max} (max {edits_max})"
    )

    # Oldest entries (lowest ids) should be evicted first (FIFO)
    async with edits_lock:
        oldest_remaining = min(seen_edits.keys())[0] if seen_edits else -1
    # At least the first ~100 entries should be evicted
    assert oldest_remaining > 50, (
        f"Oldest remaining msg_id={oldest_remaining} — "
        "FIFO eviction should have removed earliest entries"
    )

    # Recent entries should still be present
    assert await is_edit_dup(total - 1, 1), "Most recent entry must be present"


@pytest.mark.asyncio
async def test_mark_edit_processed_move_to_end():
    """mark_edit_processed refreshes insertion order so active keys are not evicted."""
    (
        seen_edits,
        _,
        edits_lock,
        _,
        is_edit_dup,
        _,
        mark_edit,
        _,
        _,
        _,
    ) = await _import_dedup_modules()

    async with edits_lock:
        seen_edits.clear()

    # Fill exactly to max
    for i in range(100):
        await mark_edit(i, 1)

    # Now re-mark entry 0 (oldest) — should move to end
    await mark_edit(0, 1)

    # Insert one more — should evict 50% oldest
    # 0 should survive because it was moved to end
    await mark_edit(100, 1)

    assert await is_edit_dup(0, 1), (
        "Re-marked entry must survive eviction (move-to-end semantics)"
    )


@pytest.mark.asyncio
async def test_classify_returns_real_kind():
    """Bug 1: _classify() returns correct kind based on message attributes."""
    from unittest.mock import MagicMock
    from src.userbot.mirror import _classify

    # Voice message
    voice_msg = MagicMock()
    voice_msg.voice = True
    voice_msg.audio = False
    voice_msg.document = False
    voice_msg.photo = False
    voice_msg.video = False
    voice_msg.sticker = False
    voice_msg.video_note = False
    voice_msg.poll = False
    voice_msg.geo = False
    voice_msg.venue = False
    voice_msg.contact = False
    voice_msg.game = False
    voice_msg.invoice = False
    voice_msg.text = False
    assert _classify(voice_msg) == "voice"

    # Photo message
    photo_msg = MagicMock()
    photo_msg.voice = False
    photo_msg.audio = False
    photo_msg.document = False
    photo_msg.photo = True
    photo_msg.video = False
    photo_msg.sticker = False
    photo_msg.video_note = False
    photo_msg.poll = False
    photo_msg.geo = False
    photo_msg.venue = False
    photo_msg.contact = False
    photo_msg.game = False
    photo_msg.invoice = False
    photo_msg.text = False
    assert _classify(photo_msg) == "photo"

    # Text message
    text_msg = MagicMock()
    for attr in (
        "voice",
        "audio",
        "document",
        "photo",
        "video",
        "sticker",
        "video_note",
        "poll",
        "geo",
        "venue",
        "contact",
        "game",
        "invoice",
    ):
        setattr(text_msg, attr, False)
    text_msg.text = True
    assert _classify(text_msg) == "text"

    # Unknown
    unknown_msg = MagicMock()
    for attr in (
        "voice",
        "audio",
        "document",
        "photo",
        "video",
        "sticker",
        "video_note",
        "poll",
        "geo",
        "venue",
        "contact",
        "game",
        "invoice",
        "text",
    ):
        setattr(unknown_msg, attr, False)
    assert _classify(unknown_msg) == "other"
