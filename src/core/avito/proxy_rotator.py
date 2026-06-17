"""Ротатор прокси для мобильной прокси-фермы.

Поддерживает паттерн «2 телефона»: один парсит, второй меняет IP.
Управляет состоянием прокси (active/cooldown/banned/changing),
автоматически ротирует IP при превышении лимита ошибок.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

from src.core.security.ssrf_guard import _check_ssrf_async

logger = logging.getLogger(__name__)


def _safe_url(url: str | None, max_len: int = 60) -> str:
    """Return URL without credentials, truncated for logging/status.

    Strips user:pass from URLs like socks5://user:pass@host:port.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        safe = urlunparse(
            (
                parsed.scheme,
                parsed.hostname or "",
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        if parsed.port:
            safe = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        out = safe[:max_len]
        if len(safe) > max_len:
            out += "…"
        return out
    except Exception:
        return url[:max_len]


# ═══════════════════════════════════════════════════════════════════════════
#  Типы
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ProxyEntry:
    """Запись об одном прокси в пуле."""

    url: str  # e.g. "socks5://user:pass@192.168.1.100:1080"
    type: str  # "mobile" или "static"
    change_ip_url: str | None = None  # URL для смены IP на мобильном прокси
    status: str = "active"  # "active" | "cooldown" | "banned" | "changing"
    last_used: float = 0.0  # timestamp последнего использования
    fail_count: int = 0  # последовательные ошибки
    cooldown_until: float = 0.0  # timestamp до которого прокси на кулдауне


@dataclass
class RotatorStatus:
    """Статус пула прокси для мониторинга."""

    total: int
    active: int
    cooldown: int
    banned: int
    changing: int
    entries: list[dict[str, Any]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
#  Основной класс
# ═══════════════════════════════════════════════════════════════════════════


class ProxyRotator:
    """Управляет пулом прокси с авто-ротацией.

    Использование::

        rotator = ProxyRotator(
            proxies=[
                {"url": "socks5://...", "type": "mobile", "change_ip_url": "http://..."},
            ],
        )
        proxy = await rotator.get_proxy()
        try:
            resp = await fetch(url, proxy=proxy.url)
            await rotator.mark_success(proxy)
        except Exception:
            await rotator.mark_failure(proxy)
    """

    def __init__(
        self,
        proxies: list[dict],
        *,
        cooldown_sec: int = 120,
        max_fails: int = 3,
    ) -> None:
        """Инициализирует ротатор.

        Args:
            proxies: Список словарей с ключами url, type, change_ip_url.
            cooldown_sec: Время кулдауна после блокировки (сек).
            max_fails: Максимум последовательных ошибок до смены IP/кулдауна.
        """
        self._entries: list[ProxyEntry] = []
        self._cooldown_sec = cooldown_sec
        self._max_fails = max_fails
        self._lock = asyncio.Lock()
        self._idx = 0  # round-robin индекс
        self._pending_tasks: set[asyncio.Task] = set()  # tracked rotate tasks

        for p in proxies:
            entry = ProxyEntry(
                url=p["url"],
                type=p.get("type", "static"),
                change_ip_url=p.get("change_ip_url"),
            )
            self._entries.append(entry)

        if self._entries:
            logger.info(
                "ProxyRotator: %d прокси загружено (%d mobile, %d static)",
                len(self._entries),
                sum(1 for e in self._entries if e.type == "mobile"),
                sum(1 for e in self._entries if e.type == "static"),
            )

    # ── Публичный API ──────────────────────────────────────────────────────

    async def get_proxy(self) -> ProxyEntry | None:
        """Получить следующий доступный прокси (round-robin по active).

        Returns:
            ProxyEntry или None если все прокси недоступны.
        """
        async with self._lock:
            now = time.time()
            active = [
                e
                for e in self._entries
                if e.status == "active" and e.cooldown_until <= now
            ]

            if not active:
                # Проверяем, может есть в changing (ждём смены IP)
                changing = [e for e in self._entries if e.status == "changing"]
                if changing:
                    logger.info(
                        "ProxyRotator: ждём смены IP для %d прокси", len(changing)
                    )
                else:
                    # Пробуем разбудить кулдаун-прокси если время вышло
                    for e in self._entries:
                        if e.status == "cooldown" and e.cooldown_until <= now:
                            e.status = "active"
                            e.fail_count = 0
                            logger.info(
                                "ProxyRotator: кулдаун истёк для %s...",
                                _safe_url(e.url, 50),
                            )
                    active = [
                        e
                        for e in self._entries
                        if e.status == "active" and e.cooldown_until <= now
                    ]

            if not active:
                logger.warning("ProxyRotator: нет доступных прокси")
                return None

            # Round-robin
            self._idx = (self._idx + 1) % len(active)
            entry = active[self._idx]
            entry.last_used = now
            return entry

    async def mark_success(self, proxy: ProxyEntry) -> None:
        """Сбросить счётчик ошибок после успешного запроса."""
        async with self._lock:
            proxy.fail_count = 0
            if proxy.status in ("cooldown", "banned"):
                proxy.status = "active"
                proxy.cooldown_until = 0.0

    async def mark_failure(self, proxy: ProxyEntry) -> None:
        """Зафиксировать ошибку. При превышении лимита — сменить IP или кулдаун."""
        async with self._lock:
            proxy.fail_count += 1
            logger.debug(
                "ProxyRotator: ошибка %d/%d для %s...",
                proxy.fail_count,
                self._max_fails,
                _safe_url(proxy.url, 50),
            )

            if proxy.fail_count >= self._max_fails and proxy.status != "changing":
                if proxy.type == "mobile" and proxy.change_ip_url:
                    # Мобильный прокси — пробуем сменить IP
                    proxy.status = "changing"
                    logger.info(
                        "ProxyRotator: запуск смены IP для %s...",
                        _safe_url(proxy.url, 50),
                    )
                    # Запускаем смену IP в фоне, не блокируем
                    task = asyncio.create_task(self._do_rotate_ip(proxy))
                    self._pending_tasks.add(task)
                    task.add_done_callback(self._pending_tasks.discard)
                else:
                    # Статический прокси — кулдаун
                    proxy.status = "cooldown"
                    proxy.cooldown_until = time.time() + self._cooldown_sec
                    logger.warning(
                        "ProxyRotator: кулдаун %d сек для %s...",
                        self._cooldown_sec,
                        _safe_url(proxy.url, 50),
                    )

    async def rotate_ip(self, proxy: ProxyEntry) -> bool:
        """Принудительная смена IP для мобильного прокси.

        Returns:
            True если IP успешно сменён.
        """
        return await self._do_rotate_ip(proxy)

    def status(self) -> RotatorStatus:
        """Статус пула прокси для мониторинга."""
        entries_data = []
        counts = {"active": 0, "cooldown": 0, "banned": 0, "changing": 0}

        for e in self._entries:
            counts[e.status] = counts.get(e.status, 0) + 1
            entries_data.append(
                {
                    "url_preview": _safe_url(e.url, 60),
                    "type": e.type,
                    "status": e.status,
                    "fail_count": e.fail_count,
                    "cooldown_remaining": max(0.0, e.cooldown_until - time.time()),
                }
            )

        return RotatorStatus(
            total=len(self._entries),
            active=counts.get("active", 0),
            cooldown=counts.get("cooldown", 0),
            banned=counts.get("banned", 0),
            changing=counts.get("changing", 0),
            entries=entries_data,
        )

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Graceful shutdown: cancel pending rotate tasks and drain them."""
        async with self._lock:
            tasks = list(self._pending_tasks)
            self._pending_tasks.clear()
        if not tasks:
            return
        for t in tasks:
            t.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning(
                "ProxyRotator: shutdown timed out with %d pending tasks", len(tasks)
            )
        logger.info("ProxyRotator: %d pending rotate tasks drained", len(tasks))

    # ── Приватные методы ───────────────────────────────────────────────────

    async def _do_rotate_ip(self, proxy: ProxyEntry) -> bool:
        """Выполнить HTTP-запрос для смены IP мобильного прокси.

        Все мутации полей proxy внутри выполняются под self._lock
        для защиты от гонки данных с get_proxy().
        """
        if not proxy.change_ip_url:
            logger.warning(
                "ProxyRotator: нет change_ip_url для %s", _safe_url(proxy.url, 50)
            )
            async with self._lock:
                proxy.status = "cooldown"
                proxy.cooldown_until = time.time() + self._cooldown_sec
            return False

        try:
            ssrf_error = await _check_ssrf_async(proxy.change_ip_url)
            if ssrf_error:
                logger.warning(
                    "ProxyRotator: change_ip_url SSRF blocked for %s: %s",
                    _safe_url(proxy.url, 50),
                    ssrf_error.get("error", "unknown"),
                )
                async with self._lock:
                    proxy.status = "cooldown"
                    proxy.cooldown_until = time.time() + self._cooldown_sec
                return False

            import httpx

            async with httpx.AsyncClient(timeout=15.0) as client:
                url = proxy.change_ip_url
                if "?" in url:
                    url += "&format=json"
                else:
                    url += "?format=json"
                resp = await client.get(url)
                if resp.status_code == 200:
                    logger.info(
                        "ProxyRotator: IP успешно сменён для %s...",
                        _safe_url(proxy.url, 50),
                    )
                    async with self._lock:
                        proxy.status = "active"
                        proxy.fail_count = 0
                        proxy.cooldown_until = 0.0
                    return True
                else:
                    logger.warning(
                        "ProxyRotator: ошибка смены IP (HTTP %d) для %s...",
                        resp.status_code,
                        _safe_url(proxy.url, 50),
                    )
                    async with self._lock:
                        proxy.status = "cooldown"
                        proxy.cooldown_until = time.time() + self._cooldown_sec
                    return False
        except asyncio.CancelledError:
            logger.info(
                "ProxyRotator: rotate task cancelled for %s...",
                _safe_url(proxy.url, 50),
            )
            async with self._lock:
                proxy.status = "cooldown"
                proxy.cooldown_until = time.time() + self._cooldown_sec
            raise
        except Exception:
            logger.exception(
                "ProxyRotator: исключение при смене IP для %s...",
                _safe_url(proxy.url, 50),
            )
            async with self._lock:
                proxy.status = "cooldown"
                proxy.cooldown_until = time.time() + self._cooldown_sec
            return False
