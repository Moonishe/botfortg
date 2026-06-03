"""Centralized callback_data constants for type-safety.

Usage::

    from src.bot.callbacks import SettingsCB, OnboardingCB, KeysCB

    # Static values — use .value
    callback_data=SettingsCB.MENU.value
    F.data == SettingsCB.CLOSE.value

    # Dynamic values — use static methods
    callback_data=SettingsCB.section("tz")       # "set:sec:tz"
    callback_data=SettingsCB.toggle("digest_enabled")  # "set:tog:digest_enabled"
    callback_data=SettingsCB.choose("llm_provider", "openai")  # "set:choose:llm_provider:openai"
    callback_data=SettingsCB.input("openai_key")  # "set:input:openai_key"
    callback_data=SettingsCB.model_sel(task_type)  # "set:model_sel:maestro"
    callback_data=SettingsCB.timezone(tz)         # "set:tz:Europe/Moscow"
    callback_data=SettingsCB.folder_toggle(name)  # "set:folder:tog:Работа"
"""

from enum import Enum


class SettingsCB(str, Enum):
    """Settings menu callbacks (set: prefix)."""

    MENU = "set:menu"
    CLOSE = "set:close"
    ANALYZE = "set:analyze"
    EXPORT_CONFIG = "set:export_config"
    IMPORT_CONFIG = "set:import_config"
    MODEL_RESET_ALL = "set:model:reset_all"
    FOLDER_REFRESH = "set:folder:refresh"

    @staticmethod
    def section(name: str) -> str:
        return f"set:sec:{name}"

    @staticmethod
    def toggle(field: str) -> str:
        return f"set:tog:{field}"

    @staticmethod
    def choose(field: str, value: str) -> str:
        return f"set:choose:{field}:{value}"

    @staticmethod
    def input(field: str) -> str:
        return f"set:input:{field}"

    @staticmethod
    def model_sel(task_type: str) -> str:
        return f"set:model_sel:{task_type}"

    @staticmethod
    def model_set(task_type: str, model_name: str) -> str:
        return f"set:model:set:{task_type}:{model_name}"

    @staticmethod
    def model_custom(task_type: str) -> str:
        return f"set:model:custom:{task_type}"

    @staticmethod
    def model_del(task_type: str) -> str:
        return f"set:model:del:{task_type}"

    @staticmethod
    def timezone(tz: str) -> str:
        return f"set:tz:{tz}"

    @staticmethod
    def folder_toggle(name: str) -> str:
        return f"set:folder:tog:{name}"

    @staticmethod
    def back(parent: str = "menu") -> str:
        return f"settings:back:{parent}"

    @staticmethod
    def noop(field: str) -> str:
        return f"set:noop:{field}"

    @staticmethod
    def persona_reset() -> str:
        return "set:persona:reset"


class OnboardingCB(str, Enum):
    """Onboarding flow callbacks (onb: / onboarding: prefix)."""

    SKIP_LLM_KEY = "onb:skip:llm_key"
    GOBACK = "onb:goback"
    DONE_KEYS = "onb:done:keys"
    FINISH = "onb:finish"
    BACK = "onb:back"
    GO_SETTINGS = "go_settings"

    @staticmethod
    def start() -> str:
        return "onboarding:start"

    @staticmethod
    def hint_login() -> str:
        return "onboarding:hint_login"

    @staticmethod
    def provider(name: str) -> str:
        return f"onb:provider:{name}"

    @staticmethod
    def category(name: str) -> str:
        return f"onb:category:{name}"

    @staticmethod
    def tts(name: str) -> str:
        return f"onb:tts:{name}"

    @staticmethod
    def stt(name: str) -> str:
        return f"onb:stt:{name}"

    @staticmethod
    def custom(name: str) -> str:
        return f"onb:custom:{name}"

    @staticmethod
    def mimo_region(region: str) -> str:
        return f"onb:mimo_region:{region}"

    @staticmethod
    def back_extra(name: str) -> str:
        return f"onb:back:{name}"

    @staticmethod
    def timezone(tz: str) -> str:
        return f"onboarding:tz:{tz}"

    @staticmethod
    def sync(mode: str) -> str:
        return f"onboarding:sync:{mode}"

    @staticmethod
    def skip(section: str) -> str:
        return f"onb:skip:{section}"


class KeysCB(str, Enum):
    """API keys management callbacks (keys: prefix)."""

    BACK_CLOSE = "keys:back:close"
    BACK_CAT = "keys:back:cat"

    @staticmethod
    def category(name: str) -> str:
        return f"keys:cata:{name}"

    @staticmethod
    def provider(category: str, name: str) -> str:
        return f"keys:cat:{category}:{name}"

    @staticmethod
    def model(provider: str, model: str) -> str:
        return f"keys:model:{provider}:{model}"

    @staticmethod
    def remove(slot_id: int) -> str:
        return f"keys:remove:{slot_id}"

    @staticmethod
    def back_provider(category: str) -> str:
        return f"keys:back:provider:{category}"
