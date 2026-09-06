# OBS Switcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CleanMint page that builds, protects, backs up, and tests the laptop⇄Samsung-tablet OBS scene-switching setup from `obs.md`.

**Architecture:** One UI-free engine module `core/obs_switcher.py` (status checks, `build()`, lock/unlock via the existing pkexec helper, backup/verify/restore, `self_test()`), one `ui/obs_switcher_page.py` page following the `snapshot_page.py` pattern (QThread workers, `Theme` styling), a new `obs-lock`/`obs-unlock` operation in `assets/cleanmint-helper`, and a sidebar entry. The engine never edits OBS scene contents; it only manages the automation layer (script, password, venv, GNOME shortcuts, WebSocket toggle).

**Tech Stack:** Python 3.12, PyQt6, `subprocess` (list-form only), `json`, `hashlib`, `shutil`, `chattr`/`lsattr`, `gsettings`/`dconf`, `pgrep`, `pkexec`.

**Spec:** `docs/superpowers/specs/2026-09-06-obs-switcher-design.md`

**Test convention:** This repo's tests are plain scripts (not pytest) under `tests/`, each with a `PASS`/`FAIL` counter that `sys.exit(1)` on any failure and `sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))`. Run a suite with `venv/bin/python tests/test_<name>.py`. New tests follow that style.

---

## File Structure

| File | Responsibility |
|---|---|
| `cleanmint/core/obs_switcher.py` | All engine logic, UI-free. Paths, dataclasses, status checks, `build()`, `set_password()`, `lock()/unlock()/is_locked()`, `backup()/list_backups()/verify()/restore()`, `self_test()`, pure helpers `render_script()` / `plan_shortcut_slots()`. |
| `cleanmint/ui/obs_switcher_page.py` | The page + one generic `Worker(QThread)` + `RestoreDialog` + `TestReportDialog`. No business logic. |
| `cleanmint/assets/cleanmint-helper` | Add `obs-lock` / `obs-unlock` case (chattr +i/-i on a fixed path list derived from `PKEXEC_UID`'s home). |
| `cleanmint/ui/main_window.py` | One `NAV_ITEMS` entry + one `_create_page` branch. |
| `CLAUDE.md` | Document the feature and the two new helper operations. |
| `tests/test_obs_switcher.py` | Offline engine tests (sandboxed `HOME`, mocked OBS). |
| `tests/test_ui_imports.py` | Add `ui.obs_switcher_page` import check. |
| `STATE.md` | Update resume pointer at the end. |

---

## Task 1: Engine scaffold — paths, dataclasses, pure helpers

**Files:**
- Create: `cleanmint/core/obs_switcher.py`
- Test: `tests/test_obs_switcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_obs_switcher.py`:

```python
"""
tests/test_obs_switcher.py — OBS Switcher engine tests (offline, sandboxed HOME).

No real OBS. HOME is redirected to a tmpdir; obs_switcher reads paths at call
time so the redirect takes effect.
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "cleanmint"))

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    suffix = f" ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    results.append(bool(condition))


def new_home():
    """Fresh sandbox HOME; point every home-derived lookup at it."""
    d = Path(tempfile.mkdtemp(prefix="obsw_"))
    os.environ["HOME"] = str(d)
    return d


import core.obs_switcher as ob  # noqa: E402


print("\n=== OBS Switcher — pure helpers ===\n")

home = new_home()
p = ob._paths()
check("_paths().script under HOME", str(p.script).startswith(str(home)),
      str(p.script))
check("_paths().pw_file is ~/.config/obs-hotkeys/password",
      p.pw_file == home / ".config/obs-hotkeys/password")

src = ob.render_script("/x/venv/bin/python3", "/x/pw")
check("render_script shebang", src.splitlines()[0] == "#!/x/venv/bin/python3")
check("render_script embeds pw path", '"/x/pw"' in src)
check("render_script calls set_current_program_scene",
      "set_current_program_scene(scene)" in src)

print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.obs_switcher'`

- [ ] **Step 3: Write minimal implementation**

Create `cleanmint/core/obs_switcher.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS — `5/5 checks passed`

- [ ] **Step 5: Commit**

```bash
git add cleanmint/core/obs_switcher.py tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): engine scaffold — paths, types, render_script"
```

---

## Task 2: `plan_shortcut_slots()` — no-clobber GNOME shortcut planner

**Files:**
- Modify: `cleanmint/core/obs_switcher.py` (add function after `render_script`)
- Test: `tests/test_obs_switcher.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_switcher.py` before the final summary block:

```python
print("\n=== OBS Switcher — plan_shortcut_slots ===\n")

_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
SCRIPT = "/home/u/.local/bin/obs-scene"

# custom0 = user's unrelated shortcut, custom1 = an existing obs Tablet binding
existing = [_BASE + "custom0/", _BASE + "custom1/"]
existing_cmds = {
    _BASE + "custom0/": "'/usr/bin/flameshot gui'",
    _BASE + "custom1/": f"'{SCRIPT} Tablet'",
}
plan, final_list = ob.plan_shortcut_slots(existing, existing_cmds, SCRIPT)

by_scene = {entry[3].split()[-1]: entry for entry in plan}
check("reuses custom1 for Tablet",
      by_scene["Tablet"][0] == _BASE + "custom1/")
check("allocates a fresh slot for Laptop (not custom0/custom1)",
      by_scene["Laptop"][0] not in (_BASE + "custom0/", _BASE + "custom1/"))
check("keeps user's custom0 in the final list",
      _BASE + "custom0/" in final_list)
check("final list contains both obs slots",
      by_scene["Laptop"][0] in final_list and by_scene["Tablet"][0] in final_list)
check("plan carries correct accelerator for Laptop",
      by_scene["Laptop"][1] == "<Control><Alt>1")
check("plan command is absolute script + scene",
      by_scene["Laptop"][3] == f"{SCRIPT} Laptop")

# empty starting point
plan2, final2 = ob.plan_shortcut_slots([], {}, SCRIPT)
check("from empty: two slots allocated", len(plan2) == 2 and len(final2) == 2)
check("from empty: slots are custom0 and custom1",
      sorted(final2) == [_BASE + "custom0/", _BASE + "custom1/"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `AttributeError: module 'core.obs_switcher' has no attribute 'plan_shortcut_slots'`

- [ ] **Step 3: Write minimal implementation**

Add to `cleanmint/core/obs_switcher.py` after `render_script`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS — all checks pass

- [ ] **Step 5: Commit**

```bash
git add cleanmint/core/obs_switcher.py tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): plan_shortcut_slots no-clobber planner"
```

---

## Task 3: Environment probes — `obs_running`, `websocket_reachable`, `read_obs_password`, `_run`

**Files:**
- Modify: `cleanmint/core/obs_switcher.py`
- Test: `tests/test_obs_switcher.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_switcher.py`:

```python
print("\n=== OBS Switcher — environment probes ===\n")

home = new_home()
p = ob._paths()

# read_obs_password: no config yet
check("read_obs_password None when config missing", ob.read_obs_password() is None)

# create a fake OBS websocket config
p.obs_ws_config.parent.mkdir(parents=True)
p.obs_ws_config.write_text(json.dumps({
    "server_enabled": True, "server_port": 4455,
    "auth_required": True, "server_password": "s3cr3t",
}))
check("read_obs_password reads server_password", ob.read_obs_password() == "s3cr3t")

# malformed config -> None, no raise
p.obs_ws_config.write_text("{ not json")
check("read_obs_password None on bad json", ob.read_obs_password() is None)

# websocket_reachable: nothing listening on 4455 in CI
check("websocket_reachable False when nothing listens",
      ob.websocket_reachable(timeout=0.2) is False)

# obs_running returns a bool and does not raise
check("obs_running returns bool", isinstance(ob.obs_running(), bool))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `AttributeError: ... has no attribute 'read_obs_password'`

- [ ] **Step 3: Write minimal implementation**

Add to `cleanmint/core/obs_switcher.py` after `plan_shortcut_slots`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cleanmint/core/obs_switcher.py tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): environment probes"
```

---

## Task 4: `set_password()` and the script writer

**Files:**
- Modify: `cleanmint/core/obs_switcher.py`
- Test: `tests/test_obs_switcher.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_switcher.py`:

```python
print("\n=== OBS Switcher — set_password / _write_script ===\n")

home = new_home()
p = ob._paths()

ob.set_password("hunter2")
check("password file created", p.pw_file.is_file())
check("password file mode 600",
      stat.S_IMODE(p.pw_file.stat().st_mode) == 0o600)
check("password dir mode 700",
      stat.S_IMODE(p.pw_dir.stat().st_mode) == 0o700)
check("password content", p.pw_file.read_text() == "hunter2")

ob._write_script()
check("script created", p.script.is_file())
check("script executable", os.access(p.script, os.X_OK))
check("script shebang points at venv py",
      p.script.read_text().splitlines()[0] == f"#!{p.venv_py}")
check("script embeds pw file path", f'"{p.pw_file}"' in p.script.read_text())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `AttributeError: ... has no attribute 'set_password'`

- [ ] **Step 3: Write minimal implementation**

Add to `cleanmint/core/obs_switcher.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cleanmint/core/obs_switcher.py tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): set_password + script writer"
```

---

## Task 5: `check_status()`

**Files:**
- Modify: `cleanmint/core/obs_switcher.py`
- Test: `tests/test_obs_switcher.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_switcher.py`:

```python
print("\n=== OBS Switcher — check_status ===\n")

home = new_home()
checks = ob.check_status()
keys = {c.key for c in checks}
for expected in ("obs_installed", "websocket", "password_file", "venv",
                 "script", "shortcuts", "scenes", "scrcpy", "adb",
                 "protection", "backup"):
    check(f"check present: {expected}", expected in keys)

check("nothing raised; all are Check", all(isinstance(c, ob.Check) for c in checks))
byk = {c.key: c for c in checks}
check("empty sandbox: password_file not ok", byk["password_file"].ok is False)
check("empty sandbox: script not ok", byk["script"].ok is False)
check("password_file is fixable", byk["password_file"].fixable is True)
check("scenes check is not fixable", byk["scenes"].fixable is False)
check("non-fixable scenes has manual_steps text",
      len(byk["scenes"].manual_steps) > 0)

# after writing a good password + script, those flip to ok
p = ob._paths()
p.obs_ws_config.parent.mkdir(parents=True)
p.obs_ws_config.write_text(json.dumps({"server_password": "x",
                                       "server_enabled": True,
                                       "server_port": 4455}))
ob.set_password("x")
byk2 = {c.key: c for c in ob.check_status()}
check("password_file ok once written & matching", byk2["password_file"].ok is True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `AttributeError: ... has no attribute 'check_status'`

- [ ] **Step 3: Write minimal implementation**

Add to `cleanmint/core/obs_switcher.py`:

```python
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
    p = _paths()
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


def _newest_backup():
    backups = list_backups()
    return backups[0] if backups else None


def _tablet_connected() -> bool:
    if not _which("adb"):
        return False
    try:
        r = _run(["adb", "devices"], timeout=10)
        for line in r.stdout.splitlines()[1:]:
            if line.strip().endswith("\tdevice") or line.strip().endswith(" device"):
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
                 "ready" if _venv_ok() else "missing or obsws-python not installed"),
        fixable=True)
    add("script", "Switch script (~/.local/bin/obs-scene)", _script_ok,
        fixable=True)
    add("shortcuts", "GNOME keyboard shortcuts", _shortcuts_ok, fixable=True)
    add("scenes", "OBS scenes 'Laptop' and 'Tablet'",
        lambda: (SCENES[0] in _scene_names_on_disk()
                 and SCENES[1] in _scene_names_on_disk(),
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
        "connected" if _tablet_connected() else "not detected (USB + debugging)",
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
```

Note: `check_status` references `is_locked`, `list_backups` — defined in Tasks 6 and 7. That is fine at import time (they are module-level names resolved at call time), but Task 5's test calls `check_status()`, which calls `is_locked()` and `list_backups()`. **Add minimal stubs now** so Task 5's test passes, and replace them with real implementations in Tasks 6/7:

```python
def list_backups() -> list[Path]:
    root = _paths().backup_root
    if not root.is_dir():
        return []
    return sorted(
        (d for d in root.iterdir() if d.is_dir() and (d / "manifest.json").is_file()),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cleanmint/core/obs_switcher.py tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): check_status + list_backups + is_locked"
```

---

## Task 6: `backup()` / `verify()` / `restore()`

**Files:**
- Modify: `cleanmint/core/obs_switcher.py`
- Test: `tests/test_obs_switcher.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_switcher.py`:

```python
print("\n=== OBS Switcher — backup / verify / restore ===\n")

home = new_home()
p = ob._paths()
# minimal setup: obs config dir + script + password
p.obs_cfg_dir.mkdir(parents=True)
(p.obs_cfg_dir / "global.ini").write_text("[General]\n")
ob.set_password("pw-A")
ob._write_script()

b1 = ob.backup()
check("backup dir created", b1.is_dir())
check("manifest written", (b1 / "manifest.json").is_file())
man = json.loads((b1 / "manifest.json").read_text())
check("manifest has sha256 for obs-scene", "obs-scene" in man["sha256"])
check("manifest has sha256 for password", "password" in man["sha256"])
check("obs-studio copied into backup", (b1 / "obs-studio" / "global.ini").is_file())
check("backup dir mode 700", stat.S_IMODE(b1.stat().st_mode) == 0o700)

# nothing changed -> all ok
items = {i.label: i for i in ob.verify()}
check("verify: script ok", items["obs-scene script"].status == "ok")
check("verify: password ok", items["WebSocket password"].status == "ok")

# mutate script, delete password
p.script.write_text("#!/bin/sh\necho tampered\n")
p.pw_file.unlink()
items2 = {i.label: i for i in ob.verify()}
check("verify: script changed", items2["obs-scene script"].status == "changed")
check("verify: password missing", items2["WebSocket password"].status == "missing")

# restore both
res = {r.label: r for r in ob.restore(["obs-scene script", "WebSocket password"])}
check("restore script ok", res["obs-scene script"].ok)
check("restore password ok", res["WebSocket password"].ok)
sha = hashlib.sha256(p.script.read_bytes()).hexdigest()
check("restored script matches manifest hash", sha == man["sha256"]["obs-scene"])
check("restored password mode 600",
      stat.S_IMODE(p.pw_file.stat().st_mode) == 0o600)

# pruning
for _ in range(12):
    import time
    time.sleep(0.01)
    ob.backup()
check("prune keeps MAX_BACKUPS", len(ob.list_backups()) == ob.MAX_BACKUPS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `AttributeError: ... has no attribute 'backup'`

- [ ] **Step 3: Write minimal implementation**

Add to `cleanmint/core/obs_switcher.py` (and delete the temporary `list_backups` stub from Task 5 — keep only this real version if it differs; the Task 5 version is already correct, so keep it):

```python
# ── Backup / verify / restore ──────────────────────────────────────────────

_TRACKED = {  # manifest key -> live path attribute on Paths
    "obs-scene": "script",
    "password": "pw_file",
}


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
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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
        has_src = (backup_dir / Path(_backup_name(key))).is_file()
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


def _backup_name(key: str) -> str:
    return {"obs-scene": "obs-scene", "password": "password"}[key]


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
                if obs_running():
                    results.append(StepResult(
                        label, False, "close OBS first, then restore"))
                else:
                    shutil.copytree(backup_dir / "obs-studio", p.obs_cfg_dir,
                                    dirs_exist_ok=True)
                    results.append(StepResult(label, True, "OBS config restored"))
            else:
                results.append(StepResult(label, False, "unknown item"))
        except Exception as e:  # noqa: BLE001
            results.append(StepResult(label, False, str(e)))
    return results
```

Note: `restore` references `_write_shortcuts` — implemented in Task 7. Task 6's test does not exercise the `"GNOME shortcuts"` branch, so the name being unresolved until Task 7 is fine (Python resolves it at call time).

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cleanmint/core/obs_switcher.py tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): backup / verify / restore"
```

---

## Task 7: `build()`, `_write_shortcuts()`, lock/unlock

**Files:**
- Modify: `cleanmint/core/obs_switcher.py`
- Test: `tests/test_obs_switcher.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_switcher.py`:

```python
print("\n=== OBS Switcher — build ===\n")

home = new_home()
p = ob._paths()

# stub the slow/privileged bits
_venv_calls = []
ob._venv_build = lambda: (_venv_calls.append(1), None)[1]
ob._venv_ok = lambda: True
ob._write_shortcuts = lambda: None            # gsettings not under test here
ob.obs_running = lambda: False

# OBS websocket config with a password present
p.obs_ws_config.parent.mkdir(parents=True)
p.obs_ws_config.write_text(json.dumps({
    "server_enabled": False, "server_port": 4455,
    "auth_required": False, "server_password": "from-obs",
}))

r1 = ob.build()
check("build ok", r1.ok, f"steps={[(s.label, s.ok) for s in r1.steps]}")
check("venv built once", _venv_calls == [1])
check("password copied from OBS", p.pw_file.read_text() == "from-obs")
check("password mode 600", stat.S_IMODE(p.pw_file.stat().st_mode) == 0o600)
check("script written & executable",
      p.script.is_file() and os.access(p.script, os.X_OK))
ws = json.loads(p.obs_ws_config.read_text())
check("websocket enabled in OBS config", ws["server_enabled"] is True)
check("websocket auth_required set", ws["auth_required"] is True)
check("build took a backup", len(ob.list_backups()) >= 1)

# idempotent: second run, venv not rebuilt
r2 = ob.build()
check("second build ok", r2.ok)
check("venv not rebuilt (still one call)", _venv_calls == [1])

print("\n=== OBS Switcher — build needs password ===\n")
home = new_home()
p = ob._paths()
ob._venv_build = lambda: None
ob._venv_ok = lambda: True
ob._write_shortcuts = lambda: None
ob.obs_running = lambda: False
# no OBS config at all
r3 = ob.build()
check("needs_password flagged", r3.needs_password is True)
check("build not ok when password missing", r3.ok is False)
ob.set_password("typed-by-user")
r4 = ob.build()
check("build ok after set_password", r4.ok, f"{[(s.label,s.ok,s.detail) for s in r4.steps]}")

print("\n=== OBS Switcher — _write_shortcuts arg building ===\n")
# reload module to drop the monkeypatches above
import importlib
ob = importlib.reload(ob)
home = new_home()

calls = []
ob._run = lambda cmd, timeout=15, **kw: calls.append(cmd) or _fake_cp()

def _fake_cp():
    import subprocess as sp
    return sp.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")

ob._dconf_shortcuts = lambda: {}
ob._write_shortcuts()
sets = [c for c in calls if c[:2] == ["gsettings", "set"]]
check("_write_shortcuts issued gsettings set calls", len(sets) >= 6)
check("_write_shortcuts wrote the custom-keybindings array",
      any(c[3] == "custom-keybindings" for c in sets))
```

Add this tiny helper near the top of the test file (after `check`):

```python
def _fake_cp(stdout="", rc=0):
    import subprocess as sp
    return sp.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")
```

(Adjust the two `_fake_cp` references above to use it; remove the inline def.)

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `AttributeError: ... has no attribute 'build'`

- [ ] **Step 3: Write minimal implementation**

Add to `cleanmint/core/obs_switcher.py`:

```python
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

    def step(label, pct, fn):
        if progress_cb:
            progress_cb(label, pct)
        try:
            detail = fn() or "done"
            result.steps.append(StepResult(label, True, detail))
        except NeedsPasswordError:
            result.needs_password = True
            result.steps.append(StepResult(label, False, "no password available"))
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
        if obs_running():
            result.warnings.append(
                "OBS is open — enable WebSocket in Tools → WebSocket Server "
                "Settings (port 4455, authentication on), then run Build again.")
            return "skipped (OBS running)"
        if not p.obs_ws_config.is_file():
            raise RuntimeError(
                "OBS WebSocket config not found — open OBS once first")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cleanmint/core/obs_switcher.py tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): build(), shortcut writer, lock/unlock"
```

---

## Task 8: `self_test()`

**Files:**
- Modify: `cleanmint/core/obs_switcher.py`
- Test: `tests/test_obs_switcher.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_switcher.py`:

```python
print("\n=== OBS Switcher — self_test ===\n")

import importlib
ob = importlib.reload(ob)
home = new_home()
p = ob._paths()
p.script.parent.mkdir(parents=True)

# fake obs-scene: writes the requested scene to a state file
state = home / "scene_state.txt"
state.write_text("Intro")
p.script.write_text(
    "#!/bin/sh\n"
    f'echo "$1" > "{state}"\n'
)
os.chmod(p.script, 0o755)

# _query_scene reads the state file; pretend OBS + websocket are up
ob.obs_running = lambda: True
ob.websocket_reachable = lambda timeout=1.0: True
ob._query_scene = lambda: state.read_text().strip()

steps = ob.self_test()
labels = [s.label for s in steps]
oks = {s.label: s.ok for s in steps}
check("self_test switched to Laptop", any("Laptop" in l for l in labels))
check("self_test Laptop step ok",
      all(v for k, v in oks.items() if "Laptop" in k))
check("self_test Tablet step ok",
      all(v for k, v in oks.items() if "Tablet" in k))
check("self_test restored original scene last", state.read_text().strip() == "Intro")
check("self_test all ok", all(s.ok for s in steps))

# precondition failure
ob.websocket_reachable = lambda timeout=1.0: False
steps2 = ob.self_test()
check("self_test one precondition failure when ws down",
      len(steps2) == 1 and steps2[0].ok is False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `AttributeError: ... has no attribute 'self_test'`

- [ ] **Step 3: Write minimal implementation**

Add to `cleanmint/core/obs_switcher.py`:

```python
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


def self_test(progress_cb=None) -> list[StepResult]:
    p = _paths()

    def _prog(msg, pct):
        if progress_cb:
            progress_cb(msg, pct)

    if not (obs_running() and websocket_reachable()):
        return [StepResult(
            "Precondition", False,
            "Start OBS first (WebSocket must be reachable on port 4455).")]

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cleanmint/core/obs_switcher.py tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): self_test end-to-end switch check"
```

---

## Task 9: Helper script — `obs-lock` / `obs-unlock`

**Files:**
- Modify: `cleanmint/assets/cleanmint-helper` (add a case before `*)`; update the usage echo)
- Test: `tests/test_obs_switcher.py` (append a lint check)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_obs_switcher.py`:

```python
print("\n=== OBS Switcher — helper asset ===\n")

helper = Path(__file__).parent.parent / "cleanmint" / "assets" / "cleanmint-helper"
htext = helper.read_text()
check("helper has obs-lock case", "obs-lock" in htext)
check("helper has obs-unlock case", "obs-unlock" in htext)
check("helper derives home from PKEXEC_UID", "PKEXEC_UID" in htext)
check("helper uses chattr", "chattr" in htext)
lint = subprocess.run(["bash", "-n", str(helper)], capture_output=True, text=True)
check("helper passes bash -n", lint.returncode == 0, lint.stderr.strip())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: FAIL — `helper has obs-lock case` fails

- [ ] **Step 3: Write minimal implementation**

In `cleanmint/assets/cleanmint-helper`, add this case immediately before the `*)` default case:

```bash
    obs-lock|obs-unlock)
        # Toggle the immutable (+i) attribute on the OBS-switcher files that
        # must never be modified or deleted. Paths are derived from the
        # calling user's home — this op can only ever touch this fixed list.
        home="$(getent passwd "${PKEXEC_UID:-0}" | cut -d: -f6)"
        [ -n "$home" ] && [ -d "$home" ] || {
            echo "obs-lock: cannot resolve caller home directory" >&2
            exit 1
        }
        flag="+i"
        [ "$OPERATION" = "obs-unlock" ] && flag="-i"
        for rel in ".local/bin/obs-scene" \
                   ".config/obs-hotkeys/password" \
                   ".config/obs-hotkeys"; do
            target="$home/$rel"
            if [ -e "$target" ]; then
                /usr/bin/chattr "$flag" "$target" 2>/dev/null || true
            fi
        done
        exit 0
        ;;
```

Then update the usage echo in the `*)` case. Change:

```bash
        echo "Valid: journal-vacuum, apt-clean, apt-update, apt-upgrade, apt-upgrade-pkgs," >&2
        echo "       snap-remove, snap-uninstall, apt-remove," >&2
        echo "       flatpak-uninstall, systemctl-restart" >&2
```

to:

```bash
        echo "Valid: journal-vacuum, apt-clean, apt-update, apt-upgrade, apt-upgrade-pkgs," >&2
        echo "       snap-remove, snap-uninstall, apt-remove," >&2
        echo "       flatpak-uninstall, systemctl-restart, obs-lock, obs-unlock" >&2
```

- [ ] **Step 4: Run test to verify it passes**

Run: `venv/bin/python tests/test_obs_switcher.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cleanmint/assets/cleanmint-helper tests/test_obs_switcher.py
git commit -m "feat(obs-switcher): obs-lock/obs-unlock helper operation"
```

---

## Task 10: The UI page

**Files:**
- Create: `cleanmint/ui/obs_switcher_page.py`
- Test: manual (covered by Task 11's import test + live smoke)

- [ ] **Step 1: Write the page**

Create `cleanmint/ui/obs_switcher_page.py`:

```python
"""
ui/obs_switcher_page.py — OBS laptop⇄tablet switcher setup, protection & test.
"""

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core import obs_switcher as ob
from ui.theme import Theme


class Worker(QThread):
    progress = pyqtSignal(str, int)
    done = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, fn, *args, wants_progress=False):
        super().__init__()
        self._fn = fn
        self._args = args
        self._wants_progress = wants_progress

    def run(self):
        try:
            if self._wants_progress:
                res = self._fn(
                    *self._args,
                    progress_cb=lambda m, p: self.progress.emit(m, p),
                )
            else:
                res = self._fn(*self._args)
            self.done.emit(res)
        except Exception as e:  # noqa: BLE001
            self.fail.emit(str(e))


class RestoreDialog(QDialog):
    def __init__(self, items: list[ob.VerifyItem], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Check & Restore")
        self.setMinimumWidth(460)
        self.setStyleSheet(Theme.stylesheet())
        self._boxes: list[tuple[QCheckBox, str]] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        title = QLabel("Comparison with the most recent backup:")
        title.setObjectName("TitleLabel")
        lay.addWidget(title)

        icon = {"ok": "✓", "changed": "!", "missing": "✗"}
        for it in items:
            row = QHBoxLayout()
            box = QCheckBox(f"{icon.get(it.status, '?')}  {it.label} — {it.status}")
            restorable = it.status in ("changed", "missing") and it.can_restore
            box.setEnabled(restorable)
            box.setChecked(restorable)
            row.addWidget(box)
            lay.addLayout(row)
            self._boxes.append((box, it.label))

        btns = QDialogButtonBox()
        self._restore_btn = btns.addButton("Restore Selected",
                                           QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def selected_labels(self) -> list[str]:
        return [label for box, label in self._boxes
                if box.isChecked() and box.isEnabled()]


class TestReportDialog(QDialog):
    def __init__(self, steps: list[ob.StepResult], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Test Switching")
        self.setMinimumWidth(460)
        self.setStyleSheet(Theme.stylesheet())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(8)

        all_ok = all(s.ok for s in steps)
        head = QLabel("✓  All checks passed" if all_ok
                      else "✗  Something needs attention")
        head.setObjectName("TitleLabel")
        lay.addWidget(head)

        for s in steps:
            lbl = QLabel(f"{'✓' if s.ok else '✗'}  {s.label}"
                         + (f" — {s.detail}" if s.detail else ""))
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        lay.addWidget(btns)


class ObsSwitcherPage(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: QThread | None = None
        self._checks: list[ob.Check] = []
        self._build_ui()
        QTimer.singleShot(200, self._refresh)

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        p = Theme.p()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        hdr = QHBoxLayout()
        title = QLabel("OBS Switcher")
        title.setObjectName("TitleLabel")
        hdr.addWidget(title)
        hdr.addStretch()
        self._refresh_btn = QPushButton("⟳  Refresh")
        self._refresh_btn.setObjectName("SecondaryBtn")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setFixedHeight(30)
        self._refresh_btn.clicked.connect(self._refresh)
        hdr.addWidget(self._refresh_btn)
        layout.addLayout(hdr)

        sub = QLabel(
            "Build, protect, back up and test the Ctrl+Alt+1 / Ctrl+Alt+2 "
            "OBS scene-switching setup for your laptop and Samsung tablet. "
            "Your OBS scenes are never modified."
        )
        sub.setObjectName("SubtitleLabel")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(6)
        self._progress.setTextVisible(False)
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setObjectName("MutedLabel")
        self._status.hide()
        layout.addWidget(self._status)

        # action bar
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._build_btn = QPushButton("⚙  Build / Repair Setup")
        self._build_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._build_btn.setFixedHeight(34)
        self._build_btn.setStyleSheet(
            f"QPushButton {{ background: {p.accent}; color: {p.accent_text};"
            f"  border: none; border-radius: 6px; font-weight: 600;"
            f"  padding: 0 16px; }}"
            f"QPushButton:hover {{ background: {p.accent_hover}; }}"
            f"QPushButton:disabled {{ background: {p.accent}55; }}"
        )
        self._build_btn.clicked.connect(self._build)
        bar.addWidget(self._build_btn)

        self._lock_btn = QPushButton("🔒  Protect Files")
        self._lock_btn.setObjectName("SecondaryBtn")
        self._lock_btn.setFixedHeight(34)
        self._lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lock_btn.clicked.connect(self._toggle_lock)
        bar.addWidget(self._lock_btn)

        self._backup_btn = QPushButton("💾  Back Up Now")
        self._backup_btn.setObjectName("SecondaryBtn")
        self._backup_btn.setFixedHeight(34)
        self._backup_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._backup_btn.clicked.connect(self._backup)
        bar.addWidget(self._backup_btn)

        self._restore_btn = QPushButton("🔍  Check & Restore")
        self._restore_btn.setObjectName("SecondaryBtn")
        self._restore_btn.setFixedHeight(34)
        self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_btn.clicked.connect(self._check_restore)
        bar.addWidget(self._restore_btn)

        self._test_btn = QPushButton("▶  Test Switching")
        self._test_btn.setObjectName("SecondaryBtn")
        self._test_btn.setFixedHeight(34)
        self._test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_btn.clicked.connect(self._test)
        bar.addWidget(self._test_btn)

        bar.addStretch()
        layout.addLayout(bar)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Check", "Detail"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.cellClicked.connect(self._on_cell_clicked)
        layout.addWidget(self._table, 1)

    # ── helpers ──────────────────────────────────────────────────────────
    def _set_busy(self, busy: bool):
        for b in (self._build_btn, self._lock_btn, self._backup_btn,
                  self._restore_btn, self._test_btn, self._refresh_btn):
            b.setEnabled(not busy)

    def _set_status(self, msg: str):
        self._status.setText(msg)
        self._status.setVisible(bool(msg))

    def _run_worker(self, fn, *args, wants_progress=False, on_done=None):
        self._set_busy(True)
        w = Worker(fn, *args, wants_progress=wants_progress)
        w.progress.connect(lambda m, p: (self._progress.show(),
                                         self._progress.setValue(p),
                                         self._set_status(m)))
        def _finish(res):
            self._set_busy(False)
            self._progress.hide()
            self._set_status("")
            if on_done:
                on_done(res)
        w.done.connect(_finish)
        w.fail.connect(lambda e: (_finish(None),
                                  QMessageBox.warning(self, "Error", e)))
        w.start()
        self._worker = w

    # ── actions ──────────────────────────────────────────────────────────
    def _refresh(self):
        self._run_worker(ob.check_status, on_done=self._populate)

    def _populate(self, checks):
        if checks is None:
            return
        p = Theme.p()
        self._checks = checks
        self._table.setRowCount(0)
        for c in checks:
            row = self._table.rowCount()
            self._table.insertRow(row)
            if c.key in ("session", "protection", "backup", "tablet"):
                mark, colour = "•", p.text_secondary
            elif c.ok:
                mark, colour = "✓", p.success
            elif c.fixable:
                mark, colour = "✗", p.danger
            else:
                mark, colour = "!", p.warning
            it_left = QTableWidgetItem(f"{mark}  {c.label}")
            it_right = QTableWidgetItem(c.detail)
            for it in (it_left, it_right):
                it.setForeground(QColor(colour))
            self._table.setItem(row, 0, it_left)
            self._table.setItem(row, 1, it_right)
        # lock button label
        locked = any(c.key == "protection" and c.detail == "Locked"
                     for c in checks)
        self._lock_btn.setText("🔓  Unprotect Files" if locked
                               else "🔒  Protect Files")

    def _on_cell_clicked(self, row, _col):
        if row >= len(self._checks):
            return
        c = self._checks[row]
        if (not c.ok) and (not c.fixable) and c.manual_steps:
            QMessageBox.information(self, c.label, c.manual_steps)

    def _build(self):
        self._run_worker(ob.build, wants_progress=True, on_done=self._after_build)

    def _after_build(self, result):
        if result is None:
            return
        if result.needs_password:
            pw, ok = QInputDialog.getText(
                self, "OBS WebSocket password",
                "CleanMint could not read the password from OBS.\n"
                "Open OBS → Tools → WebSocket Server Settings → Show Connect "
                "Info, and paste the Server Password here:",
                QLineEdit.EchoMode.Password,
            )
            if ok and pw.strip():
                ob.set_password(pw.strip())
                self._run_worker(ob.build, wants_progress=True,
                                 on_done=self._after_build)
                return
        msgs = "\n".join(f"{'✓' if s.ok else '✗'}  {s.label}: {s.detail}"
                         for s in result.steps)
        if result.warnings:
            msgs += "\n\n" + "\n".join("⚠  " + w for w in result.warnings)
        box = QMessageBox.information if result.ok else QMessageBox.warning
        box(self, "Build / Repair", msgs)
        self._refresh()

    def _toggle_lock(self):
        locked = self._lock_btn.text().startswith("🔓")
        if not locked:
            if QMessageBox.question(
                self, "Protect Files",
                "This locks the switch script and password file so they cannot "
                "be changed or deleted.\n\nYou must click “Unprotect Files” here "
                "before editing them again. Continue?",
            ) != QMessageBox.StandardButton.Yes:
                return
        fn = ob.unlock if locked else ob.lock

        def _done(res):
            ok, err = res if res else (False, "cancelled")
            if not ok and err:
                QMessageBox.warning(self, "Protection", err)
            self._refresh()
        self._run_worker(fn, on_done=_done)

    def _backup(self):
        def _done(path):
            if path:
                QMessageBox.information(self, "Backup",
                                       f"Backup saved to:\n{path}")
        self._run_worker(ob.backup, wants_progress=True, on_done=_done)

    def _check_restore(self):
        def _done(items):
            if not items:
                QMessageBox.information(
                    self, "Check & Restore",
                    "No backup found yet. Click “Back Up Now” first.")
                return
            dlg = RestoreDialog(items, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                labels = dlg.selected_labels()
                if labels:
                    self._run_worker(ob.restore, labels,
                                     on_done=self._after_restore)
        self._run_worker(ob.verify, on_done=_done)

    def _after_restore(self, results):
        if results is None:
            return
        msg = "\n".join(f"{'✓' if r.ok else '✗'}  {r.label}: {r.detail}"
                        for r in results)
        QMessageBox.information(self, "Restore", msg)
        self._refresh()

    def _test(self):
        def _done(steps):
            if steps is None:
                return
            TestReportDialog(steps, self).exec()
        self._run_worker(ob.self_test, wants_progress=True, on_done=_done)
```

- [ ] **Step 2: Verify it imports**

Run:
```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -c "import sys; sys.path.insert(0,'cleanmint'); import ui.obs_switcher_page; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add cleanmint/ui/obs_switcher_page.py
git commit -m "feat(obs-switcher): UI page with status list, build, lock, backup, test"
```

---

## Task 11: Wire into main window + import test

**Files:**
- Modify: `cleanmint/ui/main_window.py:31` (NAV_ITEMS) and `:193` (after the `transcriber` branch)
- Modify: `tests/test_ui_imports.py`

- [ ] **Step 1: Add the import-test line**

In `tests/test_ui_imports.py`, find the block that checks `ui` page imports (search for `ui.transcriber_page` or `ui.snapshot_page`) and add alongside it:

```python
check("ui.obs_switcher_page", lambda: __import__("ui.obs_switcher_page"))
```

- [ ] **Step 2: Run it to verify it fails**

Run: `venv/bin/python tests/test_ui_imports.py`
Expected: FAIL — `ui.obs_switcher_page` line fails (module not wired / import error) — actually it will PASS if Task 10 is done. If it passes, that's fine; continue. If your runner requires the nav wiring, proceed to Step 3.

- [ ] **Step 3: Wire NAV_ITEMS**

In `cleanmint/ui/main_window.py`, change:

```python
    ("transcriber", "▶  Transcriber"),
    ("settings",    "⚙  Settings"),
```

to:

```python
    ("transcriber", "▶  Transcriber"),
    ("obs_switcher", "⇄  OBS Switcher"),
    ("settings",    "⚙  Settings"),
```

- [ ] **Step 4: Wire `_create_page`**

In `cleanmint/ui/main_window.py`, change:

```python
        elif key == "transcriber":
            from ui.transcriber_page import TranscriberPage
            return TranscriberPage()
        elif key == "settings":
```

to:

```python
        elif key == "transcriber":
            from ui.transcriber_page import TranscriberPage
            return TranscriberPage()
        elif key == "obs_switcher":
            from ui.obs_switcher_page import ObsSwitcherPage
            return ObsSwitcherPage()
        elif key == "settings":
```

- [ ] **Step 5: Run the import test + full suite**

Run:
```bash
venv/bin/python tests/test_ui_imports.py
venv/bin/python tests/test_obs_switcher.py
```
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add cleanmint/ui/main_window.py tests/test_ui_imports.py
git commit -m "feat(obs-switcher): sidebar entry + import test"
```

---

## Task 12: Docs + full regression + live smoke test

**Files:**
- Modify: `CLAUDE.md`
- Modify: `STATE.md`

- [ ] **Step 1: Update CLAUDE.md — feature list**

In `CLAUDE.md`, after the `- **Transcriber page** …` bullet block, add:

```markdown
- **OBS Switcher page**: Builds/repairs/protects/tests the laptop⇄Samsung-tablet
  OBS scene-switching setup (Ctrl+Alt+1 → Laptop, Ctrl+Alt+2 → Tablet)
  - Status checklist: OBS, WebSocket, password file, venv, obs-scene script,
    GNOME shortcuts, scenes, scrcpy, adb, tablet
  - "Build / Repair Setup": creates the venv + obsws-python, writes
    `~/.local/bin/obs-scene`, copies the WebSocket password from OBS's own
    config (prompts if absent), enables OBS WebSocket (only while OBS is
    closed, backed up first), writes the two GNOME shortcuts additively
  - "Protect Files": `chattr +i` on `~/.local/bin/obs-scene` +
    `~/.config/obs-hotkeys/` via `cleanmint-helper obs-lock` (one pkexec prompt);
    "Unprotect Files" reverses it
  - "Back Up Now" + "Check & Restore": versioned backups of the OBS config
    folder, GNOME shortcuts, script and password under
    `~/.local/share/cleanmint/obs-switcher/backups/` (last 10); verify detects
    changed/missing/scene-name-gone and restores per item
  - "Test Switching": runs the real `obs-scene Laptop`/`Tablet` against a live
    OBS and confirms the program scene changed, then restores it
  - Engine `core/obs_switcher.py` (UI-free), page `ui/obs_switcher_page.py`
  - NEVER edits OBS scene contents
```

- [ ] **Step 2: Update CLAUDE.md — helper operations**

In `CLAUDE.md`, in the Polkit policy section, change:

```markdown
  - Covers: journalctl, snap, apt-get, systemctl — all with `auth_admin_keep` (one password per session)
```

to:

```markdown
  - Covers: journalctl, snap, apt-get, systemctl, chattr (obs-lock/obs-unlock)
    — all with `auth_admin_keep` (one password per session)
```

- [ ] **Step 3: Run the full test suite**

Run:
```bash
for t in tests/test_*.py; do echo "=== $t ==="; venv/bin/python "$t" || exit 1; done
```
Expected: every suite prints PASS lines and exits 0. (Existing count was 81; new
`test_obs_switcher.py` adds its own checks and `test_ui_imports.py` gains one.)

- [ ] **Step 4: Live smoke test on this machine**

The real setup exists here. Run:
```bash
bash cleanmint/run.sh
```
Then in the app:
1. Open **OBS Switcher**. Confirm the status list loads and most rows are green
   (`session`, `scenes`, `script`, `shortcuts`, `password_file`). If OBS is
   running, `websocket` may show as needing OBS closed — acceptable.
2. Click **Test Switching** with OBS open → report shows Laptop ✓, Tablet ✓,
   Restore ✓.
3. Click **Back Up Now** → confirm a folder appears under
   `~/.local/share/cleanmint/obs-switcher/backups/`.
4. Click **Protect Files** → enter password once → then in a terminal:
   `rm ~/.local/bin/obs-scene` → must fail with "Operation not permitted".
   `lsattr ~/.local/bin/obs-scene` shows `----i---------`.
5. Click **Unprotect Files** → `rm` is allowed again (don't actually delete;
   just confirm `lsattr` no longer shows `i`).
6. Click **Check & Restore** → all items `ok`.

Record the outcome. If any step fails, **stop and fix before completing** — the
spec requires this feature never to fail in normal use.

- [ ] **Step 5: Update STATE.md**

Replace `STATE.md` body with:

```markdown
# STATE — resume pointer

**Focus:** OBS Switcher feature — COMPLETE.

**Last decision:** Implemented per plan
`docs/superpowers/plans/2026-09-06-obs-switcher.md`. Engine
`core/obs_switcher.py`, page `ui/obs_switcher_page.py`, helper `obs-lock`/
`obs-unlock`, sidebar entry. Live smoke test passed on this machine.

**Next action:** none — feature done. Future: consider optional cloud/USB
backup export.

**Hints:** —
```

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md STATE.md
git commit -m "docs(obs-switcher): document feature + helper ops; update STATE"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Paths / dataclasses | 1 |
| `render_script` | 1 |
| `plan_shortcut_slots` no-clobber | 2 |
| `obs_running` / `websocket_reachable` / `read_obs_password` | 3 |
| `set_password` / script writer | 4 |
| `check_status` (all 14 checks) | 5 |
| `backup` / `list_backups` / `verify` / `restore` | 5 (list_backups), 6 |
| `build` (7 steps, idempotent, needs_password) | 7 |
| `_write_shortcuts` (gsettings, additive) | 7 |
| `lock` / `unlock` / `is_locked` | 5 (is_locked), 7 |
| `self_test` | 8 |
| helper `obs-lock` / `obs-unlock` | 9 |
| UI page (status list, 5 action buttons, dialogs, workers) | 10 |
| sidebar entry + import test | 11 |
| CLAUDE.md + STATE.md + full regression + live smoke | 12 |
| Rules: subprocess list-form, no user-data deletion, QThread, backup-first | enforced in 6, 7, 10 |
| Error-handling table | 5 (swallowed checks), 7 (guarded steps), 10 (`fail` signal) |

No gaps.

**Placeholder scan:** No "TBD"/"TODO"/"handle edge cases"/"similar to Task N".
Every code step carries full code.

**Type consistency:**
- `Check(key, label, ok, detail, fixable, manual_steps="")` — used consistently;
  positional `Check(...)` calls in `check_status` pass 5 args + keyword
  `manual_steps` for the three hand-built ones. ✓
- `StepResult(label, ok, detail="")` — consistent across `build`, `restore`,
  `self_test`. ✓
- `BuildResult.ok` property — used in Task 7 test and Task 10 `_after_build`. ✓
- `VerifyItem(label, status, can_restore)` — produced in Task 6, consumed in
  Task 10 `RestoreDialog`. ✓
- `_venv_build` / `_venv_ok` / `_write_shortcuts` / `_query_scene` / `obs_running`
  / `websocket_reachable` — all module-level so tests monkeypatch
  `ob.<name>`; `build()` and `self_test()` call them as module globals (not
  captured references), so monkeypatching works. ✓
- `list_backups` defined once (Task 5), referenced by `check_status`, `backup`,
  `verify`, `restore`. Task 6 does not redefine it. ✓
- `_newest_backup` (Task 5) used by `verify`/`restore`/`check_status`. ✓

Fixed inline: Task 6 note clarifies `list_backups` is not re-added; Task 5 note
says the stubs it adds (`list_backups`, `is_locked`) are the real versions, not
throwaways.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-06-obs-switcher.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
