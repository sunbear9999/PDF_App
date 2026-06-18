"""
Regression tests for shortcut capture in the settings dialog.
"""
from __future__ import annotations

import sys
import tempfile
import unittest

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent, QKeySequence
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget
except ModuleNotFoundError:  # pragma: no cover - local CI may omit Qt.
    QEvent = None
    Qt = None
    QKeyEvent = None
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TestShortcutCapture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_key_sequence_from_qt6_key_event(self):
        from gui.components.dialogs.settings_dialog import _key_sequence_from_event

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_S,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )

        self.assertEqual(_key_sequence_from_event(event), "Ctrl+Shift+S")

    def test_key_sequence_fallback_unwraps_enum_values(self):
        from gui.components.dialogs.settings_dialog import _key_sequence_from_event

        class _FakeEvent:
            def key(self):
                return Qt.Key.Key_F

            def modifiers(self):
                return Qt.KeyboardModifier.ControlModifier

        self.assertEqual(_key_sequence_from_event(_FakeEvent()), "Ctrl+F")

    def test_unbound_shortcut_activates_immediately_after_override(self):
        from gui.managers.keybinding_registry import KeybindingRegistry, KeySpec

        parent = QWidget()
        activations = []
        with tempfile.TemporaryDirectory() as temp_dir:
            registry = KeybindingRegistry(temp_dir)
            registry.register(
                KeySpec(
                    "app.prompt_editor",
                    "Prompt Editor",
                    "global",
                    "Open the AI prompt editor",
                    "",
                )
            )

            shortcut = registry.bind("app.prompt_editor", lambda: activations.append(True), parent)

            self.assertIsNotNone(shortcut)
            self.assertEqual(
                shortcut.key().toString(QKeySequence.SequenceFormat.PortableText),
                "",
            )

            registry.set_override("app.prompt_editor", "Ctrl+Shift+P")

            self.assertEqual(
                shortcut.key().toString(QKeySequence.SequenceFormat.PortableText),
                "Ctrl+Shift+P",
            )

            parent.show()
            parent.activateWindow()
            self.app.processEvents()
            QTest.keyClick(
                parent,
                Qt.Key.Key_P,
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
            )
            self.app.processEvents()

            self.assertEqual(activations, [True])


if __name__ == "__main__":
    unittest.main()
