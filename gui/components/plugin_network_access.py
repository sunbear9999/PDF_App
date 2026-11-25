"""User-mediated network permission controls shared by plugin GUI hosts."""
from __future__ import annotations

from typing import Iterable

from PySide6.QtWidgets import QMessageBox, QWidget


def set_plugin_network_permission(
    parent: QWidget,
    core,
    plugin_id: str,
    hosts: Iterable[str],
    granted: bool,
) -> bool:
    """Confirm and persist a plugin network grant or revocation."""
    service = getattr(core, "network_access_service", None)
    if service is None or not plugin_id:
        return False
    current = service.is_granted(plugin_id)
    if current == granted:
        return True
    destinations = ", ".join(str(host) for host in hosts if str(host).strip())
    if granted:
        detail = destinations or "public HTTPS hosts explicitly entered by you"
        title = "Grant Plugin Network Access"
        message = (
            f"Allow '{plugin_id}' to contact:\n\n{detail}\n\n"
            "Papyrus will still block private addresses, unsafe schemes, "
            "unlisted redirects, and oversized responses."
        )
    else:
        title = "Revoke Plugin Network Access"
        message = f"Block all future network requests from '{plugin_id}'?"
    answer = QMessageBox.question(parent, title, message)
    if answer != QMessageBox.StandardButton.Yes:
        return False
    service.set_granted(plugin_id, granted)
    return True
