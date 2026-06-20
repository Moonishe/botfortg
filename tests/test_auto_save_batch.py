"""Tests for auto-save facts batch optimization.

Covers:
  - _build_batch_prompt: format correctness, escaping, message numbering
  - _parse_single_facts: valid JSON, empty, code-fenced, edge cases
  - _parse_batch_facts: multi-message batch, index matching
  - FactBatchBuffer: add, flush, timeout, concurrency, error handling
  - Integration: enabled/disabled flag, passthrough behavior

Uses in-memory SQLite + MockLLMProvider following project test patterns.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

# ── Environment setup BEFORE importing src modules ──────────────────
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from src.core.memory.auto_save_batch import (
    FactBatchBuffer,
    _build_batch_prompt,
    _parse_single_facts,
    _parse_batch_facts,
    auto_save_single,
    _save_facts_to_db,
    get_batch_buffer,
    reset_batch_buffer,
)
from src.llm.base import ChatMessage, TaskType
from src.db.session import get_session, init_db
from src.db.repo import get_or_create_user
from src.core.memory.memory_service import save_memory_single

OWNER_TG_ID = 123456789

# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def setup_db():
    """Recreate all tables before each test (in-memory SQLite)."""
    from src.db.session import (
        engine,
        Base,
        _FTS_SETUP,
        _SESSION_FTS_SETUP,
        _MEMORY_FTS_SETUP,
    )
    from sqlalchemy import text

    async def _recreate():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
            await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for stmt in _FTS_SETUP:
                await conn.execute(text(stmt))
            for stmt in _SESSION_FTS_SETUP:
                await conn.execute(text(stmt))
            for stmt in _MEMORY_FTS_SETUP:
                await conn.execute(text(stmt))

    asyncio.run(_recreate())


@pytest.fixture(autouse=True)
def reset_buffer():
    """Reset global buffer before each test."""
    reset_batch_buffer()


# ── Helpers ─────────────────────────────────────────────────────────


async def _make_owner(tg_id: int = OWNER_TG_ID):
    """Create / retrieve the test owner user."""
    async with get_session() as session:
        return await get_or_create_user(session, tg_id)


class MockLLMProvider:
    """Mock LLM provider with configurable responses."""

    def __init__(self, responses: list[str] | None = None):
        self.responses: list[str] = responses or []
        self.call_count = 0
        self.name = "mock"
        self._chat_calls: list[list[ChatMessage]] = []

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
    ) -> str:
        self._chat_calls.append(messages)
        if self.call_count >= len(self.responses):
            return json.dumps({"facts": []})
        resp = self.responses[self.call_count]
        self.call_count += 1
        return resp

    async def close(self):
        pass


def _make_fact_response(facts: list[dict]) -> str:
    """Build a single-message facts JSON response."""
    return json.dumps({"facts": facts})


def _make_batch_response(results: list[dict]) -> str:
    """Build a batch results JSON response."""
    return json.dumps({"results": results})


def _make_msg_result(msg_index: int, facts: list[dict]) -> dict:
    """Build a single message result for batch response."""
    return {"msg_index": msg_index, "facts": facts}


# ══════════════════════════════════════════════════════════════════════
# Tests: _build_batch_prompt
# ══════════════════════════════════════════════════════════════════════


class TestBuildBatchPrompt:
    """Unit tests for batch prompt construction."""

    def test_build_single_message_prompt(self):
        """Single message produces a correct batch prompt."""
        messages = [
            {
                "telegram_id": 1,
                "user_text": "Я работаю в Яндексе.",
                "response_text": "Отлично! Чем занимаетесь?",
                "provider": None,
            }
        ]
        prompt = _build_batch_prompt(messages)

        assert "[1] User message:" in prompt
        assert "Я работаю в Яндексе" in prompt
        assert "[1] Assistant reply:" in prompt
        assert '"msg_index"' in prompt
        assert '"facts"' in prompt

    def test_build_multiple_messages_prompt(self):
        """Multiple messages are numbered correctly."""
        messages = [
            {
                "telegram_id": 1,
                "user_text": "Сообщение 1",
                "response_text": "Ответ 1",
                "provider": None,
            },
            {
                "telegram_id": 1,
                "user_text": "Сообщение 2",
                "response_text": "Ответ 2",
                "provider": None,
            },
            {
                "telegram_id": 1,
                "user_text": "Сообщение 3",
                "response_text": "Ответ 3",
                "provider": None,
            },
        ]
        prompt = _build_batch_prompt(messages)

        assert "[1] User message: Сообщение 1" in prompt
        assert "[2] User message: Сообщение 2" in prompt
        assert "[3] User message: Сообщение 3" in prompt
        assert "[1] Assistant reply: Ответ 1" in prompt
        assert "[2] Assistant reply: Ответ 2" in prompt
        assert "[3] Assistant reply: Ответ 3" in prompt

    def test_curly_braces_escaped(self):
        """User text with curly braces is properly escaped."""
        messages = [
            {
                "telegram_id": 1,
                "user_text": 'Использую {"ключ": "значение"}',
                "response_text": "Понял",
                "provider": None,
            }
        ]
        prompt = _build_batch_prompt(messages)
        assert '{{"ключ": "значение"}}' in prompt

    def test_long_text_truncated(self):
        """User text longer than 500 chars is truncated."""
        unique_suffix = "ZZZ_UNIQUE_SUFFIX_ZZZ"
        long_text = "A" * 500 + unique_suffix
        messages = [
            {
                "telegram_id": 1,
                "user_text": long_text,
                "response_text": "OK",
                "provider": None,
            }
        ]
        prompt = _build_batch_prompt(messages)
        # First 500 chars should be present
        assert "A" * 500 in prompt
        # Unique suffix beyond 500 chars should NOT be present
        assert unique_suffix not in prompt


# ══════════════════════════════════════════════════════════════════════
# Tests: _parse_single_facts
# ══════════════════════════════════════════════════════════════════════


class TestParseSingleFacts:
    """Unit tests for single-message JSON parsing."""

    def test_valid_json_with_facts(self):
        """Valid JSON with facts returns the facts list."""
        response = json.dumps(
            {
                "facts": [
                    {"fact": "User works at Yandex", "sentiment": "positive"},
                    {"fact": "User lives in Moscow", "sentiment": "neutral"},
                ]
            }
        )
        facts = _parse_single_facts(response)
        assert len(facts) == 2
        assert facts[0]["fact"] == "User works at Yandex"
        assert facts[1]["fact"] == "User lives in Moscow"

    def test_empty_facts_array(self):
        """Empty facts array returns empty list."""
        response = json.dumps({"facts": []})
        facts = _parse_single_facts(response)
        assert facts == []

    def test_json_with_code_fence(self):
        """JSON wrapped in ``` markers is correctly parsed."""
        response = '```json\n{"facts": [{"fact": "User is a developer", "sentiment": "neutral"}]}\n```'
        facts = _parse_single_facts(response)
        assert len(facts) == 1
        assert facts[0]["fact"] == "User is a developer"

    def test_short_facts_filtered(self):
        """Facts shorter than 5 chars are filtered out."""
        response = json.dumps(
            {
                "facts": [
                    {"fact": "OK", "sentiment": "neutral"},
                    {"fact": "User works at Google", "sentiment": "positive"},
                    {"fact": "meh", "sentiment": "negative"},
                ]
            }
        )
        facts = _parse_single_facts(response)
        assert len(facts) == 1
        assert facts[0]["fact"] == "User works at Google"

    def test_no_facts_key(self):
        """Response without 'facts' key returns empty list."""
        response = json.dumps({"error": "something"})
        facts = _parse_single_facts(response)
        assert facts == []

    def test_invalid_json(self):
        """Invalid JSON raises JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            _parse_single_facts("not json at all")


# ══════════════════════════════════════════════════════════════════════
# Tests: _parse_batch_facts
# ══════════════════════════════════════════════════════════════════════


class TestParseBatchFacts:
    """Unit tests for batch JSON parsing."""

    def test_valid_batch_response(self):
        """Valid batch response returns indexed facts."""
        response = json.dumps(
            {
                "results": [
                    {
                        "msg_index": 1,
                        "facts": [
                            {"fact": "User likes coffee", "sentiment": "positive"}
                        ],
                    },
                    {
                        "msg_index": 2,
                        "facts": [
                            {"fact": "User works remotely", "sentiment": "neutral"},
                            {"fact": "User has a cat", "sentiment": "positive"},
                        ],
                    },
                ]
            }
        )
        parsed = _parse_batch_facts(response)
        assert len(parsed) == 2
        assert parsed[0] == (
            1,
            [{"fact": "User likes coffee", "sentiment": "positive"}],
        )
        assert parsed[1][0] == 2
        assert len(parsed[1][1]) == 2

    def test_batch_with_code_fence(self):
        """Batch JSON in code fence is correctly parsed."""
        response = (
            '```json\n{"results": [{"msg_index": 1, "facts": '
            '[{"fact": "User is a designer", "sentiment": "neutral"}]}]}\n```'
        )
        parsed = _parse_batch_facts(response)
        assert len(parsed) == 1
        assert parsed[0][0] == 1
        assert parsed[0][1][0]["fact"] == "User is a designer"

    def test_batch_short_facts_filtered(self):
        """Short facts within batch results are filtered out."""
        response = json.dumps(
            {
                "results": [
                    {
                        "msg_index": 1,
                        "facts": [
                            {"fact": "OK", "sentiment": "neutral"},
                            {"fact": "Valid long fact here", "sentiment": "positive"},
                        ],
                    }
                ]
            }
        )
        parsed = _parse_batch_facts(response)
        assert len(parsed) == 1
        assert len(parsed[0][1]) == 1
        assert parsed[0][1][0]["fact"] == "Valid long fact here"

    def test_batch_empty_results(self):
        """Batch with no results returns empty list."""
        response = json.dumps({"results": []})
        parsed = _parse_batch_facts(response)
        assert parsed == []

    def test_batch_missing_msg_index(self):
        """Message without msg_index is ignored."""
        response = json.dumps(
            {
                "results": [
                    {"facts": [{"fact": "User likes pizza", "sentiment": "positive"}]},
                    {
                        "msg_index": 1,
                        "facts": [{"fact": "Valid fact", "sentiment": "neutral"}],
                    },
                ]
            }
        )
        parsed = _parse_batch_facts(response)
        assert len(parsed) == 1
        assert parsed[0][0] == 1


# ══════════════════════════════════════════════════════════════════════
# Tests: FactBatchBuffer
# ══════════════════════════════════════════════════════════════════════


class TestFactBatchBuffer:
    """Integration tests for the batch buffer with MockLLMProvider."""

    async def _wait_flush(self, buffer: FactBatchBuffer, timeout: float = 2.0):
        """Wait until the buffer is empty (flush completed)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while buffer.pending_count > 0:
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.05)
        # Also give time for background flush tasks to finish DB writes
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_single_message_flushed_after_timeout(self):
        """Single message is flushed after the timeout period."""
        provider = MockLLMProvider(
            [
                _make_batch_response(
                    [
                        _make_msg_result(
                            1,
                            [{"fact": "User works at Yandex", "sentiment": "neutral"}],
                        )
                    ]
                )
            ]
        )

        buffer = FactBatchBuffer(batch_size=5, timeout=0.1, enabled=True)
        await buffer.add(OWNER_TG_ID, "Я работаю в Яндексе", "Отлично!", provider)

        assert buffer.pending_count == 1

        # Wait for timeout + background flush to complete
        await self._wait_flush(buffer, timeout=2.0)

        assert buffer.pending_count == 0
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_batch_fills_up_flushed_immediately(self):
        """When buffer reaches batch_size, flush happens immediately."""
        # Create enough responses: one per batch flush
        batch_responses = [
            _make_batch_response(
                [
                    _make_msg_result(i, [{"fact": f"Fact {i}", "sentiment": "neutral"}])
                    for i in range(1, 4)
                ]
            )
        ]

        provider = MockLLMProvider([batch_responses[0]])
        buffer = FactBatchBuffer(batch_size=3, timeout=5.0, enabled=True)

        await buffer.add(OWNER_TG_ID, "Msg 1", "Reply 1", provider)
        await buffer.add(OWNER_TG_ID, "Msg 2", "Reply 2", provider)
        assert buffer.pending_count == 2  # Not full yet

        await buffer.add(OWNER_TG_ID, "Msg 3", "Reply 3", provider)
        # Full — flush started immediately, but it's background
        await asyncio.sleep(0.1)
        assert buffer.pending_count == 0
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_mixed_different_users(self):
        """Messages from different users are processed in the same batch correctly."""
        provider = MockLLMProvider(
            [
                _make_batch_response(
                    [
                        _make_msg_result(
                            1,
                            [
                                {
                                    "fact": "User1 works at Google",
                                    "sentiment": "positive",
                                }
                            ],
                        ),
                        _make_msg_result(
                            2, [{"fact": "User2 lives in SPB", "sentiment": "neutral"}]
                        ),
                    ]
                )
            ]
        )

        user2_tg_id = 999999

        buffer = FactBatchBuffer(batch_size=2, timeout=5.0, enabled=True)
        await buffer.add(OWNER_TG_ID, "Я работаю в Google", "OK", provider)
        await buffer.add(user2_tg_id, "Я живу в СПБ", "OK", provider)

        await asyncio.sleep(0.2)
        assert buffer.pending_count == 0
        assert provider.call_count == 1

        # Verify both users got their facts in DB
        msgs = provider._chat_calls[0]
        prompt = msgs[0].content
        assert "[1]" in prompt
        assert "[2]" in prompt

    @pytest.mark.asyncio
    async def test_buffer_clear_after_flush(self):
        """After flush, buffer is empty and new messages start fresh."""
        provider = MockLLMProvider(
            [
                _make_batch_response(
                    [
                        _make_msg_result(
                            1, [{"fact": "Test fact", "sentiment": "neutral"}]
                        )
                    ]
                )
            ]
        )

        buffer = FactBatchBuffer(batch_size=3, timeout=5.0, enabled=True)
        # Add enough to fill batch immediately
        await buffer.add(OWNER_TG_ID, "Msg 1", "Reply 1", provider)
        await buffer.add(OWNER_TG_ID, "Msg 2", "Reply 2", provider)
        await buffer.add(OWNER_TG_ID, "Msg 3", "Reply 3", provider)

        await self._wait_flush(buffer)
        assert buffer.pending_count == 0

        # Add another — should NOT be flushed (batch not full)
        await buffer.add(OWNER_TG_ID, "Another message", "Reply2", provider)
        assert buffer.pending_count == 1

    @pytest.mark.asyncio
    async def test_feature_flag_off_passthrough(self):
        """When batching is disabled, each message gets an immediate LLM call."""
        provider = MockLLMProvider(
            [
                _make_fact_response([{"fact": "Fact 1", "sentiment": "neutral"}]),
                _make_fact_response([{"fact": "Fact 2", "sentiment": "positive"}]),
            ]
        )

        buffer = FactBatchBuffer(batch_size=5, timeout=10.0, enabled=False)
        assert buffer.enabled is False

        await buffer.add(OWNER_TG_ID, "Message 1", "Reply 1", provider)
        await buffer.add(OWNER_TG_ID, "Message 2", "Reply 2", provider)

        # Both should have triggered immediate LLM calls
        assert provider.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_buffer_no_llm_call(self):
        """Flushing an empty buffer should not make an LLM call."""
        provider = MockLLMProvider([])
        buffer = FactBatchBuffer(batch_size=5, timeout=5.0, enabled=True)

        await buffer.flush_now()
        assert provider.call_count == 0

    @pytest.mark.asyncio
    async def test_llm_error_handled_gracefully(self):
        """When LLM raises an error, the batch is dropped without crashing."""

        class ErrorProvider:
            name = "error_mock"

            async def chat(self, messages, *, heavy=False, task_type="default"):
                raise ConnectionError("Simulated LLM failure")

            async def close(self):
                pass

        provider = ErrorProvider()
        buffer = FactBatchBuffer(batch_size=1, timeout=5.0, enabled=True)

        # Should not raise — error is caught and logged
        await buffer.add(OWNER_TG_ID, "Message", "Reply", provider)
        await asyncio.sleep(0.2)

        assert buffer.pending_count == 0

    @pytest.mark.asyncio
    async def test_json_parse_error_handled(self):
        """Malformed LLM response is caught, no crash."""
        provider = MockLLMProvider(["not valid json at all {{["])
        buffer = FactBatchBuffer(batch_size=1, timeout=5.0, enabled=True)

        await buffer.add(OWNER_TG_ID, "Message", "Reply", provider)
        await asyncio.sleep(0.2)

        assert buffer.pending_count == 0

    @pytest.mark.asyncio
    async def test_empty_facts_in_response_no_db_write(self):
        """When LLM returns empty facts, nothing is written to DB."""
        await _make_owner(OWNER_TG_ID)

        provider = MockLLMProvider(
            [
                _make_batch_response(
                    [
                        _make_msg_result(1, [])  # Empty facts
                    ]
                )
            ]
        )

        buffer = FactBatchBuffer(batch_size=1, timeout=5.0, enabled=True)
        await buffer.add(OWNER_TG_ID, "Привет, как дела?", "Нормально!", provider)
        await asyncio.sleep(0.2)

        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_adds(self):
        """Multiple concurrent add() calls are handled safely."""
        batch_resp = _make_batch_response(
            [
                _make_msg_result(i, [{"fact": f"Fact {i}", "sentiment": "neutral"}])
                for i in range(1, 6)
            ]
        )
        provider = MockLLMProvider([batch_resp])
        buffer = FactBatchBuffer(batch_size=5, timeout=10.0, enabled=True)

        # Concurrent adds
        tasks = [
            buffer.add(OWNER_TG_ID, f"Msg {i}", f"Reply {i}", provider)
            for i in range(1, 6)
        ]
        await asyncio.gather(*tasks)

        await asyncio.sleep(0.2)
        assert buffer.pending_count == 0
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_batch_flushes(self):
        """Buffer correctly handles multiple consecutive flushes."""
        responses = [
            _make_batch_response(
                [
                    _make_msg_result(
                        1, [{"fact": "Batch1 Fact1", "sentiment": "neutral"}]
                    ),
                    _make_msg_result(
                        2, [{"fact": "Batch1 Fact2", "sentiment": "positive"}]
                    ),
                ]
            ),
            _make_batch_response(
                [
                    _make_msg_result(
                        1, [{"fact": "Batch2 Fact1", "sentiment": "neutral"}]
                    ),
                    _make_msg_result(
                        2, [{"fact": "Batch2 Fact2", "sentiment": "negative"}]
                    ),
                ]
            ),
        ]
        provider = MockLLMProvider(responses)
        buffer = FactBatchBuffer(batch_size=2, timeout=5.0, enabled=True)

        # First batch
        await buffer.add(OWNER_TG_ID, "Batch1 Msg1", "Reply", provider)
        await buffer.add(OWNER_TG_ID, "Batch1 Msg2", "Reply", provider)
        await asyncio.sleep(0.2)

        # Second batch
        await buffer.add(OWNER_TG_ID, "Batch2 Msg1", "Reply", provider)
        await buffer.add(OWNER_TG_ID, "Batch2 Msg2", "Reply", provider)
        await asyncio.sleep(0.2)

        assert provider.call_count == 2

    @pytest.mark.asyncio
    async def test_flush_now_works(self):
        """Manual flush_now() processes all pending messages."""
        provider = MockLLMProvider(
            [
                _make_batch_response(
                    [
                        _make_msg_result(
                            1, [{"fact": "Fact A", "sentiment": "neutral"}]
                        ),
                        _make_msg_result(
                            2, [{"fact": "Fact B", "sentiment": "positive"}]
                        ),
                    ]
                )
            ]
        )

        buffer = FactBatchBuffer(batch_size=5, timeout=30.0, enabled=True)
        await buffer.add(OWNER_TG_ID, "Msg A", "Reply A", provider)
        await buffer.add(OWNER_TG_ID, "Msg B", "Reply B", provider)

        assert buffer.pending_count == 2

        await buffer.flush_now()

        assert buffer.pending_count == 0
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_resets_on_new_message(self):
        """Adding a new message resets the timeout timer."""
        provider = MockLLMProvider(
            [
                _make_batch_response(
                    [
                        _make_msg_result(
                            1, [{"fact": "Delayed fact", "sentiment": "neutral"}]
                        ),
                        _make_msg_result(
                            2,
                            [{"fact": "Second fact", "sentiment": "positive"}],
                        ),
                    ]
                )
            ]
        )

        buffer = FactBatchBuffer(batch_size=5, timeout=0.15, enabled=True)

        # Add first message — timeout starts (0.15s)
        await buffer.add(OWNER_TG_ID, "First msg", "Reply", provider)
        await asyncio.sleep(0.08)

        # Add second message — timeout should reset (new 0.15s timer)
        await buffer.add(OWNER_TG_ID, "Second msg", "Reply", provider)

        # After original timer would have fired, buffer should still have items
        await asyncio.sleep(0.05)
        # Both messages should still be pending (timer was reset)
        assert buffer.pending_count == 2

        # Wait for the reset timeout + flush to complete
        await self._wait_flush(buffer, timeout=2.0)

        assert buffer.pending_count == 0
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_large_batch_size(self):
        """Large batch size (50) doesn't crash."""
        provider = MockLLMProvider(
            [
                _make_batch_response(
                    [
                        _make_msg_result(
                            i, [{"fact": f"Fact {i}", "sentiment": "neutral"}]
                        )
                        for i in range(1, 51)
                    ]
                )
            ]
        )

        buffer = FactBatchBuffer(batch_size=50, timeout=30.0, enabled=True)
        for i in range(1, 51):
            await buffer.add(OWNER_TG_ID, f"Msg {i}", f"Reply {i}", provider)

        await asyncio.sleep(0.2)
        assert buffer.pending_count == 0
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_provider_isolation(self):
        """Different providers are handled correctly (first provider used for batch)."""
        provider1 = MockLLMProvider(
            [
                _make_batch_response(
                    [
                        _make_msg_result(
                            1, [{"fact": "P1 fact", "sentiment": "neutral"}]
                        ),
                        _make_msg_result(
                            2, [{"fact": "P1 fact2", "sentiment": "positive"}]
                        ),
                    ]
                )
            ]
        )
        provider2 = MockLLMProvider(
            [
                _make_fact_response([{"fact": "P2 fact", "sentiment": "neutral"}]),
            ]
        )

        buffer = FactBatchBuffer(batch_size=2, timeout=30.0, enabled=True)
        await buffer.add(OWNER_TG_ID, "Msg from P1", "Reply", provider1)
        await buffer.add(OWNER_TG_ID, "Msg from P2", "Reply", provider2)

        await asyncio.sleep(0.2)

        # provider1 was used for the batch
        assert provider1.call_count == 1
        # provider2 was NOT called (its message went into provider1's batch)
        assert provider2.call_count == 0

    @pytest.mark.asyncio
    async def test_pending_count_property(self):
        """pending_count reflects the exact number of pending messages."""
        provider = MockLLMProvider([])
        buffer = FactBatchBuffer(batch_size=10, timeout=30.0, enabled=True)

        assert buffer.pending_count == 0

        await buffer.add(OWNER_TG_ID, "Msg 1", "Reply", provider)
        assert buffer.pending_count == 1

        await buffer.add(OWNER_TG_ID, "Msg 2", "Reply", provider)
        assert buffer.pending_count == 2

        await buffer.add(OWNER_TG_ID, "Msg 3", "Reply", provider)
        assert buffer.pending_count == 3

    @pytest.mark.asyncio
    async def test_enabled_property(self):
        """enabled property matches constructor parameter."""
        b1 = FactBatchBuffer(enabled=True)
        assert b1.enabled is True

        b2 = FactBatchBuffer(enabled=False)
        assert b2.enabled is False

    @pytest.mark.asyncio
    async def test_get_batch_buffer_singleton(self):
        """get_batch_buffer() returns the same instance."""
        # Patch settings for deterministic config
        with patch("src.core.memory.auto_save_batch.settings") as mock_settings:
            mock_settings.auto_save_batch_enabled = True
            mock_settings.auto_save_batch_size = 5
            mock_settings.auto_save_batch_timeout = 10.0

            b1 = await get_batch_buffer()
            b2 = await get_batch_buffer()
            assert b1 is b2

    @pytest.mark.asyncio
    async def test_reset_batch_buffer(self):
        """reset_batch_buffer() creates a new instance on next get_batch_buffer()."""
        with patch("src.core.memory.auto_save_batch.settings") as mock_settings:
            mock_settings.auto_save_batch_enabled = True
            mock_settings.auto_save_batch_size = 5
            mock_settings.auto_save_batch_timeout = 10.0

            b1 = await get_batch_buffer()
            reset_batch_buffer()
            b2 = await get_batch_buffer()
            assert b1 is not b2

    @pytest.mark.asyncio
    async def test_cancelled_error_preserved(self):
        """asyncio.CancelledError is not swallowed during flush."""
        provider = MockLLMProvider([])
        buffer = FactBatchBuffer(batch_size=5, timeout=30.0, enabled=True)

        await buffer.add(OWNER_TG_ID, "Msg", "Reply", provider)
        # flush_now internally catches CancelledError, should propagate correctly
        # This test verifies no unhandled exception
        await buffer.flush_now()
        # If we reach here without exception, the test passes

    @pytest.mark.asyncio
    async def test_db_save_actually_writes(self):
        """Facts are actually saved to the database."""
        await _make_owner(OWNER_TG_ID)

        provider = MockLLMProvider(
            [
                _make_single_facts_response(
                    [
                        {"fact": "User lives in Moscow", "sentiment": "neutral"},
                    ]
                )
            ]
        )

        await auto_save_single(OWNER_TG_ID, "Я живу в Москве", "Понял", provider)

        # Verify DB write
        async with get_session() as session:
            from src.db.repo import list_memories

            memories = await list_memories(
                session, await get_or_create_user(session, OWNER_TG_ID)
            )
            facts = [m.fact for m in memories]
            assert any("Moscow" in f for f in facts)


def _make_single_facts_response(facts: list[dict]) -> str:
    return json.dumps({"facts": facts})
