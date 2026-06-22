"""Anthropic provider — Claude via Messages API."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from src.llm.base_provider import BaseLLMProvider
from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage

logger = logging.getLogger(__name__)


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude provider via Messages API.

    Unlike OpenAI, Anthropic uses a different API structure:
    - System prompt is top-level, not a message
    - Messages are list[{"role": "user"|"assistant", "content": [...]}]
    - Supports streaming via server-sent events
    - Models: claude-3-5-sonnet, claude-3-5-haiku, claude-3-opus

    Models are hardcoded to match the Anthropic catalog from provider_catalog.py.
    """

    name = "anthropic"
    _LIGHT_MODEL = "claude-3-5-haiku-20241022"
    _HEAVY_MODEL = "claude-3-5-sonnet-20241022"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        import anthropic
        import httpx

        base_url = _validate_base_url(base_url)
        kwargs: dict = {
            "api_key": api_key,
            "max_retries": 2,
            "timeout": httpx.Timeout(60.0, connect=10.0),
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client: anthropic.AsyncAnthropic = anthropic.AsyncAnthropic(**kwargs)
        super().__init__(api_key=api_key, model=model, embed_model=embed_model)

    async def validate_key(self) -> bool:
        import anthropic
        import httpx

        try:
            await self._client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=1,
                messages=[{"role": "user", "content": "hi"}],
            )
            return True
        except anthropic.AuthenticationError:
            return False
        except anthropic.PermissionDeniedError:
            return False
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            anthropic.APIConnectionError,
        ):
            raise  # transient — don't mark key as invalid
        except Exception:
            logger.debug("Anthropic validate_key unexpected error", exc_info=True)
            return False

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
        max_tokens: int | None = None,
    ) -> str:
        # Anthropic prompt caching: system + last user message get cache_control
        # breakpoints. Cache TTL is 5 minutes (ephemeral). Saves ~90% on repeated
        # system prompts. See: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
        system, anthropic_messages = self._convert_messages(messages)
        model = self._resolve_model(heavy)
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens if max_tokens is not None else 4096,
            "messages": anthropic_messages,
        }
        if system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        # Add cache breakpoint to last user message for rolling window cache
        if anthropic_messages:
            last_msg = anthropic_messages[-1]
            if last_msg.get("role") == "user" and isinstance(
                last_msg.get("content"), str
            ):
                anthropic_messages[-1] = {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": last_msg["content"],
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
        resp = await self._client.messages.create(**kwargs)
        # Anthropic returns content as list of blocks
        for block in resp.content or []:
            if hasattr(block, "text"):
                return block.text
        return ""

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str]:
        # Anthropic prompt caching: system + last user message get cache_control
        # breakpoints. See chat() for full explanation.
        system, anthropic_messages = self._convert_messages(messages)
        model = self._resolve_model(heavy)
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens if max_tokens is not None else 4096,
            "messages": anthropic_messages,
        }
        if system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        # Add cache breakpoint to last user message for rolling window cache
        if anthropic_messages:
            last_msg = anthropic_messages[-1]
            if last_msg.get("role") == "user" and isinstance(
                last_msg.get("content"), str
            ):
                anthropic_messages[-1] = {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": last_msg["content"],
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and hasattr(event.delta, "text"):
                    yield event.delta.text

    async def embed(self, text: str) -> list[float]:
        # NOTE: Not all providers support embedding/model listing. Router handles this via try/except.
        raise NotImplementedError("Anthropic does not support embeddings")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # NOTE: Not all providers support embedding/model listing. Router handles this via try/except.
        raise NotImplementedError("Anthropic does not support embeddings")

    async def list_models(self) -> list[str]:
        # NOTE: Not all providers support embedding/model listing. Router handles this via try/except.
        raise NotImplementedError("Anthropic does not expose model listing API")

    async def close(self) -> None:
        if hasattr(self._client, "close"):
            await self._client.close()

    def _convert_messages(
        self, messages: list[ChatMessage]
    ) -> tuple[str | None, list[dict]]:
        """Convert ChatMessage list to Anthropic format.

        Returns (system_text, [{"role": "user"|"assistant", "content": str}])
        Anthropic requires: system is top-level, roles are only user/assistant.
        """
        system_parts: list[str] = []
        anthropic_msgs: list[dict] = []

        idx = 0
        while idx < len(messages):
            msg = messages[idx]
            role = msg.role
            if role == "system":
                system_parts.append(msg.content)
                idx += 1
            elif role == "tool":
                # Group consecutive tool results into one Anthropic user message.
                tool_blocks: list[dict] = []
                while idx < len(messages) and messages[idx].role == "tool":
                    tool_msg = messages[idx]
                    tool_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_msg.tool_call_id or "unknown",
                            "content": tool_msg.content,
                        }
                    )
                    idx += 1
                anthropic_msgs.append({"role": "user", "content": tool_blocks})
            elif role in ("user", "assistant"):
                anthropic_msgs.append({"role": role, "content": msg.content})
                idx += 1
            else:
                # Unknown role -> treat as user
                anthropic_msgs.append({"role": "user", "content": msg.content})
                idx += 1

        system = "\n\n".join(system_parts) if system_parts else None
        return system, anthropic_msgs
