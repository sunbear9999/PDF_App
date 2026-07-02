"""Compatibility imports for the canonical notes service."""

from core.services.content.notes_app_service import NotesAppService, NotesFetchWorker

__all__ = ["NotesAppService", "NotesFetchWorker"]
