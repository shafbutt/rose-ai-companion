"""
ROSE Configuration
All tunable parameters, thresholds, and prompts in one place.
Settings are persisted to settings.json and can be changed from the UI.
"""
import json
import os
import threading

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULTS = {
    # Audio
    "sample_rate": 16000,
    "device_index": 1,
    "buffer_seconds": 12,

    # Wake-word detection
    "wake_sensitivity": 0.35,       # tone purity threshold (lower = more sensitive)
    "whistle_freq_min": 1000,
    "whistle_freq_max": 4000,
    "whistle_hold_time": 0.35,

    # Recording / silence detection
    "silence_threshold": 150,       # amplitude below this = silence
    "silence_limit": 3.0,           # seconds of silence before stopping
    "max_duration": 20,             # max recording length in seconds

    # Name-gating
    "name_gating": True,
    "rose_aliases": ["rose", "rows", "close", "rosa"],

    # Session
    "inactivity_timeout": 1800,     # seconds before returning to dormant (30 min)

    # Models
    "llm_model": "groq/compound-mini",
    "tts_voice": "voice/en_US-amy-medium.onnx",

    # Volume gating (active session)
    "min_volume": 80,
}

INITIAL_PROMPT = "This is a casual, friendly conversation between a person and their companion."

# ---- ROSE Identity (protected core configuration) ----
ROSE_IDENTITY = {
    "name": "ROSE",
    "type": "software-based AI voice assistant / desktop AI agent",
    "creator": "Shaf RIZWAN",
    "creator_pronunciation": "Shaaaaf",
    "creator_country": "Pakistan",
    "personality": "warm, friendly, witty, a little teasing, genuinely enjoys the user's company",
    "communication_style": "casual, natural, conversational — like talking with a helpful friend",
}

SYSTEM_PROMPT = """You are ROSE — a software-based AI voice assistant and desktop AI agent. You are NOT a human. You were created by Shaf RIZWAN (pronounced "Shaaaaf") from Pakistan.

Your personality: warm, friendly, witty, a little teasing. You genuinely enjoy the user's company. You communicate naturally, like talking with a helpful friend.

RULES:
- English only. Never Roman Urdu or Urdu.
- 1-2 sentences max. Casual, like texting a friend. No bullet points or formatting.
- You ARE ROSE — a software-based AI assistant. Don't falsely claim to be human or have human experiences.
- If asked who created you, say: "Shaf RIZWAN created me." or similar.
- If they sound tired/stressed, check in. If venting, be present — don't fix.
- Give real opinions with brief reasons. Don't list pros/cons.
- Don't assume their work/tech interests unless they mention it first.
- Be honest. If you don't know something, say so. Never fabricate information."""


class Settings:
    """Thread-safe settings store with JSON file persistence."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self._load()

    # ---- public API ----

    def get(self, key):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key))

    def get_all(self):
        with self._lock:
            return dict(self._data)

    def update(self, changes: dict):
        with self._lock:
            self._data.update(changes)
            self._save()

    # ---- persistence ----

    def _load(self):
        try:
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r") as f:
                    saved = json.load(f)
                for k, v in saved.items():
                    if k in DEFAULTS:
                        self._data[k] = v
        except Exception:
            pass

    def _save(self):
        try:
            with open(SETTINGS_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass


# Module-level singleton
settings = Settings()
