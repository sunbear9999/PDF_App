# Backward-compat shim — canonical location is gui.base.core
from gui.base.core import *  # noqa: F401, F403
from gui.base.core import FALLBACK_THEME, SOLARIZED_THEME, UnifiedThemedMixin, ThemedMixin, ModernThemedMixin, BaseDock
