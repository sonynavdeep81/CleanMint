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
V4L2_NR = 42
V4L2_DEVICE = f"/dev/video{V4L2_NR}"
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
    needs_unlock: bool = False

    @property
    def ok(self) -> bool:
        return (all(s.ok for s in self.steps)
                and not self.needs_password
                and not self.needs_unlock)


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
    # Flatpak OBS runs as a bare "obs" process inside bwrap — its command line
    # never contains "com.obsproject.Studio", so match the process name too.
    for cmd in (["pgrep", "-x", "obs"],
                ["pgrep", "-f", "com.obsproject.Studio"],
                ["pgrep", "-f", "obs-studio"]):
        try:
            r = _run(cmd, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return True
        except Exception:
            pass
    return False


def websocket_reachable(timeout: float = 1.0, port: int = WS_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
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


_BACKUP_TS_FMT = "%Y%m%d_%H%M%S_%f"


def _backup_age_text(path: Path) -> str:
    try:
        ts = datetime.strptime(path.name, _BACKUP_TS_FMT)
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
    feed_ok = v4l2_ready() and scrcpy_v4l2_running()
    checks.append(Check(
        "tablet_feed", "Tablet live feed",
        feed_ok,
        "streaming to OBS" if feed_ok
        else ("virtual camera loaded, feed stopped" if v4l2_ready()
              else "not set up"),
        fixable=False,
        manual_steps='Click "Set Up Scenes" to load the virtual camera and '
                     "start streaming the tablet into OBS.",
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


# ── Backup / verify / restore ──────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _dconf_dump() -> str:
    try:
        return _run(["dconf", "dump", DCONF_MEDIA_KEYS], timeout=10).stdout
    except Exception:
        return ""


def backup(progress_cb=None) -> Path:
    """Snapshot the automation layer + OBS config into a timestamped folder."""
    def _prog(msg):
        if progress_cb:
            progress_cb(msg, 0)

    p = _paths()
    ts = datetime.now().strftime(_BACKUP_TS_FMT)
    dest = p.backup_root / ts
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)

    sha: dict[str, str] = {}

    _prog("Copying OBS configuration…")
    if p.obs_cfg_dir.is_dir():
        shutil.copytree(p.obs_cfg_dir, dest / "obs-studio", dirs_exist_ok=True)

    _prog("Saving GNOME shortcuts…")
    (dest / "obs-gnome-shortcuts.dconf").write_text(_dconf_dump())

    _prog("Copying script and password…")
    if p.script.is_file():
        shutil.copy2(p.script, dest / "obs-scene")
        sha["obs-scene"] = _sha256(p.script)
    if p.pw_file.is_file():
        shutil.copy2(p.pw_file, dest / "password")
        os.chmod(dest / "password", 0o600)
        sha["password"] = _sha256(p.pw_file)

    manifest = {
        "created_at": datetime.now().isoformat(),
        "obs_running": obs_running(),
        "scene_names": sorted(_scene_names_on_disk()),
        "sha256": sha,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))

    _prog("Pruning old backups…")
    for old in list_backups()[MAX_BACKUPS:]:
        shutil.rmtree(old, ignore_errors=True)

    return dest


def verify(against: Path | None = None) -> list[VerifyItem]:
    p = _paths()
    backup_dir = against or _newest_backup()
    if backup_dir is None:
        return []
    manifest = json.loads((backup_dir / "manifest.json").read_text())
    sha = manifest.get("sha256", {})
    items: list[VerifyItem] = []

    def _verify_file(label, live: Path, key: str):
        has_src = (backup_dir / key).is_file()
        if not live.is_file():
            return VerifyItem(label, "missing", has_src)
        if key in sha and _sha256(live) == sha[key]:
            return VerifyItem(label, "ok", has_src)
        return VerifyItem(label, "changed", has_src)

    items.append(_verify_file("obs-scene script", p.script, "obs-scene"))
    items.append(_verify_file("WebSocket password", p.pw_file, "password"))

    cur = _dconf_dump().strip()
    bak = (backup_dir / "obs-gnome-shortcuts.dconf").read_text().strip()
    if not cur:
        status = "missing"
    elif cur == bak:
        status = "ok"
    else:
        status = "changed"
    items.append(VerifyItem("GNOME shortcuts", status, bool(bak)))

    on_disk = _scene_names_on_disk()
    for scene in SCENES:
        items.append(VerifyItem(
            f'OBS scene "{scene}"',
            "ok" if scene in on_disk else "missing",
            (backup_dir / "obs-studio").is_dir(),
        ))

    items.append(VerifyItem(
        "OBS config folder",
        "ok" if p.obs_cfg_dir.is_dir() else "missing",
        (backup_dir / "obs-studio").is_dir(),
    ))
    return items


def restore(labels: list[str], against: Path | None = None) -> list[StepResult]:
    p = _paths()
    backup_dir = against or _newest_backup()
    results: list[StepResult] = []
    if backup_dir is None:
        return [StepResult("restore", False, "no backup available")]

    for label in labels:
        try:
            if label == "obs-scene script":
                shutil.copy2(backup_dir / "obs-scene", p.script)
                os.chmod(p.script, 0o755)
                results.append(StepResult(label, True, "restored"))
            elif label == "WebSocket password":
                p.pw_dir.mkdir(parents=True, exist_ok=True)
                os.chmod(p.pw_dir, 0o700)
                shutil.copy2(backup_dir / "password", p.pw_file)
                os.chmod(p.pw_file, 0o600)
                results.append(StepResult(label, True, "restored"))
            elif label == "GNOME shortcuts":
                _write_shortcuts()
                results.append(StepResult(label, True, "shortcuts re-applied"))
            elif label in ('OBS scene "Laptop"', 'OBS scene "Tablet"',
                           "OBS config folder"):
                if obs_running() or websocket_reachable():
                    results.append(StepResult(
                        label, False, "close OBS first, then restore"))
                else:
                    shutil.copytree(backup_dir / "obs-studio", p.obs_cfg_dir,
                                    dirs_exist_ok=True)
                    results.append(StepResult(
                        label, True, "OBS config restored"))
            else:
                results.append(StepResult(label, False, "unknown item"))
        except Exception as e:  # noqa: BLE001
            results.append(StepResult(label, False, str(e)))
    return results


# ── GNOME shortcut writer ──────────────────────────────────────────────────

def _write_shortcuts() -> None:
    """Create/repair the two OBS shortcuts without touching other bindings."""
    p = _paths()
    current = _dconf_shortcuts()
    existing_paths = list(current.keys())
    existing_cmds = {path: current[path].get("command", "")
                     for path in existing_paths}

    plan, final_list = plan_shortcut_slots(
        existing_paths, existing_cmds, str(p.script))

    _run(["gsettings", "set", MEDIA_KEYS_SCHEMA, "custom-keybindings",
          str(final_list)], timeout=10)

    for path, accel, name, cmd in plan:
        base = f"{CUSTOM_KEYBINDING_SCHEMA}:{path}"
        _run(["gsettings", "set", base, "name", f"'{name}'"], timeout=10)
        _run(["gsettings", "set", base, "command", f"'{cmd}'"], timeout=10)
        _run(["gsettings", "set", base, "binding", f"'{accel}'"], timeout=10)


# ── venv builder (separate so tests can stub it) ───────────────────────────

def _venv_build() -> None:
    p = _paths()
    _run(["python3", "-m", "venv", str(p.venv_dir)], timeout=120)
    _run([str(p.venv_dir / "bin" / "pip"), "install", "--quiet",
          "--upgrade", "obsws-python"], timeout=300)


# ── build ─────────────────────────────────────────────────────────────────

def build(progress_cb=None) -> BuildResult:
    """Run every auto-fixable setup step, idempotently."""
    p = _paths()
    result = BuildResult()

    # The script + password live behind chattr +i when protection is on;
    # writing to them would fail. Surface this instead of half-failing.
    if is_locked():
        result.needs_unlock = True
        result.steps.append(StepResult(
            "File protection", False,
            "the switch script and password are protected — unprotect them "
            "first (this page can do it, then re-protect afterwards)."))
        return result

    def step(label, pct, fn):
        if progress_cb:
            progress_cb(label, pct)
        try:
            detail = fn() or "done"
            result.steps.append(StepResult(label, True, detail))
        except NeedsPasswordError:
            result.needs_password = True
            result.steps.append(
                StepResult(label, False, "no password available"))
        except Exception as e:  # noqa: BLE001
            result.steps.append(StepResult(label, False, str(e)))

    def _do_venv():
        if _venv_ok():
            return "already present"
        _venv_build()
        if not _venv_ok():
            raise RuntimeError("venv created but obsws-python import failed")
        return "created + obsws-python installed"

    def _do_pwdir():
        p.pw_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(p.pw_dir, 0o700)
        return str(p.pw_dir)

    def _do_password():
        pw = read_obs_password()
        if pw:
            set_password(pw)
            return "copied from OBS config"
        ok, _ = _password_ok()
        if ok:
            return "kept existing password file"
        raise NeedsPasswordError()

    def _do_script():
        _write_script()
        return str(p.script)

    def _do_websocket():
        # Already on? Nothing to do — this is the normal case once set up.
        enabled, _ = _websocket_enabled()
        if enabled or websocket_reachable():
            return "already enabled on port 4455"
        # Disabled. We can only safely patch the config file while OBS is
        # closed — OBS rewrites config.json from memory on exit, so a change
        # made while it runs would be lost.
        if obs_running():
            result.warnings.append(
                "OBS's WebSocket server is off. Turn it on in OBS: "
                "Tools → WebSocket Server Settings → tick “Enable WebSocket "
                "server” (port 4455, Enable Authentication). CleanMint can't "
                "change this while OBS is running.")
            return "needs OBS (turn it on in OBS's Tools menu)"
        if not p.obs_ws_config.is_file():
            result.warnings.append(
                "OBS WebSocket config not found — open OBS once, then run "
                "Build again to enable the WebSocket server.")
            return "skipped (OBS not set up yet)"
        backup()  # pre-flight
        data = json.loads(p.obs_ws_config.read_text())
        data["server_enabled"] = True
        data["server_port"] = WS_PORT
        data["auth_required"] = True
        p.obs_ws_config.write_text(json.dumps(data, indent=2))
        return "enabled on port 4455"

    def _do_shortcuts():
        _write_shortcuts()
        return "Ctrl+Alt+1 → Laptop, Ctrl+Alt+2 → Tablet"

    step("Python environment", 10, _do_venv)
    step("Password folder", 25, _do_pwdir)
    step("WebSocket password", 40, _do_password)
    step("Switch script", 55, _do_script)
    step("OBS WebSocket", 70, _do_websocket)
    step("GNOME shortcuts", 85, _do_shortcuts)
    step("Backup", 100, lambda: str(backup()))
    return result


# ── Lock / unlock ─────────────────────────────────────────────────────────

def _pkexec_helper(op: str) -> tuple[bool, str]:
    if not HELPER.exists():
        return False, ("CleanMint helper not installed — relaunch CleanMint "
                       "and accept the helper install prompt.")
    try:
        r = _run(["pkexec", str(HELPER), op], timeout=60)
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    if r.returncode == 0:
        return True, ""
    return False, (r.stderr.strip() or r.stdout.strip()
                   or f"pkexec exited {r.returncode}")


def lock() -> tuple[bool, str]:
    return _pkexec_helper("obs-lock")


def unlock() -> tuple[bool, str]:
    return _pkexec_helper("obs-unlock")


# ── Self-test ─────────────────────────────────────────────────────────────

_QUERY_SNIPPET = (
    "import obsws_python as obs;"
    "c=obs.ReqClient(host='localhost',port=4455,"
    "password=open({pw!r}).read().strip(),timeout=3);"
    "r=c.get_current_program_scene();"
    "print(getattr(r,'current_program_scene_name',None) "
    "or getattr(r,'scene_name',''))"
)


def _query_scene() -> str | None:
    """Return OBS's current program scene name, via the obs-hotkey venv."""
    p = _paths()
    try:
        r = _run([str(p.venv_py), "-c",
                  _QUERY_SNIPPET.format(pw=str(p.pw_file))], timeout=10)
        name = r.stdout.strip()
        return name or None
    except Exception:
        return None


def _obs_py(body: str, timeout: int = 60) -> tuple[dict | None, str]:
    """Run `body` in the obs-hotkey venv with a connected ReqClient `c`.

    `body` must end by printing one line of JSON. Returns (parsed, "") or
    (None, error).
    """
    p = _paths()
    preamble = (
        "import json, obsws_python as obs\n"
        f"c = obs.ReqClient(host='localhost', port=4455, "
        f"password=open({str(p.pw_file)!r}).read().strip(), timeout=6)\n"
    )
    try:
        r = _run([str(p.venv_py), "-c", preamble + body], timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    if r.returncode != 0:
        return None, (r.stderr.strip().splitlines()[-1]
                      if r.stderr.strip() else "OBS call failed")
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]), ""
    except Exception as e:  # noqa: BLE001
        return None, f"unexpected response ({e})"


def _setup_create_snippet() -> str:
    """OBS-side scene/source creation. Laptop = PipeWire screen capture (its
    restore token is stable); Tablet = v4l2 capture of the scrcpy feed
    (/dev/videoN — no Wayland portal, survives restarts)."""
    dev = V4L2_DEVICE
    return (
        "res = {'created_scenes': [], 'created_sources': [], 'existing': [], "
        "'replaced': [], 'errors': []}\n"
        "scenes = [s['sceneName'] for s in c.get_scene_list().scenes]\n"
        "for sc in ('Laptop', 'Tablet'):\n"
        "    if sc not in scenes:\n"
        "        try:\n"
        "            c.create_scene(sc); res['created_scenes'].append(sc)\n"
        "        except Exception as e:\n"
        "            res['errors'].append('scene %s: %s' % (sc, e))\n"
        "try:\n"
        "    items = c.get_scene_item_list('Laptop').scene_items\n"
        "    if items:\n"
        "        res['existing'].append('Laptop: ' + items[0]['sourceName'])\n"
        "    else:\n"
        "        c.create_input('Laptop', 'HP Laptop', "
        "'pipewire-screen-capture-source', {}, True)\n"
        "        res['created_sources'].append('Laptop / HP Laptop')\n"
        "except Exception as e:\n"
        "    res['errors'].append('Laptop source: %s' % e)\n"
        "try:\n"
        "    for i in c.get_scene_item_list('Tablet').scene_items:\n"
        "        try:\n"
        "            c.remove_input(i['sourceName'])\n"
        "            if i['sourceName'] != 'Samsung Tablet':\n"
        "                res['replaced'].append(i['sourceName'])\n"
        "        except Exception:\n"
        "            pass\n"
        f"    c.create_input('Tablet', 'Samsung Tablet', 'v4l2_input', "
        f"{{'device_id': '{dev}'}}, True)\n"
        "    res['created_sources'].append('Tablet / Samsung Tablet')\n"
        "except Exception as e:\n"
        "    res['errors'].append('Tablet source: %s' % e)\n"
        "# Fit every source to the canvas (no stripes / letterboxing).\n"
        "try:\n"
        "    v = c.get_video_settings()\n"
        "    W, H = float(v.base_width), float(v.base_height)\n"
        "    for sc in ('Laptop', 'Tablet'):\n"
        "        try:\n"
        "            for it in c.get_scene_item_list(sc).scene_items:\n"
        "                c.set_scene_item_transform(sc, it['sceneItemId'], {\n"
        "                    'boundsType': 'OBS_BOUNDS_SCALE_INNER',\n"
        "                    'boundsWidth': W, 'boundsHeight': H,\n"
        "                    'boundsAlignment': 0,\n"
        "                    'positionX': 0.0, 'positionY': 0.0,\n"
        "                    'alignment': 5,\n"
        "                })\n"
        "            res.setdefault('fitted', []).append(sc)\n"
        "        except Exception as e:\n"
        "            res['errors'].append('fit %s: %s' % (sc, e))\n"
        "except Exception as e:\n"
        "    res['errors'].append('fit: %s' % e)\n"
        "print(json.dumps(res))\n"
    )


def _scrcpy_binary() -> str | None:
    w = shutil.which("scrcpy")
    if w:
        return w
    for d in sorted(_paths().home.glob("scrcpy-linux-x86_64*"), reverse=True):
        cand = d / "scrcpy"
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def v4l2_ready() -> bool:
    """True if the CleanMint v4l2loopback device exists and we can read it."""
    try:
        st = os.stat(V4L2_DEVICE)
        return stat.S_ISCHR(st.st_mode) and os.access(V4L2_DEVICE, os.R_OK)
    except OSError:
        return False


_V4L2_PERSIST = Path("/etc/modules-load.d/cleanmint-v4l2.conf")


def _ensure_v4l2() -> tuple[bool, str]:
    """Load the v4l2loopback module + make it boot-persistent (pkexec helper).

    Skips the prompt only when the device is already present AND the
    boot-persistence file is in place.
    """
    import time
    if v4l2_ready() and _V4L2_PERSIST.exists():
        return True, f"virtual camera ready ({V4L2_DEVICE})"
    ok, err = _pkexec_helper("obs-v4l2")
    if not ok:
        return False, err or "could not load v4l2loopback"
    for _ in range(10):
        time.sleep(0.4)
        if v4l2_ready():
            return True, f"virtual camera ready ({V4L2_DEVICE})"
    return False, f"{V4L2_DEVICE} did not appear after loading v4l2loopback"


def scrcpy_v4l2_running() -> bool:
    try:
        r = _run(["pgrep", "-af", "scrcpy"], timeout=5)
        return r.returncode == 0 and "v4l2-sink" in r.stdout
    except Exception:
        return False


def _scrcpy_log() -> Path:
    d = _paths().home / ".cache" / "cleanmint"
    d.mkdir(parents=True, exist_ok=True)
    return d / "obs-scrcpy.log"


# scrcpy can only write to the v4l2 device if nothing else has it open. OBS
# holds it via the "Samsung Tablet" input, so that input must be removed first.
_RELEASE_V4L2_SNIPPET = (
    f"DEV = {V4L2_DEVICE!r}\n"
    "released = []\n"
    "for i in c.get_input_list().inputs:\n"
    "    if i['inputKind'] != 'v4l2_input':\n"
    "        continue\n"
    "    try:\n"
    "        s = c.get_input_settings(i['inputName']).input_settings\n"
    "    except Exception:\n"
    "        s = {}\n"
    "    if s.get('device_id') == DEV:\n"
    "        try:\n"
    "            c.remove_input(i['inputName']); released.append(i['inputName'])\n"
    "        except Exception:\n"
    "            pass\n"
    "print(json.dumps({'released': released}))\n"
)

_SCRCPY_ERR_MARKERS = (
    "Failed to write header", "Could not send frame to v4l2 sink",
    "Could not send packet to v4l2 sink", "ioctl(VIDIOC",
)


def _start_scrcpy_v4l2() -> tuple[bool, str]:
    """(Re)start scrcpy feeding the tablet screen into the v4l2 device.

    Verifies the feed actually connected — a scrcpy that starts but cannot
    write to the device (because something still holds it) is reported as a
    failure, not a success.
    """
    import time
    binary = _scrcpy_binary()
    if not binary:
        return False, ("scrcpy not found — download the official static "
                       "release and extract it into your home folder")
    if not _tablet_connected():
        return False, ("no tablet detected — connect it by USB and authorise "
                       "USB debugging")
    try:
        _run(["pkill", "-f", "scrcpy.*v4l2-sink"], timeout=5)
        time.sleep(0.6)
    except Exception:
        pass

    log = _scrcpy_log()
    try:
        fh = open(log, "wb")
        proc = subprocess.Popen(
            [binary, f"--v4l2-sink={V4L2_DEVICE}", "--no-audio", "--no-window",
             "--stay-awake"],
            stdout=fh, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except Exception as e:  # noqa: BLE001
        return False, str(e)

    time.sleep(4.0)

    if proc.poll() is not None:
        return False, "scrcpy stopped right after starting"
    try:
        text = log.read_text(errors="replace")
    except OSError:
        text = ""
    if any(m in text for m in _SCRCPY_ERR_MARKERS):
        try:
            proc.terminate()
        except Exception:
            pass
        return False, ("could not write to the virtual camera — something "
                       "still has it open. Close the “Samsung Tablet” source "
                       "in OBS and run Set Up Scenes again.")
    return True, f"tablet screen → {V4L2_DEVICE}"


def setup_scenes(progress_cb=None) -> list[StepResult]:
    """Create the Laptop/Tablet scenes and their capture sources in OBS.

    Needs OBS running with the WebSocket server enabled. Backs up the OBS
    config first and leaves already-working sources untouched.

    - Laptop: PipeWire screen capture (needs one portal pick the first time;
      its restore token then persists).
    - Tablet: scrcpy → v4l2loopback → a plain Video Capture Device source.
      Fully automatic, no portal, survives restarts.
    """
    import time

    def prog(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    if not websocket_reachable():
        return [StepResult(
            "Precondition", False,
            "Start OBS first, with its WebSocket server enabled on port 4455.")]

    steps: list[StepResult] = []

    prog("Backing up OBS configuration…", 8)
    try:
        backup()
        steps.append(StepResult("Backup", True, "OBS config backed up"))
    except Exception as e:  # noqa: BLE001
        steps.append(StepResult("Backup", False, str(e)))

    prog("Preparing the virtual camera…", 18)
    v_ok, v_msg = _ensure_v4l2()
    steps.append(StepResult("Virtual camera", v_ok, v_msg))

    if v_ok:
        # Free the device in OBS first, otherwise scrcpy cannot write to it.
        prog("Freeing the virtual camera…", 32)
        _obs_py(_RELEASE_V4L2_SNIPPET)
        time.sleep(0.5)
        prog("Starting the tablet feed…", 44)
        s_ok, s_msg = _start_scrcpy_v4l2()
        steps.append(StepResult("Tablet feed (scrcpy)", s_ok, s_msg))

    prog("Creating scenes and sources in OBS…", 80)
    res, err = _obs_py(_setup_create_snippet())
    if res is None:
        steps.append(StepResult("Create scenes", False, err))
        return steps

    for s in res.get("created_scenes", []):
        steps.append(StepResult(f"Scene “{s}”", True, "created"))
    for s in res.get("replaced", []):
        steps.append(StepResult(f"Replaced old source “{s}”", True,
                                "swapped for the v4l2 capture"))
    for s in res.get("created_sources", []):
        steps.append(StepResult(f"Source {s}", True, "created"))
    for s in res.get("existing", []):
        steps.append(StepResult(f"Already set up — {s}", True, "left as-is"))
    if res.get("fitted"):
        steps.append(StepResult(
            "Fit to screen", True,
            "scaled " + " + ".join(res["fitted"]) + " to fill the canvas"))
    for e in res.get("errors", []):
        steps.append(StepResult("OBS", False, e))

    if any("Laptop / HP Laptop" in s for s in res.get("created_sources", [])):
        steps.append(StepResult(
            "Finish the Laptop scene", True,
            "OBS is showing a screen-share dialog for “HP Laptop” — pick your "
            "laptop display once (it is remembered after that)."))

    prog("Done.", 100)
    return steps


def self_test(progress_cb=None) -> list[StepResult]:
    p = _paths()

    def _prog(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    if not websocket_reachable():
        return [StepResult(
            "Precondition", False,
            "Start OBS first, and make sure its WebSocket server is enabled "
            "(Tools → WebSocket Server Settings) on port 4455.")]

    steps: list[StepResult] = []

    _prog("Reading current scene…", 10)
    original = _query_scene()
    steps.append(StepResult("Connect to OBS", original is not None,
                            f"current scene: {original or 'unknown'}"))

    for pct, scene in ((40, "Laptop"), (70, "Tablet")):
        _prog(f"Switching to {scene}…", pct)
        try:
            _run([str(p.script), scene], timeout=10)
        except Exception as e:  # noqa: BLE001
            steps.append(StepResult(f"Switch to {scene}", False, str(e)))
            continue
        now = _query_scene()
        steps.append(StepResult(
            f"Switch to {scene}", now == scene,
            "ok" if now == scene else f"OBS is on '{now}', expected '{scene}'"))

    if original:
        _prog("Restoring original scene…", 95)
        try:
            _run([str(p.script), original], timeout=10)
            steps.append(StepResult(f"Restore '{original}'",
                                    _query_scene() == original, "ok"))
        except Exception as e:  # noqa: BLE001
            steps.append(StepResult(f"Restore '{original}'", False, str(e)))

    _prog("Done.", 100)
    return steps
