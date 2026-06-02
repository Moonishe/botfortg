from src.core.security.ssrf_guard import _check_ssrf


def test_network_guard_blocks_localhost() -> None:
    result = _check_ssrf("https://localhost")

    assert result is not None
    assert "SSRF protection" in result["error"]


def test_network_guard_blocks_private_ip() -> None:
    result = _check_ssrf("https://192.168.1.1")

    assert result is not None
    assert "SSRF protection" in result["error"]


def test_network_guard_allows_public_literal_ip() -> None:
    result = _check_ssrf("https://8.8.8.8")

    assert result is None
