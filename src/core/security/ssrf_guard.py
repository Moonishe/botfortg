"""SSRF (Server-Side Request Forgery) protection — canonical module.

Consolidates all SSRF guard logic that was previously scattered across:
- ``src/core/actions/mcp_http.py`` (``_check_ssrf`` + blocklist)
- ``src/core/actions/mcp_web.py`` (duplicated ``_check_ssrf``)
- ``src/llm/_ssrf_guard.py`` (``validate_base_url`` + ``_is_ip_blocked``)

This module is the **single source of truth** for SSRF prevention.

Exports
-------
``_check_ssrf(url) -> dict[str, Any] | None``
    Returns an error dict if *url* targets a blocked/reserved host,
    otherwise ``None``.  Used by MCP tools (mcp_http, mcp_web,
    mcp_playwright) to reject dangerous requests before they hit the wire.

``_is_ip_blocked(ip_str) -> str | None``
    Low-level helper — checks whether an IP string falls in a blocked
    range.  Returns a human-readable reason string or ``None``.

``validate_base_url(url) -> str | None``
    Provider-side validation used by LLM providers (openai, anthropic,
    deepseek, groq, etc.) to prevent SSRF via misconfigured base_url.
    Raises ``ValueError`` on blocked URLs; returns the URL unchanged on
    success.  Accepts ``None`` (passed through unchanged).

``_SSRF_BLOCKED_HOSTS``
    A ``frozenset[str]`` of hostnames that are unconditionally blocked
    (e.g. ``"localhost"``, ``"127.0.0.1"``, ``"::1"``,
    ``"169.254.169.254"``).

Implementation notes
--------------------
- Both ``_check_ssrf`` and ``validate_base_url`` resolve hostnames via
  ``socket.getaddrinfo`` to prevent DNS rebinding attacks.
- All private / loopback / link-local / reserved / unspecified IP
  ranges are blocked for both IPv4 and IPv6.
- IPv4-mapped IPv6 addresses (``::ffff:x.x.x.x``) are also checked
  against the mapped IPv4 address.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# Blocklist — hostnames that are *always* denied before DNS resolution
# ══════════════════════════════════════════════════════════════════════════

_SSRF_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "[::1]",
        "169.254.169.254",
    }
)

# ══════════════════════════════════════════════════════════════════════════
# Shared helper — parse URL, check blocklist + non‑standard IP notation
# ══════════════════════════════════════════════════════════════════════════


def _is_nonstandard_ip_notation(hostname: str) -> str | None:
    """Return error reason if *hostname* is non-standard IP notation, else None.

    Detects hex (``0x7f000001``), octal (``0127``), and decimal-long
    (``2130706433``) IP encodings that could bypass naive blocklists.
    Legitimate domain names (``0x.org``) are not blocked — the hex check
    requires ALL remaining characters to be hex digits.
    """
    if not hostname:
        return None
    hostname_stripped = hostname.strip("[]")
    if (
        (
            hostname_stripped.startswith(("0x", "0X"))
            and all(c in "0123456789abcdefABCDEF" for c in hostname_stripped[2:])
        )
        or (
            hostname_stripped.startswith("0")
            and hostname_stripped.isdigit()
            and len(hostname_stripped) > 1
        )
        or (
            hostname_stripped.isdigit()
            and len(hostname_stripped) >= 10
            and int(hostname_stripped) <= 0xFFFFFFFF
        )
    ):
        return (
            f"Non-standard IP notation detected: {hostname!r}. "
            f"Use standard dotted-decimal or hostname."
        )
    return None


def _parse_and_prefilter_url(url: str) -> tuple[str, dict[str, Any] | None]:
    """Parse URL, check blocklist and hex/octal/decimal notation.

    Returns
    -------
    ``(hostname, error_dict_or_None)``
        If the URL is parseable and passes all pre‑filter checks,
        *error_dict_or_None* is ``None`` and the caller should proceed
        to DNS resolution.
        Otherwise an error dict is returned with an ``"error"`` key.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return ("", {"error": f"Cannot parse URL: {exc}"})

    hostname = parsed.hostname or ""

    # Direct match against blocklist
    if hostname.lower() in _SSRF_BLOCKED_HOSTS:
        return (
            hostname,
            {
                "error": (
                    f"SSRF protection: requests to {hostname!r} are not allowed. "
                    f"Use an external URL instead."
                )
            },
        )

    # Detect hex/octal/decimal IP notation before DNS resolution
    if hostname:
        _notation_error = _is_nonstandard_ip_notation(hostname)
        if _notation_error:
            return (hostname, {"error": f"SSRF protection: {_notation_error}"})

    return (hostname, None)


# ══════════════════════════════════════════════════════════════════════════
# _check_ssrf — full SSRF validation (used by MCP tools)
# ══════════════════════════════════════════════════════════════════════════


def _check_ssrf(url: str) -> dict[str, Any] | None:
    """Return an error dict if *url* targets a blocked host, else ``None``.

    Resolves the hostname to an IP first (prevents DNS rebinding attacks),
    then checks against blocklists for:
    - ``localhost`` and all variants (``127.0.0.1``, ``0.0.0.0``, ``::1``).
    - Private / link-local / reserved IP ranges (``10.x.x.x``,
      ``172.16-31.x.x``, ``192.168.x.x``, ``169.254.x.x``,
      ``127.x.x.x``, ``255.255.255.255``).
    - IPv6 loopback (``::1``), link-local (``fe80::/10``), ULA (``fc00::/7``).
    - AWS metadata endpoint (``169.254.169.254``).
    - IPv4-mapped IPv6 addresses that resolve to private IPv4.

    DNS rebinding limitation: this function resolves the hostname once and
    validates resolved IPs. By the time the HTTP client opens its own
    connection, DNS could have been repointed to a different IP. For
    high-security deployments, use a pre-resolved IP scheme + custom
    Host header, or a sandbox/proxy with outbound ACLs.
    """
    hostname, error = _parse_and_prefilter_url(url)
    if error:
        return error

    # Resolve DNS first — prevents rebinding attacks
    # Uses getaddrinfo for full IPv4 + IPv6 support
    try:
        addrinfo = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return {"error": f"SSRF protection: cannot resolve hostname {hostname!r}."}

    for _family, _, _, _, sockaddr in addrinfo:
        ip_addr = str(sockaddr[0])
        reason = _is_ip_blocked(ip_addr)
        if reason:
            return {
                "error": (
                    f"SSRF protection: requests to {hostname!r} "
                    f"(resolved to {ip_addr!r}, {reason}) are not allowed."
                )
            }

    return None


# ══════════════════════════════════════════════════════════════════════════
# _is_ip_blocked — low-level IP check (used by validate_base_url)
# ══════════════════════════════════════════════════════════════════════════


def _is_ip_blocked(ip_str: str) -> str | None:
    """Check if an IP string is in a blocked range.

    Returns a human-readable reason string (e.g. ``"loopback"``,
    ``"private network"``) if the IP is blocked, or ``None`` if it is
    safe.

    Handles:
    - Loopback (``127.0.0.0/8``, ``::1``)
    - Private networks (``10.0.0.0/8``, ``172.16.0.0/12``,
      ``192.168.0.0/16``, ``fc00::/7``)
    - Link-local (``169.254.0.0/16``, ``fe80::/10``)
    - Unspecified (``0.0.0.0``, ``::``)
    - IPv4-mapped IPv6 addresses that resolve to private/loopback
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return None

    if ip.is_loopback:
        return "loopback"
    if ip.is_private:
        return "private network"
    if ip.is_link_local:
        return "link-local"
    if ip.is_unspecified:
        return "unspecified address"
    if ip.is_reserved:
        return "reserved address"
    if ip.is_multicast:
        return "multicast address"
    # IPv4-mapped IPv6 (::ffff:x.x.x.x)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        mapped = ip.ipv4_mapped
        if (
            mapped.is_loopback
            or mapped.is_private
            or mapped.is_link_local
            or mapped.is_unspecified
        ):
            return "IPv6-mapped private/loopback"
    return None


async def _check_ssrf_async(url: str) -> dict[str, Any] | None:
    """Async wrapper: same as ``_check_ssrf`` but non‑blocking DNS.

    Use this from ``async def`` callers (MCP tools) to avoid blocking the
    asyncio event loop on ``socket.getaddrinfo``.  The sync ``_check_ssrf``
    remains available for sync code paths (startup, constructors).
    """
    hostname, error = _parse_and_prefilter_url(url)
    if error:
        return error

    try:
        addrinfo = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    except socket.gaierror:
        return {"error": f"SSRF protection: cannot resolve hostname {hostname!r}."}

    for _family, _, _, _, sockaddr in addrinfo:
        ip_addr = str(sockaddr[0])
        reason = _is_ip_blocked(ip_addr)
        if reason:
            return {
                "error": (
                    f"SSRF protection: requests to {hostname!r} "
                    f"(resolved to {ip_addr!r}, {reason}) are not allowed."
                )
            }

    return None


# ══════════════════════════════════════════════════════════════════════════
# validate_base_url — provider-side SSRF guard (used by LLM providers)
# ══════════════════════════════════════════════════════════════════════════


def validate_base_url(url: str | None) -> str | None:
    """Validate base_url to prevent SSRF to internal networks.

    Raises ``ValueError`` if the URL targets a private / loopback /
    link-local address.
    Also resolves domain names via DNS and checks all resolved IPs.

    Returns the URL unchanged if valid.  Passes through ``None``
    unchanged.

    This is the validation used by all LLM providers (OpenAI, Anthropic,
    DeepSeek, Groq, etc.) when constructing their API clients.
    """
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid URL scheme: {parsed.scheme!r} (only http/https allowed)"
        )
    hostname = (parsed.hostname or "").lower()

    # Plain hostname blocklist
    if hostname in ("localhost",):
        raise ValueError("Localhost endpoints not allowed")

    # Defense-in-depth: block hex/octal/decimal IP notation
    _notation_error = _is_nonstandard_ip_notation(hostname)
    if _notation_error:
        raise ValueError(_notation_error)

    # Try to parse as IP address — catches dotted, IPv6-mapped
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # hostname is a domain name — resolve via DNS and check all IPs
        try:
            addrinfos = socket.getaddrinfo(
                hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
            )
            for _family, _, _, _, sockaddr in addrinfos:
                ip_str = str(sockaddr[0])
                reason = _is_ip_blocked(ip_str)
                if reason:
                    raise ValueError(
                        f"Domain {hostname} resolves to {ip_str} ({reason}). "
                        f"DNS rebinding attack or misconfigured endpoint."
                    )
        except socket.gaierror:
            # DNS resolution failed — domain doesn't exist, let connection fail later
            pass
        return url

    # Direct IP checks
    reason = _is_ip_blocked(hostname)
    if reason:
        raise ValueError(f"Endpoint {hostname} not allowed ({reason})")

    return url
