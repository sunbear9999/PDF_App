# core/registries/voice_registry.py
import os


class VoiceRegistry:
    def __init__(self):
        self.voices = {}
        self._scan_local_voices()

    def _voice_dirs(self):
        """Return candidate directories that may contain .onnx voice files."""
        dirs = []
        # Legacy: models/ in cwd
        legacy = os.path.join(os.getcwd(), "models")
        if os.path.isdir(legacy):
            dirs.append(legacy)
        # New: ~/.papyrus/tts/
        new_dir = os.path.join(os.path.expanduser("~"), ".papyrus", "tts")
        if os.path.isdir(new_dir):
            dirs.append(new_dir)
        return dirs

    def _scan_local_voices(self):
        self.voices = {}
        for directory in self._voice_dirs():
            for f in sorted(os.listdir(directory)):
                if f.endswith(".onnx"):
                    display_name = (
                        f.replace(".onnx", "")
                        .replace("voice", "Voice ")
                        .replace("_", " ")
                        .replace("-", " ")
                        .title()
                    )
                    # Store path relative to scan for tts_engine compatibility,
                    # but prefer absolute paths for the new voice dir
                    if directory == os.path.join(os.getcwd(), "models"):
                        self.voices[display_name] = f  # legacy relative path
                    else:
                        self.voices[display_name] = os.path.join(directory, f)

    def reload(self) -> None:
        """Re-scan voice directories (called after install/remove)."""
        self._scan_local_voices()

    def get_available_voices(self):
        """Returns a dict of {Display Name: filename or path}"""
        return self.voices
