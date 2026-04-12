"""
config/settings.py — CleanMint persistent settings

Stored as JSON at ~/.config/cleanmint/settings.json
"""

import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "cleanmint" / "settings.json"

DEFAULTS = {
    "dark_mode": True,
    "scan_on_startup": False,
    "auto_monthly_reminder": True,
    "duplicate_method": "hash",        # "hash" | "name_size"
    "excluded_paths": [],
    "excluded_extensions": [],
    "downloads_age_days": 30,
    "log_retention_days": 90,
    "last_clean_date": None,
    "last_scan_date": None,
}


class Settings:
    def __init__(self):
        self._data: dict = {}
        self.load()

    def load(self):
        if SETTINGS_PATH.exists():
            try:
                with open(SETTINGS_PATH, "r") as f:
                    saved = json.load(f)
                # Merge: keep defaults for any missing keys
                self._data = {**DEFAULTS, **saved}
            except (json.JSONDecodeError, OSError):
                self._data = dict(DEFAULTS)
        else:
            self._data = dict(DEFAULTS)

    def save(self):
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(self._data, f, indent=2, default=str)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self.set(key, value)


# Global singleton
settings = Settings()
