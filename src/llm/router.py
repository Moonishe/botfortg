import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.infra.telemetry import start_span
from src.core.infra.key_guard import safe_str
from src.db.repo import mark_key_failure, mark_key_used
from src.db.session import get_session
from src.llm.base import ChatMessage, TaskType

# ── Импорты из выделенного provider_manager ────────────────────────────
from src.llm.provider_manager import (
    _CircuitState,
    _KeyCircuitBreaker,
    _PURPOSE_SEMAPHORES,  # pyright: ignore[reportUnusedImport] — re-export for memory_admin_cmds
    _provider_class_for,  # pyright: ignore[reportUnusedImport] — re-export for keys_cmd + free_text_exec
    _record_provider_success,
    _record_provider_failure,
    _score_provider,
    KEY_COOLDOWN_SECONDS,
    MAX_RETRIES_PER_KEY,
    RETRY_BASE_DELAY,
    _CIRCUIT_BREAKERS,
    _CIRCUIT_BREAKERS_LOCK,
    _trim_circuit_breakers_if_needed,
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


class ExhaustedError(Exception):
    """Все API-ключи провайдера исчерпаны (колдаун/отключены)."""

    pass


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
    """True for transient capacity/rate-limit/server errors worth trying another key/provider."""
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
    text = f"{type(exc).__name__} {exc}".lower()
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
        session_provider: Callable[[], tuple[AsyncSession, object]] | None = None,
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
        self._current_purpose = purpose
        self._model: str | None = None  # global override; None = use per-slot model
        self._default_heavy: bool = False  # overridden by use_heavy_model setting
        self.name = f"{provider_name}(×{len(self._keys)})"

    async def _reserve_start_idx(self) -> int:
        async with self._idx_lock:
            start_idx = self._idx
            self._idx = (self._idx + 1) % len(self._keys)
            return start_idx

    async def _try_with_retry(
        self,
        operation,
        *args: object,
        model_override: str | None = None,
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
        now = start_time

        # ── Iteration Budget — prevent runaway LLM calls ──
        from src.core.intelligence.iteration_budget import IterationBudget

        if not hasattr(self, "_llm_budget"):
            self._llm_budget = IterationBudget()
        if not self._llm_budget.record_llm_call():
            raise RuntimeError("LLM call budget exhausted — too many calls in window")

        # Round-robin: reserve a unique start index for concurrent calls.
        start_idx = await self._reserve_start_idx()

        skipped = 0
        for attempt in range(len(self._keys)):
            idx = (start_idx + attempt) % len(self._keys)
            key = self._keys[idx]
            cache_key = (
                (self.provider_name, str(self._slot_ids[idx]))
                if self._slot_ids and idx < len(self._slot_ids)
                else (self.provider_name, key)
            )
            if _CIRCUIT_BREAKERS_LOCK is not None:
                async with _CIRCUIT_BREAKERS_LOCK:
                    cb = _CIRCUIT_BREAKERS.get(cache_key)
            else:
                cb = None
            if cb is not None and not cb.is_ready(now):
                # OPEN state (cooldown not yet expired) → skip the key.
                # When the cooldown expires, try_half_open() transitions
                # OPEN→HALF_OPEN and is_ready() returns True — the caller
                # proceeds to probe the key.
                # HALF_OPEN keys are always ready (is_ready=True), so they
                # bypass this block entirely.
                if not cb.try_half_open(now):
                    skipped += 1
                    continue
            # Create provider instance — handle creation failure separately
            try:
                endpoint = (
                    self._endpoints[idx]
                    if self._endpoints and idx < len(self._endpoints)
                    else None
                )
                provider_kwargs = dict(self._kwargs)
                if endpoint:
                    provider_kwargs["base_url"] = endpoint
                if model_override:
                    provider_kwargs["model"] = model_override
                elif self._model:
                    provider_kwargs["model"] = self._model
                elif self._models and idx < len(self._models):
                    per_slot = self._models[idx]
                    if per_slot:
                        # Используем первую enabled-модель из списка слота
                        provider_kwargs["model"] = per_slot[0]
                if self._embed_model:
                    provider_kwargs["embed_model"] = self._embed_model
                provider = self._provider_class(key, **provider_kwargs)
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
                                "LLM %s key %s attempt %d/%d failed, retrying in %.1fs: %s",
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
                        if _CIRCUIT_BREAKERS_LOCK is not None:
                            async with _CIRCUIT_BREAKERS_LOCK:
                                cb = _CIRCUIT_BREAKERS.get(cache_key)
                                if cb:
                                    cb.record_success()
                                    if cb.state == _CircuitState.CLOSED:
                                        _CIRCUIT_BREAKERS.pop(cache_key, None)
                        # DB: отметить успешное использование (fresh session)
                        if self._slot_ids:
                            try:
                                async with get_session() as fresh_s:
                                    await mark_key_used(fresh_s, self._slot_ids[idx])
                            except SQLAlchemyError:
                                logger.exception(
                                    "Failed to mark key slot %d as used",
                                    self._slot_ids[idx],
                                )
                        # Adaptive Provider Selection: запись метрик успеха
                        # (try/except — потеря result при ошибке метрики дороже, чем сама метрика)
                        latency = asyncio.get_running_loop().time() - start_time
                        try:
                            await _record_provider_success(self.provider_name, latency)
                        except Exception:
                            logger.exception(
                                "Failed to record provider success metric for %s",
                                self.provider_name,
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
                            "LLM error category %r is not retryable — aborting key",
                            category,
                        )
                        raise
                    logger.debug("LLM error category=%r — retrying", category)
                    if _CIRCUIT_BREAKERS_LOCK is not None:
                        async with _CIRCUIT_BREAKERS_LOCK:
                            if cache_key not in _CIRCUIT_BREAKERS:
                                _trim_circuit_breakers_if_needed()
                                _CIRCUIT_BREAKERS[cache_key] = _KeyCircuitBreaker()
                            _CIRCUIT_BREAKERS[cache_key].record_failure(now)
                            # Persist actual exponential backoff to DB.
                            # Use the *capped* cooldown property (max 1h) —
                            # consistent with the circuit breaker's own
                            # ready_at() which also caps at 3600s.
                            _cb = _CIRCUIT_BREAKERS[cache_key]
                            _cb_cooldown = int(_cb.cooldown_seconds)
                    else:
                        _cb_cooldown = int(KEY_COOLDOWN_SECONDS)
                    last_error = exc
                    logger.warning(
                        "LLM %s key %s temporarily failed, rotating: %s",
                        self.provider_name,
                        _mask_key(key),
                        safe_str(exc)[:200],
                    )
                    # DB: отметить падение слота (fresh session)
                    if self._slot_ids:
                        try:
                            async with get_session() as fresh_s:
                                error_msg = (
                                    f"{type(exc).__name__}: {safe_str(exc).split(chr(10))[0]}"
                                )[:256]
                                await mark_key_failure(
                                    fresh_s,
                                    self._slot_ids[idx],
                                    error_msg,
                                    cooldown_sec=_cb_cooldown,
                                )
                        except SQLAlchemyError:
                            logger.exception(
                                "Failed to mark key slot %d as failed",
                                self._slot_ids[idx],
                            )
                    continue
                raise
            finally:
                try:
                    await provider.close()
                except Exception:
                    logger.debug(
                        "Non-critical error", exc_info=True
                    )  # close failures should never mask the actual result
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
        self, messages, *, heavy=_UNSET, task_type: str = TaskType.DEFAULT
    ) -> str:
        sem = await acquire_purpose_slot(self._current_purpose)
        try:
            return await self._chat_with_retry(
                messages, heavy=heavy, task_type=task_type
            )
        finally:
            release_purpose_slot(sem)

    async def _chat_with_retry(
        self, messages, *, heavy=_UNSET, task_type: str = TaskType.DEFAULT
    ) -> str:
        await self._semaphore.acquire()
        try:
            return await self._retry_inner(messages, heavy=heavy, task_type=task_type)
        finally:
            self._semaphore.release()

    async def _retry_inner(
        self, messages, *, heavy=_UNSET, task_type: str = TaskType.DEFAULT
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
            lambda p: p.chat(messages, heavy=effective_heavy),
            model_override=model_override,
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
        self, messages, *, heavy=_UNSET, task_type: str = TaskType.DEFAULT
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
                    cache_key = (
                        (self.provider_name, str(self._slot_ids[idx]))
                        if self._slot_ids and idx < len(self._slot_ids)
                        else (self.provider_name, key)
                    )
                    # Circuit breaker check — skip keys in cooldown
                    if _CIRCUIT_BREAKERS_LOCK is not None:
                        async with _CIRCUIT_BREAKERS_LOCK:
                            cb = _CIRCUIT_BREAKERS.get(cache_key)
                    else:
                        cb = None
                    if cb is not None:
                        now = asyncio.get_running_loop().time()
                        if not cb.is_ready(now) and not cb.try_half_open(now):
                            # CLOSED → is_ready=True → skip this block.
                            # HALF_OPEN → is_ready=True → skip this block.
                            # OPEN (cooldown expired) → try_half_open=True → skip this block.
                            # Only OPEN with active cooldown → both False → skip entirely.
                            continue
                    endpoint = (
                        self._endpoints[idx]
                        if self._endpoints and idx < len(self._endpoints)
                        else None
                    )
                    provider_kwargs = dict(self._kwargs)
                    if endpoint:
                        provider_kwargs["base_url"] = endpoint
                    if model_override:
                        provider_kwargs["model"] = model_override
                    elif self._model:
                        provider_kwargs["model"] = self._model
                    elif self._models and idx < len(self._models):
                        per_slot = self._models[idx]
                        if per_slot:
                            provider_kwargs["model"] = per_slot[0]
                    if self._embed_model:
                        provider_kwargs["embed_model"] = self._embed_model
                    provider = None
                    try:
                        provider = self._provider_class(key, **provider_kwargs)
                        total_text = ""
                        # 180s overall timeout; httpx 60s socket-level timeout per read
                        async with asyncio.timeout(180):
                            async for token in provider.chat_stream(
                                messages, heavy=effective_heavy
                            ):
                                total_text += token
                                yield token
                        # Stream completed successfully — record metrics
                        # Circuit breaker: record success
                        if _CIRCUIT_BREAKERS_LOCK is not None:
                            async with _CIRCUIT_BREAKERS_LOCK:
                                cb = _CIRCUIT_BREAKERS.get(cache_key)
                                if cb:
                                    cb.record_success()
                                    if cb.state == _CircuitState.CLOSED:
                                        _CIRCUIT_BREAKERS.pop(cache_key, None)
                        # DB: mark key as used (fresh session)
                        if self._slot_ids:
                            try:
                                async with get_session() as fresh_s:
                                    await mark_key_used(fresh_s, self._slot_ids[idx])
                            except SQLAlchemyError:
                                logger.exception(
                                    "Failed to mark key slot %d as used",
                                    self._slot_ids[idx],
                                )
                        # Adaptive Provider Selection: record success metrics
                        latency = asyncio.get_running_loop().time() - start_time
                        try:
                            await _record_provider_success(self.provider_name, latency)
                        except Exception:
                            logger.exception(
                                "Failed to record provider success metric for %s",
                                self.provider_name,
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
                        continue
                    except Exception as e:
                        if _is_retryable_llm_error(e):
                            # Circuit breaker: record failure
                            if _CIRCUIT_BREAKERS_LOCK is not None:
                                async with _CIRCUIT_BREAKERS_LOCK:
                                    if cache_key not in _CIRCUIT_BREAKERS:
                                        _trim_circuit_breakers_if_needed()
                                        _CIRCUIT_BREAKERS[cache_key] = (
                                            _KeyCircuitBreaker()
                                        )
                                    _CIRCUIT_BREAKERS[cache_key].record_failure(
                                        asyncio.get_running_loop().time()
                                    )
                                    _cb = _CIRCUIT_BREAKERS[cache_key]
                                    _cb_cooldown = int(_cb.cooldown_seconds)
                            else:
                                _cb_cooldown = int(KEY_COOLDOWN_SECONDS)
                            last_error = e
                            logger.warning(
                                "Stream key %s failed: %s",
                                _mask_key(key),
                                safe_str(e)[:200],
                            )
                            # DB: mark key slot as failed
                            if self._slot_ids:
                                try:
                                    async with get_session() as fresh_s:
                                        error_msg = (
                                            f"{type(e).__name__}: {safe_str(e).split(chr(10))[0]}"
                                        )[:256]
                                        await mark_key_failure(
                                            fresh_s,
                                            self._slot_ids[idx],
                                            error_msg,
                                            cooldown_sec=_cb_cooldown,
                                        )
                                except SQLAlchemyError:
                                    logger.exception(
                                        "Failed to mark key slot %d as failed",
                                        self._slot_ids[idx],
                                    )
                            continue
                        raise
                    finally:
                        try:
                            await provider.close()
                        except Exception:
                            logger.debug(
                                "Non-critical error", exc_info=True
                            )  # close failures should never mask the actual result
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
                    messages, heavy=effective_heavy, task_type=task_type
                )
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

    async def validate_key(self) -> bool:
        # NOTE: валидирует доступ к ключу, а не наличие конкретной модели.
        # model_override не передаётся — проверяется только сам факт доступа к API.
        try:
            return await self._try_with_retry(lambda p: p.validate_key())
        except Exception:  # NOTE: validate_key может поднять сетевые ошибки (httpx),
            # ошибки аутентификации (401/403) или таймауты. Все → False.
            return False

    async def close(self) -> None:
        """MultiKeyProvider is a factory — instances are closed in _try_with_retry."""
        pass

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
            except Exception:
                logger.warning(
                    "list_models() failed for key %s, returning empty",
                    key[:16] + "…" if len(key) > 16 else key,
                    exc_info=True,
                )
                return []
        finally:
            await provider.close()


@dataclass
class ProviderFallback:
    """Primary provider with chat fallback to other configured providers.

    Embeddings intentionally stay on the primary provider to avoid mixing vector
    dimensions in Qdrant.
    """

    providers: list[MultiKeyProvider]
    _last_primary_dim: int | None = None

    @property
    def name(self) -> str:
        return " → ".join(p.name for p in self.providers)

    @property
    def primary(self) -> MultiKeyProvider:
        return self.providers[0]

    @property
    def _model(self) -> str | None:
        """Global model override propagated from settings (e.g. maestro_model)."""
        return self.providers[0]._model if self.providers else None

    @_model.setter
    def _model(self, value: str | None) -> None:
        for p in self.providers:
            p._model = value

    @property
    def _default_heavy(self) -> bool:
        """Default heavy flag propagated from user's use_heavy_model setting."""
        return self.providers[0]._default_heavy if self.providers else False

    @_default_heavy.setter
    def _default_heavy(self, value: bool) -> None:
        for p in self.providers:
            p._default_heavy = value

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool | None = None,
        task_type: str = TaskType.DEFAULT,
    ) -> str:
        """Chat c адаптивным выбором провайдера.

        Сортирует провайдеров по композитному score (успешность + латентность)
        и пробует наиболее надёжного/быстрого первым. Embeddings не сортируются —
        остаются на primary для совместимости размерностей векторов.
        """
        last_error: Exception | None = None
        now = asyncio.get_running_loop().time()
        sorted_providers = sorted(
            self.providers,
            key=lambda p: _score_provider(p.provider_name, now),
            reverse=True,
        )
        # Map None → _UNSET for MultiKeyProvider (preserves "use _default_heavy" semantic)
        mkp_heavy = _UNSET if heavy is None else heavy
        for provider in sorted_providers:
            try:
                with start_span(
                    "llm.chat",
                    provider=provider.provider_name,
                    task_type=task_type,
                    msg_count=len(messages),
                ):
                    return await provider.chat(
                        messages, heavy=mkp_heavy, task_type=task_type
                    )
            except Exception as exc:
                if not isinstance(exc, ExhaustedError) and not _is_retryable_llm_error(
                    exc
                ):
                    raise
                last_error = exc
                logger.warning(
                    "LLM provider %s failed, trying next: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        raise last_error or RuntimeError("All LLM providers failed")

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        heavy: bool | None = None,
        task_type: str = TaskType.DEFAULT,
    ) -> AsyncGenerator[str]:
        """Stream chat with adaptive provider fallback. Falls back to regular chat."""
        now = asyncio.get_running_loop().time()
        sorted_providers = sorted(
            self.providers,
            key=lambda p: _score_provider(p.provider_name, now),
            reverse=True,
        )
        # Map None → _UNSET for MultiKeyProvider (preserves "use _default_heavy" semantic)
        mkp_heavy = _UNSET if heavy is None else heavy
        for provider in sorted_providers:
            try:
                async for token in provider.chat_stream(
                    messages, heavy=mkp_heavy, task_type=task_type
                ):
                    yield token
                return
            except (AttributeError, NotImplementedError):
                continue
            except Exception as exc:
                if not isinstance(exc, ExhaustedError) and not _is_retryable_llm_error(
                    exc
                ):
                    raise
                logger.warning(
                    "LLM provider %s streaming failed, trying next: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        # All streaming failed — fallback to regular chat
        yield await self.chat(messages, heavy=heavy, task_type=task_type)

    async def embed(self, text: str) -> list[float]:
        """Embed с fallback по цепочке провайдеров.

        При фейле primary — пробует следующих. ВАЖНО: размерности векторов
        могут отличаться между провайдерами (BGE-M3: 1024, OpenAI: 1536).
        Fallback с несовпадающей размерностью вызывает ValueError.
        """
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                result = await provider.embed(text)
                # M8: запоминаем размерность первого успешного эмбеддинга,
                # даже если primary не сработал — для последующей валидации размерностей.
                if self._last_primary_dim is None:
                    self._last_primary_dim = len(result)
                elif len(result) != self._last_primary_dim:
                    raise ValueError(
                        f"Embedding dimension mismatch: primary={self._last_primary_dim}, "
                        f"fallback {provider.name}={len(result)}. "
                        "Vectors would corrupt Qdrant index."
                    )
                return result
            except Exception as exc:
                if not isinstance(
                    exc, (ExhaustedError, NotImplementedError, ValueError)
                ) and not _is_retryable_llm_error(exc):
                    raise
                last_error = exc
                logger.warning(
                    "Embed provider %s failed, trying fallback: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        raise last_error or RuntimeError("All embed providers failed")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed_batch с fallback по цепочке провайдеров.

        Аналогично embed() — при фейле primary пробует backup-провайдеров,
        с проверкой размерности векторов для предотвращения повреждения Qdrant.
        """
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                result = await provider.embed_batch(texts)
                if result:
                    # M8: запоминаем размерность первого успешного эмбеддинга
                    if self._last_primary_dim is None:
                        self._last_primary_dim = len(result[0])
                    elif len(result[0]) != self._last_primary_dim:
                        raise ValueError(
                            f"Embedding dimension mismatch: primary={self._last_primary_dim}, "
                            f"fallback {provider.name}={len(result[0])}. "
                            "Vectors would corrupt Qdrant index."
                        )
                return result
            except Exception as exc:
                if not isinstance(
                    exc, (ExhaustedError, NotImplementedError, ValueError)
                ) and not _is_retryable_llm_error(exc):
                    raise
                last_error = exc
                logger.warning(
                    "Embed_batch provider %s failed, trying fallback: %s",
                    provider.name,
                    safe_str(exc)[:200],
                )
        raise last_error or RuntimeError("All embed_batch providers failed")

    async def validate_key(self) -> bool:
        for provider in self.providers:
            if await provider.validate_key():
                return True
        return False

    async def close(self) -> None:
        """Close all child provider instances."""
        for p in self.providers:
            if hasattr(p, "close"):
                await p.close()

    async def list_models(self) -> list[str]:
        """Возвращает только включённые (enabled) модели из всех primary-провайдеров."""
        all_models: set[str] = set()
        for provider in self.providers:
            try:
                models = await provider.list_models()
                all_models.update(models)
            except Exception:
                continue
        return sorted(all_models)


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
    ) -> str:
        raise ExhaustedError(self._reason)

    async def chat_stream(
        self,
        messages: object,
        *,
        heavy: bool = False,
        task_type: str = TaskType.DEFAULT,
    ) -> AsyncGenerator[str]:
        raise ExhaustedError(self._reason)

    async def embed(self, text: str) -> list[float]:
        raise ExhaustedError("Cannot embed: all keys exhausted")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise ExhaustedError("Cannot embed batch: all keys exhausted")

    async def close(self) -> None:
        pass
