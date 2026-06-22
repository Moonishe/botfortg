import asyncio
import atexit
import functools
import logging
from collections.abc import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx
from google import genai
from google.genai import errors as genai_errors

from src.core.security.ssrf_guard import validate_base_url as _validate_base_url
from src.llm.base import ChatMessage
from src.llm.base_provider import BaseLLMProvider


logger = logging.getLogger(__name__)

GEMINI_CHAT_LIGHT = "gemini-2.0-flash"
GEMINI_CHAT_HEAVY = "gemini-2.5-pro"

_GEMINI_REQUEST_TIMEOUT = 90.0  # секунд — таймаут синхронного вызова Gemini API

# ponytail: shared executor avoids creating a fresh OS thread per token / per call.
# Was: asyncio.to_thread() per token → 500 threads per streaming response.
# 4 workers is enough: Gemini calls are I/O-bound (network wait), not CPU-bound.
_GEMINI_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini")
atexit.register(_GEMINI_EXECUTOR.shutdown, wait=True)
# ponytail: atexit ensures ThreadPoolExecutor threads (non-daemon) don't block
# process exit. wait=True completes in-flight Gemini calls before shutdown.


async def _run_in_executor(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run sync func in shared Gemini executor (avoids asyncio.to_thread thread-per-call)."""
    if func is None:
        raise TypeError("_run_in_executor requires a callable, got None")
    loop = asyncio.get_running_loop()
    # ponytail: partial avoids kwargs explosion and keeps executor API simple.
    # asyncio.to_thread accepted **kwargs but run_in_executor doesn't forward them.
    if kwargs:
        func = functools.partial(func, **kwargs)
    return await loop.run_in_executor(_GEMINI_EXECUTOR, func, *args)


def _to_gemini_contents(messages: list[ChatMessage]) -> tuple[str | None, list[dict]]:
    """Возвращает (system_instruction, contents) для google-genai."""
    system_chunks: list[str] = []
    contents: list[dict] = []
    for m in messages:
        if m.role == "system":
            system_chunks.append(m.content)
        elif m.role == "tool":
            # Gemini SDK requires tool results as functionResponse parts.
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": m.name or "unknown",
                                "response": {"output": m.content},
                            }
                        }
                    ],
                }
            )
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

        return await _run_in_executor(_check)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
        max_tokens: int | None = None,
    ) -> str:
        from google.genai import types as genai_types

        model = self._resolve_model(heavy)
        system, contents = _to_gemini_contents(messages)

        config_kwargs: dict = {}
        if system:
            config_kwargs["system_instruction"] = system
        if max_tokens is not None and max_tokens > 0:
            config_kwargs["max_output_tokens"] = max_tokens
        config = (
            genai_types.GenerateContentConfig(**config_kwargs)
            if config_kwargs
            else None
        )

        def _call() -> str:
            resp = self._client.models.generate_content(
                model=model,
                contents=contents,  # type: ignore[arg-type]
                config=config,
            )
            return resp.text or ""

        return await asyncio.wait_for(
            _run_in_executor(_call), timeout=_GEMINI_REQUEST_TIMEOUT
        )

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool = False,
        task_type: str = "default",
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str]:
        """Stream chat output token by token using Gemini's streaming API."""
        import queue as sync_queue

        model = self._resolve_model(heavy)
        system, contents = _to_gemini_contents(messages)
        config: dict | None = {}
        if system:
            config["system_instruction"] = system
        if max_tokens is not None and max_tokens > 0:
            config["max_output_tokens"] = max_tokens
        if not config:
            config = None

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
        # DR2 fix: use shared _GEMINI_EXECUTOR instead of spawning a fresh
        # Thread per call (was: 100+ concurrent streams → 100+ OS threads).
        token_queue: sync_queue.Queue = sync_queue.Queue()
        stream_future = None  # Future for tracking the submitted streaming task

        def _stream_sync() -> None:
            try:
                # ponytail: reuse same config as async path (was: only system_instruction,
                # silently dropping max_tokens → responses could be truncated/overlong)
                _config: dict | None = {}
                if system:
                    _config["system_instruction"] = system
                if max_tokens is not None and max_tokens > 0:
                    _config["max_output_tokens"] = max_tokens
                if not _config:
                    _config = None
                for chunk in self._client.models.generate_content_stream(  # type: ignore[arg-type]
                    model=model,
                    contents=contents,  # type: ignore[arg-type]
                    config=_config,  # type: ignore[arg-type]
                ):
                    if chunk.text:
                        token_queue.put(chunk.text)
            except Exception as exc:
                token_queue.put(exc)
            finally:
                token_queue.put(None)  # sentinel

        # Submit streaming work to shared executor (4 workers)
        loop = asyncio.get_running_loop()
        stream_future = loop.run_in_executor(_GEMINI_EXECUTOR, _stream_sync)

        try:
            while True:
                # 60s timeout per token — prevents indefinite hang on stalled connection
                try:
                    item = await asyncio.wait_for(
                        _run_in_executor(token_queue.get, timeout=60), timeout=65
                    )
                except sync_queue.Empty:
                    raise TimeoutError("Gemini stream token queue timed out") from None
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            # DR2: ensure the streaming task completes (waits via future, not thread.join)
            # ponytail: future.result() will block if _stream_sync is still running,
            # so we wait with a short timeout to avoid hanging the event loop.
            if not stream_future.done():
                try:
                    await asyncio.wait_for(
                        asyncio.wrap_future(stream_future), timeout=5
                    )
                except TimeoutError:
                    logger.debug(
                        "Stream task did not finish in 5s — daemon executor will clean up"
                    )

    async def embed(self, text: str) -> list[float]:
        from src.core.actions.embedding_cache import aget, aset

        embed_model = self._embed_model
        if embed_model is None:
            raise ValueError("GeminiProvider embed_model is not configured")
        cached = await aget(text, embed_model)
        if cached is not None:
            return cached

        def _call() -> list[float]:
            resp = self._client.models.embed_content(
                model=embed_model,
                contents=text,
            )
            if not resp.embeddings:
                raise ValueError("Gemini API returned no embeddings")
            vals = resp.embeddings[0].values
            if vals is None:
                raise ValueError("Gemini API returned no embedding values")
            return list(vals)

        result = await asyncio.wait_for(
            _run_in_executor(_call), timeout=_GEMINI_REQUEST_TIMEOUT
        )
        await aset(text, result, embed_model)
        return result

    async def list_models(self) -> list[str]:
        def _list() -> list[str]:
            return [m.name for m in self._client.models.list()]  # type: ignore[return-value]

        return await _run_in_executor(_list)

    async def close(self) -> None:
        if hasattr(self._client, "close"):
            await _run_in_executor(self._client.close)  # type: ignore[arg-type]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        from src.core.actions.embedding_cache import aget, aset

        if not texts:
            return []
        embed_model = self._embed_model
        if embed_model is None:
            raise ValueError("GeminiProvider embed_model is not configured")

        # Проверяем кэш — собираем только некэшированные тексты
        results: list[list[float] | None] = [None] * len(texts)
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []
        for i, t in enumerate(texts):
            cached = await aget(t, embed_model)
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

                # ponytail: explicit default-arg avoids late-binding closure trap
                # (was: def _call(chunk=chunk) — same effect, less readable)
                def _call(c: list[str] = chunk) -> list[list[float]]:
                    resp = self._client.models.embed_content(
                        model=embed_model,
                        contents=c,  # type: ignore[arg-type]
                    )
                    if not resp.embeddings:
                        raise ValueError("Gemini API returned no embeddings")
                    result: list[list[float]] = []
                    for e in resp.embeddings:
                        vals = e.values
                        if vals is None:
                            raise ValueError("Gemini API returned no embedding values")
                        result.append(list(vals))
                    return result

                api_results.extend(
                    await asyncio.wait_for(
                        _run_in_executor(_call), timeout=_GEMINI_REQUEST_TIMEOUT
                    )
                )

            for idx, emb in zip(uncached_indices, api_results, strict=True):
                await aset(texts[idx], emb, embed_model)
                results[idx] = emb

        return results  # type: ignore[return-value]
