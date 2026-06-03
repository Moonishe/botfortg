"""Service-layer exceptions.

All service functions raise one of these specific exception types —
never a bare ``Exception`` — so callers can handle validation vs
not-found vs provider errors granularly.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base exception for all service-layer errors."""


class ValidationError(ServiceError):
    """Input data failed validation (bad provider, short key, wrong type, …)."""


class NotFoundError(ServiceError):
    """The requested entity does not exist (user, key slot, memory, …)."""


class ProviderError(ServiceError):
    """An external provider call failed (key validation, API reachability)."""
