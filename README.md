# ROSE — AI Voice Companion

**A voice-activated AI companion for Windows with a live HUD interface — whistle to wake, talk, and she responds.**

ROSE is not a task assistant. She's a conversational companion — someone to keep you company during long solo coding sessions. She wakes up when you whistle, listens for her name, and replies with a natural voice. Everything runs in a cyberpunk-inspired desktop HUD with audio-reactive visuals.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows)

---

## Features

- **Whistle Wake-Word Detection** — FFT-based tone analysis detects a human whistle (1–4 kHz frequency range, spectral purity check, sustained for 350ms) without any keyword model or internet connection
- **Voice Activity Recording** — amplitude-based silence detection starts recording when you speak and stops after 3s of silence (20s max), no push-to-talk button needed
- **Name-Gating** — ROSE only responds when she hears her name in the transcript (with fuzzy aliases: rose / rows / close / rosa to handle Whisper mishearings)
- **Auto-Sleep** — returns to dormant whistle-listening mode after 30 minutes of no valid interaction (configurable)
- **Live HUD Interface** — pywebview desktop window with a JARVIS-inspired dashboard: audio-reactive orb, real-time mic level meter, conversation log with search, system health panel, and a settings drawer
- **Thread-Safe Architecture** — single persistent microphone stream feeds a shared ring buffer that both the whistle detector and speech recorder read from, avoiding device contention
- **Real-Time Communication Layer** — `UIBridge` class replaces fragile JavaScript injection with a clean poll-based event system between the Python backend and the HTML frontend
- **Persistent Settings** — all thresholds and model choices are stored in `settings.json` and adjustable from the UI without touching code

---

## Tech Stack

| Layer | Technology | Details |
|-------|-----------|---------|
| **STT** | OpenAI Whisper | `small` model, local CPU inference |
| **LLM** | Groq API | `llama-3.1-8b-instant` — free tier, ~200ms latency |
| **TTS** | Piper TTS | `en_US-amy-medium` voice, ONNX runtime, local |
| **UI** | pywebview | Desktop window rendering HTML/CSS/JS |
| **Audio** | sounddevice | Persistent `InputStream`, shared ring buffer |
| **Threading** | stdlib `threading` | Backend in daemon thread, pywebview owns main thread |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  pywebview window (main thread)                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │  rose_interface.html                              │  │
│  │  polls api.poll() every 250ms for state + events  │  │
│  └───────────────────────┬───────────────────────────┘  │
│                          │ pywebview JS API              │
│  ┌───────────────────────┴───────────────────────────┐  │
│  │  UIBridge (ui_bridge.py)                          │  │
│  │  thread-safe event queue + state snapshot          │  │
│  └───────────────────────┬───────────────────────────┘  │
│                          │                               │
│  ┌───────────────────────┴───────────────────────────┐  │
│  │  Backend daemon thread (rose_app.py)              │  │
│  │                                                   │  │
│  │  ┌─────────┐    ┌──────────┐    ┌─────────────┐  │  │
│  │  │ DORMANT │───>│ ACTIVE   │───>│ DORMANT     │  │  │
│  │  │ whistle │    │ record → │    │ (after 30m  │  │  │
│  │  │ listen  │    │ STT →    │    │ inactivity) │  │  │
│  │  │         │    │ LLM →    │    │             │  │  │
│  │  │         │    │ TTS      │    │             │  │  │
│  │  └─────────┘    └──────────┘    └─────────────┘  │  │
│  └───────────────────────────────────────────────────┘  │
│                          ▲                               │
│  ┌───────────────────────┴───────────────────────────┐  │
│  │  Shared audio ring buffer (12s, numpy)            │  │
│  │  fed by ONE persistent sd.InputStream             │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Key Design Decisions

**Single persistent audio stream.** Instead of opening and closing the microphone for each operation (whistle detection, speech recording), ROSE runs one `sd.InputStream` for the app's entire lifetime. All audio flows into a shared ring buffer that both the whistle detector and the speech recorder read from. This avoids:
- Device contention (two streams competing for the same mic)
- Audio driver glitches from repeated open/close cycles
- Timing gaps between operations

**Thread model.** pywebview requires the main thread for its event loop. The entire backend (state machine, STT, LLM, TTS) runs in a single daemon thread. Communication with the UI happens through the `UIBridge` — a thread-safe event queue that the frontend polls every 250ms. This avoids cross-thread `evaluate_js()` calls (which worked but were fragile and undocumented).

**Settings as a singleton.** The `Settings` class in `config.py` is a thread-safe dictionary that persists to `settings.json`. Every module reads from it via `settings.get('key')`, so changes from the UI settings panel take effect on the next loop iteration without restarting the app.

---

## Setup

### Prerequisites

- **Windows 10/11** (pywebview and sounddevice are Windows-targeted)
- **Python 3.10+**
- A free [Groq API key](https://console.groq.com) (for the LLM)
- A working microphone

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/shafbutt/rose-ai-companion.git
cd rose-ai-companion

# 2. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure your API key
cp .env.example .env
# Edit .env and paste your Groq API key

# 5. Download the Piper voice model
#    Place en_US-amy-medium.onnx and en_US-amy-medium.onnx.json
#    in the voice/ directory
#    Download from: https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/amy/medium
```

### Running

```bash
# Option A: use the batch launcher
start_rose.bat

# Option B: run directly
python rose_app.py
```

The ROSE window will appear. Wait for the models to load (~10–20s for Whisper), then whistle to wake her up.

---

## Project Structure

```
D:\ROSE\
├── rose_app.py              # Main backend — state machine, audio, STT/LLM/TTS
├── config.py                # All settings, thresholds, and prompts
├── ui_bridge.py             # Thread-safe Python ↔ JS communication layer
├── rose_interface.html      # JARVIS-inspired HUD interface
├── start_rose.bat           # Windows launcher
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
├── .gitignore
├── LICENSE
├── voice/                   # Piper TTS model files (not in git)
│   ├── en_US-amy-medium.onnx
│   └── en_US-amy-medium.onnx.json
└── venv/                    # Virtual environment (not in git)
```

---

## Configuration

All settings are adjustable from the in-app settings panel (gear icon) or by editing `settings.json`:

| Setting | Default | Description |
|---------|---------|-------------|
| `wake_sensitivity` | 0.35 | FFT tone purity threshold for whistle detection (lower = more sensitive) |
| `silence_threshold` | 150 | Amplitude below this is treated as silence |
| `silence_limit` | 3.0s | Seconds of silence before recording stops |
| `inactivity_timeout` | 30 min | Time before returning to dormant mode |
| `llm_model` | llama-3.1-8b-instant | Groq model to use |
| `name_gating` | true | Require "Rose" in transcript before responding |

---

## Status

**Active development.** Current phase: interface rebuild and architecture cleanup.

### Completed
- Core state machine (dormant ↔ active)
- Whistle wake-word detection via FFT
- Whisper STT + Groq LLM + Piper TTS pipeline
- JARVIS-inspired HUD with audio-reactive visuals
- Thread-safe UI bridge (replacing raw JS injection)
- Centralized settings with persistence
- Error handling for API and transcription failures

### Planned
- Switch to `faster-whisper` (already installed) for 3–4x speedup
- Conversation history trimming / summarization
- Better TTS engine (Coqui XTTS-v2 for expressive voice)
- Persistent conversation memory across sessions

---

## License

MIT — see [LICENSE](LICENSE).
