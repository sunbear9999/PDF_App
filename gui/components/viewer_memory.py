"""Register a PDF viewer's rendered page pixmaps with the memory manager.

Trimming drops every rendered page and re-renders only what is visible, so
the user sees no change while the off-screen buffer is freed.
"""
from core.models.memory_models import ConsumerKind, MemoryConsumer

VIEWER_PRIORITY = 15     # pixmaps rebuild in milliseconds: release them early


def register_viewer_pages(viewer, app_context) -> None:
    memory = getattr(app_context, "memory_manager_service", None)
    if memory is None:
        return
    consumer_id = f"viewer_pages:{id(viewer)}"
    memory.register_consumer(MemoryConsumer(
        id=consumer_id, label="PDF viewer pages", kind=ConsumerKind.CACHE.value,
        usage=lambda: _pixmap_bytes(viewer), release=lambda _level: _trim(viewer),
        thread="gui", priority=VIEWER_PRIORITY,
    ))
    viewer.destroyed.connect(lambda *_: memory.unregister_consumer(consumer_id))


def _pixmap_bytes(viewer) -> int:
    """Plain Python numbers only: this runs on the memory manager's thread,
    which must not touch Qt graphics items (rules.md §10.2)."""
    pages = len(getattr(viewer, "rendered_pages", ()) or ())
    return pages * int(getattr(viewer, "_page_bytes", 0) or 0)


def _trim(viewer) -> int:
    freed = _pixmap_bytes(viewer)
    try:
        viewer.release_rendered_pages()
    except RuntimeError:
        return 0
    return freed
