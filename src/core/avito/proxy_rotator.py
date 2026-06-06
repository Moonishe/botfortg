"""Ротатор прокси для мобильной прокси-фермы.

Поддерживает паттерн «2 телефона»: один парсит, второй меняет IP.
Управляет состоянием прокси (active/cooldown/banned/changing),
автоматически ротирует IP при превышении лимита ошибок.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

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
                                e.url[:50],
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
                proxy.url[:50],
            )

            if proxy.fail_count >= self._max_fails:
                if proxy.type == "mobile" and proxy.change_ip_url:
                    # Мобильный прокси — пробуем сменить IP
                    proxy.status = "changing"
                    logger.info(
                        "ProxyRotator: запуск смены IP для %s...", proxy.url[:50]
                    )
                    # Запускаем смену IP в фоне, не блокируем
                    asyncio.create_task(self._do_rotate_ip(proxy))
                else:
                    # Статический прокси — кулдаун
                    proxy.status = "cooldown"
                    proxy.cooldown_until = time.time() + self._cooldown_sec
                    logger.warning(
                        "ProxyRotator: кулдаун %d сек для %s...",
                        self._cooldown_sec,
                        proxy.url[:50],
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
                    "url_preview": e.url[:60] + "..." if len(e.url) > 60 else e.url,
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

    # ── Приватные методы ───────────────────────────────────────────────────

    async def _do_rotate_ip(self, proxy: ProxyEntry) -> bool:
        """Выполнить HTTP-запрос для смены IP мобильного прокси.

        Все мутации полей proxy внутри выполняются под self._lock
        для защиты от гонки данных с get_proxy().
        """
        if not proxy.change_ip_url:
            logger.warning("ProxyRotator: нет change_ip_url для %s", proxy.url[:50])
            async with self._lock:
                proxy.status = "cooldown"
                proxy.cooldown_until = time.time() + self._cooldown_sec
            return False

        try:
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
                        "ProxyRotator: IP успешно сменён для %s...", proxy.url[:50]
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
                        proxy.url[:50],
                    )
                    async with self._lock:
                        proxy.status = "cooldown"
                        proxy.cooldown_until = time.time() + self._cooldown_sec
                    return False
        except Exception:
            logger.exception(
                "ProxyRotator: исключение при смене IP для %s...", proxy.url[:50]
            )
            async with self._lock:
                proxy.status = "cooldown"
                proxy.cooldown_until = time.time() + self._cooldown_sec
            return False
