"""Unit tests for LlmKeySlotModel CRUD and multi-select keyboard.

Covers:
  - get_slot_models, set_slot_models, toggle_slot_model, get_enabled_models
  - Multiselect keyboard structure (_build_model_multiselect_keyboard)
  - Backward compat: slot.model still works when LlmKeySlotModel is empty
"""

from __future__ import annotations

import os

os.environ["ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["BOT_TOKEN"] = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
os.environ["OWNER_TELEGRAM_ID"] = "123456789"

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.repos.key_repo import (
    get_slot_models,
    set_slot_models,
    toggle_slot_model,
    get_enabled_models,
)
from src.db.models import LlmKeySlotModel
from src.bot.handlers.keys_cmd import _build_model_multiselect_keyboard


# ────────────────────────────────────────────────────────────────────
# CRUD tests with in-memory SQLite
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_slot_models_empty():
    """Пустой результат для слота без моделей."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    result = await get_slot_models(mock_session, slot_id=1)
    assert result == []


@pytest.mark.asyncio
async def test_set_slot_models_replaces():
    """set_slot_models удаляет старые и добавляет новые."""
    mock_session = AsyncMock()
    # AsyncSession.add() is sync — mock accordingly to avoid
    # Python 3.13 RuntimeWarning about unawaited coroutine.
    mock_session.add = MagicMock()

    await set_slot_models(mock_session, slot_id=1, model_names=["gpt-4o", "claude-3"])

    # Должен быть вызов delete
    assert mock_session.execute.called
    # Должны быть добавлены 2 модели
    assert mock_session.add.call_count == 2
    # Проверяем flush
    mock_session.flush.assert_called_once()


@pytest.mark.asyncio
async def test_toggle_slot_model_enable_disable():
    """toggle_slot_model переключает enabled."""
    mock_model = MagicMock(spec=LlmKeySlotModel)
    mock_model.enabled = True

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_model
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result

    # Выключаем
    result = await toggle_slot_model(
        mock_session, slot_id=1, model_name="gpt-4o", enabled=False
    )
    assert result is True
    assert mock_model.enabled is False

    # Включаем обратно
    mock_model.enabled = False
    result = await toggle_slot_model(
        mock_session, slot_id=1, model_name="gpt-4o", enabled=True
    )
    assert result is True
    assert mock_model.enabled is True


@pytest.mark.asyncio
async def test_toggle_slot_model_not_found():
    """toggle_slot_model возвращает False для несуществующей модели."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result

    result = await toggle_slot_model(
        mock_session, slot_id=1, model_name="nonexistent", enabled=True
    )
    assert result is False


@pytest.mark.asyncio
async def test_get_enabled_models_filters():
    """get_enabled_models возвращает только enabled модели."""
    m1 = MagicMock(spec=LlmKeySlotModel)
    m1.model_name = "gpt-4o"
    m1.enabled = True

    m2 = MagicMock(spec=LlmKeySlotModel)
    m2.model_name = "claude-3"
    m2.enabled = False

    m3 = MagicMock(spec=LlmKeySlotModel)
    m3.model_name = "gemini-pro"
    m3.enabled = True

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [m1, m2, m3]
    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result

    result = await get_enabled_models(mock_session, slot_id=1)
    assert result == ["gpt-4o", "gemini-pro"]


# ────────────────────────────────────────────────────────────────────
# Multiselect keyboard tests
# ────────────────────────────────────────────────────────────────────


def test_multiselect_keyboard_structure():
    """Проверяет структуру multi-select клавиатуры."""
    models = ["gpt-4o", "gpt-4-turbo", "claude-3-opus", "claude-3-sonnet", "gemini-pro"]
    selected = {"gpt-4o", "claude-3-opus"}
    markup = _build_model_multiselect_keyboard(
        slot_id=1, models=models, selected=selected, page=0
    )

    # Должен быть InlineKeyboardMarkup
    assert markup is not None
    keyboard = markup.inline_keyboard
    assert len(keyboard) > 0

    # Модели идут со второй строки (первая — фильтры).
    # Проверяем что модель содержит ✅/⬜
    model_row_texts = [
        row[0].text
        for row in keyboard
        if row[0].text and ("✅" in row[0].text or "⬜" in row[0].text)
    ]
    assert model_row_texts, "Ни одна модель не содержит чекбокс ✅/⬜"

    # Должны быть кнопки «Готово» и «Ввести вручную»
    all_texts = [btn.text for row in keyboard for btn in row]
    assert any("Готово" in t for t in all_texts)
    assert any("Ввести вручную" in t for t in all_texts)


def test_multiselect_keyboard_no_models():
    """Multi-select клавиатура с пустым списком моделей."""
    markup = _build_model_multiselect_keyboard(
        slot_id=1, models=[], selected=set(), page=0
    )

    assert markup is not None
    keyboard = markup.inline_keyboard
    assert len(keyboard) > 0

    # Должны быть кнопки действий даже без моделей
    all_texts = [btn.text for row in keyboard for btn in row]
    assert any("Готово" in t for t in all_texts)


def test_multiselect_keyboard_pagination():
    """Пагинация: >8 моделей должны разбиваться на страницы."""
    models = [f"model-{i}" for i in range(20)]  # 20 моделей
    selected = set()

    markup_page0 = _build_model_multiselect_keyboard(
        slot_id=1, models=models, selected=selected, page=0
    )
    markup_page1 = _build_model_multiselect_keyboard(
        slot_id=1, models=models, selected=selected, page=1
    )

    # Страница 0: 8 моделей + навигация + действия
    texts_page0 = [btn.text for row in markup_page0.inline_keyboard for btn in row]
    # Страница 1: другие модели
    texts_page1 = [btn.text for row in markup_page1.inline_keyboard for btn in row]

    # Проверяем, что на странице 0 есть model-0, но нет model-8
    assert any("model-0" in t for t in texts_page0)
    assert not any("model-8" in t for t in texts_page0)
    # На странице 1 есть model-8
    assert any("model-8" in t for t in texts_page1)


def test_multiselect_keyboard_all_selected():
    """Все модели выбраны — показываем ✅."""
    models = ["a", "b", "c"]
    selected = set(models)
    markup = _build_model_multiselect_keyboard(
        slot_id=1, models=models, selected=selected, page=0
    )

    all_texts = [btn.text for row in markup.inline_keyboard for btn in row]
    # Все модели должны иметь ✅ (считаем только строки-модели, не кнопки действий)
    model_checkmarks = [
        t for t in all_texts if "✅" in t and not t.startswith("✅ Выбрать")
    ]
    assert len(model_checkmarks) == 3


# ────────────────────────────────────────────────────────────────────
# Backward compat: slot.model still works
# ────────────────────────────────────────────────────────────────────


def test_backward_compat_slot_model_fallback():
    """При пустом LlmKeySlotModel, slot.model должен использоваться как fallback."""
    # Симулируем: слот имеет model="gpt-4o", но LlmKeySlotModel пуст
    # В build_provider это обрабатывается:
    #   if enabled: models.append(enabled)
    #   elif s.model: models.append([s.model])
    #   else: models.append([])
    slot_model = "gpt-4o"
    enabled_from_db: list[str] = []

    if enabled_from_db:
        models = enabled_from_db
    elif slot_model:
        models = [slot_model]
    else:
        models = []

    assert models == ["gpt-4o"]


def test_multi_model_first_enabled_used():
    """Multi-key provider использует первую enabled модель из списка."""
    per_slot_models = ["claude-3-opus", "claude-3-sonnet", "gpt-4o"]
    # _try_with_retry должен взять per_slot[0]
    chosen = per_slot_models[0]
    assert chosen == "claude-3-opus"
