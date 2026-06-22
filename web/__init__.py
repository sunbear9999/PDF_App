"""Papyrus Local Web Companion.

The package is intentionally isolated from ``core`` and ``gui``.  Desktop
Papyrus imports it only from the Web App settings tab.
"""

from .controller import WebCompanionController

__all__ = ["WebCompanionController"]
