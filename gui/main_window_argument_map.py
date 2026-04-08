from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class MainWindowArgumentMap:
    """Argument map banner + needs checks + generation orchestration."""

    def __init__(self, main_window):
        self.main_window = main_window

    @property
    def w(self):
        return self.main_window

    def build_argument_map_banner(self) -> None:
        w = self.w
        w.argument_map_banner = QFrame()
        w.argument_map_banner.setFixedHeight(50)
        banner_layout = QHBoxLayout(w.argument_map_banner)
        banner_layout.setContentsMargins(20, 0, 10, 0)

        w.lbl_argument_map_banner = QLabel(
            "🧠 This PDF has no argument map. Generate one now — it takes about a minute and greatly improves LLM results."
        )
        banner_layout.addWidget(w.lbl_argument_map_banner)
        banner_layout.addStretch()

        w.btn_generate_argument_map_banner = QPushButton(
            "Generate Argument Map"
        )
        w.btn_generate_argument_map_banner.setObjectName("ArgumentMapButton")
        w.btn_generate_argument_map_banner.clicked.connect(
            w._trigger_argument_map_generation
        )
        banner_layout.addWidget(w.btn_generate_argument_map_banner)

        btn_dismiss = QPushButton("Dismiss")
        btn_dismiss.setObjectName("ArgumentMapDismissButton")
        btn_dismiss.clicked.connect(w.argument_map_banner.hide)
        banner_layout.addWidget(btn_dismiss)

        w.argument_map_banner.hide()

    def check_needs_ocr(self) -> None:
        w = self.w
        if not w.viewer.doc:
            return

        # Best-effort heuristic; failure should not break PDF switching.
        try:
            pages_to_check = min(3, len(w.viewer.doc))
            total_text = "".join(
                [w.viewer.doc.load_page(i).get_text() for i in range(pages_to_check)]
            )
            if len(total_text.strip()) < 50:
                pass
        except Exception:
            pass

    def check_needs_argument_map(self) -> None:
        w = self.w
        if not w.current_file_path:
            return
        w._set_argument_map_button_state(running=False)

    def trigger_auto_ocr(self) -> None:
        self.w.toggle_tool_panel("OCR")

    def trigger_argument_map_generation(self) -> None:
        w = self.w
        if not w.current_file_path:
            return
        w._set_argument_map_button_state(running=True)
        w.start_background_indexing([w.current_file_path])

    def toggle_argument_map_generation(self) -> None:
        w = self.w
        if getattr(w, "ai_indexing_worker", None) and w.ai_indexing_worker.isRunning():
            w.ai_indexing_worker.stop()
            w.ai_indexing_worker.wait(3000)
            w._show_indexing_status("⚠️ Argument map generation canceled.")
            w._set_argument_map_button_state(running=False)
            return
        w._trigger_argument_map_generation()

    def set_argument_map_button_state(self, running: bool) -> None:
        # Intentionally unchanged placeholder (original code was `pass`).
        # Kept to preserve existing UI behavior and API surface.
        _ = running
        return

