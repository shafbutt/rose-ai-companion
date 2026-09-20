"""
ROSE UI Bridge
Clean communication layer between the Python backend and the pywebview frontend.
Replaces the old pattern of injecting raw JavaScript strings from Python.

Architecture:
  - Backend pushes events (messages, state changes) into a thread-safe queue.
  - Frontend polls `api.poll()` every 250 ms to receive events + live state.
  - Frontend calls `api.*` methods to read settings, save settings, etc.
  - pywebview serialises return values as JSON automatically.
"""
import json
import time
import threading
from config import settings


class UIBridge:
    """Exposed to the frontend as `window.pywebview.api`."""

    def __init__(self):
        self._events = []
        self._lock = threading.Lock()
        self._state = "booting"
        self._audio_level = 0.0
        self._conversation = []          # [{sender, text, time}, ...]
        self._mood_label = "not yet detected"
        self._mood_confidence = 0
        self._task_count = 0
        self._start_time = time.time()
        self._api_status = "connected"
        self._mic_device = f"Device {settings.get('device_index')}"
        self._groq_ok = False

    # ------------------------------------------------------------------ #
    #  Backend → UI  (called from the daemon thread)
    # ------------------------------------------------------------------ #

    def set_state(self, state):
        """state: dormant | listening | thinking | speaking"""
        self._state = state
        self._push("state", {"state": state})

    def emit_message(self, sender, text):
        ts = time.strftime("%H:%M:%S")
        self._conversation.append({"sender": sender, "text": text, "time": ts})
        self._push("message", {"sender": sender, "text": text, "time": ts})

    def set_mood(self, label, confidence):
        self._mood_label = label
        self._mood_confidence = confidence
        self._push("mood", {"label": label, "confidence": confidence})

    def set_task_count(self, count):
        self._task_count = count

    def set_audio_level(self, level):
        """0.0 – 1.0, called from the audio callback."""
        self._audio_level = level

    def set_api_status(self, status):
        self._api_status = status
        self._push("api_status", {"status": status})

    # ------------------------------------------------------------------ #
    #  Frontend → Backend  (called via window.pywebview.api.*)
    # ------------------------------------------------------------------ #

    def get_settings(self):
        return settings.get_all()

    def save_settings(self, changes_json):
        try:
            changes = json.loads(changes_json) if isinstance(changes_json, str) else changes_json
            # type-coerce known numeric / bool fields
            _coerce = {
                "wake_sensitivity": float, "silence_threshold": int,
                "silence_limit": float, "max_duration": int,
                "inactivity_timeout": int, "whistle_hold_time": float,
                "name_gating": lambda v: v in (True, "true", "True", 1),
            }
            for k, fn in _coerce.items():
                if k in changes:
                    changes[k] = fn(changes[k])
            settings.update(changes)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_system_info(self):
        return {
            "stt_model": "faster-whisper base",
            "tts_model": "piper en_US-amy-medium",
            "llm_model": settings.get("llm_model"),
            "api_status": self._api_status,
            "mic_device": self._mic_device,
        }

    def get_conversation(self):
        return list(self._conversation)

    def test_api_connection(self):
        """Lightweight check that the Groq client is reachable."""
        try:
            from rose_app import groq_client
            groq_client.chat.completions.create(
                model=settings.get("llm_model"),
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            self._api_status = "connected"
            return {"ok": True}
        except Exception as e:
            self._api_status = "error"
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    #  Poll endpoint  (called by frontend every ~250 ms)
    # ------------------------------------------------------------------ #

    def poll(self):
        state_snapshot = {
            "state": self._state,
            "audio_level": self._audio_level,
            "mood_label": self._mood_label,
            "mood_confidence": self._mood_confidence,
            "task_count": self._task_count,
            "uptime": int(time.time() - self._start_time),
            "api_status": self._api_status,
            "mic_device": self._mic_device,
        }
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return {"state": state_snapshot, "events": events}

    # ------------------------------------------------------------------ #
    #  Internal
    # ------------------------------------------------------------------ #

    def _push(self, event_type, data):
        with self._lock:
            if len(self._events) > 100:
                self._events = self._events[-50:]
            self._events.append({"type": event_type, "data": data})
