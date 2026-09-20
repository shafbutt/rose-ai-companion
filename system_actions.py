"""
ROSE System Actions Module
Handles voice-controlled system operations with safety guardrails.

Design:
  - ActionManager is fully self-contained — no imports from rose_app.
  - All public methods catch exceptions and never crash the caller.
  - WHITELIST-ONLY: only pre-approved apps/folders can be opened.
  - DANGEROUS actions (shutdown, restart) require explicit user confirmation.
  - SAFE actions (lock, sleep) execute immediately.
  - Pending confirmations expire after 30 seconds.

Security:
  - No arbitrary command execution — every action maps to a fixed command.
  - App/folder names are resolved from a whitelist, never from raw user input.
  - subprocess is called only with known-safe argument lists.
"""

import os
import re
import subprocess
import threading
import time
import logging

logger = logging.getLogger("rose.actions")

# ================================================================== #
#  WHITELISTS — only these apps/folders can be opened by voice
# ================================================================== #

# Common apps → their executable or start command
ALLOWED_APPS = {
    # Browsers
    "chrome":       "chrome.exe",
    "google":       "chrome.exe",
    "firefox":      "firefox.exe",
    "edge":         "msedge.exe",
    "brave":        "brave.exe",
    # Productivity
    "notepad":      "notepad.exe",
    "calculator":   "calc.exe",
    "calc":         "calc.exe",
    "paint":        "mspaint.exe",
    "snipping":     "snippingtool.exe",
    "terminal":     "wt.exe",
    "cmd":          "cmd.exe",
    "powershell":   "powershell.exe",
    "task manager": "taskmgr.exe",
    # Media
    "spotify":      "spotify.exe",
    "vlc":          "vlc.exe",
    # Communication
    "discord":      "discord.exe",
    "whatsapp":     "whatsapp.exe",
    "telegram":     "telegram.exe",
    "zoom":         "zoom.exe",
    # Microsoft Office
    "word":         "winword.exe",
    "excel":        "excel.exe",
    "powerpoint":   "powerpnt.exe",
    "outlook":      "outlook.exe",
}

# Named folders → actual paths (resolved at runtime)
def _get_known_folders():
    """Build whitelist of openable folders from environment variables."""
    user = os.path.expanduser("~")
    return {
        "documents":    os.path.join(user, "Documents"),
        "downloads":    os.path.join(user, "Downloads"),
        "desktop":      os.path.join(user, "Desktop"),
        "pictures":     os.path.join(user, "Pictures"),
        "music":        os.path.join(user, "Music"),
        "videos":       os.path.join(user, "Videos"),
    }

# ================================================================== #
#  ACTION DEFINITIONS
# ================================================================== #

# Each action: (command_args, risk_level, description)
# risk_level: "safe" = immediate, "dangerous" = needs confirmation
SYSTEM_COMMANDS = {
    "shutdown": {
        "command":  ["shutdown", "/s", "/t", "10"],
        "risk":     "dangerous",
        "label":    "shut down the PC",
    },
    "restart": {
        "command":  ["shutdown", "/r", "/t", "10"],
        "risk":     "dangerous",
        "label":    "restart the PC",
    },
    "lock": {
        "command":  ["rundll32.exe", "user32.dll,LockWorkStation"],
        "risk":     "safe",
        "label":    "lock the screen",
    },
    "sleep": {
        "command":  ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "risk":     "safe",
        "label":    "put the PC to sleep",
    },
}

# ================================================================== #
#  VOICE PATTERNS
# ================================================================== #

_PATTERNS = {
    "shutdown": [
        re.compile(r".*\b(shut\s*down|power\s*off|turn\s*off)\s*(the\s+)?(pc|computer|laptop|system)?\b.*", re.I),
    ],
    "restart": [
        re.compile(r".*\b(restart|reboot)\s*(the\s+)?(pc|computer|laptop|system)?\b.*", re.I),
    ],
    "lock": [
        re.compile(r".*\b(lock\s*(the\s+)?(screen|pc|computer|laptop)|lock\s*up)\b.*", re.I),
    ],
    "sleep": [
        re.compile(r".*\b(go\s+to\s+sleep|sleep|hibernate)\b.*", re.I),
    ],
}

_OPEN_APP_PATTERN = re.compile(
    r"\b(?:open|launch|start|run)\s+(.+?)(?:\s+(?:for\s+me|please|pls|now))?\s*$", re.I
)

_OPEN_FOLDER_PATTERN = re.compile(
    r"\b(?:open|show|go\s+to)\s+(?:my\s+)?(documents|downloads|desktop|pictures|music|videos)\s*(?:folder)?\s*$", re.I
)

# Confirmation / cancellation phrases
_CONFIRM_PATTERNS = [
    re.compile(r"^\s*(yes|yeah|yep|yup|sure|go\s+ahead|do\s+it|confirm|proceed|ok|okay)\s*[.!]?\s*$", re.I),
]
_CANCEL_PATTERNS = [
    re.compile(r"^\s*(no|nope|nah|cancel|stop|never\s*mind|don'?t|abort)\s*[.!]?\s*$", re.I),
]


# ================================================================== #
#  ActionManager
# ================================================================== #

class ActionManager:
    """Manages system actions with whitelist + confirmation flow."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending = None       # {"action": str, "args": dict, "expires": float} or None
        self._last_result = None   # {"ok": bool, "message": str}
        self._folders = _get_known_folders()
        self._log = []             # last 10 actions for UI

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """Always True on Windows."""
        return os.name == "nt"

    def get_allowed_apps(self) -> list[str]:
        """Return sorted list of whitelisted app names."""
        return sorted(ALLOWED_APPS.keys())

    def get_allowed_folders(self) -> list[str]:
        """Return sorted list of whitelisted folder names."""
        return sorted(self._folders.keys())

    def get_pending(self) -> dict | None:
        """Return pending confirmation info, or None."""
        with self._lock:
            if self._pending and time.time() > self._pending["expires"]:
                self._pending = None
            if self._pending:
                return {
                    "action": self._pending["action"],
                    "label": self._pending["label"],
                    "seconds_left": int(self._pending["expires"] - time.time()),
                }
            return None

    def get_log(self) -> list[dict]:
        """Return last 10 action results for UI display."""
        with self._lock:
            return list(self._log[-10:])

    def handle_command(self, user_text: str):
        """
        Check if user_text is a system action command.

        Returns:
            str  — a reply to speak to the user (for confirmation prompts or results)
            None — not a system command, caller should proceed to next handler
        """
        text = user_text.strip()
        if not text:
            return None

        # 1) Check if this is a confirmation/cancellation of a pending action
        pending_reply = self._check_confirmation(text)
        if pending_reply is not None:
            return pending_reply

        # 2) Check system commands (shutdown, restart, lock, sleep)
        for action_name, patterns in _PATTERNS.items():
            for pat in patterns:
                if pat.match(text):
                    return self._initiate_action(action_name)

        # 3) Check "open app" commands
        m = _OPEN_APP_PATTERN.match(text)
        if m:
            app_name = m.group(1).strip().lower().rstrip(".")
            return self._open_app(app_name)

        # 4) Check "open folder" commands
        m = _OPEN_FOLDER_PATTERN.match(text)
        if m:
            folder_name = m.group(1).strip().lower()
            return self._open_folder(folder_name)

        return None  # not a system command

    def cancel_pending(self):
        """Cancel any pending action."""
        with self._lock:
            self._pending = None

    # ------------------------------------------------------------------ #
    #  Internal: confirmation flow
    # ------------------------------------------------------------------ #

    def _check_confirmation(self, text: str):
        """Handle yes/no for pending dangerous actions. Returns reply or None."""
        with self._lock:
            if self._pending and time.time() > self._pending["expires"]:
                self._pending = None

            if not self._pending:
                return None  # no pending action, don't intercept

        # Is this a confirmation?
        for pat in _CONFIRM_PATTERNS:
            if pat.match(text):
                return self._execute_pending()

        # Is this a cancellation?
        for pat in _CANCEL_PATTERNS:
            if pat.match(text):
                with self._lock:
                    action_label = self._pending["label"]
                    self._pending = None
                return f"Cancelled. I won't {action_label}."

        return None  # pending exists but user said something else — let it pass to LLM

    def _initiate_action(self, action_name: str) -> str:
        """Start an action — immediate for safe, confirmation prompt for dangerous."""
        info = SYSTEM_COMMANDS.get(action_name)
        if not info:
            return None

        if info["risk"] == "safe":
            # Execute immediately
            return self._execute_command(action_name, info)
        else:
            # Dangerous — require confirmation
            with self._lock:
                self._pending = {
                    "action": action_name,
                    "label": info["label"],
                    "command": info["command"],
                    "expires": time.time() + 30,  # 30 second window
                }
            return f"Are you sure you want to {info['label']}? Say yes to confirm."

    def _execute_pending(self) -> str:
        """Execute the pending dangerous action."""
        with self._lock:
            if not self._pending:
                return "There's nothing pending."
            action_name = self._pending["action"]
            command = self._pending["command"]
            label = self._pending["label"]
            self._pending = None

        return self._run_command(action_name, command, label)

    def _execute_command(self, action_name: str, info: dict) -> str:
        """Execute a safe system command."""
        return self._run_command(action_name, info["command"], info["label"])

    def _run_command(self, action_name: str, command: list, label: str) -> str:
        """Actually run the subprocess command. Returns user-facing reply."""
        try:
            logger.info(f"Executing system action: {action_name} → {command}")
            result = subprocess.run(command, capture_output=True, timeout=10)
            ok = result.returncode == 0
            if ok:
                reply = f"Done — {label}."
            else:
                stderr = result.stderr.decode(errors="replace")[:100]
                logger.warning(f"Action {action_name} returned code {result.returncode}: {stderr}")
                reply = f"I tried to {label} but got an error."
            self._record_result(action_name, ok, reply)
            return reply
        except subprocess.TimeoutExpired:
            logger.error(f"Action {action_name} timed out")
            self._record_result(action_name, False, "Timed out")
            return f"I tried to {label} but it timed out."
        except Exception as e:
            logger.error(f"Action {action_name} failed: {e}")
            self._record_result(action_name, False, str(e))
            return f"I couldn't {label} — something went wrong."

    # ------------------------------------------------------------------ #
    #  Internal: open app / folder
    # ------------------------------------------------------------------ #

    def _open_app(self, app_name: str) -> str:
        """Open a whitelisted application."""
        # SECURITY: only use the first word for matching — prevent argument injection
        # e.g. "cmd.exe /c del c:" → only match against "cmd.exe"
        first_word = app_name.split()[0] if app_name.split() else app_name
        # Strip common suffixes like .exe
        clean_name = first_word.lower().rstrip(".exe").rstrip(".")

        # Direct match
        exe = ALLOWED_APPS.get(clean_name)
        if exe:
            return self._launch_app(clean_name, exe)

        # Also try the full first_word (e.g. "chrome" from "chrome.exe")
        exe = ALLOWED_APPS.get(first_word.lower())
        if exe:
            return self._launch_app(first_word.lower(), exe)

        # Fuzzy match: only if the clean name is very close to a whitelist key
        for key, val in ALLOWED_APPS.items():
            if clean_name == key or (len(clean_name) >= 3 and clean_name in key and len(clean_name) >= len(key) * 0.7):
                return self._launch_app(key, val)

        return f"Sorry, I can't open '{app_name}'. It's not in my allowed apps list."

    def _launch_app(self, name: str, exe: str) -> str:
        """Launch an application by executable name."""
        try:
            logger.info(f"Opening app: {name} → {exe}")
            subprocess.Popen([exe], shell=True)
            self._record_result(f"open_app:{name}", True, f"Opened {name}")
            return f"Opening {name}."
        except Exception as e:
            logger.error(f"Failed to open {name}: {e}")
            self._record_result(f"open_app:{name}", False, str(e))
            return f"I couldn't open {name} — something went wrong."

    def _open_folder(self, folder_name: str) -> str:
        """Open a whitelisted folder in Explorer."""
        path = self._folders.get(folder_name)
        if not path:
            return f"Sorry, I don't know where your {folder_name} folder is."
        if not os.path.isdir(path):
            return f"The {folder_name} folder doesn't exist at {path}."
        try:
            logger.info(f"Opening folder: {folder_name} → {path}")
            subprocess.Popen(["explorer", path])
            self._record_result(f"open_folder:{folder_name}", True, f"Opened {folder_name}")
            return f"Opening your {folder_name} folder."
        except Exception as e:
            logger.error(f"Failed to open folder {folder_name}: {e}")
            self._record_result(f"open_folder:{folder_name}", False, str(e))
            return f"I couldn't open {folder_name} — something went wrong."

    # ------------------------------------------------------------------ #
    #  Logging helper
    # ------------------------------------------------------------------ #

    def _record_result(self, action: str, ok: bool, message: str):
        """Record action result for UI display."""
        with self._lock:
            self._log.append({
                "action": action,
                "ok": ok,
                "message": message,
                "time": time.strftime("%H:%M:%S"),
            })
            # Keep only last 20 entries
            if len(self._log) > 20:
                self._log = self._log[-20:]
