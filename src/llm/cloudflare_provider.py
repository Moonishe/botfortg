import httpx
from openai import (
    APIConnectionError,
    AsyncOpenAI,
    AuthenticationError,
    PermissionDeniedError,
)

from src.config import LLMDefaults, settings
from src.llm.base import ChatMessage


class CloudflareProvider:
    """Cloudflare Workers AI провайдер (OpenAI-совместимый API).

    Использует AsyncOpenAI с кастомным base_url на Cloudflare Accounts AI Gateway.
    Поддерживает chat (Kimi K2.6, Qwen3) и embeddings (BGE-M3).
    """

    name = "cloudflare"

    def __init__(self, api_key: str) -> None:
        account_id = settings.cloudflare_account_id
        if not account_id:
            raise ValueError(
                "CLOUDFLARE_ACCOUNT_ID не задан в .env. "
                "Добавь: CLOUDFLARE_ACCOUNT_ID=6d879f0f99a8baf94562a3dbd10c10be"
            )
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    async def validate_key(self) -> bool:
        try:
            await self._client.models.list()
            return True
        except AuthenticationError:
            return False  # invalid/revoked key
        except PermissionDeniedError:
            return False  # key lacks permission
        except APIConnectionError:
            raise  # network issue (timeout, connection refused) — let caller retry
        except Exception:
            return False  # unknown error — assume invalid

    async def chat(self, messages: list[ChatMessage], *, heavy: bool = False) -> str:
        model = (
            LLMDefaults.CLOUDFLARE_CHAT_HEAVY
            if heavy
            else LLMDefaults.CLOUDFLARE_CHAT_LIGHT
        )
        resp = await self._client.chat.completions.create(
            model=model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        return resp.choices[0].message.content or ""

    async def embed(self, text: str) -> list[float]:
        from src.core.actions.embedding_cache import get as cache_get, set as cache_set

        cached = cache_get(text, LLMDefaults.CLOUDFLARE_EMBED)
        if cached is not None:
            return cached
        resp = await self._client.embeddings.create(
            model=LLMDefaults.CLOUDFLARE_EMBED, input=text
        )
        result = resp.data[0].embedding
        cache_set(text, result, LLMDefaults.CLOUDFLARE_EMBED)
        return result

    async def close(self) -> None:
        await self._client.close()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        from src.core.actions.embedding_cache import get as cache_get, set as cache_set

        if not texts:
            return []

        # Проверяем кэш — собираем только некэшированные тексты
        results: list[list[float] | None] = [None] * len(texts)
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []
        for i, t in enumerate(texts):
            cached = cache_get(t, LLMDefaults.CLOUDFLARE_EMBED)
            if cached is not None:
                results[i] = cached
            else:
                uncached_texts.append(t)
                uncached_indices.append(i)

        if uncached_texts:
            resp = await self._client.embeddings.create(
                model=LLMDefaults.CLOUDFLARE_EMBED, input=uncached_texts
            )
            api_results = [d.embedding for d in resp.data]
            for idx, emb in zip(uncached_indices, api_results):
                cache_set(texts[idx], emb, LLMDefaults.CLOUDFLARE_EMBED)
                results[idx] = emb

        return results  # type: ignore[return-value]
