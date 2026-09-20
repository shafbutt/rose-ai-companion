import os
os.environ["OMP_NUM_THREADS"] = "4"
os.environ["OPENBLAS_NUM_THREADS"] = "4"

import time
import threading
import numpy as np
import sounddevice as sd
import wave
import whisper
import webview

from groq import Groq
from dotenv import load_dotenv
from piper import PiperVoice
from piper.config import SynthesisConfig
from config import settings, INITIAL_PROMPT, SYSTEM_PROMPT
from ui_bridge import UIBridge

load_dotenv()
try:
    groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
except Exception as e:
    print(f"[ROSE] Warning: Groq client init failed: {e}")
    groq_client = None

# ---- shared audio buffer: ONE mic stream feeds everything ----
buffer_lock = threading.Lock()
audio_buffer = []   # plain list of int16 samples
ui = UIBridge()     # communication bridge to the frontend


def audio_callback(indata, frames, time_info, status):
    with buffer_lock:
        audio_buffer.extend(indata[:, 0].tolist())
        max_len = settings.get('sample_rate') * settings.get('buffer_seconds')
        if len(audio_buffer) > max_len:
            del audio_buffer[:len(audio_buffer) - max_len]
    # track live audio level for the UI meter
    ui.set_audio_level(float(np.max(np.abs(indata))) / 32768.0)


def get_recent_audio(seconds):
    with buffer_lock:
        n = int(seconds * settings.get('sample_rate'))
        data = audio_buffer[-n:] if len(audio_buffer) >= n else audio_buffer[:]
    return np.array(data, dtype=np.int16)


STEP = 0.2   # how often we check the buffer, in seconds

conversation_history = []
whisper_model = whisper.load_model("small")
piper_voice = PiperVoice.load("voice/en_US-amy-medium.onnx")

EXCITED_WORDS = ["hey!", "yay", "wow", "awesome", "great", "haha", "exciting", "!"]
TIRED_WORDS = ["tired", "sorry", "sad", "rough", "sigh", "hmm", "long day"]


def get_mood_config(text):
    lower = text.lower()
    if any(word in lower for word in EXCITED_WORDS):
        return SynthesisConfig(length_scale=0.9, noise_scale=0.8)
    elif any(word in lower for word in TIRED_WORDS):
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
    try:
        result = whisper_model.transcribe("input.wav", language="en")
        return result["text"].strip()
    except Exception as e:
        print(f"[ROSE] Transcription error: {e}")
        return ""


def ask_llm(user_text):
    conversation_history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation_history
    try:
        response = groq_client.chat.completions.create(
            model=settings.get('llm_model'), messages=messages
        )
        reply = response.choices[0].message.content
        conversation_history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        print(f"[ROSE] LLM error: {e}")
        conversation_history.pop()  # remove user msg to keep history consistent
        ui.set_api_status("error")
        return "Sorry, I'm having trouble connecting right now."


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

        task_count += 1
        ui.set_task_count(task_count)

        matched_excited = sum(1 for w in EXCITED_WORDS if w in reply.lower())
        matched_tired = sum(1 for w in TIRED_WORDS if w in reply.lower())

        if matched_excited > 0:
            mood_label = "excited"
            confidence = min(60 + matched_excited * 15, 95)
            face_state = "excited"
        elif matched_tired > 0:
            mood_label = "a bit tired"
            confidence = min(60 + matched_tired * 15, 95)
            face_state = "tired"
        else:
            mood_label = "doing okay"
            confidence = 65
            face_state = "calm"

        ui.set_mood(mood_label, confidence)
        ui.set_state("speaking")
        speak(reply)

        ui.set_state("listening")


# ---------- Main loop: alternates between dormant and active ----------

def rose_loop(ui):
    while True:
        wait_for_wake(ui)

        greeting = "Hello sir! I've been activated."
        ui.emit_message("ROSE", greeting)
        ui.set_state("speaking")
        speak(greeting)

        active_session(ui)


def start_backend(window):
    sr = settings.get('sample_rate')
    stream = sd.InputStream(
        samplerate=sr, channels=1, dtype='int16',
        device=settings.get('device_index'), blocksize=int(sr * 0.1),
        callback=audio_callback
    )
    stream.start()

    thread = threading.Thread(target=rose_loop, args=(ui,), daemon=True)
    thread.start()


window = webview.create_window("ROSE", "rose_interface.html", width=1100, height=650, js_api=ui)
webview.start(start_backend, window)