"""
ROSE Permission & Security Center
Centralized permission management for all sensitive capabilities.
Each permission has three states: ALLOW, DENY, ASK (prompt user).
Permissions are persisted to permissions.json.
"""
import json
import os
import threading
import time

PERMISSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "permissions.json")

# Permission states
ALLOW = "allow"
DENY = "deny"
ASK = "ask"

# Default permissions — conservative (most things start as ASK or DENY)
DEFAULT_PERMISSIONS = {
    "microphone": {
        "state": ALLOW,
        "description": "Allow ROSE to listen to your voice.",
        "required_by": ["Voice input", "Speech recognition"],
    },
    "location": {
        "state": DENY,
        "description": "Used for weather and location-based information.",
        "required_by": ["Weather", "Local search"],
    },
    "internet": {
        "state": ALLOW,
        "description": "Allow ROSE to access the internet for API calls.",
        "required_by": ["Groq LLM", "Web search", "Weather API"],
    },
    "web_search": {
        "state": DENY,
        "description": "Allow ROSE to search the web for current information.",
        "required_by": ["Web search", "Research mode"],
    },
    "browser": {
        "state": DENY,
        "description": "Allow ROSE to open and control a web browser.",
        "required_by": ["YouTube search", "Web browsing"],
    },
    "file_read": {
        "state": DENY,
        "description": "Allow ROSE to read local files.",
        "required_by": ["Document summarization", "File search"],
    },
    "file_write": {
        "state": DENY,
        "description": "Allow ROSE to modify or create files.",
        "required_by": ["Notes", "File export"],
    },
    "app_launch": {
        "state": ALLOW,
        "description": "Allow ROSE to open whitelisted applications.",
        "required_by": ["System actions"],
    },
    "system_actions": {
        "state": ALLOW,
        "description": "Allow ROSE to perform system actions (lock, shutdown, etc.).",
        "required_by": ["System actions"],
    },
    "clipboard": {
        "state": DENY,
        "description": "Allow ROSE to read/write the clipboard.",
        "required_by": ["Clipboard assistance"],
    },
    "notifications": {
        "state": ALLOW,
        "description": "Allow ROSE to show desktop notifications.",
        "required_by": ["Reminders", "Alerts"],
    },
}


class PermissionManager:
    """Thread-safe permission store with JSON persistence."""

    def __init__(self):
        self._lock = threading.Lock()
        self._permissions = {}
        self._load()

    def _load(self):
        """Load permissions from file, filling in any missing defaults."""
        try:
            if os.path.exists(PERMISSIONS_FILE):
                with open(PERMISSIONS_FILE, "r") as f:
                    saved = json.load(f)
                # Merge saved with defaults (saved overrides state only)
                for key, default_data in DEFAULT_PERMISSIONS.items():
                    if key in saved and "state" in saved[key]:
                        self._permissions[key] = {
                            **default_data,
                            "state": saved[key]["state"],
                        }
                    else:
                        self._permissions[key] = dict(default_data)
            else:
                self._permissions = {k: dict(v) for k, v in DEFAULT_PERMISSIONS.items()}
        except Exception:
            self._permissions = {k: dict(v) for k, v in DEFAULT_PERMISSIONS.items()}

    def _save(self):
        """Persist current permission states to file."""
        try:
            to_save = {k: {"state": v["state"]} for k, v in self._permissions.items()}
            with open(PERMISSIONS_FILE, "w") as f:
                json.dump(to_save, f, indent=2)
        except Exception:
            pass

    # ---- Public API ----

    def check(self, permission_name: str) -> str:
        """
        Check if a permission is granted.
        Returns: 'allow', 'deny', or 'ask'
        """
        with self._lock:
            perm = self._permissions.get(permission_name)
            if perm is None:
                return DENY  # fail closed — unknown permissions are denied
            return perm["state"]

    def is_allowed(self, permission_name: str) -> bool:
        """Quick check: is this permission allowed?"""
        return self.check(permission_name) == ALLOW

    def set_permission(self, permission_name: str, state: str) -> bool:
        """
        Set a permission state. Returns True if successful.
        State must be 'allow', 'deny', or 'ask'.
        """
        if state not in (ALLOW, DENY, ASK):
            return False
        with self._lock:
            if permission_name not in self._permissions:
                return False
            self._permissions[permission_name]["state"] = state
            self._save()
            return True

    def get_all(self) -> list:
        """Return all permissions with their states and descriptions."""
        with self._lock:
            result = []
            for name, data in self._permissions.items():
                result.append({
                    "name": name,
                    "state": data["state"],
                    "description": data["description"],
                    "required_by": data.get("required_by", []),
                })
            return result

    def get_denied_message(self, permission_name: str) -> str:
        """Return a user-friendly message when a permission is denied."""
        with self._lock:
            perm = self._permissions.get(permission_name)
            if perm is None:
                return f"Permission '{permission_name}' is not available."
            desc = perm.get("description", "")
            return f"I don't have '{permission_name}' permission. {desc} Please enable it in Settings → Permissions."

    def reset_to_defaults(self):
        """Reset all permissions to their default states."""
        with self._lock:
            self._permissions = {k: dict(v) for k, v in DEFAULT_PERMISSIONS.items()}
            self._save()


# Module-level singleton
permissions = PermissionManager()
