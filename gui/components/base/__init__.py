# Backward-compat shim — canonical location is now gui.base
# All imports from gui.components.base continue to work unchanged.
from gui.base import *  # noqa: F401, F403
from gui.base import __all__
