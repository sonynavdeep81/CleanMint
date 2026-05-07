"""
core/screenshot.py — Screenshot repair engine.

Diagnoses why GNOME screenshots aren't being saved and applies fixes.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SCREENSHOTS_DIR = Path.home() / "Pictures" / "Screenshots"
GNOME_SS_SCHEMA = "org.gnome.gnome-screenshot"
SHELL_KB_SCHEMA = "org.gnome.shell.keybindings"

_SEARCH_DIRS = [
    Path.home(),
    Path.home() / "Pictures",
    Path.home() / "Downloads",
    Path("/tmp"),
]
_SCREENSHOT_PATTERNS = [
    "Screenshot*.png", "Screenshot*.jpg",
    "screenshot*.png", "screenshot*.jpg",
    "Screenshot_*.png", "Captura*.png",
]


@dataclass
class DiagnosticCheck:
    name: str
    status: str        # "ok" | "warn" | "fail"
    detail: str
    fix_available: bool = False
    fix_key: str = ""


@dataclass
class ScreenshotStatus:
    checks: list[DiagnosticCheck] = field(default_factory=list)
    lost_files: list[Path] = field(default_factory=list)


def _gsettings_get(schema: str, key: str) -> Optional[str]:
    try:
        r = subprocess.run(
            ["gsettings", "get", schema, key],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _gsettings_set(schema: str, key: str, value: str) -> bool:
    try:
        r = subprocess.run(
            ["gsettings", "set", schema, key, value],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _service_active(name: str) -> Optional[bool]:
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", name],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() == "active"
    except Exception:
        return None


class ScreenshotDoctor:
    """Diagnose and repair GNOME screenshot save issues."""

    def diagnose(self) -> ScreenshotStatus:
        status = ScreenshotStatus()
        status.checks = [
            self._check_directory(),
            self._check_tool(),
            self._check_keybinding(),
            self._check_auto_save(),
            self._check_portal(),
            self._check_extension_compat(),
        ]
        status.lost_files = self._find_lost()
        if status.lost_files:
            status.checks.append(DiagnosticCheck(
                name="Lost screenshots",
                status="warn",
                detail=(
                    f"Found {len(status.lost_files)} screenshot(s) saved "
                    "outside the Screenshots folder"
                ),
                fix_available=True,
                fix_key="move_lost",
            ))
        return status

    def _check_directory(self) -> DiagnosticCheck:
        if not SCREENSHOTS_DIR.exists():
            return DiagnosticCheck(
                name="Screenshots folder",
                status="fail",
                detail=f"{SCREENSHOTS_DIR} does not exist",
                fix_available=True,
                fix_key="create_dir",
            )
        if not os.access(SCREENSHOTS_DIR, os.W_OK):
            return DiagnosticCheck(
                name="Screenshots folder",
                status="fail",
                detail=f"{SCREENSHOTS_DIR} is not writable",
                fix_available=True,
                fix_key="fix_perms",
            )
        return DiagnosticCheck(
            name="Screenshots folder",
            status="ok",
            detail=str(SCREENSHOTS_DIR),
        )

    def _check_tool(self) -> DiagnosticCheck:
        if shutil.which("gnome-screenshot"):
            return DiagnosticCheck(
                name="Screenshot tool",
                status="ok",
                detail="gnome-screenshot is installed",
            )
        return DiagnosticCheck(
            name="Screenshot tool",
            status="warn",
            detail="gnome-screenshot not installed — GNOME Shell built-in handles screenshots (this is fine)",
        )

    def _check_keybinding(self) -> DiagnosticCheck:
        kb = _gsettings_get(SHELL_KB_SCHEMA, "show-screenshot-ui")
        if kb and "Print" in kb:
            return DiagnosticCheck(
                name="Print Screen keybinding",
                status="ok",
                detail=f"Bound to: {kb}",
            )
        return DiagnosticCheck(
            name="Print Screen keybinding",
            status="fail",
            detail=f"Not bound to Print Screen (current: {kb or 'unset'})",
            fix_available=True,
            fix_key="fix_keybinding",
        )

    def _check_auto_save(self) -> DiagnosticCheck:
        raw = _gsettings_get(GNOME_SS_SCHEMA, "auto-save-directory")
        if raw is None:
            return DiagnosticCheck(
                name="Auto-save directory",
                status="warn",
                detail="org.gnome.gnome-screenshot schema not found — GNOME Shell built-in handles saving",
            )
        value = raw.strip("'\"")
        expected = f"file://{SCREENSHOTS_DIR}"
        if value == expected:
            return DiagnosticCheck(
                name="Auto-save directory",
                status="ok",
                detail=f"Set to: {value}",
            )
        return DiagnosticCheck(
            name="Auto-save directory",
            status="fail",
            detail=(
                f"Not pointing at Screenshots folder — "
                f"current: '{value or 'empty'}', expected: '{expected}'"
            ),
            fix_available=True,
            fix_key="fix_auto_save",
        )

    def _check_portal(self) -> DiagnosticCheck:
        active = _service_active("xdg-desktop-portal")
        if active is True:
            return DiagnosticCheck(
                name="xdg-desktop-portal service",
                status="ok",
                detail="Running",
            )
        if active is False:
            return DiagnosticCheck(
                name="xdg-desktop-portal service",
                status="fail",
                detail="Service is stopped — screenshots cannot be saved",
                fix_available=True,
                fix_key="restart_portal",
            )
        return DiagnosticCheck(
            name="xdg-desktop-portal service",
            status="warn",
            detail="Could not determine status",
        )

    def _get_enabled_extensions(self) -> list[str]:
        try:
            r = subprocess.run(
                ["gnome-extensions", "list", "--enabled"],
                capture_output=True, text=True, timeout=5,
            )
            return [e.strip() for e in r.stdout.splitlines() if e.strip()]
        except Exception:
            return []

    _CRASH_KEYWORDS = ("TypeError", "JS ERROR", "Error:", "exception", "crashed", "Traceback")

    def _get_crashing_extensions(self) -> list[str]:
        """Read current-boot journal for GNOME Shell extension errors blocking screenshots."""
        try:
            r = subprocess.run(
                ["journalctl", "--user", "-b", "0", "--no-pager", "_COMM=gnome-shell"],
                capture_output=True, text=True, timeout=8,
            )
            lines = r.stdout.splitlines()
        except Exception:
            return []

        ext_dir = Path.home() / ".local/share/gnome-shell/extensions"
        enabled = self._get_enabled_extensions()
        crashing: list[str] = []
        for ext_id in enabled:
            ext_path = str(ext_dir / ext_id)
            # If the extension is currently active it's not blocking screenshots
            try:
                info = subprocess.run(
                    ["gnome-extensions", "info", ext_id],
                    capture_output=True, text=True, timeout=5,
                )
                if "State: ACTIVE" in info.stdout:
                    continue
            except Exception:
                pass
            for line in lines:
                if ext_path in line and any(kw in line for kw in self._CRASH_KEYWORDS):
                    crashing.append(ext_id)
                    break
        return crashing

    def _check_extension_compat(self) -> DiagnosticCheck:
        """Check enabled extensions for broken APIs or active GNOME Shell crashes."""
        ext_dir = Path.home() / ".local/share/gnome-shell/extensions"
        enabled = self._get_enabled_extensions()
        if not enabled:
            return DiagnosticCheck(
                name="Extension compatibility",
                status="warn",
                detail="Could not list enabled extensions",
            )

        # 1. Static: unguarded inhibit_cursor_visibility (removed in GNOME 46)
        api_broken: list[str] = []
        for ext_id in enabled:
            js = ext_dir / ext_id / "extension.js"
            if not js.exists():
                continue
            try:
                text = js.read_text(errors="replace")
                already_guarded = (
                    "inhibit_cursor_visibility !== undefined" in text or
                    "typeof this._cursorTracker.inhibit_cursor_visibility" in text
                )
                if "inhibit_cursor_visibility" in text and not already_guarded:
                    api_broken.append(ext_id)
            except Exception:
                pass

        # 2. Dynamic: extensions actively crashing GNOME Shell (seen in journal)
        crashing = self._get_crashing_extensions()

        # Merge both lists (crashing takes priority)
        all_broken = list(dict.fromkeys(crashing + api_broken))  # deduplicate, crashing first

        if all_broken:
            reasons = []
            if crashing:
                reasons.append(f"crashing in journal: {', '.join(crashing)}")
            if api_broken:
                reasons.append(f"removed API calls: {', '.join(api_broken)}")
            # Patch + re-enable for API-fixable extensions; disable-only for pure crashes
            fix_key = "fix_extension_compat" if api_broken else "disable_crashing_extensions"
            return DiagnosticCheck(
                name="Extension compatibility",
                status="fail",
                detail=(
                    f"{len(all_broken)} extension(s) are blocking screenshots — "
                    + "; ".join(reasons)
                ),
                fix_available=True,
                fix_key=fix_key,
            )
        return DiagnosticCheck(
            name="Extension compatibility",
            status="ok",
            detail="No extensions interfering with screenshots",
        )

    def _fix_extension_compat(self) -> tuple[bool, str]:
        """Patch extensions that call inhibit/uninhibit_cursor_visibility without a guard."""
        ext_dir = Path.home() / ".local/share/gnome-shell/extensions"
        try:
            r = subprocess.run(
                ["gnome-extensions", "list", "--enabled"],
                capture_output=True, text=True, timeout=5,
            )
            enabled = [e.strip() for e in r.stdout.splitlines() if e.strip()]
        except Exception:
            return False, "Could not list enabled extensions"

        patched: list[str] = []
        for ext_id in enabled:
            js = ext_dir / ext_id / "extension.js"
            if not js.exists():
                continue
            try:
                original = js.read_text(errors="replace")
                already_guarded = (
                    "inhibit_cursor_visibility !== undefined" in original or
                    "typeof this._cursorTracker.inhibit_cursor_visibility" in original
                )
                if "inhibit_cursor_visibility" not in original or already_guarded:
                    continue
                fixed = original.replace(
                    "this._cursorTracker.uninhibit_cursor_visibility();",
                    "if (typeof this._cursorTracker.uninhibit_cursor_visibility === 'function')\n"
                    "                this._cursorTracker.uninhibit_cursor_visibility();",
                ).replace(
                    "this._cursorTracker.inhibit_cursor_visibility();",
                    "if (typeof this._cursorTracker.inhibit_cursor_visibility === 'function')\n"
                    "                this._cursorTracker.inhibit_cursor_visibility();",
                )
                js.write_text(fixed)
                patched.append(ext_id)
                # Attempt hot-reload (effective on X11; on Wayland takes effect after re-login)
                subprocess.run(
                    ["gnome-extensions", "disable", ext_id],
                    capture_output=True, timeout=5,
                )
                # Always re-enable so the extension (and its UI, e.g. brightness bar) stays active
                subprocess.run(
                    ["gnome-extensions", "enable", ext_id],
                    capture_output=True, timeout=5,
                )
            except Exception as e:
                return False, f"Failed to patch {ext_id}: {e}"

        if patched:
            return True, f"Patched and reloaded: {', '.join(patched)}"
        return True, "No extensions needed patching"

    def _disable_crashing_extensions(self) -> tuple[bool, str]:
        """Disable all extensions found to be crashing GNOME Shell or using removed APIs."""
        ext_dir = Path.home() / ".local/share/gnome-shell/extensions"
        enabled = self._get_enabled_extensions()
        crashing = self._get_crashing_extensions()

        # Also include any with unguarded removed API calls
        api_broken: list[str] = []
        for ext_id in enabled:
            js = ext_dir / ext_id / "extension.js"
            if not js.exists():
                continue
            try:
                text = js.read_text(errors="replace")
                already_guarded = (
                    "inhibit_cursor_visibility !== undefined" in text or
                    "typeof this._cursorTracker.inhibit_cursor_visibility" in text
                )
                if "inhibit_cursor_visibility" in text and not already_guarded:
                    api_broken.append(ext_id)
            except Exception:
                pass

        targets = list(dict.fromkeys(crashing + api_broken))
        if not targets:
            return True, "No broken extensions found to disable"

        disabled: list[str] = []
        failed: list[str] = []
        for ext_id in targets:
            try:
                r = subprocess.run(
                    ["gnome-extensions", "disable", ext_id],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0:
                    disabled.append(ext_id)
                else:
                    failed.append(ext_id)
            except Exception:
                failed.append(ext_id)

        msg = f"Disabled: {', '.join(disabled)}" if disabled else ""
        if failed:
            msg += f"  (could not disable: {', '.join(failed)})"
        return bool(disabled), msg.strip()

    def _find_lost(self) -> list[Path]:
        found: list[Path] = []
        for d in _SEARCH_DIRS:
            if not d.exists():
                continue
            for pat in _SCREENSHOT_PATTERNS:
                try:
                    for f in d.glob(pat):
                        if f.is_file() and f.parent != SCREENSHOTS_DIR:
                            found.append(f)
                except PermissionError:
                    pass
        seen: set[Path] = set()
        unique: list[Path] = []
        for f in found:
            if f not in seen:
                seen.add(f)
                unique.append(f)
        return unique

    def apply_fix(self, fix_key: str) -> tuple[bool, str]:
        if fix_key == "create_dir":
            try:
                SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
                return True, f"Created {SCREENSHOTS_DIR}"
            except Exception as e:
                return False, str(e)

        if fix_key == "fix_perms":
            try:
                SCREENSHOTS_DIR.chmod(0o775)
                return True, "Fixed folder permissions"
            except Exception as e:
                return False, str(e)

        if fix_key == "fix_keybinding":
            ok = _gsettings_set(SHELL_KB_SCHEMA, "show-screenshot-ui", "['Print']")
            return (True, "Print Screen keybinding restored") if ok else (False, "gsettings set failed")

        if fix_key == "fix_auto_save":
            uri = f"'file://{SCREENSHOTS_DIR}'"
            ok1 = _gsettings_set(GNOME_SS_SCHEMA, "auto-save-directory", uri)
            ok2 = _gsettings_set(GNOME_SS_SCHEMA, "last-save-directory", uri)
            if ok1 or ok2:
                return True, f"Auto-save directory set to {SCREENSHOTS_DIR}"
            return False, "Could not update gsettings"

        if fix_key == "restart_portal":
            try:
                subprocess.run(
                    ["systemctl", "--user", "restart", "xdg-desktop-portal"],
                    timeout=15, check=True,
                )
                return True, "xdg-desktop-portal restarted"
            except Exception as e:
                return False, str(e)

        if fix_key == "move_lost":
            lost = self._find_lost()
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
            moved, failed = 0, 0
            for f in lost:
                dest = SCREENSHOTS_DIR / f.name
                if dest.exists():
                    dest = SCREENSHOTS_DIR / (f.stem + "_recovered" + f.suffix)
                try:
                    shutil.move(str(f), str(dest))
                    moved += 1
                except Exception:
                    failed += 1
            msg = f"Moved {moved} screenshot(s) to Screenshots folder"
            if failed:
                msg += f" ({failed} could not be moved)"
            return True, msg

        if fix_key == "fix_extension_compat":
            return self._fix_extension_compat()

        if fix_key == "disable_crashing_extensions":
            return self._disable_crashing_extensions()

        return False, f"Unknown fix key: {fix_key}"

    def apply_all_fixes(self, checks: list[DiagnosticCheck]) -> list[tuple[str, bool, str]]:
        results: list[tuple[str, bool, str]] = []
        for check in checks:
            if check.fix_available:
                ok, msg = self.apply_fix(check.fix_key)
                results.append((check.name, ok, msg))
        return results
