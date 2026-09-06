"""
core/obs_switcher.py — OBS laptop⇄tablet scene-switching engine (UI-free).

Builds, protects, backs up, verifies, and tests the automation layer around
the OBS "Laptop" / "Tablet" scenes. Never edits scene contents.

See docs/superpowers/specs/2026-09-06-obs-switcher-design.md
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

WS_PORT = 4455
SCENES = ("Laptop", "Tablet")
# (accelerator, shortcut name, scene)
HOTKEYS = [
    ("<Control><Alt>1", "OBS — Laptop", "Laptop"),
    ("<Control><Alt>2", "OBS — Tablet", "Tablet"),
]
MAX_BACKUPS = 10
DCONF_MEDIA_KEYS = "/org/gnome/settings-daemon/plugins/media-keys/"
MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEYBINDING_SCHEMA = (
    "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
)
HELPER = Path("/usr/local/lib/cleanmint/cleanmint-helper")


@dataclass(frozen=True)
class Paths:
    home: Path
    script: Path
    pw_dir: Path
    pw_file: Path
    venv_dir: Path
    venv_py: Path
    obs_cfg_dir: Path
    obs_ws_config: Path
    scene_dir: Path
    backup_root: Path


def _paths() -> Paths:
    """Resolve all paths from the current HOME at call time (test-friendly)."""
    home = Path.home()
    obs_cfg = home / ".var/app/com.obsproject.Studio/config/obs-studio"
    return Paths(
        home=home,
        script=home / ".local/bin/obs-scene",
        pw_dir=home / ".config/obs-hotkeys",
        pw_file=home / ".config/obs-hotkeys/password",
        venv_dir=home / ".obs-hotkey-venv",
        venv_py=home / ".obs-hotkey-venv/bin/python3",
        obs_cfg_dir=obs_cfg,
        obs_ws_config=obs_cfg / "plugin_config/obs-websocket/config.json",
        scene_dir=obs_cfg / "basic/scenes",
        backup_root=home / ".local/share/cleanmint/obs-switcher/backups",
    )


# ── Data types ─────────────────────────────────────────────────────────────

@dataclass
class Check:
    key: str
    label: str
    ok: bool
    detail: str
    fixable: bool
    manual_steps: str = ""


@dataclass
class StepResult:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class BuildResult:
    steps: list[StepResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_password: bool = False

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps) and not self.needs_password


@dataclass
class VerifyItem:
    label: str
    status: str  # "ok" | "changed" | "missing"
    can_restore: bool


class NeedsPasswordError(Exception):
    pass


# ── Pure helpers ───────────────────────────────────────────────────────────

_SCRIPT_TEMPLATE = '''\
#!{venv_py}

import sys
import obsws_python as obs

if len(sys.argv) != 2:
    sys.exit(1)

scene = sys.argv[1]

with open("{pw_file}", "r") as f:
    password = f.read().strip()

client = obs.ReqClient(host="localhost", port=4455, password=password, timeout=2)
client.set_current_program_scene(scene)
client.disconnect()
'''


def render_script(venv_py: str, pw_file: str) -> str:
    """Return the exact text of ~/.local/bin/obs-scene for these paths."""
    return _SCRIPT_TEMPLATE.format(venv_py=venv_py, pw_file=pw_file)


def plan_shortcut_slots(
    existing: list[str],
    existing_cmds: dict[str, str],
    script_path: str,
) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    """
    Plan where the two OBS shortcuts go without disturbing other custom
    keybindings.

    Args:
        existing:      current custom-keybinding dconf paths (each ends with "/")
        existing_cmds: path -> stored `command` value (may include surrounding
                       quotes; compared loosely)
        script_path:   absolute path to obs-scene

    Returns:
        (plan, final_list) where plan is a list of
        (dconf_path, accelerator, name, command) and final_list is the full
        custom-keybindings array to write back (existing order preserved,
        new slots appended).
    """
    base = DCONF_MEDIA_KEYS + "custom-keybindings/"
    final_list = list(existing)
    plan: list[tuple[str, str, str, str]] = []

    def _norm(v: str) -> str:
        return v.strip().strip("'\"").strip()

    used_paths = set(existing)
    for accel, name, scene in HOTKEYS:
        want_cmd = f"{script_path} {scene}"
        reuse = None
        for path in existing:
            if _norm(existing_cmds.get(path, "")).endswith(f"obs-scene {scene}"):
                reuse = path
                break
        if reuse is None:
            n = 0
            while f"{base}custom{n}/" in used_paths:
                n += 1
            reuse = f"{base}custom{n}/"
            used_paths.add(reuse)
            final_list.append(reuse)
        plan.append((reuse, accel, name, want_cmd))

    return plan, final_list


# ── Environment probes ─────────────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = 15, **kw) -> subprocess.CompletedProcess:
    """subprocess.run wrapper: list-form only, captured, text, never shell."""
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, **kw
    )


def obs_running() -> bool:
    try:
        r = _run(["pgrep", "-f", "com.obsproject.Studio"], timeout=5)
        return r.returncode == 0 and r.stdout.strip() != ""
    except Exception:
        return False


def websocket_reachable(timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", WS_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def read_obs_password() -> str | None:
    p = _paths()
    try:
        data = json.loads(p.obs_ws_config.read_text())
        pw = data.get("server_password")
        return pw if isinstance(pw, str) and pw else None
    except Exception:
        return None


def set_password(pw: str) -> None:
    """Write the WebSocket password to ~/.config/obs-hotkeys/password (600)."""
    p = _paths()
    p.pw_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(p.pw_dir, 0o700)
    p.pw_file.write_text(pw.strip())
    os.chmod(p.pw_file, 0o600)


def _write_script() -> None:
    """(Re)write ~/.local/bin/obs-scene from the template, mode 755."""
    p = _paths()
    p.script.parent.mkdir(parents=True, exist_ok=True)
    p.script.write_text(render_script(str(p.venv_py), str(p.pw_file)))
    os.chmod(p.script, 0o755)


# ── Status checks ──────────────────────────────────────────────────────────

_STEPS_OBS_INSTALL = (
    "Install OBS (Flatpak):\n"
    "  flatpak remote-add --if-not-exists flathub "
    "https://flathub.org/repo/flathub.flatpakrepo\n"
    "  flatpak install flathub com.obsproject.Studio"
)
_STEPS_OBS_CONFIG = "Launch OBS once so it creates its configuration folder."
_STEPS_WEBSOCKET = (
    "In OBS: Tools → WebSocket Server Settings → Enable WebSocket server, "
    "port 4455, Enable Authentication. Then run Build / Repair again."
)
_STEPS_SCENES = (
    'In OBS, create two scenes named exactly "Laptop" and "Tablet".\n'
    '"Laptop" = Screen Capture (PipeWire) of your laptop display.\n'
    '"Tablet" = Screen Capture (PipeWire) of the scrcpy window '
    '"Samsung Tablet".'
)
_STEPS_SCRCPY = (
    "Download the official scrcpy static release "
    "(github.com/Genymobile/scrcpy) and extract it into your home folder, "
    'then run it once with:  ./scrcpy --window-title="Samsung Tablet"'
)
_STEPS_ADB = "Install adb:  sudo apt install adb"
_STEPS_TABLET = (
    "Connect the tablet by USB. On the tablet enable Developer options "
    "(tap Build number 7×) → USB debugging, then accept the authorisation "
    "prompt."
)


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _flatpak_has_obs() -> bool:
    if not _which("flatpak"):
        return False
    try:
        return _run(["flatpak", "info", "com.obsproject.Studio"],
                    timeout=10).returncode == 0
    except Exception:
        return False


def _venv_ok() -> bool:
    p = _paths()
    if not p.venv_py.exists():
        return False
    try:
        return _run([str(p.venv_py), "-c", "import obsws_python"],
                    timeout=15).returncode == 0
    except Exception:
        return False


def _script_ok() -> tuple[bool, str]:
    p = _paths()
    if not p.script.is_file():
        return False, "not created yet"
    if not os.access(p.script, os.X_OK):
        return False, "not executable"
    body = p.script.read_text()
    if body.splitlines()[0] != f"#!{p.venv_py}":
        return False, "shebang does not point at the venv"
    if str(p.pw_file) not in body:
        return False, "does not reference the password file"
    return True, "ok"


def _password_ok() -> tuple[bool, str]:
    p = _paths()
    if not p.pw_file.is_file() or p.pw_file.stat().st_size == 0:
        return False, "missing"
    if stat.S_IMODE(p.pw_file.stat().st_mode) != 0o600:
        return False, "wrong permissions (should be 600)"
    obs_pw = read_obs_password()
    if obs_pw is not None and p.pw_file.read_text().strip() != obs_pw:
        return False, "does not match the password in OBS"
    return True, "ok"


def _scene_names_on_disk() -> set[str]:
    p = _paths()
    names: set[str] = set()
    if not p.scene_dir.is_dir():
        return names
    for f in p.scene_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        for src in data.get("sources", []):
            if src.get("id") == "scene" and isinstance(src.get("name"), str):
                names.add(src["name"])
    return names


def _websocket_enabled() -> tuple[bool, str]:
    p = _paths()
    if not p.obs_ws_config.is_file():
        return False, "OBS WebSocket config not found"
    try:
        data = json.loads(p.obs_ws_config.read_text())
    except Exception:
        return False, "OBS WebSocket config unreadable"
    if data.get("server_enabled") is not True:
        return False, "WebSocket server is disabled"
    if data.get("server_port") != WS_PORT:
        return False, f"WebSocket port is {data.get('server_port')}, not {WS_PORT}"
    return True, "enabled on port 4455"


def _dconf_shortcuts() -> dict[str, dict[str, str]]:
    """Return {path: {binding, command, name}} for current custom keybindings."""
    out: dict[str, dict[str, str]] = {}
    try:
        raw = _run(["gsettings", "get", MEDIA_KEYS_SCHEMA,
                    "custom-keybindings"], timeout=10).stdout.strip()
    except Exception:
        return out
    raw = raw.replace("@as ", "").strip()
    try:
        import ast
        paths = ast.literal_eval(raw) if raw and raw != "[]" else []
    except Exception:
        paths = []
    for path in paths:
        entry = {}
        for key in ("binding", "command", "name"):
            try:
                entry[key] = _run(
                    ["gsettings", "get",
                     f"{CUSTOM_KEYBINDING_SCHEMA}:{path}", key],
                    timeout=10,
                ).stdout.strip().strip("'\"")
            except Exception:
                entry[key] = ""
        out[path] = entry
    return out


def _shortcuts_ok() -> tuple[bool, str]:
    current = _dconf_shortcuts()
    have = {}
    for path, entry in current.items():
        cmd = entry.get("command", "").strip()
        for scene in SCENES:
            if cmd.endswith(f"obs-scene {scene}") and entry.get("binding"):
                have[scene] = entry["binding"]
    missing = [s for s in SCENES if s not in have]
    if missing:
        return False, f"missing shortcut(s) for: {', '.join(missing)}"
    wanted = {h[2]: h[0] for h in HOTKEYS}
    wrong = [s for s in SCENES if have[s] != wanted[s]]
    if wrong:
        return False, f"wrong key for: {', '.join(wrong)}"
    return True, "Ctrl+Alt+1 → Laptop, Ctrl+Alt+2 → Tablet"


def _tablet_connected() -> bool:
    if not _which("adb"):
        return False
    try:
        r = _run(["adb", "devices"], timeout=10)
        for line in r.stdout.splitlines()[1:]:
            s = line.strip()
            if s.endswith("\tdevice") or s.endswith(" device"):
                return True
        return False
    except Exception:
        return False


def _scrcpy_present() -> bool:
    if _which("scrcpy"):
        return True
    for d in _paths().home.glob("scrcpy-linux-x86_64*"):
        cand = d / "scrcpy"
        if cand.is_file() and os.access(cand, os.X_OK):
            return True
    return False


def list_backups() -> list[Path]:
    root = _paths().backup_root
    if not root.is_dir():
        return []
    return sorted(
        (d for d in root.iterdir()
         if d.is_dir() and (d / "manifest.json").is_file()),
        key=lambda d: d.name, reverse=True,
    )


def is_locked() -> bool:
    p = _paths()
    try:
        r = _run(["lsattr", str(p.script)], timeout=5)
        if r.returncode != 0 or not r.stdout.strip():
            return False
        return "i" in r.stdout.split()[0]
    except Exception:
        return False


def _newest_backup() -> Path | None:
    backups = list_backups()
    return backups[0] if backups else None


def _backup_age_text(path: Path) -> str:
    try:
        ts = datetime.strptime(path.name, "%Y%m%d_%H%M%S")
        delta = datetime.now() - ts
        if delta.days:
            return f"{delta.days} day(s) ago"
        hrs = delta.seconds // 3600
        if hrs:
            return f"{hrs} hour(s) ago"
        return f"{max(1, delta.seconds // 60)} minute(s) ago"
    except Exception:
        return path.name


def check_status() -> list[Check]:
    """Run every check. Never raises: a failing check becomes ok=False."""
    p = _paths()
    checks: list[Check] = []

    def add(key, label, fn, fixable, manual=""):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"check failed: {e}"
        checks.append(Check(key, label, ok, detail, fixable, manual))

    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    checks.append(Check(
        "session", "Session type", session == "wayland",
        f"{session} (Xorg also works — do not switch just for this)",
        fixable=False,
    ))
    add("obs_installed", "OBS installed",
        lambda: (_flatpak_has_obs() or _which("obs"),
                 "found" if (_flatpak_has_obs() or _which("obs"))
                 else "not found"),
        fixable=False, manual=_STEPS_OBS_INSTALL)
    add("obs_config", "OBS configuration folder",
        lambda: (p.obs_cfg_dir.is_dir(),
                 str(p.obs_cfg_dir) if p.obs_cfg_dir.is_dir() else "missing"),
        fixable=False, manual=_STEPS_OBS_CONFIG)
    add("websocket", "OBS WebSocket enabled", _websocket_enabled,
        fixable=True, manual=_STEPS_WEBSOCKET)
    add("password_file", "WebSocket password file", _password_ok, fixable=True)
    add("venv", "Python environment (obsws-python)",
        lambda: (_venv_ok(),
                 "ready" if _venv_ok()
                 else "missing or obsws-python not installed"),
        fixable=True)
    add("script", "Switch script (~/.local/bin/obs-scene)", _script_ok,
        fixable=True)
    add("shortcuts", "GNOME keyboard shortcuts", _shortcuts_ok, fixable=True)
    add("scenes", "OBS scenes 'Laptop' and 'Tablet'",
        lambda: (set(SCENES) <= _scene_names_on_disk(),
                 "both present" if set(SCENES) <= _scene_names_on_disk()
                 else f"found: {sorted(_scene_names_on_disk()) or 'none'}"),
        fixable=False, manual=_STEPS_SCENES)
    add("scrcpy", "scrcpy available",
        lambda: (_scrcpy_present(),
                 "found" if _scrcpy_present() else "not found"),
        fixable=False, manual=_STEPS_SCRCPY)
    add("adb", "adb installed",
        lambda: (_which("adb"), "found" if _which("adb") else "not found"),
        fixable=False, manual=_STEPS_ADB)
    checks.append(Check(
        "tablet", "Tablet connected", _tablet_connected(),
        "connected" if _tablet_connected()
        else "not detected (USB + debugging)",
        fixable=False, manual_steps=_STEPS_TABLET,
    ))
    checks.append(Check(
        "protection", "File protection", True,
        "Locked" if is_locked() else "Unlocked", fixable=False,
    ))
    nb = _newest_backup()
    checks.append(Check(
        "backup", "Last backup", True,
        _backup_age_text(nb) if nb else "none yet", fixable=False,
    ))
    return checks
