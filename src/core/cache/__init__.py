"""Cache package — 3-level smart cache (L0 in-memory → L1 SQLite → L2 Memory.fact).

Public API:
    - ManagedCache, CacheManager, cache_manager (manager.py)
    - SmartCache (smart_cache.py)
    - AdaptiveTTLCache (adaptive.py)
    - PredictivePrefetch, prefetch_tracker (prefetch.py)
"""

from src.core.cache.adaptive import AdaptiveTTLCache
from src.core.cache.manager import CacheManager, ManagedCache, cache_manager
from src.core.cache.prefetch import PredictivePrefetch, prefetch_tracker

__all__ = [
    "AdaptiveTTLCache",
    "CacheManager",
    "ManagedCache",
    "PredictivePrefetch",
    "cache_manager",
    "prefetch_tracker",
]
