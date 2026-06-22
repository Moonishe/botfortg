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

