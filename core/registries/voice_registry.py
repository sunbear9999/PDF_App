# core/engine/registries/voice_registry.py
import os

class VoiceRegistry:
    def __init__(self):
        self.voices = {}
        self._scan_local_voices()

    def _scan_local_voices(self):
        models_dir = os.path.join(os.getcwd(), "models")
        if os.path.exists(models_dir):
            onnx_files = [f for f in os.listdir(models_dir) if f.endswith(".onnx")]
            for f in sorted(onnx_files):
                display_name = f.replace(".onnx", "").replace("voice", "Voice ").title()
                self.voices[display_name] = f

    def get_available_voices(self):
        """Returns a dict of {Display Name: filename.onnx}"""
        return self.voices