import asyncio
import threading
from collections.abc import AsyncGenerator

import httpx
from google import genai
from google.genai import errors as genai_errors

from src.llm.base_provider import BaseLLMProvider
from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage
import logging


logger = logging.getLogger(__name__)

GEMINI_CHAT_LIGHT = "gemini-3-flash"
GEMINI_CHAT_HEAVY = "gemini-3.1-pro"

_GEMINI_REQUEST_TIMEOUT = 90.0  # секунд — таймаут синхронного вызова Gemini API


def _to_gemini_contents(messages: list[ChatMessage]) -> tuple[str | None, list[dict]]:
    """Возвращает (system_instruction, contents) для google-genai."""
    system_chunks: list[str] = []
    contents: list[dict] = []
    for m in messages:
        if m.role == "system":
            system_chunks.append(m.content)
        else:
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
    system = "\n\n".join(system_chunks) if system_chunks else None
    return system, contents


class GeminiProvider(BaseLLMProvider):
    name = "gemini"
    _LIGHT_MODEL = GEMINI_CHAT_LIGHT
    _HEAVY_MODEL = GEMINI_CHAT_HEAVY

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        model: str | None = None,
        embed_model: str | None = None,
    ) -> None:
        if base_url:
            raise ValueError(
                "GeminiProvider does not support custom base_url. "
                "Use the native Google API endpoint."
            )
        _validate_base_url(
            base_url
        )  # defense-in-depth: guards against non-None custom URLs
        self._client = genai.Client(api_key=api_key, http_options={"timeout": 60000})
        super().__init__(api_key=api_key, model=model, embed_model=embed_model)

    async def validate_key(self) -> bool:
        def _check() -> bool:
            try:
                # пагинированный итератор; первый элемент достаточен
                next(iter(self._client.models.list()))
                return True
            except genai_errors.ClientError as e:
                if e.code in (401, 403):
                    return False  # invalid/revoked key or permission denied
                raise  # other client errors (429, etc.) — let caller retry
            except (httpx.TimeoutException, httpx.ConnectError):
                raise  # network issue — let caller retry
            except Exception:
                return False  # unknown error — assume invalid

        return await asyncio.to_thread(_check)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
    ) -> str:
        model = self._resolve_model(heavy)
        system, contents = _to_gemini_contents(messages)

        def _call() -> str:
            config = {"system_instruction": system} if system else None
            resp = self._client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            return resp.text or ""

        return await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=_GEMINI_REQUEST_TIMEOUT
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
    ) -> AsyncGenerator[str]:
        """Stream chat output token by token using Gemini's streaming API."""
        import queue as sync_queue

        model = self._resolve_model(heavy)
        system, contents = _to_gemini_contents(messages)
        config = {"system_instruction": system} if system else None

        # Try async client first (available in google-genai >= 1.0)
        aio_client = getattr(self._client, "aio", None)
        if aio_client is not None:
            try:
                stream = await aio_client.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=config,
                )
                async for chunk in stream:
                    if chunk.text:
                        yield chunk.text
                return
            except Exception:
                # fall through to thread-based streaming
                logger.debug("Non-critical error", exc_info=True)

        # Thread-based streaming fallback for sync-only client
        token_queue: sync_queue.Queue = sync_queue.Queue()

        def _stream_sync() -> None:
            try:
                _config = {"system_instruction": system} if system else None
                for chunk in self._client.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=_config,
                ):
                    if chunk.text:
                        token_queue.put(chunk.text)
            except Exception as exc:
                token_queue.put(exc)
            finally:
                token_queue.put(None)  # sentinel

        thread = threading.Thread(target=_stream_sync, daemon=True)
        thread.start()

        while True:
            item = await asyncio.to_thread(token_queue.get)
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    async def embed(self, text: str) -> list[float]:
        from src.core.actions.embedding_cache import aget, aset

        cached = await aget(text, self._embed_model)
        if cached is not None:
            return cached

        def _call() -> list[float]:
            resp = self._client.models.embed_content(
                model=self._embed_model,
                contents=text,
            )
            return list(resp.embeddings[0].values)

        result = await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=_GEMINI_REQUEST_TIMEOUT
        )
        await aset(text, result, self._embed_model)
        return result

    async def list_models(self) -> list[str]:
        def _list() -> list[str]:
            return [m.name for m in self._client.models.list()]

        return await asyncio.to_thread(_list)

    async def close(self) -> None:
        if hasattr(self._client, "close"):
            await asyncio.to_thread(self._client.close)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        from src.core.actions.embedding_cache import aget, aset

        if not texts:
            return []

        # Проверяем кэш — собираем только некэшированные тексты
        results: list[list[float] | None] = [None] * len(texts)
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []
        for i, t in enumerate(texts):
            cached = await aget(t, self._embed_model)
            if cached is not None:
                results[i] = cached
            else:
                uncached_texts.append(t)
                uncached_indices.append(i)

        if uncached_texts:
            # Gemini поддерживает до 100 текстов за вызов — разбиваем на чанки
            api_results: list[list[float]] = []
            chunk_size = 100
            for start in range(0, len(uncached_texts), chunk_size):
                chunk = uncached_texts[start : start + chunk_size]

                def _call(chunk: list[str] = chunk) -> list[list[float]]:
                    resp = self._client.models.embed_content(
                        model=self._embed_model,
                        contents=chunk,
                    )
                    return [list(e.values) for e in resp.embeddings]

                api_results.extend(
                    await asyncio.wait_for(
                        asyncio.to_thread(_call), timeout=_GEMINI_REQUEST_TIMEOUT
                    )
                )

            for idx, emb in zip(uncached_indices, api_results, strict=True):
                await aset(texts[idx], emb, self._embed_model)
                results[idx] = emb

        return results  # type: ignore[return-value]
