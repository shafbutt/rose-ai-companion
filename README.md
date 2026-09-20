# ROSE — AI Desktop Agent

**A secure, intelligent, persistent, voice-controlled desktop AI agent for Windows.**

ROSE is a local AI voice assistant that understands natural language, remembers information across sessions, performs system actions, searches the web, checks weather, and communicates through a polished HUD interface. Everything runs locally with privacy-first design.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4?logo=windows)

**Creator:** Shaf RIZWAN (Pakistan)

---

## Features

### Core
- **Whistle Wake-Word** — FFT-based tone detection (1–4 kHz), no internet needed
- **Voice Activity Recording** — amplitude-based silence detection, hands-free
- **Name-Gating** — responds only to "Rose" (with fuzzy aliases)
- **Auto-Sleep** — dormant mode after inactivity

### Intelligence
- **Persistent Memory** — SQLite-backed, auto-saves conversations, remembers preferences
- **ROSE Identity** — knows she's an AI assistant, knows her creator (protected)
- **Anti-Hallucination** — honesty rules, never fabricates information
- **Mood Detection** — 8 categories (happy, sad, stressed, angry, excited, tired, flirty, neutral)

### System
- **System Information** — CPU, RAM, disk, battery, network, uptime
- **Time/Date Awareness** — answers "what time is it", "what's today's date"
- **System Actions** — lock, shutdown, restart, sleep, open apps (whitelist-only, confirmation for dangerous actions)
- **Weather** — current conditions + 3-day forecast (Open-Meteo, free)
- **Location** — IP-based geolocation (ip-api.com, free)

### Search & Research
- **Web Search** — DuckDuckGo Instant Answer API (free, no key)
- **YouTube Search** — opens results in browser
- **Research Mode** — multi-source search with summarization

### Agent Tools
- **Calculations** — safe math evaluation
- **Unit Conversions** — km/mi, m/ft, cm/in, kg/lb, °C/°F, l/gal
- **Notes** — store quick notes in memory

### Security
- **Permission Center** — 11 permissions (microphone, location, internet, web search, browser, file read/write, app launch, system actions, clipboard, notifications)
- **Fail-Closed** — unknown permissions default to DENY
- **No Arbitrary Commands** — whitelist-only actions, no shell injection
- **Identity Protection** — creator identity cannot be overwritten via conversation

### Interface
- **HUD Interface** — JARVIS-inspired with audio-reactive orb
- **Mute Control** — toggle mic with visual indicator
- **Full-Screen Mode** — immersive experience
- **Settings Panel** — all thresholds adjustable from UI
- **Error Resilience** — contextual fallbacks, API recovery, TTS/STT error handling

---

## Tech Stack

| Layer | Technology | Details |
|-------|-----------|---------|
| **STT** | faster-whisper | `base` model, CTranslate2 int8, ~2s for 10s audio |
| **LLM** | Groq API | `groq/compound-mini` — free tier, ~1s latency |
| **TTS** | Piper TTS | `en_US-amy-medium` voice, ONNX runtime, local |
| **UI** | pywebview | Desktop window rendering HTML/CSS/JS |
| **Audio** | sounddevice | Persistent `InputStream`, shared ring buffer |
| **Memory** | SQLite | Local persistent storage via `memory.py` |
| **Permissions** | Custom | JSON-backed permission manager |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  pywebview window (main thread)                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  rose_interface.html                                   │  │
│  │  polls api.poll() every 250ms for state + events       │  │
│  └────────────────────────┬───────────────────────────────┘  │
│                           │ pywebview JS API                 │
│  ┌────────────────────────┴───────────────────────────────┐  │
│  │  UIBridge (ui_bridge.py)                               │  │
│  │  thread-safe event queue + state snapshot               │  │
│  │  Injected: memory, groq, actions, error_state, perms   │  │
│  └────────────────────────┬───────────────────────────────┘  │
│                           │                                  │
│  ┌────────────────────────┴───────────────────────────────┐  │
│  │  Backend daemon thread (rose_app.py)                   │  │
│  │                                                        │  │
│  │  Priority chain in ask_llm():                          │  │
│  │  1. System actions (lock, shutdown, open apps)         │  │
│  │  2. Memory commands (remember, forget, recall)         │  │
│  │  3. System info (time, CPU, RAM)                       │  │
│  │  4. Weather / Location                                 │  │
│  │  5. Web search / YouTube                               │  │
│  │  6. Research mode                                      │  │
│  │  7. Agent tools (calc, convert, notes)                 │  │
│  │  8. LLM call (Groq API)                                │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  Modules: memory.py | system_actions.py | system_info.py     │
│           permissions.py | location_weather.py | web_search.py│
│           agent_tools.py                                     │
└──────────────────────────────────────────────────────────────┘
```

---

## Setup

### Prerequisites
- **Windows 10/11**
- **Python 3.10+**
- A free [Groq API key](https://console.groq.com)
- A working microphone

### Installation

```bash
# 1. Clone
git clone https://github.com/shafbutt/rose-ai-companion.git
cd rose-ai-companion

# 2. Virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Dependencies
pip install -r requirements.txt

# 4. API key
cp .env.example .env
# Edit .env and paste your Groq API key

# 5. Voice model (if not included)
# Place en_US-amy-medium.onnx + .json in voice/
# Download: https://huggingface.co/rhasspy/piper-voices
```

### Running

```bash
start_rose.bat
# or
python rose_app.py
```

### Building Desktop Package

```bash
build.bat
# Creates dist/ROSE/ROSE.exe
```

---

## Project Structure

```
D:\ROSE\
├── rose_app.py              # Main backend — state machine, audio, priority chain
├── config.py                # Settings, prompts, ROSE identity
├── ui_bridge.py             # Thread-safe Python ↔ JS communication
├── rose_interface.html      # HUD interface
├── memory.py                # SQLite persistent memory
├── system_actions.py        # Whitelist-only system actions
├── system_info.py           # System info + time/date
├── permissions.py           # Centralized permission manager
├── location_weather.py      # Location + weather (free APIs)
├── web_search.py            # Web search + YouTube
├── agent_tools.py           # Research, calculations, conversions, notes
├── start_rose.bat           # Windows launcher
├── build.bat                # PyInstaller build script
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
├── .gitignore
├── LICENSE
├── voice/                   # Piper TTS model (not in git)
└── venv/                    # Virtual environment (not in git)
```

---

## Permissions

| Permission | Default | Purpose |
|-----------|---------|---------|
| microphone | ALLOW | Voice input |
| internet | ALLOW | API calls |
| app_launch | ALLOW | Open whitelisted apps |
| system_actions | ALLOW | Lock, shutdown, etc. |
| notifications | ALLOW | Desktop notifications |
| location | DENY | Weather, local info |
| web_search | DENY | Web search |
| browser | DENY | YouTube, web browsing |
| file_read | DENY | Document reading |
| file_write | DENY | File creation |
| clipboard | DENY | Clipboard access |

---

## Voice Commands

### System Actions
- "Lock the computer" / "Lock my PC"
- "Shut down" / "Restart" (requires confirmation)
- "Open Chrome" / "Open Notepad" / "Open Spotify"
- "Open my documents" / "Open downloads folder"

### Memory
- "Remember that my favorite color is blue"
- "What do you remember about me?"
- "Forget about my favorite color"
- "Clear my memories"

### System Info
- "What time is it?"
- "What's today's date?"
- "How much RAM am I using?"
- "What's my CPU usage?"
- "Is my laptop charging?"

### Weather
- "What's the weather?"
- "Will it rain today?"
- "Show me the forecast"

### Search
- "Search the web for Python tutorials"
- "Search YouTube for music"
- "Research quantum computing"

### Agent Tools
- "What is 145 * 23?"
- "Convert 100 km to miles"
- "Take a note: buy groceries"

---

## License

MIT — see [LICENSE](LICENSE).

---

## Creator

**Shaf RIZWAN** (Pakistan)

ROSE is being built as a serious personal AI desktop agent — intelligent, secure, persistent, and genuinely useful.
