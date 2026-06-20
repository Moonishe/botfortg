"""Tests for LearnedRouter — self-improving keyword router."""

from __future__ import annotations

import json
import os
import tempfile



from src.core.intelligence.routing_wordlists import (
    LearnedRouter,
    learned_match,
    learn_routing,
    get_learned_router,
    reset_learned_routing,
)


# ===========================================================================
# TestLearnedRouter
# ===========================================================================


class TestLearnedRouter:
    """LearnedRouter: загрузка, сохранение, match, learn."""

    def test_initial_empty(self):
        """Новый LearnedRouter не содержит слов."""
        router = LearnedRouter(filepath=":memory:")
        assert router.match("черкани Насте") is None

    def test_learn_and_match(self):
        """После learn() слово матчится с интентом."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("черкани пару строк про встречу", "draft_reply")
        assert router.match("черкани Насте привет") == "draft_reply"

    def test_learn_ignores_stop_words(self):
        """Стоп-слова (предлоги, местоимения) не запоминаются."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("и в на с", "send_message")
        assert router.match("и в на с") is None

    def test_learn_ignores_short_words(self):
        """Слова короче 3 символов не запоминаются."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("на пиши", "send_message")
        assert router.match("на") is None
        assert router.match("пиши") == "send_message"

    def test_learn_ignores_non_learnable_intents(self):
        """Интенты вне _LEARNABLE_INTENTS не обучаются."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("как дела", "chat")
        assert router.match("дела") is None

    def test_first_word_wins(self):
        """При повторном learn() первое изученное слово не перезаписывается."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("черкани ответ", "draft_reply")
        router.learn("черкани письмо", "send_message")  # same word
        assert router.match("черкани") == "draft_reply"

    def test_persistence(self):
        """Слова сохраняются и загружаются из файла."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            fname = f.name
            json.dump({"черкани": "draft_reply"}, f)

        try:
            router = LearnedRouter(filepath=fname)
            assert router.match("черкани Насте") == "draft_reply"
        finally:
            os.unlink(fname)

    def test_save_on_learn(self):
        """После learn() слова сохраняются в файл."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            fname = f.name
            f.write("{}")

        try:
            router = LearnedRouter(filepath=fname)
            router.learn("поищи новости", "search")
            with open(fname, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "поищи" in data
            assert data["поищи"] == "search"
        finally:
            os.unlink(fname)

    def test_reset_clears_words(self):
        """reset() очищает все изученные слова."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("черкани", "draft_reply")
        router.reset()
        assert router.match("черкани") is None

    def test_match_case_insensitive(self):
        """match() не зависит от регистра."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("Черкани", "draft_reply")
        assert router.match("черкани Насте") == "draft_reply"
        assert router.match("ЧЕРКАНИ ПРИВЕТ") == "draft_reply"

    def test_learn_multiple_words(self):
        """Из одного запроса запоминаются все значащие слова."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("черкани Васе про встречу", "draft_reply")
        assert router.match("черкани") == "draft_reply"
        assert router.match("Васе") == "draft_reply"

    def test_no_cross_contamination(self):
        """Разные интенты не путаются."""
        router = LearnedRouter(filepath=":memory:")
        router.learn("черкани Васе", "draft_reply")
        router.learn("поищи контакт", "search")
        assert router.match("черкани") == "draft_reply"
        assert router.match("поищи") == "search"

    def test_file_not_found_graceful(self):
        """Несуществующий файл → пустой роутер."""
        router = LearnedRouter(filepath="/tmp/nonexistent_file_12345.json")
        assert router.match("черкани") is None

    def test_invalid_json_graceful(self):
        """Битый JSON → пустой роутер."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            fname = f.name
            f.write("not json at all!!!")

        try:
            router = LearnedRouter(filepath=fname)
            assert router.match("черкани") is None
        finally:
            os.unlink(fname)

    def test_learnable_intents_filtered(self):
        """_LEARNABLE_INTENTS содержит все основные action-интенты."""
        from src.core.intelligence.routing_wordlists import _LEARNABLE_INTENTS

        essential = {
            "send_message",
            "draft_reply",
            "search",
            "find_in_chats",
            "summarize_chat",
            "catchup",
            "add_reminder",
            "store_memory",
            "check_memories",
        }
        assert essential.issubset(_LEARNABLE_INTENTS)


# ===========================================================================
# TestLearnedRoutingIntegration
# ===========================================================================


def test_global_router_singleton():
    """get_learned_router() всегда возвращает один и тот же экземпляр."""
    r1 = get_learned_router()
    r2 = get_learned_router()
    assert r1 is r2


def test_learn_routing_wrapper():
    """learn_routing() wrapper работает без ошибок."""
    reset_learned_routing()
    learn_routing("поищи контакт Вася", "search")
    assert learned_match("поищи контакт") == "search"
    reset_learned_routing()


def test_reset_learned_routing():
    """reset_learned_routing() очищает глобальный роутер."""
    learn_routing("черкани", "draft_reply")
    reset_learned_routing()
    assert learned_match("черкани") is None
