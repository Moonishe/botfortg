"""Tests for src.core.memory.memory_service."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef")
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from src.db.session import init_db, get_session
from src.db.repo import get_or_create_user, list_memories
from src.core.memory.memory_service import (
    save_memories_batch,
    save_memory_single,
    delete_memory_service,
)


OWNER_TG_ID = 123456789


@pytest.fixture(autouse=True)
async def setup_db():
    from src.db.session import engine, Base
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(text("DROP TABLE IF EXISTS messages_fts"))
        await conn.execute(text("DROP TABLE IF EXISTS memories_fts"))
    await init_db()

    yield

    # Do not dispose the engine here; session-scoped _db_init and other
    # tests rely on the in-memory StaticPool connection staying alive.


@pytest.mark.asyncio
async def test_save_memories_batch_basic():
    """save_memories_batch сохраняет факты и возвращает их количество."""
    facts = [
        {"fact": "Пользователь работает в IT", "sentiment": "neutral"},
        {"fact": "Пользователь любит кофе", "sentiment": "positive"},
    ]
    stored = await save_memories_batch(OWNER_TG_ID, facts, source="auto")
    assert stored == 2

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        mems = await list_memories(session, owner)
        assert len(mems) == 2
        assert any("IT" in m.fact for m in mems)
        assert any("кофе" in m.fact for m in mems)


@pytest.mark.asyncio
async def test_save_memories_batch_empty():
    """Пустой список фактов → 0, ошибок нет."""
    stored = await save_memories_batch(OWNER_TG_ID, [], source="auto")
    assert stored == 0


@pytest.mark.asyncio
async def test_save_memories_batch_filters_short_facts():
    """Факты короче 5 символов фильтруются сервисом."""
    facts = [
        {"fact": "Hi", "sentiment": "neutral"},
        {"fact": "Пользователь живёт в Москве", "sentiment": "neutral"},
    ]
    stored = await save_memories_batch(OWNER_TG_ID, facts, source="auto")
    assert stored == 1

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        mems = await list_memories(session, owner)
        assert len(mems) == 1
        assert "Москве" in mems[0].fact


@pytest.mark.asyncio
async def test_save_memories_batch_dedup_updates_existing():
    """Повторный факт увеличивает times_mentioned."""
    facts = [{"fact": "Пользователь любит кофе", "sentiment": "positive"}]
    stored = await save_memories_batch(OWNER_TG_ID, facts, source="auto")
    assert stored == 1

    stored = await save_memories_batch(OWNER_TG_ID, facts, source="auto")
    assert stored == 1

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        mems = await list_memories(session, owner)
        assert len(mems) == 1
        assert mems[0].times_mentioned == 2


@pytest.mark.asyncio
async def test_save_memories_batch_runs_side_effects():
    """При сохранении фактов вызываются инвалидации кэша и bump_recall_version."""
    facts = [{"fact": "Пользователь любит кофе", "sentiment": "positive"}]

    with (
        patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
        patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
        patch(
            "src.core.memory.memory_service.invalidate_contact_digest"
        ) as mock_digest,
    ):
        stored = await save_memories_batch(OWNER_TG_ID, facts, source="auto")
        assert stored == 1

    mock_invalidate.assert_awaited_once_with("mem_")
    mock_bump.assert_awaited_once_with(OWNER_TG_ID)
    mock_digest.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_memories_batch_contact_id_invalidates_digest():
    """Если передан contact_id, инвалидируется дайджест контакта."""
    facts = [{"fact": "Пользователь любит кофе", "sentiment": "positive"}]

    with (
        patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
        patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
        patch(
            "src.core.memory.memory_service.invalidate_contact_digest"
        ) as mock_digest,
    ):
        stored = await save_memories_batch(
            OWNER_TG_ID, facts, source="auto", contact_id=42
        )
        assert stored == 1

    mock_invalidate.assert_awaited_once_with("mem_")
    mock_bump.assert_awaited_once_with(OWNER_TG_ID)
    mock_digest.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_save_memories_batch_no_side_effects_when_empty():
    """При пустом списке фактов побочные эффекты не вызываются."""
    with (
        patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
        patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
        patch(
            "src.core.memory.memory_service.invalidate_contact_digest"
        ) as mock_digest,
    ):
        stored = await save_memories_batch(OWNER_TG_ID, [], source="auto")
        assert stored == 0

        mock_invalidate.assert_not_awaited()
        mock_bump.assert_not_awaited()
        mock_digest.assert_not_awaited()


# ── Edge case tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_memory_candidate_rejects_empty_fact():
    """add_memory_candidate raises ValueError for empty/short/long facts."""
    from src.db.repos.memory_repo._candidates import add_memory_candidate

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        # Empty after strip
        with pytest.raises(ValueError, match="3–10000"):
            await add_memory_candidate(session, owner, fact="  ")

        # Too short
        with pytest.raises(ValueError, match="3–10000"):
            await add_memory_candidate(session, owner, fact="AB")

        # Too long
        with pytest.raises(ValueError, match="3–10000"):
            await add_memory_candidate(session, owner, fact="X" * 10001)


@pytest.mark.asyncio
async def test_save_memory_version_rejects_empty_fact_text():
    """save_memory_version raises ValueError for empty fact_text."""
    from types import SimpleNamespace

    from src.db.repos.memory_repo._versioning import save_memory_version

    async with get_session() as session:
        fake_user = SimpleNamespace(id=1)
        # Empty string
        with pytest.raises(ValueError, match="fact_text must not be empty"):
            await save_memory_version(session, fake_user, 1, "  ", edited_by="test")

        # Empty after strip
        with pytest.raises(ValueError, match="fact_text must not be empty"):
            await save_memory_version(session, fake_user, 1, "", edited_by="test")


@pytest.mark.asyncio
async def test_extend_memory_expiry_handles_naive_datetime():
    """_extend_memory_expiry normalises naive datetimes to UTC before comparison."""
    from src.db.repos.memory_repo._core import _extend_memory_expiry
    from unittest.mock import MagicMock
    from datetime import timezone, timedelta

    # Memory mock with naive expires_at (as if stored without timezone)
    mem = MagicMock()
    mem.expires_at = datetime(2026, 12, 31, 12, 0, 0)  # naive

    # new_expires_at is also naive
    new_expires = datetime(2026, 6, 15, 12, 0, 0)
    _extend_memory_expiry(mem, new_expires)

    # Should keep the later date (Dec > Jun), both now UTC-aware
    assert mem.expires_at.tzinfo is not None
    assert mem.expires_at.year == 2026
    assert mem.expires_at.month == 12


@pytest.mark.asyncio
async def test_extend_memory_expiry_mixed_naive_aware():
    """_extend_memory_expiry handles mixing naive existing + aware new."""
    from src.db.repos.memory_repo._core import _extend_memory_expiry
    from unittest.mock import MagicMock

    mem = MagicMock()
    mem.expires_at = datetime(2026, 6, 1, 12, 0, 0)  # naive

    # new_expires_at is aware UTC (later date)
    new_expires = datetime(2026, 12, 1, 12, 0, 0, tzinfo=timezone.utc)
    _extend_memory_expiry(mem, new_expires)

    # Should pick the aware Dec date
    assert mem.expires_at.tzinfo is not None
    assert mem.expires_at.month == 12


@pytest.mark.asyncio
async def test_extend_memory_expiry_both_none():
    """_extend_memory_expiry leaves expires_at as None when both are None."""
    from src.db.repos.memory_repo._core import _extend_memory_expiry
    from unittest.mock import MagicMock

    mem = MagicMock()
    mem.expires_at = None
    _extend_memory_expiry(mem, None)
    assert mem.expires_at is None


@pytest.mark.asyncio
async def test_temporal_marker_no_false_positive_uzhe_ne():
    """Temporal markers do NOT match substrings like 'уже некуда'."""
    from src.db.repos.memory_repo._core import _TEMPORAL_RE

    # Should NOT match (false positive prevention)
    assert not _TEMPORAL_RE.search("Пользователь уже некуда не ходит")
    assert not _TEMPORAL_RE.search("Больше некого спросить")
    assert not _TEMPORAL_RE.search("сейчасные дела")  # "сейчас" not standalone

    # Should match (true positives)
    assert _TEMPORAL_RE.search("Пользователь уже не работает")
    assert _TEMPORAL_RE.search("Он больше не курит")
    assert _TEMPORAL_RE.search("сейчас он дома")
    assert _TEMPORAL_RE.search("раньше было иначе")
    assert _TEMPORAL_RE.search("перестал звонить")


@pytest.mark.asyncio
async def test_deduplicate_false_creates_new_record():
    """deduplicate=False всегда создаёт новую запись, даже при дубликате."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        with (
            patch("src.core.memory.auto_linker.auto_link_memory"),
            patch("src.core.infra.hooks.hooks"),
            patch("src.core.memory.memory_service.invalidate"),
            patch("src.core.memory.memory_service.bump_recall_version"),
        ):
            mem1 = await save_memory_single(
                session,
                owner,
                fact="Уникальный факт для дедупа",
                deduplicate=True,
            )
            assert mem1 is not None

            mem2 = await save_memory_single(
                session,
                owner,
                fact="Уникальный факт для дедупа",
                deduplicate=False,
            )
            assert mem2 is not None
            assert mem2.id != mem1.id  # новый id

        # Проверяем: две записи в БД
        mems = await list_memories(session, owner)
        assert len(mems) == 2


@pytest.mark.asyncio
async def test_vector_store_none_skips_qdrant_gracefully():
    """Если vector_store_obj=None, Qdrant indexing молча пропускается."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        fake_embedding = [0.1] * 384

        with (
            patch("src.core.memory.auto_linker.auto_link_memory"),
            patch("src.core.infra.hooks.hooks"),
            patch("src.core.memory.memory_service.invalidate"),
            patch("src.core.memory.memory_service.bump_recall_version"),
        ):
            # vector_store_obj явно None, но embedding есть
            mem = await save_memory_single(
                session,
                owner,
                fact="Факт без векторного индекса",
                embedding=fake_embedding,
                vector_store_obj=None,
            )
            assert mem is not None  # создан успешно, без падения

        # Проверяем БД
        mems = await list_memories(session, owner)
        assert len(mems) == 1


@pytest.mark.parametrize(
    "bad_id",
    [-1, 0, True, False, 1.5, "abc"],
)
@pytest.mark.asyncio
async def test_save_memories_batch_invalid_telegram_id(bad_id):
    """Некорректный telegram_id вызывает ValueError."""
    facts = [{"fact": "Пользователь любит кофе", "sentiment": "positive"}]

    with pytest.raises(ValueError, match="telegram_id must be a positive integer"):
        await save_memories_batch(bad_id, facts, source="auto")


@pytest.mark.asyncio
async def test_save_memories_batch_skips_none_and_non_dict_facts():
    """None и не-dict элементы в списке фактов игнорируются."""
    facts = [
        None,
        "not a dict",
        {"fact": "Пользователь любит кофе", "sentiment": "positive"},
    ]
    stored = await save_memories_batch(OWNER_TG_ID, facts, source="auto")
    assert stored == 1

    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        mems = await list_memories(session, owner)
        assert len(mems) == 1
        assert "кофе" in mems[0].fact


# ── save_memory_single tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_memory_single_creates_new_memory():
    """save_memory_single создаёт новую память с полными side effects."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        with (
            patch("src.core.memory.auto_linker.auto_link_memory") as mock_link,
            patch("src.core.infra.hooks.hooks") as mock_hooks,
            patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
            patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
            patch(
                "src.core.memory.memory_service.invalidate_contact_digest"
            ) as mock_digest,
        ):
            mem = await save_memory_single(
                session, owner, fact="Пользователь любит чай"
            )

        # Проверяем возвращённый объект
        assert mem is not None
        assert mem.fact == "Пользователь любит чай"
        assert mem.is_active is True

        # Always-run side effects
        mock_invalidate.assert_awaited_once_with("mem_")
        mock_bump.assert_awaited_once_with(OWNER_TG_ID)
        # contact_id=None → digest NOT invalidated
        mock_digest.assert_not_awaited()

        # created_new=True → auto-link + hooks вызываются
        mock_link.assert_called_once()
        mock_hooks.emit.assert_called_once()


@pytest.mark.asyncio
async def test_save_memory_single_merge_updates_existing():
    """Повторный факт обновляет существующую запись, без auto-link/hooks."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        with (
            patch("src.core.memory.auto_linker.auto_link_memory") as mock_link,
            patch("src.core.infra.hooks.hooks") as mock_hooks,
            patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
            patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
        ):
            # Первое сохранение — создаёт новую запись
            mem1 = await save_memory_single(
                session, owner, fact="Пользователь любит зелёный чай"
            )
            assert mem1 is not None
            # created_new=True — auto-link + hooks вызваны
            mock_link.assert_called_once()
            mock_hooks.emit.assert_called_once()
            link_call_args = mock_link.call_args
            hooks_call_args = mock_hooks.emit.call_args

            # Сбрасываем моки для второго вызова
            mock_link.reset_mock()
            mock_hooks.emit.reset_mock()
            mock_invalidate.reset_mock()
            mock_bump.reset_mock()

            # Второе сохранение того же факта — merge
            mem2 = await save_memory_single(
                session, owner, fact="Пользователь любит зелёный чай"
            )
            assert mem2 is not None
            assert mem2.id == mem1.id  # та же запись

            # created_new=False — auto-link + hooks НЕ вызываются
            mock_link.assert_not_called()
            mock_hooks.emit.assert_not_called()

            # Always-run side effects вызываются и при merge
            mock_invalidate.assert_awaited_once_with("mem_")
            mock_bump.assert_awaited_once_with(OWNER_TG_ID)

        # Проверяем БД: одна запись, times_mentioned == 2
        mems = await list_memories(session, owner)
        assert len(mems) == 1
        assert mems[0].times_mentioned == 2
        assert mems[0].confidence > 0.85  # выросла при merge


@pytest.mark.asyncio
async def test_save_memory_single_merge_extends_expires_at():
    """Merge продлевает expires_at, если новый факт содержит срок."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)
        past = datetime.now(timezone.utc) + timedelta(days=5)
        future = datetime.now(timezone.utc) + timedelta(days=60)

        with (
            patch("src.core.memory.auto_linker.auto_link_memory"),
            patch("src.core.infra.hooks.hooks"),
            patch("src.core.memory.memory_service.invalidate"),
            patch("src.core.memory.memory_service.bump_recall_version"),
        ):
            # Первая запись с коротким сроком
            mem1 = await save_memory_single(
                session,
                owner,
                fact="Пользователь любит зелёный чай",
                expires_at=past,
            )
            assert mem1 is not None
            assert mem1.expires_at == past

            # Второй факт с более длинным сроком → merge и продление
            mem2 = await save_memory_single(
                session,
                owner,
                fact="Пользователь любит зелёный чай",
                expires_at=future,
            )
            assert mem2 is not None
            assert mem2.id == mem1.id

        # Проверяем БД: expires_at вырос
        mems = await list_memories(session, owner)
        assert len(mems) == 1
        assert mems[0].expires_at == future


@pytest.mark.asyncio
async def test_save_memory_single_invalid_fact():
    """Некорректный факт → None, side effects не вызываются."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        # Слишком короткий факт (< 3 символов)
        with (
            patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
            patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
        ):
            result = await save_memory_single(session, owner, fact="AB")
            assert result is None

        mock_invalidate.assert_not_awaited()
        mock_bump.assert_not_awaited()

        # Слишком длинный факт (> 10000 символов)
        with (
            patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
            patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
        ):
            long_fact = "X" * 10001
            result = await save_memory_single(session, owner, fact=long_fact)
            assert result is None

        mock_invalidate.assert_not_awaited()
        mock_bump.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_memory_single_contact_id_invalidates_digest():
    """contact_id → invalidate_contact_digest вызывается."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        with (
            patch("src.core.memory.auto_linker.auto_link_memory"),
            patch("src.core.infra.hooks.hooks"),
            patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
            patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
            patch(
                "src.core.memory.memory_service.invalidate_contact_digest"
            ) as mock_digest,
        ):
            mem = await save_memory_single(
                session, owner, fact="Чай с бергамотом", contact_id=42
            )
            assert mem is not None

        mock_invalidate.assert_awaited_once_with("mem_")
        mock_bump.assert_awaited_once_with(OWNER_TG_ID)
        mock_digest.assert_awaited_once_with(42)


@pytest.mark.asyncio
async def test_save_memory_single_invalid_sentiment_normalized():
    """Невалидный sentiment нормализуется в 'neutral'."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        with (
            patch("src.core.memory.auto_linker.auto_link_memory"),
            patch("src.core.infra.hooks.hooks"),
            patch("src.core.memory.memory_service.invalidate"),
            patch("src.core.memory.memory_service.bump_recall_version"),
        ):
            mem = await save_memory_single(
                session,
                owner,
                fact="Пользователь любит чай",
                sentiment="happy",
            )
            assert mem is not None

        # Проверяем в БД — sentiment нормализован
        mems = await list_memories(session, owner)
        assert len(mems) == 1
        assert mems[0].sentiment == "neutral"


# ── delete_memory_service tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_memory_service_soft_deletes():
    """Soft-delete факта с side effects."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        # Сначала сохраняем факт
        with (
            patch("src.core.memory.auto_linker.auto_link_memory"),
            patch("src.core.infra.hooks.hooks"),
            patch("src.core.memory.memory_service.invalidate"),
            patch("src.core.memory.memory_service.bump_recall_version"),
        ):
            mem = await save_memory_single(
                session,
                owner,
                fact="Пользователь любит чай",
                contact_id=42,
            )
            assert mem is not None
            mem_id = mem.id

        # Удаляем
        mock_vs = AsyncMock()
        with (
            patch(
                "src.core.memory.memory_service.get_vector_store",
                return_value=mock_vs,
            ),
            patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
            patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
            patch(
                "src.core.memory.memory_service.invalidate_contact_digest"
            ) as mock_digest,
        ):
            success = await delete_memory_service(session, owner, mem_id)
            assert success is True

        mock_invalidate.assert_awaited_once_with("mem_")
        mock_bump.assert_awaited_once_with(OWNER_TG_ID)
        mock_digest.assert_awaited_once_with(42)
        mock_vs.delete_memories.assert_awaited_once_with([mem_id])

        # Проверяем в БД — is_active=False
        from src.db.models import Memory
        from sqlalchemy import select as sa_select

        result = await session.execute(sa_select(Memory).where(Memory.id == mem_id))
        db_mem = result.scalar_one()
        assert db_mem.is_active is False


@pytest.mark.asyncio
async def test_delete_memory_service_not_found_or_other_owner():
    """Несуществующий или чужой memory_id → False, side effects не вызываются."""
    async with get_session() as session:
        owner = await get_or_create_user(session, OWNER_TG_ID)

        # Несуществующий memory_id
        with (
            patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
            patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
            patch(
                "src.core.memory.memory_service.invalidate_contact_digest"
            ) as mock_digest,
        ):
            success = await delete_memory_service(session, owner, 999999)
            assert success is False

        mock_invalidate.assert_not_awaited()
        mock_bump.assert_not_awaited()
        mock_digest.assert_not_awaited()

        # Чужой факт: создаём второго пользователя
        other_owner = await get_or_create_user(session, 987654321)

        with (
            patch("src.core.memory.auto_linker.auto_link_memory"),
            patch("src.core.infra.hooks.hooks"),
            patch("src.core.memory.memory_service.invalidate"),
            patch("src.core.memory.memory_service.bump_recall_version"),
        ):
            mem = await save_memory_single(
                session, other_owner, fact="Факт другого пользователя"
            )
            assert mem is not None
            other_mem_id = mem.id

        # Пытаемся удалить чужой факт от имени первого пользователя
        with (
            patch("src.core.memory.memory_service.invalidate") as mock_invalidate,
            patch("src.core.memory.memory_service.bump_recall_version") as mock_bump,
            patch(
                "src.core.memory.memory_service.invalidate_contact_digest"
            ) as mock_digest,
        ):
            success = await delete_memory_service(session, owner, other_mem_id)
            assert success is False

        mock_invalidate.assert_not_awaited()
        mock_bump.assert_not_awaited()
        mock_digest.assert_not_awaited()
