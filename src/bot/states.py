from aiogram.fsm.state import State, StatesGroup


class LoginStates(StatesGroup):
    # api_id / api_hash states removed — dead code after credentials refactor.
    # Credentials are now read from settings directly via cmd_login.
    # See src/bot/handlers/login.py line 132-133 for migration notes.
    phone = State()
    code = State()
    password_2fa = State()


class SettingsStates(StatesGroup):
    waiting_openai_key = State()
    waiting_gemini_key = State()
    waiting_mistral_key = State()
    waiting_cloudflare_key = State()
    waiting_digest_time = State()
    waiting_news_time = State()
    waiting_timezone = State()
    waiting_auto_reply_text = State()
    waiting_sync_interval = State()
    waiting_custom_instructions = State()
    waiting_alias = State()
    waiting_deepseek_key = State()
    waiting_grok_key = State()
    waiting_mimo_key = State()
    waiting_groq_key = State()
    waiting_deepgram_key = State()
    waiting_assemblyai_key = State()
    waiting_mimo_region = State()
    waiting_custom_model_name = State()
    waiting_custom_name = State()
    waiting_custom_endpoint = State()
    waiting_custom_key = State()
    waiting_custom_models = State()
    waiting_config_import = State()


class NewsTopicStates(StatesGroup):
    waiting_topic = State()


class DraftStates(StatesGroup):
    waiting_edit = State()


class OnboardingStates(StatesGroup):
    waiting_login = State()
    waiting_provider_choice = State()  # новый: выбор провайдера инлайн-клавиатурой
    waiting_llm_key = State()
    waiting_stt_provider = State()
    waiting_stt_key = State()
    waiting_timezone = State()
    waiting_sync_choice = State()


class CustomProviderStates(StatesGroup):
    """FSM для добавления кастомного провайдера через онбординг."""

    waiting_provider_name = State()
    waiting_endpoint = State()
    waiting_key = State()
    waiting_model = State()


class MemoryCorrectionStates(StatesGroup):
    """FSM для ручного исправления факта памяти (`/memory --correct <id>`).

    Использует встроенный aiogram FSM вместо legacy _PENDING_CORRECTIONS dict.
    TTL проверяется лениво через `set_at_ts` в state data при обработке
    следующего сообщения.
    """

    waiting_new_text = State()  # user typing the corrected fact
