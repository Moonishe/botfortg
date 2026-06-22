import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.infra.key_guard import safe_str
from src.llm.base import ChatMessage, TaskType
from src.llm.tool_calling.models import ChatResponse, ToolDefinition

# ── Импорты из выделенного provider_manager ────────────────────────────
from src.llm.provider_manager import (
    _check_key_circuit_breaker,
    _CircuitState,  # pyright: ignore[reportUnusedImport] — re-export for tests
    _KeyCircuitBreaker,  # pyright: ignore[reportUnusedImport] — re-export for tests
    _make_cache_key,
    _PURPOSE_SEMAPHORES,  # pyright: ignore[reportUnusedImport] — re-export for memory_admin_cmds
    _provider_class_for,  # pyright: ignore[reportUnusedImport] — re-export for keys_cmd + free_text_exec
    _record_key_failure,
    _record_key_success,
    _record_provider_success,  # pyright: ignore[reportUnusedImport] — re-export for tests
    _record_provider_failure,
    MAX_RETRIES_PER_KEY,
    RETRY_BASE_DELAY,
    acquire_purpose_slot,
    build_provider,  # pyright: ignore[reportUnusedImport] — re-export for 64 existing consumers
    cleanup_circuit_breakers,  # pyright: ignore[reportUnusedImport] — re-export for main.py cleanup loop
    ensure_locks_initialized,  # pyright: ignore[reportUnusedImport] — re-export for main.py
    release_purpose_slot,
)

logger = logging.getLogger(__name__)

# Sentinel to distinguish "heavy not passed" from "heavy=False".
# Used so that `_default_heavy` (from user's use_heavy_model setting)
# is respected when callers don't explicitly specify heavy/light.

# ── Account Usage Tracking helper ─────────────────────────────────────────


async def _track_llm_usage(
    provider_name: str,
    model: str | None,
    messages: list,
    result_text: str,
) -> None:
    """Estimate and record LLM usage after a successful chat call."""
    try:
        from src.core.context.token_tracker import estimate_tokens
        from src.core.observability.account_usage import get_tracker

        model_name = model or "unknown"
        input_text = ""
        for m in messages:
            content = (
                m.content
                if hasattr(m, "content")
                else m.get("content", "")
                if isinstance(m, dict)
                else str(m)
            )
            input_text += content + "\n"
        tokens_in = estimate_tokens(input_text)
        tokens_out = estimate_tokens(result_text)
        await get_tracker().record_usage(
            provider_name,
            model_name,
            tokens_in,
            tokens_out,
        )
    except Exception:
        logger.debug("Failed to record LLM usage", exc_info=True)


_UNSET = object()

# ── Module constants ─────────────────────────────────────────────────────
_DEFAULT_LLM_TIMEOUT = 90.0  # секунд — таймаут одного LLM-вызова (включая retries)
_DEFAULT_TOTAL_LLM_TIMEOUT = (
    300.0  # секунд — общий таймаут всего retry-loop (15 keys × 3 retries × 90s)
)


class ExhaustedError(Exception):
    """Все API-ключи провайдера исчерпаны (колдаун/отключены)."""


RETRYABLE_MARKERS = (
    "429",
    "500",
    "503",
    "capacity",
    "capacity exceeded",
    "service_tier_capacity_exceeded",
    "rate limit",
    "ratelimit",
    "resource_exhausted",
    "quota",
    "overloaded",
    "temporarily unavailable",
    "raw_status_code': 429",
    'raw_status_code": 429',
    # Cloudflare Workers AI async-модели (cold start, async queue)
    "async queue",
    "queued",
    "model is busy",
    "cold start",
    "workers ai",
    "cf-ray",
)


def _is_retryable_llm_error(exc: Exception) -> bool:
    """True for transient capacity/rate-limit/server errors worth trying
    another key/provider."""
    # Timeouts are always retryable — rotate key / fallback to next provider.
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    # httpx timeout exceptions (ReadTimeout, ConnectTimeout, etc.)
    if type(exc).__name__ in (
        "TimeoutException",
        "ReadTimeout",
        "ConnectTimeout",
        "WriteTimeout",
        "PoolTimeout",
    ):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in {429, 500, 503}:
        return True
    code = str(getattr(exc, "code", "") or "").lower()
    if code in {"429", "500", "503", "3505", "service_tier_capacity_exceeded"}:
        return True
    # NOTE: body намеренно не включено — полный ответ LLM может содержать
    # чувствительные данные пользователя (PII, секреты, содержимое диалога).
    text = f"{type(exc).__name__} {safe_str(exc)}".lower()
    return any(marker in text for marker in RETRYABLE_MARKERS)


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


# ─── MultiKey: обёртка для ротации ключей ─────────────────────────────


class MultiKeyProvider:
    """Обёртка: ротирует ключи провайдера при ошибке 429/503/500.

    Позволяет указать несколько API-ключей для одного LLM-провайдера.
    Round-robin распределяет параллельные вызовы по ключам,
    Semaphore(N) ограничивает число одновременных запросов.
    При получении ошибки пропускной способности (rate limit, capacity exceeded)
    автоматически переключается на следующий ключ.
    """

    def __init__(
        self,
        provider_name: str,
        provider_class: type,
        keys: list[str],
        slot_ids: list[int] | None = None,
        endpoints: list[str | None] | None = None,
        models: list[str | None] | list[list[str]] | None = None,
        embed_model: str | None = None,
        session_provider: Callable[[], tuple[AsyncSession | None, object]]
        | None = None,
        purpose: str = "main",
        **kwargs: object,
    ) -> None:
        if not keys:
            raise ValueError("MultiKeyProvider requires at least one key")
        self.provider_name = provider_name
        self._provider_class = provider_class
        self._keys = keys
        self._slot_ids = slot_ids or []
        self._endpoints = endpoints or []
        # Поддержка старого (list[str]) и нового (list[list[str]]) форматов
        self._models: list[list[str]] = (
            [[m] for m in models]
            if models and isinstance(models[0], str)
            else (models if models else [[] for _ in keys])  # type: ignore[arg-type]
        )
        self._embed_model = embed_model
        self._session_provider = session_provider
        self._kwargs = kwargs
        self._idx = 0
        self._idx_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(len(self._keys))
        self._providers: list[Any] = []
        self._providers_lock = asyncio.Lock()
        self._current_purpose = purpose
        self._model: str | None = None  # global override; None = use per-slot model
        self._default_heavy: bool = False  # overridden by use_heavy_model setting
        self.name = f"{provider_name}(×{len(self._keys)})"

    async def _reserve_start_idx(self) -> int:
        async with self._idx_lock:
            start_idx = self._idx
            self._idx = (self._idx + 1) % len(self._keys)
            return start_idx

    def _build_provider_kwargs(
        self, idx: int, model_override: str | None = None
    ) -> dict[str, Any]:
        """Build kwargs for a single raw provider instance."""
        provider_kwargs = dict(self._kwargs)
        if self._endpoints and idx < len(self._endpoints):
            endpoint = self._endpoints[idx]
            if endpoint:
                provider_kwargs["base_url"] = endpoint
        if model_override:
            provider_kwargs["model"] = model_override
        elif self._model:
            provider_kwargs["model"] = self._model
        elif self._models and idx < len(self._models):
            per_slot = self._models[idx]
            if per_slot and per_slot[0]:
                provider_kwargs["model"] = per_slot[0]
        if self._embed_model:
            provider_kwargs["embed_model"] = self._embed_model
        return provider_kwargs

    async def _try_with_retry(
        self,
        operation,
        *args: object,
        model_override: str | None = None,
        _skip_budget: bool = False,
        **kwargs: object,
    ):
        """Пробует операцию со всеми ключами по очереди.

        Пропускает ключи, которые фейлились менее 60 секунд назад.
        При успехе обновляет активный индекс и отмечает слот (DB).
        При временной ошибке помечает слот как упавший (DB cooldown).
        Записывает метрики для Adaptive Provider Selection.
        """
        start_time = asyncio.get_running_loop().time()
        last_error: Exception | None = None

        # ── Iteration Budget — prevent runaway LLM calls ──
        if not _skip_budget:
            from src.core.intelligence.iteration_budget import IterationBudget

            if not hasattr(self, "_llm_budget"):
                self._llm_budget = IterationBudget()
            if not self._llm_budget.record_llm_call():
                raise RuntimeError(
                    "LLM call budget exhausted — too many calls in window"
                )

        # ── Total timeout: prevent hours-long hangs on 15×3 retries ──
        async with asyncio.timeout(_DEFAULT_TOTAL_LLM_TIMEOUT):
            # Round-robin: reserve a unique start index for concurrent calls.
            start_idx = await self._reserve_start_idx()

            skipped = 0
            for attempt in range(len(self._keys)):
                idx = (start_idx + attempt) % len(self._keys)
                key = self._keys[idx]
                cache_key = _make_cache_key(
                    self.provider_name, self._slot_ids, idx, key
                )
                if not await _check_key_circuit_breaker(cache_key):
                    skipped += 1
                    continue
                # Create provider instance — handle creation failure separately
                try:
                    provider_kwargs = self._build_provider_kwargs(idx, model_override)
                    provider = self._provider_class(key, **provider_kwargs)
                    async with self._providers_lock:
                        self._providers.append(provider)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    continue

                try:
                    for retry in range(MAX_RETRIES_PER_KEY):
                        try:
                            result = await asyncio.wait_for(
                                operation(provider, *args, **kwargs),
                                timeout=_DEFAULT_LLM_TIMEOUT,
                            )
                        except Exception as exc:
                            if not _is_retryable_llm_error(exc):
                                raise
                            if retry < MAX_RETRIES_PER_KEY - 1:
                                delay = RETRY_BASE_DELAY * (2**retry)
                                logger.warning(
                                    "LLM %s key %s attempt %d/%d "
                                    "failed, retrying in %.1fs: %s",
                                    self.provider_name,
                                    _mask_key(key),
                                    retry + 1,
                                    MAX_RETRIES_PER_KEY,
                                    delay,
                                    safe_str(exc)[:200],
                                )
                                await asyncio.sleep(delay)
                            else:
                                raise
                        else:
                            await _record_key_success(
                                self.provider_name,
                                cache_key,
                                self._slot_ids,
                                idx,
                                start_time,
                            )
                            # _reserve_start_idx already advanced the round-robin index;
                            # no separate advance needed — avoids double-increment race.
                            return result
                except Exception as exc:
                    if _is_retryable_llm_error(exc):
                        # ── Error Classifier: more nuanced retry decision ──
                        from src.core.intelligence.error_classifier import (
                            classify_llm_error,
                            should_retry,
                        )

                        category = classify_llm_error(exc)
                        if not should_retry(category):
                            logger.info(
                                "LLM error category %r is not retryable — propagate",
                                category,
                            )
                            raise
                        logger.debug("LLM error category=%r — retrying", category)
                        await _record_key_failure(
                            self.provider_name, cache_key, self._slot_ids, idx, exc
                        )
                        last_error = exc
                        logger.warning(
                            "LLM %s key %s temporarily failed, rotating: %s",
                            self.provider_name,
                            _mask_key(key),
                            safe_str(exc)[:200],
                        )
                        continue
                    raise
                # ponytail: provider lifecycle left to caller; per-attempt close removed
                # to avoid repeated close/recreate overhead during key rotation.
            if last_error:
                try:
                    await _record_provider_failure(self.provider_name)
                except Exception:
                    logger.exception(
                        "Failed to record provider failure metric for %s",
                        self.provider_name,
                    )
                raise ExhaustedError(
                    f"Все {len(self._keys)} ключей {self.provider_name} недоступны "
                    f"(последняя ошибка: {last_error}, "
                    f"пропущено по кулдауну: {skipped})"
                )
            # Все ключи пропущены по кулдауну — ни один не был опробован
            if skipped != len(self._keys):
                logger.error(
                    "BUG: skipped=%d != total_keys=%d but last_error is None",
                    skipped,
                    len(self._keys),
                )
            try:
                await _record_provider_failure(self.provider_name)
            except Exception:
                logger.exception(
                    "Failed to record provider failure metric for %s",
                    self.provider_name,
                )
            raise ExhaustedError(
                f"Все {len(self._keys)} ключей {self.provider_name} в кулдауне"
            )

    async def chat(
        self,
        messages,
        *,
        heavy=_UNSET,
        task_type: str = TaskType.DEFAULT,
        max_tokens: int | None = None,
    ) -> str:
        sem = await acquire_purpose_slot(self._current_purpose)
        try:
            return await self._chat_with_retry(
                messages,
                heavy=heavy,
                task_type=task_type,
                max_tokens=max_tokens,
            )
        finally:
            release_purpose_slot(sem)

    async def _chat_with_retry(
        self,
        messages,
        *,
        heavy=_UNSET,
        task_type: str = TaskType.DEFAULT,
        max_tokens: int | None = None,
    ) -> str:
        await self._semaphore.acquire()
        try:
            return await self._retry_inner(
                messages,
                heavy=heavy,
                task_type=task_type,
                max_tokens=max_tokens,
            )
        finally:
            self._semaphore.release()

    async def _retry_inner(
        self,
        messages,
        *,
        heavy=_UNSET,
        task_type: str = TaskType.DEFAULT,
        max_tokens: int | None = None,
        _skip_budget: bool = False,
    ) -> str:
        """Core retry logic WITHOUT semaphore acquisition.

        Both chat_stream (which already holds the semaphore) and
        _chat_with_retry (which acquires it) call this.
        """
        # Resolve heavy: explicit True/False wins; if not passed, use _default_heavy
        # (set from user's use_heavy_model setting by build_provider).
        effective_heavy = self._default_heavy if heavy is _UNSET else heavy
        model_override = self._resolve_model_for_task(task_type)
        result = await self._try_with_retry(
            lambda p: p.chat(messages, heavy=effective_heavy, max_tokens=max_tokens),
            model_override=model_override,
            _skip_budget=_skip_budget,
        )
        await _track_llm_usage(
            self.provider_name,
            model_override or self._model,
            messages,
            result,
        )
        return result

    def _resolve_model_for_task(self, task_type: str) -> str | None:
        """Resolve model for task type.

        Returns model name or None to use provider default.
        Priority: _model (set by build_provider from task overrides) > None

        Note: ``task_type`` is intentionally unused — model selection
        happens at provider-build time via ``_model``. Per-task routing
        can be added here later if dynamic model switching is needed.
        """
        if self._model:
            return self._model
        return None

    async def chat_stream(
        self,
        messages,
        *,
        heavy=_UNSET,
        task_type: str = TaskType.DEFAULT,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str]:
        """Stream chat output token by token with key rotation.
        Falls back to regular chat() if no provider supports streaming."""
        # Resolve heavy: explicit True/False wins; if not passed, use _default_heavy
        effective_heavy = self._default_heavy if heavy is _UNSET else heavy
        model_override = self._resolve_model_for_task(task_type)
        sem = await acquire_purpose_slot(self._current_purpose)
        try:
            await self._semaphore.acquire()
            try:
                start_time = asyncio.get_running_loop().time()
                start_idx = await self._reserve_start_idx()
                last_error: Exception | None = None
                for attempt in range(len(self._keys)):
                    idx = (start_idx + attempt) % len(self._keys)
                    key = self._keys[idx]
                    cache_key = _make_cache_key(
                        self.provider_name, self._slot_ids, idx, key
                    )
                    if not await _check_key_circuit_breaker(cache_key):
                        continue
                    try:
                        provider_kwargs = self._build_provider_kwargs(
                            idx, model_override
                        )
                        provider = self._provider_class(key, **provider_kwargs)
                        async with self._providers_lock:
                            self._providers.append(provider)
                        total_text = ""
                        # 180s overall timeout; httpx 60s socket-level timeout per read
                        async with asyncio.timeout(180):
                            async for token in provider.chat_stream(
                                messages,
                                heavy=effective_heavy,
                                max_tokens=max_tokens,
                            ):
                                total_text += token
                                yield token
                        # Stream completed successfully — record metrics
                        await _record_key_success(
                            self.provider_name,
                            cache_key,
                            self._slot_ids,
                            idx,
                            start_time,
                        )
                        # _reserve_start_idx already advanced the round-robin index;
                        # no separate advance needed — avoids double-increment race.
                        # ── Account Usage Tracking ──
                        try:
                            await _track_llm_usage(
                                self.provider_name,
                                model_override or self._model,
                                messages,
                                total_text,
                            )
                        except Exception:
                            logger.debug(
                                "Failed to track usage in stream",
                                exc_info=True,
                            )
                        return
                    except (AttributeError, NotImplementedError):
                        logger.debug(
                            "Provider %s does not support streaming for key %s",
                            self.provider_name,
                            _mask_key(key),
                            exc_info=True,
                        )
                        continue
                    except Exception as e:
                        if _is_retryable_llm_error(e):
                            await _record_key_failure(
                                self.provider_name, cache_key, self._slot_ids, idx, e
                            )
                            last_error = e
                            logger.warning(
                                "Stream key %s failed: %s",
                                _mask_key(key),
                                safe_str(e)[:200],
                            )
                            continue
                        raise
                    # ponytail: per-attempt close removed — lifecycle left to caller
                # All streaming attempts failed — record failure and fallback
                if last_error:
                    try:
                        await _record_provider_failure(self.provider_name)
                    except Exception:
                        logger.exception(
                            "Failed to record provider failure metric for %s",
                            self.provider_name,
                        )
                yield await self._retry_inner(
                    messages,
                    heavy=effective_heavy,
                    task_type=task_type,
                    _skip_budget=True,
                )
            finally:
                self._semaphore.release()
        finally:
            release_purpose_slot(sem)

    async def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        task_type: str = TaskType.DEFAULT,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """Chat with tool definitions and key rotation."""
        sem = await acquire_purpose_slot(self._current_purpose)
        try:
            await self._semaphore.acquire()
            try:
                model_override = self._resolve_model_for_task(task_type)
                resp = await self._try_with_retry(
                    lambda p: p.chat_with_tools(
                        messages,
                        tools=tools,
                        task_type=task_type,
                        max_tokens=max_tokens,
                    ),
                    model_override=model_override,
                )
                await _track_llm_usage(
                    self.provider_name,
                    model_override or self._model,
                    messages,
                    resp.text,
                )
                return resp
            finally:
                self._semaphore.release()
        finally:
            release_purpose_slot(sem)

    async def embed(self, text: str) -> list[float]:
        """Embed с защитой backpressure (background семафор)."""
        sem = await acquire_purpose_slot("background")
        try:
            await self._semaphore.acquire()
            try:
                return await self._try_with_retry(lambda p: p.embed(text))
            finally:
                self._semaphore.release()
        finally:
            release_purpose_slot(sem)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed_batch с защитой backpressure (background семафор)."""
        sem = await acquire_purpose_slot("background")
        try:
            await self._semaphore.acquire()
            try:
                return await self._try_with_retry(lambda p: p.embed_batch(texts))
            finally:
                self._semaphore.release()
        finally:
            release_purpose_slot(sem)

    def reset_llm_budget(self) -> None:
        """Reset the LLM iteration budget for a new user request window.

        Called by ProviderFallback at the start of each user-facing request
        to prevent budget exhaustion across requests.
        """
        if hasattr(self, "_llm_budget"):
            self._llm_budget.reset()

    async def validate_key(self) -> bool:
        # NOTE: валидирует доступ к ключу, а не наличие конкретной модели.
        # model_override не передаётся — проверяется только сам факт доступа к API.
        try:
            return await self._try_with_retry(lambda p: p.validate_key())
        except Exception:  # NOTE: validate_key может поднять сетевые ошибки (httpx),
            # ошибки аутентификации (401/403) или таймауты. Все → False.
            return False

    async def close(self) -> None:
        """Close all raw providers created during key rotation.

        Per-attempt close() was removed from retry loops to avoid repeated
        open/close overhead. Created instances are tracked here and closed
        when the MultiKeyProvider is disposed.
        """
        async with self._providers_lock:
            providers = self._providers
            self._providers = []
        _cancelled = False
        for p in providers:
            try:
                await p.close()
            except asyncio.CancelledError:
                # Shield: finish closing remaining providers even if this
                # task is being cancelled. Re-raise after all providers are closed.
                if (task := asyncio.current_task()) is not None:
                    task.uncancel()
                _cancelled = True
            except Exception:
                logger.debug("Non-critical error closing provider", exc_info=True)
        if _cancelled:
            raise asyncio.CancelledError()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def list_models(self) -> list[str]:
        """Возвращает включённые (enabled) модели для всех ключей провайдера.

        Если в БД есть LlmKeySlotModel с enabled=True — возвращаем только их.
        Иначе проверяем self._models (старые single-model слоты с slot.model).
        Иначе fallback: запрашиваем все модели через API первого ключа.
        """
        enabled = set()
        if self._slot_ids:
            from src.db.session import get_session
            from src.db.repos.key_repo import get_enabled_models_for_slots

            try:
                async with get_session() as session:
                    enabled = set(
                        await get_enabled_models_for_slots(session, self._slot_ids)
                    )
            except SQLAlchemyError:
                logger.exception(
                    "Failed to get enabled models for slots %s", self._slot_ids
                )
        # Проверяем старые single-model слоты (slot.model без LlmKeySlotModel)
        for model_list in self._models:
            if not isinstance(model_list, list):
                continue
            for m in model_list:
                if m:
                    enabled.add(m)
        if enabled:
            return sorted(enabled)
        # Fallback: если нет enabled моделей ни в БД, ни в self._models —
        # запрашиваем все модели через API первого ключа
        key = self._keys[0]
        provider = self._provider_class(key)
        try:
            try:
                return await provider.list_models()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "list_models() failed for key %s, returning empty",
                    _mask_key(key),
                    exc_info=True,
                )
                return []
        finally:
            await provider.close()


class ExhaustedProvider:
    """Заглушка — все ключи в кулдауне или отсутствуют."""

    name: str = "exhausted"

    def __init__(self, reason: str = "no keys available") -> None:
        self._reason = reason

    async def validate_key(self) -> bool:
        return False

    async def chat(
        self,
        messages: object,
        *,
        heavy: bool = False,
        task_type: str = TaskType.DEFAULT,
        max_tokens: int | None = None,
    ) -> str:
        raise ExhaustedError(self._reason)

    async def chat_stream(
        self,
        messages: object,
        *,
        heavy: bool = False,
        task_type: str = TaskType.DEFAULT,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str]:
        raise ExhaustedError(self._reason)
        yield  # type: ignore[unreachable]

    async def embed(self, text: str) -> list[float]:
        raise ExhaustedError("Cannot embed: all keys exhausted")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise ExhaustedError("Cannot embed batch: all keys exhausted")

    async def close(self) -> None:
        pass
