"""Event handlers for the headless source-intelligence facade."""
from core.services.intelligence.note_artifacts import retire_highlight


def source_added(service, payload) -> None:
    source_id = getattr(payload, "source_id", None)
    path = getattr(payload, "path", None)
    if not source_id and path and service.project_manager:
        source = service.project_manager.get_source_record_by_path(path)
        source_id = source.get("id") if source else None
    if not source_id:
        return
    source = service._source_by_id(source_id)
    path = (source or {}).get("path") or path
    service.run_after_hashing(path, lambda: _register_source(service, source_id, payload))


def _register_source(service, source_id, payload) -> None:
    source = service._source_by_id(source_id)
    if source:
        selection = (getattr(payload, "context", None) or {}).get(
            "processing_selection"
        )
        if selection and service.policy_service:
            service.policy_service.set_source_selection(source_id, selection)
        service.artifact_service.ensure_source_artifact(source)
        try:
            service.artifact_service.mark_source_stale(source_id)
        except Exception:
            pass
    if service.scheduler:
        service.scheduler.handle_source_added(source_id)


def highlight_changed(service, payload) -> None:
    data = getattr(payload, "highlight_data", None) or {}
    highlight_id = getattr(payload, "annot_id", None) or data.get("id")
    if highlight_id and service.artifact_service:
        service.artifact_service.sync_annotation_artifact(highlight_id)


def video_status(service, event, payload) -> None:
    if str(getattr(event, "name", event)) != "TRANSCRIPTION_COMPLETED":
        return
    source_id = getattr(payload, "source_id", None)
    if source_id and service.artifact_service:
        service.artifact_service.sync_transcript_artifacts(source_id)
    if source_id and service.scheduler:
        service.scheduler.enqueue_source(source_id)


def project_closing(service) -> None:
    """Jobs bound to the closing project must not write into the next one."""
    service._background_generation += 1
    if getattr(service, "_background", None) is not None:
        service._background.clear()
    if service.scheduler and hasattr(service.scheduler, "on_project_closing"):
        service.scheduler.on_project_closing()


def source_removed(service, payload) -> None:
    source_id = getattr(payload, "source_id", None)
    if source_id:
        service._submit_background(lambda: service.purge_source(source_id))


def highlight_deleted(service, payload) -> None:
    highlight_id = getattr(payload, "annot_id", None)
    if highlight_id and service.artifact_service:
        retire_highlight(service.artifact_service, highlight_id)


def ocr_completed(service, event, payload) -> None:
    """OCR rewrote a source file in place: its text is new, so resume processing.

    A copy saved as a new file arrives as ``source_added`` instead.
    """
    if str(getattr(event, "name", event)) != "COMPLETE" or getattr(payload, "mode", "") != "replace":
        return
    path = getattr(payload, "file_path", None)
    source = service.project_manager.get_source_record_by_path(path) if path and service.project_manager else None
    source_id = getattr(payload, "source_id", None) or (source or {}).get("id")
    if not source_id:
        return
    service.run_after_hashing(path, lambda: _resume_after_ocr(service, source_id))


def _resume_after_ocr(service, source_id) -> None:
    try:
        service.artifact_service.mark_source_stale(source_id)
    except Exception:
        pass
    if service.scheduler:
        service.scheduler.enqueue_source(source_id)
