"""Async utilities — shared helpers for offloading blocking I/O to thread pool."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any


async def run_in_thread(func, *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous function in a thread pool executor (offloads blocking I/O).

    Usage::

        result = await run_in_thread(socket.getaddrinfo, host, 80)
        digest = await run_in_thread(hashlib.sha256, data).hexdigest()
        content = await run_in_thread(Path.read_text, path)

    This is a thin wrapper around ``loop.run_in_executor(None, ...)`` that
    handles ``**kwargs`` via ``functools.partial`` transparently.
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))
    return await loop.run_in_executor(None, func, *args)
