"""Bot package — re-exports the application factory.

Note: ``run_bot`` is exported lazily via ``__getattr__``
to avoid a circular import chain (notifier → bot → handlers → core → ...).
"""

__all__ = [
    "resolve_contact_fast",
    "run_bot",
    "start_voice_worker",
    "stop_voice_worker",
]


def __getattr__(name: str):
    if name == "resolve_contact_fast":
        from src.bot.contact_resolver import resolve_contact_fast

        return resolve_contact_fast
    if name == "run_bot":
        from src.bot.app import run_bot

        return run_bot
    if name in ("start_voice_worker", "stop_voice_worker"):
        from src.bot.handlers.free_text_legacy import (
            start_voice_worker,
            stop_voice_worker,
        )

        return start_voice_worker if name == "start_voice_worker" else stop_voice_worker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
