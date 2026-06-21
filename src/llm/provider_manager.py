"""Менеджер LLM-провайдеров: регистрация, lifecycle, метрики, circuit breaker, semaphores.

Выделен из router.py (1655 строк god-class) для разделения зон ответственности:

- provider_manager — управление провайдерами:
  * сборка провайдеров (build_provider)
  * lifecycle: circuit breaker, метрики, семафоры
  * очистка устаревших записей (cleanup_circuit_breakers)
  * маппинг имён провайдеров → классы
  * авто-выбор модели (auto_select_model)

- src.llm.router — маршрутизация:
  * MultiKeyProvider (ротация ключей)
  * ProviderFallback (цепочка fallback)
  * ExhaustedError, ExhaustedProvider
  * вспомогательные утилиты (_is_retryable_llm_error, _mask_key)
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from src.llm.provider_fallback import ProviderFallback

from src.core.infra.key_guard import safe_str
from src.core.infra.timeutil import ensure_utc as _ensure_utc
from src.crypto import decrypt_async
from src.db.models import User
from src.db.repo import get_active_keys, get_api_keys, mark_key_failure, mark_key_used
from src.llm.base import TaskType

# ── Импорты классов провайдеров (нужны для _provider_class_for) ─────
from src.llm.anthropic_provider import AnthropicProvider
from src.llm.cloudflare_provider import CloudflareProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.mistral_provider import MistralProvider
from src.llm.openai_provider import OpenAIProvider
from src.llm.openrouter_provider import OpenRouterProvider
from src.llm.deepseek_provider import DeepSeekProvider
from src.llm.grok_provider import GrokProvider
from src.llm.mimo_provider import MiMoProvider
from src.llm.groq_provider import GroqProvider
from src.llm.custom_provider import CustomProvider

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
#  Circuit Breaker
# ═══════════════════════════════════════════════════════════════════════


class _CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _KeyCircuitBreaker:
    def __init__(self, failure_threshold: int = 3, base_timeout: float = 90.0) -> None:
        self._failure_count = 0
        self._tripped_count = 0
        self._state = _CircuitState.CLOSED
        self._last_failure_time = 0.0
        self._base_timeout = base_timeout
        self._failure_threshold = failure_threshold
        self._last_touched = asyncio.get_running_loop().time()

    @property
    def state(self) -> _CircuitState:
        return self._state

    def ready_at(self, now: float) -> float:
        """Возвращает монотонное время, когда ключ снова готов."""
        if self._state != _CircuitState.OPEN:
            return now
        timeout = self._base_timeout * (2**self._tripped_count)
        return self._last_failure_time + min(timeout, 3600.0)

    @property
    def cooldown_seconds(self) -> float:
        """Текущий экспоненциальный cooldown в секундах (capped 1h)."""
        return min(self._base_timeout * (2**self._tripped_count), 3600.0)

    def is_ready(self, now: float) -> bool:
        if self._state == _CircuitState.CLOSED:
            return True
        # HALF_OPEN is ready for probing.  Multiple concurrent callers may
        # attempt the key during the probe window — the first failure
        # re-trips back to OPEN (see record_failure), and the extra
        # parallelism is bounded by the purpose semaphore + round-robin
        # index.  The transition OPEN→HALF_OPEN itself is gated by
        # try_half_open() under _CIRCUIT_BREAKERS_LOCK.
        if self._state == _CircuitState.HALF_OPEN:
            return True
        return False

    def record_success(self) -> None:
        self._failure_count = 0
        self._tripped_count = 0
        self._state = _CircuitState.CLOSED
        self._last_touched = asyncio.get_running_loop().time()

    def record_failure(self, now: float) -> None:
        self._failure_count += 1
        self._last_failure_time = now
        self._last_touched = now
        if self._state == _CircuitState.HALF_OPEN:
            self._state = _CircuitState.OPEN
            self._tripped_count += 1
        elif (
            self._state == _CircuitState.CLOSED
            and self._failure_count >= self._failure_threshold
        ):
            self._state = _CircuitState.OPEN
            # NOTE: _tripped_count НЕ инкрементится при первом открытии —
            # он растёт только при re-trip'ах (HALF_OPEN → OPEN),
            # чтобы экспоненциальный backoff считался с base*2^0 = base.

    def try_half_open(self, now: float) -> bool:
        """Переводит в HALF_OPEN если пришло время пробовать."""
        if self._state != _CircuitState.OPEN:
            return False
        if now >= self.ready_at(now):
            self._state = _CircuitState.HALF_OPEN
            return True
        return False


# ═══════════════════════════════════════════════════════════════════════
#  Adaptive Provider Selection — метрики провайдеров
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class _ProviderMetrics:
    """Per-provider performance metrics for adaptive selection.

    Хранит историю успехов/неудач и среднюю латентность для каждого
    LLM-провайдера (openai, gemini, mistral, ...). Используется в
    ProviderFallback.chat() для сортировки провайдеров — наиболее
    надёжный и быстрый пробуется первым.
    """

    success_count: int = 0
    failure_count: int = 0
    total_latency: float = 0.0
    call_count: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        if total == 0:
            return 1.0  # неизвестный = оптимистичный (exploration bias)
        return self.success_count / total

    @property
    def avg_latency(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_latency / self.call_count

    def score(self, now: float) -> float:
        """Composite score 0..1. Higher = provider to try first.

        Формула: 60% успешность + 40% латентность, с штрафом за
        недавние (последние 60s) ошибки.
        """
        sr = self.success_rate
        lat = self.avg_latency
        # Normalize latency: 0s → 1.0, >=10s → 0.0
        lat_score = max(0.0, 1.0 - lat / 10.0) if self.call_count > 0 else 0.5
        # Recent failure penalty
        if self.last_failure_time > 0 and now - self.last_failure_time < 60.0:
            recency_penalty = 0.3
        else:
            recency_penalty = 1.0
        return (sr * 0.6 + lat_score * 0.4) * recency_penalty


_PROVIDER_METRICS: dict[str, _ProviderMetrics] = {}
_PROVIDER_METRICS_LOCK: asyncio.Lock | None = (
    None  # initialized via ensure_locks_initialized() at startup
)


async def _record_provider_success(name: str, latency: float) -> None:
    """Записывает успешный вызов провайдера с замеренной латентностью."""
    if _PROVIDER_METRICS_LOCK is None:
        return  # locks not initialized yet (startup race)
    now = asyncio.get_running_loop().time()
    async with _PROVIDER_METRICS_LOCK:
        metrics = _PROVIDER_METRICS.get(name)
        if metrics is None:
            metrics = _ProviderMetrics()
            _PROVIDER_METRICS[name] = metrics
        metrics.success_count += 1
        metrics.call_count += 1
        metrics.total_latency += latency
        metrics.last_success_time = now


async def _record_provider_failure(name: str) -> None:
    """Записывает неудачный вызов провайдера."""
    if _PROVIDER_METRICS_LOCK is None:
        return  # locks not initialized yet (startup race)
    now = asyncio.get_running_loop().time()
    async with _PROVIDER_METRICS_LOCK:
        metrics = _PROVIDER_METRICS.get(name)
        if metrics is None:
            metrics = _ProviderMetrics()
            _PROVIDER_METRICS[name] = metrics
        metrics.failure_count += 1
        metrics.last_failure_time = now


def _score_provider(name: str, now: float) -> float:
    """Public score lookup. 1.0 для провайдеров без истории (exploration).

    Безопасность: читает _PROVIDER_METRICS без блокировки — допустимо, т.к.:
    - dict.get() атомарен в CPython (GIL)
    - поля _ProviderMetrics — простые типы (int/float), атомарное чтение
    - худший случай: score на слегка устаревших данных (метрики — soft state)
    """
    metrics = _PROVIDER_METRICS.get(name)
    if metrics is None:
        return 1.0
    return metrics.score(now)


# ═══════════════════════════════════════════════════════════════════════
#  Key-level helpers — shared by router.py (MultiKeyProvider)
# ═══════════════════════════════════════════════════════════════════════


async def _check_key_circuit_breaker(cache_key: tuple[str, str]) -> bool:
    """Returns True if the key is ready to use (CLOSED/HALF_OPEN) or
    successfully transitioned OPEN→HALF_OPEN.

    Returns False if the key is in OPEN state with active cooldown
    and ``try_half_open`` fails — caller should skip this key.
    """
    if _CIRCUIT_BREAKERS_LOCK is not None:
        async with _CIRCUIT_BREAKERS_LOCK:
            cb = _CIRCUIT_BREAKERS.get(cache_key)
    else:
        cb = None
    if cb is None:
        return True
    now = asyncio.get_running_loop().time()
    if cb.is_ready(now):
        return True
    return cb.try_half_open(now)


def _make_cache_key(
    provider_name: str, slot_ids: list[int], idx: int, key: str
) -> tuple[str, str]:
    """Build the cache key used by circuit breaker and DB tracking."""
    if slot_ids and idx < len(slot_ids):
        return (provider_name, str(slot_ids[idx]))
    return (provider_name, key)


async def _record_key_success(
    provider_name: str,
    cache_key: tuple[str, str],
    slot_ids: list[int],
    idx: int,
    start_time: float,
) -> None:
    """Record circuit breaker success, DB ``mark_key_used``, and provider metrics."""
    # Circuit breaker: record success
    if _CIRCUIT_BREAKERS_LOCK is not None:
        async with _CIRCUIT_BREAKERS_LOCK:
            cb = _CIRCUIT_BREAKERS.get(cache_key)
            if cb:
                cb.record_success()
                if cb.state == _CircuitState.CLOSED:
                    _CIRCUIT_BREAKERS.pop(cache_key, None)
    # DB: mark key as used (fresh session)
    if slot_ids and idx < len(slot_ids):
        try:
            async with get_session() as fresh_s:
                await mark_key_used(fresh_s, slot_ids[idx])
        except SQLAlchemyError:
            logger.exception(
                "Failed to mark key slot %d as used",
                slot_ids[idx],
            )
    # Adaptive Provider Selection: record success metrics
    latency = asyncio.get_running_loop().time() - start_time
    try:
        await _record_provider_success(provider_name, latency)
    except Exception:
        logger.exception(
            "Failed to record provider success metric for %s",
            provider_name,
        )


async def _record_key_failure(
    provider_name: str,
    cache_key: tuple[str, str],
    slot_ids: list[int],
    idx: int,
    exc: Exception,
) -> None:
    """Record circuit breaker failure and DB ``mark_key_failure``."""
    # Circuit breaker: record failure
    if _CIRCUIT_BREAKERS_LOCK is not None:
        async with _CIRCUIT_BREAKERS_LOCK:
            if cache_key not in _CIRCUIT_BREAKERS:
                _trim_circuit_breakers_if_needed()
                _CIRCUIT_BREAKERS[cache_key] = _KeyCircuitBreaker()
            _CIRCUIT_BREAKERS[cache_key].record_failure(
                asyncio.get_running_loop().time()
            )
            _cb = _CIRCUIT_BREAKERS[cache_key]
            _cb_cooldown = int(_cb.cooldown_seconds)
    else:
        _cb_cooldown = int(KEY_COOLDOWN_SECONDS)
    # DB: mark key slot as failed (fresh session)
    if slot_ids and idx < len(slot_ids):
        try:
            async with get_session() as fresh_s:
                error_msg = (
                    f"{type(exc).__name__}: {safe_str(exc).split(chr(10))[0]}"
                )[:256]
                await mark_key_failure(
                    fresh_s,
                    slot_ids[idx],
                    error_msg,
                    cooldown_sec=_cb_cooldown,
                )
        except SQLAlchemyError:
            logger.exception(
                "Failed to mark key slot %d as failed",
                slot_ids[idx],
            )


# ═══════════════════════════════════════════════════════════════════════
#  Порядок провайдеров для fallback-цепочки
# ═══════════════════════════════════════════════════════════════════════

PROVIDER_ORDER = (
    "deepseek",
    "grok",
    "mimo",
    "groq",
    "openrouter",
    "openai",
    "gemini",
    "mistral",
    "cloudflare",
    # anthropic and custom must also participate in the fallback chain.
    # Keeping them at the end because they require explicit user-provided
    # credentials and a base_url (custom) / specific model (anthropic) — the
    # free-tier providers above are tried first.
    "anthropic",
    "custom",
)

# ═══════════════════════════════════════════════════════════════════════
#  Circuit Breaker — глобальный реестр
# ═══════════════════════════════════════════════════════════════════════

KEY_COOLDOWN_SECONDS = 90.0
MAX_RETRIES_PER_KEY = 3
RETRY_BASE_DELAY = 1.0  # seconds
_CIRCUIT_BREAKERS_MAX_SIZE = 1000

# NOTE: Circuit breaker состояние хранится только в памяти и сбрасывается при рестарте.
# Ключи, которые были в cooldown на момент останова, будут повторно запрошены после
# перезапуска. _restore_cooldowns() частично восстанавливает состояние из DB-поля
# cooldown_until, но экспоненциальный backoff-счётчик (_tripped_count) обнуляется.
_CIRCUIT_BREAKERS: dict[tuple[str, str], _KeyCircuitBreaker] = {}
_CIRCUIT_BREAKERS_LOCK: asyncio.Lock | None = (
    None  # initialized via ensure_locks_initialized() at startup
)


async def cleanup_circuit_breakers(
    stale_threshold: float = 3600.0,
    open_timeout: float = 1800.0,
) -> int:
    """Remove stale entries from global in-memory caches to prevent memory leaks.

    Called periodically (every ~5 min) from the global cleanup loop.

    Design trade-off: CLOSED breakers удаляются при неактивности >1h;
    OPEN/HALF_OPEN — при простое >30 мин, даже если backoff ещё не истёк.
    Это предотвращает перманентную блокировку ключа после transient outage,
    ценой сброса экспоненциального backoff-состояния. Для single-user бота
    приемлемо — повторная ошибка быстро восстановит OPEN.

    Cleans:

    * ``_CIRCUIT_BREAKERS`` — circuit breakers that haven't been accessed
      recently (prevents unbounded growth when keys are rotated or removed
      from the database).

      - CLOSED:  removed if ``_last_touched`` > stale_threshold ago (1 h).
      - OPEN / HALF_OPEN: removed if ``_last_failure_time`` > open_timeout
        ago (30 min) — the breaker should retry from scratch instead of
        staying stuck forever.

    * ``_PROVIDER_METRICS`` — per-provider performance metrics for adaptive
      provider selection (prevents unbounded growth when many provider names
      appear over time).

      - Entries are removed if the last activity (max of last_success_time
        and last_failure_time) is older than ``stale_threshold``.
      - Entries that have never recorded any activity (last_active == 0)
        are preserved — they represent newly-seen providers with optimistic
        (exploration) scores.

    Args:
        stale_threshold: seconds since last activity after which a CLOSED
            breaker or a provider-metrics entry is considered eligible for
            cleanup. Default: 3600 (1 h).
        open_timeout: seconds since last failure after which an OPEN /
            HALF_OPEN breaker is considered stuck and eligible for cleanup.
            Default: 1800 (30 min).

    Returns:
        Total number of entries removed (circuit breakers + provider metrics).
    """
    if _CIRCUIT_BREAKERS_LOCK is None:
        return 0

    now = asyncio.get_running_loop().time()
    removed = 0

    async with _CIRCUIT_BREAKERS_LOCK:
        stale_keys: list[tuple] = []
        for key, cb in _CIRCUIT_BREAKERS.items():
            if cb.state == _CircuitState.CLOSED:
                if (now - cb._last_touched) > stale_threshold:
                    stale_keys.append(key)
            elif cb.state in (_CircuitState.OPEN, _CircuitState.HALF_OPEN):
                if (now - cb._last_failure_time) > open_timeout:
                    stale_keys.append(key)

        for key in stale_keys:
            del _CIRCUIT_BREAKERS[key]
            removed += 1

    if removed:
        logger.debug(
            "cleanup_circuit_breakers: removed %d stale circuit breakers (%d remain)",
            removed,
            len(_CIRCUIT_BREAKERS),
        )

    # ── Clean stale _PROVIDER_METRICS entries ──────────────────────
    if _PROVIDER_METRICS_LOCK is not None:
        async with _PROVIDER_METRICS_LOCK:
            stale_metrics: list[str] = []
            for name, m in _PROVIDER_METRICS.items():
                last_active = max(m.last_success_time, m.last_failure_time)
                # last_active == 0 means the entry was just created and never
                # had any activity — keep it (exploration bias).
                if last_active > 0 and (now - last_active) > stale_threshold:
                    stale_metrics.append(name)

            for name in stale_metrics:
                del _PROVIDER_METRICS[name]
                removed += 1

        if stale_metrics:
            logger.debug(
                "cleanup_circuit_breakers: removed %d stale provider-metrics entries "
                "(%d remain)",
                len(stale_metrics),
                len(_PROVIDER_METRICS),
            )

    return removed


# ═══════════════════════════════════════════════════════════════════════
#  Purpose-семафоры — ограничение параллельных запросов
# ═══════════════════════════════════════════════════════════════════════

# Per-purpose лимиты параллельных запросов
_PURPOSE_SEMAPHORES: dict[str, asyncio.Semaphore] | None = (
    None  # initialized via ensure_locks_initialized() at startup
)

_locks_initialized = False


async def ensure_locks_initialized() -> None:
    """Инициализирует глобальные locks внутри event loop контекста.

    Вызывается при старте приложения — безопасная альтернатива lazy-init,
    который имеет race condition при первом обращении.
    """
    global \
        _PROVIDER_METRICS_LOCK, \
        _CIRCUIT_BREAKERS_LOCK, \
        _PURPOSE_SEMAPHORES, \
        _locks_initialized
    if not _locks_initialized:
        _PROVIDER_METRICS_LOCK = asyncio.Lock()
        _CIRCUIT_BREAKERS_LOCK = asyncio.Lock()
        _PURPOSE_SEMAPHORES = {
            "main": asyncio.Semaphore(2),
            "draft": asyncio.Semaphore(1),
            "memory": asyncio.Semaphore(1),
            "background": asyncio.Semaphore(3),
            "analysis": asyncio.Semaphore(1),
            "urgent": asyncio.Semaphore(2),
            "search": asyncio.Semaphore(2),
            "summarize": asyncio.Semaphore(2),
            "fallback": asyncio.Semaphore(2),
        }
        _locks_initialized = True


def _trim_circuit_breakers_if_needed() -> None:
    """Cap in-memory breaker cache to prevent unbounded growth.

    Called by _record_key_failure (in this module) before inserting a new
    breaker. Removes the oldest CLOSED entries first; preserves
    OPEN/HALF_OPEN entries.
    """
    excess = len(_CIRCUIT_BREAKERS) - _CIRCUIT_BREAKERS_MAX_SIZE
    if excess <= 0:
        return
    # Sort by last access time; remove oldest CLOSED entries first.
    ordered = sorted(
        _CIRCUIT_BREAKERS.items(),
        key=lambda item: (item[1].state != _CircuitState.CLOSED, item[1]._last_touched),
    )
    for key, _ in ordered[:excess]:
        del _CIRCUIT_BREAKERS[key]


async def acquire_purpose_slot(
    purpose: str, timeout: float = 120.0
) -> asyncio.Semaphore:
    """Захватывает слот для purpose с таймаутом. Возвращает семафор.

    Если purpose-семафор не освобождается за *timeout* секунд
    (все слоты заняты и не возвращаются — deadlock/stuck),
    переключается на fallback-семафор с собственным acquire.
    """
    if _PURPOSE_SEMAPHORES is None:
        raise RuntimeError("ensure_locks_initialized() must be called at startup")
    sem = _PURPOSE_SEMAPHORES.get(purpose)
    if sem is None:
        sem = _PURPOSE_SEMAPHORES.get("fallback", asyncio.Semaphore(1))
    try:
        await asyncio.wait_for(sem.acquire(), timeout=timeout)
        return sem
    except TimeoutError:
        logger.warning(
            "Timed out waiting for '%s' purpose slot (%.0fs), using fallback",
            purpose,
            timeout,
        )
        fallback = _PURPOSE_SEMAPHORES.get("fallback")
        if fallback is None:
            fallback = asyncio.Semaphore(1)
        await fallback.acquire()
        return fallback


def release_purpose_slot(sem: asyncio.Semaphore) -> None:
    """Освобождает слот."""
    sem.release()


# ═══════════════════════════════════════════════════════════════════════
#  Восстановление кулдаунов после рестарта
# ═══════════════════════════════════════════════════════════════════════


async def _restore_cooldowns(slot_ids: list[int]) -> None:
    """Восстанавливает circuit breaker'ы для ключей в кулдауне после рестарта.

    После перезапуска in-memory _KeyCircuitBreaker объекты теряются.
    DB-поле cooldown_until переживает рестарт — используем его для восстановления
    OPEN-состояния и экспоненциального backoff'а.

    Принимает slot_ids от одного или нескольких провайдеров — запрашивает
    все за один проход (единая DB-сессия).
    """
    if not slot_ids:
        return

    try:
        from sqlalchemy import select
        from src.db.models import LlmKeySlot

        async with get_session() as session:
            now_utc = datetime.now(UTC)

            # Запрашиваем конкретные слоты с активным кулдауном на уровне SQL
            # (DateTime(timezone=True) гарантирует корректное сравнение для новых записей)
            q = select(LlmKeySlot).where(
                LlmKeySlot.id.in_(slot_ids),
                LlmKeySlot.cooldown_until.is_not(None),
                LlmKeySlot.cooldown_until > now_utc,
            )
            r = await session.execute(q)
            all_candidates = list(r.scalars().all())

            # Safety net: Python-фильтрация для legacy наивных дат,
            # которые SQL-уровень может пропустить/недопустить при строковом сравнении
            cooldown_slots: list[LlmKeySlot] = []
            for slot in all_candidates:
                if (
                    slot.cooldown_until is not None
                    and slot.cooldown_until.tzinfo is None
                ):
                    logger.debug(
                        "Legacy naive datetime in cooldown_until for slot %d (provider=%s)",
                        slot.id,
                        slot.provider,
                    )
                cu = _ensure_utc(slot.cooldown_until)
                if cu is not None and cu > now_utc:
                    cooldown_slots.append(slot)

            if not cooldown_slots:
                return

            now_mono = asyncio.get_running_loop().time()
            restored_by_provider: dict[str, int] = {}

            if _CIRCUIT_BREAKERS_LOCK is None:
                logger.warning(
                    "_restore_cooldowns called before locks initialized — skipping"
                )
                return
            async with _CIRCUIT_BREAKERS_LOCK:
                for slot in cooldown_slots:
                    cu = _ensure_utc(slot.cooldown_until)
                    if cu is None:
                        continue
                    cache_key = (slot.provider, str(slot.id))
                    if cache_key in _CIRCUIT_BREAKERS:
                        continue  # уже восстановлен (повторный вызов build_provider)

                    remaining = (cu - now_utc).total_seconds()
                    if remaining <= 0:
                        continue

                    cb = _KeyCircuitBreaker(
                        failure_threshold=3,
                        base_timeout=KEY_COOLDOWN_SECONDS,
                    )

                    # Подбираем _tripped_count: наименьшее значение,
                    # при котором backoff >= оставшегося времени кулдауна.
                    # Экспонента: base * 2^0 = 90s, 2^1 = 180s, 2^2 = 360s, …
                    tripped = 0
                    while (
                        KEY_COOLDOWN_SECONDS * (2**tripped) < remaining and tripped < 10
                    ):
                        tripped += 1

                    cb._state = _CircuitState.OPEN
                    cb._failure_count = cb._failure_threshold
                    cb._tripped_count = tripped
                    cb._last_failure_time = max(
                        0.0,
                        now_mono - (KEY_COOLDOWN_SECONDS * (2**tripped) - remaining),
                    )

                    _CIRCUIT_BREAKERS[cache_key] = cb
                    restored_by_provider[slot.provider] = (
                        restored_by_provider.get(slot.provider, 0) + 1
                    )

            for provider_name, count in restored_by_provider.items():
                logger.info(
                    "Restored %d circuit breaker(s) for %s from DB cooldown",
                    count,
                    provider_name,
                )
    except SQLAlchemyError:
        logger.exception("Failed to restore cooldowns from DB")


# Lazy import for get_session used by cooldown/metrics helpers
from src.db.session import get_session

# ═══════════════════════════════════════════════════════════════════════
#  Маппинг имён провайдеров → классы
# ═══════════════════════════════════════════════════════════════════════


def _provider_class_for(name: str) -> type | None:
    """Маппинг имени провайдера → класс."""
    return {
        "deepseek": DeepSeekProvider,
        "grok": GrokProvider,
        "mimo": MiMoProvider,
        "groq": GroqProvider,
        "custom": CustomProvider,
        "openrouter": OpenRouterProvider,
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
        "mistral": MistralProvider,
        "cloudflare": CloudflareProvider,
    }.get(name)


def _provider_order(primary: str) -> list[str]:
    return [primary] + [name for name in PROVIDER_ORDER if name != primary]


# ═══════════════════════════════════════════════════════════════════════
#  Task-type model resolution (авто-выбор модели)
# ═══════════════════════════════════════════════════════════════════════

# Mapping TaskType → Settings attribute name for agent-specific overrides.
_TASK_TYPE_TO_SETTINGS_ATTR: dict[str, str] = {
    TaskType.MAESTRO: "maestro_model",
    TaskType.DRAFT: "draft_model",
    TaskType.MEMORY: "memory_model",
    TaskType.SEARCH: "search_model",
    TaskType.STT: "stt_model",
    TaskType.HUMANIZE: "humanize_model",
    TaskType.CLASSIFY: "classify_model",
    TaskType.SUMMARIZE: "summarize_model",
    TaskType.SKILLS: "skills_model",
    TaskType.BACKGROUND: "background_model",
    TaskType.VISION: "vision_model",
}


def _parse_user_model_overrides(user: User) -> dict[str, str] | None:
    """Parse user.settings.model_overrides JSON safely."""
    if not user.settings or not user.settings.model_overrides:
        return None
    try:
        parsed = json.loads(user.settings.model_overrides)
        if isinstance(parsed, dict):
            return parsed
        return None
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "Failed to parse model_overrides for user %s",
            user.telegram_id,
        )
        return None


def auto_select_model(
    task_type: str,
    available_slots: list,
    provider_catalog: list,
) -> str | None:
    """Smart auto-routing: pick best provider+model for task type.

    Returns "provider_name/model_name" or None if no suitable slot found.
    """
    if not available_slots:
        return None

    catalog: dict[str, object] = {p.name: p for p in provider_catalog}

    # Task profiles: требования к модели для каждого типа задачи
    _TASK_PROFILES: dict[str, dict[str, object]] = {
        "maestro": {
            "prefer_tier": "paid",
            "needs_vision": False,
            "label": "reasoning+big ctx",
        },
        "draft": {"prefer_tier": "free", "needs_vision": False, "label": "speed"},
        "vision": {"prefer_tier": None, "needs_vision": True, "label": "vision"},
        "memory": {
            "prefer_tier": None,
            "needs_vision": False,
            "label": "universal-light",
        },
        "classify": {
            "prefer_tier": None,
            "needs_vision": False,
            "label": "universal-light",
        },
        "summarize": {
            "prefer_tier": None,
            "needs_vision": False,
            "label": "universal-light",
        },
        "background": {
            "prefer_tier": "free",
            "needs_vision": False,
            "label": "cheapest",
        },
        "search": {
            "prefer_tier": None,
            "needs_vision": False,
            "label": "universal-light",
        },
        "stt": {"prefer_tier": None, "needs_vision": False, "label": "universal-light"},
        "humanize": {
            "prefer_tier": None,
            "needs_vision": False,
            "label": "universal-light",
        },
        "skills": {
            "prefer_tier": None,
            "needs_vision": False,
            "label": "universal-light",
        },
        "default": {"prefer_tier": None, "needs_vision": False, "label": "universal"},
    }

    profile = _TASK_PROFILES.get(task_type, _TASK_PROFILES["default"])

    def _slot_success_rate(slot) -> float:
        total = (getattr(slot, "usage_count", None) or 0) + (
            getattr(slot, "failure_count", None) or 0
        )
        if total == 0:
            return 0.5
        return (getattr(slot, "usage_count", None) or 0) / total

    scored: list[tuple[float, str]] = []
    for slot in available_slots:
        if not getattr(slot, "enabled", True):
            continue
        provider_name = getattr(slot, "provider", "")
        info = catalog.get(provider_name)
        if info is None:
            continue
        if getattr(info, "category", "") != "llm":
            continue

        # Hard capability gate — vision tasks need vision support
        if profile["needs_vision"] and not getattr(info, "supports_vision", False):
            continue

        score: float = 0.0

        # Tier preference (±30 for matching preferred tier)
        _raw_tier: object = profile.get("prefer_tier")
        pref_tier: str | None = _raw_tier if isinstance(_raw_tier, str) else None
        info_tier: str = getattr(info, "tier", "")
        if (pref_tier == "paid" and info_tier == "paid") or (
            pref_tier == "free" and info_tier == "free"
        ):
            score += 30.0
        elif pref_tier is not None and info_tier != pref_tier:
            score -= 10.0  # slight penalty for wrong tier

        # Priority: normalize to 0-10 range (assume max realistic priority ~50)
        slot_priority: int = getattr(slot, "priority", 0) or 0
        score += min(float(slot_priority), 50.0) * 0.2

        # Success rate: 0-15 range
        score += _slot_success_rate(slot) * 15.0

        # Explicit model on slot → bonus (user explicitly chose this model)
        slot_model: str | None = getattr(slot, "model", None)
        if slot_model:
            score += 10.0

        # Пропускаем слоты без явной модели — "default" не является
        # валидным именем модели и приведёт к ошибке при вызове API.
        if not slot_model:
            continue

        scored.append((score, f"{provider_name}/{slot_model}"))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    logger.debug(
        "auto_select_model: task=%s → %s (score=%.1f, candidates=%d)",
        task_type,
        best,
        scored[0][0],
        len(scored),
    )
    return best


def _resolve_model_for_task(
    task_type: str,
    user_overrides: dict[str, str] | None,
    available_slots: list | None = None,
) -> str | None:
    """Resolve model name for a given task type.

    Priority:
      1. user.model_overrides[task_type]
      2. Settings agent override (maestro_model, draft_model, …)
      3. auto_select_model() — if settings.auto_select_model == True
      4. None (use provider default)
    """
    from src.config import settings

    # 1. User overrides (highest priority)
    if user_overrides:
        model = user_overrides.get(task_type, "")
        if model:
            # Strip provider prefix if present ("provider/model" -> "model")
            # Only strip if prefix is a known provider name
            if "/" in model:
                from src.llm.provider_catalog import get_provider as _gp

                maybe_provider = model.split("/", 1)[0]
                if _gp(maybe_provider) or _provider_class_for(maybe_provider):
                    model = model.split("/", 1)[1]
            return model

    # 2. Settings agent overrides
    settings_attr = _TASK_TYPE_TO_SETTINGS_ATTR.get(task_type)
    if settings_attr:
        model = getattr(settings, settings_attr, "")
        if model:
            return model

    # 3. Auto-select if enabled and no explicit override
    # NOTE: available_slots is always None when called from build_provider()
    # (the caller never passes it). This branch is intentionally unreachable
    # from the build_provider path but is kept as future-proofing for
    # direct callers that may supply slot data.
    if settings.auto_select_model and available_slots:
        from src.llm.provider_catalog import LLM_PROVIDERS as _LLM_PROVIDERS

        selected = auto_select_model(task_type, available_slots, _LLM_PROVIDERS)
        if selected:
            # Strip provider prefix: "provider/model" -> "model"
            if "/" in selected:
                selected = selected.split("/", 1)[1]
            logger.info(
                "auto_select_model: task=%s → model=%s",
                task_type,
                selected,
            )
            return selected

    # 4. None = use provider default
    return None


# ═══════════════════════════════════════════════════════════════════════
#  build_provider — главная точка сборки провайдеров
# ═══════════════════════════════════════════════════════════════════════


async def build_provider(
    session: AsyncSession,
    user: User,
    purpose: str = "main",
    task_type: str = TaskType.DEFAULT,
    embed_model: str | None = None,
) -> ProviderFallback | None:
    """Строит провайдер с авто-ротацией ключей из LlmKeySlot.

    Сначала пробует получить активные слоты (LlmKeySlot) для нужного провайдера
    и назначения. Если слотов нет — падает на старый ApiKey.
    Для chat() строит цепочку fallback-провайдеров.
    Для embed() остаётся на первичном провайдере.
    """
    # Lazy imports for MultiKeyProvider / ProviderFallback
    # (avoid circular import: provider_manager ↔ router)
    from src.llm.provider_fallback import ProviderFallback
    from src.llm.router import MultiKeyProvider

    # Проверка кэша
    from src.core.context_cache import get as cache_get

    cache_key = f"provider:{user.telegram_id}:{purpose}:{task_type}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return cached

    provider_name = user.settings.llm_provider if user.settings else "openai"
    use_heavy = user.settings.use_heavy_model if user.settings else False

    # Resolve embed_model default per provider if not specified
    if embed_model is None:
        from src.config import settings as _settings

        _embed_defaults = {
            "openai": _settings.openai_embed_model,
            "gemini": _settings.gemini_embed_model,
            "mistral": _settings.mistral_embed_model,
        }
        embed_model = _embed_defaults.get(provider_name)
        if embed_model is None:
            logger.warning(
                "No embed model default for provider %r — embed_model remains None, "
                "provider will use its own default. Known providers: %s",
                provider_name,
                sorted(_embed_defaults.keys()),
            )

    # Попытка через новую систему LlmKeySlot
    providers: list[MultiKeyProvider] = []
    all_slot_ids: list[int] = []
    for name in _provider_order(provider_name):
        try:
            slots = await get_active_keys(session, user, name, purpose)
            if not slots:
                continue
            keys = [await decrypt_async(s.key_enc) for s in slots]
            slot_ids = [s.id for s in slots]
            endpoints = [s.endpoint for s in slots]
            # Читаем мульти-модели из LlmKeySlotModel; fallback на s.model
            from src.db.repos.key_repo import get_enabled_models as _get_enabled

            models = []
            for s in slots:
                enabled = await _get_enabled(session, s.id)
                if enabled:
                    models.append(enabled)
                elif s.model:
                    models.append([s.model])
                else:
                    models.append([])
            all_slot_ids.extend(slot_ids)
            provider_class = _provider_class_for(name)
            if provider_class is None:
                logger.warning("Unknown provider class for %s, skipping", name)
                continue
            providers.append(
                MultiKeyProvider(
                    name,
                    provider_class,
                    keys,
                    slot_ids=slot_ids,
                    endpoints=endpoints,
                    models=models,
                    embed_model=embed_model,
                    # сессия для DB-трекинга открывается внутри _try_with_retry
                    # (lambda захватывает user для совместимости, session не используется)
                    session_provider=lambda: (None, user),
                    purpose=purpose,
                )
            )
        except (SQLAlchemyError, ValueError, TypeError) as exc:
            # M7: сбой одного провайдера не убивает всю цепочку — пропускаем и идём дальше
            logger.warning("Failed to build provider %s, skipping: %s", name, exc)
            continue
    # Восстанавливаем cooldown за один проход по всем провайдерам
    await _restore_cooldowns(all_slot_ids)
    if providers:
        if len(providers) > 1:
            logger.info(
                "LLM fallback chain (slots): %s",
                " -> ".join(p.name for p in providers),
            )
        from src.core.context_cache import put as cache_put

        result = ProviderFallback(providers)
        result._default_heavy = use_heavy
        # Resolve model for task_type (user overrides > settings > provider default)
        if task_type != TaskType.DEFAULT:
            user_overrides = _parse_user_model_overrides(user)
            resolved_model = _resolve_model_for_task(task_type, user_overrides)
            if resolved_model:
                result._model = resolved_model
                logger.debug(
                    "build_provider: task_type=%s → model=%s (from overrides)",
                    task_type,
                    resolved_model,
                )
        await cache_put(cache_key, result, ttl=300)
        return result

    # Fallback: старый ApiKey
    providers = []
    for name in _provider_order(provider_name):
        keys = await get_api_keys(session, user, name)
        if not keys:
            continue
        provider_class = _provider_class_for(name)
        if provider_class is None:
            logger.warning("Unknown provider class for %s, skipping", name)
            continue
        providers.append(
            MultiKeyProvider(
                name, provider_class, keys, purpose=purpose, embed_model=embed_model
            )
        )
    if not providers:
        # Проверяем: есть слоты но все в кулдауне?
        try:
            from src.db.repo import list_key_slots

            all_slots = await list_key_slots(
                session,
                user,
                provider=user.settings.llm_provider if user.settings else "openai",
            )
            in_cooldown = [
                s
                for s in all_slots
                if (cooldown := _ensure_utc(s.cooldown_until))
                and cooldown > datetime.now(UTC)
            ]
            if in_cooldown:
                in_cooldown_utc = [
                    c
                    for s in in_cooldown
                    if (c := _ensure_utc(s.cooldown_until)) is not None
                ]
                min_cooldown = min(in_cooldown_utc) if in_cooldown_utc else None
                if min_cooldown is not None:
                    wait_sec = max(
                        1,
                        int((min_cooldown - datetime.now(UTC)).total_seconds()),
                    )
                else:
                    wait_sec = 60
                logger.warning(
                    "build_provider: все ключи в кулдауне (wait %d сек).",
                    wait_sec,
                )
                return None
            elif all_slots:
                logger.warning("build_provider: все ключи отключены (enabled=False).")
                return None
            else:
                logger.warning("build_provider: нет ключей для провайдера.")
                return None
        except (SQLAlchemyError, ValueError):
            logger.exception("Failed to check key cooldown slots")
        return None
    if len(providers) > 1:
        logger.info(
            "LLM fallback chain (legacy): %s",
            " -> ".join(p.name for p in providers),
        )
    from src.core.context_cache import put as cache_put

    result = ProviderFallback(providers)
    result._default_heavy = use_heavy
    # Resolve model for task_type (user overrides > settings > provider default)
    if task_type != TaskType.DEFAULT:
        user_overrides = _parse_user_model_overrides(user)
        resolved_model = _resolve_model_for_task(task_type, user_overrides)
        if resolved_model:
            result._model = resolved_model
            logger.debug(
                "build_provider: task_type=%s → model=%s (from overrides, legacy)",
                task_type,
                resolved_model,
            )
    await cache_put(cache_key, result, ttl=300)
    return result


async def flush_provider_cache() -> None:
    """Закрыть все закэшированные LLM-провайдеры и очистить кэш.

    Вызывается из shutdown-цепочки в main.py.
    """
    from src.core.context_cache import extract as cache_extract
    from src.llm.provider_fallback import ProviderFallback

    cached_values = await cache_extract("provider:")
    closed = 0
    _cancelled = False
    for value in cached_values:
        if isinstance(value, ProviderFallback):
            try:
                await value.close()
                closed += 1
            except asyncio.CancelledError:
                # Shield: finish closing remaining providers even if
                # the shutdown task is being cancelled.
                # ProviderFallback.close() itself already uses this pattern
                # for its children — the re-raised CancelledError from
                # a fully-closed fallback must not abort the outer loop
                # before all other fallback instances are also closed.
                if (task := asyncio.current_task()) is not None:
                    task.uncancel()
                _cancelled = True
            except Exception:
                logger.exception(
                    "Failed to close cached ProviderFallback during shutdown"
                )
    if closed:
        logger.info("Closed %d cached LLM provider(s) during shutdown", closed)
    if _cancelled:
        raise asyncio.CancelledError()
