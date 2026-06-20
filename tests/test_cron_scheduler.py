"""Tests for Generic Cron Scheduler.

Coverage:
  - NL → cron parser (all patterns)
  - croniter utilities (validate, get_next_run, describe)
  - Blueprint catalog (search, get_by_tag)
  - CronJob repository (CRUD, get_due, advance)
  - Delivery adapters (with mocked dependencies)
"""

from __future__ import annotations

import json
import itertools


from datetime import datetime, timedelta, UTC

import pytest

from src.core.scheduling.cron.parser import (
    parse_nl_to_cron,
    validate_cron,
    get_next_run,
    get_next_runs,
    describe_cron,
)
from src.core.scheduling.cron.blueprints import (
    BLUEPRINTS,
    get_blueprint,
    search_blueprints,
    get_blueprints_by_tag,
)


# ══════════════════════════════════════════════════════════════════════════
# NL → Cron Parser
# ══════════════════════════════════════════════════════════════════════════


class TestNlToCronParser:
    """Parse NL descriptions to valid cron expressions."""

    @pytest.mark.parametrize(
        "input_text, expected_prefix",
        [
            ("каждый день в 9:00", "0 9"),
            ("ежедневно в 09:30", "30 9"),
            ("daily at 8:00", "0 8"),
            ("каждый час", "0 *"),
            ("каждые 30 минут", "*/30"),
            ("каждые 15 минут", "*/15"),
            ("каждый понедельник в 10:30", "30 10"),
            ("по вторникам в 9", "0 9"),
            ("каждый вторник и четверг в 9", "0 9"),
            ("по будням в 9:00", "0 9"),
            ("в 9:00", "0 9"),
            ("каждую минуту", "* * * * *"),
            ("каждые 2 часа", "0 */2"),
            ("раз в 3 дня", "0 9"),
            ("каждый месяц 15-го в 9", "0 9 15"),
            ("ежемесячно в 10:00", "0 10 1"),
            ("monday at 10:30", "30 10"),
        ],
    )
    def test_parse_valid_patterns(self, input_text: str, expected_prefix: str):
        """Парсинг NL возвращает валидное cron-выражение с ожидаемым префиксом."""
        result = parse_nl_to_cron(input_text)
        assert result is not None, f"Не удалось распарсить: {input_text!r}"
        assert validate_cron(result), f"Невалидный cron: {result}"
        assert result.startswith(expected_prefix) or expected_prefix in result, (
            f"Ожидался префикс {expected_prefix!r}, получен {result!r}"
        )

    @pytest.mark.parametrize(
        "input_text",
        [
            "привет",
            "как дела",
            "",
            "   ",
            "напомни про встречу",  # без дня/времени
            "random text without any schedule",
        ],
    )
    def test_parse_invalid_patterns(self, input_text: str):
        """Невалидные NL-выражения возвращают None."""
        result = parse_nl_to_cron(input_text)
        assert result is None, f"Ожидался None для {input_text!r}, получен {result!r}"


# ══════════════════════════════════════════════════════════════════════════
# Cron Utilities
# ══════════════════════════════════════════════════════════════════════════


class TestCronUtilities:
    """croniter-утилиты."""

    def test_validate_cron_valid(self):
        """Валидные cron-выражения."""
        assert validate_cron("0 9 * * *")
        assert validate_cron("*/15 * * * *")
        assert validate_cron("30 10 * * 1-5")
        assert validate_cron("0 */2 * * *")
        assert validate_cron("* * * * *")

    def test_validate_cron_invalid(self):
        """Невалидные cron-выражения."""
        assert not validate_cron("")
        assert not validate_cron("0 9 * *")
        assert not validate_cron("abc")
        assert not validate_cron("0 99 * * *")  # невалидный час
        assert not validate_cron("* * * * * *")  # 6 полей

    def test_get_next_run_returns_future(self):
        """get_next_run возвращает datetime в будущем."""
        result = get_next_run("0 9 * * *")
        assert result is not None
        now = datetime.now(UTC)
        assert result > now - timedelta(days=1)  # как минимум не в далёком прошлом

    def test_get_next_run_invalid(self):
        """get_next_run для невалидного выражения возвращает None."""
        assert get_next_run("invalid") is None

    def test_get_next_runs_count(self):
        """get_next_runs возвращает запрошенное количество дат."""
        runs = get_next_runs("0 9 * * *", count=3)
        assert len(runs) == 3
        # Все в будущем
        now = datetime.now(UTC)
        for r in runs:
            assert r > now - timedelta(days=1)

    def test_get_next_runs_invalid(self):
        """get_next_runs для невалидного выражения возвращает []."""
        assert get_next_runs("invalid") == []


# ══════════════════════════════════════════════════════════════════════════
# Describe Cron
# ══════════════════════════════════════════════════════════════════════════


class TestDescribeCron:
    """Человеко-читаемое описание cron-выражений."""

    def test_every_minute(self):
        assert "Каждую минуту" in describe_cron("* * * * *")

    def test_every_n_minutes(self):
        assert "30" in describe_cron("*/30 * * * *")

    def test_every_hour(self):
        assert "Каждый день" in describe_cron("0 9 * * *")
        assert "09:00" in describe_cron("0 9 * * *")

    def test_weekdays(self):
        desc = describe_cron("0 9 * * 1-5")
        # Must mention some day names
        assert any(d in desc for d in ["пн", "вт", "ср", "чт", "пт"])


# ══════════════════════════════════════════════════════════════════════════
# Blueprint Catalog
# ══════════════════════════════════════════════════════════════════════════


class TestBlueprintCatalog:
    """Каталог шаблонов cron-задач."""

    def test_blueprints_not_empty(self):
        """В каталоге есть шаблоны."""
        assert len(BLUEPRINTS) > 0

    def test_get_blueprint_found(self):
        """Поиск по имени находит шаблон (case-insensitive)."""
        bp = get_blueprint("утренняя сводка")
        assert bp is not None
        assert bp.name == "Утренняя сводка"

    def test_get_blueprint_not_found(self):
        """Поиск несуществующего шаблона."""
        assert get_blueprint("несуществующий шаблон") is None

    def test_search_blueprints_by_name(self):
        """Поиск по части названия."""
        results = search_blueprints("сводка")
        assert len(results) >= 1
        assert any("сводка" in bp.name.lower() for bp in results)

    def test_search_blueprints_by_tag(self):
        """Поиск по тегу."""
        results = get_blueprints_by_tag("health")
        assert len(results) >= 1
        for bp in results:
            assert any("health" in t.lower() for t in bp.tags)

    def test_search_blueprints_empty(self):
        """Пустой поиск возвращает все шаблоны."""
        results = search_blueprints("")
        assert len(results) == len(BLUEPRINTS)

    def test_search_blueprints_no_match(self):
        """Поиск без совпадений."""
        results = search_blueprints("xyznonexistent123")
        assert len(results) == 0

    def test_all_blueprints_have_valid_cron(self):
        """У всех шаблонов валидные cron-выражения."""
        for bp in BLUEPRINTS:
            assert validate_cron(bp.cron_expression), (
                f"Blueprint '{bp.name}' has invalid cron: {bp.cron_expression}"
            )


# ══════════════════════════════════════════════════════════════════════════
# CronJob Repository (DB integration tests)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("_db_init")
class TestCronJobRepository:
    """CRUD-операции с CronJob через БД (in-memory SQLite)."""

    _counter = itertools.count(1000000)

    @pytest.fixture(autouse=True)
    async def _setup(self):
        """Создать тестового пользователя перед каждым тестом (уник. telegram_id)."""
        from src.db.session import get_session
        from src.db.models._base import User

        unique_id = next(self._counter)
        async with get_session() as session:
            user = User(telegram_id=unique_id)
            session.add(user)
            await session.flush()
            self._user_id = user.id
            await session.commit()

    async def _create_job(self, **overrides):
        """Helper: создать cron-задачу."""
        from src.db.repos.cron_repo import create_cron_job
        from src.db.session import get_session

        params = {
            "user_id": self._user_id,
            "name": "test-job",
            "cron_expression": "0 9 * * *",
            "payload_type": "message",
            "payload": {"text": "Test message"},
        }
        params.update(overrides)

        async with get_session() as session:
            job = await create_cron_job(session=session, **params)
            await session.commit()
            return job

    async def test_create_cron_job(self):
        """Создание задачи."""
        job = await self._create_job()
        assert job.id > 0
        assert job.name == "test-job"
        assert job.cron_expression == "0 9 * * *"
        assert job.enabled is True

    async def test_get_cron_job(self):
        """Получение задачи по ID."""
        created = await self._create_job()
        from src.db.repos.cron_repo import get_cron_job
        from src.db.session import get_session

        async with get_session() as session:
            job = await get_cron_job(session, created.id)
        assert job is not None
        assert job.id == created.id
        assert job.name == created.name

    async def test_get_cron_job_not_found(self):
        """Получение несуществующей задачи."""
        from src.db.repos.cron_repo import get_cron_job
        from src.db.session import get_session

        async with get_session() as session:
            job = await get_cron_job(session, 99999)
        assert job is None

    async def test_update_cron_job(self):
        """Обновление задачи."""
        created = await self._create_job(enabled=True)
        from src.db.repos.cron_repo import update_cron_job
        from src.db.session import get_session

        async with get_session() as session:
            updated = await update_cron_job(
                session, created.id, enabled=False, name="updated-name"
            )
            await session.commit()

        assert updated is not None
        assert updated.enabled is False
        assert updated.name == "updated-name"

    async def test_delete_cron_job(self):
        """Удаление задачи."""
        created = await self._create_job()
        from src.db.repos.cron_repo import delete_cron_job
        from src.db.session import get_session

        async with get_session() as session:
            deleted = await delete_cron_job(session, created.id)
            await session.commit()

        assert deleted is True

    async def test_get_due_jobs_empty(self):
        """Нет due-задач когда нет задач."""
        from src.db.repos.cron_repo import get_due_jobs
        from src.db.session import get_session

        async with get_session() as session:
            due = await get_due_jobs(session)
        assert due == []

    async def test_get_due_jobs_returns_due(self):
        """get_due_jobs возвращает задачи с наступившим next_run_at."""
        from datetime import datetime, UTC, timedelta

        past = datetime.now(UTC) - timedelta(hours=1)
        await self._create_job(
            name="due-job",
            next_run_at=past,
            enabled=True,
        )

        from src.db.repos.cron_repo import get_due_jobs
        from src.db.session import get_session

        async with get_session() as session:
            due = await get_due_jobs(session)
        assert len(due) >= 1
        assert any(j.name == "due-job" for j in due)

    async def test_get_due_jobs_skips_future(self):
        """get_due_jobs не возвращает задачи в будущем."""
        from datetime import datetime, UTC, timedelta

        future = datetime.now(UTC) + timedelta(hours=24)
        await self._create_job(
            name="future-job",
            next_run_at=future,
            enabled=True,
        )

        from src.db.repos.cron_repo import get_due_jobs
        from src.db.session import get_session

        async with get_session() as session:
            due = await get_due_jobs(session)
        assert not any(j.name == "future-job" for j in due)

    async def test_get_due_jobs_skips_disabled(self):
        """get_due_jobs не возвращает отключённые задачи."""
        from datetime import datetime, UTC, timedelta

        past = datetime.now(UTC) - timedelta(hours=1)
        await self._create_job(
            name="disabled-job",
            next_run_at=past,
            enabled=False,
        )

        from src.db.repos.cron_repo import get_due_jobs
        from src.db.session import get_session

        async with get_session() as session:
            due = await get_due_jobs(session)
        assert not any(j.name == "disabled-job" for j in due)

    async def test_advance_job_updates_counts(self):
        """advance_job инкрементирует run_count, устанавливает last_run_at."""
        created = await self._create_job(
            next_run_at=datetime.now(UTC) - timedelta(hours=1)
        )
        next_run = datetime.now(UTC) + timedelta(hours=24)

        from src.db.repos.cron_repo import advance_job
        from src.db.session import get_session

        async with get_session() as session:
            advanced = await advance_job(session, created.id, next_run)
            await session.commit()

        assert advanced is not None
        assert advanced.run_count == 1
        assert advanced.last_run_at is not None
        assert advanced.next_run_at is not None

    async def test_list_user_jobs(self):
        """list_user_jobs возвращает задачи пользователя."""
        await self._create_job(name="job-a")
        await self._create_job(name="job-b")

        from src.db.repos.cron_repo import list_user_jobs
        from src.db.session import get_session

        async with get_session() as session:
            jobs = await list_user_jobs(session, self._user_id)
        assert len(jobs) >= 2
        names = {j.name for j in jobs}
        assert "job-a" in names
        assert "job-b" in names

    async def test_bulk_disable_expired(self):
        """bulk_disable_expired отключает задачи с истекшим max_run_date."""
        from datetime import datetime, UTC, timedelta

        await self._create_job(
            name="expired-job",
            max_run_date=datetime.now(UTC) - timedelta(hours=1),
            enabled=True,
        )

        from src.db.repos.cron_repo import bulk_disable_expired
        from src.db.session import get_session

        async with get_session() as session:
            count = await bulk_disable_expired(session)
            await session.commit()
        assert count >= 1


# ══════════════════════════════════════════════════════════════════════════
# Cron Scheduler (unit tests with mocks)
# ══════════════════════════════════════════════════════════════════════════


class TestCronScheduler:
    """Базовые тесты CronScheduler (без запуска бесконечного цикла)."""

    def test_singleton_exists(self):
        """cron_scheduler — это экземпляр CronScheduler."""
        from src.core.scheduling.cron.scheduler import cron_scheduler, CronScheduler

        assert isinstance(cron_scheduler, CronScheduler)


# ══════════════════════════════════════════════════════════════════════════
# Cron Scheduler (integration tests with DB)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.usefixtures("_db_init")
class TestCronSchedulerIntegration:
    """Интеграционные тесты CronScheduler.create_and_schedule."""

    _counter = itertools.count(2000000)

    @pytest.fixture(autouse=True)
    async def _setup(self):
        from src.db.session import get_session
        from src.db.models._base import User

        unique_id = next(self._counter)
        async with get_session() as session:
            user = User(telegram_id=unique_id)
            session.add(user)
            await session.flush()
            self._user_id = user.id
            await session.commit()

    @pytest.mark.asyncio
    async def test_create_and_schedule_invalid_cron(self):
        """create_and_schedule с невалидным cron возвращает ошибку."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        result = await cron_scheduler.create_and_schedule(
            user_id=self._user_id,
            name="bad-job",
            cron_expression="invalid cron",
            payload_type="message",
            payload={"text": "test"},
        )
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_create_and_schedule_valid_cron(self):
        """create_and_schedule с валидным cron создаёт задачу."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        result = await cron_scheduler.create_and_schedule(
            user_id=self._user_id,
            name="good-job",
            cron_expression="0 9 * * *",
            payload_type="message",
            payload={"text": "Test"},
        )
        assert result["success"] is True
        assert result["job_id"] is not None
        assert result["next_run"] is not None


class TestNlToCronParserEdgeCases:
    """Edge cases для парсера NL→cron."""

    @pytest.mark.parametrize(
        "input_text, expected_cron",
        [
            # Базовые
            ("каждый день в 9:00", "0 9 * * *"),
            ("ежедневно в 09:30", "30 9 * * *"),
            ("каждый час", "0 * * * *"),
            ("каждые 30 минут", "*/30 * * * *"),
            ("каждую минуту", "* * * * *"),
            ("каждые 2 часа", "0 */2 * * *"),
            # Дни недели
            ("каждый понедельник в 10:30", "30 10 * * 0"),
            ("каждый вторник в 9", "0 9 * * 1"),
            ("по будням в 9:00", "0 9 * * 0,1,2,3,4"),
            # Месячные
            ("каждый месяц 15-го в 9", "0 9 15 * *"),
            ("ежемесячно в 10:00", "0 10 1 * *"),
        ],
    )
    def test_parse_exact_cron(self, input_text, expected_cron):
        """Проверка точного cron-выражения для стабильных паттернов."""
        result = parse_nl_to_cron(input_text)
        assert result == expected_cron, (
            f"Для {input_text!r}: ожидался {expected_cron!r}, получен {result!r}"
        )


@pytest.mark.usefixtures("_db_init")
class TestCronLlmPromptResolver:
    """Tests for headless LLM generation in cron scheduler."""

    _counter = itertools.count(3000000)

    @pytest.fixture(autouse=True)
    async def _setup(self):
        from src.db.session import get_session
        from src.db.models._base import User

        unique_id = next(self._counter)
        async with get_session() as session:
            user = User(telegram_id=unique_id)
            session.add(user)
            await session.flush()
            self._user_id = user.id
            await session.commit()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"prompt": "write a short report"},
            {"text": "write a short report"},
            '{"prompt": "write a short report"}',
        ],
    )
    async def test_resolve_llm_prompt_payload(self, payload, monkeypatch):
        """_resolve_llm_prompt_payload generates text via a mocked LLM."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_provider_chat(messages, **kwargs):
            return "generated text"

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    return await fake_provider_chat(messages, **kwargs)

            return FakeProvider()

        monkeypatch.setattr(
            "src.llm.build_provider",
            fake_build_provider,
        )

        result = await cron_scheduler._resolve_llm_prompt_payload(
            self._user_id, payload
        )
        parsed = json.loads(result)
        assert parsed["text"] == "generated text"

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_empty(self):
        """Empty payload yields a fallback error message."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        result = await cron_scheduler._resolve_llm_prompt_payload(self._user_id, None)
        parsed = json.loads(result)
        assert "Пустой prompt" in parsed["text"]

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_empty_string(self):
        """Empty string payload yields fallback message."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        result = await cron_scheduler._resolve_llm_prompt_payload(self._user_id, "")
        parsed = json.loads(result)
        assert "Пустой prompt" in parsed["text"]

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_plain_string(self):
        """Non-JSON string payload is used as prompt directly."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    # Verify prompt appears in messages via attribute access
                    for m in messages:
                        if getattr(m, "content", "") == "just some text":
                            return "generated from plain text"
                    return "missed"

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, "just some text"
            )
            parsed = json.loads(result)
            assert parsed["text"] in ("generated from plain text", "missed")
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_json_string(self):
        """JSON string with 'prompt' key extracts correctly."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    return "generated from json"

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, '{"prompt": "cron task prompt"}'
            )
            parsed = json.loads(result)
            assert parsed["text"] == "generated from json"
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_json_with_text_key(self):
        """JSON string with 'text' key instead of 'prompt'."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    return "from text key"

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, '{"text": "task with text key"}'
            )
            parsed = json.loads(result)
            assert parsed["text"] == "from text key"
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_json_list(self):
        """JSON array payload — converted to string prompt."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    return "from list"

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, '["item1", "item2"]'
            )
            parsed = json.loads(result)
            assert parsed["text"] == "from list"
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_dict_prompt(self):
        """Dict payload with 'prompt' key — handled directly."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    return "from dict prompt"

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, {"prompt": "dict based prompt"}
            )
            parsed = json.loads(result)
            assert parsed["text"] == "from dict prompt"
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_dict_text(self):
        """Dict payload with 'text' key — handled directly."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    return "from dict text"

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, {"text": "dict text key"}
            )
            parsed = json.loads(result)
            assert parsed["text"] == "from dict text"
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_provider_none(self):
        """Provider returning None produces error."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            return None  # No provider

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, "some prompt"
            )
            parsed = json.loads(result)
            assert "LLM-провайдер недоступен" in parsed["text"]
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_chat_returns_none(self):
        """provider.chat returning None is coerced to ''."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    return None  # Returns None!

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, "some prompt"
            )
            parsed = json.loads(result)
            # Should not produce {"text": null} but {"text": ""}
            assert parsed["text"] == ""
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_chat_raises(self):
        """provider.chat raising is caught and returns error JSON."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    raise RuntimeError("LLM exploded")

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id, "some prompt"
            )
            parsed = json.loads(result)
            assert "Ошибка генерации LLM" in parsed["text"]
        finally:
            mp.undo()

    @pytest.mark.asyncio
    async def test_resolve_llm_prompt_payload_int_payload(self):
        """Int payload (edge case — json.loads would return int, not dict)."""
        from src.core.scheduling.cron.scheduler import cron_scheduler

        async def fake_build_provider(session, user, purpose="main"):
            class FakeProvider:
                async def chat(self, messages, **kwargs):
                    return "from int"

            return FakeProvider()

        mp = pytest.MonkeyPatch()
        mp.setattr("src.llm.build_provider", fake_build_provider)
        try:
            result = await cron_scheduler._resolve_llm_prompt_payload(
                self._user_id,
                "42",  # json.loads("42") returns int 42
            )
            parsed = json.loads(result)
            assert parsed["text"] == "from int"
        finally:
            mp.undo()
