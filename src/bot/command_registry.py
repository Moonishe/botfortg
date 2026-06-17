"""Command Registry — единый реестр всех команд бота.

Авто-генерация ``/help``, ``bot.set_my_commands()`` и описания команд.

Usage::

    from src.bot.command_registry import CommandRegistry, register_all_commands

    registry = CommandRegistry()
    register_all_commands(registry)
    commands = registry.as_telegram_commands()
    await bot.set_my_commands(commands)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from aiogram.types import BotCommand

logger = logging.getLogger(__name__)


@dataclass
class CommandInfo:
    name: str
    description: str
    category: str = "general"


class CommandRegistry:
    """Глобальный реестр команд с авто-генерацией справочных данных."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandInfo] = {}
        self._category_order: list[str] = [
            "general",
            "memory",
            "chat",
            "search",
            "settings",
            "tools",
            "diagnostics",
            "admin",
        ]

    def register(self, name: str, description: str, category: str = "general") -> None:
        """Зарегистрировать команду. Безопасен повторный вызов (перезаписывает)."""
        if not name or not name.strip():
            logger.warning("command_registry.register: empty name, skipping")
            return
        if not description or not description.strip():
            logger.warning(
                "command_registry.register: empty description for /%s, "
                "using placeholder",
                name,
            )
            description = f"Command /{name}"
        self._commands[name] = CommandInfo(
            name=name.strip(), description=description.strip(), category=category
        )

    def get(self, name: str) -> CommandInfo | None:
        return self._commands.get(name)

    def as_telegram_commands(
        self, exclude_categories: tuple[str, ...] | None = None
    ) -> list[BotCommand]:
        """Вернуть список для ``bot.set_my_commands()``.

        Args:
            exclude_categories: Категории команд, которые НЕ показывать
                в меню бота (по умолчанию скрывает ``"admin"`` и
                ``"diagnostics"`` — команды администратора и диагностики).
        """
        if exclude_categories is None:
            exclude_categories = ("admin", "diagnostics")
        # Snapshot via list() — защита от «dictionary changed size during
        # iteration» если register() вызывается из другого потока.
        return [
            BotCommand(command=info.name, description=info.description)
            for info in list(self._commands.values())
            if info.category not in exclude_categories
        ]

    def by_category(self) -> dict[str, list[CommandInfo]]:
        """Сгруппировать команды по категориям."""
        grouped: dict[str, list[CommandInfo]] = defaultdict(list)
        for info in self._commands.values():
            grouped[info.category].append(info)
        return dict(grouped)

    def format_help(self, category: str | None = None) -> str:
        """Сгенерировать текст справки для ``/help``.

        Args:
            category: Если задана — только команды этой категории.
        """
        grouped = self.by_category()
        lines: list[str] = []

        if category and category in grouped:
            lines.append(f"📂 <b>{_category_title(category)}</b>\n")
            for info in grouped[category]:
                lines.append(f"/{info.name} — {info.description}")
            return "\n".join(lines)

        lines.append("🤖 <b>Доступные команды:</b>\n")
        for cat in self._category_order:
            cmds = grouped.get(cat)
            if not cmds:
                continue
            lines.append(f"\n📂 <b>{_category_title(cat)}</b>")
            for info in cmds:
                lines.append(f"  /{info.name} — {info.description}")

        return "\n".join(lines)


def _category_title(cat: str) -> str:
    return {
        "general": "Основное",
        "memory": "Память и факты",
        "chat": "Чаты и синхронизация",
        "search": "Поиск",
        "settings": "Настройки",
        "tools": "Инструменты",
        "diagnostics": "Диагностика",
        "admin": "Администрирование",
    }.get(cat, cat.capitalize())


# ── Registry holder (explicitly initialized by app.py) ──
_registry: CommandRegistry | None = None


def get_registry() -> CommandRegistry:
    """Return the command registry initialized by the application.

    Raises RuntimeError if the registry has not been set up yet.
    """
    if _registry is None:
        raise RuntimeError(
            "CommandRegistry has not been initialized. Call register_all_commands() first."
        )
    return _registry


# ── Авто-регистрация команд (вызывается из main.py/startup) ──


def register_all_commands(registry: CommandRegistry | None = None) -> CommandRegistry:
    """Зарегистрировать все известные команды.

    Args:
        registry: Registry to populate. If not provided, a new one is created
            and stored as the application registry.

    Returns:
        The populated registry.

    Вызывается при запуске бота, после импорта всех handler-модулей.
    Идемпотентна — повторный вызов безопасен.
    """
    global _registry
    if registry is None:
        registry = CommandRegistry()
    _registry = registry

    # ── General ──
    registry.register("help", "Показать справку по командам", "general")
    registry.register("start", "Начать работу / онбординг", "general")
    registry.register("cancel", "Отменить текущее действие", "general")
    registry.register("me", "Информация о профиле", "general")
    registry.register("profile", "Настройки профиля", "general")

    # ── Memory ──
    registry.register("memory", "Просмотр и управление памятью", "memory")
    registry.register("remember", "Запомнить факт вручную", "memory")
    registry.register("forget", "Удалить факт из памяти", "memory")
    registry.register("habits", "Просмотр привычек", "memory")
    registry.register("insights", "Автоматические инсайты", "memory")
    registry.register("conflicts", "Конфликты в памяти", "memory")
    registry.register("warnings", "Предупреждения памяти", "memory")
    registry.register("clusters", "Кластеры памяти", "memory")
    registry.register("archetypes", "Архетипы контактов", "memory")
    registry.register("distill", "Дистилляция памяти", "memory")
    registry.register("instructions", "Постоянные инструкции", "memory")
    registry.register("tag", "Управление тегами", "memory")
    registry.register("persona", "Управление персоной", "memory")

    # ── Chat ──
    registry.register("chat", "Начать диалог с контактом", "chat")
    registry.register("sync", "Синхронизировать диалоги", "chat")
    registry.register("send", "Отправить сообщение", "chat")
    registry.register("recent", "Последние сообщения", "chat")
    registry.register("watchlist", "Список наблюдения", "chat")
    registry.register("contact", "Информация о контакте", "chat")
    registry.register("inbox", "Входящие сообщения", "chat")
    registry.register("greet", "Приветствия контактов", "chat")
    registry.register("threads", "Треды сообщений", "chat")
    registry.register("timeline", "Хронология общения", "chat")

    # ── Search ──
    registry.register("search", "Поиск по сообщениям", "search")
    registry.register("index", "Переиндексировать FTS5", "search")

    # ── Settings ──
    registry.register("settings", "Настройки бота", "settings")
    registry.register("keys", "Управление LLM-ключами", "settings")
    registry.register("models", "Управление моделями", "settings")
    registry.register("login", "Подключить Telegram-аккаунт", "settings")
    registry.register("logout", "Отключить Telegram-аккаунт", "settings")
    registry.register("todos", "Список задач", "settings")
    registry.register("skills", "Управление навыками", "settings")
    registry.register("cron", "Управление cron-задачами", "settings")

    # ── Tools ──
    registry.register("install", "Установка компонентов", "tools")
    registry.register("install_playwright", "Установка Playwright", "tools")
    registry.register("today", "Сводка на сегодня", "tools")
    registry.register("radar", "Радар контактов", "tools")
    registry.register("digest", "Дайджест сообщений", "tools")
    registry.register("briefing", "Брифинг", "tools")
    registry.register("smart_digest", "Умный дайджест", "tools")
    registry.register("weekly", "Недельный отчёт", "tools")

    # ── Diagnostics ──
    registry.register("health", "Проверка здоровья системы", "diagnostics")
    registry.register("monitor", "Мониторинг в реальном времени", "diagnostics")
    registry.register("llm_status", "Статус LLM-провайдеров", "diagnostics")
    registry.register("gates", "Состояние защитных гейтов", "diagnostics")
    registry.register("stats", "Статистика использования", "diagnostics")

    # ── Admin ──
    registry.register("avito", "Поиск на Avito", "admin")
    registry.register("avito_list", "Список поисков Avito", "admin")
    registry.register("avito_remove", "Удалить поиск Avito", "admin")
    return registry
