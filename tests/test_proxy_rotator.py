"""Тесты ротатора прокси (proxy_rotator)."""

import asyncio
import time

import pytest

from src.core.avito.proxy_rotator import ProxyRotator, ProxyEntry, RotatorStatus


# ═══════════════════════════════════════════════════════════════════════════
#  Фикстуры
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def empty_rotator() -> ProxyRotator:
    """Ротатор без прокси."""
    return ProxyRotator(proxies=[], cooldown_sec=60, max_fails=2)


@pytest.fixture
def single_proxy_rotator() -> ProxyRotator:
    """Ротатор с одним статическим прокси."""
    return ProxyRotator(
        proxies=[{"url": "http://proxy1:8080", "type": "static"}],
        cooldown_sec=60,
        max_fails=2,
    )


@pytest.fixture
def multi_proxy_rotator() -> ProxyRotator:
    """Ротатор с тремя прокси (2 mobile, 1 static)."""
    return ProxyRotator(
        proxies=[
            {
                "url": "socks5://user:pass@mobile1:1080",
                "type": "mobile",
                "change_ip_url": "http://mobile1/change",
            },
            {
                "url": "http://static1:8080",
                "type": "static",
            },
            {
                "url": "socks5://user:pass@mobile2:1080",
                "type": "mobile",
                "change_ip_url": "http://mobile2/change",
            },
        ],
        cooldown_sec=120,
        max_fails=3,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: пустой ротатор
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_empty_rotator_returns_none(empty_rotator: ProxyRotator) -> None:
    """Пустой ротатор должен возвращать None."""
    proxy = await empty_rotator.get_proxy()
    assert proxy is None


@pytest.mark.asyncio
async def test_empty_rotator_status(empty_rotator: ProxyRotator) -> None:
    """Статус пустого ротатора."""
    status = empty_rotator.status()
    assert status.total == 0
    assert status.active == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: один прокси
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_single_proxy_get(single_proxy_rotator: ProxyRotator) -> None:
    """Один прокси должен возвращаться всегда."""
    proxy = await single_proxy_rotator.get_proxy()
    assert proxy is not None
    assert proxy.url == "http://proxy1:8080"
    assert proxy.type == "static"
    assert proxy.status == "active"


@pytest.mark.asyncio
async def test_single_proxy_failure_cooldown(
    single_proxy_rotator: ProxyRotator,
) -> None:
    """После max_fails ошибок прокси должен уйти в cooldown."""
    proxy = await single_proxy_rotator.get_proxy()
    assert proxy is not None

    # Две ошибки (max_fails=2)
    await single_proxy_rotator.mark_failure(proxy)
    await single_proxy_rotator.mark_failure(proxy)

    # После cooldown — прокси недоступен
    next_proxy = await single_proxy_rotator.get_proxy()
    assert next_proxy is None

    status = single_proxy_rotator.status()
    assert status.active == 0
    assert status.cooldown == 1


@pytest.mark.asyncio
async def test_single_proxy_success_resets_fails(
    single_proxy_rotator: ProxyRotator,
) -> None:
    """Успешный запрос сбрасывает счётчик ошибок."""
    proxy = await single_proxy_rotator.get_proxy()
    assert proxy is not None

    # Одна ошибка
    await single_proxy_rotator.mark_failure(proxy)
    assert proxy.fail_count == 1

    # Успех
    await single_proxy_rotator.mark_success(proxy)
    assert proxy.fail_count == 0
    assert proxy.status == "active"


@pytest.mark.asyncio
async def test_single_proxy_cooldown_recovery(
    single_proxy_rotator: ProxyRotator,
) -> None:
    """После cooldown прокси должен восстановиться."""
    proxy = await single_proxy_rotator.get_proxy()
    assert proxy is not None

    # Две ошибки
    await single_proxy_rotator.mark_failure(proxy)
    await single_proxy_rotator.mark_failure(proxy)
    assert proxy.status == "cooldown"

    # Симулируем истечение cooldown
    proxy.cooldown_until = time.time() - 10  # 10 секунд назад

    next_proxy = await single_proxy_rotator.get_proxy()
    assert next_proxy is not None
    assert next_proxy.status == "active"
    assert next_proxy.fail_count == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: несколько прокси
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_round_robin_distribution(
    multi_proxy_rotator: ProxyRotator,
) -> None:
    """Round-robin: последовательные вызовы должны перебирать прокси."""
    urls: set[str] = set()
    for _ in range(10):
        proxy = await multi_proxy_rotator.get_proxy()
        assert proxy is not None
        urls.add(proxy.url)

    # Все 3 прокси были использованы
    assert len(urls) == 3


@pytest.mark.asyncio
async def test_mobile_proxy_changing_on_failure(
    multi_proxy_rotator: ProxyRotator,
) -> None:
    """Мобильный прокси должен перейти в changing при превышении ошибок."""
    # Находим первый мобильный прокси
    proxy = None
    for _ in range(3):
        p = await multi_proxy_rotator.get_proxy()
        if p is not None and p.type == "mobile":
            proxy = p
            break

    if proxy is None:
        pytest.skip("Мобильный прокси не найден")

    # Три ошибки (max_fails=3)
    await multi_proxy_rotator.mark_failure(proxy)
    await multi_proxy_rotator.mark_failure(proxy)
    await multi_proxy_rotator.mark_failure(proxy)

    assert proxy.status == "changing"

    # Статус должен отражать changing
    status = multi_proxy_rotator.status()
    assert status.changing >= 1


@pytest.mark.asyncio
async def test_status_reflects_pool_state(
    multi_proxy_rotator: ProxyRotator,
) -> None:
    """Статус должен отражать состояние пула."""
    status = multi_proxy_rotator.status()
    assert status.total == 3
    assert status.active == 3
    assert status.cooldown == 0
    assert status.changing == 0
    assert status.banned == 0

    # Детальные записи
    assert len(status.entries) == 3
    for entry in status.entries:
        assert "url_preview" in entry
        assert "type" in entry
        assert entry["status"] == "active"
        assert entry["fail_count"] == 0


@pytest.mark.asyncio
async def test_mark_success_revives_cooldown(
    multi_proxy_rotator: ProxyRotator,
) -> None:
    """mark_success должен оживлять cooldown-прокси."""
    proxy = await multi_proxy_rotator.get_proxy()
    assert proxy is not None

    # Имитируем cooldown
    proxy.status = "cooldown"
    proxy.fail_count = 5

    await multi_proxy_rotator.mark_success(proxy)

    assert proxy.status == "active"
    assert proxy.fail_count == 0


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: rotate_ip (мобильный)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rotate_ip_no_url_fallback(single_proxy_rotator: ProxyRotator) -> None:
    """Статический прокси без change_ip_url — rotate_ip должен вернуть False."""
    proxy = ProxyEntry(url="http://test:8080", type="static", change_ip_url=None)
    result = await single_proxy_rotator.rotate_ip(proxy)
    assert result is False
    assert proxy.status == "cooldown"


@pytest.mark.asyncio
async def test_rotate_ip_with_url_attempts_request(
    multi_proxy_rotator: ProxyRotator,
) -> None:
    """Мобильный прокси с change_ip_url — должен попытаться сменить IP."""
    proxy = ProxyEntry(
        url="socks5://test:1080",
        type="mobile",
        change_ip_url="http://localhost:9999/change",
    )
    # Вызовет исключение (сервер не запущен) → cooldown
    result = await multi_proxy_rotator.rotate_ip(proxy)
    assert result is False
    assert proxy.status == "cooldown"


# ═══════════════════════════════════════════════════════════════════════════
#  Тесты: thread-safety (asyncio)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_concurrent_get_proxy(
    multi_proxy_rotator: ProxyRotator,
) -> None:
    """Параллельные вызовы get_proxy не должны падать."""

    async def _get_one() -> ProxyEntry | None:
        return await multi_proxy_rotator.get_proxy()

    results = await asyncio.gather(*[_get_one() for _ in range(10)])
    non_none = [r for r in results if r is not None]
    assert len(non_none) == 10  # Все 3 активны → должно хватить
