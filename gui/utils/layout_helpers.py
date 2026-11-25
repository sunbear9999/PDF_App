"""Layout helpers shared by docks that must survive being resized very narrow."""
from __future__ import annotations

from PySide6.QtWidgets import QSizePolicy, QWidget


def make_shrinkable(*widgets: QWidget, minimum_width: int = 0) -> None:
    """Let *widgets* shrink below their preferred content width.

    Qt derives a layout's minimum width from each child's ``minimumSizeHint``,
    so one row of combo boxes and labelled buttons pins the whole dock to the
    width of that row — the user cannot drag the dock any thinner. An
    ``Ignored`` horizontal policy drops that contribution to *minimum_width*,
    so the widgets elide their text instead of blocking the resize while their
    preferred size still wins whenever there is room.
    """
    for widget in widgets:
        policy = widget.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        widget.setSizePolicy(policy)
        widget.setMinimumWidth(minimum_width)
