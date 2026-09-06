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


def _fake_cp(stdout="", rc=0):
    import subprocess as sp
    return sp.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


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
check("obs-studio copied into backup",
      (b1 / "obs-studio" / "global.ini").is_file())
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
    ob.backup()
check("prune keeps MAX_BACKUPS", len(ob.list_backups()) == ob.MAX_BACKUPS,
      str(len(ob.list_backups())))


print("\n=== OBS Switcher — build ===\n")

home = new_home()
p = ob._paths()

# stub the slow/privileged bits (stateful venv: not-ok until built)
_venv_calls = []
_venv_state = {"ok": False}


def _fake_venv_build():
    _venv_calls.append(1)
    _venv_state["ok"] = True


ob._venv_build = _fake_venv_build
ob._venv_ok = lambda: _venv_state["ok"]
ob._write_shortcuts = lambda: None            # gsettings not under test here
ob.obs_running = lambda: False

# OBS websocket config with a password present
p.obs_ws_config.parent.mkdir(parents=True)
p.obs_ws_config.write_text(json.dumps({
    "server_enabled": False, "server_port": 4455,
    "auth_required": False, "server_password": "from-obs",
}))

r1 = ob.build()
check("build ok", r1.ok, f"steps={[(s.label, s.ok, s.detail) for s in r1.steps]}")
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
check("build ok after set_password", r4.ok,
      f"{[(s.label, s.ok, s.detail) for s in r4.steps]}")

print("\n=== OBS Switcher — _write_shortcuts arg building ===\n")
import importlib
ob = importlib.reload(ob)
home = new_home()

calls = []
ob._run = lambda cmd, timeout=15, **kw: (calls.append(cmd), _fake_cp("[]"))[1]
ob._dconf_shortcuts = lambda: {}
ob._write_shortcuts()
sets = [c for c in calls if c[:2] == ["gsettings", "set"]]
check("_write_shortcuts issued gsettings set calls", len(sets) >= 6)
check("_write_shortcuts wrote the custom-keybindings array",
      any(c[3] == "custom-keybindings" for c in sets))


print("\n=== OBS Switcher — self_test ===\n")

ob = importlib.reload(ob)
home = new_home()
p = ob._paths()
p.script.parent.mkdir(parents=True)

# fake obs-scene: writes the requested scene to a state file
state = home / "scene_state.txt"
state.write_text("Intro")
p.script.write_text("#!/bin/sh\n" f'echo "$1" > "{state}"\n')
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
check("self_test restored original scene last",
      state.read_text().strip() == "Intro")
check("self_test all ok", all(s.ok for s in steps),
      f"{[(s.label, s.ok, s.detail) for s in steps]}")

# precondition failure
ob.websocket_reachable = lambda timeout=1.0: False
steps2 = ob.self_test()
check("self_test one precondition failure when ws down",
      len(steps2) == 1 and steps2[0].ok is False)


print(f"\n{sum(results)}/{len(results)} checks passed\n")
sys.exit(0 if all(results) else 1)
