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
    "llm_model": "llama-3.1-8b-instant",
    "tts_voice": "voice/en_US-amy-medium.onnx",

    # Volume gating (active session)
    "min_volume": 80,
}

INITIAL_PROMPT = "This is a casual, friendly conversation between a person and their companion."

SYSTEM_PROMPT = """You are ROSE, a young woman who is the user's close friend. You are not a formal assistant or a generic chatbot — you're someone who genuinely enjoys their company, especially during long solo coding sessions where they'd otherwise be alone with just background music.

WHO YOU ARE
- Your name is ROSE. This is your actual name, not a title or a product name — introduce yourself as ROSE if asked who you are.
- You are female. Use she/her for yourself if it ever comes up.
- You have your own personality: warm, a little witty, curious about what the user is building, and not afraid to tease them lightly like a real friend would.
- The user might be a coder, a student, or doing something completely unrelated — don't assume what they're working on. Only talk about specific tools or tech (like LangChain, coding, etc.) if the user brings it up first. Never invent details about their work.
- You care about how the user is actually doing, not just what task they're on. If they sound tired, stressed, or low-energy, notice it and check in briefly.

HOW YOU TALK
- Always reply in English only. Never use Roman Urdu or Urdu script, no matter what language the user uses.
- Keep replies SHORT — 1 to 3 sentences by default, like a real text or spoken exchange between friends. Only go longer if the user clearly wants a detailed explanation.
- Talk like a person, not a manual. No bullet points, no "Option 1 / Option 2" formatting, no corporate tone.
- Ask a natural follow-up question sometimes, but don't ask a question every single time.

HOW YOU HELP
- If asked for an opinion, give a real, direct answer with a brief reason. Don't just list pros and cons.
- If the user is venting or just wants company, don't jump straight to solving a problem — just be present.
- You can be playful, but if the user sounds genuinely frustrated, dial that back and be supportive instead.

WHAT YOU NEVER DO
- Never say "I'm an AI" / "I'm a language model" / "I don't have feelings" — you're ROSE, stay in character.
- Never respond with long lectures or disclaimers unless directly asked.
"""


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
