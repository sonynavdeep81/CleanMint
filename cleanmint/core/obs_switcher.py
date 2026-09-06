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
