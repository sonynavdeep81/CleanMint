"""
core/startup.py — CleanMint Startup Manager

Lists autostart applications (XDG .desktop files) and
systemd user services. Supports enable/disable toggles.
Read-only scan; enable/disable writes only to XDG autostart files.
"""

import json
import os
import shutil
import subprocess
import configparser
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable


HOME = Path.home()
XDG_AUTOSTART_USER  = HOME / ".config" / "autostart"
XDG_AUTOSTART_SYS   = Path("/etc/xdg/autostart")


@dataclass
class StartupEntry:
    id: str
    name: str
    description: str
    source: str          # "xdg_user" | "xdg_system" | "systemd_user"
    enabled: bool
    path: Path | None = None
    exec_cmd: str = ""
    comment: str = ""
    safety: str = "unknown"   # "keep" | "caution" | "safe" | "unknown"
    safety_detail: str = ""   # beginner-friendly explanation


# ── Safety knowledge base ──────────────────────────────────────────
# Maps lowercase substrings of entry id/name to (safety, detail).
# First match wins. Order from most specific to least specific.
_SAFETY_KB: list[tuple[str, str, str]] = [
    # ── Essential — never disable ──────────────────────────────
    ("networkmanager",      "keep",    "Manages your Wi-Fi and network connections. Disabling this will disconnect you from the internet."),
    ("nm-applet",           "keep",    "Shows the network icon in your taskbar. Required to connect to Wi-Fi visually."),
    ("gnome-keyring",       "keep",    "Stores your saved passwords and Wi-Fi keys securely. Many apps depend on this."),
    ("secret-storage",      "keep",    "Stores your saved passwords and Wi-Fi keys securely. Many apps depend on this."),
    ("polkit",              "keep",    "Handles admin permission prompts (the password dialogs). Disabling breaks many system actions."),
    ("dbus",                "keep",    "Core system messaging bus. Almost all apps and services depend on this."),
    ("at-spi",              "keep",    "Accessibility service used by screen readers and assistive tools. Safe to disable only if you use no accessibility features."),
    ("xdg-user-dirs",       "keep",    "Creates your home folders (Documents, Downloads, Pictures…). Safe to keep."),
    ("gnome-session",       "keep",    "Core GNOME session manager. Do not disable."),
    ("ibus",                "keep",    "Input method framework for typing in non-Latin languages. Disable only if you type in English only."),
    ("fcitx",               "keep",    "Input method for typing in Chinese/Japanese/Korean. Disable if you don't need it."),
    ("pipewire",            "keep",    "Modern audio system. Disabling will break sound."),
    ("pulseaudio",          "keep",    "Audio system. Disabling will break sound."),
    ("bluetooth",           "caution", "Bluetooth daemon. Safe to disable if you never use Bluetooth devices."),
    ("gvfs",                "keep",    "Virtual filesystem support (USB drives, network shares). Keep this enabled."),
    ("evolution-alarm",     "safe",    "Shows reminders for GNOME Calendar events. Safe to disable if you don't use the Calendar app."),
    ("gnome-calendar",      "caution", "Calendar background service. Disable if you don't use GNOME Calendar."),

    # ── Safe to disable ────────────────────────────────────────
    ("update-notifier",     "safe",    "Shows a pop-up when software updates are available. Safe to disable — you can still update manually via Software Updater."),
    ("update-manager",      "safe",    "Update notification background service. Safe to disable."),
    ("gnome-software",      "safe",    "App store background service. Safe to disable — only slows boot slightly."),
    ("tracker",             "safe",    "File search indexer. Disable if you never use GNOME Search. Saves CPU and battery."),
    ("zeitgeist",           "safe",    "Logs your file/app activity for search history. Safe to disable for privacy."),
    ("geoclue",             "safe",    "Location service. Safe to disable if no apps use your location (maps, weather, etc.)."),
    ("gnome-initial-setup", "safe",    "First-time GNOME setup wizard. Safe to disable — it only runs once during initial install."),
    ("initial-setup",       "safe",    "First-time setup wizard. Safe to disable after initial installation."),
    ("gnome-color-manager", "safe",    "Colour profile manager for monitors. Safe to disable unless you do graphic design/printing."),
    ("colord",              "safe",    "Colour management service. Safe to disable unless you do colour-critical work."),
    ("gnome-disk-utility",  "safe",    "Disk health notification service. Safe to disable — you can check disk health manually."),
    ("disk-utility",        "safe",    "Disk health monitoring. Safe to disable."),
    ("xscreensaver",        "safe",    "Screen saver. Safe to disable if you use the built-in GNOME screen lock instead."),
    ("redshift",            "safe",    "Adjusts screen colour temperature at night. Safe to disable if you don't want this."),
    ("flux",                "safe",    "Blue light filter for night time. Safe to disable."),
    ("dropbox",             "safe",    "Dropbox cloud sync. Safe to disable to save startup time; Dropbox won't sync until you open it."),
    ("onedrive",            "safe",    "OneDrive sync client. Safe to disable; sync stops until launched."),
    ("google-drive",        "safe",    "Google Drive sync. Safe to disable; sync stops until launched."),
    ("steam",               "safe",    "Steam gaming client. Safe to disable — Steam will still work when you open it manually."),
    ("discord",             "safe",    "Discord chat app. Safe to disable — start it manually when needed."),
    ("slack",               "safe",    "Slack messaging app. Safe to disable — start it manually when needed."),
    ("zoom",                "safe",    "Zoom video conferencing. Safe to disable — start it manually when needed."),
    ("skype",               "safe",    "Skype. Safe to disable — start it manually when needed."),
    ("telegram",            "safe",    "Telegram messaging app. Safe to disable."),
    ("signal",              "safe",    "Signal messenger. Safe to disable."),
    ("spotify",             "safe",    "Spotify music player. Safe to disable — start it manually when needed."),
    ("virtualbox",          "safe",    "VirtualBox VM service. Safe to disable if you don't use virtual machines regularly."),

    # ── Use with caution ──────────────────────────────────────
    ("ssh-agent",           "caution", "Stores SSH keys in memory so you don't re-enter your passphrase. Disable if you don't use SSH (terminal connections to servers)."),
    ("gpg-agent",           "caution", "Stores GPG encryption keys. Disable only if you don't use encrypted email or Git commit signing."),
    ("im-launch",           "caution", "Input method launcher. Disable only if you don't type in non-Latin scripts (e.g. Chinese, Arabic, Hindi)."),
    ("gnome-accessibility", "caution", "Accessibility features (screen magnifier, keyboard shortcuts). Disable only if you use no accessibility tools."),
    ("caribou",             "caution", "On-screen keyboard. Safe to disable if you use a physical keyboard only."),
    ("orca",                "caution", "Screen reader for visually impaired users. Disable only if you don't need it."),
    ("print",               "caution", "Printing service. Disable only if you never print."),
    ("cups",                "caution", "Printer daemon. Disable if you never print."),
    ("gnome-keyring-daemon", "keep",   "Stores saved passwords and encryption keys. Keep this enabled."),
    ("gnome-settings-daemon","keep",   "Core GNOME settings (keyboard, mouse, themes, power). Do not disable."),
    ("gnome-shell",         "keep",    "The GNOME desktop shell itself. Do not disable."),
    ("gnome-screensaver",   "caution", "Screen lock/screensaver. Disable only if you use a different screen locker."),
    ("light-locker",        "caution", "Screen locker. Disable only if you use a different screen locker."),

    # ── GNOME Shell extensions ─────────────────────────────────
    ("ubuntu-appindicators", "keep",   "Adds system tray icon support (Wi-Fi, battery, Dropbox, etc.). Keep enabled if you use any tray apps."),
    ("ubuntu-dock",          "caution","Ubuntu's side dock. Safe to disable if you prefer a different dock or launcher."),
    ("tiling-assistant",     "safe",   "Adds window tiling helpers (snap windows to halves/quarters). Safe to disable if you don't use tiling."),
    ("ding@rastersoft",      "safe",   "Desktop Icons NG — shows files and drives directly on your desktop. Safe to disable for a cleaner look."),
    ("soft-brightness",      "safe",   "Controls screen brightness in software (useful when hardware brightness keys don't work). Safe to disable."),
    ("blur-my-shell",        "safe",   "Adds blur effects to the GNOME shell. Safe to disable — purely cosmetic."),
    ("dash-to-dock",         "caution","Replaces the default dock. Disable with care if it is your primary launcher."),
    ("dash-to-panel",        "caution","Moves the dash into the top/bottom panel. Disable with care if it is your primary launcher."),
    ("just-perfection",      "safe",   "Tweaks GNOME UI elements. Safe to disable."),
    ("night-light-slider",   "safe",   "Adds a slider for the built-in Night Light strength. Safe to disable."),
    ("caffeine",             "safe",   "Prevents the screen from sleeping when toggled on. Safe to disable."),
    ("gsconnect",            "safe",   "Connects your Android phone to GNOME (KDE Connect for GNOME). Safe to disable if you don't use it."),
    ("vitals",               "safe",   "Shows CPU/RAM/temperature in the top bar. Safe to disable."),
    ("system-monitor",       "safe",   "Shows system resource usage in the top bar. Safe to disable."),
    ("clipboard-indicator",  "safe",   "Clipboard history manager in the top bar. Safe to disable."),
    ("user-theme",           "safe",   "Allows installing custom GNOME Shell themes. Safe to disable if you use the default theme."),
]


def _classify_entry(entry_id: str, entry_name: str) -> tuple[str, str]:
    """Return (safety, detail) for a startup entry using the knowledge base."""
    haystack = (entry_id + " " + entry_name).lower()
    for keyword, safety, detail in _SAFETY_KB:
        if keyword in haystack:
            return safety, detail
    return "unknown", "No information available for this entry. Search online for its name to learn more."


class StartupManager:
    def __init__(self, progress_callback: Callable[[str, int], None] | None = None):
        self._progress = progress_callback or (lambda m, p: None)

    def list_entries(self) -> list[StartupEntry]:
        entries: list[StartupEntry] = []
        self._progress("Reading XDG autostart entries…", 20)
        entries += self._read_xdg_autostart(XDG_AUTOSTART_SYS, source="xdg_system")
        entries += self._read_xdg_autostart(XDG_AUTOSTART_USER, source="xdg_user")
        self._progress("Reading systemd user services…", 60)
        entries += self._read_systemd_user()
        self._progress("Reading GNOME Shell extensions…", 80)
        entries += self._read_gnome_extensions()
        self._progress("Done.", 100)
        return entries

    def disable_entry(self, entry: StartupEntry) -> tuple[bool, str]:
        """
        Disable a startup entry.
        For XDG system entries: copies to ~/.config/autostart with Hidden=true.
        For XDG user entries: sets Hidden=true in place.
        For systemd user: calls systemctl --user disable.
        For GNOME extensions: calls gnome-extensions disable.
        Returns (success, message).
        """
        if entry.source == "xdg_system":
            return self._disable_xdg_system(entry)
        elif entry.source == "xdg_user":
            return self._set_xdg_hidden(entry.path, True)
        elif entry.source == "systemd_user":
            return self._systemd_user_toggle(entry.id, enable=False)
        elif entry.source == "gnome_extension":
            return self._gnome_ext_toggle(entry.id, enable=False)
        return False, "Unknown source"

    def enable_entry(self, entry: StartupEntry) -> tuple[bool, str]:
        """Re-enable a previously disabled startup entry."""
        if entry.source in ("xdg_system", "xdg_user"):
            return self._set_xdg_hidden(
                XDG_AUTOSTART_USER / (entry.id + ".desktop"), False
            )
        elif entry.source == "systemd_user":
            return self._systemd_user_toggle(entry.id, enable=True)
        elif entry.source == "gnome_extension":
            return self._gnome_ext_toggle(entry.id, enable=True)
        return False, "Unknown source"

    # ── XDG autostart ──────────────────────────────────────────

    def _read_xdg_autostart(self, directory: Path, source: str) -> list[StartupEntry]:
        entries = []
        if not directory.exists():
            return entries

        # User overrides: collect names that are explicitly disabled
        user_overrides: dict[str, bool] = {}
        if source == "xdg_system" and XDG_AUTOSTART_USER.exists():
            for f in XDG_AUTOSTART_USER.glob("*.desktop"):
                cfg = self._parse_desktop(f)
                hidden = cfg.get("Desktop Entry", {}).get("Hidden", "false").lower()
                user_overrides[f.stem] = (hidden == "true")

        for desktop_file in sorted(directory.glob("*.desktop")):
            cfg = self._parse_desktop(desktop_file)
            if not cfg:
                continue
            entry_cfg = cfg.get("Desktop Entry", {})

            name = entry_cfg.get("Name", desktop_file.stem)
            hidden = entry_cfg.get("Hidden", "false").lower() == "true"
            no_display = entry_cfg.get("NoDisplay", "false").lower() == "true"

            # Check user override for system entries
            if source == "xdg_system":
                if desktop_file.stem in user_overrides:
                    hidden = user_overrides[desktop_file.stem]

            safety, safety_detail = _classify_entry(desktop_file.stem, name)
            entries.append(StartupEntry(
                id=desktop_file.stem,
                name=name,
                description=entry_cfg.get("Comment", ""),
                source=source,
                enabled=not hidden,
                path=desktop_file,
                exec_cmd=entry_cfg.get("Exec", ""),
                comment=entry_cfg.get("Comment", ""),
                safety=safety,
                safety_detail=safety_detail,
            ))

        return entries

    def _parse_desktop(self, path: Path) -> dict:
        cfg = configparser.RawConfigParser()
        cfg.optionxform = str  # preserve case
        try:
            cfg.read(str(path), encoding="utf-8")
            return {section: dict(cfg[section]) for section in cfg.sections()}
        except Exception:
            return {}

    def _disable_xdg_system(self, entry: StartupEntry) -> tuple[bool, str]:
        """Copy system .desktop to user autostart dir with Hidden=true."""
        XDG_AUTOSTART_USER.mkdir(parents=True, exist_ok=True)
        dest = XDG_AUTOSTART_USER / (entry.id + ".desktop")
        try:
            import shutil
            if entry.path:
                shutil.copy2(entry.path, dest)
            return self._set_xdg_hidden(dest, True)
        except OSError as e:
            return False, str(e)

    def _set_xdg_hidden(self, path: Path, hidden: bool) -> tuple[bool, str]:
        cfg = configparser.RawConfigParser()
        cfg.optionxform = str
        try:
            if path.exists():
                cfg.read(str(path), encoding="utf-8")
            if not cfg.has_section("Desktop Entry"):
                cfg.add_section("Desktop Entry")
            cfg.set("Desktop Entry", "Hidden", "true" if hidden else "false")
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                cfg.write(f)
            return True, f"{'Disabled' if hidden else 'Enabled'}: {path.name}"
        except OSError as e:
            return False, str(e)

    # ── systemd user services ──────────────────────────────────

    def _read_systemd_user(self) -> list[StartupEntry]:
        entries = []
        try:
            result = subprocess.run(
                ["systemctl", "--user", "list-unit-files", "--type=service",
                 "--state=enabled,disabled", "--no-legend", "--plain"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    unit, state = parts[0], parts[1]
                    svc_name = unit.replace(".service", "")
                    safety, safety_detail = _classify_entry(unit, svc_name)
                    entries.append(StartupEntry(
                        id=unit,
                        name=svc_name,
                        description="systemd user service",
                        source="systemd_user",
                        enabled=(state == "enabled"),
                        exec_cmd=f"systemctl --user {'enable' if state != 'enabled' else 'disable'} {unit}",
                        safety=safety,
                        safety_detail=safety_detail,
                    ))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return entries

    def _systemd_user_toggle(self, unit: str, enable: bool) -> tuple[bool, str]:
        action = "enable" if enable else "disable"
        try:
            result = subprocess.run(
                ["systemctl", "--user", action, unit],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return True, f"{action.capitalize()}d {unit}"
            return False, result.stderr.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return False, str(e)

    # ── GNOME Shell extensions ─────────────────────────────────

    _EXT_SEARCH_PATHS = [
        HOME / ".local" / "share" / "gnome-shell" / "extensions",
        Path("/usr/share/gnome-shell/extensions"),
    ]

    def _read_gnome_extensions(self) -> list[StartupEntry]:
        if not shutil.which("gnome-extensions"):
            return []
        try:
            enabled_out = subprocess.run(
                ["gnome-extensions", "list", "--enabled"],
                capture_output=True, text=True, timeout=10,
            )
            enabled_uuids = {
                line.strip() for line in enabled_out.stdout.splitlines() if line.strip()
            }
            all_out = subprocess.run(
                ["gnome-extensions", "list"],
                capture_output=True, text=True, timeout=10,
            )
            all_uuids = [
                line.strip() for line in all_out.stdout.splitlines() if line.strip()
            ]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []

        entries = []
        for uuid in sorted(all_uuids):
            name = uuid
            ext_path = None
            for search in self._EXT_SEARCH_PATHS:
                candidate = search / uuid
                if candidate.is_dir():
                    ext_path = candidate
                    meta_file = candidate / "metadata.json"
                    if meta_file.is_file():
                        try:
                            meta = json.loads(meta_file.read_text())
                            name = meta.get("name", uuid)
                        except Exception:
                            pass
                    break

            safety, safety_detail = _classify_entry(uuid, name)
            entries.append(StartupEntry(
                id=uuid,
                name=name,
                description="GNOME Shell extension",
                source="gnome_extension",
                enabled=(uuid in enabled_uuids),
                path=ext_path,
                exec_cmd="",
                safety=safety,
                safety_detail=safety_detail,
            ))
        return entries

    def _gnome_ext_toggle(self, uuid: str, enable: bool) -> tuple[bool, str]:
        action = "enable" if enable else "disable"
        try:
            result = subprocess.run(
                ["gnome-extensions", action, uuid],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return True, f"{action.capitalize()}d {uuid}"
            return False, result.stderr.strip() or f"Failed to {action} extension"
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return False, str(e)
