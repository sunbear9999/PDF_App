# core/services/tts_app_service.py
import os
import time
from PySide6.QtCore import QObject, QThread, Signal
from core.events.event_bus import EventBus
from core.tts_engine import generate_audio
from core.registries.voice_registry import VoiceRegistry
from core.events.domains.tool_events import TTSIntent, TTSPayload, TTSStatus, TTSStatusPayload

class TTSWorker(QThread):
    progress_updated = Signal(str)
    
    def __init__(self, text, voice, speed, parent=None):
        super().__init__(parent)
        self.text = text
        self.voice = voice
        self.speed = speed
        self.result = None
        self.filename = None

    def run(self):
        audio_dir = os.path.join(os.getcwd(), "audio")
        os.makedirs(audio_dir, exist_ok=True)
        
        self.filename = f"tts_output_{int(time.time())}.wav"
        output_filepath = os.path.join(audio_dir, self.filename)
        
        self.result = generate_audio(
            self.text, 
            output_filepath, 
            voice_file=self.voice, 
            speed=self.speed, 
            progress_callback=lambda msg: self.progress_updated.emit(msg)
        )

class TTSAppService(QObject):
    def __init__(self):
        super().__init__()
        self.bus = EventBus.get_instance()
        self.registry = VoiceRegistry()
        self.worker = None
        self.bus.tts_action_requested.connect(self._handle_intent)

    def _handle_intent(self, intent: TTSIntent, payload: TTSPayload):
        if intent == TTSIntent.FETCH_VOICES:
            self.bus.tts_status_updated.emit(
                TTSStatus.VOICES_LOADED,
                TTSStatusPayload(status=TTSStatus.VOICES_LOADED, voices=self.registry.get_available_voices()),
            )
        elif intent == TTSIntent.GENERATE:
            self._start_generation(payload)

    def _start_generation(self, payload: TTSPayload):
        if self.worker and self.worker.isRunning():
            return # Prevent concurrent overlapping runs
            
        text = payload.get("text", "").strip()
        voice = payload.get("voice_file", "voice1.onnx")
        speed = payload.get("speed", 1.0)
        
        if not text:
            self.bus.tts_status_updated.emit(
                TTSStatus.ERROR,
                TTSStatusPayload(status=TTSStatus.ERROR, msg="No text provided."),
            )
            return

        self.bus.tts_status_updated.emit(
            TTSStatus.RUNNING,
            TTSStatusPayload(status=TTSStatus.RUNNING, msg="Generating audio... This may take a moment."),
        )
        
        self.worker = TTSWorker(text, voice, speed)
        self.worker.progress_updated.connect(
            lambda msg: self.bus.tts_status_updated.emit(
                TTSStatus.RUNNING,
                TTSStatusPayload(status=TTSStatus.RUNNING, msg=msg),
            )
        )
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_worker_finished(self):
        if self.worker.result is True:
            self.bus.tts_status_updated.emit(
                TTSStatus.COMPLETE,
                TTSStatusPayload(
                    status=TTSStatus.COMPLETE,
                    file=self.worker.filename,
                    msg=f"✅ Audio saved to: audio/{self.worker.filename}",
                ),
            )
        else:
            self.bus.tts_status_updated.emit(
                TTSStatus.ERROR,
                TTSStatusPayload(status=TTSStatus.ERROR, msg=str(self.worker.result)),
            )
