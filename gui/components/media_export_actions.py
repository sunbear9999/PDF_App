"""Shared audio/video save dialog, registry action and background export."""
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, QThread, Qt, Signal
from PySide6.QtWidgets import QApplication, QFileDialog

from gui.registry.action_spec import ActionSpec
from gui.registry.context_menu_registry import ContextMenuContext

ACTION_ID = "video.export_transcript"
ACTION_LABEL = "Save with Transcript…"
MENU_CONTEXT = "media_player"
TEXT_FILTERS = {
    "Plain text transcript (*.txt)": ("txt", "txt"),
    "SubRip subtitles (*.srt)": ("srt", "srt"),
    "WebVTT subtitles (*.vtt)": ("vtt", "vtt"),
}


def _save_from_context(context):
    actions = context.payload["viewer"].export_actions
    source = context.payload.get("export_source")
    if source:
        actions.save_source(*source)
    else:
        actions.save()


class _ExportWorker(QThread):
    saved = Signal(str)
    failed = Signal(str)

    def __init__(self, service, registry, arguments, source_id=None):
        super().__init__(QApplication.instance())
        self.service, self.registry, self.arguments = service, registry, arguments
        self.job = None
        self.source_id = source_id

    def cancelled(self):
        return self.isInterruptionRequested() or bool(self.job and self.job.abort_event.is_set())

    def run(self):
        try:
            if self.source_id is not None:
                path, _segments, destination, format_id = self.arguments
                result = self.service.export_source_transcript(
                    self.source_id, path, destination, format_id, cancel_check=self.cancelled)
            else:
                result = self.service.export_transcript(*self.arguments, cancel_check=self.cancelled)
            self.saved.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.arguments = ()
            if self.job:
                self.registry.complete_job_threadsafe(self.job.id)


class MediaExportActions(QObject):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer, self.ctx = viewer, viewer._ctx
        self._workers = []
        self._dialog = None
        self._bound_keys = None
        self.register_actions()
        self.watch(viewer)
        for name in ("video_widget", "caption_label", "status_label"):
            widget = getattr(viewer, name, None)
            if widget is not None:
                self.watch(widget)
        viewer.bus.project_clearing_started.connect(self.cancel)
        targets = getattr(getattr(self.ctx, "help_service", None), "ui_target_registry", None)
        if targets:
            identity = f"media.export.{id(viewer)}"
            targets.register_widget(identity, viewer, "core.media.export_transcript")
            viewer.destroyed.connect(lambda *_, r=targets, key=identity: r.unregister(key))
        processes = getattr(self.ctx, "process_registry", None)
        if processes:
            processes.job_removed.connect(self._job_removed)

    def register_actions(self):
        """Registries are attached to AppContext after the initial widgets exist."""
        registry = getattr(self.ctx, "action_registry", None)
        if registry and registry.get(ACTION_ID) is None:
            registry.register(ActionSpec(
                action_id=ACTION_ID, label=ACTION_LABEL, callback=_save_from_context,
                mounts=[f"context_menu:{MENU_CONTEXT}"],
                tooltip="Choose a media copy with selectable subtitles, or a TXT, SRT or WebVTT transcript.",
            ))
        keys = getattr(self.ctx, "keybinding_registry", None)
        if keys and self._bound_keys is not keys:
            callback = getattr(self.viewer, "save_selected_transcript", self.save)
            keys.bind(ACTION_ID, callback, self.viewer, Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self._bound_keys = keys

    def watch(self, widget):
        widget.installEventFilter(self)

    def available(self):
        return bool(getattr(self.viewer, "video_path", "") and getattr(self.viewer, "segments", None)
                    and getattr(self.ctx, "video_transcription_service", None)
                    and getattr(self.ctx, "process_registry", None))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show:
            self.register_actions()
        if event.type() == QEvent.Type.ContextMenu and hasattr(self.viewer, "video_path"):
            menu = self.build_menu(self.viewer)
            if menu is not None:
                menu.aboutToHide.connect(menu.deleteLater)
                menu.popup(event.globalPos())
                return True
        return super().eventFilter(watched, event)

    def build_menu(self, parent, source=None):
        self.register_actions()
        registry = getattr(self.ctx, "context_menu_registry", None)
        if registry is None:
            return None
        path, source_id = source[:2] if source else (self.viewer.video_path, self.viewer.source_id)
        context = ContextMenuContext(
            context_type=MENU_CONTEXT, selected_ids=[source_id],
            payload={"viewer": self.viewer, "path": path,
                     "source_id": source_id, "export_source": source}, event_bus=self.viewer.bus,
        )
        menu = registry.build_menu(context, parent_widget=parent)
        for action in menu.actions():
            if action.text() == ACTION_LABEL:
                action.setEnabled(bool(source_id and getattr(self.ctx, "video_transcription_service", None))
                                  if source else self.available())
        return menu

    def append_to_menu(self, menu, source=None):
        extra = self.build_menu(menu, source)
        if extra is not None:
            menu.addSeparator()
            menu.addActions(extra.actions())

    def show_transcript_menu(self, pos):
        view = self.viewer.transcript_view
        menu = view.createStandardContextMenu()
        self.append_to_menu(menu)
        if view.textCursor().hasSelection():
            menu.addSeparator()
            action = menu.addAction("Highlight Selected Transcript Text")
            action.triggered.connect(self.viewer.save_note_at_current_time)
        menu.exec(view.mapToGlobal(pos))
        menu.deleteLater()

    def save(self):
        if not self.available():
            return
        self._show_save_dialog(self.viewer.video_path, self.viewer.segments, self.viewer.source_type)

    def save_source(self, path, source_id, source_type):
        if source_type in {"video", "audio"} and source_id:
            self._show_save_dialog(path, None, source_type, source_id)

    def _show_save_dialog(self, path, segments, source_type, source_id=None):
        if self._dialog is not None:
            self._dialog.raise_()
            return
        extension = "mka" if source_type == "audio" else "mkv"
        media_filter = f"Media copy with selectable subtitles (*.{extension})"
        filters = {media_filter: ("media", extension), **TEXT_FILTERS}
        dialog = QFileDialog(self.viewer, "Save with Transcript — choose a file type")
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setNameFilters(list(filters))
        dialog.setDefaultSuffix(extension)
        dialog.selectFile(f"{Path(path).stem}-with-transcript.{extension}")
        dialog.filterSelected.connect(lambda label: self._select_format(dialog, filters[label][1]))
        dialog.accepted.connect(lambda: self._accept(dialog, filters, path, segments, source_id))
        dialog.finished.connect(self._dialog_finished)
        self._dialog = dialog
        manager = getattr(self.ctx, "dialog_manager", None)
        if manager:
            manager.show_instance(dialog)
        else:
            dialog.open()

    def _select_format(self, dialog, extension):
        selected = dialog.selectedFiles()
        dialog.setDefaultSuffix(extension)
        if selected:
            dialog.selectFile(str(Path(selected[0]).with_suffix(f".{extension}")))

    def _dialog_finished(self, _result):
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.deleteLater()

    def _accept(self, dialog, filters, path, segments, source_id=None):
        destination = dialog.selectedFiles()[0]
        format_id, extension = filters[dialog.selectedNameFilter()]
        if Path(destination).suffix.lower() != f".{extension}":
            self._status(f"Choose a filename ending in .{extension} for this file type.", error=True)
            return
        worker = _ExportWorker(self.ctx.video_transcription_service, self.ctx.process_registry,
                               (path, segments, destination, format_id), source_id)
        worker.saved.connect(self._saved, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self.viewer.destroyed.connect(worker.requestInterruption)
        self._workers.append(worker)
        self.ctx.process_registry.enqueue_runner(worker, "Save with Transcript", job_type="Export",
                                                  is_express=True)
        self._status("Saving transcript export…")

    def _saved(self, path):
        self.viewer.bus.status_message_requested.emit(f"Saved transcript export: {path}", 8000)
        if hasattr(self.viewer, "_set_status"):
            self.viewer._set_status(f"Saved: {path}")

    def _failed(self, message):
        self._status(message, error=True)

    def _status(self, message, error=False):
        if hasattr(self.viewer, "_set_status"):
            self.viewer._set_status(message, error=error)
        else:
            self.viewer.bus.status_message_requested.emit(message, 8000)

    def _finished(self):
        worker = self.sender()
        if worker in self._workers:
            self._workers.remove(worker)

    def _job_removed(self, job_id):
        for worker in list(self._workers):
            if worker.job and worker.job.id == job_id and not worker.isRunning() and not worker.isFinished():
                self._workers.remove(worker)
                worker.deleteLater()  # A cancelled queued export never emits finished.

    def cancel(self, *_):
        if self._dialog is not None:
            self._dialog.reject()
        for worker in list(self._workers):
            worker.requestInterruption()
            if worker.job and not worker.isRunning() and not worker.isFinished():
                self.ctx.process_registry.cancel_job(worker.job.id)
