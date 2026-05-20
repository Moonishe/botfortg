import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.repo import get_api_keys
from src.llm.base import LLMProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.mistral_provider import MistralProvider
from src.llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


# ─── MultiKey: обёртка для ротации ключей ─────────────────────────────


class MultiKeyProvider:
    """Обёртка: ротирует ключи провайдера при ошибке 429/503/500.

    Позволяет указать несколько API-ключей для одного LLM-провайдера.
    При получении ошибки пропускной способности (rate limit, capacity exceeded)
    автоматически переключается на следующий ключ.
    """

    def __init__(self, provider_class: type, keys: list[str], **kwargs: object) -> None:
        self._provider_class = provider_class
        self._keys = keys
        self._kwargs = kwargs
        self._idx = 0
        self._lock = asyncio.Lock()
        # ключ → timestamp ошибки (для временного бана на 60 сек)
        self._failed_keys: dict[str, float] = {}
        self.name = f"{self._provider_class.__name__}(×{len(self._keys)})"

    def _current_key(self) -> str:
        return self._keys[self._idx % len(self._keys)]

    async def _rotate(self) -> None:
        """Переключает на следующий ключ."""
        self._idx = (self._idx + 1) % len(self._keys)

    async def _try_with_retry(self, operation, *args: object, **kwargs: object):
        """Пробует операцию со всеми ключами по очереди.

        Пропускает ключи, которые фейлились менее 60 секунд назад.
        При успехе обновляет активный индекс.
        """
        last_error: Exception | None = None
        now = asyncio.get_event_loop().time()
        for attempt in range(len(self._keys)):
            key = self._keys[(self._idx + attempt) % len(self._keys)]
            # Пропускаем ключи, которые фейлились <60 сек назад
            if key in self._failed_keys and now - self._failed_keys[key] < 60:
                continue
            try:
                provider = self._provider_class(key, **self._kwargs)
                result = await operation(provider, *args, **kwargs)
                # Успех — переключаем активный индекс на этот ключ
                self._idx = (self._idx + attempt) % len(self._keys)
                return result
            except Exception as e:
                err_str = str(e).lower()
                # 429, 503, 500, capacity exceeded — ротируем
                if any(
                    x in err_str
                    for x in (
                        "429",
                        "503",
                        "500",
                        "capacity",
                        "rate limit",
                        "quota",
                        "overloaded",
                    )
                ):
                    self._failed_keys[key] = asyncio.get_event_loop().time()
                    last_error = e
                    continue
                raise  # остальные ошибки не ротируем
        raise last_error or RuntimeError("All API keys failed")

    async def chat(self, messages, *, heavy: bool = False) -> str:
        return await self._try_with_retry(lambda p: p.chat(messages, heavy=heavy))

    async def embed(self, text: str) -> list[float]:
        return await self._try_with_retry(lambda p: p.embed(text))

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await self._try_with_retry(lambda p: p.embed_batch(texts))

    async def validate_key(self) -> bool:
        try:
            return await self._try_with_retry(lambda p: p.validate_key())
        except Exception:
            return False


# ─── Хелперы ──────────────────────────────────────────────────────────


def _provider_class_for(name: str) -> type:
    """Маппинг имени провайдера → класс."""
    return {
        "openai": OpenAIProvider,
        "gemini": GeminiProvider,
        "mistral": MistralProvider,
    }[name]


def _single_provider(name: str, key: str) -> LLMProvider:
    """Создаёт экземпляр провайдера для одного ключа."""
    return _provider_class_for(name)(key)


async def build_provider(session: AsyncSession, user: User) -> LLMProvider | None:
    """Создаёт провайдера согласно настройкам пользователя.

    Если у провайдера один ключ — возвращается обычный Provider.
    Если несколько — обёртка MultiKeyProvider с ротацией.
    None — если ключ не задан.
    """
    provider_name = user.settings.llm_provider if user.settings else "openai"
    keys = await get_api_keys(session, user, provider_name)
    if not keys:
        return None
    if len(keys) == 1:
        # Один ключ — без обёртки (без оверхеда)
        return _single_provider(provider_name, keys[0])
    logger.info("MultiKey ротация для %s: %d ключей", provider_name, len(keys))
    return MultiKeyProvider(
        _provider_class_for(provider_name),
        keys,
    )
