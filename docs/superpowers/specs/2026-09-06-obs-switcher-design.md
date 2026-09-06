# OBS Switcher — Design Spec (2026-09-06)

## Goal

A new CleanMint page (`⇄ OBS Switcher`) that builds, repairs, protects, backs up,
and tests the laptop⇄Samsung-tablet OBS scene-switching setup documented in
`obs.md`. It turns a multi-hour manual procedure into buttons, and hardens the
files that must never be lost.

The feature **never modifies the contents of the OBS scenes themselves** — only
the automation layer around them (script, password, venv, GNOME shortcuts,
WebSocket toggle).

## Background — the working setup (already present on this machine)

| Component | Path | Notes |
|---|---|---|
| Switch script | `~/.local/bin/obs-scene` | `obs-scene Laptop` / `obs-scene Tablet` |
| WebSocket password | `~/.config/obs-hotkeys/password` | mode 600, dir mode 700 |
| Python venv | `~/.obs-hotkey-venv/` | has `obsws-python` |
| OBS config (Flatpak) | `~/.var/app/com.obsproject.Studio/config/obs-studio/` | holds scene collections |
| OBS WebSocket config | `…/obs-studio/plugin_config/obs-websocket/config.json` | `server_password`, `server_port`, `server_enabled`, `auth_required` |
| GNOME shortcuts | dconf `/org/gnome/settings-daemon/plugins/media-keys/` | `custom0` → Ctrl+Alt+1 → Laptop, `custom1` → Ctrl+Alt+2 → Tablet |
| scrcpy | `~/scrcpy-linux-x86_64-v4.1/` | window title `Samsung Tablet` |

The OBS scene collection already contains scenes named `Laptop` and `Tablet`.
The WebSocket password is readable directly from OBS's own `config.json` — no
manual paste needed in the normal case.

## Decisions (user-approved)

- **Protection**: `chattr +i` (immutable) on the stable files via the existing
  pkexec helper. OBS config + GNOME shortcuts cannot be locked (OBS/GNOME rewrite
  them) — covered by versioned backups + verify/restore instead.
- **Build scope**: automation layer only. System-package installation and scrcpy
  download are **not** automated — shown as guided checklist steps.
- **Password**: auto-read from OBS `config.json`; prompt (paste dialog) only as
  fallback.
- **Scenes**: verify `Laptop`/`Tablet` exist; never create or edit them.

## Files

- `cleanmint/core/obs_switcher.py` — all logic, UI-free (~450 lines)
- `cleanmint/ui/obs_switcher_page.py` — page + QThread workers, snapshot-page
  pattern (~400 lines)
- `cleanmint/assets/cleanmint-helper` — add `obs-lock` / `obs-unlock` operations
- `cleanmint/ui/main_window.py` — one `NAV_ITEMS` entry + lazy import in `_make_page`
- `CLAUDE.md` — document the feature and the new helper operations
- `tests/test_obs_switcher.py` — offline suite (~300 lines)
- `tests/test_ui_imports.py` — add `ui.obs_switcher_page` import check

## core/obs_switcher.py

### Paths (module constants, all derived from `Path.home()`)

```
OBS_SCENE_SCRIPT  = HOME/".local/bin/obs-scene"
PW_DIR            = HOME/".config/obs-hotkeys"
PW_FILE           = PW_DIR/"password"
VENV_DIR          = HOME/".obs-hotkey-venv"
VENV_PY           = VENV_DIR/"bin/python3"
OBS_CFG_DIR       = HOME/".var/app/com.obsproject.Studio/config/obs-studio"
OBS_WS_CONFIG     = OBS_CFG_DIR/"plugin_config/obs-websocket/config.json"
SCENE_DIR         = OBS_CFG_DIR/"basic/scenes"
BACKUP_ROOT       = HOME/".local/share/cleanmint/obs-switcher/backups"
DCONF_MEDIA_KEYS  = "/org/gnome/settings-daemon/plugins/media-keys/"
WS_PORT           = 4455
SCENES            = ("Laptop", "Tablet")
HOTKEYS = [
    ("<Control><Alt>1", "OBS — Laptop", "Laptop"),
    ("<Control><Alt>2", "OBS — Tablet", "Tablet"),
]
MAX_BACKUPS       = 10
LOCK_TARGETS_REL  = [".local/bin/obs-scene", ".config/obs-hotkeys/password",
                     ".config/obs-hotkeys"]
```

Paths are computed at call time (not import time) via a small `_paths()` helper so
tests can point `HOME` at a tmpdir.

### Data types

```python
@dataclass
class Check:
    key: str
    label: str
    ok: bool
    detail: str
    fixable: bool          # True = "Build / Repair" can fix it
    manual_steps: str = "" # shown when clicked, for non-fixable failures

@dataclass
class StepResult:
    label: str
    ok: bool
    detail: str

@dataclass
class BuildResult:
    steps: list[StepResult]
    warnings: list[str]
    needs_password: bool          # OBS config had no password → UI must prompt
    @property
    def ok(self) -> bool: ...     # all fixable steps succeeded

@dataclass
class VerifyItem:
    label: str
    status: str    # "ok" | "changed" | "missing"
    can_restore: bool

class NeedsPasswordError(Exception): ...
```

### Public API

```python
def obs_running() -> bool
    # pgrep -f com.obsproject.Studio  (list-form, no shell)

def websocket_reachable(timeout=1.0) -> bool
    # socket.create_connection(("127.0.0.1", WS_PORT))

def read_obs_password() -> str | None
    # json.loads(OBS_WS_CONFIG); return server_password or None

def check_status() -> list[Check]
    # runs every check below, never raises

def build(progress_cb=None) -> BuildResult
    # runs the fixable steps; each step guarded, collected into result

def set_password(pw: str) -> None
    # write PW_FILE mode 600, PW_DIR mode 700  (used after the paste dialog)

def lock() -> tuple[bool, str]        # pkexec helper obs-lock
def unlock() -> tuple[bool, str]      # pkexec helper obs-unlock
def is_locked() -> bool               # lsattr on OBS_SCENE_SCRIPT shows 'i'

def backup(progress_cb=None) -> Path  # returns new backup dir
def list_backups() -> list[Path]      # newest first
def verify(against: Path | None = None) -> list[VerifyItem]  # default = newest
def restore(labels: list[str], against: Path | None = None) -> list[StepResult]

def self_test(progress_cb=None) -> list[StepResult]
```

### check_status() — individual checks

| key | ok when | fixable | non-fixable manual_steps |
|---|---|---|---|
| `session` | `$XDG_SESSION_TYPE == "wayland"` | no (info) | note: Xorg also works; do not switch just for this |
| `obs_installed` | `flatpak info com.obsproject.Studio` rc 0, **or** `shutil.which("obs")` | no | `flatpak install flathub com.obsproject.Studio` |
| `obs_config` | `OBS_CFG_DIR.is_dir()` | no | launch OBS once to create it |
| `websocket` | `OBS_WS_CONFIG` parses and `server_enabled is True` and `server_port == 4455` | **yes, only if `not obs_running()`** | OBS → Tools → WebSocket Server Settings → Enable, port 4455, auth on |
| `password_file` | `PW_FILE` exists, non-empty, mode `0o600`, and (if OBS pw readable) bytes match | yes | — |
| `venv` | `VENV_PY` exists and `VENV_PY -c "import obsws_python"` rc 0 | yes | — |
| `script` | `OBS_SCENE_SCRIPT` exists, `os.access(X_OK)`, first line == `#!{VENV_PY}`, and `str(PW_FILE)` appears in body | yes | — |
| `shortcuts` | both `HOTKEYS` present in dconf with matching `binding` **and** `command` ending `obs-scene <Scene>` | yes | — |
| `scenes` | scene names `Laptop` and `Tablet` both found — via WebSocket `get_scene_list` if reachable, else by scanning `*.json` in `SCENE_DIR` for `"name": "Laptop"` / `"Tablet"` | no | OBS → add scenes named exactly `Laptop` and `Tablet` |
| `scrcpy` | `shutil.which("scrcpy")` or any `~/scrcpy-linux-x86_64*/scrcpy` is executable | no | download official static release, extract to `~/` |
| `adb` | `shutil.which("adb")` | no | `sudo apt install adb` |
| `tablet` | `adb devices` lists a line ending `\tdevice` | no (info) | connect tablet, enable USB debugging, authorise |
| `protection` | informational: `is_locked()` → "Locked" / "Unlocked" | — | — |
| `backup` | informational: newest backup age, or "none yet" | — | — |

`check_status` swallows every per-check exception and renders it as `ok=False,
detail="check failed: <e>"`.

### build() — steps (in order, each guarded, idempotent)

1. **venv** — if `VENV_PY` missing: `python3 -m venv VENV_DIR`. Then
   `VENV_DIR/bin/pip install --quiet --upgrade obsws-python`. Verify
   `import obsws_python`. (The venv-builder call is routed through a module-level
   `_run` indirection so tests can stub it.)
2. **pw dir** — `PW_DIR.mkdir(parents=True, exist_ok=True)`, `chmod 0o700`.
3. **password** — `pw = read_obs_password()`. If `pw`: write `PW_FILE`, `chmod
   0o600`. If `None` **and** `PW_FILE` already valid: keep it. If `None` and no
   file: set `result.needs_password = True`, record step as failed with a clear
   message, continue.
4. **script** — write `OBS_SCENE_SCRIPT` from the template (below);
   `mkdir -p` its parent; `chmod 0o755`.
5. **websocket** — if `obs_running()`: append warning "OBS is open — enable
   WebSocket in Tools → WebSocket Server Settings, then run Build again", skip.
   Else: `backup()` first (pre-flight), then `data = json.loads(OBS_WS_CONFIG)`,
   set `server_enabled=True, server_port=4455, auth_required=True`, keep every
   other key, write back with `indent=2`. If `OBS_WS_CONFIG` does not exist,
   record failure ("open OBS once so the WebSocket plugin creates its config").
6. **shortcuts** — see "GNOME shortcut writer" below.
7. **backup** — `backup()` (final snapshot of the now-good state).

`progress_cb(msg: str, pct: int)` called before each step.

### obs-scene script template

Functionally identical to the known-good script; only the interpreter path and
the password path are substituted (both absolute).

```python
#!{VENV_PY}

import sys
import obsws_python as obs

if len(sys.argv) != 2:
    sys.exit(1)

scene = sys.argv[1]

with open("{PW_FILE}", "r") as f:
    password = f.read().strip()

client = obs.ReqClient(host="localhost", port=4455, password=password, timeout=2)
client.set_current_program_scene(scene)
client.disconnect()
```

`render_script(venv_py: str, pw_file: str) -> str` is a pure function (unit-tested).

### GNOME shortcut writer

Must not disturb the user's other custom keybindings.

```
existing = gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings
           → parse the list of dconf paths (may be "@as []")
for each (accel, name, scene) in HOTKEYS:
    want_cmd = f"{OBS_SCENE_SCRIPT} {scene}"
    find a path in `existing` whose stored `command` already ends with
        f"obs-scene {scene}"  → reuse it
    else allocate the next free "customN" slot not in `existing`, add its path
        to the list
    dconf write  <path>binding  "'<accel>'"
    dconf write  <path>command  "'<want_cmd>'"
    dconf write  <path>name     "'<name>'"
gsettings set … custom-keybindings "[<all paths incl. untouched ones>]"
```

`plan_shortcut_slots(existing: list[str], existing_cmds: dict[str,str]) ->
list[(path, accel, name, cmd)]` is a pure function (unit-tested for the
no-clobber property). All reads/writes go through `gsettings` / `dconf`
subprocess calls (list-form).

### Protection — helper operations

Add to `cleanmint/assets/cleanmint-helper`:

```bash
obs-lock|obs-unlock)
    op="$OPERATION"
    home="$(getent passwd "${PKEXEC_UID:-0}" | cut -d: -f6)"
    [ -n "$home" ] && [ -d "$home" ] || { echo "obs-$op: no home for uid" >&2; exit 1; }
    flag="+i"; [ "$op" = "obs-unlock" ] && flag="-i"
    for rel in ".local/bin/obs-scene" ".config/obs-hotkeys/password" ".config/obs-hotkeys"; do
        p="$home/$rel"
        [ -e "$p" ] && /usr/bin/chattr "$flag" "$p" 2>/dev/null || true
    done
    ;;
```

- The helper computes paths from `PKEXEC_UID`'s home and only ever touches the
  fixed relative list — it cannot be told to lock an arbitrary path.
- `chattr -i` runs on unlock even if only some files were locked (safe: `|| true`).
- If `chattr` fails (unsupported fs), the operation is a no-op for that path and
  `is_locked()` will simply keep reporting "Unlocked" — surfaced in the UI.

Python side: `lock()` / `unlock()` call
`pkexec /usr/local/lib/cleanmint/cleanmint-helper obs-lock` and return
`(rc == 0, stderr)`. Because the helper asset changed, `installer.is_policy_installed()`
returns False on next launch and `main_window` re-prompts to update the helper
(existing mechanism — no new install flow needed).

`is_locked()` → `lsattr OBS_SCENE_SCRIPT` output, attribute field contains `i`
(returns False if `lsattr` is missing or errors).

### Backup / Verify / Restore

**backup()** → `BACKUP_ROOT/<YYYYMMDD_HHMMSS>/`:

```
obs-studio/                     shutil.copytree(OBS_CFG_DIR)   (small; holds scenes)
obs-gnome-shortcuts.dconf       dconf dump DCONF_MEDIA_KEYS
obs-scene                       copy of the script (if present)
password                        copy of PW_FILE (if present), chmod 600
manifest.json                   { created_at, obs_running, scene_names_found,
                                  sha256: {relpath: hex, ...} }
```

Backup dir created mode `0o700`. After writing, prune `list_backups()[MAX_BACKUPS:]`
with `shutil.rmtree`.

**verify(against=newest)** → one `VerifyItem` per tracked item:

| label | ok / changed / missing basis |
|---|---|
| `obs-scene script` | sha256 vs manifest |
| `WebSocket password` | sha256 vs manifest |
| `GNOME shortcuts` | current `dconf dump` == backed-up dump |
| `OBS scene "Laptop"` | name still present in current `SCENE_DIR/*.json` |
| `OBS scene "Tablet"` | name still present |
| `OBS config folder` | exists / missing (not checksummed — OBS mutates it) |

**restore(labels, against=newest)** → `StepResult` per requested label:

- `obs-scene script` / `WebSocket password` — copy back from backup, re-apply mode.
- `GNOME shortcuts` — **re-apply the two HOTKEYS** via the shortcut writer (not a
  raw `dconf load` of the whole subtree).
- `OBS scene …` / `OBS config folder` — if `obs_running()`: refuse with
  "close OBS first". Else `shutil.copytree(backup/obs-studio, OBS_CFG_DIR,
  dirs_exist_ok=True)`.

### self_test()

Precondition: `obs_running()` and `websocket_reachable()`. If not → single
`StepResult(ok=False, "Start OBS first (WebSocket must be reachable on 4455)")`
— this is a precondition, not a setup failure.

Steps (each a `StepResult`):

1. Connect via `VENV_PY` running an inline obsws snippet (subprocess, JSON out) →
   read current program scene; remember it.
2. `subprocess.run([OBS_SCENE_SCRIPT, "Laptop"])` → re-query scene → expect `Laptop`.
3. `subprocess.run([OBS_SCENE_SCRIPT, "Tablet"])` → re-query scene → expect `Tablet`.
4. Restore the remembered scene.
5. Summary line.

The obsws client factory used for querying is injectable (`_client_factory`) so the
sequence can be unit-tested with a fake.

## ui/obs_switcher_page.py

Follows `ui/snapshot_page.py` structure: page class + small `QThread` worker
classes, `Theme` styling, accent primary button, `SecondaryBtn` / `DangerBtn`
object names.

Layout:

- Header: title `OBS Switcher` + `⟳ Refresh`.
- Subtitle: one line on what the page does.
- **Status list** — `QTableWidget` (or vertical list of rows), columns
  `[icon] Check | Detail`. Icon/colour: green ✓ ok, red ✗ fail-fixable,
  orange ! fail-not-fixable, grey for info. Clicking a non-fixable failed row →
  `QMessageBox` with its `manual_steps`.
- **Action bar**:
  - `⚙  Build / Repair Setup` — accent. Worker runs `build()`; live log in the
    status label + progress bar. On `BuildResult.needs_password`:
    `QInputDialog.getText(..., EchoMode.Password)` → `set_password()` →
    auto re-run `build()` once. Then refresh status.
  - `🔒  Protect Files` / `🔓  Unprotect Files` — label reflects `is_locked()`.
    Worker calls `lock()`/`unlock()` (pkexec prompt). Refresh.
  - `💾  Back Up Now` — worker `backup()`; toast the path.
  - `🔍  Check & Restore` — worker `verify()`; opens `RestoreDialog` listing
    items, checkboxes enabled only for `changed`/`missing` & `can_restore`,
    `Restore Selected` → `restore()`.
  - `▶  Test Switching` — worker `self_test()`; opens `TestReportDialog` with the
    per-step ✓/✗ list.
- All buttons disabled while any worker runs. Every worker has `finished` /
  `error` signals; `error` → `QMessageBox.warning`, never crashes the page.

Sidebar: `("obs_switcher", "⇄  OBS Switcher")` inserted in `NAV_ITEMS` after
`"transcriber"`; `_make_page` gets an `elif key == "obs_switcher"` lazy import.

## Rules compliance

- All `subprocess` calls list-form, `shell=True` never used.
- No deletion of user data. Only deletions: pruning CleanMint's own old backup
  dirs under `~/.local/share/cleanmint/obs-switcher/`.
- OBS config changes: minimal-key JSON patch (`json.load` → set 3 keys →
  `json.dump`), only while OBS is closed, always preceded by a backup.
- dconf changes: additive; the whole media-keys subtree is never reset or
  blind-loaded.
- All scans / builds / tests run in `QThread`; main thread never blocks.
- PEP8 throughout.

## Error handling

| Situation | Behaviour |
|---|---|
| OBS not installed | `obs_installed` check red-not-fixable with install steps; `build()` still runs the parts it can |
| OBS open during Build | WebSocket step skipped with a warning; everything else proceeds |
| Password not in OBS config and no existing file | `build()` returns `needs_password`; UI shows paste dialog; retried once |
| `chattr` unsupported by filesystem | lock is a no-op; `is_locked()` stays "Unlocked"; UI note "your filesystem does not support locking" |
| Helper not installed / pkexec cancelled | `lock()/unlock()` return `(False, msg)`; `QMessageBox.warning` |
| `self_test` with OBS closed | single informational failure row "Start OBS first" |
| Any worker exception | caught, surfaced via `error` signal as a warning dialog; page stays usable |
| Missing `gsettings`/`dconf`/`adb`/`flatpak` binaries | treated as "not available"; relevant check reports it; no crash |

## Progress reporting

- `build()` — `progress_cb(msg, pct)` at 7 fixed step boundaries.
- `backup()` — messages per artifact copied.
- `self_test()` — message per step.

## Tests — `tests/test_obs_switcher.py`

Repo style: plain script, `PASS`/`FAIL` counter, `sys.path.insert` the
`cleanmint/` dir, exit non-zero on any failure. All tests offline; `HOME`
redirected to a `tempfile.mkdtemp()` and `obs_switcher._paths()` picks it up.
OBS is mocked.

1. `render_script()` → correct shebang line and embedded password path.
2. `check_status()` on an empty sandbox → returns all checks, none raise,
   fixable ones `ok=False`.
3. `build()` in sandbox with a fake `OBS_WS_CONFIG` containing a password →
   creates `PW_FILE` (mode 600), `obs-scene` (mode 755, correct content),
   `PW_DIR` mode 700. Second `build()` run → still ok, no errors (idempotent).
4. `build()` with no password anywhere → `BuildResult.needs_password is True`;
   then `set_password("x")` writes mode-600 file; re-`build()` → ok.
5. venv step stubbed (module `_venv_build` indirection) — assert it's invoked
   once when `VENV_PY` absent, skipped when present.
6. `plan_shortcut_slots()` — given an `existing` list with an unrelated
   `custom0` and an obs `custom1`, returns a plan that reuses `custom1` for its
   scene, allocates `custom2` for the other, and keeps `custom0` in the final
   list. (no-clobber property)
7. `backup()` → creates timestamped dir, `manifest.json` with a sha256 per file,
   dir mode 700. `list_backups()` newest-first. `MAX_BACKUPS` pruning: create 12,
   assert 10 remain.
8. `verify()` — after `backup()`, mutate `obs-scene` → item status `changed`;
   delete `PW_FILE` → status `missing`; untouched → `ok`.
9. `restore(["obs-scene script"])` → file back, sha256 matches manifest.
10. `self_test()` with an injected fake obsws client + a fake `obs-scene`
    (writes the requested scene name to a file the fake client reads back) →
    all steps `ok`, original scene restored last.
11. Helper asset lints: `bash -n cleanmint/assets/cleanmint-helper` rc 0, and the
    file contains an `obs-lock` and `obs-unlock` case.
12. `tests/test_ui_imports.py` — add `ui.obs_switcher_page` import under
    `QT_QPA_PLATFORM=offscreen`.

**Manual live smoke test during implementation** (this machine has the real
setup): open the page → status all green → `Test Switching` passes →
`Protect Files` then confirm `rm ~/.local/bin/obs-scene` is refused →
`Unprotect Files` → `Check & Restore` clean.

## Out of scope

- Installing OBS / adb / python3-venv; downloading scrcpy.
- Creating or editing OBS scenes and sources.
- Any Xorg / OBS-plugin path (the abandoned `obs-wayland-hotkeys` route).
- Syncing backups to external/cloud storage.
