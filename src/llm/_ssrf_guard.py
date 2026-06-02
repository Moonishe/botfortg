"""SSRF prevention for provider base_url validation.

Uses ipaddress module to catch IPv4-mapped IPv6, hex/octal/decimal IP
representations, link-local, ULA, and other bypass vectors.

Also resolves domain names via DNS and checks resolved IPs against
private/loopback ranges to prevent DNS rebinding attacks.

.. note::
    This module is kept as a compatibility shim.  The canonical
    implementations now live in ``src.core.security.ssrf_guard``.
    All new code should import from there directly.
"""

from __future__ import annotations

from src.core.security.ssrf_guard import _is_ip_blocked, validate_base_url  # noqa: F401
