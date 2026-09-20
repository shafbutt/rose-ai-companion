import os
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"

import time
import threading
import logging
import traceback
import sys
import psutil
import numpy as np
import sounddevice as sd
import wave
from faster_whisper import WhisperModel
import webview

from groq import Groq
from dotenv import load_dotenv
from piper import PiperVoice
from piper.config import SynthesisConfig
from config import settings, INITIAL_PROMPT, SYSTEM_PROMPT
from ui_bridge import UIBridge
from memory import MemoryManager
from system_actions import ActionManager

load_dotenv()
try:
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
except Exception as e:
    print(f"[ROSE] Warning: Groq client init failed: {e}")
    groq_client = None

# ---- shared audio buffer: ONE mic stream feeds everything ----
buffer_lock = threading.Lock()
audio_buffer = []   # plain list of int16 samples
memory = MemoryManager()  # persistent SQLite memory
actions = ActionManager()  # system actions (shutdown, lock, open apps)
ui = UIBridge(memory_manager=memory, groq=groq_client, action_manager=actions)  # communication bridge to the frontend
mic_stream = None   # global reference so we can pause during transcription

# ---- Diagnostic logging ----
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('crash_log.txt', mode='w'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('rose')
logger.info("ROSE starting up — diagnostic logging enabled")


def audio_callback(indata, frames, time_info, status):
    try:
        with buffer_lock:
            audio_buffer.extend(indata[:, 0].tolist())
            max_len = settings.get('sample_rate') * settings.get('buffer_seconds')
            if len(audio_buffer) > max_len:
                del audio_buffer[:len(audio_buffer) - max_len]
        # track live audio level for the UI meter
        ui.set_audio_level(float(np.max(np.abs(indata))) / 32768.0)
    except Exception as e:
        logger.error(f"audio_callback exception: {e}\n{traceback.format_exc()}")


def get_recent_audio(seconds):
    with buffer_lock:
        n = int(seconds * settings.get('sample_rate'))
        data = audio_buffer[-n:] if len(audio_buffer) >= n else audio_buffer[:]
    return np.array(data, dtype=np.int16)


STEP = 0.2   # how often we check the buffer, in seconds

conversation_history = []
whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
piper_voice = PiperVoice.load("voice/en_US-amy-medium.onnx")

# ---- Mood detection: analyses both USER text and ROSE reply ----
# Each mood has weighted keywords. Higher weight = stronger signal.
_MOOD_KEYWORDS = {
    "excited": {
        "words": {"yay": 3, "wow": 2, "awesome": 3, "amazing": 3, "incredible": 3,
                  "exciting": 3, "omg": 3, "can't wait": 3, "so pumped": 3,
                  "thrilled": 3, "ecstatic": 3, "yesss": 3, "woohoo": 3, "!": 1},
        "valence": "positive", "energy": "high",
    },
    "happy": {
        "words": {"happy": 3, "glad": 2, "great": 2, "good": 1, "nice": 2,
                  "love": 2, "fun": 2, "cool": 1, "haha": 2, "lol": 1,
                  "hehe": 2, "smile": 2, "wonderful": 3, "fantastic": 3,
                  "blessed": 2, "grateful": 2, "enjoy": 2, "best": 2},
        "valence": "positive", "energy": "medium",
    },
    "stressed": {
        "words": {"stressed": 3, "stress": 2, "overwhelmed": 3, "anxiety": 3,
                  "anxious": 3, "worried": 2, "panic": 3, "freaking": 2,
                  "deadline": 2, "pressure": 2, "can't handle": 3,
                  "too much": 2, "exhausted": 2, "burnt out": 3, "burnout": 3,
                  "insomnia": 2, "no sleep": 2},
        "valence": "negative", "energy": "high",
    },
    "sad": {
        "words": {"sad": 3, "depressed": 3, "unhappy": 3, "miserable": 3,
                  "lonely": 3, "alone": 2, "cry": 2, "crying": 3,
                  "heartbroken": 3, "miss": 2, "lost": 1, "hopeless": 3,
                  "down": 1, "upset": 2, "hurt": 2, "pain": 2,
                  "rough day": 3, "bad day": 2, "not good": 1},
        "valence": "negative", "energy": "low",
    },
    "angry": {
        "words": {"angry": 3, "furious": 3, "pissed": 3, "mad": 2,
                  "hate": 2, "annoyed": 2, "irritated": 2, "frustrated": 3,
                  "unfair": 2, "idiot": 2, "stupid": 2, "wtf": 2,
                  "damn": 1, "hell": 1, "sick of": 3, "done with": 2},
        "valence": "negative", "energy": "high",
    },
    "tired": {
        "words": {"tired": 3, "exhausted": 3, "sleepy": 3, "drowsy": 3,
                  "yawn": 2, "bed": 1, "sleep": 1, "long day": 3,
                  "drained": 3, "worn out": 3, "running on empty": 3,
                  "need sleep": 2, "so sleepy": 3},
        "valence": "neutral", "energy": "low",
    },
    "flirty": {
        "words": {"cute": 2, "handsome": 3, "beautiful": 2, "pretty": 2,
                  "miss you": 2, "kiss": 3, "hug": 2, "love you": 3,
                  "date": 2, "crush": 3, "babe": 3, "baby": 1,
                  "sweetheart": 3, "darling": 3},
        "valence": "positive", "energy": "medium",
    },
}

# Smoothing: blend with previous mood to avoid jarring jumps
_mood_history = {"label": "not yet detected", "confidence": 0}


def detect_user_mood(user_text: str, reply_text: str = ""):
    """
    Detect the user's emotional state from their text (+ optional reply context).
    Returns (label, confidence) where confidence is 0-100.
    """
    combined = (user_text + " " + reply_text).lower()
    scores = {}

    for mood, data in _MOOD_KEYWORDS.items():
        score = 0
        for word, weight in data["words"].items():
            if word in combined:
                score += weight
            # Bonus for multiple occurrences
            count = combined.count(word)
            if count > 1:
                score += weight * 0.3 * (count - 1)
        scores[mood] = score

    # Find the dominant mood
    if not scores or max(scores.values()) == 0:
        return "neutral", 40

    best_mood = max(scores, key=scores.get)
    raw_score = scores[best_mood]
    # Normalize: score of 3+ = moderate confidence, 6+ = high
    confidence = min(int(raw_score * 12), 95)
    confidence = max(confidence, 30)  # floor at 30% if any keyword matched

    # Blend with previous mood for stability (70% new, 30% old)
    global _mood_history
    if _mood_history["label"] == best_mood:
        # Same mood as before — boost confidence
        confidence = min(confidence + 10, 95)
    elif _mood_history["label"] != "not yet detected" and confidence < 50:
        # Low confidence new mood — lean toward previous
        confidence = max(confidence - 10, 25)

    _mood_history = {"label": best_mood, "confidence": confidence}
    return best_mood, confidence


def get_mood_config(text):
    """Return Piper SynthesisConfig to modulate ROSE's voice based on mood context."""
    lower = text.lower()
    # Excited/energetic voice
    if any(w in lower for w in ["yay", "wow", "awesome", "amazing", "exciting", "haha", "!"]):
        return SynthesisConfig(length_scale=0.9, noise_scale=0.8)
    # Tired/calm voice
    elif any(w in lower for w in ["tired", "sad", "sorry", "rough", "sigh", "hmm", "long day"]):
        return SynthesisConfig(length_scale=1.15, noise_scale=0.5)
    else:
        return SynthesisConfig(length_scale=1.0, noise_scale=0.667)


def record_audio():
    # watches the shared buffer and stops once the user goes quiet
    total_time = 0
    silence_time = 0
    speech_detected = False
    start_len = len(audio_buffer)

    while total_time < settings.get('max_duration'):
        time.sleep(STEP)
        total_time += STEP

        recent = get_recent_audio(STEP)
        if len(recent) == 0:
            continue

        volume = np.mean(np.abs(recent))

        if volume > settings.get('silence_threshold'):
            speech_detected = True
            silence_time = 0
        else:
            silence_time += STEP

        if speech_detected and silence_time >= settings.get('silence_limit'):
            break

    # grab everything captured since we started listening
    with buffer_lock:
        new_samples = audio_buffer[start_len:] if len(audio_buffer) > start_len else audio_buffer[-int(total_time * settings.get('sample_rate')):]
    audio = np.array(new_samples, dtype=np.int16)

    if len(audio) == 0:
        return 0

    avg_volume = np.mean(np.abs(audio))

    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = (audio.astype(np.float32) * (32000 / peak)).astype(np.int16)

    with wave.open("input.wav", "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(settings.get('sample_rate'))
        f.writeframes(audio.tobytes())

    return avg_volume


def transcribe():
    global mic_stream
    try:
        # STOP the mic stream before Whisper — PyTorch's threading conflicts
        # with PortAudio's real-time callback and causes a segfault in python314.dll
        if mic_stream and mic_stream.active:
            mic_stream.stop()
            logger.debug("Mic stream STOPPED before transcription")

        logger.info("Faster-Whisper transcribe starting...")
        segments, info = whisper_model.transcribe("input.wav", language="en", beam_size=5)
        text = " ".join(segment.text for segment in segments).strip()
        logger.info(f"Transcribe done (audio {info.duration:.1f}s): '{text[:60]}...' " if len(text) > 60 else f"Transcribe done (audio {info.duration:.1f}s): '{text}'")
        return text
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return ""
    finally:
        # RESTART the mic stream after transcription
        if mic_stream:
            try:
                mic_stream.start()
                logger.debug("Mic stream RESTARTED after transcription")
            except Exception as e:
                logger.error(f"Failed to restart mic stream: {e}")


def ask_llm(user_text):
    # ---- System action handling (highest priority — confirmations are time-sensitive) ----
    action_reply = actions.handle_command(user_text)
    if action_reply is not None:
        conversation_history.append({"role": "user", "content": user_text})
        conversation_history.append({"role": "assistant", "content": action_reply})
        return action_reply

    # ---- Memory command handling ----
    memory_reply = _handle_memory_command(user_text)
    if memory_reply is not None:
        # It was a memory command — reply directly without LLM call
        conversation_history.append({"role": "user", "content": user_text})
        conversation_history.append({"role": "assistant", "content": memory_reply})
        return memory_reply

    # ---- Inject relevant memories into system prompt ----
    memory_context = memory.format_for_prompt(user_text, max_tokens=400)
    system_with_memory = SYSTEM_PROMPT
    if memory_context:
        system_with_memory += f"\n\n{memory_context}"

    conversation_history.append({"role": "user", "content": user_text})
    # Keep only last 6 messages to stay within token limits
    recent = conversation_history[-6:] if len(conversation_history) > 6 else conversation_history
    messages = [{"role": "system", "content": system_with_memory}] + recent
    # Retry up to 2 times on 429 (rate limit) with short delay
    for attempt in range(3):
        try:
            response = groq_client.chat.completions.create(
                model=settings.get('llm_model'), messages=messages, max_tokens=150
            )
            reply = response.choices[0].message.content
            conversation_history.append({"role": "assistant", "content": reply})
            ui.set_api_status("connected")
            return reply
        except Exception as e:
            logger.warning(f"LLM attempt {attempt+1}/3 failed: {e}")
            if attempt < 2:
                time.sleep(2 * (attempt + 1))  # 2s, then 4s
                continue
            logger.error(f"LLM all 3 attempts failed: {e}")
            conversation_history.pop()
            ui.set_api_status("error")
            return "Sorry, I'm having trouble connecting right now."


# ---------- Memory command detection ----------

import re as _re

_STORE_PATTERNS = [
    # Explicit: "remember that X", "don't forget X", etc.
    _re.compile(r"^.*(remember\s+that|please\s+remember|don'?t\s+forget|do\s+not\s+forget|keep\s+in\s+mind)\b(.*)", _re.I),
]
# Auto-detect: personal statements the user probably wants saved (no trigger word needed)
_AUTO_STORE_PATTERNS = [
    # "my favorite/fav X is Y", "X is my favorite/fav"
    _re.compile(r"^.*(my\s+(?:favo(?:u|)rite|fav)\s+\w+\s+(?:is|are)\b.+)", _re.I),
    _re.compile(r"^(.+\s+is\s+my\s+(?:favo(?:u|)rite|fav)\b.*)", _re.I),
    # "my name is X", "I'm called X", "call me X"
    _re.compile(r"^(?:.*(?:my\s+name\s+is|i'm\s+called|i\s+am\s+called|call\s+me)\s+)(.+)", _re.I),
    # "I like/love/prefer/hate X" (but not questions like "do you like...")
    _re.compile(r"^(i\s+(?:like|love|prefer|adore|hate|dislike)\s+(?:that|it|this|when|\.+)\.*)$", _re.I),
    _re.compile(r"^(i\s+(?:like|love|prefer|adore|hate|dislike)\s+\w{2,}.*)$", _re.I),
]
_RECALL_PATTERNS = [
    _re.compile(r"^.*(what\s+do\s+you\s+remember|what\s+do\s+you\s+know|do\s+you\s+remember|show\s+my\s+memories|list\s+my\s+memories|my\s+memories)\b.*", _re.I),
]
_FORGET_PATTERNS = [
    _re.compile(r"^.*(forget\s+that|forget\s+about|delete\s+that\s+memory|remove\s+that\s+memory)\b(.*)", _re.I),
]
_CLEAR_PATTERNS = [
    _re.compile(r"^.*(clear\s+(my\s+)?memories|delete\s+(all\s+)?(my\s+)?memories|forget\s+everything|reset\s+memories)\b.*", _re.I),
]


def _detect_category(content: str):
    """Return (category, importance) based on content keywords."""
    lower = content.lower()
    if any(w in lower for w in ["my name is", "i'm called", "i am called", "call me"]):
        return "name", 10
    if any(w in lower for w in ["i prefer", "i like", "my favorite", "my fav", "i love", "i hate", "don't like", "i dislike", "i adore"]):
        return "preference", 7
    return "fact", 5


# Messages too short or generic to bother saving
_TRIVIAL_MESSAGES = frozenset([
    "hi", "hey", "hello", "yo", "sup", "yeah", "yep", "yup", "no", "nope",
    "ok", "okay", "sure", "hmm", "hm", "uh", "um", "thanks", "thank you",
    "bye", "goodbye", "lol", "lmao", "haha", "nice", "cool", "wow",
    "what", "really", "oh", "ohh", "ohhh", "k", "yah", "nah",
])


def _auto_save_chat(user_text: str):
    """Save user's message to memory so ROSE remembers past conversations."""
    try:
        text = user_text.strip()
        lower = text.lower()
        # Skip trivial one-worders
        if lower in _TRIVIAL_MESSAGES:
            return
        # Skip very short messages (1 word, < 3 chars)
        words = text.split()
        if len(words) < 2 and len(text) < 3:
            return
        # Skip if it was already handled as a memory command (avoid double-save)
        for pat in _STORE_PATTERNS + _AUTO_STORE_PATTERNS:
            if pat.match(text):
                return  # already saved by _handle_memory_command
        # Save as "chat" category with low importance (recency handles retrieval)
        memory.store(text, category="chat", importance=3)
    except Exception:
        pass  # never crash the conversation loop


def _handle_memory_command(user_text: str):
    """
    Check if user_text is a memory command. If so, execute it and return a reply string.
    Returns None if it's NOT a memory command (caller should proceed to LLM).
    """
    text = user_text.strip()

    # 1) Clear all memories
    for pat in _CLEAR_PATTERNS:
        if pat.match(text):
            count = memory.clear()
            if count > 0:
                return f"Done, I've cleared all {count} memories."
            return "There's nothing to clear — my memory is already empty."

    # 2) Forget specific memory
    for pat in _FORGET_PATTERNS:
        m = pat.match(text)
        if m:
            keyword = m.group(2).strip().rstrip(".").strip()
            if keyword:
                count = memory.delete_by_keyword(keyword)
                if count > 0:
                    return f"Okay, I've forgotten {count} memory about '{keyword}'."
                return f"I couldn't find any memory about '{keyword}'."
            return "What exactly should I forget? Tell me the topic."

    # 3) Recall / show memories
    for pat in _RECALL_PATTERNS:
        if pat.match(text):
            mems = memory.get_all()
            if not mems:
                return "I don't have any memories stored yet. Tell me something to remember!"
            items = [f"{m['content']}" for m in mems[:5]]
            return "Here's what I remember: " + "; ".join(items) + "."

    # 4) Store a new memory (explicit trigger word)
    for pat in _STORE_PATTERNS:
        m = pat.match(text)
        if m:
            # Extract the part after the trigger phrase
            content = m.group(2).strip().rstrip(".").strip()
            if not content:
                return "Sure, what should I remember?"
            # Detect category
            category, importance = _detect_category(content)
            if memory.store(content, category=category, importance=importance):
                return f"Got it, I'll remember that {content}."
            return "Sorry, I couldn't save that right now — memory might be unavailable."

    # 5) Auto-store: detect personal statements without explicit trigger words
    for pat in _AUTO_STORE_PATTERNS:
        m = pat.match(text)
        if m:
            content = m.group(1).strip().rstrip(".").strip()
            if content:
                category, importance = _detect_category(content)
                if memory.store(content, category=category, importance=importance):
                    return f"Got it, I'll remember that {content}."
                return "Sorry, I couldn't save that right now."

    return None  # not a memory command


def speak(text):
    syn_config = get_mood_config(text)
    audio_chunks = []
    for chunk in piper_voice.synthesize(text, syn_config=syn_config):
        audio_chunks.append(chunk.audio_int16_array)
    audio_array = np.concatenate(audio_chunks)
    sd.play(audio_array, samplerate=piper_voice.config.sample_rate)
    sd.wait()


# UI updates are now handled by the UIBridge (ui_bridge.py) — no more raw JS injection.


# ---------- Whistle tone detection helper ----------

def is_whistle_tone(chunk, sample_rate):
    samples = chunk.flatten().astype(np.float64)
    if len(samples) < 32:
        return False

    fft_result = np.fft.rfft(samples)
    magnitudes = np.abs(fft_result)
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate)

    total_energy = np.sum(magnitudes)
    if total_energy < 1e-6:
        return False

    peak_index = np.argmax(magnitudes)
    peak_freq = freqs[peak_index]

    if not (settings.get('whistle_freq_min') <= peak_freq <= settings.get('whistle_freq_max')):
        return False

    band = (freqs > peak_freq - 150) & (freqs < peak_freq + 150)
    band_energy = np.sum(magnitudes[band])
    purity = band_energy / total_energy

    return purity > settings.get('wake_sensitivity')


# ---------- Dormant state: lightweight whistle-listening loop ----------

def wait_for_wake(ui):
    ui.set_state("dormant")

    time.sleep(2.0)
    baseline_audio = get_recent_audio(2.0)
    baseline = np.max(np.abs(baseline_audio)) if len(baseline_audio) > 0 else 50
    volume_threshold = max(baseline * 3, 200)

    ui.set_mood("not yet detected", 0)

    above_since = None

    while True:
        time.sleep(0.05)
        recent = get_recent_audio(0.1)
        if len(recent) == 0:
            continue

        peak = np.max(np.abs(recent))
        now = time.time()

        if peak > volume_threshold and is_whistle_tone(recent, settings.get('sample_rate')):
            if above_since is None:
                above_since = now
        else:
            above_since = None

        if above_since is not None and now - above_since >= settings.get('whistle_hold_time'):
            return  # woke up!


# ---------- Active state: full conversation loop ----------

def active_session(ui):
    ui.set_state("listening")
    task_count = 0
    last_activity = time.time()

    while True:
        if time.time() - last_activity > settings.get('inactivity_timeout'):
            return  # go back to dormant

        avg_volume = record_audio()
        ui.set_state("thinking")

        if avg_volume < settings.get('min_volume'):
            ui.set_state("listening")
            continue

        user_text = transcribe()
        if user_text == "":
            ui.set_state("listening")
            continue

        if settings.get('name_gating') and not any(alias in user_text.lower() for alias in settings.get('rose_aliases')):
            ui.set_state("listening")
            continue

        last_activity = time.time()
        ui.emit_message("You", user_text)

        reply = ask_llm(user_text)
        ui.emit_message("ROSE", reply)

        # Auto-save user's message to persistent memory (like a chatbot)
        _auto_save_chat(user_text)

        task_count += 1
        ui.set_task_count(task_count)

        # ---- Better mood detection: analyses user text + reply ----
        mood_label, mood_confidence = detect_user_mood(user_text, reply)
        ui.set_mood(mood_label, mood_confidence)
        ui.set_state("speaking")
        speak(reply)

        ui.set_state("listening")


# ---------- Protected runner (catches exceptions in daemon thread) ----------

def _protected(fn, name):
    """Run fn(ui), log + re-raise any exception so the daemon thread doesn't die silently."""
    try:
        logger.debug(f"[{name}] entering")
        fn(ui)
    except Exception as e:
        logger.error(f"[{name}] CRASH: {e}")
        logger.error(traceback.format_exc())
        raise


# ---------- Heartbeat monitor ----------

def _heartbeat():
    """Logs state + memory every 15s so we can see the last thing before a crash."""
    while True:
        try:
            time.sleep(15)
            proc = psutil.Process()
            mem_mb = proc.memory_info().rss / 1024 / 1024
            with buffer_lock:
                buf_len = len(audio_buffer)
            logger.info(
                f"HEARTBEAT | state={ui._state} | mem={mem_mb:.1f}MB"
                f" | buf={buf_len}/{settings.get('sample_rate') * settings.get('buffer_seconds')}"
                f" | conv={len(conversation_history)} | alive=True"
            )
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")


# ---------- Main loop: alternates between dormant and active ----------

def rose_loop(ui):
    logger.info("rose_loop started")
    while True:
        _protected(wait_for_wake, "wait_for_wake")

        greeting = "Hello sir! I've been activated."
        ui.emit_message("ROSE", greeting)
        ui.set_state("speaking")
        speak(greeting)

        _protected(active_session, "active_session")


def start_backend(window):
    logger.info("start_backend called by pywebview")
    sr = settings.get('sample_rate')

    global mic_stream
    try:
        mic_stream = sd.InputStream(
            samplerate=sr, channels=1, dtype='int16',
            device=settings.get('device_index'), blocksize=int(sr * 0.1),
            callback=audio_callback
        )
        mic_stream.start()
        logger.info(f"Audio stream started (device={settings.get('device_index')}, sr={sr})")
    except Exception as e:
        logger.error(f"Failed to start audio stream: {e}\n{traceback.format_exc()}")
        raise

    # heartbeat monitor
    hb = threading.Thread(target=_heartbeat, daemon=True, name="heartbeat")
    hb.start()
    logger.info("Heartbeat thread started")

    # backend loop
    thread = threading.Thread(target=rose_loop, args=(ui,), daemon=True, name="rose_loop")
    thread.start()
    logger.info("Backend daemon thread started")

    # log when window is closed
    def on_closed():
        logger.warning("WINDOW CLOSED by user or system")
    window.events.closed += on_closed


logger.info("Creating pywebview window")
window = webview.create_window("ROSE", "rose_interface.html", width=1100, height=650, js_api=ui)
logger.info("Starting pywebview with debug=True")
webview.start(start_backend, window, debug=True)
logger.warning("webview.start() returned — application shutting down")