"""/settings — главное меню и разделы.

Callback constants: :class:`src.bot.callbacks.SettingsCB`.

SRP: thin facade — only imports from sub-modules. Router lives in settings_router.
"""

# ── Core router (exported from settings_router to avoid circular deps) ──
from src.bot.handlers.settings_router import router

# ── Import handler modules to register callbacks on router ──
# Handlers are registered via `@router.callback_query()` which imports
# router from settings_router. The side-effects here just ensure all
# handler modules are loaded so their decorators execute.

from src.bot.handlers import settings_handler
from src.bot.handlers import settings_sections
from src.bot.handlers import settings_inputs
from src.bot.handlers import settings_menu
from src.bot.handlers import settings_service
from src.bot.handlers import settings_validator

# Re-export public symbols for backward compat
from src.bot.handlers.settings_handler import (
    cmd_settings,
    cb_menu,
    cb_settings_back,
    cb_close,
    cb_export_config,
    cb_import_config,
    step_import_config,
    cb_settings_analyze,
    cb_toggle,
    cb_choose,
    cb_open_section,
    cb_folder_toggle,
    cb_folder_refresh,
    cb_model_reset_all,
    cb_model_set,
    cb_model_del,
    cb_model_custom,
    step_custom_model_name,
    cb_model_open,
    cb_pick_tz,
    cb_input_tz,
    cb_done_adding_key,
    cb_persona_reset,
    cancel_settings_state,
)

from src.bot.handlers.settings_sections import _render_section

from src.bot.handlers.settings_inputs import (
    cb_input_openai,
    cb_input_gemini,
    cb_input_mistral,
    cb_input_cloudflare,
    cb_input_deepseek,
    cb_input_grok,
    cb_input_mimo,
    cb_input_groq,
    cb_input_deepgram,
    cb_input_assemblyai,
    cb_input_custom_name,
    cb_input_digest,
    cb_input_auto_reply,
    cb_input_sync_interval,
    cb_input_news_time,
    cb_noop_news_topics,
    cb_input_quiet_hours_start,
    cb_input_quiet_hours_end,
    cb_input_alias,
    cb_input_custom_instructions,
    step_mimo_key,
    cb_mimo_region,
    step_custom_name,
    step_custom_endpoint,
    step_custom_key,
    step_custom_models,
    step_digest_time,
    step_news_time,
    step_auto_reply_text,
    step_timezone,
    step_sync_interval,
    step_quiet_hours_start,
    step_quiet_hours_end,
    step_alias,
    step_custom_instructions,
    step_openai_key,
    step_gemini_key,
    step_mistral_key,
    step_cloudflare_key,
    step_deepseek_key,
    step_grok_key,
    step_groq_key,
    step_deepgram_key,
    step_assemblyai_key,
)

from src.bot.handlers.settings_menu import (
    _check,
    _back_row,
    _render_menu,
)

from src.bot.handlers.settings_service import (
    _count_slots_for_provider,
)

from src.bot.handlers.settings_validator import (
    SEARCHABLE_SETTINGS,
    BOOL_KEYS,
    CHOICE_KEYS,
    NUMERIC_KEYS,
    PERSONA_KEYS,
    section_for_key,
)
