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
from typing import TYPE_CHECKING

from aiogram.filters import Command
from aiogram.types import BotCommand

if TYPE_CHECKING:
    from aiogram import Dispatcher, Router

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
        self,
        exclude_categories: tuple[str, ...] | None = None,
        *,
        include_admin: bool = False,
    ) -> list[BotCommand]:
        """Вернуть список для ``bot.set_my_commands()``.

        Args:
            exclude_categories: Категории команд, которые НЕ показывать
                в меню бота. Если не заданы — скрывает ``"admin"`` и
                ``"diagnostics"``.
            include_admin: Если ``True`` — не исключать admin-категорию
                (для тестов/внутреннего использования).
        """
        if exclude_categories is None:
            exclude_categories = () if include_admin else ("admin", "diagnostics")
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

    def validate_against_routers(self, dp: Dispatcher) -> list[str]:
        """Collect commands from all routers and compare against registry.

        Returns commands present in routers but missing from the registry.
        Warning-only — does not raise or block startup.
        """
        missing: list[str] = []

        def _collect_commands(router: Router) -> set[str]:
            cmds: set[str] = set()
            handler_groups = (
                router.message.handlers,
                router.callback_query.handlers,
                router.inline_query.handlers,
                router.edited_message.handlers,
                router.channel_post.handlers,
                router.edited_channel_post.handlers,
            )
            for handlers in handler_groups:
                for handler in handlers:
                    for f in handler.filters or ():
                        if isinstance(f, Command):
                            cmds.update(f.commands)
            for sub in router.sub_routers:
                cmds |= _collect_commands(sub)
            return cmds

        router_commands = _collect_commands(dp)
        for cmd_name in sorted(router_commands):
            if cmd_name not in self._commands:
                missing.append(cmd_name)

        # Reverse check: commands in registry but missing from routers
        # Uses debug (not warning) — some commands use F.text/MagicFilter instead of Command()
        for cmd_name in sorted(self._commands):
            if cmd_name not in router_commands:
                logger.debug(
                    "Command /%s in registry but no Command() filter in routers "
                    "(may use F.text or other filter)",
                    cmd_name,
                )

        return missing


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
    registry.register("ask", "Задать вопрос AI", "general")
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
    registry.register("mem_heatmap", "Тепловая карта уверенности", "memory")
    registry.register("mem_expire", "Истекающие факты", "memory")
    registry.register("mem_export", "Экспорт памяти в JSON", "memory")
    registry.register("mem_similar", "Семантический поиск по памяти", "memory")
    registry.register("mem_working", "Рабочая память (TTL)", "memory")
    registry.register("mem_decay", "Граф удержания памяти", "memory")
    registry.register("mem_dedup", "Поиск дубликатов фактов", "memory")
    registry.register("mem_importance", "Изменить важность факта", "memory")
    registry.register("mem_tags", "Список тегов памяти", "memory")

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
    registry.register("catchup", "Где остановились с контактом", "chat")
    registry.register("style", "Стиль общения с контактом", "chat")

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
    registry.register("mode", "Режим работы бота", "settings")

    # ── Tools ──
    registry.register("install", "Установка компонентов", "tools")
    registry.register("install_playwright", "Установка Playwright", "tools")
    registry.register("today", "Сводка на сегодня", "tools")
    registry.register("radar", "Радар контактов", "tools")
    registry.register("digest", "Дайджест сообщений", "tools")
    registry.register("briefing", "Брифинг", "tools")
    registry.register("smart_digest", "Умный дайджест", "tools")
    registry.register("weekly", "Недельный отчёт", "tools")
    registry.register("followup", "Follow-up предложения", "tools")
    registry.register("intention", "Намерение дня", "tools")
    registry.register("birthdays", "Дни рождения", "tools")
    registry.register("summarize", "Пересказ веб-страницы", "tools")
    registry.register("translate", "Перевод текста", "tools")
    registry.register("currency", "Конвертация валют", "tools")
    registry.register("analyze", "Анализ сообщения", "tools")
    registry.register("docs", "Документация по API", "tools")
    registry.register("explain", "Объяснить концепцию", "tools")
    registry.register("humanize", "Анализ AI-шаблонности", "tools")
    registry.register("news", "Новостной дайджест", "tools")
    registry.register("news_channels", "Источники новостей", "tools")
    registry.register("news_topics", "Темы авто-новостей", "tools")
    registry.register("pubmed", "Поиск в PubMed", "tools")
    registry.register("pubmed_abstract", "Реферат статьи PubMed", "tools")
    registry.register("pubmed_full", "Полный текст PubMed", "tools")
    registry.register("wiki", "Поиск в Wikipedia", "tools")

    # ── Diagnostics ──
    registry.register("health", "Проверка здоровья системы", "diagnostics")
    registry.register("ops", "Единый операционный дашборд", "diagnostics")
    registry.register("monitor", "Мониторинг в реальном времени", "diagnostics")
    registry.register("llm_status", "Статус LLM-провайдеров", "diagnostics")
    registry.register("gates", "Состояние защитных гейтов", "diagnostics")
    registry.register("stats", "Статистика использования", "diagnostics")
    registry.register("sessions", "Управление сессиями", "diagnostics")
    registry.register("trajectory", "Траектория использования", "diagnostics")
    registry.register("evolve", "Эволюция модели", "diagnostics")
    registry.register("thinking", "Последний запрос бота", "diagnostics")
    registry.register("graph", "Граф знаний", "diagnostics")
    registry.register("entities", "Сущности памяти", "diagnostics")
    registry.register("confidence", "Статистика уверенности", "diagnostics")
    registry.register("dreams", "Журнал снов", "diagnostics")
    registry.register("contact_health", "Здоровье контакта", "diagnostics")
    registry.register("memory_growth", "Рост памяти по дням", "diagnostics")

    # ── Planning+Reminders (Batch 1) ──
    registry.register("nlcron", "Задача через естественный язык", "planning")
    registry.register("smart_reminder", "Напоминание с контекстом", "planning")
    registry.register("meeting_prep", "Подготовка к встрече", "planning")
    registry.register("topics", "Темы для обсуждения", "planning")
    registry.register("calendar", "Ближайшие события", "planning")
    registry.register("nudge_timing", "Лучшее время напоминаний", "planning")

    # ── Auto-reply extensions ──
    registry.register("away", "Статус отсутствия", "auto-reply")
    registry.register("templates", "Шаблоны ответов", "auto-reply")
    registry.register("per_contact_emoji", "Эмодзи для контакта", "auto-reply")

    # ── Analytics extensions ──
    registry.register("stats", "Общая статистика", "analytics")
    registry.register("tokens", "Использование токенов", "analytics")
    registry.register("quality", "Качество ответов", "analytics")
    registry.register("tool_heatmap", "Топ инструментов", "analytics")
    registry.register("conv_depth", "Глубина диалогов", "analytics")

    # ── Auto-reply Batch 2 ──
    registry.register("personality", "Персона авто-ответов", "auto-reply")
    registry.register("contact_personality", "Персона для контакта", "auto-reply")
    registry.register("greeting", "Приветствие авто-ответа", "auto-reply")
    registry.register("auto_reply_stats", "Статистика авто-ответов", "auto-reply")
    registry.register("schedule_reply", "Расписание авто-ответов", "auto-reply")

    # ── Tools Batch 3 ──
    registry.register("url_summary", "Пересказ веб-страницы", "tools")
    registry.register("weather_clothing", "Погода + одежда", "tools")
    registry.register("sticker_search", "Поиск стикеров", "tools")

    # ── Batch 4: Tools + Analytics + Auto-reply toggles ──
    registry.register("code", "Выполнить код", "tools")
    registry.register("pdf", "Текст из PDF", "tools")
    registry.register("ocr", "Распознать текст с фото", "tools")
    registry.register("skill_stats", "Эффективность навыков", "analytics")
    registry.register("reply_quality", "Статистика авто-ответов", "analytics")
    registry.register("reaction_reply", "Reaction auto-reply toggle", "auto-reply")
    registry.register("typing_sim", "Typing simulation toggle", "auto-reply")
    registry.register("read_receipts", "Read receipts toggle", "auto-reply")
    registry.register("custom_tool", "Создать навык", "tools")

    # ── Admin ──
    registry.register("avito", "Поиск на Avito", "admin")
    registry.register("avito_list", "Список поисков Avito", "admin")
    registry.register("avito_remove", "Удалить поиск Avito", "admin")
    registry.register("approve", "Подтвердить запрос доступа", "admin")
    registry.register("revoke", "Отозвать доступ", "admin")
    registry.register("pending", "Ожидающие запросы", "admin")
    registry.register("audit", "Аудит безопасности", "admin")
    return registry
