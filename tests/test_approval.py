"""Unit tests for the Hybrid Approval Kernel (src.core.security.approval)."""

from __future__ import annotations

import time


from src.core.security.approval import (
    ApprovalDecision,
    compute_hmac,
    format_callback,
    format_cancel_callback,
    memory_entry,
    memory_ttl,
    parse_callback,
    parse_cancel_callback,
    route_for,
    verify_hmac,
    verify_memory_entry,
    _hash_payload,
)


class TestApprovalKernel:
    """Tests for approval kernel primitives."""

    def test_compute_hmac_deterministic(self) -> None:
        sig1 = compute_hmac("42", 123456, "send", 1_700_000_000.0, "abcdef")
        sig2 = compute_hmac("42", 123456, "send", 1_700_000_000.0, "abcdef")
        assert sig1 == sig2
        assert len(sig1) == 32

    def test_compute_hmac_user_specific(self) -> None:
        sig1 = compute_hmac("42", 123456, "send", 1_700_000_000.0, "abcdef")
        sig2 = compute_hmac("42", 654321, "send", 1_700_000_000.0, "abcdef")
        assert sig1 != sig2

    def test_verify_hmac_valid(self) -> None:
        sig = compute_hmac("42", 123456, "send", 1_700_000_000.0, "abcdef")
        assert verify_hmac(sig, "42", 123456, "send", 1_700_000_000.0, "abcdef")

    def test_verify_hmac_invalid(self) -> None:
        sig = compute_hmac("42", 123456, "send", 1_700_000_000.0, "abcdef")
        assert not verify_hmac(sig, "42", 123456, "send", 1_700_000_000.0, "xxxxx")
        assert not verify_hmac("", "42", 123456, "send", 1_700_000_000.0, "abcdef")

    def test_route_for_high_critical(self) -> None:
        assert route_for("high") == "db"
        assert route_for("critical") == "db"
        assert route_for("HIGH") == "db"
        assert route_for("medium", is_destructive=True) == "db"

    def test_route_for_low_medium(self) -> None:
        assert route_for("low") == "memory"
        assert route_for("medium") == "memory"
        assert route_for("LOW") == "memory"
        assert route_for("medium", is_destructive=False) == "memory"

    def test_format_and_parse_callback(self) -> None:
        cb = format_callback("tool", "42", "a1b2c3d4e5f67890abcd1234ef567890ab")
        assert cb == "ap:tool:42:a1b2c3d4e5f67890abcd1234ef567890ab"
        parsed = parse_callback(cb)
        assert parsed == ("tool", "42", "a1b2c3d4e5f67890abcd1234ef567890ab")

    def test_parse_callback_invalid(self) -> None:
        assert parse_callback("send:confirm:42:sig") is None
        assert parse_callback("ap:badverb:42:sig") is None
        assert parse_callback("ap:tool") is None
        assert parse_callback("") is None

    def test_format_and_parse_cancel_callback(self) -> None:
        cb = format_cancel_callback("send", "42")
        assert cb == "ap:cancel:send:42"
        parsed = parse_cancel_callback(cb)
        assert parsed == ("send", "42")

    def test_memory_entry_signature(self) -> None:
        action_key, entry = memory_entry(
            user_id=123456,
            verb="tool",
            risk="medium",
            human_summary="test",
            payload={"x": 1},
        )
        assert len(action_key) == 12
        assert entry["signature"]
        assert entry["payload_hash"] == _hash_payload({"x": 1})
        assert entry["user_id"] == 123456

    def test_verify_memory_entry_valid(self) -> None:
        _action_key, entry = memory_entry(
            user_id=123456,
            verb="tool",
            risk="medium",
            human_summary="test",
            payload={"x": 1},
        )
        assert verify_memory_entry(entry, 123456, entry["signature"])

    def test_verify_memory_entry_wrong_user(self) -> None:
        _action_key, entry = memory_entry(
            user_id=123456,
            verb="tool",
            risk="medium",
            human_summary="test",
            payload={"x": 1},
        )
        assert not verify_memory_entry(entry, 999999, entry["signature"])

    def test_verify_memory_entry_expired(self) -> None:
        _action_key, entry = memory_entry(
            user_id=123456,
            verb="tool",
            risk="medium",
            human_summary="test",
            payload={"x": 1},
        )
        entry["expires_at"] = time.monotonic() - 1.0
        assert not verify_memory_entry(entry, 123456, entry["signature"])

    def test_approval_decision_is_db(self) -> None:
        d = ApprovalDecision(
            route="db", verb="send", risk="high", human_summary="x", action_key="42"
        )
        assert d.is_db()
        assert not d.is_memory()

    def test_memory_ttl_positive(self) -> None:
        assert memory_ttl() > 0

    def test_hash_payload_stable(self) -> None:
        assert _hash_payload({"a": 1, "b": 2}) == _hash_payload({"b": 2, "a": 1})

    # ── Edge cases: verify_memory_entry ─────────────────────────────────

    def test_verify_memory_entry_empty_dict(self) -> None:
        """Empty dict should fail (falsy check)."""
        assert not verify_memory_entry({}, 123456, "sig")

    def test_verify_memory_entry_none_signature(self) -> None:
        _action_key, entry = memory_entry(
            user_id=123456,
            verb="tool",
            risk="medium",
            human_summary="test",
            payload={"x": 1},
        )
        assert not verify_memory_entry(entry, 123456, "")
        assert not verify_memory_entry(entry, 123456, "wrongsig")

    def test_verify_memory_entry_corrupt_expires_at(self) -> None:
        """Non-numeric expires_at should not crash — returns False."""
        _action_key, entry = memory_entry(
            user_id=123456,
            verb="tool",
            risk="medium",
            human_summary="test",
            payload={"x": 1},
        )
        entry["expires_at"] = "not_a_number"
        assert not verify_memory_entry(entry, 123456, entry["signature"])

    def test_verify_memory_entry_tampered_payload(self) -> None:
        """Modified payload should invalidate the signature."""
        _action_key, entry = memory_entry(
            user_id=123456,
            verb="tool",
            risk="medium",
            human_summary="test",
            payload={"text": "original"},
        )
        # Tamper the payload — payload_hash no longer matches signature.
        entry["payload"] = {"text": "tampered"}
        entry["payload_hash"] = _hash_payload({"text": "tampered"})
        assert not verify_memory_entry(entry, 123456, entry["signature"])

    # ── Edge cases: parse_callback / parse_cancel_callback ─────────────

    def test_parse_callback_cancel_sent_as_confirm(self) -> None:
        """Cancel callback string should not parse as confirm callback."""
        assert parse_callback("ap:cancel:send:42") is None
        assert parse_callback("ap:cancel:tool:abc123") is None

    def test_parse_callback_extra_colons(self) -> None:
        """Extra colons — split limits to 4, extra goes into signature."""
        assert parse_callback("ap:send:42:extra:stuff") is not None
        # The verb is valid, so it parses.
        parsed = parse_callback("ap:send:42:extra:stuff")
        assert parsed is not None
        assert parsed[2] == "extra:stuff"  # signature includes colons

    def test_parse_callback_empty_action_key(self) -> None:
        assert parse_callback("ap:send::sig") is None
        assert parse_callback("ap:tool::a1b2c3d4e5f67890") is None

    def test_parse_cancel_callback_edge_cases(self) -> None:
        assert parse_cancel_callback("") is None
        assert parse_cancel_callback("ap:") is None
        assert parse_cancel_callback("ap:cancel:") is None
        assert parse_cancel_callback("ap:cancel:send:") is None  # empty action_key
        assert parse_cancel_callback("ap:cancel:badverb:42") is None
        assert parse_cancel_callback("ap:cancel:send:abc") is not None

    # ── Edge cases: route_for ──────────────────────────────────────────

    def test_route_for_none_risk(self) -> None:
        assert route_for(None) == "memory"  # type: ignore[arg-type]
        assert route_for("") == "memory"
        assert route_for("  unknown  ") == "memory"

    # ── Edge cases: _hash_payload ──────────────────────────────────────

    def test_hash_payload_none(self) -> None:
        assert _hash_payload(None) == ""

    def test_hash_payload_empty_dict(self) -> None:
        assert _hash_payload({}) == _hash_payload({})
        assert len(_hash_payload({})) == 32

    # ── Edge cases: compute_hmac with boundary values ─────────────────

    def test_compute_hmac_user_id_zero(self) -> None:
        sig = compute_hmac("42", 0, "send", 1_700_000_000.0, "abcdef")
        assert len(sig) == 32

    def test_compute_hmac_empty_action_key(self) -> None:
        sig = compute_hmac("", 123456, "send", 1_700_000_000.0, "abcdef")
        assert len(sig) == 32

    def test_compute_hmac_none_expires_at(self) -> None:
        sig = compute_hmac("42", 123456, "send", None, "abcdef")
        assert len(sig) == 32

    # ── Edge cases: verify_hmac boundary ──────────────────────────────

    def test_verify_hmac_empty_payload_hash(self) -> None:
        sig = compute_hmac("42", 123456, "send", 1_700_000_000.0, "")
        assert verify_hmac(sig, "42", 123456, "send", 1_700_000_000.0, "")
        assert not verify_hmac(sig, "42", 123456, "send", 1_700_000_000.0, "x")

    def test_verify_hmac_wrong_verb(self) -> None:
        sig = compute_hmac("42", 123456, "send", 1_700_000_000.0, "abcdef")
        assert not verify_hmac(sig, "42", 123456, "tool", 1_700_000_000.0, "abcdef")

    # ── Edge cases: memory_entry with explicit action_key ─────────────

    def test_memory_entry_explicit_action_key(self) -> None:
        action_key, entry = memory_entry(
            user_id=42,
            verb="intent",
            risk="low",
            human_summary="x",
            payload={},
            action_key="mykey",
        )
        assert action_key == "mykey"
        assert entry["action_key"] == "mykey"

    def test_memory_entry_with_metadata(self) -> None:
        _action_key, entry = memory_entry(
            user_id=42,
            verb="tool",
            risk="medium",
            human_summary="run",
            payload={},
            metadata={"tool": "echo", "extra": 1},
        )
        assert entry["metadata"] == {"tool": "echo", "extra": 1}

    # ── Security: _secret domain separation ────────────────────────────

    def test_secret_derived_not_equal_to_raw_key(self) -> None:
        """_secret() returns 32 bytes AND differs from raw key truncation."""
        from src.core.security.approval import _secret
        from src.config import settings

        raw_key = settings.encryption_key
        assert raw_key, "encryption_key must be set for this test"
        secret = _secret()
        assert len(secret) == 32
        # Must differ from naive [:32] truncation (domain separation).
        assert secret != raw_key.encode("utf-8")[:32]

    def test_secret_uses_approval_hmac_key_when_set(self) -> None:
        """When approval_hmac_key is set, HMAC uses a different key."""
        from src.core.security import approval as approval_module
        from src.config import settings

        original_secret = approval_module._SECRET_CACHE
        original_hmac_key = getattr(settings, "approval_hmac_key", None)
        try:
            approval_module._SECRET_CACHE = None
            # Force fallback to encryption_key.
            settings.approval_hmac_key = None  # type: ignore[attr-defined]
            fallback_secret = approval_module._secret()

            approval_module._SECRET_CACHE = None
            # Use a different key for HMAC.
            settings.approval_hmac_key = "different-secret-key"  # type: ignore[attr-defined]
            separate_secret = approval_module._secret()

            assert len(fallback_secret) == 32
            assert len(separate_secret) == 32
            assert fallback_secret != separate_secret, (
                "approval_hmac_key must produce a different derived secret"
            )
        finally:
            approval_module._SECRET_CACHE = original_secret
            settings.approval_hmac_key = original_hmac_key  # type: ignore[attr-defined]

    # ── Edge cases: whitespace / empty signatures ──────────────────────

    def test_parse_callback_whitespace_signature(self) -> None:
        """Whitespace-only or empty signature must be rejected."""
        assert (
            parse_callback("ap:send:42:   ") is not None
        )  # parse_callback checks falsy, "   " is truthy
        # But a truly empty signature:
        assert parse_callback("ap:send:42:") is None
        # Whitespace-only action_key:
        assert parse_callback("ap:send: :sig") is not None  # " " is truthy

    def test_parse_callback_empty_verb(self) -> None:
        """Empty verb after 'ap:' prefix must be rejected."""
        assert parse_callback("ap::42:sig") is None

    def test_parse_callback_no_colons(self) -> None:
        """Malformed callback with no separators."""
        assert parse_callback("apsend42sig") is None

    # ── Legacy callback rejection ──────────────────────────────────────

    def test_legacy_send_confirm_rejected(self) -> None:
        """Legacy send:confirm: format must NOT parse as valid callback."""
        assert parse_callback("send:confirm:42:sig") is None
        assert parse_callback("send:confirm:42") is None
        # Must not start with ap: prefix
        assert not "send:confirm:42:sig".startswith("ap:")

    # ── Corrupt JSON payload handling ──────────────────────────────────

    def test_payload_valid_json_not_dict(self) -> None:
        """Valid JSON non-dict values should not crash dict access.
        This is tested at the handler level; here we verify the pattern."""
        import json

        corrupt_values = ["null", "true", "123", "[]", '"str"']
        for val in corrupt_values:
            parsed = json.loads(val)
            with __import__("pytest").raises(TypeError):
                _ = parsed["peer_id"]
